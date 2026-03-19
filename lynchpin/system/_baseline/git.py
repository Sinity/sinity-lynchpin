"""Git activity loaders and summaries for baseline rebuilds."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd
from pandas import DataFrame

from ...sources.indices import gitstats as lp_gitstats
from .shared import ensure_datetime, normalise_repo_path


def extract_git_numstat(
    repos: list[Path],
    git_since_ts: Optional[pd.Timestamp],
    until_ts: pd.Timestamp,
) -> DataFrame:
    records = list(
        lp_gitstats.iter_numstat(
            repos,
            since=git_since_ts.to_pydatetime() if git_since_ts is not None else None,
            until=until_ts.to_pydatetime(),
        )
    )
    return pd.DataFrame(records)


def load_git_numstat(
    bundle_path: Path,
    mode: str,
    repos: list[Path],
    git_since_ts: Optional[pd.Timestamp],
    until_ts: pd.Timestamp,
) -> DataFrame:
    if mode in {"bundle", "auto"} and bundle_path.exists():
        return pd.read_json(bundle_path, lines=True)
    if mode == "bundle":
        raise FileNotFoundError(f"Missing git numstat export: {bundle_path}")
    return extract_git_numstat(repos, git_since_ts, until_ts)


def build_git_summary(df: DataFrame) -> dict[str, object]:
    if df.empty:
        return {"per_repo_month": [], "per_month_total": [], "repo_totals": []}

    df = df.copy()
    df["date"] = ensure_datetime(df["date"])
    df["month"] = df["date"].dt.strftime("%Y-%m")
    df["repo_clean"] = df["repo"].apply(normalise_repo_path)
    df["lines_added"] = df.get("lines_added", 0).fillna(0).astype(int)
    df["lines_deleted"] = df.get("lines_deleted", 0).fillna(0).astype(int)
    df["files_changed"] = df.get("files_changed", 0).fillna(0).astype(int)

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


def build_git_supporting_summary(df: DataFrame) -> dict[str, object]:
    if df.empty:
        return {"daily": [], "weekly": [], "top_days": [], "repo_stats": {}}

    df = df.copy()
    df["date_dt"] = ensure_datetime(df["date"])
    df["date"] = df["date_dt"].dt.strftime("%Y-%m-%d")
    iso = df["date_dt"].dt.isocalendar()
    df["iso_week"] = (
        iso["year"].astype(str) + "-W" + iso["week"].astype(str).str.zfill(2)
    )
    df["repo_clean"] = df["repo"].apply(normalise_repo_path)
    df["lines_added"] = df["lines_added"].fillna(0).astype(int)
    df["lines_deleted"] = df["lines_deleted"].fillna(0).astype(int)
    df["files_changed"] = df["files_changed"].fillna(0).astype(int)
    df["lines_changed"] = df["lines_added"].abs() + df["lines_deleted"].abs()

    daily_records: list[dict[str, object]] = []
    for date, group in df.groupby("date"):
        totals: dict[str, object] = {
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

    daily_records.sort(key=lambda row: row["date"])

    weekly_records: list[dict[str, object]] = []
    for iso_week, group in df.groupby("iso_week"):
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
    weekly_records.sort(key=lambda row: row["iso_week"])

    top_days = sorted(
        daily_records,
        key=lambda row: row["lines_changed"],
        reverse=True,
    )[:10]

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
