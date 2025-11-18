#!/usr/bin/env python3
"""Generate an enriched multi-source focus analytics portal."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import pandas as pd
import plotly.graph_objects as go
from plotly.offline import plot

from focus_data import (
    DEFAULT_ACTIVITYWATCH_DB,
    DEV_CATEGORY_NAMES,
    LOCAL_TZ,
    REPO_PATHS,
    SUSPICIOUS_SESSION_HOURS,
    UTC,
    add_time_columns,
    aggregate_weekly,
    append_total_row,
    build_weekly_summary,
    classify_segment,
    load_activitywatch_segments,
    mark_suspicious_segments,
    merge_adjacent_blocks,
    parse_git_log,
    summarise_top_sessions,
)


DEFAULT_ATUIN_DB = Path("~/.local/share/atuin/history.db").expanduser()
PLOT_CONFIG = {"displayModeBar": True, "displaylogo": False, "responsive": True}


def _parse_local_datetime(value: str) -> Tuple[datetime, bool]:
    """Return a timezone-aware local datetime and a flag if the input was date-only."""

    try:
        if len(value) == 10:
            dt_date = datetime.strptime(value, "%Y-%m-%d").date()
            return datetime.combine(dt_date, time.min, tzinfo=LOCAL_TZ), True
    except ValueError:
        pass

    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LOCAL_TZ)
    else:
        dt = dt.astimezone(LOCAL_TZ)
    return dt, False


def load_activitywatch_context(
    db_path: Path, start: datetime, end: datetime
) -> Dict[str, Any]:
    segments = load_activitywatch_segments(db_path, start, end)
    if segments.empty:
        return {
            "segments": segments,
            "timeline": [],
            "week_summary_all": pd.DataFrame(),
            "week_summary_filtered": pd.DataFrame(),
            "week_summary_total_all": pd.DataFrame(),
            "week_summary_total_filtered": pd.DataFrame(),
            "category_totals": pd.DataFrame(),
            "merged": pd.DataFrame(),
            "suspicious": pd.DataFrame(),
            "llm_sessions": pd.DataFrame(),
        }

    classified = segments.copy()
    classified[["category", "label"]] = classified.apply(
        lambda row: pd.Series(classify_segment(row["app"], row["title"])), axis=1
    )
    classified = add_time_columns(classified)

    merged = merge_adjacent_blocks(classified)
    merged["suspicious"] = merged["duration_h"] >= SUSPICIOUS_SESSION_HOURS

    suspicious_blocks = merged.loc[merged["suspicious"]].copy()
    classified = mark_suspicious_segments(classified, suspicious_blocks)

    category_totals = (
        classified.groupby("category")["duration_h"].sum().reset_index().sort_values("duration_h", ascending=False)
    )

    week_summary_all = build_weekly_summary(classified, category_totals["category"].tolist(), DEV_CATEGORY_NAMES)
    week_summary_filtered = build_weekly_summary(
        classified.loc[~classified["suspicious"]],
        category_totals["category"].tolist(),
        DEV_CATEGORY_NAMES,
    )

    week_summary_total_all = append_total_row(week_summary_all, category_totals["category"].tolist())
    week_summary_total_filtered = append_total_row(
        week_summary_filtered, category_totals["category"].tolist()
    )

    llm_sessions = merged.loc[merged["category"] == "LLM"].copy()
    llm_sessions = llm_sessions.sort_values("start")

    timeline = []
    for _, row in merged.iterrows():
        timeline.append(
            {
                "category": row["category"],
                "label": row.get("label", row["category"]),
                "start": row["start"].isoformat(),
                "end": row["end"].isoformat(),
                "start_local": row["start_local"].isoformat() if "start_local" in row else row["start"].astimezone(LOCAL_TZ).isoformat(),
                "end_local": row["end_local"].isoformat() if "end_local" in row else row["end"].astimezone(LOCAL_TZ).isoformat(),
                "duration_h": row["duration_h"],
                "sample": next((t for t in row.get("details", []) if t), row.get("title", "")),
                "suspicious": bool(row.get("suspicious", False)),
            }
        )

    return {
        "segments": classified,
        "timeline": timeline,
        "week_summary_all": week_summary_all,
        "week_summary_filtered": week_summary_filtered,
        "week_summary_total_all": week_summary_total_all,
        "week_summary_total_filtered": week_summary_total_filtered,
        "category_totals": category_totals,
        "merged": merged,
        "suspicious": suspicious_blocks,
        "llm_sessions": llm_sessions,
    }


def load_atuin_history(db_path: Path, start: datetime, end: datetime) -> pd.DataFrame:
    if not db_path.exists():
        return pd.DataFrame(columns=["timestamp", "duration", "exit", "command", "cwd", "session", "hostname"])

    conn = sqlite3.connect(db_path)
    start_ns = int(start.timestamp() * 1e9)
    end_ns = int(end.timestamp() * 1e9)
    query = """
        SELECT timestamp, duration, exit, command, cwd, session, hostname
        FROM history
        WHERE deleted_at IS NULL
          AND timestamp BETWEEN ? AND ?
    """
    df = pd.read_sql_query(query, conn, params=(start_ns, end_ns))
    conn.close()

    if df.empty:
        return df

    df["ts_utc"] = pd.to_datetime(df["timestamp"], unit="ns", utc=True)
    df["ts_local"] = df["ts_utc"].dt.tz_convert(LOCAL_TZ)
    df["date"] = df["ts_local"].dt.date
    df["hour"] = df["ts_local"].dt.hour
    df["weekday"] = df["ts_local"].dt.day_name()
    df["iso_week"] = df["ts_local"].dt.isocalendar().week
    return df


def _project_from_cwd(cwd: str) -> str:
    if not isinstance(cwd, str) or not cwd:
        return "misc"
    parts = Path(cwd.replace("~", "/realm/home")).parts
    if "project" in parts:
        idx = parts.index("project")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return parts[-1] if parts else "misc"


def build_atuin_summary(df: pd.DataFrame) -> Dict[str, Any]:
    if df.empty:
        return {
            "total": 0,
            "daily": [],
            "hour_matrix": [],
            "weekday_hour": [],
            "top_commands": [],
            "top_projects": [],
        }

    daily = (
        df.groupby("date").size().reset_index(name="count").sort_values("date")
    )
    hour_matrix = (
        df.groupby(["date", "hour"]).size().reset_index(name="count").sort_values(["date", "hour"])
    )
    weekday_hour = (
        df.groupby(["weekday", "hour"]).size().reset_index(name="count").sort_values(["weekday", "hour"])
    )
    top_commands = (
        df.groupby("command").size().reset_index(name="count").sort_values("count", ascending=False).head(30)
    )
    top_projects = (
        df.assign(project=df["cwd"].map(_project_from_cwd))
        .groupby("project")
        .size()
        .reset_index(name="count")
        .sort_values("count", ascending=False)
    )

    return {
        "total": int(df.shape[0]),
        "daily": daily,
        "hour_matrix": hour_matrix,
        "weekday_hour": weekday_hour,
        "top_commands": top_commands,
        "top_projects": top_projects,
    }


def build_git_summary(start: datetime, end: datetime) -> Dict[str, Any]:
    repo_totals: List[Dict[str, Any]] = []
    weekly_rows: List[Dict[str, Any]] = []
    highlights: List[Dict[str, Any]] = []

    for repo_name, repo_path in REPO_PATHS.items():
        if not repo_path.exists():
            continue
        commits = parse_git_log(repo_path, start, end)
        if not commits:
            continue

        repo_totals.append(
            {
                "repository": repo_name,
                "commits": len(commits),
                "additions": sum(c.additions for c in commits),
                "deletions": sum(c.deletions for c in commits),
            }
        )

        for commit in commits:
            local_dt = commit.date.astimezone(LOCAL_TZ)
            iso = local_dt.isocalendar()
            weekly_rows.append(
                {
                    "week": f"{iso.year}-W{iso.week:02d}",
                    "repo": repo_name,
                    "commits": 1,
                    "additions": commit.additions,
                    "deletions": commit.deletions,
                }
            )

            top_files = sorted(commit.files, key=lambda item: item[1] + item[2], reverse=True)[:3]
            highlights.append(
                {
                    "repo": repo_name,
                    "commit": commit.commit,
                    "date": local_dt,
                    "subject": commit.subject,
                    "additions": commit.additions,
                    "deletions": commit.deletions,
                    "top_files": top_files,
                }
            )

    weekly_df = pd.DataFrame(weekly_rows)
    if not weekly_df.empty:
        weekly_df = (
            weekly_df.groupby(["week", "repo"], as_index=False)
            .sum(numeric_only=True)
            .sort_values(["week", "repo"])
        )

    repo_totals_df = pd.DataFrame(repo_totals).sort_values("commits", ascending=False)
    highlights.sort(key=lambda item: item["date"], reverse=True)

    return {
        "weekly": weekly_df,
        "repo_totals": repo_totals_df,
        "highlights": highlights,
    }


def _format_hours(value: float) -> str:
    return f"{value:.2f}"


def build_activity_charts(activity: Dict[str, Any]) -> Dict[str, str]:
    charts: Dict[str, str] = {}

    category_names = activity["category_totals"]["category"].tolist()

    weekly_all = activity["week_summary_all"].copy()
    weekly_filtered = activity["week_summary_filtered"].copy()

    def _stacked(df: pd.DataFrame, title: str) -> str:
        fig = go.Figure()
        for category in category_names:
            if category not in df.columns:
                continue
            fig.add_trace(
                go.Bar(
                    name=category,
                    x=df["week"],
                    y=df[category],
                    hovertemplate="%{x}<br>%{y:.2f} h<extra>" + category + "</extra>",
                )
            )
        fig.update_layout(
            barmode="stack",
            title=title,
            xaxis_title="Week",
            yaxis_title="Active hours",
            legend_title="Category",
            template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )
        return plot(fig, include_plotlyjs=False, output_type="div", auto_open=False, config=PLOT_CONFIG)

    charts["weekly_all"] = _stacked(weekly_all, "Weekly category hours (all sessions)")
    charts["weekly_filtered"] = _stacked(
        weekly_filtered, "Weekly category hours (suspicious filtered)"
    )

    dev_fig = go.Figure()
    dev_fig.add_trace(
        go.Scatter(
            name="Dev hours",
            x=weekly_all["week"],
            y=weekly_all["DevHours"],
            mode="lines+markers",
        )
    )
    dev_fig.add_trace(
        go.Scatter(
            name="Total active",
            x=weekly_all["week"],
            y=weekly_all["Total"],
            mode="lines+markers",
        )
    )
    dev_fig.update_layout(
        title="Development focus vs total active time",
        xaxis_title="Week",
        yaxis_title="Hours",
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    charts["dev_curve"] = plot(dev_fig, include_plotlyjs=False, output_type="div", auto_open=False, config=PLOT_CONFIG)

    if activity["timeline"]:
        timeline_fig = go.Figure()
        color_map: Dict[str, str] = {}
        palette = [
            "#8fd3ff",
            "#6fd5b9",
            "#f7a6ff",
            "#ffc46b",
            "#ff8a9a",
            "#c4f08a",
            "#a6a8ff",
        ]
        idx = 0
        legend_seen: Dict[str, bool] = {}
        for entry in activity["timeline"]:
            category = entry["category"]
            if category not in color_map:
                color_map[category] = palette[idx % len(palette)]
                idx += 1

            start = datetime.fromisoformat(entry["start_local"]).replace(tzinfo=None)
            end = datetime.fromisoformat(entry["end_local"]).replace(tzinfo=None)
            y = datetime.fromisoformat(entry["start_local"]).strftime("%Y-%m-%d")
            timeline_fig.add_trace(
                go.Scatter(
                    x=[start, end],
                    y=[y, y],
                    mode="lines",
                    line={"width": 12, "color": color_map[category]},
                    name=category,
                    legendgroup=category,
                    showlegend=not legend_seen.get(category, False),
                    hovertemplate=(
                        f"{entry['label']}<br>%{{x|%Y-%m-%d %H:%M}}" " → %{x|%H:%M}" "<br>{_format_hours(entry['duration_h'])} h"
                    ),
                )
            )
            legend_seen[category] = True

        timeline_fig.update_layout(
            title="Timeline of merged focus blocks",
            xaxis_title="Local time",
            yaxis_title="Day",
            yaxis={"type": "category", "categoryorder": "category ascending"},
            template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            height=600,
        )
        charts["timeline"] = plot(
            timeline_fig,
            include_plotlyjs=False,
            output_type="div",
            auto_open=False,
            config=PLOT_CONFIG,
        )
    else:
        charts["timeline"] = "<p>No ActivityWatch segments available for the requested range.</p>"

    return charts


def build_atuin_charts(summary: Dict[str, Any]) -> Dict[str, str]:
    charts: Dict[str, str] = {}
    daily = summary["daily"]
    if isinstance(daily, pd.DataFrame) and not daily.empty:
        daily_fig = go.Figure()
        daily_fig.add_trace(
            go.Bar(
                x=daily["date"],
                y=daily["count"],
                marker_color="#8fd3ff",
            )
        )
        daily_fig.update_layout(
            title="Shell commands per day",
            xaxis_title="Date",
            yaxis_title="Commands",
            template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )
        charts["daily"] = plot(daily_fig, include_plotlyjs=False, output_type="div", auto_open=False, config=PLOT_CONFIG)
    else:
        charts["daily"] = "<p>No Atuin commands recorded.</p>"

    hour_matrix = summary["hour_matrix"]
    if isinstance(hour_matrix, pd.DataFrame) and not hour_matrix.empty:
        pivot = hour_matrix.pivot(index="hour", columns="date", values="count").fillna(0)
        heatmap_fig = go.Figure(
            go.Heatmap(
                z=pivot.values,
                x=pivot.columns.astype(str),
                y=pivot.index,
                colorscale="YlGnBu",
                hovertemplate="%{x} @ %{y}: %{z} commands<extra></extra>",
            )
        )
        heatmap_fig.update_layout(
            title="Command intensity by hour",
            xaxis_title="Date",
            yaxis_title="Hour of day",
            template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )
        charts["hourly"] = plot(heatmap_fig, include_plotlyjs=False, output_type="div", auto_open=False, config=PLOT_CONFIG)
    else:
        charts["hourly"] = "<p>No hourly command data.</p>"

    return charts


def build_git_charts(summary: Dict[str, Any]) -> Dict[str, str]:
    charts: Dict[str, str] = {}

    repo_totals = summary["repo_totals"]
    if isinstance(repo_totals, pd.DataFrame) and not repo_totals.empty:
        fig = go.Figure()
        fig.add_trace(
            go.Bar(
                x=repo_totals["repository"],
                y=repo_totals["commits"],
                marker_color="#f7a6ff",
            )
        )
        fig.update_layout(
            title="Commits per repository",
            xaxis_title="Repository",
            yaxis_title="Commits",
            template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )
        charts["repo_commits"] = plot(fig, include_plotlyjs=False, output_type="div", auto_open=False, config=PLOT_CONFIG)
    else:
        charts["repo_commits"] = "<p>No commits detected in the selected range.</p>"

    weekly = summary["weekly"]
    if isinstance(weekly, pd.DataFrame) and not weekly.empty:
        pivot = weekly.pivot(index="week", columns="repo", values="commits").fillna(0)
        fig = go.Figure()
        for repo in pivot.columns:
            fig.add_trace(
                go.Scatter(
                    name=repo,
                    x=pivot.index,
                    y=pivot[repo],
                    mode="lines+markers",
                )
            )
        fig.update_layout(
            title="Weekly commit cadence",
            xaxis_title="ISO week",
            yaxis_title="Commits",
            template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )
        charts["weekly_commits"] = plot(fig, include_plotlyjs=False, output_type="div", auto_open=False, config=PLOT_CONFIG)
    else:
        charts["weekly_commits"] = "<p>No weekly commit data.</p>"

    return charts


def render_table(df: pd.DataFrame, classes: str = "table") -> str:
    if df.empty:
        return "<p>No data.</p>"
    return df.to_html(index=False, classes=classes, border=0, justify="left")


def build_portal_html(
    output_path: Path,
    start_label: str,
    end_label: str,
    activity: Dict[str, Any],
    activity_charts: Dict[str, str],
    atuin_summary: Dict[str, Any],
    atuin_charts: Dict[str, str],
    git_summary: Dict[str, Any],
    git_charts: Dict[str, str],
) -> None:
    segments = activity["segments"]
    total_hours = float(segments["duration_h"].sum()) if not segments.empty else 0.0
    dev_hours = float(segments.loc[segments["category"].isin(DEV_CATEGORY_NAMES), "duration_h"].sum()) if not segments.empty else 0.0
    dev_percent = (dev_hours / total_hours * 100.0) if total_hours else 0.0
    llm_hours = float(segments.loc[segments["category"] == "LLM", "duration_h"].sum()) if not segments.empty else 0.0
    suspicious_hours = float(segments.loc[segments["suspicious"], "duration_h"].sum()) if not segments.empty else 0.0
    suspicious_sessions = int(activity["suspicious"].shape[0]) if isinstance(activity["suspicious"], pd.DataFrame) else 0

    atuin_total = int(atuin_summary["total"])
    git_commits_total = int(git_summary["repo_totals"]["commits"].sum()) if isinstance(git_summary["repo_totals"], pd.DataFrame) and not git_summary["repo_totals"].empty else 0

    top_category = (
        activity["category_totals"].iloc[0]["category"]
        if not activity["category_totals"].empty
        else "n/a"
    )

    timeline_html = activity_charts.get("timeline", "")
    weekly_all_html = activity_charts.get("weekly_all", "")
    weekly_filtered_html = activity_charts.get("weekly_filtered", "")
    dev_curve_html = activity_charts.get("dev_curve", "")

    llm_df = activity["llm_sessions"][
        ["start_local", "end_local", "duration_h", "label", "suspicious"]
    ] if isinstance(activity["llm_sessions"], pd.DataFrame) and not activity["llm_sessions"].empty else pd.DataFrame(columns=["start_local", "end_local", "duration_h", "label", "suspicious"])
    if not llm_df.empty:
        llm_df = llm_df.assign(
            start_local=llm_df["start_local"].dt.strftime("%Y-%m-%d %H:%M"),
            end_local=llm_df["end_local"].dt.strftime("%Y-%m-%d %H:%M"),
            duration_h=llm_df["duration_h"].map(_format_hours),
        )

    suspicious_df = activity["suspicious"].copy() if isinstance(activity["suspicious"], pd.DataFrame) and not activity["suspicious"].empty else pd.DataFrame(columns=["start_local", "end_local", "duration_h", "category", "label"])
    if not suspicious_df.empty:
        if "start_local" not in suspicious_df:
            suspicious_df["start_local"] = suspicious_df["start"].dt.tz_convert(LOCAL_TZ)
            suspicious_df["end_local"] = suspicious_df["end"].dt.tz_convert(LOCAL_TZ)
        suspicious_df = suspicious_df.assign(
            start_local=suspicious_df["start_local"].dt.strftime("%Y-%m-%d %H:%M"),
            end_local=suspicious_df["end_local"].dt.strftime("%Y-%m-%d %H:%M"),
            duration_h=suspicious_df["duration_h"].map(_format_hours),
        )

    llm_html = render_table(llm_df, classes="table table-tight") if not llm_df.empty else "<p>No browser LLM sessions recorded.</p>"
    suspicious_html = render_table(
        suspicious_df[["start_local", "end_local", "duration_h", "category", "label"]],
        classes="table table-tight",
    ) if not suspicious_df.empty else "<p>No suspicious sessions detected.</p>"

    category_totals_html = render_table(
        activity["category_totals"].assign(duration_h=activity["category_totals"]["duration_h"].map(_format_hours)),
        classes="table table-tight table-striped",
    ) if not activity["category_totals"].empty else "<p>No activity recorded.</p>"

    atuin_top_commands_html = render_table(
        atuin_summary["top_commands"].head(20), classes="table table-tight table-striped"
    ) if isinstance(atuin_summary["top_commands"], pd.DataFrame) and not atuin_summary["top_commands"].empty else "<p>No command statistics.</p>"

    atuin_top_projects_html = render_table(
        atuin_summary["top_projects"], classes="table table-tight table-striped"
    ) if isinstance(atuin_summary["top_projects"], pd.DataFrame) and not atuin_summary["top_projects"].empty else "<p>No project breakdown.</p>"

    git_repo_totals_html = render_table(
        git_summary["repo_totals"], classes="table table-tight table-striped"
    ) if isinstance(git_summary["repo_totals"], pd.DataFrame) and not git_summary["repo_totals"].empty else "<p>No commit totals.</p>"

    git_weekly_html = render_table(
        git_summary["weekly"], classes="table table-tight table-striped"
    ) if isinstance(git_summary["weekly"], pd.DataFrame) and not git_summary["weekly"].empty else "<p>No weekly breakdown.</p>"

    git_highlights_list = "".join(
        f"<li><span class='timestamp'>{item['date'].strftime('%Y-%m-%d %H:%M')}</span> "
        f"<code>{item['repo']}</code> <span class='commit-hash'>{item['commit'][:10]}</span> — "
        f"{item['subject']} <em>(+{item['additions']} / -{item['deletions']})</em></li>"
        for item in git_summary["highlights"][:40]
    ) if git_summary["highlights"] else ""

    git_highlights_html = (
        f"<ol class='highlight-list'>{git_highlights_list}</ol>"
        if git_highlights_list
        else "<p>No commit highlights in this interval.</p>"
    )

    html = f"""<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <title>REALM Focus Portal ({start_label} → {end_label})</title>
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <link rel=\"preconnect\" href=\"https://fonts.googleapis.com\" />
  <link rel=\"preconnect\" href=\"https://fonts.gstatic.com\" crossorigin />
  <link href=\"https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap\" rel=\"stylesheet\" />
  <script src=\"https://cdn.plot.ly/plotly-2.32.0.min.js\"></script>
  <style>
    :root {{
      --bg: #030712;
      --panel: #0b1324;
      --panel-strong: #131f33;
      --text: #f5f8ff;
      --muted: #9aa6c8;
      --accent: #8fd3ff;
      --accent-2: #f7a6ff;
      --danger: #ff8a9a;
    }}

    body {{
      margin: 0;
      font-family: 'Inter', sans-serif;
      background: linear-gradient(180deg, #020614 0%, #050b1e 100%);
      color: var(--text);
      padding: 1.5rem 1.25rem 4rem;
    }}

    h1, h2, h3 {{
      font-weight: 600;
      margin-bottom: 0.5rem;
    }}

    h1 {{
      font-size: 2rem;
      margin-bottom: 1.5rem;
    }}

    h2 {{
      font-size: 1.5rem;
      margin-top: 2rem;
    }}

    p {{
      color: var(--muted);
      line-height: 1.6;
    }}

    a {{
      color: var(--accent);
    }}

    .grid {{
      display: grid;
      gap: 1.25rem;
    }}

    .grid.kpi {{
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      margin-bottom: 1.75rem;
    }}

    .kpi-card {{
      background: var(--panel);
      border-radius: 14px;
      padding: 1.1rem 1.25rem;
      box-shadow: 0 12px 24px rgba(4, 12, 32, 0.45);
      border: 1px solid rgba(143, 211, 255, 0.12);
    }}

    .kpi-title {{
      font-size: 0.85rem;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 0.4rem;
    }}

    .kpi-value {{
      font-size: 1.75rem;
      font-weight: 600;
    }}

    .kpi-sub {{
      font-size: 0.85rem;
      color: var(--muted);
      margin-top: 0.2rem;
    }}

    .panel {{
      background: linear-gradient(180deg, rgba(19, 31, 51, 0.9), rgba(9, 16, 28, 0.95));
      border-radius: 16px;
      padding: 1.4rem 1.6rem;
      border: 1px solid rgba(143, 211, 255, 0.1);
      box-shadow: 0 32px 60px rgba(2, 8, 22, 0.45);
    }}

    .panel h3 {{
      margin-top: 0;
    }}

    .chart-container {{
      position: relative;
    }}

    .table {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 0.75rem;
      color: var(--text);
      font-size: 0.95rem;
    }}

    .table thead th {{
      text-align: left;
      padding: 0.5rem 0.6rem;
      font-weight: 600;
      background: rgba(255, 255, 255, 0.04);
    }}

    .table tbody td {{
      padding: 0.45rem 0.6rem;
      border-bottom: 1px solid rgba(255, 255, 255, 0.05);
      color: var(--muted);
    }}

    .table-striped tbody tr:nth-child(odd) td {{
      background: rgba(255, 255, 255, 0.02);
    }}

    .note-card {{
      border-left: 3px solid var(--accent);
      padding: 0.75rem 1rem;
      background: rgba(143, 211, 255, 0.08);
      border-radius: 10px;
      margin-top: 1rem;
      color: var(--accent);
    }}

    .highlight-list {{
      margin: 0.5rem 0 0 1.1rem;
      padding: 0;
      display: grid;
      gap: 0.6rem;
    }}

    .highlight-list li {{
      line-height: 1.4;
      color: var(--muted);
    }}

    .timestamp {{
      font-size: 0.8rem;
      color: var(--accent);
      margin-right: 0.5rem;
    }}

    .commit-hash {{
      color: var(--accent-2);
      margin-left: 0.4rem;
      font-family: 'JetBrains Mono', 'Fira Code', monospace;
    }}

    details summary {{
      cursor: pointer;
      font-weight: 500;
    }}

    @media (min-width: 900px) {{
      .grid.two-col {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}

      .grid.three-col {{
        grid-template-columns: repeat(3, minmax(0, 1fr));
      }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>REALM Focus Portal · {start_label} → {end_label}</h1>
    <p>This portal fuses ActivityWatch, Atuin shell telemetry, and git history to surface sustained focus patterns, outliers, and development throughput. Use the panels below to explore the cadence of work, LLM engagements, shell activity density, and repository velocity.</p>
  </header>

  <section class=\"grid kpi\">
    <div class=\"kpi-card\">
      <div class=\"kpi-title\">Active focus time</div>
      <div class=\"kpi-value\">{_format_hours(total_hours)} h</div>
      <div class=\"kpi-sub\">Top channel: {top_category}</div>
    </div>
    <div class=\"kpi-card\">
      <div class=\"kpi-title\">Developer focus</div>
      <div class=\"kpi-value\">{_format_hours(dev_hours)} h</div>
      <div class=\"kpi-sub\">{dev_percent:.1f}% of active</div>
    </div>
    <div class=\"kpi-card\">
      <div class=\"kpi-title\">LLM browser sessions</div>
      <div class=\"kpi-value\">{_format_hours(llm_hours)} h</div>
      <div class=\"kpi-sub\">Excludes terminal coding agents</div>
    </div>
    <div class=\"kpi-card\">
      <div class=\"kpi-title\">Shell commands</div>
      <div class=\"kpi-value\">{atuin_total:,}</div>
      <div class=\"kpi-sub\">Across Atuin sessions in scope</div>
    </div>
    <div class=\"kpi-card\">
      <div class=\"kpi-title\">Git commits</div>
      <div class=\"kpi-value\">{git_commits_total}</div>
      <div class=\"kpi-sub\">Across curated REALM repositories</div>
    </div>
    <div class=\"kpi-card\">
      <div class=\"kpi-title\">Suspicious spans</div>
      <div class=\"kpi-value\">{suspicious_sessions}</div>
      <div class=\"kpi-sub\">{_format_hours(suspicious_hours)} h flagged for review</div>
    </div>
  </section>

  <section class=\"grid two-col\">
    <div class=\"panel\">
      <h2>Timeline explorer</h2>
      <p>Each bar represents a merged ActivityWatch block (gap ≤ 10 minutes). Use the legend to isolate categories and inspect suspicious spans flagged for review.</p>
      <div class=\"chart-container\">{timeline_html}</div>
      <div class=\"note-card\">
        Toggle suspicious outliers using the category legend. The <strong>{SUSPICIOUS_SESSION_HOURS}h</strong> heuristic still treats long-running media tabs as suspicious until enriched metadata is available.</div>
    </div>

    <div class=\"panel\">
      <h2>Weekly category load</h2>
      <div class=\"chart-container\">{weekly_all_html}</div>
      <details>
        <summary>View with suspicious sessions removed</summary>
        <div class=\"chart-container\">{weekly_filtered_html}</div>
      </details>
      <h3>Development vs total</h3>
      <div class=\"chart-container\">{dev_curve_html}</div>
    </div>
  </section>

  <section class=\"panel\">
    <h2>Category totals</h2>
    {category_totals_html}
  </section>

  <section class=\"grid two-col\">
    <div class=\"panel\">
      <h2>Browser LLM engagements</h2>
      <p>Non-agent interactions grouped by contiguous focus windows.</p>
      {llm_html}
    </div>
    <div class=\"panel\">
      <h2>Suspicious sessions</h2>
      <p>Windows exceeding {SUSPICIOUS_SESSION_HOURS} hours of continuous focus.</p>
      {suspicious_html}
    </div>
  </section>

  <section class=\"grid two-col\">
    <div class=\"panel\">
      <h2>Atuin command cadence</h2>
      {atuin_charts.get('daily', '')}
      <h3>Hourly density</h3>
      {atuin_charts.get('hourly', '')}
    </div>
    <div class=\"panel\">
      <h2>Shell focus breakdown</h2>
      <h3>Top commands</h3>
      {atuin_top_commands_html}
      <h3>Projects by command volume</h3>
      {atuin_top_projects_html}
    </div>
  </section>

  <section class=\"grid two-col\">
    <div class=\"panel\">
      <h2>Repository throughput</h2>
      {git_charts.get('repo_commits', '')}
      <h3>Weekly cadence</h3>
      {git_charts.get('weekly_commits', '')}
    </div>
    <div class=\"panel\">
      <h2>Commit tables</h2>
      <h3>Repository totals</h3>
      {git_repo_totals_html}
      <h3>Weekly matrix</h3>
      {git_weekly_html}
    </div>
  </section>

  <section class=\"panel\">
    <h2>Commit highlights</h2>
    {git_highlights_html}
  </section>

  <footer style=\"margin-top:3rem;color:var(--muted);font-size:0.85rem;\">
    <p>Generated on {datetime.now().astimezone(LOCAL_TZ).strftime('%Y-%m-%d %H:%M %Z')}. Source inputs: ActivityWatch window+AFK streams, Atuin shell history, git repositories {', '.join(REPO_PATHS.keys())}. Extend this portal via <code>scripts/build_focus_portal.py</code>.</p>
  </footer>
</body>
</html>
"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the enriched REALM focus portal.")
    parser.add_argument(
        "--activitywatch-db",
        type=Path,
        default=DEFAULT_ACTIVITYWATCH_DB,
        help="Path to ActivityWatch SQLite database",
    )
    parser.add_argument(
        "--atuin-db",
        type=Path,
        default=DEFAULT_ATUIN_DB,
        help="Path to Atuin history.db",
    )
    parser.add_argument(
        "--start",
        type=str,
        default="2025-09-01",
        help="Start date (local timezone, inclusive)",
    )
    parser.add_argument(
        "--end",
        type=str,
        default=datetime.now(tz=LOCAL_TZ).strftime("%Y-%m-%d"),
        help="End date (local timezone, inclusive)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/focus_portal/index.html"),
        help="Output HTML path",
    )
    args = parser.parse_args()

    start_local, _ = _parse_local_datetime(args.start)
    end_local_raw, end_date_only = _parse_local_datetime(args.end)
    end_local = end_local_raw + timedelta(days=1) if end_date_only else end_local_raw

    start_label = start_local.strftime("%Y-%m-%d")
    end_label = end_local_raw.strftime("%Y-%m-%d")

    start_dt = start_local.astimezone(UTC)
    end_dt = end_local.astimezone(UTC)

    activity = load_activitywatch_context(args.activitywatch_db, start_dt, end_dt)
    activity_charts = build_activity_charts(activity)

    atuin_df = load_atuin_history(args.atuin_db, start_dt, end_dt)
    atuin_summary = build_atuin_summary(atuin_df)
    atuin_charts = build_atuin_charts(atuin_summary)

    git_summary = build_git_summary(start_dt, end_dt)
    git_charts = build_git_charts(git_summary)

    build_portal_html(
        output_path=args.output,
        start_label=start_label,
        end_label=end_label,
        activity=activity,
        activity_charts=activity_charts,
        atuin_summary=atuin_summary,
        atuin_charts=atuin_charts,
        git_summary=git_summary,
        git_charts=git_charts,
    )
    print(f"Portal written to {args.output}")


if __name__ == "__main__":
    main()
