"""ActivityWatch loaders and summaries for the baseline rebuild."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from pandas import DataFrame

from ...sources.captures import activitywatch as lp_activitywatch
from .shared import ensure_datetime, host_from_bucket, round_metric, to_utc_timestamp


def activitywatch_live_events(
    kind: str,
    db_path: Path,
    since_ts: pd.Timestamp,
    until_ts: pd.Timestamp,
    defaults: dict[str, Any],
) -> pd.DataFrame:
    db = Path(db_path).expanduser()
    columns = ["bucket", "start", "end", "duration_seconds", *defaults.keys()]
    if not db.exists():
        return pd.DataFrame(columns=columns)

    iterator_map = {
        "window": lp_activitywatch.window_events,
        "afk": lp_activitywatch.afk_events,
    }
    if kind not in iterator_map:
        raise ValueError(f"Unsupported ActivityWatch kind: {kind}")

    start_dt = since_ts.to_pydatetime()
    end_dt = until_ts.to_pydatetime()
    iterator = iterator_map[kind](start=start_dt, end=end_dt, db_path=db)

    rows: list[dict[str, Any]] = []
    for event in iterator:
        start = to_utc_timestamp(event.start)
        end = to_utc_timestamp(event.end)
        clipped_start = max(start, since_ts)
        clipped_end = min(end, until_ts)
        duration = float((clipped_end - clipped_start).total_seconds())
        if duration <= 0:
            continue
        payload = event.data or {}
        record: dict[str, Any] = {
            "bucket": event.bucket,
            "start": clipped_start,
            "end": clipped_end,
            "duration_seconds": duration,
        }
        for key, default in defaults.items():
            value = payload.get(key, default)
            record[key] = value if value is not None else default
        rows.append(record)

    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns)


def load_activitywatch_windows(
    bundle_path: Path,
    mode: str,
    db_path: Path,
    since_ts: pd.Timestamp,
    until_ts: pd.Timestamp,
) -> pd.DataFrame:
    """Return an ActivityWatch windows event frame (bundle export or live DB)."""
    if mode in {"bundle", "auto"} and bundle_path.exists():
        df = pd.read_json(bundle_path, lines=True)
        df["bucket"] = df.get("bucket", "unknown")
        df["start"] = ensure_datetime(
            df.get("start", pd.Series(dtype="datetime64[ns]"))
        )
        df["duration_seconds"] = df.get("duration_seconds", 0.0)
        df["app"] = df.get("app", "unknown")
        return df
    if mode == "bundle":
        raise FileNotFoundError(f"Missing ActivityWatch windows export: {bundle_path}")
    return activitywatch_live_events(
        "window",
        db_path,
        since_ts,
        until_ts,
        defaults={"app": "unknown"},
    )


def load_activitywatch_afk(
    bundle_path: Path,
    mode: str,
    db_path: Path,
    since_ts: pd.Timestamp,
    until_ts: pd.Timestamp,
) -> pd.DataFrame:
    """Return an ActivityWatch AFK event frame (bundle export or live DB)."""
    if mode in {"bundle", "auto"} and bundle_path.exists():
        df = pd.read_json(bundle_path, lines=True)
        df["bucket"] = df.get("bucket", "unknown")
        df["start"] = ensure_datetime(
            df.get("start", pd.Series(dtype="datetime64[ns]"))
        )
        df["end"] = ensure_datetime(df.get("end", pd.Series(dtype="datetime64[ns]")))
        df["duration_seconds"] = df.get("duration_seconds", 0.0)
        df["status"] = df.get("status", "unknown")
        return df
    if mode == "bundle":
        raise FileNotFoundError(f"Missing ActivityWatch AFK export: {bundle_path}")
    return activitywatch_live_events(
        "afk",
        db_path,
        since_ts,
        until_ts,
        defaults={"status": "unknown"},
    )


def build_activitywatch_window_summary(df: DataFrame) -> dict[str, Any]:
    if df.empty:
        return {"daily_totals": [], "monthly_totals": [], "top_apps_monthly": {}}

    df = df.copy()
    df["start"] = ensure_datetime(df["start"])
    df["host"] = df["bucket"].apply(
        lambda bucket: host_from_bucket(bucket, "aw-watcher-window_")
    )
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
            "hours": round_metric(row.duration_seconds / 3600.0, 3),
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
            "hours": round_metric(row.duration_seconds / 3600.0, 1),
        }
        for row in monthly.itertuples(index=False)
    ]

    top_apps: dict[str, list[dict[str, Any]]] = {}
    df_apps = (
        df.groupby(["month", "host", "app"])["duration_seconds"]
        .sum()
        .reset_index()
        .rename(columns={"duration_seconds": "seconds"})
    )
    if not df_apps.empty:
        for (month, host), chunk in df_apps.groupby(["month", "host"]):
            rows = chunk.sort_values("seconds", ascending=False).assign(
                hours=lambda frame: frame["seconds"] / 3600.0
            )
            key = f"{month}::{host}"
            top_apps[key] = [
                {"app": row.app, "hours": round_metric(row.hours, 1)}
                for row in rows.itertuples(index=False)
            ]

    return {
        "daily_totals": daily_records,
        "monthly_totals": monthly_records,
        "top_apps_monthly": top_apps,
    }


def build_activitywatch_afk_summary(df: DataFrame) -> dict[str, Any]:
    if df.empty:
        return {"daily": [], "monthly": []}

    df = df.copy()
    df["start"] = ensure_datetime(df["start"])
    df["end"] = ensure_datetime(df["end"])
    df["host"] = df["bucket"].apply(
        lambda bucket: host_from_bucket(bucket, "aw-watcher-afk_")
    )
    df["date"] = df["start"].dt.strftime("%Y-%m-%d")
    df["month"] = df["start"].dt.strftime("%Y-%m")
    df["duration_seconds"] = df["duration_seconds"].fillna(0.0)

    def aggregate(group_cols: list[str]) -> list[dict[str, Any]]:
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
        pivot = pivot.rename(columns={"not-afk": "not_afk"})
        records: list[dict[str, Any]] = []
        for row in pivot.itertuples(index=False):
            row_dict = row._asdict()
            payload = {col: row_dict.get(col) for col in group_cols}
            payload["active_hours"] = round_metric(
                row_dict.get("not_afk", 0.0) / 3600.0,
                2,
            )
            payload["afk_hours"] = round_metric(row_dict.get("afk", 0.0) / 3600.0, 2)
            records.append(payload)
        return sorted(records, key=lambda item: tuple(item[col] for col in group_cols))

    return {
        "daily": aggregate(["date", "host"]),
        "monthly": aggregate(["month", "host"]),
    }


def build_activitywatch_afk_window(df: DataFrame) -> dict[str, Any]:
    if df.empty:
        return {}

    df = df.copy()
    df["start"] = ensure_datetime(df["start"])
    df["end"] = ensure_datetime(df["end"])
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
        "afk_long_hours": round_metric(long_hours, 2),
        "afk_long_avg_hours": round_metric(
            (long_hours / long_blocks.shape[0]) if long_blocks.shape[0] else 0.0,
            1,
        ),
        "afk_short_blocks": int(short_blocks.shape[0]),
        "afk_short_hours": round_metric(short_hours, 2),
        "active_hours": round_metric(active_hours, 2),
    }


def snapshot_web_bucket(
    aw_api: str,
    bucket: str,
    limit: int = 50,
) -> Optional[list[dict[str, Any]]]:
    """Pull recent events from the ActivityWatch HTTP API if reachable."""
    import urllib.error
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
