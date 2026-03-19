"""Atuin history loaders and summaries for baseline rebuilds."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pandas as pd
from pandas import DataFrame

from ...sources.captures import atuin as lp_atuin
from .shared import categorise_command, to_utc_timestamp


def atuin_live_history(
    db_path: Path,
    since_ts: pd.Timestamp,
    until_ts: pd.Timestamp,
) -> pd.DataFrame:
    db = Path(db_path).expanduser()
    columns = ["timestamp", "duration", "exit_code", "cwd", "command"]
    if not db.exists():
        return pd.DataFrame(columns=columns)

    start_dt = since_ts.to_pydatetime()
    end_dt = until_ts.to_pydatetime()
    rows: list[dict[str, Any]] = []
    for cmd in lp_atuin.iter_commands(start=start_dt, end=end_dt, db_path=db):
        rows.append(
            {
                "timestamp": to_utc_timestamp(cmd.timestamp),
                "duration": cmd.duration_ns,
                "exit_code": cmd.exit_code,
                "cwd": cmd.cwd,
                "command": cmd.command,
            }
        )

    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns)


def extract_atuin_history(
    db_path: Path,
    since_ts: pd.Timestamp,
    until_ts: pd.Timestamp,
) -> DataFrame:
    return atuin_live_history(db_path, since_ts, until_ts)


def load_atuin_history(
    bundle_path: Path,
    mode: str,
    db_path: Path,
    since_ts: pd.Timestamp,
    until_ts: pd.Timestamp,
) -> DataFrame:
    if mode in {"bundle", "auto"} and bundle_path.exists():
        return pd.read_csv(
            bundle_path,
            names=["timestamp", "duration", "exit_code", "cwd", "command"],
            parse_dates=["timestamp"],
            keep_default_na=False,
        )
    if mode == "bundle":
        raise FileNotFoundError(f"Missing Atuin CSV export: {bundle_path}")
    return extract_atuin_history(db_path, since_ts, until_ts)


def build_atuin_summary(df: DataFrame) -> dict[str, object]:
    if df.empty:
        return {
            "total_commands": 0,
            "daily_counts": [],
            "monthly_counts": [],
            "project_command_counts": [],
            "top_commands": [],
        }

    df = df.copy()
    df["date"] = df["timestamp"].dt.strftime("%Y-%m-%d")
    df["month"] = df["timestamp"].dt.strftime("%Y-%m")

    daily_counts = df.groupby("date").size().reset_index(name="count").sort_values("date")
    monthly_counts = df.groupby("month").size().reset_index(name="count").sort_values("month")

    def project_from_cwd(cwd: str) -> str:
        if not isinstance(cwd, str) or not cwd:
            return "misc"
        parts = Path(cwd.replace("~", "/realm/home")).parts
        if "project" in parts:
            idx = parts.index("project")
            if idx + 1 < len(parts):
                return parts[idx + 1]
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


def build_command_category_pivot(df: DataFrame) -> dict[str, Counter]:
    result: dict[str, Counter] = defaultdict(Counter)
    if df.empty:
        return result
    df = df.copy()
    df["date"] = df["timestamp"].dt.strftime("%Y-%m-%d")
    for row in df.itertuples(index=False):
        category = categorise_command(row.cwd, row.command)
        result[row.date][category] += 1
    return result
