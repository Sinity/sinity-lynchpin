"""Analysis helpers for bounded ``below`` exports."""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
from typing import Any, Sequence

from lynchpin.analysis.core.io import save_json


DEFAULT_STABILITY_ROOT = Path("/realm/data/captures/stability-lab")


@dataclass(frozen=True)
class BelowSystemSummary:
    capture_id: str
    sample_count: int
    first_observed_at: datetime | None
    last_observed_at: datetime | None
    avg_cpu_pct: float | None
    p95_cpu_pct: float | None
    avg_iowait_pct: float | None
    min_available_gb: float | None
    max_running_procs: int | None
    oom_kills: int


@dataclass(frozen=True)
class BelowEntitySummary:
    capture_id: str
    kind: str
    key: str
    sample_count: int
    first_observed_at: datetime | None
    last_observed_at: datetime | None
    avg_cpu_pct: float | None
    max_cpu_pct: float | None
    max_rss_mb: float | None
    max_mem_total_mb: float | None


@dataclass(frozen=True)
class BelowAnalysis:
    window_count: int
    system: list[BelowSystemSummary]
    top_process_count: int
    top_processes: list[BelowEntitySummary]
    top_cgroup_count: int
    top_cgroups: list[BelowEntitySummary]
    caveats: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_below_exports(
    *,
    root: Path = DEFAULT_STABILITY_ROOT,
    top_n: int = 20,
) -> BelowAnalysis:
    captures = _discover_reports(root)
    system = [_system_summary(report) for report in captures if (report / "below-system.csv").exists()]
    processes = _entity_summaries(captures, "process", top_n=top_n)
    cgroups = _entity_summaries(captures, "cgroup", top_n=top_n)
    caveats = []
    if not system:
        caveats.append("no bounded below system exports found")
    caveats.append("live /var/log/below store is not promoted wholesale; export bounded windows for incidents and experiments")
    system_rows = [row for row in system if row is not None]
    return BelowAnalysis(
        window_count=len(system_rows),
        system=system_rows,
        top_process_count=len(processes),
        top_processes=processes,
        top_cgroup_count=len(cgroups),
        top_cgroups=cgroups,
        caveats=caveats,
    )


def _discover_reports(root: Path) -> list[Path]:
    reports: dict[Path, Path] = {}
    for report in sorted(path for path in root.glob("*/report") if path.is_dir()):
        reports.setdefault(report.resolve(), report)
    return list(reports.values())


def write_below_analysis(
    out: Path,
    *,
    root: Path = DEFAULT_STABILITY_ROOT,
    top_n: int = 20,
) -> BelowAnalysis:
    analysis = analyze_below_exports(root=root, top_n=top_n)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), **analysis.to_dict()}
    save_json(
        out,
        json.loads(json.dumps(payload, default=str)),
        sort_keys=True,
    )
    return analysis


def _system_summary(report: Path) -> BelowSystemSummary | None:
    rows = list(_read_csv(report / "below-system.csv"))
    if not rows:
        return None
    cpu = [_parse_pct(row.get("Usage")) for row in rows]
    iowait = [_parse_pct(row.get("IOWait")) for row in rows]
    available = [_parse_gb(row.get("Available")) for row in rows]
    running = [_parse_int(row.get("Running Procs")) for row in rows]
    oom = [_parse_int(row.get("OOM Kills")) for row in rows]
    timestamps: list[datetime] = [
        ts for row in rows
        for ts in (_parse_below_datetime(row.get("Datetime")),)
        if ts is not None
    ]
    return BelowSystemSummary(
        capture_id=report.parent.name,
        sample_count=len(rows),
        first_observed_at=min(timestamps) if timestamps else None,
        last_observed_at=max(timestamps) if timestamps else None,
        avg_cpu_pct=_mean(cpu),
        p95_cpu_pct=_quantile(cpu, 0.95),
        avg_iowait_pct=_mean(iowait),
        min_available_gb=_min(available),
        max_running_procs=_max_int(running),
        oom_kills=sum(value for value in oom if value is not None),
    )


