"""Life-phase boundary detection via multi-signal change-point analysis.

Detects phase transitions by building a coverage-aware normalized composite
across several ``OperatorDay`` signals, then running binary-segmentation
change-point detection (``core.analytics.detect_changepoints``) on that
composite. Each phase is then characterized (what's different before vs after?).

Known life events do **not** create boundaries. They are only used to
*annotate* boundaries the data actually detected: a detected boundary within
30 days of a known event snaps to the event date and is labelled. A known
event with no nearby detected shift is reported as an un-aligned annotation,
never as a phase boundary — synthesizing boundaries from the event list would
be confirmation bias presented as detection.

Known life events (annotation candidates only) are entirely personal by
definition (career, health, substance-use, relocation-shaped milestones), so
the list itself is not in source — see ``_load_known_events`` below. With no
override file present, KNOWN_EVENTS is simply empty and boundaries are
reported unannotated.

Coverage semantics (missing != zero)
------------------------------------
Each composite metric is tied to a data source with an observed coverage
range. For a given day a metric is included in that day's composite only when
the day falls inside the metric's coverage; otherwise it is ABSENT (excluded
entirely — neither 0 nor the mean). This prevents a genuinely-unobserved day
from being read as a real low/zero value, which would bias or fabricate
transitions. The covered range of every signal used is recorded on the report.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from datetime import date as Date  # alias for annotations in classes with a `date` field
from pathlib import Path
from typing import Any, Callable, Optional

from ..core.analytics import detect_changepoints
from ..core.config import get_config
from ..core.coverage import CoverageBounds, partition_by_coverage
from ..sources.source_observations import coverage_bounds
from .operator_daily import OperatorDay, operator_daily_matrix


@dataclass(frozen=True)
class PhaseBoundary:
    """A detected phase transition."""

    date: date
    confidence: float  # 0-1, higher = sharper transition
    signals_involved: tuple[str, ...]  # which signals changed / annotation label

    # What changed across this boundary? (before → after)
    changes: tuple[tuple[str, float, float], ...]  # (signal, before_mean, after_mean)


@dataclass(frozen=True)
class EventAnnotation:
    """A known life event and whether the data corroborated it.

    ``aligned`` is True when a detected boundary fell within the snap window of
    this event (the event then labels that boundary). ``aligned`` is False when
    no detected shift was near the event — the event is recorded for context but
    is explicitly NOT treated as a phase boundary.
    """

    date: Date
    label: str
    aligned: bool
    nearest_detected: Optional[Date] = None
    nearest_distance_days: Optional[int] = None


@dataclass(frozen=True)
class LifePhase:
    """A contiguous period between two boundaries."""

    start: date
    end: date
    n_days: int

    # Mean signal values during this phase
    aw_active_hours: Optional[float] = None
    git_commits_per_day: float = 0
    stress_mean: Optional[float] = None
    sleep_hours: Optional[float] = None
    substance_mg_per_day: float = 0
    wykop_comments_per_day: float = 0

    # Social / music signals (None when no covered data exists in this phase).
    reddit_comments_per_day: Optional[float] = None
    web_distraction_ratio: Optional[float] = None  # social_visits / total_visits mean
    spotify_hours_per_day: Optional[float] = None

    label: str = ""  # human-readable phase name


@dataclass
class LifePhaseReport:
    """Complete life-phase analysis."""

    window_start: date
    window_end: date
    n_days: int

    boundaries: list[PhaseBoundary] = field(default_factory=list)
    phases: list[LifePhase] = field(default_factory=list)

    # Known events and whether the data corroborated them. Un-aligned events
    # are context only — they never appear in ``boundaries``.
    event_annotations: list[EventAnnotation] = field(default_factory=list)

    # Per-metric coverage provenance for the signals fed into detection.
    signal_coverage: list[str] = field(default_factory=list)

    summary: str = ""


def _load_known_events() -> list[tuple[date, str]]:
    """Load known life events from an optional external override file.

    Entirely personal by definition (career, health, substance-use,
    tooling-adoption milestones) — never in source. Format:
    ``[{"date": "YYYY-MM-DD", "label": "..."}]``.
    """
    path = get_config().derived_root / "local-config" / "life_events.json"
    try:
        if not path.exists():
            return []
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            return []
        events: list[tuple[date, str]] = []
        for row in raw:
            if not isinstance(row, dict):
                continue
            d, label = row.get("date"), row.get("label")
            if not d or not label:
                continue
            events.append((date.fromisoformat(str(d)), str(label)))
        return sorted(events)
    except (OSError, json.JSONDecodeError, ValueError):
        return []


# Known life events for alignment / annotation (never synthesized into boundaries).
KNOWN_EVENTS: list[tuple[date, str]] = _load_known_events()

#: Maximum distance (days) for a known event to snap to a detected boundary.
SNAP_WINDOW_DAYS = 30


# ── Composite metric definitions ──────────────────────────────────────────
# Each metric: (name, accessor, weight, coverage_source_key).
# ``coverage_source_key`` names the data source whose observed coverage range
# decides whether the metric is present on a given day. ``stress`` and
# ``substance`` are materialized health datasets, not keys in
# ``available_sources()``, so their bounds are resolved separately below.
@dataclass(frozen=True)
class _Metric:
    name: str
    accessor: Callable[[OperatorDay], float]
    weight: float
    coverage_key: str


def _web_distraction_ratio(r: "OperatorDay") -> float:
    """Social visits as a fraction of total web visits (0.0–1.0).

    Using a ratio rather than the raw count makes the metric robust to days
    that happen to have high overall web activity; what matters for phase
    detection is the *proportion* of time spent on social browsing, not the
    absolute volume. Returns 0.0 when web_visits == 0 so covered days with no
    browsing record a genuine-zero rather than being excluded.
    """
    if r.web_visits <= 0:
        return 0.0
    return r.web_social_visits / r.web_visits


_METRICS: tuple[_Metric, ...] = (
    # ── Productivity / coding ────────────────────────────────────────────
    _Metric("aw_active", lambda r: r.aw_active_hours or 0.0, 1.0, "activitywatch"),
    _Metric("git_commits", lambda r: float(r.git_commits), 0.5, "git_baseline"),
    # ── Wellbeing ────────────────────────────────────────────────────────
    _Metric("stress", lambda r: r.stress_mean or 0.0, 0.5, "stress"),
    _Metric("sleep", lambda r: r.sleep_hours or 0.0, 1.0, "sleep"),
    # ── Substance ────────────────────────────────────────────────────────
    _Metric("substance_mg", lambda r: sum(r.substance_mg_by_name.values()), 0.3, "substance"),
    # ── Social ───────────────────────────────────────────────────────────
    # wykop_comments: Polish social-platform comment volume (export, gated by
    # wykop coverage). Weight 0.3 — comparable social signal to reddit.
    _Metric("wykop", lambda r: float(r.wykop_comments), 0.3, "wykop"),
    # reddit_comments: raw comment count on Reddit. Measures social/media
    # engagement independently of wykop. Coverage key "reddit".
    _Metric("reddit", lambda r: float(r.reddit_comments), 0.3, "reddit"),
    # web_distraction: social-visits / total-visits ratio (0–1). Captures days
    # dominated by distraction browsing regardless of absolute visit volume.
    # Coverage follows webhistory so it is present only when web data exists.
    _Metric("web_distraction", _web_distraction_ratio, 0.4, "webhistory"),
    # ── Music ────────────────────────────────────────────────────────────
    # spotify_hours: hours of music/podcast listening. A high-spotify,
    # low-git phase is a "media/leisure phase" and should form a distinct
    # cluster from a focused coding phase. Weight 0.5 — on par with git so
    # it can pull its own weight as a phase discriminator.
    _Metric("spotify", lambda r: r.spotify_hours or 0.0, 0.5, "spotify"),
)


def analyze(
    start: date,
    end: date,
    *,
    known_events: list[tuple[date, str]] | None = None,
) -> LifePhaseReport:
    """Detect life phases from multi-signal change-point analysis.

    Builds a coverage-aware normalized composite of multiple daily signals,
    then applies binary-segmentation change-point detection. Known events are
    used only to annotate detected boundaries, never to create them.
    """
    rows = operator_daily_matrix(start, end, skip_slow=True)
    if len(rows) < 60:
        return LifePhaseReport(window_start=start, window_end=end, n_days=len(rows))

    report = LifePhaseReport(window_start=start, window_end=end, n_days=len(rows))
    events = known_events if known_events is not None else KNOWN_EVENTS

    # Resolve per-metric coverage from observed source bounds.
    metric_bounds = _resolve_metric_bounds(rows)
    report.signal_coverage = [metric_bounds[m.name].provenance() for m in _METRICS]

    # Build coverage-aware composite signal (missing != zero).
    signals = _build_composite_signal(rows, metric_bounds)

    # Detect boundaries on the composite (real binary-segmentation, not events).
    detected = _detect_boundaries(signals, rows)

    # Snap-annotate known events onto detected boundaries (no synthesis).
    report.boundaries, report.event_annotations = _align_with_events(detected, events)

    # Build phases between *detected* boundaries.
    report.phases = _build_phases(rows, report.boundaries)

    report.summary = _summarize_phases(report)
    return report


def write_report(out: Path, *, start: date, end: date) -> dict[str, Any]:
    import json
    from datetime import datetime, timezone
    from dataclasses import asdict
    from lynchpin.core.io import save_json
    report = analyze(start, end)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        **asdict(report),
    }
    save_json(out, json.loads(json.dumps(payload, default=str)))
    return payload


def _resolve_metric_bounds(rows: list[OperatorDay]) -> dict[str, CoverageBounds]:
    """Resolve a ``CoverageBounds`` for every composite metric.

    Most metrics map directly to a ``coverage_bounds()`` source key.
    ``stress`` and ``substance`` are materialized health datasets not exposed
    in ``available_sources()``, so they are read from the materialization
    audit. ``git_baseline`` is a live subprocess source with no materialized
    first/last date; its coverage is derived from the matrix row span (git is
    always queryable for any day the matrix produced a row).
    """
    src_bounds = coverage_bounds()
    row_first = rows[0].date
    row_last = rows[-1].date

    health_bounds = _materialized_health_bounds()

    out: dict[str, CoverageBounds] = {}
    for m in _METRICS:
        key = m.coverage_key
        if key == "git_baseline":
            # Live source: coverage = the analysed row span itself.
            bound = src_bounds.get(key)
            first = bound.first if bound and bound.first is not None else row_first
            last = bound.last if bound and bound.last is not None else row_last
            out[m.name] = CoverageBounds(
                source="git", first=first, last=last, kind="capture"
            )
        elif key in ("stress", "substance"):
            out[m.name] = health_bounds.get(
                key, CoverageBounds(source=key, first=None, last=None, kind="export")
            )
        else:
            out[m.name] = src_bounds.get(
                key, CoverageBounds(source=key, first=None, last=None, kind="export")
            )
    return out


def _materialized_health_bounds() -> dict[str, CoverageBounds]:
    """Coverage bounds for ``stress`` (health) and ``substance`` datasets.

    These are materialized export datasets keyed by their dataset-contract name
    (``health`` carries the stress series, ``substance`` the dose log). They are
    not part of ``available_sources()``, so ``coverage_bounds()`` omits them.
    """
    from lynchpin.materialization import audit_materialization

    rows = {row.name: row for row in audit_materialization()}
    out: dict[str, CoverageBounds] = {}
    # stress lives in the unified health dataset
    health = rows.get("health")
    out["stress"] = CoverageBounds(
        source="stress",
        first=health.first_date if health else None,
        last=health.last_date if health else None,
        kind="export",
    )
    substance = rows.get("substance")
    out["substance"] = CoverageBounds(
        source="substance",
        first=substance.first_date if substance else None,
        last=substance.last_date if substance else None,
        kind="export",
    )
    return out


def _build_composite_signal(
    rows: list[OperatorDay],
    metric_bounds: dict[str, CoverageBounds],
) -> list[float]:
    """Build a coverage-aware normalized composite from daily metrics.

    Per-metric semantics (uniform across all metrics):
      - A day is included for a metric only when it falls inside that metric's
        observed coverage range (``partition_by_coverage``). Out-of-coverage
        days are ABSENT — excluded from that metric entirely. They are never
        coerced to 0 and never imputed to the mean.
      - For covered days, the raw value (including a genuine 0) is z-normalized
        against that metric's covered-day distribution.
      - A day's composite is the weight-averaged z-score over only the metrics
        present (covered) on that day. Days with no covered metric get 0.0.

    This removes the prior mixed semantics where aw/git entered at literal 0
    (so absence read as low) while other metrics entered only when ``v>0`` (so
    absence read as omitted). Now absence is uniformly "not observed".
    """
    all_dates = [r.date for r in rows]
    covered_dates: dict[str, set[date]] = {}
    for m in _METRICS:
        in_cov, _ = partition_by_coverage(all_dates, metric_bounds[m.name])
        covered_dates[m.name] = set(in_cov)

    # Collect covered values per metric, and per-row covered values.
    metric_values: dict[str, list[float]] = defaultdict(list)
    metric_per_row: list[dict[str, float]] = []
    for r in rows:
        row_vals: dict[str, float] = {}
        for m in _METRICS:
            if r.date in covered_dates[m.name]:
                v = m.accessor(r)
                metric_values[m.name].append(v)
                row_vals[m.name] = v
        metric_per_row.append(row_vals)

    # Z-normalize each metric over its covered-day distribution.
    stats: dict[str, tuple[float, float]] = {}
    for name, vals in metric_values.items():
        if len(vals) < 10:
            stats[name] = (0.0, 1.0)
            continue
        mean = statistics.mean(vals)
        stdev = statistics.stdev(vals)
        stats[name] = (mean, stdev if stdev > 0 else 1.0)

    weight_of = {m.name: m.weight for m in _METRICS}
    composite: list[float] = []
    for row_vals in metric_per_row:
        total_weight = 0.0
        total_signal = 0.0
        for name, value in row_vals.items():
            mean, stdev = stats.get(name, (0.0, 1.0))
            z = (value - mean) / stdev
            w = weight_of[name]
            total_signal += z * w
            total_weight += w
        composite.append(total_signal / total_weight if total_weight > 0 else 0.0)

    return composite


def _detect_boundaries(
    signal: list[float],
    rows: list[OperatorDay],
) -> list[date]:
    """Detect phase boundaries via binary-segmentation change-point detection.

    Delegates to ``core.analytics.detect_changepoints`` (recursive binary
    segmentation on residual SSE with a BIC penalty) — a real change-point
    method, not the previous hand-rolled rolling-mean-shift heuristic and not
    PELT. Each accepted split index is mapped back to the corresponding day.
    """
    if len(signal) < 30:
        return []

    # min_segment doubles as the minimum spacing between boundaries (each
    # segment is >= min_segment days), so 14 keeps the prior ">=2 weeks apart"
    # behaviour without a separate distance guard.
    changepoints = detect_changepoints(signal, min_segment=14, max_changepoints=8)
    return [rows[cp.index].date for cp in changepoints if 0 <= cp.index < len(rows)]


def _align_with_events(
    detected: list[date],
    known_events: list[tuple[date, str]],
) -> tuple[list[PhaseBoundary], list[EventAnnotation]]:
    """Annotate detected boundaries with nearby known events.

    A detected boundary within ``SNAP_WINDOW_DAYS`` of an unused known event
    snaps to the event date and is labelled with it (higher confidence the
    closer the match). Detected boundaries with no nearby event are kept at
    their detected date with a neutral label.

    Crucially, known events that align to no detected boundary are returned as
    un-aligned ``EventAnnotation`` records ONLY — they are never turned into
    ``PhaseBoundary`` objects. This is the fix for the prior behaviour that
    fabricated a confidence-0.3 boundary for every unused event.
    """
    boundaries: list[PhaseBoundary] = []
    annotations: list[EventAnnotation] = []
    used_events: set[int] = set()
    # Track, for each event, the nearest detected boundary (for honest context).
    nearest_for_event: dict[int, tuple[date, int]] = {}

    for det_date in detected:
        best_event: Optional[tuple[int, date, str]] = None
        best_dist = SNAP_WINDOW_DAYS + 1
        for i, (ev_date, ev_label) in enumerate(known_events):
            dist = abs((det_date - ev_date).days)
            prev = nearest_for_event.get(i)
            if prev is None or dist < prev[1]:
                nearest_for_event[i] = (det_date, dist)
            if i in used_events:
                continue
            if dist < best_dist:
                best_dist = dist
                best_event = (i, ev_date, ev_label)

        if best_event is not None and best_dist <= SNAP_WINDOW_DAYS:
            i, ev_date, ev_label = best_event
            used_events.add(i)
            boundaries.append(
                PhaseBoundary(
                    date=ev_date,
                    confidence=1.0 - best_dist / (SNAP_WINDOW_DAYS + 1.0),
                    signals_involved=(ev_label,),
                    changes=(),
                )
            )
        else:
            boundaries.append(
                PhaseBoundary(
                    date=det_date,
                    confidence=0.5,
                    signals_involved=("composite",),
                    changes=(),
                )
            )

    for i, (ev_date, ev_label) in enumerate(known_events):
        nearest = nearest_for_event.get(i)
        annotations.append(
            EventAnnotation(
                date=ev_date,
                label=ev_label,
                aligned=i in used_events,
                nearest_detected=nearest[0] if nearest else None,
                nearest_distance_days=nearest[1] if nearest else None,
            )
        )

    boundaries.sort(key=lambda b: b.date)
    annotations.sort(key=lambda a: a.date)
    return boundaries, annotations


def _build_phases(
    rows: list[OperatorDay],
    boundaries: list[PhaseBoundary],
) -> list[LifePhase]:
    """Build LifePhase objects for each period between detected boundaries."""
    if not boundaries:
        return []

    bounds = [
        (b.date, b.signals_involved[0] if b.signals_involved else "")
        for b in boundaries
    ]
    bounds.sort()

    phases: list[LifePhase] = []
    for i in range(len(bounds) + 1):
        p_start = bounds[i - 1][0] if i > 0 else rows[0].date
        p_end = bounds[i][0] - date.resolution if i < len(bounds) else rows[-1].date

        phase_rows = [r for r in rows if p_start <= r.date <= p_end]
        if not phase_rows:
            continue

        n = len(phase_rows)

        # Web distraction ratio: only compute when at least one day has visits.
        web_rows = [r for r in phase_rows if r.web_visits > 0]
        web_dist: Optional[float] = (
            statistics.mean(_web_distraction_ratio(r) for r in web_rows)
            if web_rows
            else None
        )

        phases.append(LifePhase(
            start=p_start,
            end=p_end,
            n_days=n,
            aw_active_hours=statistics.mean(
                [r.aw_active_hours for r in phase_rows if r.aw_active_hours is not None]
            ) if any(r.aw_active_hours is not None for r in phase_rows) else None,
            git_commits_per_day=sum(r.git_commits for r in phase_rows) / n,
            stress_mean=statistics.mean(
                [r.stress_mean for r in phase_rows if r.stress_mean is not None]
            ) if any(r.stress_mean is not None for r in phase_rows) else None,
            sleep_hours=statistics.mean(
                [r.sleep_hours for r in phase_rows if r.sleep_hours is not None]
            ) if any(r.sleep_hours is not None for r in phase_rows) else None,
            substance_mg_per_day=sum(sum(r.substance_mg_by_name.values()) for r in phase_rows) / n,
            wykop_comments_per_day=sum(r.wykop_comments for r in phase_rows) / n,
            # Social / music: per-phase means where covered (None = not in coverage).
            reddit_comments_per_day=statistics.mean(
                [float(r.reddit_comments) for r in phase_rows]
            ),
            web_distraction_ratio=web_dist,
            spotify_hours_per_day=statistics.mean(
                [r.spotify_hours for r in phase_rows if r.spotify_hours is not None]
            ) if any(r.spotify_hours is not None for r in phase_rows) else None,
            label=bounds[i - 1][1] if i > 0 else "start",
        ))

    return phases


def _summarize_phases(report: LifePhaseReport) -> str:
    aligned = [a for a in report.event_annotations if a.aligned]
    unaligned = [a for a in report.event_annotations if not a.aligned]
    lines = [
        f"Life Phase Analysis: {report.window_start} → {report.window_end}",
        f"  Days: {report.n_days}",
        f"  Phases detected: {len(report.phases)}",
        f"  Boundaries (data-detected): {len(report.boundaries)}",
        f"  Known events corroborated: {len(aligned)} / {len(report.event_annotations)}",
        "",
        "Signal coverage:",
    ]
    for prov in report.signal_coverage:
        lines.append(f"  {prov}")
    lines += ["", "Phases:"]
    for p in report.phases:
        aw = f"{p.aw_active_hours:.0f}h" if p.aw_active_hours else "?"
        stress = f"{p.stress_mean:.0f}" if p.stress_mean else "?"
        sleep = f"{p.sleep_hours:.1f}h" if p.sleep_hours else "?"
        reddit = f"{p.reddit_comments_per_day:.1f}" if p.reddit_comments_per_day is not None else "?"
        web_dist = f"{p.web_distraction_ratio:.2f}" if p.web_distraction_ratio is not None else "?"
        spotify = f"{p.spotify_hours_per_day:.1f}h" if p.spotify_hours_per_day is not None else "?"
        lines.append(
            f"  {p.start} → {p.end} ({p.n_days:>4}d) | "
            f"AW={aw:>4s} git={p.git_commits_per_day:>5.1f}/d "
            f"stress={stress:>3s} sleep={sleep:>5s} "
            f"substance={p.substance_mg_per_day:>6.0f}mg/d "
            f"wykop={p.wykop_comments_per_day:>5.1f}/d "
            f"reddit={reddit:>4s}/d web_soc={web_dist:>4s} spotify={spotify:>5s}"
        )
    if unaligned:
        lines += ["", "Known events with no nearby detected shift (context only, NOT boundaries):"]
        for a in unaligned:
            near = (
                f" (nearest detected {a.nearest_detected}, {a.nearest_distance_days}d away)"
                if a.nearest_detected is not None
                else ""
            )
            lines.append(f"  {a.date} {a.label}{near}")
    return "\n".join(lines)


__all__ = [
    "PhaseBoundary",
    "EventAnnotation",
    "LifePhase",
    "LifePhaseReport",
    "analyze",
]
