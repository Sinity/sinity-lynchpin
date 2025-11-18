#!/usr/bin/env python3
"""
Generate a multi-week focus and activity report by combining ActivityWatch usage
data with git history across core REALM repositories.

Outputs a self-contained HTML dashboard highlighting per-week breakdowns,
category trends, LLM sessions, and repository commit statistics.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from textwrap import shorten
from typing import Dict, List, Optional

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
    build_weekly_summary,
    append_total_row,
    classify_segment,
    load_activitywatch_segments,
    mark_suspicious_segments,
    merge_adjacent_blocks,
    parse_git_log,
    summarise_top_sessions,
)


# --------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------

WEEK_START = datetime(2025, 9, 1, tzinfo=UTC)
WEEK_END = datetime(2025, 11, 4, tzinfo=UTC)


# --------------------------------------------------------------------------------------
# HTML helpers
# --------------------------------------------------------------------------------------

def format_hours(value: float) -> str:
    return f"{value:.2f}"


def build_plotly_category_chart(
    weekly_summary: pd.DataFrame, categories: List[str], title: str
) -> str:
    """Create a stacked bar chart of category hours per week."""
    fig = go.Figure()
    for category in categories:
        if category not in weekly_summary.columns:
            continue
        fig.add_trace(
            go.Bar(
                name=category,
                x=weekly_summary["week"],
                y=weekly_summary[category],
                hovertemplate="%{x}<br>%{y:.2f} h<extra>" + category + "</extra>",
            )
        )
    fig.update_layout(
        barmode="stack",
        title=title,
        xaxis_title="Week",
        yaxis_title="Hours",
        legend_title="Category",
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    return plot(
        fig,
        include_plotlyjs=False,
        output_type="div",
        auto_open=False,
        config={"displayModeBar": True, "displaylogo": False},
    )


def build_plotly_dev_chart(weekly_dev: pd.DataFrame, title: str) -> str:
    """Create a line chart comparing dev vs total hours."""
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            name="Dev-focused hours",
            x=weekly_dev["week"],
            y=weekly_dev["dev_hours"],
            mode="lines+markers",
        )
    )
    fig.add_trace(
        go.Scatter(
            name="Total active hours",
            x=weekly_dev["week"],
            y=weekly_dev["total_hours"],
            mode="lines+markers",
        )
    )
    fig.update_layout(
        title=title,
        xaxis_title="Week",
        yaxis_title="Hours",
        legend_title="Metric",
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    return plot(
        fig,
        include_plotlyjs=False,
        output_type="div",
        auto_open=False,
        config={"displayModeBar": True, "displaylogo": False},
    )


def render_week_section(
    week: str,
    summary_table: pd.DataFrame,
    top_sessions: List[Dict[str, object]],
    llm_sessions: List[Dict[str, object]],
    metrics: Dict[str, float],
    suspicious_sessions: List[Dict[str, object]],
) -> str:
    """Render HTML for a single week block."""
    total_all = metrics.get("total_all", 0.0)
    total_filtered = metrics.get("total_filtered", 0.0)
    dev_all = metrics.get("dev_all", 0.0)
    dev_filtered = metrics.get("dev_filtered", 0.0)

    dev_pct_all = (dev_all / total_all * 100.0) if total_all > 0 else 0.0
    dev_pct_filtered = (dev_filtered / total_filtered * 100.0) if total_filtered > 0 else 0.0

    summary_display = summary_table.copy()
    numeric_cols = [col for col in summary_display.columns if col != "Category"]
    for col in numeric_cols:
        summary_display[col] = summary_display[col].map(lambda v: f"{v:.2f}")
    summary_html = summary_display.to_html(index=False, classes="table table-tight table-striped")

    session_items = []
    for sess in top_sessions:
        flag = " <span class='pill pill-alert'>suspicious</span>" if sess["suspicious"] else ""
        css_class = "session-item suspicious" if sess["suspicious"] else "session-item"
        item = (
            f"<li class='{css_class}'>"
            f"<span class='timestamp'>{sess['start']:%Y-%m-%d %H:%M} → {sess['end']:%H:%M}</span> "
            f"<span class='duration'>{sess['duration_h']:.2f} h</span> "
            f"<em>{escape_html(sess['category'])}</em>{flag}"
            f"<br><code>{escape_html(sess['sample'])}</code></li>"
        )
        session_items.append(item)

    if not session_items:
        session_items.append("<li class='session-item'>No sessions ≥10 minutes recorded.</li>")

    llm_items = []
    for sess in llm_sessions:
        flag = " <span class='pill pill-alert'>suspicious</span>" if sess["suspicious"] else ""
        llm_items.append(
            "<li>"
            f"<span class='timestamp'>{sess['start']:%Y-%m-%d %H:%M} → {sess['end']:%H:%M}</span> "
            f"<span class='duration'>{sess['duration_h']:.2f} h</span>{flag}"
            f"<br><code>{escape_html(sess['sample'])}</code></li>"
        )
    if not llm_items:
        llm_items.append("<li>No browser-based LLM sessions tracked.</li>")

    suspicious_details = ""
    if suspicious_sessions:
        entries = []
        for sess in suspicious_sessions:
            entries.append(
                "<li>"
                f"<span class='timestamp'>{sess['start']:%Y-%m-%d %H:%M} → {sess['end']:%H:%M}</span> "
                f"<span class='duration'>{sess['duration_h']:.2f} h</span> "
                f"<em>{escape_html(sess['category'])}</em><br>"
                f"<code>{escape_html(sess['sample'])}</code></li>"
            )
        suspicious_details = (
            "<details class='suspicious-blocks'><summary>Suspicious sessions in this week</summary>"
            "<ul>"
            + "".join(entries)
            + "</ul></details>"
        )

    return f"""
    <section class="week-section" id="week-{week}">
      <h2>Week {week} — {total_all:.2f} h active ({total_filtered:.2f} h excl. suspicious)</h2>
      <p class="week-meta">
        Dev focus: <strong>{dev_all:.2f} h</strong> ({dev_pct_all:.1f}%) &nbsp; | &nbsp;
        Excl. suspicious: <strong>{dev_filtered:.2f} h</strong> ({dev_pct_filtered:.1f}%)
      </p>
      <div class="week-grid">
        <div>
          <h3>Category Breakdown</h3>
          {summary_html}
        </div>
        <div>
          <h3>Top Sustained Sessions</h3>
          <label class="toggle-label">
            <input type="checkbox" class="toggle-week-suspicious" data-week="{week}" checked />
            Show suspicious sessions
          </label>
          <ol class="session-list" data-week="{week}">
            {' '.join(session_items)}
          </ol>
          <h3>LLM Conversations</h3>
          <ol>
            {' '.join(llm_items)}
          </ol>
          {suspicious_details}
        </div>
      </div>
    </section>
    """


def escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


# --------------------------------------------------------------------------------------
# Main execution
# --------------------------------------------------------------------------------------


def generate_report(output_path: Path, db_path: Path, start: datetime, end: datetime) -> None:
    segments = load_activitywatch_segments(db_path, start, end)
    if segments.empty:
        raise SystemExit("No ActivityWatch data available for the requested period.")

    classifications = [classify_segment(app, title) for app, title in zip(segments["app"], segments["title"])]
    segments["super_category"] = [c[0] for c in classifications]
    segments["category"] = [c[1] for c in classifications]
    segments = add_time_columns(segments)

    merged_segments = merge_adjacent_blocks(
        segments[["start", "end", "duration_h", "category", "super_category", "title"]]
    )
    merged_segments = add_time_columns(merged_segments)
    merged_segments["suspicious"] = merged_segments["duration_h"] >= SUSPICIOUS_SESSION_HOURS

    segments = mark_suspicious_segments(segments, merged_segments[merged_segments["suspicious"]])
    segments_filtered = segments[~segments["suspicious"]].copy()

    category_names = sorted(segments["category"].unique())

    weekly_summary_all = build_weekly_summary(segments, category_names, DEV_CATEGORY_NAMES)
    weekly_summary_filtered = build_weekly_summary(segments_filtered, category_names, DEV_CATEGORY_NAMES)
    weekly_summary_all = append_total_row(weekly_summary_all, category_names)
    weekly_summary_filtered = append_total_row(weekly_summary_filtered, category_names)

    weekly_summary_all_no_total = weekly_summary_all[weekly_summary_all["week"] != "TOTAL"].reset_index(drop=True)
    weekly_summary_filtered_no_total = weekly_summary_filtered[weekly_summary_filtered["week"] != "TOTAL"].reset_index(drop=True)

    weekly_dev_all = weekly_summary_all_no_total[["week"]].copy()
    weekly_dev_all["dev_hours"] = weekly_summary_all_no_total["DevHours"]
    weekly_dev_all["total_hours"] = weekly_summary_all_no_total["Total"]

    weekly_dev_filtered = weekly_summary_filtered_no_total[["week"]].copy()
    weekly_dev_filtered["dev_hours"] = weekly_summary_filtered_no_total["DevHours"]
    weekly_dev_filtered["total_hours"] = weekly_summary_filtered_no_total["Total"]

    category_chart_all = build_plotly_category_chart(
        weekly_summary_all_no_total[["week", *category_names]],
        category_names,
        "Weekly Active Hours (incl. suspicious sessions)",
    )
    category_chart_filtered = build_plotly_category_chart(
        weekly_summary_filtered_no_total[["week", *category_names]],
        category_names,
        "Weekly Active Hours (excl. suspicious sessions)",
    )
    dev_chart_all = build_plotly_dev_chart(weekly_dev_all, "Development vs Total Hours (incl. suspicious)")
    dev_chart_filtered = build_plotly_dev_chart(weekly_dev_filtered, "Development vs Total Hours (excl. suspicious)")

    suspicious_rows = merged_segments[merged_segments["suspicious"]]
    suspicious_html = "<p>No suspiciously long sessions detected.</p>"
    if not suspicious_rows.empty:
        details: list[str] = []
        for week_key, group in suspicious_rows.groupby("iso_week_str"):
            group = group.sort_values("start")
            total_hours_week = group["duration_h"].sum()
            entries = []
            for _, row in group.iterrows():
                sample = next((t for t in row.get("details", []) if t), row["title"])
                entries.append(
                    "<li>"
                    f"<span class='timestamp'>{row['start_local']:%Y-%m-%d %H:%M} → {row['end_local']:%H:%M}</span> "
                    f"<span class='duration'>{row['duration_h']:.2f} h</span> "
                    f"<em>{escape_html(row['category'])}</em><br>"
                    f"<code>{escape_html(shorten(sample, width=160))}</code>"
                    "</li>"
                )
            details.append(
                f"<details><summary>Week {week_key} · {len(group)} sessions · {total_hours_week:.2f} h</summary>"
                f"<ul>{''.join(entries)}</ul></details>"
            )
        suspicious_html = "".join(details)

    overall_total_all = segments["duration_h"].sum()
    overall_total_filtered = segments_filtered["duration_h"].sum()
    overall_dev_all = segments.loc[segments["category"].isin(DEV_CATEGORY_NAMES), "duration_h"].sum()
    overall_dev_filtered = segments_filtered.loc[
        segments_filtered["category"].isin(DEV_CATEGORY_NAMES), "duration_h"
    ].sum()
    overall_dev_pct_all = (overall_dev_all / overall_total_all * 100.0) if overall_total_all > 0 else 0.0
    overall_dev_pct_filtered = (
        overall_dev_filtered / overall_total_filtered * 100.0 if overall_total_filtered > 0 else 0.0
    )
    overall_llm_all = segments.loc[segments["super_category"] == "LLM", "duration_h"].sum()
    overall_llm_filtered = segments_filtered.loc[segments_filtered["super_category"] == "LLM", "duration_h"].sum()
    overall_adult_all = segments.loc[segments["super_category"] == "Adult", "duration_h"].sum()
    overall_adult_filtered = segments_filtered.loc[segments_filtered["super_category"] == "Adult", "duration_h"].sum()
    suspicious_hours_total = suspicious_rows["duration_h"].sum()
    suspicious_session_count = len(suspicious_rows)
    suspicious_week_count = suspicious_rows["iso_week_str"].nunique()

    weekly_sections: list[str] = []
    for week in weekly_summary_all_no_total["week"]:
        week_df_all = segments[segments["iso_week_str"] == week]
        week_df_filtered = segments_filtered[segments_filtered["iso_week_str"] == week]

        cat_all = (
            week_df_all.groupby("category")["duration_h"].sum().reset_index().rename(columns={"duration_h": "Hours (All)"})
        )
        cat_filtered = (
            week_df_filtered.groupby("category")["duration_h"].sum().reset_index().rename(columns={"duration_h": "Hours (No Suspicious)"})
        )
        cat_summary = pd.merge(cat_all, cat_filtered, on="category", how="outer").fillna(0.0)
        if cat_summary.empty:
            cat_summary = pd.DataFrame(
                [{"Category": "(none)", "Hours (All)": 0.0, "Hours (No Suspicious)": 0.0}]
            )
        else:
            cat_summary.rename(columns={"category": "Category"}, inplace=True)
            if "Hours (All)" not in cat_summary.columns:
                cat_summary["Hours (All)"] = 0.0
            if "Hours (No Suspicious)" not in cat_summary.columns:
                cat_summary["Hours (No Suspicious)"] = 0.0
            cat_summary = cat_summary[["Category", "Hours (All)", "Hours (No Suspicious)"]]
            cat_summary = cat_summary.sort_values("Hours (All)", ascending=False).reset_index(drop=True)

        merged_week = merged_segments[merged_segments["iso_week_str"] == week]
        top_sessions = summarise_top_sessions(merged_week)
        llm_week = merged_week[merged_week["category"].str.contains("ChatGPT|Claude", regex=True)]
        llm_sessions = summarise_top_sessions(llm_week, top_n=10)

        suspicious_week = merged_week[merged_week["suspicious"]]
        week_suspicious_sessions: List[Dict[str, object]] = []
        for _, row in suspicious_week.sort_values("start").iterrows():
            sample = next((t for t in row.get("details", []) if t), row["title"])
            week_suspicious_sessions.append(
                {
                    "start": row["start_local"],
                    "end": row["end_local"],
                    "duration_h": row["duration_h"],
                    "category": row["category"],
                    "sample": sample,
                }
            )

        metrics = {
            "total_all": week_df_all["duration_h"].sum(),
            "total_filtered": week_df_filtered["duration_h"].sum(),
            "dev_all": week_df_all.loc[week_df_all["category"].isin(DEV_CATEGORY_NAMES), "duration_h"].sum(),
            "dev_filtered": week_df_filtered.loc[
                week_df_filtered["category"].isin(DEV_CATEGORY_NAMES), "duration_h"
            ].sum(),
        }

        weekly_sections.append(
            render_week_section(
                week,
                cat_summary,
                top_sessions,
                llm_sessions,
                metrics,
                week_suspicious_sessions,
            )
        )

    weekly_sections_html = "".join(weekly_sections)

    git_since = datetime(2025, 9, 1, tzinfo=UTC)
    git_until = datetime(2025, 11, 4, tzinfo=UTC)
    git_weekly_rows: List[Dict[str, object]] = []
    git_highlights = defaultdict(list)
    repo_metrics: Dict[str, Dict[str, int]] = {}

    for repo_name, repo_path in REPO_PATHS.items():
        if not repo_path.exists():
            continue
        commits = parse_git_log(repo_path, git_since, git_until)
        if not commits:
            continue

        total_commits = len(commits)
        additions_total = sum(c.additions for c in commits)
        deletions_total = sum(c.deletions for c in commits)
        repo_metrics[repo_name] = {
            "commits": total_commits,
            "additions": additions_total,
            "deletions": deletions_total,
        }

        for commit in commits:
            iso = commit.date.astimezone(LOCAL_TZ).isocalendar()
            week_key = f"{iso.year}-W{iso.week:02d}"
            git_weekly_rows.append(
                {
                    "week": week_key,
                    "repo": repo_name,
                    "commits": 1,
                    "additions": commit.additions,
                    "deletions": commit.deletions,
                }
            )

            top_files = sorted(commit.files, key=lambda item: item[1] + item[2], reverse=True)[:3]
            git_highlights[week_key].append(
                {
                    "repo": repo_name,
                    "commit": commit.commit,
                    "date": commit.date.astimezone(LOCAL_TZ),
                    "subject": commit.subject,
                    "additions": commit.additions,
                    "deletions": commit.deletions,
                    "top_files": top_files,
                }
            )

    git_weekly_table_html = "<p>No git activity detected.</p>"
    if git_weekly_rows:
        git_weekly_df = (
            pd.DataFrame(git_weekly_rows)
            .groupby(["week", "repo"], as_index=False)
            .sum(numeric_only=True)
            .sort_values(["week", "repo"])
        )
        git_weekly_table_html = git_weekly_df.to_html(index=False, classes="table table-tight table-striped")

    repo_totals_html = "<p>No git activity detected.</p>"
    if repo_metrics:
        repo_totals_df = pd.DataFrame(
            [
                {
                    "Repository": name,
                    "Commits": stats["commits"],
                    "Additions": stats["additions"],
                    "Deletions": stats["deletions"],
                }
                for name, stats in sorted(repo_metrics.items())
            ]
        )
        repo_totals_html = repo_totals_df.to_html(index=False, classes="table table-tight table-striped")

    git_highlights_html = ""
    if git_highlights:
        for week_key in sorted(git_highlights.keys()):
            rows = git_highlights[week_key]
            items = []
            for entry in rows:
                file_list = ", ".join(
                    f"{path} (+{add}/-{delete})" for path, add, delete in entry["top_files"]
                )
                items.append(
                    "<li>"
                    f"<span class='timestamp'>{entry['date']:%Y-%m-%d %H:%M}</span> "
                    f"<code>{entry['repo']}</code>"
                    f" &nbsp;<span class='commit-hash'>{entry['commit'][:10]}</span><br>"
                    f"{escape_html(entry['subject'])}<br>"
                    f"Δ +{entry['additions']} / -{entry['deletions']} &nbsp;"
                    f"<em>{escape_html(file_list)}</em>"
                    "</li>"
                )
            git_highlights_html += (
                f"<details><summary>Week {week_key} · {len(rows)} commits</summary><ul>{''.join(items)}</ul></details>"
            )
    else:
        git_highlights_html = "<p>No notable commits.</p>"

    html_output = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Focus & Repository Report (2025-09-01 → 2025-11-03)</title>
  <style>
    :root {{
      --bg: #050b18;
      --panel: #10192b;
      --panel-strong: #15243a;
      --text: #e8f0ff;
      --muted: #9aa8c3;
      --accent: #8fd3ff;
      --accent-soft: #c6e7ff;
      --pill-alert: #ff8a8a;
    }}
    body {{
      background: linear-gradient(180deg, rgba(5,11,24,1) 0%, rgba(12,20,38,1) 100%);
      color: var(--text);
      margin: 0 auto;
      padding: 2.5rem 2rem 3rem;
      max-width: 1280px;
      font-family: 'Inter', 'Segoe UI', sans-serif;
      line-height: 1.6;
    }}
    h1, h2, h3 {{ font-weight: 600; }}
    a {{ color: var(--accent); }}
    .stat-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 1rem; margin-bottom: 1.5rem; }}
    .stat-card {{ background: var(--panel); border: 1px solid rgba(255,255,255,0.05); border-radius: 14px; padding: 1rem 1.2rem; box-shadow: 0 12px 30px rgba(0,0,0,0.35); }}
    .stat-label {{ display: block; text-transform: uppercase; font-size: 0.75rem; letter-spacing: 0.12em; color: var(--muted); margin-bottom: 0.4rem; }}
    .stat-value {{ display: block; font-size: 1.9rem; font-weight: 700; }}
    .stat-sub {{ display: block; font-size: 0.85rem; color: var(--muted); margin-top: 0.4rem; }}
    .view-toggle {{ display: flex; gap: 1rem; align-items: center; margin: 1.2rem 0; flex-wrap: wrap; }}
    .view-toggle label {{ background: rgba(255,255,255,0.06); padding: 0.4rem 0.8rem; border-radius: 999px; cursor: pointer; font-size: 0.85rem; color: var(--muted); }}
    .view-toggle input {{ margin-right: 0.4rem; }}
    .chart-panel {{ background: var(--panel); border: 1px solid rgba(255,255,255,0.08); border-radius: 16px; padding: 1rem; margin-bottom: 1.6rem; box-shadow: 0 14px 32px rgba(0,0,0,0.4); }}
    .hidden {{ display: none !important; }}
    .table {{ width: 100%; border-collapse: collapse; margin-bottom: 1rem; background: var(--panel); }}
    .table-tight th, .table-tight td {{ padding: 0.45rem 0.6rem; }}
    .table th {{ text-align: left; font-weight: 600; background: rgba(255,255,255,0.04); }}
    .table td, .table th {{ border: 1px solid rgba(255,255,255,0.08); }}
    .table-striped tr:nth-child(even) {{ background: rgba(255,255,255,0.03); }}
    code {{ background: rgba(255,255,255,0.08); padding: 0.1rem 0.3rem; border-radius: 4px; font-family: 'JetBrains Mono','Fira Code',monospace; font-size: 0.85rem; }}
    .week-section {{ margin-bottom: 3rem; padding-bottom: 2rem; border-bottom: 1px solid rgba(255,255,255,0.06); }}
    .week-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 1.5rem; }}
    .week-meta {{ color: var(--muted); margin-top: -0.4rem; margin-bottom: 1rem; }}
    .session-list {{ margin: 0; padding-left: 1.1rem; }}
    .session-item {{ margin-bottom: 0.6rem; }}
    .session-item.hidden {{ display: none; }}
    .timestamp {{ font-weight: 600; margin-right: 0.6rem; }}
    .duration {{ font-weight: 600; color: var(--accent-soft); margin-right: 0.6rem; }}
    .toggle-label {{ display: flex; align-items: center; gap: 0.5rem; font-size: 0.9rem; margin-bottom: 0.6rem; color: var(--muted); }}
    .pill {{ display: inline-block; padding: 0.1rem 0.55rem; border-radius: 999px; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.08em; margin-left: 0.4rem; }}
    .pill-alert {{ background: rgba(255,140,140,0.25); color: #ff9c9c; border: 1px solid rgba(255,140,140,0.4); }}
    details {{ background: var(--panel); border: 1px solid rgba(255,255,255,0.08); border-radius: 12px; padding: 0.7rem 1rem; margin-bottom: 0.8rem; box-shadow: 0 6px 18px rgba(0,0,0,0.35); }}
    details > summary {{ cursor: pointer; font-weight: 600; }}
    .note-card {{ background: var(--panel-strong); border: 1px dashed rgba(255,255,255,0.2); border-radius: 12px; padding: 1rem 1.2rem; margin: 1rem 0; color: var(--accent-soft); }}
    .commit-hash {{ font-family: 'JetBrains Mono','Fira Code',monospace; color: var(--accent); }}
    @media (max-width: 760px) {{
      body {{ padding: 2.5rem 1.2rem 3rem; }}
      .stat-grid {{ grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); }}
      .week-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
  <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
</head>
<body>
  <h1>Focus & Repository Report</h1>
  <p>Observational window: <strong>2025-09-01</strong> → <strong>2025-11-03</strong>. ActivityWatch data is filtered to <code>sinnix-prime</code> with AFK spans removed. Suspiciously long windows remain visible but can be excluded from the visualisations below.</p>

  <section class="overview">
    <div class="stat-grid">
      <div class="stat-card">
        <span class="stat-label">Active hours</span>
        <span class="stat-value">{overall_total_all:.2f} h</span>
        <span class="stat-sub">Excl. suspicious: {overall_total_filtered:.2f} h</span>
      </div>
      <div class="stat-card">
        <span class="stat-label">Dev focus</span>
        <span class="stat-value">{overall_dev_all:.2f} h</span>
        <span class="stat-sub">{overall_dev_pct_all:.1f}% of active · excl. suspicious: {overall_dev_filtered:.2f} h / {overall_dev_pct_filtered:.1f}%</span>
      </div>
      <div class="stat-card">
        <span class="stat-label">Browser LLM interactions</span>
        <span class="stat-value">{overall_llm_all:.2f} h</span>
        <span class="stat-sub">Excl. suspicious: {overall_llm_filtered:.2f} h</span>
      </div>
      <div class="stat-card">
        <span class="stat-label">Adult browsing</span>
        <span class="stat-value">{overall_adult_all:.2f} h</span>
        <span class="stat-sub">Excl. suspicious: {overall_adult_filtered:.2f} h</span>
      </div>
      <div class="stat-card">
        <span class="stat-label">Suspicious windows</span>
        <span class="stat-value">{suspicious_session_count} sessions</span>
        <span class="stat-sub">{suspicious_hours_total:.2f} h across {suspicious_week_count} weeks</span>
      </div>
    </div>

    <div class="view-toggle">
      <label><input type="radio" name="suspicious-mode" value="all" checked /> Include suspicious sessions</label>
      <label><input type="radio" name="suspicious-mode" value="filtered" /> Exclude suspicious sessions</label>
    </div>

    <div class="chart-panel" data-view="all">
      <h3>Weekly Active Hours</h3>
      {category_chart_all}
      <h3>Development vs Total Hours</h3>
      {dev_chart_all}
    </div>
    <div class="chart-panel hidden" data-view="filtered">
      <h3>Weekly Active Hours (filtered)</h3>
      {category_chart_filtered}
      <h3>Development vs Total Hours (filtered)</h3>
      {dev_chart_filtered}
    </div>
  </section>

  <section>
    <h2>Suspicious Sessions (≥ {SUSPICIOUS_SESSION_HOURS} h)</h2>
    <p>These windows stayed in focus for unusually long spans. Use the toggle above to exclude them from the charts; they remain listed in the weekly tables for manual review.</p>
    {suspicious_html}
    <div class="note-card">
      <strong>Next refinement ideas:</strong>
      <ul>
        <li>Fill in <code>fetch_youtube_duration()</code> to obtain actual video lengths (YouTube API / local metadata) and adjust the suspicious heuristic.</li>
        <li>Serialise the per-session data to JSON so manual corrections can feed back into the renderer before the HTML layer is rebuilt.</li>
        <li>Extend <code>DEV_CATEGORY_NAMES</code> if additional editors or containerised terminals should count towards focus.</li>
      </ul>
    </div>
  </section>

  <section>
    <h2>Week-by-Week Breakdown</h2>
    {weekly_sections_html}
  </section>

  <section>
    <h2>Git Activity (2025-09-01 → 2025-11-03)</h2>
    <h3>Repository totals</h3>
    {repo_totals_html}
    <h3>Weekly commits</h3>
    {git_weekly_table_html}
    <h3>Highlights</h3>
    {git_highlights_html}
  </section>

  <script>
    document.addEventListener('DOMContentLoaded', () => {{
      const modeRadios = document.querySelectorAll('input[name="suspicious-mode"]');
      const panels = document.querySelectorAll('.chart-panel');
      const updatePanels = () => {{
        const value = document.querySelector('input[name="suspicious-mode"]:checked').value;
        panels.forEach(panel => panel.classList.toggle('hidden', panel.dataset.view !== value));
      }};
      modeRadios.forEach(radio => radio.addEventListener('change', updatePanels));
      updatePanels();

      document.querySelectorAll('.toggle-week-suspicious').forEach(checkbox => {{
        const week = checkbox.dataset.week;
        const list = document.querySelector(`.session-list[data-week="${{week}}"]`);
        const apply = () => {{
          if (!list) return;
          list.querySelectorAll('.suspicious').forEach(item => {{
            item.classList.toggle('hidden', !checkbox.checked);
          }});
        }};
        checkbox.addEventListener('change', apply);
        apply();
      }});
    }});
  </script>
</body>
</html>
"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_output, encoding="utf-8")
    print(f"Report written to {output_path}")

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate weekly focus report.")
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_ACTIVITYWATCH_DB,
        help="Path to ActivityWatch SQLite database (default: %(default)s)",
    )
    parser.add_argument(
        "--start",
        type=str,
        default=WEEK_START.strftime("%Y-%m-%d"),
        help="Start date (UTC, inclusive). Default: %(default)s",
    )
    parser.add_argument(
        "--end",
        type=str,
        default=(WEEK_END - pd.Timedelta(seconds=1)).strftime("%Y-%m-%d"),
        help="End date (UTC, exclusive upper bound). Default: %(default)s",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/focus_report_2025-09-01_to_2025-11-03.html"),
        help="Output HTML path",
    )
    args = parser.parse_args()

    start_dt = datetime.fromisoformat(args.start).replace(tzinfo=UTC)
    end_dt = datetime.fromisoformat(args.end).replace(tzinfo=UTC) + pd.Timedelta(days=1)

    generate_report(args.output, args.db, start_dt, end_dt)


if __name__ == "__main__":
    main()
