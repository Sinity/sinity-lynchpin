"""Stall-attribution: join host-level memory PSI windows to the slice(s)
whose memory footprint spiked concurrently.

sinnix-a1dp.1: distinct from ``pressure_incidents.py`` (which detects PSI
spikes at the fast avg10 resolution and enriches with vmstat/kill/workload
evidence for *any* memory-or-io spike) and from ``episodes.py`` (which
detects a broader set of avg60/avg300 machine-state episode kinds without
attribution). This view answers one narrower question: for a *sustained*
avg60 memory-PSI freeze, which systemd *slice* (``system.*``/``user.*``
labels in ``machine_cgroup_memory_sample`` -- not the individual
``unit:*`` service rows also present in that table) was actually growing
at the time, so each freeze self-attributes to a resource-governance class
instead of staying an unexplained host-level number.

No per-slice PSI is captured today (``service_cgroup_pressure_sample`` only
covers a fixed list of named services, not the top-level slices) so
attribution here is necessarily via slice memory bytes (current/peak
growth during the window), not slice PSI -- documented as a caveat on
every analysis, not silently assumed away.

Each window carries two attribution rankings rather than one, since they
answer different questions and can disagree on real data: ``top_attributed_
slices`` ranks by peak resident bytes ("who was biggest during the freeze"
-- catches a slice that was already huge and stayed huge) and
``top_by_growth`` ranks by delta bytes, end minus start ("who was actively
growing/escaping" -- catches the slice driving a fresh allocation spike
even under a larger, already-resident slice). sinnix-a1dp.1's own
containment-escape ground truth (2026-08-03 05:11:08) is exactly this
case: ``user.build`` tops peak_bytes (19.32GB) while shrinking during the
window (delta -3.4GB), and ``user.agent`` tops top_by_growth (delta
+1.1GB) as the only slice that actually grew.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

from lynchpin.core.io import save_json
from lynchpin.analysis.machine.sql import latest_machine_rows
from lynchpin.substrate.connection import connect, substrate_path

STALL_ATTRIBUTION_DETECTOR_VERSION = "stall-attribution-v1"

# avg60 (not pressure_incidents' avg10) per the a1dp.1 AC: this view is
# about sustained freezes, not short blips -- avg60 already low-passes.
DEFAULT_MEMORY_PSI_THRESHOLD = 15.0
DEFAULT_MERGE_GAP = timedelta(minutes=2)
# Padding applied to the detected PSI window before joining slice memory
# samples -- a slice's memory growth can lead or trail the PSI samples
# that tripped the window by a sample interval or two.
DEFAULT_WINDOW_PAD = timedelta(minutes=2)
DEFAULT_TOP_N = 5
# Slice labels only: machine_cgroup_memory_sample also carries "unit:*"
# rows for individually-tracked services (below.service, sinexd.service,
# ...) which are a different attribution axis (see pressure_incidents.py's
# top_cgroup_memory_deltas) -- excluded here to keep "slice" meaning slice.
_SLICE_LABEL_PREFIXES = ("system.", "user.")


@dataclass(frozen=True)
class SliceAttribution:
    label: str
    scope: str
    sample_count: int
    start_bytes: int | None
    end_bytes: int | None
    peak_bytes: int | None
    delta_bytes: int | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StallWindow:
    window_id: str
    host: str
    started_at: datetime
    ended_at: datetime
    sample_count: int
    peak_memory_psi_full_avg60: float | None
    # Ranked by peak resident bytes during the window: "who was biggest
    # while the freeze was happening" -- catches a slice that was already
    # huge and merely held its peak through the stall.
    top_attributed_slices: tuple[SliceAttribution, ...]
    # Ranked by delta_bytes (end - start) during the window: "who was
    # actively growing/escaping" -- catches the slice driving a fresh
    # allocation spike even when a larger, already-resident slice ranks
    # higher on peak_bytes alone. The two rankings can disagree (e.g. a
    # huge, shrinking slice outranks a smaller, actively-growing one on
    # peak_bytes) -- both are kept rather than picking one philosophy.
    top_by_growth: tuple[SliceAttribution, ...]
    caveats: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            **{
                k: v
                for k, v in asdict(self).items()
                if k not in ("top_attributed_slices", "top_by_growth")
            },
            "top_attributed_slices": [s.to_dict() for s in self.top_attributed_slices],
            "top_by_growth": [s.to_dict() for s in self.top_by_growth],
        }


@dataclass(frozen=True)
class StallAttributionAnalysis:
    detector_version: str
    memory_psi_threshold: float
    window_count: int
    windows: list[StallWindow]
    caveats: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "detector_version": self.detector_version,
            "memory_psi_threshold": self.memory_psi_threshold,
            "window_count": self.window_count,
            "windows": [w.to_dict() for w in self.windows],
            "caveats": self.caveats,
        }


def _window_clause(start: date | None, end: date | None, column: str = "observed_at") -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if start is not None:
        clauses.append(f"CAST({column} AS DATE) >= ?")
        params.append(start)
    if end is not None:
        clauses.append(f"CAST({column} AS DATE) <= ?")
        params.append(end)
    return ("WHERE " + " AND ".join(clauses), params) if clauses else ("", params)


def _metric_rows(conn: Any, *, start: date | None, end: date | None) -> list[dict[str, Any]]:
    where, params = _window_clause(start, end)
    metric_rows = latest_machine_rows("machine_metric_sample")
    rows = conn.execute(
        f"""
        SELECT observed_at, host,
               coalesce(memory_psi_full_avg60, memory_psi_full_avg300, 0.0) AS memory_psi_full
        FROM ({metric_rows})
        {where}
        ORDER BY host, observed_at
        """,
        params,
    ).fetchall()
    columns = ["observed_at", "host", "memory_psi_full"]
    return [dict(zip(columns, row)) for row in rows]


def _group_by_host(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["host"]), []).append(row)
    return grouped


def _merge_sustained_runs(
    rows: list[dict[str, Any]], *, merge_gap: timedelta
) -> list[list[dict[str, Any]]]:
    ordered = sorted(rows, key=lambda row: row["observed_at"])
    runs: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for row in ordered:
        if current and row["observed_at"] - current[-1]["observed_at"] > merge_gap:
            runs.append(current)
            current = []
        current.append(row)
    if current:
        runs.append(current)
    return runs


def _slice_attributions(
    conn: Any, *, host: str, window_start: datetime, window_end: datetime, top_n: int
) -> tuple[list[SliceAttribution], list[SliceAttribution], list[str]]:
    cgroup_rows = latest_machine_rows("machine_cgroup_memory_sample")
    rows = conn.execute(
        f"""
        SELECT label, scope, observed_at, memory_current_bytes, memory_peak_bytes
        FROM ({cgroup_rows})
        WHERE host = ? AND observed_at BETWEEN ? AND ?
          AND (label LIKE 'system.%' OR label LIKE 'user.%')
        ORDER BY label, observed_at
        """,
        [host, window_start, window_end],
    ).fetchall()
    if not rows:
        return [], [], ["no slice-level machine_cgroup_memory_sample rows in the padded window"]

    by_label: dict[str, list[tuple[Any, Any, Any]]] = {}
    scope_of: dict[str, str] = {}
    for label, scope, observed_at, cur_bytes, peak_bytes in rows:
        by_label.setdefault(label, []).append((observed_at, cur_bytes, peak_bytes))
        scope_of[label] = scope

    attributions: list[SliceAttribution] = []
    for label, samples in by_label.items():
        samples.sort(key=lambda s: s[0])
        start_bytes = samples[0][1]
        end_bytes = samples[-1][1]
        peaks = [s[2] for s in samples if s[2] is not None]
        peak_bytes = max(peaks) if peaks else None
        delta_bytes = (
            int(end_bytes) - int(start_bytes)
            if start_bytes is not None and end_bytes is not None
            else None
        )
        attributions.append(
            SliceAttribution(
                label=label,
                scope=scope_of[label],
                sample_count=len(samples),
                start_bytes=int(start_bytes) if start_bytes is not None else None,
                end_bytes=int(end_bytes) if end_bytes is not None else None,
                peak_bytes=int(peak_bytes) if peak_bytes is not None else None,
                delta_bytes=delta_bytes,
            )
        )

    # Two rankings, kept side by side rather than picking one philosophy:
    # peak_bytes ("who was simply LARGEST while the freeze was happening" --
    # delta_bytes alone would miss a slice that was already huge and merely
    # held its peak through the stall, still the culprit) and delta_bytes
    # ("who was actively growing/escaping" -- peak_bytes alone would miss a
    # smaller slice mid-escape while a larger, already-resident slice sits
    # on top just by size). They can and do disagree on real data.
    by_peak = sorted(attributions, key=lambda a: a.peak_bytes or 0, reverse=True)
    by_growth = sorted(attributions, key=lambda a: a.delta_bytes or 0, reverse=True)
    return by_peak[:top_n], by_growth[:top_n], []


def analyze_stall_attribution(
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
    memory_psi_threshold: float = DEFAULT_MEMORY_PSI_THRESHOLD,
    merge_gap: timedelta = DEFAULT_MERGE_GAP,
    window_pad: timedelta = DEFAULT_WINDOW_PAD,
    top_n: int = DEFAULT_TOP_N,
) -> StallAttributionAnalysis:
    """For every host memory_psi_full_avg60 > threshold window, attribute
    the stall to the slice(s) whose memory footprint peaked concurrently.

    Reads promoted telemetry only (``machine_metric_sample``,
    ``machine_cgroup_memory_sample``). Missing tables or slice-less
    windows are reported as caveats, never coerced to a silent zero.
    """
    resolved_path = path or substrate_path()
    with connect(resolved_path, read_only=True) as conn:
        rows = _metric_rows(conn, start=start, end=end)
        spikes = [row for row in rows if row["memory_psi_full"] > memory_psi_threshold]
        grouped = _group_by_host(spikes)

        windows: list[StallWindow] = []
        for host, host_rows in grouped.items():
            for run in _merge_sustained_runs(host_rows, merge_gap=merge_gap):
                started_at = run[0]["observed_at"]
                ended_at = run[-1]["observed_at"]
                peak = max((r["memory_psi_full"] for r in run), default=None)
                by_peak, by_growth, window_caveats = _slice_attributions(
                    conn,
                    host=host,
                    window_start=started_at - window_pad,
                    window_end=ended_at + window_pad,
                    top_n=top_n,
                )
                windows.append(
                    StallWindow(
                        window_id=f"{host}:{started_at.isoformat()}",
                        host=host,
                        started_at=started_at,
                        ended_at=ended_at,
                        sample_count=len(run),
                        peak_memory_psi_full_avg60=peak,
                        top_attributed_slices=tuple(by_peak),
                        top_by_growth=tuple(by_growth),
                        caveats=tuple(window_caveats),
                    )
                )
    windows.sort(key=lambda w: (w.started_at, w.host))

    caveats: list[str] = []
    if not rows:
        caveats.append("machine_metric_sample has no rows in this window")
    elif not windows:
        caveats.append(
            f"no memory_psi_full_avg60 sample crossed the configured threshold ({memory_psi_threshold})"
        )
    caveats.append(
        "attribution is by slice MEMORY (bytes), not slice PSI: "
        "service_cgroup_pressure_sample does not capture top-level slices "
        "(only a fixed list of named services), only machine_cgroup_memory_sample does"
    )

    return StallAttributionAnalysis(
        detector_version=STALL_ATTRIBUTION_DETECTOR_VERSION,
        memory_psi_threshold=memory_psi_threshold,
        window_count=len(windows),
        windows=windows,
        caveats=caveats,
    )


def write_stall_attribution(
    out: Path,
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
    **kwargs: Any,
) -> StallAttributionAnalysis:
    analysis = analyze_stall_attribution(start=start, end=end, path=path, **kwargs)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), **analysis.to_dict()}
    save_json(out, json.loads(json.dumps(payload, default=str)), sort_keys=True)
    return analysis
