"""Minute-level sleep/wake classification from watch sensors.

Why this exists: night-level analysis is capped at ~200 observations, which
puts a floor of |r| ≈ 0.22 on anything detectable (see the wave-2 power audit).
Per-minute sensor bins move the same window to ~10^5 observations, so
minute-level questions are well-powered on data already captured.

What it does: trains a classifier to predict *asleep vs awake* for a given
minute from record-boundary-independent sensors, using Samsung's own sleep
stage records as labels (``awake`` -> 0, ``light``/``deep``/``rem`` -> 1).
Features are deliberately restricted to signals that exist whenever the watch
is worn, so the model can score minutes on nights Samsung never scored:

- per-minute movement ``activity_level``
- heart rate relative to the operator's own nightly baseline (ratio and delta)
- short-window HR variability (rolling standard deviation)
- time of day as sine/cosine (circadian prior, not a clock lookup)

Honest limits, stated up front:

- **Labels and one feature share a sensor.** Samsung's stage classifier is
  itself driven by the same accelerometer that produces ``activity_level``, so
  this model partly learns to imitate Samsung rather than to measure sleep
  independently. It is a *recovery* tool for unscored minutes, not an
  independent validation of Samsung's scoring.
- **Evaluation is grouped by night.** Minutes within a night are massively
  autocorrelated; a random minute-level split would leak and report a wildly
  optimistic score. ``train_sleep_wake_model`` splits by night.
- Coverage is bounded by the movement product (2025-05 onward).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Optional

__all__ = [
    "MinuteFeature",
    "ModelReport",
    "PredictedSleepBlock",
    "build_minute_features",
    "train_sleep_wake_model",
    "predict_sleep_blocks",
    "threshold_for_precision",
    "SequenceReport",
    "train_sequence_model",
]

_ASLEEP_STAGES = frozenset({"light", "deep", "rem"})
_HR_ROLL_MINUTES = 5


@dataclass(frozen=True)
class MinuteFeature:
    """One minute of sensor evidence, optionally labelled."""

    when: datetime
    night: date
    activity: float
    hr: float
    hr_ratio: float          # hr / that night's baseline hr
    hr_delta: float          # hr - baseline
    hr_roll_sd: float
    tod_sin: float
    tod_cos: float
    label: Optional[int]     # 1 asleep, 0 awake, None unlabelled

    def vector(self) -> list[float]:
        return [
            self.activity,
            self.hr_ratio,
            self.hr_delta,
            self.hr_roll_sd,
            self.tod_sin,
            self.tod_cos,
        ]


FEATURE_NAMES = (
    "activity",
    "hr_ratio",
    "hr_delta",
    "hr_roll_sd",
    "tod_sin",
    "tod_cos",
)


@dataclass
class ModelReport:
    n_minutes: int
    n_nights: int
    n_asleep: int
    n_awake: int
    accuracy: float
    balanced_accuracy: float
    roc_auc: float
    baseline_majority: float
    coefficients: dict[str, float] = field(default_factory=dict)
    caveats: tuple[str, ...] = ()


def _logical_night(moment: datetime) -> date:
    day = moment.date()
    if moment.hour < 6:
        day -= timedelta(days=1)
    return day


def _load_minute_series(
    filename: str, value_key: str, start: date, end: date
) -> dict[datetime, float]:
    """Expand a product's binning_data into {minute -> value}."""
    from ..core.parse import parse_datetime as _parse_dt
    from ..sources.sleep import _load_jsonl

    lo = datetime.combine(start, datetime.min.time())
    hi = datetime.combine(end + timedelta(days=2), datetime.min.time())
    out: dict[datetime, float] = {}
    for row in _load_jsonl(filename):
        row_start = _parse_dt(row.get("start_time"))
        if row_start is None:
            continue
        naive = row_start.replace(tzinfo=None)
        if naive < lo - timedelta(days=1) or naive > hi:
            continue
        bins = row.get("binning_data")
        if not isinstance(bins, list):
            continue
        tz = row_start.tzinfo
        for entry in bins:
            if not isinstance(entry, dict):
                continue
            value = entry.get(value_key)
            ts = entry.get("start_time")
            if not isinstance(value, (int, float)) or not isinstance(ts, (int, float)):
                continue
            when = datetime.fromtimestamp(ts / 1000.0, tz=tz).replace(tzinfo=None)
            if lo <= when <= hi:
                out[when.replace(second=0, microsecond=0)] = float(value)
    return out