def _entity_summaries(reports: list[Path], kind: str, *, top_n: int) -> list[BelowEntitySummary]:
    filename = "below-top-processes.csv" if kind == "process" else "below-top-cgroups.csv"
    grouped: dict[tuple[str, str], dict[str, list[float]]] = {}
    timestamps: dict[tuple[str, str], list[datetime]] = {}
    counts: dict[tuple[str, str], int] = {}
    for report in reports:
        path = report / filename
        if not path.exists():
            continue
        capture_id = report.parent.name
        for row in _read_csv(path):
            key = _entity_key(row, kind)
            if not key:
                continue
            group_key = (capture_id, key)
            counts[group_key] = counts.get(group_key, 0) + 1
            entry = grouped.setdefault(group_key, {"cpu": [], "rss": [], "mem": []})
            observed_at = _parse_below_datetime(row.get("Datetime"))
            if observed_at is not None:
                timestamps.setdefault(group_key, []).append(observed_at)
            cpu_col = "CPU" if kind == "process" else "CPU Usage"
            cpu = _parse_pct(row.get(cpu_col))
            if cpu is not None:
                entry["cpu"].append(cpu)
            rss = _parse_mb(row.get("RSS"))
            if rss is not None:
                entry["rss"].append(rss)
            mem = _parse_mb(row.get("Mem Total"))
            if mem is not None:
                entry["mem"].append(mem)
    summaries = [
        BelowEntitySummary(
            capture_id=capture_id,
            kind=kind,
            key=key,
            sample_count=counts[(capture_id, key)],
            first_observed_at=min(timestamps.get((capture_id, key), ()), default=None),
            last_observed_at=max(timestamps.get((capture_id, key), ()), default=None),
            avg_cpu_pct=_mean(values["cpu"]),
            max_cpu_pct=_max(values["cpu"]),
            max_rss_mb=_max(values["rss"]),
            max_mem_total_mb=_max(values["mem"]),
        )
        for (capture_id, key), values in grouped.items()
    ]
    summaries.sort(
        key=lambda row: (
            row.max_cpu_pct or 0.0,
            row.avg_cpu_pct or 0.0,
            row.max_rss_mb or row.max_mem_total_mb or 0.0,
        ),
        reverse=True,
    )
    return summaries[:top_n]


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def _entity_key(row: dict[str, str], kind: str) -> str:
    if kind == "process":
        return row.get("Cmdline") or row.get("Comm") or ""
    return row.get("Full Path") or row.get("Name") or ""


def _parse_below_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _parse_pct(value: str | None) -> float | None:
    if not value:
        return None
    text = value.strip().rstrip("%")
    try:
        return float(text)
    except ValueError:
        return None


def _parse_int(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(float(value.strip()))
    except ValueError:
        return None


def _parse_gb(value: str | None) -> float | None:
    mb = _parse_mb(value)
    return None if mb is None else round(mb / 1024, 4)


def _parse_mb(value: str | None) -> float | None:
    if not value:
        return None
    parts = value.strip().split()
    if not parts:
        return None
    try:
        number = float(parts[0])
    except ValueError:
        return None
    unit = parts[1].lower() if len(parts) > 1 else "b"
    if unit.startswith("gb"):
        return round(number * 1024, 4)
    if unit.startswith("mb"):
        return number
    if unit.startswith("kb"):
        return round(number / 1024, 4)
    if unit.startswith("b"):
        return round(number / (1024 * 1024), 4)
    return number


def _mean(values: Sequence[float | None]) -> float | None:
    valid = [value for value in values if value is not None]
    return None if not valid else round(statistics.mean(valid), 4)


def _quantile(values: Sequence[float | None], q: float) -> float | None:
    valid = sorted(value for value in values if value is not None)
    if not valid:
        return None
    idx = min(len(valid) - 1, max(0, round((len(valid) - 1) * q)))
    return round(valid[idx], 4)


def _min(values: Sequence[float | None]) -> float | None:
    valid = [value for value in values if value is not None]
    return None if not valid else min(valid)


def _max(values: list[float]) -> float | None:
    return None if not values else max(values)


def _max_int(values: list[int | None]) -> int | None:
    valid = [value for value in values if value is not None]
    return None if not valid else max(valid)
