#!/usr/bin/env python3
"""Generate day-level focus narratives from the baseline timeline.

The script reads `activity_timeline.json` (plus optional supporting JSON) and
produces a Markdown report describing each day's activity over a selected date
range. It is meant to turn the quantitative baseline into human-readable
context so future passes can quickly review prior weeks.

Example:
    python scripts/generate_daily_focus.py \
        --timeline results/2025-10-23-baseline/activity_timeline.json \
        --start 2025-09-24 --end 2025-10-23 \
        --output docs/analysis/daily-focus-2025-09-24_to_2025-10-23.md
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd
import typer

app = typer.Typer(pretty_exceptions_show_locals=False)


@dataclass
class DayNarrative:
    day: date
    intensity: str
    summary: str


def load_timeline(path: Path) -> pd.DataFrame:
    data = json.loads(path.read_text(encoding="utf-8"))
    df = pd.DataFrame(data)
    if df.empty:
        raise RuntimeError(f"Timeline {path} is empty")
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def select_days(df: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    mask = (df["date"] >= start) & (df["date"] <= end)
    subset = df.loc[mask].copy()
    return subset.sort_values("date")


def classify_intensity(row: pd.Series) -> str:
    focus_ratio = safe_div(row.get("active_hours"), row.get("window_hours"))
    cmds = row.get("command_total", 0) or 0
    codex = row.get("codex_sessions", 0) or 0

    if row.get("active_hours", 0) < 3 and cmds < 50 and codex == 0:
        return "low-activity / rest"
    if focus_ratio >= 0.75 and (cmds >= 400 or codex >= 6):
        return "high-intensity build" if codex > 0 else "deep solo build"
    if focus_ratio < 0.45 and row.get("active_hours", 0) > 6:
        return "fragmented focus"
    if codex >= 5:
        return "agent-heavy iterations"
    if cmds >= 250:
        return "hands-on implementation"
    return "steady maintenance"


def safe_div(num: Optional[float], denom: Optional[float]) -> float:
    if not num or not denom:
        return 0.0
    try:
        return float(num) / float(denom)
    except ZeroDivisionError:
        return 0.0


def dominant_categories(categories: Dict[str, int], limit: int = 3) -> str:
    if not categories:
        return ""
    total = sum(categories.values()) or 1
    items = sorted(categories.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    parts = [
        f"{label} {round((count / total) * 100)}%"
        for label, count in items
        if count > 0
    ]
    return ", ".join(parts)


def describe_day(row: pd.Series) -> DayNarrative:
    focus_ratio = safe_div(row.get("active_hours"), row.get("window_hours"))
    focus_pct_raw = focus_ratio * 100 if focus_ratio else 0
    focus_pct = round(min(focus_pct_raw, 100))
    active = row.get("active_hours", 0.0) or 0.0
    codex = int(row.get("codex_sessions", 0) or 0)
    cmds = int(row.get("command_total", 0) or 0)
    categories = row.get("command_categories") or {}
    cat_summary = dominant_categories(categories)

    highlights: List[str] = []
    highlight = f"active {active:.1f}h ({focus_pct}% focus)"
    if focus_pct_raw > 110:
        highlight += "*"
    highlights.append(highlight)
    if codex:
        highlights.append(f"{codex} Codex sessions")
    if cmds:
        highlights.append(f"{cmds} shell commands")
    if cat_summary:
        highlights.append(f"command mix: {cat_summary}")

    summary = "; ".join(highlights)
    intensity = classify_intensity(row)
    if focus_pct_raw > 110:
        summary += "; note: AW window under-counted (focus > 100%)"
    return DayNarrative(day=row["date"], intensity=intensity, summary=summary)


def to_markdown(narratives: Iterable[DayNarrative]) -> str:
    lines = ["# Daily Focus Narrative\n"]
    for item in narratives:
        day_str = item.day.isoformat()
        lines.append(f"## {day_str} — {item.intensity}")
        lines.append(item.summary)
        lines.append("")
    return "\n".join(lines).strip() + "\n"


@app.command()
def generate(
    timeline: Path = typer.Option(
        Path("results/2025-10-23-baseline/activity_timeline.json"),
        help="Path to activity timeline JSON",
    ),
    start: datetime = typer.Option(
        None,
        formats=["%Y-%m-%d"],
        help="Start date (YYYY-MM-DD). Defaults to max(date)-30",
    ),
    end: datetime = typer.Option(
        None,
        formats=["%Y-%m-%d"],
        help="End date (YYYY-MM-DD). Defaults to latest date in timeline",
    ),
    output: Optional[Path] = typer.Option(
        None,
        help="Markdown output path. Defaults to stdout",
    ),
) -> None:
    df = load_timeline(timeline)
    latest_day = df["date"].max()
    default_start = latest_day - pd.Timedelta(days=29)
    start_date = (start.date() if start else default_start)
    end_date = (end.date() if end else latest_day)

    typer.echo(f"Summarising {start_date} → {end_date}")
    subset = select_days(df, start_date, end_date)
    if subset.empty:
        raise RuntimeError("No timeline entries in the requested range")

    narratives = [describe_day(row) for _, row in subset.iterrows()]
    markdown = to_markdown(narratives)

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(markdown, encoding="utf-8")
        typer.echo(f"Report written to {output}")
    else:
        typer.echo(markdown)


if __name__ == "__main__":
    app()