def _stage_labels(start: date, end: date) -> dict[datetime, int]:
    """{minute -> 1 asleep / 0 awake} from Samsung stage records."""
    from ..sources.sleep import sleep_stages

    labels: dict[datetime, int] = {}
    for record in sleep_stages(start=start, end=end):
        value = 1 if record.stage in _ASLEEP_STAGES else 0
        cursor = record.start.replace(tzinfo=None, second=0, microsecond=0)
        finish = record.end.replace(tzinfo=None)
        while cursor < finish:
            labels[cursor] = value
            cursor += timedelta(minutes=1)
    return labels


def build_minute_features(
    *, start: date, end: date, labelled_only: bool = False
) -> list[MinuteFeature]:
    """Assemble per-minute sensor features, labelled where Samsung scored."""
    movement = _load_minute_series("health_movement.jsonl", "activity_level", start, end)
    heart = _load_minute_series("health_heart_rate.jsonl", "heart_rate", start, end)
    if not movement or not heart:
        return []
    labels = _stage_labels(start, end)

    minutes = sorted(set(movement) & set(heart))
    if not minutes:
        return []

    # Per-night HR baseline: the 20th percentile of that night's HR, a robust
    # stand-in for resting level that does not need a separate calibration.
    by_night: dict[date, list[float]] = {}
    for minute in minutes:
        by_night.setdefault(_logical_night(minute), []).append(heart[minute])
    baseline: dict[date, float] = {}
    for night, values in by_night.items():
        ordered = sorted(values)
        baseline[night] = ordered[max(int(len(ordered) * 0.2) - 1, 0)] or 1.0

    index = {m: i for i, m in enumerate(minutes)}
    features: list[MinuteFeature] = []
    for minute in minutes:
        night = _logical_night(minute)
        base = baseline.get(night) or 1.0
        hr = heart[minute]
        i = index[minute]
        window = [
            heart[minutes[j]]
            for j in range(max(i - _HR_ROLL_MINUTES, 0), min(i + _HR_ROLL_MINUTES + 1, len(minutes)))
        ]
        mean = sum(window) / len(window)
        roll_sd = math.sqrt(sum((v - mean) ** 2 for v in window) / len(window))
        tod = minute.hour + minute.minute / 60.0
        angle = tod / 24.0 * 2 * math.pi
        label = labels.get(minute)
        if labelled_only and label is None:
            continue
        features.append(
            MinuteFeature(
                when=minute,
                night=night,
                activity=movement[minute],
                hr=hr,
                hr_ratio=hr / base if base else 1.0,
                hr_delta=hr - base,
                hr_roll_sd=roll_sd,
                tod_sin=math.sin(angle),
                tod_cos=math.cos(angle),
                label=label,
            )
        )
    return features


def train_sleep_wake_model(
    *, start: date, end: date, test_fraction: float = 0.3, seed: int = 42
) -> tuple[Optional[Any], ModelReport]:
    """Fit and night-grouped-evaluate a minute-level sleep/wake classifier.

    Returns ``(fitted_pipeline_or_None, report)``. The split is by NIGHT, never
    by minute: minutes inside a night are so autocorrelated that a random
    minute split leaks the answer and reports a meaningless score.
    """
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import balanced_accuracy_score, roc_auc_score
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    labelled = [f for f in build_minute_features(start=start, end=end) if f.label is not None]
    caveats = (
        "Labels come from Samsung's own stage classifier, which is driven by the "
        "same accelerometer that produces the activity feature: this model partly "
        "imitates Samsung rather than independently measuring sleep.",
        "Evaluation is split BY NIGHT; a random minute-level split would leak "
        "through within-night autocorrelation and inflate every metric.",
        "Coverage is bounded by the movement product (2025-05 onward).",
    )
    if len(labelled) < 500:
        return None, ModelReport(
            n_minutes=len(labelled), n_nights=0, n_asleep=0, n_awake=0,
            accuracy=float("nan"), balanced_accuracy=float("nan"),
            roc_auc=float("nan"), baseline_majority=float("nan"), caveats=caveats,
        )

    nights = sorted({f.night for f in labelled})
    rng = np.random.default_rng(seed)
    shuffled = list(nights)
    rng.shuffle(shuffled)
    cut = max(int(len(shuffled) * (1 - test_fraction)), 1)
    train_nights = set(shuffled[:cut])

    x_train = np.array([f.vector() for f in labelled if f.night in train_nights])
    y_train = np.array([f.label for f in labelled if f.night in train_nights])
    x_test = np.array([f.vector() for f in labelled if f.night not in train_nights])
    y_test = np.array([f.label for f in labelled if f.night not in train_nights])

    if len(set(y_train)) < 2 or len(y_test) == 0 or len(set(y_test)) < 2:
        return None, ModelReport(
            n_minutes=len(labelled), n_nights=len(nights),
            n_asleep=int(sum(f.label or 0 for f in labelled)),
            n_awake=len(labelled) - int(sum(f.label or 0 for f in labelled)),
            accuracy=float("nan"), balanced_accuracy=float("nan"),
            roc_auc=float("nan"), baseline_majority=float("nan"), caveats=caveats,
        )

    model = make_pipeline(
        StandardScaler(),
        LogisticRegression(max_iter=1000, class_weight="balanced"),
    )
    model.fit(x_train, y_train)
    proba = model.predict_proba(x_test)[:, 1]
    predicted = (proba >= 0.5).astype(int)

    majority = max(float(y_test.mean()), 1.0 - float(y_test.mean()))
    coefs = model[-1].coef_[0]
    report = ModelReport(
        n_minutes=len(labelled),
        n_nights=len(nights),
        n_asleep=int(sum(f.label or 0 for f in labelled)),
        n_awake=len(labelled) - int(sum(f.label or 0 for f in labelled)),
        accuracy=round(float((predicted == y_test).mean()), 4),
        balanced_accuracy=round(float(balanced_accuracy_score(y_test, predicted)), 4),
        roc_auc=round(float(roc_auc_score(y_test, proba)), 4),
        baseline_majority=round(majority, 4),
        coefficients={
            name: round(float(value), 4) for name, value in zip(FEATURE_NAMES, coefs)
        },
        caveats=caveats,
    )
    return model, report


