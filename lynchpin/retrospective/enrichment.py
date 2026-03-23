"""Data enrichment functions for building rich agent prompts.

This module provides reusable data formatting functions that extract and structure
information from ActivityWatch, shell history, git repositories, DuckDB warehouses,
and wearables. These functions serve both the automated narrative pipeline and
interactive sessions, enabling consistent data representation across agents.

Key functions:
- parse_date_range(): parse scale + key into start/end date
- format_activity_spans(): format ActivityWatch activity as timeline
- format_shell_commands(): format shell command history
- format_git_commits(): format git log with messages and stats
- format_git_oneline(): format git log oneline summary
- format_warehouse_context(): query DuckDB for structured data
- format_sleep_data(): format wearable sleep records
- format_metrics_summary(): compute metrics from lynchpin.metrics
- build_day_enrichment(): layer all sources into enriched prompt
"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .narrative import NarrativeKind

log = logging.getLogger(__name__)

NARRATIVE_DIR = Path("artefacts/retrospective/narratives")

GIT_REPOS = [
    "sinex", "sinnix", "polylogue", "sinity-lynchpin", "knowledgebase",
    "scribe-tap", "intercept-bounce", "knowledge-extract",
]


def parse_date_range(scale: NarrativeKind, key: str) -> tuple[date | None, date | None]:
    """Extract start/end dates from a scale + key.

    Examples:
        - NarrativeKind.day, "2026-03-15" → (2026-03-15, 2026-03-15)
        - NarrativeKind.week, "2026-W11" → (2026-03-09, 2026-03-15)
        - NarrativeKind.month, "2026-03" → (2026-03-01, 2026-03-31)
        - NarrativeKind.quarter, "2026-Q1" → (2026-01-01, 2026-03-31)
    """
    try:
        from .narrative import NarrativeKind

        if scale is NarrativeKind.day:
            d = date.fromisoformat(key)
            return d, d
        if scale is NarrativeKind.week:
            year, week_num = int(key[:4]), int(key.split("W")[1])
            return date.fromisocalendar(year, week_num, 1), date.fromisocalendar(year, week_num, 7)
        if scale is NarrativeKind.month:
            year, month = int(key[:4]), int(key[5:7])
            start = date(year, month, 1)
            end = date(year + (month // 12), (month % 12) + 1, 1) - timedelta(days=1)
            return start, end
        if scale is NarrativeKind.quarter:
            year, q = int(key[:4]), int(key[-1])
            start = date(year, (q - 1) * 3 + 1, 1)
            end_month = q * 3
            end = date(year + (end_month // 12), (end_month % 12) + 1, 1) - timedelta(days=1)
            return start, end
    except (ValueError, IndexError):
        pass
    return None, None


def format_git_oneline(start: date, end: date) -> str:
    """Pre-query git commit logs for all active repos in a date range (oneline format).

    Returns sections of "### {repo}\n{commits}" joined by double newline.
    """
    after = (start - timedelta(days=1)).isoformat()
    before = (end + timedelta(days=1)).isoformat()
    sections: list[str] = []
    for repo in GIT_REPOS:
        repo_path = Path(f"/realm/project/{repo}")
        if not repo_path.exists():
            continue
        try:
            result = subprocess.run(
                ["git", "-C", str(repo_path), "log", "--oneline",
                 f"--after={after}", f"--before={before}"],
                capture_output=True, text=True, check=False, timeout=10,
            )
            commits = result.stdout.strip()
            if commits:
                sections.append(f"### {repo}\n{commits}")
        except (subprocess.TimeoutExpired, OSError):
            continue
    return "\n\n".join(sections) if sections else "No commits found."


def format_warehouse_context(start: date, end: date) -> str:
    """Pre-query DuckDB for per-project breakdowns and episodes.

    Requires `artefacts/lynchpin/warehouse.duckdb` to exist.
    """
    import shutil

    duckdb_cli = shutil.which("duckdb")
    if not duckdb_cli:
        return ""
    db = "artefacts/lynchpin/warehouse.duckdb"
    sections: list[str] = []

    # Per-project time per day
    try:
        result = subprocess.run(
            [duckdb_cli, db, "-c",
             f"SELECT date, project, round(duration_seconds/3600.0, 2) as hours "
             f"FROM trajectory_day_project "
             f"WHERE date BETWEEN '{start}' AND '{end}' "
             f"AND duration_seconds > 300 "
             f"ORDER BY date, duration_seconds DESC"],
            capture_output=True, text=True, check=False, timeout=10,
        )
        if result.stdout.strip():
            sections.append(f"### Per-project time (>5min)\n{result.stdout.strip()}")
    except (subprocess.TimeoutExpired, OSError):
        pass

    # Episodes overlapping the range
    try:
        result = subprocess.run(
            [duckdb_cli, db, "-c",
             f"SELECT label, start_date, end_date, trigger, confidence, "
             f"dominant_mode, dominant_project "
             f"FROM trajectory_episode "
             f"WHERE end_date >= '{start}' AND start_date <= '{end}'"],
            capture_output=True, text=True, check=False, timeout=10,
        )
        if result.stdout.strip():
            sections.append(f"### Episodes\n{result.stdout.strip()}")
    except (subprocess.TimeoutExpired, OSError):
        pass

    return "\n\n".join(sections)


def format_activity_spans(start: date, end: date, min_seconds: float = 60) -> str:
    """Format ActivityWatch activity as app sessions with mode/project.

    Uses the processed app_sessions module (which coalesces raw AW events
    into meaningful sessions with interruption tracking and attribution).
    Falls back to trajectory signals if processed sessions unavailable.
    """
    dt_start = datetime(start.year, start.month, start.day)
    dt_end = datetime(end.year, end.month, end.day) + timedelta(days=1)

    try:
        from ..sources.processed.app_sessions import iter_app_sessions

        sessions = list(iter_app_sessions(
            start=dt_start, end=dt_end,
            min_duration_seconds=min_seconds,
        ))
        if sessions:
            lines = [f"### App sessions ({len(sessions)} sessions)"]
            for s in sessions:
                dur_m = s.duration_seconds / 60
                mode = f" [{s.mode}]" if s.mode else ""
                project = f" @{s.project}" if s.project else ""
                intr = f" ({s.interruptions} interruptions)" if s.interruptions else ""
                lines.append(
                    f"{s.start.strftime('%H:%M')}–{s.end.strftime('%H:%M')} "
                    f"({dur_m:.0f}m) {s.app} | {s.title_dominant[:70]}{mode}{project}{intr}"
                )
            return "\n".join(lines)
    except Exception:
        pass

    # Fallback to trajectory signals
    try:
        from ..trajectory.signal import load_signals

        signals = load_signals(start=dt_start, end=dt_end)
        aw_signals = [
            s for s in signals
            if s.source == "activitywatch.window"
            and (s.end - s.start).total_seconds() >= min_seconds
        ]
        if not aw_signals:
            return ""
        lines = [f"### Activity spans ({len(aw_signals)} spans)"]
        for s in aw_signals:
            dur_m = (s.end - s.start).total_seconds() / 60
            mode = f" [{s.mode_hint}]" if s.mode_hint else ""
            project = f" @{s.project_hint}" if s.project_hint else ""
            lines.append(
                f"{s.start.strftime('%H:%M')}–{s.end.strftime('%H:%M')} "
                f"({dur_m:.0f}m) {s.app or '?'} | {(s.title or '?')[:70]}{mode}{project}"
            )
        return "\n".join(lines)
    except Exception:
        return ""


def format_shell_commands(start: date, end: date) -> str:
    """Query Atuin shell commands for a date range."""
    from ..sources.captures.atuin import iter_commands

    dt_start = datetime(start.year, start.month, start.day)
    dt_end = datetime(end.year, end.month, end.day) + timedelta(days=1)

    try:
        cmds = list(iter_commands(start=dt_start, end=dt_end))
    except Exception:
        return ""

    if not cmds:
        return ""

    lines = [f"### Shell commands ({len(cmds)} commands)"]
    for c in cmds:
        exit_mark = "" if c.exit_code == 0 else f" [exit:{c.exit_code}]"
        cwd_short = str(c.cwd).replace("/realm/project/", "")
        lines.append(
            f"{c.timestamp.strftime('%H:%M')} {cwd_short}$ {c.command[:120]}{exit_mark}"
        )
    return "\n".join(lines)


def format_git_commits(start: date, end: date) -> str:
    """Query git commits with full messages and stat summaries."""
    after = (start - timedelta(days=1)).isoformat()
    before = (end + timedelta(days=1)).isoformat()
    sections: list[str] = []
    for repo in GIT_REPOS:
        repo_path = Path(f"/realm/project/{repo}")
        if not repo_path.exists():
            continue
        try:
            result = subprocess.run(
                ["git", "-C", str(repo_path), "log",
                 "--format=%h %ai %s%n%b",
                 "--stat=80", "--stat-graph-width=10",
                 f"--after={after}", f"--before={before}"],
                capture_output=True, text=True, check=False, timeout=15,
            )
            output = result.stdout.strip()
            if output:
                sections.append(f"### {repo}\n{output}")
        except (subprocess.TimeoutExpired, OSError):
            continue
    return "\n\n".join(sections) if sections else ""


def format_sleep_data(start: date, end: date) -> str:
    """Query sleep data for a date range."""
    sleep_path = Path("/realm/data/exports/health/processed/sleep_all_nights.csv")
    if not sleep_path.exists():
        return ""

    try:
        import csv
        lines = ["### Sleep data"]
        with sleep_path.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                start_local = row.get("start_local", "")
                if not start_local:
                    continue
                sleep_date = start_local[:10]
                if sleep_date < str(start) or sleep_date > str(end + timedelta(days=1)):
                    continue
                dur = row.get("duration_minutes", "?")
                score = row.get("sleep_score", "?")
                source = row.get("source", "?")
                lines.append(
                    f"{start_local[:16]} → {row.get('end_local', '?')[:16]} "
                    f"({dur}min, score:{score}, {source})"
                )
        return "\n".join(lines) if len(lines) > 1 else ""
    except Exception:
        return ""


def format_metrics_summary(start: date, end: date) -> str:
    """Build a computed metrics summary from lynchpin.metrics module."""
    lines: list[str] = []
    dt_start = datetime(start.year, start.month, start.day)
    dt_end = datetime(end.year, end.month, end.day) + timedelta(days=1)

    # Focus metrics from AW + AFK
    try:
        from ..sources.captures.activitywatch import afk_events
        from ..metrics.focus import afk_split

        afk = list(afk_events(start=dt_start, end=dt_end))
        if afk:
            active_h, afk_h = afk_split(afk)
            lines.append(f"AFK-adjusted: {active_h:.1f}h active, {afk_h:.1f}h away")
    except Exception:
        pass

    # Shell command categories
    try:
        from ..sources.captures.atuin import iter_commands
        from ..metrics.productivity import commands_by_category, command_density

        cmds = list(iter_commands(start=dt_start, end=dt_end))
        if cmds:
            cats = commands_by_category(cmds)
            cat_str = ", ".join(f"{k}: {v}" for k, v in sorted(cats.items(), key=lambda x: -x[1])[:6])
            lines.append(f"Shell categories: {cat_str}")
    except Exception:
        pass

    # Sleep metrics
    try:
        from ..sources.exports.health import iter_samsung_sleep
        from ..metrics.health import sleep_summary

        for session in iter_samsung_sleep():
            session_start = getattr(session, "start_local", None) or getattr(session, "start_time", None)
            if session_start is None:
                continue
            session_date = str(session_start)[:10]
            if session_date < str(start) or session_date > str(end + timedelta(days=1)):
                continue
            sm = sleep_summary(session)
            if sm:
                lines.append(f"Sleep: {sm.total_hours:.1f}h, {sm.segments} segments, quality={sm.quality_label}")
    except Exception:
        pass

    return "\n".join(lines)


def format_deep_work(start: date, end: date) -> str:
    """Format deep work blocks for enrichment."""
    from ..sources.processed.deep_work import iter_deep_work

    blocks = list(iter_deep_work(
        start=datetime(start.year, start.month, start.day),
        end=datetime(end.year, end.month, end.day) + timedelta(days=1),
    ))
    if not blocks:
        return ""
    lines = [f"### Deep work blocks ({len(blocks)})"]
    for b in blocks:
        lines.append(
            f"{b.start.strftime('%H:%M')}-{b.end.strftime('%H:%M')} "
            f"({b.duration_minutes:.0f}m) @{b.project} focus={b.focus_ratio:.0%} "
            f"commits={b.commit_count} cmds={b.command_count}"
        )
    return "\n".join(lines)


def format_delegation(start: date, end: date) -> str:
    """Format delegation metrics."""
    from ..sources.processed.delegation import iter_delegation_metrics

    metrics = list(iter_delegation_metrics(start=start, end=end))
    if not metrics:
        return ""
    lines = ["### Delegation"]
    for m in metrics:
        lines.append(
            f"{m.date}: {m.delegation_mode} | {m.total_commits} commits "
            f"({m.ai_ratio:.0%} AI) | {m.commits_per_tracked_hour:.1f}/h | "
            f"{m.chat_sessions} chat sessions | models: "
            f"{', '.join(m.ai_models_used) or 'none'}"
        )
    return "\n".join(lines)


def format_circadian(start: date, end: date) -> str:
    """Format circadian profile."""
    from ..sources.processed.circadian import iter_circadian

    profiles = list(iter_circadian(start=start, end=end))
    if not profiles:
        return ""
    lines = ["### Circadian (hourly)"]
    for p in sorted(profiles, key=lambda x: x.hour):
        if p.active_minutes < 1:
            continue
        lines.append(
            f"  {p.hour:02d}:00 — {p.active_minutes:.0f}m active, "
            f"{p.recovery_minutes:.0f}m recovery | "
            f"{p.dominant_mode or '?'} @{p.dominant_project or '?'} | "
            f"{p.commit_count} commits, {p.app_switches} switches"
        )
    return "\n".join(lines)


def format_context_switches(start: date, end: date) -> str:
    """Format context switching metrics."""
    from ..sources.processed.context_switches import iter_context_switch_metrics

    metrics = list(iter_context_switch_metrics(start=start, end=end))
    if not metrics:
        return ""
    lines = ["### Focus & fragmentation"]
    for m in metrics:
        lines.append(
            f"{m.date}: {m.total_switches} switches "
            f"({m.project_switches} project, {m.mode_switches} mode) | "
            f"avg focus {m.avg_focus_minutes:.0f}m, longest {m.longest_focus_minutes:.0f}m | "
            f"fragmentation={m.fragmentation_score:.2f}"
        )
    return "\n".join(lines)


def format_project_attention(start: date, end: date) -> str:
    """Format project attention metrics."""
    from ..sources.processed.project_attention import iter_project_attention

    metrics = list(iter_project_attention(start=start, end=end))
    if not metrics:
        return ""
    lines = ["### Project attention"]
    for m in metrics:
        lines.append(
            f"{m.date}: entropy={m.entropy:.2f} gini={m.gini:.2f} | "
            f"top={m.top_project} ({m.top_project_share:.0%}) | "
            f"{m.project_count} projects, rotation={m.rotation_speed:.1f}/h"
        )
        if m.new_projects:
            lines.append(f"  new: {', '.join(m.new_projects)}")
        if m.dropped_projects:
            lines.append(f"  dropped: {', '.join(m.dropped_projects)}")
    return "\n".join(lines)


def build_day_enrichment(base_prompt: str, scale: NarrativeKind, key: str) -> str:
    """Enrich a prompt with dense source data for the Agent SDK backend.

    Layers data from most processed (orientation) to most raw (evidence):
    1. Base prompt (trajectory summary — orientation)
    2. Per-project time + episode context (structured processed)
    3. Coalesced ActivityWatch spans (what was on screen)
    4. Atuin shell commands (what was typed)
    5. Git commits with messages + stats (what changed)
    6. Sleep data (physiological context)
    """
    start, end = parse_date_range(scale, key)
    if start is None or end is None:
        return base_prompt

    enrichment_parts: list[str] = []

    # Structured processed data (DuckDB warehouse)
    duckdb_context = format_warehouse_context(start, end)
    if duckdb_context:
        enrichment_parts.append(f"## Warehouse context\n\n{duckdb_context}")

    # ActivityWatch focus spans (coalesced from raw events)
    aw_spans = format_activity_spans(start, end)
    if aw_spans:
        enrichment_parts.append(f"## Desktop activity\n\n{aw_spans}")

    # Shell commands
    atuin_cmds = format_shell_commands(start, end)
    if atuin_cmds:
        enrichment_parts.append(f"## Shell history\n\n{atuin_cmds}")

    # Git commits with full messages and stats
    git_detailed = format_git_commits(start, end)
    if git_detailed:
        enrichment_parts.append(f"## Git commits ({start} to {end})\n\n{git_detailed}")

    # Sleep data
    sleep = format_sleep_data(start, end)
    if sleep:
        enrichment_parts.append(f"## Health\n\n{sleep}")

    # Computed metrics summary (from lynchpin.metrics)
    metrics_lines = format_metrics_summary(start, end)
    if metrics_lines:
        enrichment_parts.append(f"## Computed metrics\n\n{metrics_lines}")

    # Processed statistical metrics
    try:
        deep = format_deep_work(start, end)
        if deep:
            enrichment_parts.append(f"## Deep work\n\n{deep}")
    except Exception:
        pass

    try:
        deleg = format_delegation(start, end)
        if deleg:
            enrichment_parts.append(f"## Delegation\n\n{deleg}")
    except Exception:
        pass

    try:
        circadian = format_circadian(start, end)
        if circadian:
            enrichment_parts.append(f"## Circadian\n\n{circadian}")
    except Exception:
        pass

    try:
        ctx = format_context_switches(start, end)
        if ctx:
            enrichment_parts.append(f"## Focus\n\n{ctx}")
    except Exception:
        pass

    try:
        proj = format_project_attention(start, end)
        if proj:
            enrichment_parts.append(f"## Project attention\n\n{proj}")
    except Exception:
        pass

    # Temporal context: neighbor days + higher-scale narratives
    if scale.value == "day" and start is not None:
        from .narrative import load_narratives

        # Previous and next day narratives (sparse — just the text, agent already has its own full data)
        prev_day = (start - timedelta(days=1)).isoformat()
        next_day = (start + timedelta(days=1)).isoformat()
        neighbors = load_narratives("day", [prev_day, next_day])
        if prev_day in neighbors:
            enrichment_parts.append(
                f"## Previous day ({prev_day}) narrative\n\n{neighbors[prev_day][:3000]}"
            )
        if next_day in neighbors:
            enrichment_parts.append(
                f"## Next day ({next_day}) narrative\n\n{neighbors[next_day][:3000]}"
            )

        # Higher-scale context: week, month, quarter narratives if they exist
        iso = start.isocalendar()
        week_key = f"{iso[0]}-W{iso[1]:02d}"
        month_key = start.strftime("%Y-%m")
        quarter_key = f"{start.year}-Q{(start.month - 1) // 3 + 1}"

        for label, kind_val, ctx_key in [
            ("week", "week", week_key),
            ("month", "month", month_key),
            ("quarter", "quarter", quarter_key),
        ]:
            ctx_narratives = load_narratives(kind_val, [ctx_key])
            if ctx_key in ctx_narratives:
                # Truncate higher-scale context to keep budget reasonable
                max_chars = {"week": 4000, "month": 3000, "quarter": 2000}[label]
                enrichment_parts.append(
                    f"## Current {label} ({ctx_key}) narrative (abbreviated)\n\n"
                    f"{ctx_narratives[ctx_key][:max_chars]}..."
                )

    if not enrichment_parts:
        return base_prompt

    return base_prompt + "\n\n---\n\n" + "\n\n".join(enrichment_parts)
