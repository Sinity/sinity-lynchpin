"""Codex session loaders and summaries for baseline rebuilds."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from pandas import DataFrame

from ...sources.captures import codex as lp_codex
from .shared import ensure_datetime


def extract_codex_sessions(
    sessions_root: Path,
    since_ts: pd.Timestamp,
    until_ts: pd.Timestamp,
) -> DataFrame:
    rows = [
        {"start": session.start}
        for session in lp_codex.iter_sessions(
            start=since_ts.to_pydatetime(),
            end=until_ts.to_pydatetime(),
            root=sessions_root,
        )
    ]
    return pd.DataFrame(rows, columns=["start"])


def load_codex_sessions(
    bundle_path: Path,
    mode: str,
    sessions_root: Path,
    since_ts: pd.Timestamp,
    until_ts: pd.Timestamp,
) -> DataFrame:
    if mode in {"bundle", "auto"} and bundle_path.exists():
        df = pd.read_json(bundle_path, lines=True)
        df["start"] = df.get(
            "start",
            df.get("timestamp", pd.Series(dtype="datetime64[ns]")),
        )
        df["start"] = ensure_datetime(df["start"])
        return df
    if mode == "bundle":
        raise FileNotFoundError(f"Missing Codex sessions export: {bundle_path}")
    return extract_codex_sessions(sessions_root, since_ts, until_ts)


def build_codex_summary(df: DataFrame) -> dict[str, object]:
    if df.empty:
        return {
            "total_sessions": 0,
            "first_session": None,
            "last_session": None,
            "daily_counts": [],
            "monthly_counts": [],
            "hourly_profile": [],
        }

    df = df.copy()
    df["start"] = ensure_datetime(df["start"])
    df["date"] = df["start"].dt.strftime("%Y-%m-%d")
    df["month"] = df["start"].dt.strftime("%Y-%m")
    df["hour"] = df["start"].dt.hour

    total_sessions = int(df.shape[0])
    first_session = df["start"].min()
    last_session = df["start"].max()

    daily_counts = df.groupby("date").size().reset_index(name="count").sort_values("date")
    monthly_counts = df.groupby("month").size().reset_index(name="count").sort_values("month")
    hourly_profile = df.groupby("hour").size().reset_index(name="count").sort_values("hour")

    return {
        "total_sessions": total_sessions,
        "first_session": first_session.isoformat() if pd.notna(first_session) else None,
        "last_session": last_session.isoformat() if pd.notna(last_session) else None,
        "daily_counts": daily_counts.values.tolist(),
        "monthly_counts": monthly_counts.values.tolist(),
        "hourly_profile": hourly_profile.values.tolist(),
    }