@dataclass(frozen=True)
class PredictedSleepBlock:
    """A contiguous run of minutes the model scores as asleep."""

    night: date
    start: datetime
    end: datetime
    minutes: float
    mean_probability: float
    labelled_fraction: float  # share already covered by Samsung stage labels

    @property
    def is_recovered(self) -> bool:
        """True when this block is mostly NOT already scored by Samsung."""
        return self.labelled_fraction < 0.5


def threshold_for_precision(
    *,
    start: date,
    end: date,
    model: Any,
    target_precision: float = 0.9,
) -> Optional[float]:
    """Smallest probability threshold reaching ``target_precision`` for asleep.

    A fixed cutoff like 0.7 is meaningless for this model: it is fitted with
    balanced class weights against an 83/17 asleep/awake split, so its
    probabilities are calibrated to a 50/50 prior and are systematically
    deflated relative to the real one. Measured, only 8,151 of 135,703 minutes
    ever reach 0.7. Deriving the cutoff from a stated precision target keeps
    the claim honest and the number reproducible.
    """
    import numpy as np

    labelled = [
        f for f in build_minute_features(start=start, end=end) if f.label is not None
    ]
    if not labelled or model is None:
        return None
    probabilities = model.predict_proba(np.array([f.vector() for f in labelled]))[:, 1]
    truth = np.array([f.label for f in labelled])
    best: Optional[float] = None
    for candidate in np.unique(np.round(probabilities, 3)):
        selected = probabilities >= candidate
        if selected.sum() == 0:
            continue
        precision = float(truth[selected].mean())
        if precision >= target_precision:
            best = float(candidate)
            break
    return best


def predict_sleep_blocks(
    *,
    start: date,
    end: date,
    model: Any,
    min_probability: float = 0.7,
    min_block_minutes: float = 90.0,
    max_bridge_minutes: float = 15.0,
) -> list[PredictedSleepBlock]:
    """Contiguous high-probability sleep blocks predicted from sensors.

    Confidence gating is deliberate and conservative. Individual minute
    predictions at AUC 0.76 are far too noisy to assert anything about a
    night; only a *sustained* run of high-probability minutes is reported.
    Short dips are bridged (``max_bridge_minutes``) because a single noisy
    minute should not split a block, and blocks shorter than
    ``min_block_minutes`` are dropped entirely rather than surfaced as weak
    claims.

    Blocks are emitted regardless of whether Samsung already scored them;
    ``labelled_fraction`` says how much overlaps existing labels, so callers
    can select genuinely recovered sleep via ``is_recovered``.
    """
    features = build_minute_features(start=start, end=end)
    if not features or model is None:
        return []

    import numpy as np

    vectors = np.array([f.vector() for f in features])
    probabilities = model.predict_proba(vectors)[:, 1]

    blocks: list[PredictedSleepBlock] = []
    run: list[tuple[MinuteFeature, float]] = []

    def flush() -> None:
        if not run:
            return
        span_minutes = (run[-1][0].when - run[0][0].when).total_seconds() / 60.0 + 1
        if span_minutes < min_block_minutes:
            run.clear()
            return
        labelled = sum(1 for f, _ in run if f.label is not None)
        blocks.append(
            PredictedSleepBlock(
                night=run[0][0].night,
                start=run[0][0].when,
                end=run[-1][0].when + timedelta(minutes=1),
                minutes=round(span_minutes, 1),
                mean_probability=round(
                    float(sum(p for _, p in run) / len(run)), 4
                ),
                labelled_fraction=round(labelled / len(run), 4),
            )
        )
        run.clear()

    previous: Optional[MinuteFeature] = None
    for feature, probability in zip(features, probabilities):
        contiguous = (
            previous is not None
            and 0
            < (feature.when - previous.when).total_seconds() / 60.0
            <= max_bridge_minutes
        )
        if probability >= min_probability:
            if run and not contiguous:
                flush()
            run.append((feature, float(probability)))
        elif run and contiguous and probability >= min_probability * 0.75:
            # tolerate a shallow dip inside an otherwise confident block
            run.append((feature, float(probability)))
        else:
            flush()
        previous = feature
    flush()
    return blocks


