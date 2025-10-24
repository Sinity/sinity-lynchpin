"""Cross-source anomaly cross-reference.

When one source is anomalous (>2σ from its baseline), what do the OTHER
sources show? Turns anomaly DETECTION into anomaly UNDERSTANDING.

Example questions:
  - High-stress day → less git activity? More music? More social?
  - Zero-sleep night → next-day AW pattern?
  - Substance lapse → health signal cascade?
  - Low-productivity day (low deep_work) → what ELSE was happening?

Uses the OperatorDay daily matrix as input.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Optional

from ..core.coverage import CoverageBounds
from ..sources.source_observations import coverage_bounds
from .operator_daily import OperatorDay, operator_daily_matrix


@dataclass(frozen=True)
class AnomalyDay:
    """One day where a source was anomalous, with context from all other sources."""

    date: date
    source: str  # which source was anomalous
    metric: str  # which metric within that source
    value: float  # the anomalous value
    z_score: float  # how many sigma from mean
    direction: str  # "high" or "low"

    # What else was happening that day?
    aw_active_hours: Optional[float] = None
    aw_deep_work_min: Optional[float] = None
    git_commits: int = 0
    stress_mean: Optional[float] = None
    sleep_hours: Optional[float] = None
    substance_doses: int = 0
    substance_mg_total: float = 0.0
    wykop_comments: int = 0
    polylogue_sessions: int = 0
    web_visits: int = 0
    spotify_hours: Optional[float] = None
    machine_kill_events: int = 0
    machine_peak_memory_psi_some_avg10: Optional[float] = None


@dataclass
class AnomalyCrossReference:
    """Full cross-reference: for each source, what correlates with its anomalies?"""

    window_start: date
    window_end: date
    n_days: int

    # All anomalous days found
    anomalies: list[AnomalyDay] = field(default_factory=list)

    # Per-source summary: when this source is anomalous...
    per_source: dict[str, dict[str, float]] = field(default_factory=dict)

    # Coverage provenance: human-readable range for each source checked
    coverage_provenance: dict[str, str] = field(default_factory=dict)

    summary: str = ""


# Which metrics to check for anomalies, with direction.
# Tuple: (source_label, accessor_fn, direction, z_threshold, metric_name)
#
# metric_name is an explicit human label — NOT fn.__doc__ (lambdas have no
# docstring, so fn.__doc__ is always None and produces empty metric fields).
# Each check carries a distinct name so "git_commits_low" vs "git_commits_high"
# are distinguishable in AnomalyDay.metric.
_ANOMALY_METRICS: list[tuple[str, Callable[[OperatorDay], Any], str, float, str]] = [
    # ActivityWatch
    ("aw", lambda r: r.aw_active_hours, "low", 2.0, "aw_active_hours_low"),
    ("aw", lambda r: r.aw_deep_work_min, "low", 2.0, "aw_deep_work_min_low"),
    # Git
    ("git", lambda r: r.git_commits, "low", 2.0, "git_commits_low"),
    ("git", lambda r: r.git_commits, "high", 2.5, "git_commits_high"),
    # Health
    ("health", lambda r: r.stress_mean, "high", 2.0, "stress_mean_high"),
    ("health", lambda r: r.sleep_hours, "low", 2.0, "sleep_hours_low"),
    ("health", lambda r: r.hr_mean_bpm, "high", 2.0, "hr_mean_bpm_high"),
    # Substance
    ("substance", lambda r: sum(r.substance_mg_by_name.values()), "high", 2.0, "substance_mg_total_high"),
    ("substance", lambda r: r.substance_doses, "low", 2.0, "substance_doses_low"),
    # Social
    ("social", lambda r: r.wykop_comments, "high", 2.5, "wykop_comments_high"),
    ("social", lambda r: r.reddit_comments, "high", 2.5, "reddit_comments_high"),
    # Machine telemetry (sinnix-kx4): OOM-kill days and sustained memory
    # PSI-pressure days are exactly the kind of anomaly this module exists to
    # cross-reference — e.g. "was I running a heavy build/rebuild that day?"
    ("machine", lambda r: r.machine_kill_events, "high", 2.5, "machine_kill_events_high"),
    ("machine", lambda r: r.machine_peak_memory_psi_some_avg10, "high", 2.0, "machine_peak_memory_psi_high"),
]

# Maps each source_label used in _ANOMALY_METRICS to the coverage_bounds key
# returned by source_observations.coverage_bounds().  Used to restrict anomaly
# scans to days actually observed by that source (missing ≠ zero).
_SOURCE_COVERAGE_KEY: dict[str, str] = {
    "aw": "activitywatch",
    "git": "git_baseline",
    "health": "health",
    "substance": "substance",
    "social": "reddit",  # social uses reddit + wykop; reddit has longer history
    # "machine" has no coverage_bounds() entry (it isn't in available_sources());
    # this key simply won't resolve in bounds_map, degrading to an unclamped
    # scan guarded only by operator_daily's own machine_kill_events/PSI fill,
    # same as the "no coverage mapping" fallback documented above.
    "machine": "machine",
}


def analyze(
    start: date,
    end: date,
    *,
    metrics: Optional[list[tuple[str, Callable[[OperatorDay], Any], str, float, str]]] = None,
) -> AnomalyCrossReference:
    """Find anomalous days across sources and cross-reference.

    For each anomalous day, reports what ALL other sources showed.
    """
    rows = operator_daily_matrix(start, end, skip_slow=True)
    if not rows:
        return AnomalyCrossReference(window_start=start, window_end=end, n_days=0)

    report = AnomalyCrossReference(
        window_start=start, window_end=end, n_days=len(rows),
    )

    # Build coverage bounds once; restrict each source scan to covered dates.
    bounds_map: dict[str, CoverageBounds] = coverage_bounds()

    check_metrics = metrics or _ANOMALY_METRICS

    for source, fn, direction, threshold, metric_label in check_metrics:
        # Determine the covered date range for this source.
        cov_key = _SOURCE_COVERAGE_KEY.get(source)
        source_bounds: CoverageBounds | None = bounds_map.get(cov_key) if cov_key else None

        # Record coverage provenance for each source label (once per source).
        if source not in report.coverage_provenance:
            if source_bounds is not None:
                report.coverage_provenance[source] = source_bounds.provenance()
            else:
                report.coverage_provenance[source] = f"{source}: no coverage mapping"

        # Only include rows whose date falls within observed coverage.
        # Days outside coverage default to zero in OperatorDay, not genuine zeros.
        if source_bounds is not None and (source_bounds.first is not None or source_bounds.last is not None):
            covered_rows = [r for r in rows if source_bounds.covers(r.date)]
        else:
            # No coverage info available — fall back to full row set.
            covered_rows = list(rows)

        values = [(r, fn(r)) for r in covered_rows if fn(r) is not None]
        if len(values) < 20:
            continue

        vals = [v for _, v in values]
        mean = statistics.mean(vals)
        stdev = statistics.stdev(vals)
        if stdev == 0:
            continue

        for r, v in values:
            z = (v - mean) / stdev
            if direction == "high" and z > threshold:
                report.anomalies.append(_build_anomaly(r, source, metric_label, v, z, "high"))
            elif direction == "low" and z < -threshold:
                report.anomalies.append(_build_anomaly(r, source, metric_label, v, z, "low"))

    # Per-source summaries
    by_source: dict[str, list[AnomalyDay]] = defaultdict(list)
    for a in report.anomalies:
        by_source[a.source].append(a)

    for source, anoms in by_source.items():
        if len(anoms) < 3:
            continue
        report.per_source[source] = {
            "count": len(anoms),
            "avg_aw_active": statistics.mean(
                [a.aw_active_hours for a in anoms if a.aw_active_hours is not None]
            ) if any(a.aw_active_hours is not None for a in anoms) else 0,
            "avg_git_commits": statistics.mean([a.git_commits for a in anoms]),
            "avg_stress": statistics.mean(
                [a.stress_mean for a in anoms if a.stress_mean is not None]
            ) if any(a.stress_mean is not None for a in anoms) else 0,
            "avg_sleep": statistics.mean(
                [a.sleep_hours for a in anoms if a.sleep_hours is not None]
            ) if any(a.sleep_hours is not None for a in anoms) else 0,
            "avg_substance_doses": statistics.mean([a.substance_doses for a in anoms]),
            "avg_wykop_comments": statistics.mean([a.wykop_comments for a in anoms]),
            "avg_machine_kill_events": statistics.mean([a.machine_kill_events for a in anoms]),
        }

    report.summary = _summarize(report)
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


def _build_anomaly(
    r: OperatorDay, source: str, metric: str, value: float, z: float, direction: str
) -> AnomalyDay:
    return AnomalyDay(
        date=r.date,
        source=source,
        metric=metric,
        value=value,
        z_score=z,
        direction=direction,
        aw_active_hours=r.aw_active_hours,
        aw_deep_work_min=r.aw_deep_work_min,
        git_commits=r.git_commits,
        stress_mean=r.stress_mean,
        sleep_hours=r.sleep_hours,
        substance_doses=r.substance_doses,
        substance_mg_total=sum(r.substance_mg_by_name.values()),
        wykop_comments=r.wykop_comments,
        polylogue_sessions=r.polylogue_sessions,
        web_visits=r.web_visits,
        spotify_hours=r.spotify_hours,
        machine_kill_events=r.machine_kill_events,
        machine_peak_memory_psi_some_avg10=r.machine_peak_memory_psi_some_avg10,
    )


def _summarize(report: AnomalyCrossReference) -> str:
    lines = [
        f"Anomaly Cross-Reference: {report.window_start} → {report.window_end}",
        f"  Days analyzed: {report.n_days}",
        f"  Total anomalies found: {len(report.anomalies)}",
        "",
    ]
    for source, stats in sorted(report.per_source.items()):
        lines.append(
            f"  {source}: {stats['count']} anomalies | "
            f"avg AW={stats['avg_aw_active']:.1f}h | "
            f"avg git={stats['avg_git_commits']:.1f} | "
            f"avg stress={stats['avg_stress']:.1f} | "
            f"avg sleep={stats['avg_sleep']:.1f}h | "
            f"avg doses={stats['avg_substance_doses']:.1f} | "
            f"avg machine kills={stats['avg_machine_kill_events']:.1f}"
        )
    return "\n".join(lines)


__all__ = ["AnomalyDay", "AnomalyCrossReference", "analyze"]
