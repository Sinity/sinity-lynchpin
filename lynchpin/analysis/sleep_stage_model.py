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
    "build_minute_features",
    "train_sleep_wake_model",
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