# ── Sequence model: HMM smoothing over the minute-independent classifier ─────


@dataclass
class SequenceReport:
    """Night-grouped evaluation of the HMM against the independent-minute model."""

    n_minutes: int
    n_nights: int
    baseline_majority: float
    independent_auc: float
    independent_balanced_accuracy: float
    sequence_auc: float
    sequence_balanced_accuracy: float
    transition_stay_asleep: float
    transition_stay_awake: float
    median_longest_block_minutes: float
    caveats: tuple[str, ...] = ()

    @property
    def improves(self) -> bool:
        return self.sequence_auc > self.independent_auc


def _fit_transitions(sequences: list[list[int]]) -> tuple[list[list[float]], list[float]]:
    """Maximum-likelihood 2-state transition matrix and initial distribution."""
    counts = [[1.0, 1.0], [1.0, 1.0]]  # Laplace smoothing
    initial = [1.0, 1.0]
    for seq in sequences:
        if not seq:
            continue
        initial[seq[0]] += 1.0
        for a, b in zip(seq, seq[1:]):
            counts[a][b] += 1.0
    matrix = [[c / sum(row) for c in row] for row in counts]
    total = sum(initial)
    return matrix, [v / total for v in initial]


def _forward_backward(
    emissions: "Any", matrix: list[list[float]], initial: list[float]
) -> "Any":
    """Posterior P(asleep | whole sequence) per timestep. emissions: (T, 2)."""
    import numpy as np

    n = emissions.shape[0]
    trans = np.array(matrix)
    alpha = np.zeros((n, 2))
    scale = np.zeros(n)
    alpha[0] = np.array(initial) * emissions[0]
    scale[0] = alpha[0].sum() or 1.0
    alpha[0] /= scale[0]
    for t in range(1, n):
        alpha[t] = (alpha[t - 1] @ trans) * emissions[t]
        scale[t] = alpha[t].sum() or 1.0
        alpha[t] /= scale[t]
    beta = np.zeros((n, 2))
    beta[-1] = 1.0
    for t in range(n - 2, -1, -1):
        beta[t] = trans @ (emissions[t + 1] * beta[t + 1])
        norm = beta[t].sum() or 1.0
        beta[t] /= norm
    posterior = alpha * beta
    totals = posterior.sum(axis=1, keepdims=True)
    totals[totals == 0] = 1.0
    return (posterior / totals)[:, 1]


