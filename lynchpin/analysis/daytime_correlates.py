"""Daytime cross-source correlates: substances, keystrokes, machine pressure × focus.

Three cross-source families the lake supports but no module asked (gap
inventory 2026-08-03), power-gated against the coverage the sources actually
have BEFORE any correlation is reported:

  substance_focus        — daytime dose load (total mg/day + per-substance,
                           dose count) vs same-day focus (deep-work minutes,
                           fragmentation, active hours) and typing volume.
                           The nightly substance→sleep pipeline existed; the
                           daytime side did not.
  keystroke_physiology   — daily keypress volume vs same-day / next-day
                           daytime physiology (stress, mean HR, resting HR,
                           HRV rmssd) in both directions.
  machine_pressure_focus — host pressure (IO/memory PSI, swap peak, kill
                           events) vs same-day focus and commit output. The
                           machine telemetry capture began 2026-05-12, so this
                           family is young; it is emitted with its
                           min-detectable |r| so early noise cannot be read as
                           findings.

STATISTICAL INTEGRITY CONTRACT
------------------------------
* Every correlation p-value is computed against the Quenouille/Bartlett
  effective sample size (day series are autocorrelated; see
  ``core.analytics.EffectiveCorrelation``) and Benjamini-Hochberg
  FDR-corrected across the entire test family of one ``analyze`` call.
* Missing ≠ zero: a day contributes to a pair only when BOTH sides observed
  that day. Inside substance-log coverage, a day without a logged dose is a
  genuine 0 mg day (complete event log), and days before the log's first or
  after its last entry are excluded entirely.
* Power gate: a family below ``MIN_PAIRS`` overlapping days is reported as
  underpowered (with its n and min-detectable |r| at 80% power) instead of
  being silently dropped — a documented negative is a deliverable.
* Known coverage negatives are computed from the products, not asserted:
  Spotify listening (export ends 2025-12) has zero overlap with ActivityWatch
  focus capture (begins 2026-02), and sleep-composite × deep-work overlap is
  reported with its (under-powered) n.

Data access is read-only over materialized products and captures; this module
never triggers materialization (the ActivityWatch daily product is consumed
as-is, with its staleness reported, because its materializer is owned by the
nightly DAG / backfill chain).
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, Optional

from ..core.analytics import (
    EffectiveCorrelation,
    _benjamini_hochberg,
    autocorr_corrected_pearson,
)
from ..core.config import get_config
from ..core.primitives import logical_date

logger = logging.getLogger(__name__)

#: Minimum overlapping day pairs before a correlation is computed.
MIN_PAIRS = 30

#: FDR target across the entire test family of one ``analyze`` call.
FDR_TARGET = 0.05

#: ActivityWatch derived rows before this date are phantom all-zero rows
#: (bead lynchpin-3yp / lynchpin-jzb); the real capture begins 2026-02-15.
AW_VALID_FROM = date(2024, 2, 15)

#: Substances with fewer logged doses than this are folded into total mg only
#: (a per-substance series of 3 doses is noise and clutters the family).
MIN_SUBSTANCE_DOSES = 30


# ── result types ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DaytimeCorrelation:
    """One (predictor → outcome) correlation within a family."""

    family: str
    predictor: str
    outcome: str
    lag_days: int  # 0 same-day; +1 predictor day D → outcome day D+1
    r: float
    n: int
    n_eff: float
    p_value: float  # autocorr-corrected two-tailed p
    p_naive: float  # uncorrected df=n-2 p (transparency)
    q_value: float  # BH FDR across the whole analyze() family
    significant: bool
    label: str


@dataclass(frozen=True)
class FamilyCoverage:
    """Coverage + power provenance for one family (or documented negative)."""

    name: str
    n_days: int  # overlapping days actually available
    covered_start: Optional[date]
    covered_end: Optional[date]
    min_detectable_r: float  # |r| detectable at alpha=.05 two-tailed, power=.80
    status: str  # "computed" | "underpowered" | "no_overlap"
    note: str = ""


@dataclass
class DaytimeCorrelatesReport:
    window_start: date
    window_end: date
    n_tests: int = 0
    correlations: list[DaytimeCorrelation] = field(default_factory=list)
    families: list[FamilyCoverage] = field(default_factory=list)
    staleness_notes: list[str] = field(default_factory=list)
    summary: str = ""


# ── loaders (module-level override hooks for tests) ──────────────────────────
# Each returns dict[date, float] day series. Tests monkeypatch these.


def _load_aw_daily() -> dict[str, dict[date, float]]:
    """Focus series from the materialized ActivityWatch daily product.

    Read-only: consumes whatever the nightly DAG / backfill chain last wrote
    and reports staleness upstream; never triggers materialization. Rows
    before ``AW_VALID_FROM`` are phantom (all-zero, dated 2013) and dropped.
    """
    path = get_config().derived_root / "activitywatch/graph/daily_activity.ndjson"
    out: dict[str, dict[date, float]] = {
        "deep_work_min": {}, "active_hours": {}, "fragmentation": {},
    }
    if not path.exists():
        return out
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            day = date.fromisoformat(row["date"])
            if day < AW_VALID_FROM:
                continue
            out["deep_work_min"][day] = float(row.get("deep_work_min") or 0.0)
            out["active_hours"][day] = float(row.get("active_hours") or 0.0)
            if row.get("fragmentation_score") is not None:
                out["fragmentation"][day] = float(row["fragmentation_score"])
    return out


def _load_daily_signals(wanted: dict[tuple[str, str], str]) -> dict[str, dict[date, float]]:
    """Series from the materialized personal daily-signals product."""
    path = get_config().derived_root / "personal/daily_signals.ndjson"
    out: dict[str, dict[date, float]] = {name: {} for name in wanted.values()}
    if not path.exists():
        return out
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            name = wanted.get((row.get("source"), row.get("metric")))
            if name is None:
                continue
            out[name][date.fromisoformat(row["date"])] = float(row["value"])
    return out


def _load_physiology() -> dict[str, dict[date, float]]:
    return _load_daily_signals({
        ("health", "stress_avg"): "stress",
        ("health", "avg_heart_rate"): "hr_mean",
        ("health", "resting_heart_rate"): "hr_resting",
        ("health", "hrv_rmssd"): "hrv_rmssd",
    })


def _load_keypress_daily() -> dict[date, float]:
    return _load_daily_signals({("keylog", "keypress_count"): "keypress"})["keypress"]


def _load_sleep_minutes() -> dict[date, float]:
    """Sleep duration series (READ-ONLY consumer of the sleep lane's product)."""
    return _load_daily_signals({("sleep", "sleep_minutes"): "sleep_min"})["sleep_min"]


def _load_spotify_minutes() -> dict[date, float]:
    path = get_config().derived_root / "spotify/daily.ndjson"
    out: dict[date, float] = {}
    if not path.exists():
        return out
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            out[date.fromisoformat(row["date"])] = float(row.get("minutes_played") or 0.0)
    return out


def _load_substance_daily() -> dict[str, dict[date, float]]:
    """Dose series from the substance log: total mg, dose count, per-substance mg.

    Within the log's [first, last] entry dates, days without entries are
    genuine 0 mg / 0 dose days (complete event log); outside, days are absent.
    Per-substance series are emitted only for substances with at least
    ``MIN_SUBSTANCE_DOSES`` logged doses.
    """
    from ..sources.substance import entries

    rows = list(entries())
    out: dict[str, dict[date, float]] = {"total_mg": {}, "dose_count": {}}
    if not rows:
        return out
    first = min(r.date for r in rows)
    last = max(r.date for r in rows)
    dose_counts: dict[str, int] = defaultdict(int)
    for r in rows:
        dose_counts[r.substance] += 1
    major = {s for s, n in dose_counts.items() if n >= MIN_SUBSTANCE_DOSES}
    for s in major:
        out[f"mg[{s}]"] = {}
    day = first
    while day <= last:
        out["total_mg"][day] = 0.0
        out["dose_count"][day] = 0.0
        for s in major:
            out[f"mg[{s}]"][day] = 0.0
        day += timedelta(days=1)
    for r in rows:
        if r.amount_mg is not None:
            out["total_mg"][r.date] += r.amount_mg
            if r.substance in major:
                out[f"mg[{r.substance}]"][r.date] += r.amount_mg
        out["dose_count"][r.date] += 1.0
    return out


def _load_git_commits(start: date, end: date) -> dict[date, float]:
    """Commit counts per logical day from the live git source."""
    from ..sources.git import daily_activity

    out: dict[date, float] = defaultdict(float)
    for row in daily_activity(start=start, end=end):
        out[row.date] += row.commit_count
    return dict(out)


def _load_machine_daily() -> dict[str, dict[date, float]]:
    """Daily machine-pressure aggregates from the telemetry capture (read-only)."""
    path = get_config().captures_root / "machine/telemetry.sqlite"
    out: dict[str, dict[date, float]] = {
        "io_psi_mean": {}, "mem_psi_mean": {}, "swap_max_mb": {}, "kill_events": {},
    }
    if not path.exists():
        return out
    acc: dict[date, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        for observed_at, io_psi, mem_psi, swap in conn.execute(
            "SELECT observed_at, io_psi_some_avg60, memory_psi_some_avg60,"
            " swap_used_mb FROM metric_sample"
        ):
            day = logical_date(datetime.fromisoformat(observed_at))
            row = acc[day]
            if io_psi is not None:
                row["io"].append(float(io_psi))
            if mem_psi is not None:
                row["mem"].append(float(mem_psi))
            if swap is not None:
                row["swap"].append(float(swap))
        kill_days = [
            logical_date(datetime.fromisoformat(observed_at))
            for (observed_at,) in conn.execute("SELECT observed_at FROM kill_event")
        ]
    finally:
        conn.close()
    for day, cols in acc.items():
        if cols["io"]:
            out["io_psi_mean"][day] = statistics.mean(cols["io"])
        if cols["mem"]:
            out["mem_psi_mean"][day] = statistics.mean(cols["mem"])
        if cols["swap"]:
            out["swap_max_mb"][day] = max(cols["swap"])
        out["kill_events"][day] = 0.0
    for day in kill_days:
        if day in out["kill_events"]:
            out["kill_events"][day] += 1.0
    return out


# Test override hooks (None = use the production loader).
_aw_loader: Optional[Callable[[], dict[str, dict[date, float]]]] = None
_physio_loader: Optional[Callable[[], dict[str, dict[date, float]]]] = None
_keypress_loader: Optional[Callable[[], dict[date, float]]] = None
_substance_loader: Optional[Callable[[], dict[str, dict[date, float]]]] = None
_machine_loader: Optional[Callable[[], dict[str, dict[date, float]]]] = None
_git_loader: Optional[Callable[[date, date], dict[date, float]]] = None
_sleep_loader: Optional[Callable[[], dict[date, float]]] = None
_spotify_loader: Optional[Callable[[], dict[date, float]]] = None


# ── power helper ─────────────────────────────────────────────────────────────


def min_detectable_r(n: float, *, alpha_z: float = 1.959964, power_z: float = 0.841621) -> float:
    """Smallest |r| detectable at two-tailed alpha=.05 with 80% power (Fisher z)."""
    if n <= 4:
        return 1.0
    return math.tanh((alpha_z + power_z) / math.sqrt(n - 3))


# ── core ─────────────────────────────────────────────────────────────────────


def _clamp(series: dict[date, float], start: date, end: date) -> dict[date, float]:
    return {d: v for d, v in series.items() if start <= d <= end}


def _pairs(
    x: dict[date, float], y: dict[date, float], lag: int
) -> tuple[list[float], list[float], Optional[date], Optional[date]]:
    """Day-ordered paired values (x day D, y day D+lag) plus the covered span."""
    xs: list[float] = []
    ys: list[float] = []
    days: list[date] = []
    for d in sorted(x):
        target = d + timedelta(days=lag)
        if target in y:
            xs.append(x[d])
            ys.append(y[target])
            days.append(d)
    lo = days[0] if days else None
    hi = days[-1] if days else None
    return xs, ys, lo, hi


def analyze(start: date, end: date) -> DaytimeCorrelatesReport:
    """Run the three daytime cross-source families over [start, end].

    Returns per-pair autocorr-corrected, FDR-corrected correlations, per-family
    coverage + min-detectable-r provenance, computed coverage negatives
    (Spotify×focus, sleep×focus), and product staleness notes.
    """
    report = DaytimeCorrelatesReport(window_start=start, window_end=end)

    aw = (_aw_loader or _load_aw_daily)()
    physio = (_physio_loader or _load_physiology)()
    keypress = _clamp((_keypress_loader or _load_keypress_daily)(), start, end)
    substance = (_substance_loader or _load_substance_daily)()
    machine = (_machine_loader or _load_machine_daily)()
    git = (_git_loader or _load_git_commits)(start, end)
    sleep_min = (_sleep_loader or _load_sleep_minutes)()
    spotify = (_spotify_loader or _load_spotify_minutes)()

    aw = {k: _clamp(v, start, end) for k, v in aw.items()}
    physio = {k: _clamp(v, start, end) for k, v in physio.items()}
    substance = {k: _clamp(v, start, end) for k, v in substance.items()}
    machine = {k: _clamp(v, start, end) for k, v in machine.items()}
    git = _clamp(git, start, end)
    sleep_min = _clamp(sleep_min, start, end)
    spotify = _clamp(spotify, start, end)

    if aw["deep_work_min"]:
        aw_last = max(aw["deep_work_min"])
        report.staleness_notes.append(
            f"ActivityWatch daily product last covers {aw_last} (read as-is; "
            "this module never triggers materialization)."
        )

    # (family, predictor_name, x_series, outcome_name, y_series, lags)
    specs: list[tuple[str, str, dict[date, float], str, dict[date, float], tuple[int, ...]]] = []

    for pred_name, pred in sorted(substance.items()):
        for out_name in ("deep_work_min", "fragmentation", "active_hours"):
            specs.append(("substance_focus", pred_name, pred, out_name, aw[out_name], (0,)))
        specs.append(("substance_focus", pred_name, pred, "keypress", keypress, (0,)))

    for phys_name, phys in sorted(physio.items()):
        specs.append(("keystroke_physiology", "keypress", keypress, phys_name, phys, (0, 1)))
        specs.append(("keystroke_physiology", phys_name, phys, "keypress", keypress, (1,)))

    for m_name, m in sorted(machine.items()):
        for out_name in ("deep_work_min", "fragmentation"):
            specs.append(("machine_pressure_focus", m_name, m, out_name, aw[out_name], (0,)))
        specs.append(("machine_pressure_focus", m_name, m, "git_commits", git, (0,)))

    raw: list[tuple[str, str, str, int, EffectiveCorrelation]] = []
    fam_span: dict[str, tuple[Optional[date], Optional[date]]] = {}
    for fam, pred_name, x, out_name, y, lags in specs:
        for lag in lags:
            xs, ys, lo, hi = _pairs(x, y, lag)
            if len(xs) < MIN_PAIRS:
                continue
            stat = autocorr_corrected_pearson(xs, ys)
            if stat is None:
                continue
            raw.append((fam, pred_name, out_name, lag, stat))
            if lo is not None and hi is not None:
                prev = fam_span.get(fam)
                fam_span[fam] = (
                    lo if prev is None or prev[0] is None else min(prev[0], lo),
                    hi if prev is None or prev[1] is None else max(prev[1], hi),
                )

    report.n_tests = len(raw)
    if raw:
        q_map = _benjamini_hochberg({i: r[4].p_value for i, r in enumerate(raw)})
        for i, (fam, pred_name, out_name, lag, stat) in enumerate(raw):
            q = q_map[i]
            report.correlations.append(
                DaytimeCorrelation(
                    family=fam,
                    predictor=pred_name,
                    outcome=out_name,
                    lag_days=lag,
                    r=round(stat.r, 4),
                    n=stat.n,
                    n_eff=round(stat.n_eff, 1),
                    p_value=round(stat.p_value, 4),
                    p_naive=round(stat.p_naive, 4),
                    q_value=round(q, 4),
                    significant=q < FDR_TARGET,
                    label=f"{pred_name} → {out_name} (lag={lag}d)",
                )
            )

    # Family coverage rows (computed families + power verdicts).
    for fam in ("substance_focus", "keystroke_physiology", "machine_pressure_focus"):
        fam_corrs = [c for c in report.correlations if c.family == fam]
        if fam_corrs:
            n_max = max(c.n for c in fam_corrs)
            ne_min = min(c.n_eff for c in fam_corrs)
            lo, hi = fam_span.get(fam, (None, None))
            report.families.append(FamilyCoverage(
                name=fam,
                n_days=n_max,
                covered_start=lo,
                covered_end=hi,
                min_detectable_r=round(min_detectable_r(ne_min), 3),
                status="computed",
                note=f"min |r| detectable at the family's smallest n_eff ({ne_min:g}).",
            ))
        else:
            report.families.append(FamilyCoverage(
                name=fam,
                n_days=0,
                covered_start=None,
                covered_end=None,
                min_detectable_r=1.0,
                status="underpowered",
                note=f"fewer than MIN_PAIRS={MIN_PAIRS} overlapping days for every pair.",
            ))

    # Computed coverage negatives (deliberate deliverables, not omissions).
    for name, xs_map, note in (
        (
            "music_focus (negative)",
            spotify,
            "Spotify export vs ActivityWatch focus capture",
        ),
        (
            "circadian_deep_work (negative)",
            sleep_min,
            "sleep-composite duration (read-only) vs ActivityWatch focus capture",
        ),
    ):
        xs, _ys, lo, hi = _pairs(xs_map, aw["deep_work_min"], 0)
        n = len(xs)
        report.families.append(FamilyCoverage(
            name=name,
            n_days=n,
            covered_start=lo,
            covered_end=hi,
            min_detectable_r=round(min_detectable_r(n), 3),
            status="no_overlap" if n == 0 else ("underpowered" if n < MIN_PAIRS else "computed"),
            note=(
                f"{note}: {n} overlapping day(s). "
                + (
                    "No overlap — the export ended before focus capture began."
                    if n == 0
                    else f"min detectable |r|={min_detectable_r(n):.2f} — reported as an "
                         "honest negative until coverage grows."
                )
            ),
        ))

    report.summary = _build_summary(report)
    return report


def _build_summary(report: DaytimeCorrelatesReport) -> str:
    lines = [
        f"Daytime Cross-Source Correlates: {report.window_start} → {report.window_end}",
        f"  Tests in FDR family: {report.n_tests}",
        "",
    ]
    for fam in report.families:
        span = (
            f"{fam.covered_start} → {fam.covered_end}"
            if fam.covered_start is not None
            else "no overlap"
        )
        lines.append(
            f"[{fam.name}] {fam.status}: n={fam.n_days} days ({span}), "
            f"min detectable |r|={fam.min_detectable_r:.2f}"
        )
        if fam.note:
            lines.append(f"    {fam.note}")
    lines.append("")

    significant = sorted(
        (c for c in report.correlations if c.significant), key=lambda c: -abs(c.r)
    )
    if significant:
        lines.append(
            f"FDR-significant associations (BH q<{FDR_TARGET:g} across "
            f"{report.n_tests} tests; p autocorr-corrected via effective n):"
        )
        for c in significant:
            direction = "↑" if c.r > 0 else "↓"
            lines.append(
                f"  r={c.r:+.3f} {direction}  [{c.family}] {c.label} "
                f"(n={c.n}, n_eff={c.n_eff:.0f}, p={c.p_value:.4f}, q={c.q_value:.4f})"
            )
    elif report.n_tests:
        lines.append(
            f"No associations survive FDR correction (q<{FDR_TARGET:g}) across "
            f"{report.n_tests} tests — an honest negative given the coverage above."
        )

    exploratory = sorted(
        (c for c in report.correlations if not c.significant and abs(c.r) > 0.2),
        key=lambda c: -abs(c.r),
    )
    if exploratory:
        lines.append("")
        lines.append(
            "Exploratory only (|r|>0.2 but NOT FDR-significant — likely noise, "
            "do not report as findings):"
        )
        for c in exploratory[:10]:
            direction = "↑" if c.r > 0 else "↓"
            lines.append(
                f"  r={c.r:+.3f} {direction}  [{c.family}] {c.label} "
                f"(n={c.n}, n_eff={c.n_eff:.0f}, q={c.q_value:.4f})"
            )

    lines.append("")
    for note in report.staleness_notes:
        lines.append(f"STALENESS: {note}")
    lines.append(
        "CAVEAT: same-day/lagged ASSOCIATIONS, not causation. Substance doses, "
        "focus, machine load and physiology share workload/circadian "
        "confounders; a surviving q only means the association is unlikely to "
        "be multiple-comparison noise at the observed effective n."
    )
    return "\n".join(lines)


__all__ = [
    "AW_VALID_FROM",
    "FDR_TARGET",
    "MIN_PAIRS",
    "DaytimeCorrelation",
    "DaytimeCorrelatesReport",
    "FamilyCoverage",
    "analyze",
    "min_detectable_r",
]
