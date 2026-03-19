"""Sleep summary helpers for baseline rebuilds."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from pandas import DataFrame

from ...sources.exports import sleep as lp_sleep
from .shared import ensure_datetime, round_metric


def sleep_summary_from_segments(df: DataFrame) -> dict[str, object]:
    if df.empty:
        return {
            "segments": 0,
            "days": 0,
            "segment_histogram": {},
            "daily_totals": {},
            "block_summary": {},
        }

    df = df.copy()
    df["start_local"] = ensure_datetime(df["start_local"])
    df["end_local"] = ensure_datetime(df["end_local"])
    df["date"] = df["start_local"].dt.strftime("%Y-%m-%d")
    df["duration_hours"] = (
        df["end_local"] - df["start_local"]
    ).dt.total_seconds() / 3600.0

    segments = int(df.shape[0])
    days = int(df["date"].nunique())

    per_day = df.groupby("date")["duration_hours"].sum()
    segment_counts = df.groupby("date").size()
    histogram = segment_counts.value_counts().sort_index().to_dict()
    histogram = {str(int(key)): int(value) for key, value in histogram.items()}

    daily_totals = {
        "mean_hours": round_metric(per_day.mean(), 3) if not per_day.empty else 0.0,
        "median_hours": round_metric(per_day.median(), 3) if not per_day.empty else 0.0,
        "p90_hours": round_metric(
            float(np.percentile(per_day, 90)) if len(per_day) else 0.0,
            3,
        ),
    }

    df_blocks = (
        df.sort_values(["date", "start_local"])
        .reset_index(drop=True)
        .assign(block=lambda frame: frame.groupby("date").cumcount() + 1)
    )

    block_summary: dict[str, dict[str, object]] = {}
    for block, block_df in df_blocks.groupby("block"):
        durations = block_df["duration_hours"]
        block_summary[str(int(block))] = {
            "mean_hours": round_metric(durations.mean(), 3),
            "median_hours": round_metric(durations.median(), 3),
            "count": int(durations.count()),
        }

    return {
        "segments": segments,
        "days": days,
        "segment_histogram": histogram,
        "daily_totals": daily_totals,
        "block_summary": block_summary,
    }


def build_sleep_summary_from_file(sleep_path: Path) -> dict[str, object]:
    df = pd.read_json(sleep_path, lines=True) if sleep_path.exists() else pd.DataFrame()
    return sleep_summary_from_segments(df)


def build_sleep_summary_from_entries(
    entries: Iterable[lp_sleep.SleepEntry],
) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for entry in entries:
        for segment in entry.segments:
            rows.append(
                {
                    "start_local": segment.start,
                    "end_local": segment.end,
                }
            )
    df = pd.DataFrame(rows, columns=["start_local", "end_local"])
    return sleep_summary_from_segments(df)