def train_sequence_model(
    *, start: date, end: date, test_fraction: float = 0.3, seed: int = 42
) -> tuple[Optional[dict[str, Any]], SequenceReport]:
    """Fit an HMM over the independent classifier and compare, night-grouped.

    Rationale for an HMM over a GRU: the defect being fixed is precisely the
    minute-independence assumption, and an HMM addresses exactly that with two
    interpretable parameters (the stay-asleep and stay-awake probabilities)
    while fitting in milliseconds on a memory-constrained host. A GRU would add
    capacity this dataset cannot support - roughly 160 labelled nights - and
    would obscure whether any gain came from temporal structure or from raw
    capacity. If the HMM does not beat the independent model on night-grouped
    AUC, the simpler model stays the default.
    """
    import numpy as np

    from sklearn.metrics import balanced_accuracy_score, roc_auc_score

    model, base_report = train_sleep_wake_model(
        start=start, end=end, test_fraction=test_fraction, seed=seed
    )
    caveats = (
        *base_report.caveats,
        "The HMM smooths the SAME per-minute classifier; it adds temporal "
        "persistence, not new sensor information.",
    )
    if model is None:
        return None, SequenceReport(
            n_minutes=base_report.n_minutes, n_nights=base_report.n_nights,
            baseline_majority=float("nan"), independent_auc=float("nan"),
            independent_balanced_accuracy=float("nan"), sequence_auc=float("nan"),
            sequence_balanced_accuracy=float("nan"), transition_stay_asleep=float("nan"),
            transition_stay_awake=float("nan"), median_longest_block_minutes=float("nan"),
            caveats=caveats,
        )

    labelled = [
        f for f in build_minute_features(start=start, end=end) if f.label is not None
    ]
    nights = sorted({f.night for f in labelled})
    rng = np.random.default_rng(seed)
    shuffled = list(nights)
    rng.shuffle(shuffled)
    cut = max(int(len(shuffled) * (1 - test_fraction)), 1)
    train_nights = set(shuffled[:cut])

    per_night: dict[date, list[MinuteFeature]] = {}
    for feature in labelled:
        per_night.setdefault(feature.night, []).append(feature)
    for values in per_night.values():
        values.sort(key=lambda f: f.when)

    matrix, initial = _fit_transitions(
        [[f.label or 0 for f in per_night[n]] for n in nights if n in train_nights]
    )

    # The classifier is fitted with balanced class weights, so its output is
    # already P(asleep | x) under a UNIFORM prior - i.e. prior odds of 1. That
    # makes [1-p, p] proportional to the emission likelihoods directly. An
    # earlier version divided these by the empirical class prior, which
    # double-corrected: it shrank the asleep emission and inflated the awake
    # one, and the decoder then produced a median longest sleep block of zero
    # minutes on held-out nights. The HMM's own initial distribution and
    # transition matrix carry the prior.

    truth: list[int] = []
    seq_scores: list[float] = []
    ind_scores: list[float] = []
    longest_blocks: list[float] = []
    for night in nights:
        if night in train_nights:
            continue
        values = per_night[night]
        vectors = np.array([f.vector() for f in values])
        p_asleep = model.predict_proba(vectors)[:, 1]
        # Convert posterior-under-balanced-prior into an emission likelihood.
        emissions = np.clip(
            np.stack([(1.0 - p_asleep), p_asleep], axis=1), 1e-9, None
        )
        smoothed = _forward_backward(emissions, matrix, initial)
        truth.extend(f.label or 0 for f in values)
        ind_scores.extend(p_asleep.tolist())
        seq_scores.extend(smoothed.tolist())
        decoded = smoothed >= 0.5
        best = cur = 0
        for flag in decoded:
            cur = cur + 1 if flag else 0
            best = max(best, cur)
        longest_blocks.append(float(best))

    if not truth or len(set(truth)) < 2:
        return None, SequenceReport(
            n_minutes=len(labelled), n_nights=len(nights),
            baseline_majority=float("nan"), independent_auc=float("nan"),
            independent_balanced_accuracy=float("nan"), sequence_auc=float("nan"),
            sequence_balanced_accuracy=float("nan"),
            transition_stay_asleep=matrix[1][1], transition_stay_awake=matrix[0][0],
            median_longest_block_minutes=float("nan"), caveats=caveats,
        )

    truth_arr = np.array(truth)
    seq_arr = np.array(seq_scores)
    ind_arr = np.array(ind_scores)
    majority = max(float(truth_arr.mean()), 1.0 - float(truth_arr.mean()))
    fitted = {"classifier": model, "transitions": matrix, "initial": initial}
    report = SequenceReport(
        n_minutes=len(labelled),
        n_nights=len(nights),
        baseline_majority=round(majority, 4),
        independent_auc=round(float(roc_auc_score(truth_arr, ind_arr)), 4),
        independent_balanced_accuracy=round(
            float(balanced_accuracy_score(truth_arr, (ind_arr >= 0.5).astype(int))), 4
        ),
        sequence_auc=round(float(roc_auc_score(truth_arr, seq_arr)), 4),
        sequence_balanced_accuracy=round(
            float(balanced_accuracy_score(truth_arr, (seq_arr >= 0.5).astype(int))), 4
        ),
        transition_stay_asleep=round(matrix[1][1], 5),
        transition_stay_awake=round(matrix[0][0], 5),
        median_longest_block_minutes=round(float(np.median(longest_blocks)), 1)
        if longest_blocks else float("nan"),
        caveats=caveats,
    )
    return fitted, report
