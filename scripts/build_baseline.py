#!/usr/bin/env python3
"""Rebuild baseline analytics artifacts from local canonical data sources.

This script mirrors the handcrafted 2025-10-23 baseline by wiring together
ActivityWatch windows/AFK exports, Codex session metadata, Atuin history,
git stats, and merged wearable sleep segments.  Each output lands in the
requested `--output-dir` (defaults to `results/latest` but can be pointed to
any dated folder).

Usage
-----
    python scripts/build_baseline.py \
        --session-root data/raw/activitywatch/session-2025-10-23 \
        --health-root /realm/data/health/processed \
        --output-dir results/2025-10-23-baseline

All inputs stay on disk (no remote calls).  If the ActivityWatch web bucket is
available, pass `--web-bucket aw-watcher-web-firefox_sinnix-prime` to snapshot
the most recent events into `activitywatch_web_sample.json`.
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd
import typer
from pandas import DataFrame, Series
from typing_extensions import Annotated
import shutil

app = typer.Typer(pretty_exceptions_show_locals=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ensure_datetime(series: Series) -> Series:
    """Parse ISO8601 timestamps into timezone-aware pandas datetimes."""
    if series.empty:
        return series
    return pd.to_datetime(series, utc=True, errors="coerce")


def _host_from_bucket(bucket: str, prefix: str) -> str:
    """Extract the host identifier after a known bucket prefix."""
    if not isinstance(bucket, str):
        return "unknown"
    if bucket.startswith(prefix):
        return bucket[len(prefix) :]
    if "_" in bucket:
        return bucket.split("_", 1)[-1]
    return bucket


def _round(value: float, digits: int = 3) -> float:
    """Round floats while avoiding negative zero artefacts."""
    rounded = round(float(value), digits)
    return 0.0 if math.isclose(rounded, 0.0, abs_tol=10 ** (-digits)) else rounded


def _normalise_repo_path(path: str) -> str:
    """Convert absolute repository paths into short identifiers."""
    if not isinstance(path, str):
        return "unknown"
    parts = Path(path).parts
    if "realm" in parts:
        idx = parts.index("realm")
        remaining = parts[idx + 1 :]
        return "/".join(remaining) if remaining else path.strip("/")
    return path.strip("/") or "unknown"


def _categorise_command(cwd: Optional[str], command: str) -> str:
    """Map Atuin command rows onto coarse effort categories."""
    if not cwd or not isinstance(cwd, str):
        return "misc"
    path = cwd.strip()
    lowered = path.lower()
    # Prioritise explicit matches first.
    if "project/sinex" in lowered or lowered.rstrip("/").endswith("sinex"):
        return "development:sinex"
    if "sinnix" in lowered:
        return "infrastructure:sinnix"
    if "/realm/project/" in lowered:
        return "development:other"
    if lowered.startswith("/realm/home") or lowered.startswith("/home"):
        return "home"
    return "misc"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# ActivityWatch
# ---------------------------------------------------------------------------


def build_activitywatch_window_summary(windows_path: Path) -> Dict[str, Any]:
    df = pd.read_json(windows_path, lines=True) if windows_path.exists() else pd.DataFrame()
    if df.empty:
        return {"daily_totals": [], "monthly_totals": [], "top_apps_monthly": {}}

    df["start"] = _ensure_datetime(df["start"])
    df["host"] = df["bucket"].apply(lambda b: _host_from_bucket(b, "aw-watcher-window_"))
    df["date"] = df["start"].dt.strftime("%Y-%m-%d")
    df["month"] = df["start"].dt.strftime("%Y-%m")
    df["duration_seconds"] = df["duration_seconds"].fillna(0.0)

    daily = (
        df.groupby(["date", "host"])["duration_seconds"]
        .sum()
        .reset_index()
        .sort_values(["date", "host"])
    )
    daily_records = [
        {
            "date": row.date,
            "host": row.host,
            "hours": _round(row.duration_seconds / 3600.0, 3),
        }
        for row in daily.itertuples(index=False)
    ]

    monthly = (
        df.groupby(["month", "host"])["duration_seconds"]
        .sum()
        .reset_index()
        .sort_values(["month", "host"])
    )
    monthly_records = [
        {
            "month": row.month,
            "host": row.host,
            "hours": _round(row.duration_seconds / 3600.0, 1),
        }
        for row in monthly.itertuples(index=False)
    ]

    top_apps: Dict[str, List[Dict[str, Any]]] = {}
    df_apps = (
        df.groupby(["month", "host", "app"])["duration_seconds"]
        .sum()
        .reset_index()
        .rename(columns={"duration_seconds": "seconds"})
    )
    if not df_apps.empty:
        for (month, host), chunk in df_apps.groupby(["month", "host"]):
            rows = (
                chunk.sort_values("seconds", ascending=False)
                .assign(hours=lambda s: s["seconds"] / 3600.0)
            )
            key = f"{month}::{host}"
            top_apps[key] = [
                {"app": r.app, "hours": _round(r.hours, 1)} for r in rows.itertuples(index=False)
            ]

    return {
        "daily_totals": daily_records,
        "monthly_totals": monthly_records,
        "top_apps_monthly": top_apps,
    }


def build_activitywatch_afk_summary(afk_path: Path) -> Dict[str, Any]:
    df = pd.read_json(afk_path, lines=True) if afk_path.exists() else pd.DataFrame()
    if df.empty:
        return {"daily": [], "monthly": []}

    df["start"] = _ensure_datetime(df["start"])
    df["end"] = _ensure_datetime(df["end"])
    df["host"] = df["bucket"].apply(lambda b: _host_from_bucket(b, "aw-watcher-afk_"))
    df["date"] = df["start"].dt.strftime("%Y-%m-%d")
    df["month"] = df["start"].dt.strftime("%Y-%m")
    df["duration_seconds"] = df["duration_seconds"].fillna(0.0)

    def _aggregate(group_cols: List[str]) -> List[Dict[str, Any]]:
        pivot = (
            df.groupby(group_cols + ["status"])["duration_seconds"]
            .sum()
            .reset_index()
            .pivot_table(
                index=group_cols,
                columns="status",
                values="duration_seconds",
                fill_value=0.0,
            )
            .reset_index()
        )
        records: List[Dict[str, Any]] = []
        for row in pivot.itertuples(index=False):
            payload = {col: getattr(row, col) for col in group_cols}
            payload["active_hours"] = _round(getattr(row, "not-afk", 0.0) / 3600.0, 2)
            payload["afk_hours"] = _round(getattr(row, "afk", 0.0) / 3600.0, 2)
            records.append(payload)
        return sorted(records, key=lambda item: tuple(item[col] for col in group_cols))

    return {
        "daily": _aggregate(["date", "host"]),
        "monthly": _aggregate(["month", "host"]),
    }


def build_activitywatch_afk_window(afk_path: Path) -> Dict[str, Any]:
    df = pd.read_json(afk_path, lines=True) if afk_path.exists() else pd.DataFrame()
    if df.empty:
        return {}

    df["start"] = _ensure_datetime(df["start"])
    df["end"] = _ensure_datetime(df["end"])
    df["duration_seconds"] = df["duration_seconds"].fillna(0.0)

    window_start = df["start"].min()
    window_end = df["end"].max()

    afk = df[df["status"] == "afk"]
    not_afk = df[df["status"] == "not-afk"]
    threshold_seconds = 4 * 3600

    long_blocks = afk[afk["duration_seconds"] >= threshold_seconds]
    short_blocks = afk[afk["duration_seconds"] < threshold_seconds]

    long_hours = long_blocks["duration_seconds"].sum() / 3600.0
    short_hours = short_blocks["duration_seconds"].sum() / 3600.0
    active_hours = not_afk["duration_seconds"].sum() / 3600.0

    return {
        "window_start": window_start.isoformat() if pd.notna(window_start) else None,
        "window_end": window_end.isoformat() if pd.notna(window_end) else None,
        "afk_long_blocks": int(long_blocks.shape[0]),
        "afk_long_hours": _round(long_hours, 2),
        "afk_long_avg_hours": _round(
            (long_hours / long_blocks.shape[0]) if long_blocks.shape[0] else 0.0, 1
        ),
        "afk_short_blocks": int(short_blocks.shape[0]),
        "afk_short_hours": _round(short_hours, 2),
        "active_hours": _round(active_hours, 2),
    }


# ---------------------------------------------------------------------------
# Codex Sessions
# ---------------------------------------------------------------------------


def build_codex_summary(codex_path: Path) -> Dict[str, Any]:
    df = pd.read_json(codex_path, lines=True) if codex_path.exists() else pd.DataFrame()
    if df.empty:
        return {
            "total_sessions": 0,
            "first_session": None,
            "last_session": None,
            "daily_counts": [],
            "monthly_counts": [],
            "hourly_profile": [],
        }

    df["start"] = _ensure_datetime(df["start"])
    df["date"] = df["start"].dt.strftime("%Y-%m-%d")
    df["month"] = df["start"].dt.strftime("%Y-%m")
    df["hour"] = df["start"].dt.hour

    total_sessions = int(df.shape[0])
    first_session = df["start"].min()
    last_session = df["start"].max()

    daily_counts = (
        df.groupby("date").size().reset_index(name="count").sort_values("date")
    )
    monthly_counts = (
        df.groupby("month").size().reset_index(name="count").sort_values("month")
    )
    hourly_profile = (
        df.groupby("hour").size().reset_index(name="count").sort_values("hour")
    )

    return {
        "total_sessions": total_sessions,
        "first_session": first_session.isoformat() if pd.notna(first_session) else None,
        "last_session": last_session.isoformat() if pd.notna(last_session) else None,
        "daily_counts": daily_counts.values.tolist(),
        "monthly_counts": monthly_counts.values.tolist(),
        "hourly_profile": hourly_profile.values.tolist(),
    }


# ---------------------------------------------------------------------------
# Atuin
# ---------------------------------------------------------------------------


def load_atuin_history(csv_path: Path) -> DataFrame:
    if not csv_path.exists():
        return pd.DataFrame(columns=["timestamp", "cwd", "command"])
    df = pd.read_csv(
        csv_path,
        names=["timestamp", "duration", "exit_code", "cwd", "command"],
        parse_dates=["timestamp"],
        keep_default_na=False,
    )
    return df


def build_atuin_summary(df: DataFrame) -> Dict[str, Any]:
    if df.empty:
        return {
            "total_commands": 0,
            "daily_counts": [],
            "monthly_counts": [],
            "project_command_counts": [],
            "top_commands": [],
        }

    df["date"] = df["timestamp"].dt.strftime("%Y-%m-%d")
    df["month"] = df["timestamp"].dt.strftime("%Y-%m")

    daily_counts = (
        df.groupby("date").size().reset_index(name="count").sort_values("date")
    )
    monthly_counts = (
        df.groupby("month").size().reset_index(name="count").sort_values("month")
    )

    def project_from_cwd(cwd: str) -> str:
        if not isinstance(cwd, str) or not cwd:
            return "misc"
        parts = Path(cwd.replace("~", "/realm/home")).parts
        if "project" in parts:
            # keep suffix after "project"
            idx = parts.index("project")
            if idx + 1 < len(parts):
                return parts[idx + 1]
        # fall back to final directory name
        return parts[-1] if parts else "misc"

    project_counts = (
        df.assign(project=df["cwd"].map(project_from_cwd))
        .groupby("project")
        .size()
        .reset_index(name="count")
        .sort_values("count", ascending=False)
    )

    command_counts = (
        df.groupby("command")
        .size()
        .reset_index(name="count")
        .sort_values("count", ascending=False)
        .head(50)
    )

    return {
        "total_commands": int(df.shape[0]),
        "daily_counts": daily_counts.values.tolist(),
        "monthly_counts": monthly_counts.values.tolist(),
        "project_command_counts": project_counts.values.tolist(),
        "top_commands": command_counts.values.tolist(),
    }


def build_command_category_pivot(df: DataFrame) -> Dict[str, Counter]:
    result: Dict[str, Counter] = defaultdict(Counter)
    if df.empty:
        return result
    df["date"] = df["timestamp"].dt.strftime("%Y-%m-%d")
    for row in df.itertuples(index=False):
        category = _categorise_command(row.cwd, row.command)
        result[row.date][category] += 1
    return result


# ---------------------------------------------------------------------------
# Git summaries
# ---------------------------------------------------------------------------


def build_git_summary(numstat_path: Path) -> Dict[str, Any]:
    df = pd.read_json(numstat_path, lines=True) if numstat_path.exists() else pd.DataFrame()
    if df.empty:
        return {"per_repo_month": [], "per_month_total": [], "repo_totals": []}

    df["date"] = _ensure_datetime(df["date"])
    df["month"] = df["date"].dt.strftime("%Y-%m")
    df["repo_clean"] = df["repo"].apply(_normalise_repo_path)

    agg_cols = ["lines_added", "lines_deleted", "files_changed"]

    per_repo_month = df.groupby(["month", "repo_clean"]).agg(
        commits=("repo", "count"),
        lines_added=("lines_added", "sum"),
        lines_deleted=("lines_deleted", "sum"),
        files_changed=("files_changed", "sum"),
    )
    per_repo_month = (
        per_repo_month.reset_index()
        .rename(columns={"repo_clean": "repo"})
        .sort_values(["month", "repo"])
    )
    per_repo_month_records = [
        {
            "month": row.month,
            "repo": row.repo,
            "commits": int(row.commits),
            "lines_added": int(row.lines_added),
            "lines_deleted": int(row.lines_deleted),
            "files_changed": int(row.files_changed),
        }
        for row in per_repo_month.itertuples(index=False)
    ]

    per_month_total = df.groupby("month").agg(
        commits=("repo", "count"),
        lines_added=("lines_added", "sum"),
        lines_deleted=("lines_deleted", "sum"),
        files_changed=("files_changed", "sum"),
    )
    per_month_total = per_month_total.reset_index().sort_values("month")
    per_month_total_records = [
        {
            "month": row.month,
            "commits": int(row.commits),
            "lines_added": int(row.lines_added),
            "lines_deleted": int(row.lines_deleted),
            "files_changed": int(row.files_changed),
        }
        for row in per_month_total.itertuples(index=False)
    ]

    repo_totals = df.groupby("repo_clean").agg(
        commits=("repo", "count"),
        lines_added=("lines_added", "sum"),
        lines_deleted=("lines_deleted", "sum"),
        files_changed=("files_changed", "sum"),
    )
    repo_totals = (
        repo_totals.reset_index()
        .rename(columns={"repo_clean": "repo"})
        .sort_values("commits", ascending=False)
    )
    repo_totals_records = [
        {
            "repo": row.repo,
            "commits": int(row.commits),
            "lines_added": int(row.lines_added),
            "lines_deleted": int(row.lines_deleted),
            "files_changed": int(row.files_changed),
        }
        for row in repo_totals.itertuples(index=False)
    ]

    return {
        "per_repo_month": per_repo_month_records,
        "per_month_total": per_month_total_records,
        "repo_totals": repo_totals_records,
    }


def build_git_supporting_summary(df: DataFrame) -> Dict[str, Any]:
    if df.empty:
        return {"daily": [], "weekly": [], "top_days": [], "repo_stats": {}}

    df = df.copy()
    df["date_dt"] = _ensure_datetime(df["date"])
    df["date"] = df["date_dt"].dt.strftime("%Y-%m-%d")
    iso = df["date_dt"].dt.isocalendar()
    df["iso_week"] = iso["year"].astype(str) + "-W" + iso["week"].astype(str).str.zfill(2)
    df["repo_clean"] = df["repo"].apply(_normalise_repo_path)
    df["lines_added"] = df["lines_added"].fillna(0).astype(int)
    df["lines_deleted"] = df["lines_deleted"].fillna(0).astype(int)
    df["files_changed"] = df["files_changed"].fillna(0).astype(int)
    df["lines_changed"] = df["lines_added"].abs() + df["lines_deleted"].abs()

    daily_records: List[Dict[str, Any]] = []
    daily_groups = df.groupby("date")
    for date, group in daily_groups:
        totals = {
            "date": date,
            "lines_changed": int(group["lines_changed"].sum()),
            "lines_added": int(group["lines_added"].sum()),
            "lines_deleted": int(group["lines_deleted"].sum()),
            "files_changed": int(group["files_changed"].sum()),
        }
        repo_breakdown = (
            group.groupby("repo_clean")["lines_changed"]
            .sum()
            .reset_index()
            .sort_values("lines_changed", ascending=False)
        )
        if not repo_breakdown.empty:
            top_row = repo_breakdown.iloc[0]
            totals["top_repo"] = top_row["repo_clean"]
            totals["top_repo_lines"] = int(top_row["lines_changed"])
        daily_records.append(totals)

    daily_records.sort(key=lambda r: r["date"])

    weekly_records: List[Dict[str, Any]] = []
    weekly_groups = df.groupby("iso_week")
    for iso_week, group in weekly_groups:
        repo_counts = (
            group.groupby("repo_clean")["repo_clean"]
            .count()
            .reset_index(name="commits")
            .sort_values("commits", ascending=False)
        )
        weekly_records.append(
            {
                "iso_week": iso_week,
                "lines_changed": int(group["lines_changed"].sum()),
                "top_repos": repo_counts.head(5).values.tolist(),
            }
        )
    weekly_records.sort(key=lambda r: r["iso_week"])

    top_days = sorted(daily_records, key=lambda r: r["lines_changed"], reverse=True)[:10]

    repo_totals = (
        df.groupby("repo_clean")
        .agg(
            commits=("repo_clean", "count"),
            files_changed=("files_changed", "sum"),
            lines_added=("lines_added", "sum"),
            lines_deleted=("lines_deleted", "sum"),
        )
        .reset_index()
    )
    repo_stats = {
        row.repo_clean: {
            "commits": int(row.commits),
            "files_changed": int(row.files_changed),
            "lines_added": int(row.lines_added),
            "lines_deleted": int(row.lines_deleted),
            "net": int(row.lines_added - row.lines_deleted),
        }
        for row in repo_totals.itertuples(index=False)
    }

    return {
        "daily": daily_records,
        "weekly": weekly_records,
        "top_days": top_days,
        "repo_stats": repo_stats,
    }


# ---------------------------------------------------------------------------
# Sleep summary
# ---------------------------------------------------------------------------


def build_sleep_summary(sleep_path: Path) -> Dict[str, Any]:
    df = pd.read_json(sleep_path, lines=True) if sleep_path.exists() else pd.DataFrame()
    if df.empty:
        return {
            "segments": 0,
            "days": 0,
            "segment_histogram": {},
            "daily_totals": {},
            "block_summary": {},
        }

    df["start_local"] = _ensure_datetime(df["start_local"])
    df["end_local"] = _ensure_datetime(df["end_local"])
    df["date"] = df["start_local"].dt.strftime("%Y-%m-%d")
    df["duration_hours"] = (df["end_local"] - df["start_local"]).dt.total_seconds() / 3600.0

    segments = int(df.shape[0])
    days = int(df["date"].nunique())

    per_day = df.groupby("date")["duration_hours"].sum()
    segment_counts = df.groupby("date").size()
    histogram = segment_counts.value_counts().sort_index().to_dict()
    # Keys must be strings to preserve JSON compatibility
    histogram = {str(int(k)): int(v) for k, v in histogram.items()}

    daily_totals = {
        "mean_hours": _round(per_day.mean(), 3) if not per_day.empty else 0.0,
        "median_hours": _round(per_day.median(), 3) if not per_day.empty else 0.0,
        "p90_hours": _round(float(np.percentile(per_day, 90)) if len(per_day) else 0.0, 3),
    }

    df_blocks = (
        df.sort_values(["date", "start_local"])
        .reset_index(drop=True)
        .assign(block=lambda frame: frame.groupby("date").cumcount() + 1)
    )

    block_summary: Dict[str, Dict[str, Any]] = {}
    for block, block_df in df_blocks.groupby("block"):
        durations = block_df["duration_hours"]
        block_summary[str(int(block))] = {
            "mean_hours": _round(durations.mean(), 3),
            "median_hours": _round(durations.median(), 3),
            "count": int(durations.count()),
        }

    return {
        "segments": segments,
        "days": days,
        "segment_histogram": histogram,
        "daily_totals": daily_totals,
        "block_summary": block_summary,
    }


# ---------------------------------------------------------------------------
# Activity timeline (multi-source merge)
# ---------------------------------------------------------------------------


def build_activity_timeline(
    window_daily: List[Dict[str, Any]],
    afk_daily: List[Dict[str, Any]],
    codex_daily: Iterable[Iterable[Any]],
    atuin_daily: Iterable[Iterable[Any]],
    command_categories: Dict[str, Counter],
) -> List[Dict[str, Any]]:
    dates: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"active_hours": 0.0, "afk_hours": 0.0, "window_hours": 0.0}
    )

    for entry in window_daily:
        dates[entry["date"]]["window_hours"] += entry.get("hours", 0.0)

    for entry in afk_daily:
        dates[entry["date"]]["active_hours"] += entry.get("active_hours", 0.0)
        dates[entry["date"]]["afk_hours"] += entry.get("afk_hours", 0.0)

    for date, count in codex_daily:
        dates[date]["codex_sessions"] = dates[date].get("codex_sessions", 0) + int(count)

    for date, count in atuin_daily:
        dates[date]["command_total"] = dates[date].get("command_total", 0) + int(count)

    for date, counter in command_categories.items():
        dates[date]["command_categories"] = {
            key: int(value) for key, value in sorted(counter.items())
        }

    timeline = []
    for date in sorted(dates.keys()):
        payload = {"date": date}
        payload.update(dates[date])
        payload.setdefault("codex_sessions", 0)
        payload.setdefault("command_total", 0)
        payload.setdefault("command_categories", {})
        payload["active_hours"] = _round(payload.get("active_hours", 0.0), 2)
        payload["afk_hours"] = _round(payload.get("afk_hours", 0.0), 2)
        payload["window_hours"] = _round(payload.get("window_hours", 0.0), 2)
        timeline.append(payload)
    return timeline


# ---------------------------------------------------------------------------
# Web bucket snapshot (optional)
# ---------------------------------------------------------------------------


def snapshot_web_bucket(
    aw_api: str, bucket: str, limit: int = 50
) -> Optional[List[Dict[str, Any]]]:
    """Pull recent events from the ActivityWatch HTTP API if reachable."""
    import urllib.error
    import urllib.parse
    import urllib.request

    base = aw_api.rstrip("/")
    url = f"{base}/buckets/{bucket}/events?limit={limit}&order=desc"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = resp.read()
            payload = json.loads(data.decode("utf-8"))
            if isinstance(payload, list):
                return payload
    except (urllib.error.URLError, json.JSONDecodeError):
        return None
    return None


# ---------------------------------------------------------------------------
# CLI Orchestration
# ---------------------------------------------------------------------------


@app.command()
def baseline(
    session_root: Annotated[
        Path, typer.Option(help="Path containing ActivityWatch/Git/Codex exports")
    ] = Path("data/raw/activitywatch/session-2025-10-23"),
    health_root: Annotated[
        Path, typer.Option(help="Directory with merged wearable exports")
    ] = Path("/realm/data/health/processed"),
    output_dir: Annotated[
        Path, typer.Option(help="Directory to place JSON outputs")
    ] = Path("results/latest"),
    include_web_sample: Annotated[
        bool, typer.Option("--include-web-sample", help="Query ActivityWatch web bucket")
    ] = False,
    web_bucket: Annotated[
        Optional[str], typer.Option(help="ActivityWatch web bucket name")
    ] = None,
    activitywatch_api: Annotated[
        str, typer.Option(help="ActivityWatch API base URL")
    ] = "http://127.0.0.1:5600/api/0",
) -> None:
    """Rebuild the baseline analytics suite from local datasets."""
    output_dir.mkdir(parents=True, exist_ok=True)

    windows_path = session_root / "activitywatch_windows.jsonl"
    afk_path = session_root / "activitywatch_afk.jsonl"
    codex_path = session_root / "codex_sessions.jsonl"
    atuin_path = session_root / "atuin_history_last90.csv"
    git_numstat_path = session_root / "git_numstat.jsonl"
    sleep_path = health_root / "sleep_merged.jsonl"

    typer.echo(f"→ Loading ActivityWatch windows from {windows_path}")
    window_summary = build_activitywatch_window_summary(windows_path)
    _write_json(output_dir / "activitywatch_window_summary.json", window_summary)

    typer.echo(f"→ Loading ActivityWatch AFK from {afk_path}")
    afk_summary = build_activitywatch_afk_summary(afk_path)
    _write_json(output_dir / "activitywatch_afk_summary.json", afk_summary)

    afk_window_stats = build_activitywatch_afk_window(afk_path)
    if afk_window_stats:
        _write_json(output_dir / "activitywatch_afk_window.json", afk_window_stats)

    typer.echo(f"→ Summarising Codex sessions from {codex_path}")
    codex_summary = build_codex_summary(codex_path)
    _write_json(output_dir / "codex_sessions_summary.json", codex_summary)

    typer.echo(f"→ Summarising Atuin history from {atuin_path}")
    atuin_df = load_atuin_history(atuin_path)
    atuin_summary = build_atuin_summary(atuin_df)
    _write_json(output_dir / "atuin_summary.json", atuin_summary)

    typer.echo(f"→ Summarising git activity from {git_numstat_path}")
    git_summary = build_git_summary(git_numstat_path)
    _write_json(output_dir / "git_activity_summary.json", git_summary)

    if git_numstat_path.exists():
        typer.echo("→ Preserving git_numstat.jsonl and building supporting stats")
        shutil.copy2(git_numstat_path, output_dir / "git_numstat.jsonl")
        supporting_dir = output_dir / "supporting"
        supporting_dir.mkdir(exist_ok=True)
        df_git = pd.read_json(git_numstat_path, lines=True)
        git_supporting = build_git_supporting_summary(df_git)
        _write_json(supporting_dir / "git_numstat_summary.json", git_supporting)

    typer.echo(f"→ Summarising merged sleep segments from {sleep_path}")
    sleep_summary = build_sleep_summary(sleep_path)
    _write_json(output_dir / "sleep_summary.json", sleep_summary)

    typer.echo("→ Building daily activity timeline")
    command_categories = build_command_category_pivot(atuin_df)
    timeline = build_activity_timeline(
        window_summary.get("daily_totals", []),
        afk_summary.get("daily", []),
        codex_summary.get("daily_counts", []),
        atuin_summary.get("daily_counts", []),
        command_categories,
    )
    _write_json(output_dir / "activity_timeline.json", timeline)

    if include_web_sample and web_bucket:
        typer.echo(f"→ Sampling ActivityWatch web bucket {web_bucket}")
        sample = snapshot_web_bucket(activitywatch_api, web_bucket)
        if sample:
            _write_json(output_dir / "activitywatch_web_sample.json", sample)
        else:
            typer.echo("   ! Unable to fetch web bucket data; skipping.", err=True)

    typer.echo(f"✓ Baseline rebuild complete → {output_dir}")


if __name__ == "__main__":
    app()
