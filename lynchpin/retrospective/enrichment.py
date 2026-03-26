"""Data enrichment functions for building rich agent prompts.

This module provides reusable data formatting functions that extract and structure
information from ActivityWatch, shell history, git repositories, DuckDB warehouses,
and wearables. These functions serve interactive sessions and package-level
retrospective workflows, enabling consistent data representation across agents.

Key functions:
- parse_date_range(): parse scale + key into start/end date
- format_period_bundle(): build compact evidence-bundle context
- format_activity_spans(): format canonical focus timeline with AFK overrides
- format_shell_commands(): format shell command history
- format_git_commits(): format git change evidence with diff excerpts
- format_git_oneline(): format git log oneline summary
- format_warehouse_context(): query DuckDB for structured data
- format_sleep_data(): format wearable sleep records
- format_metrics_summary(): summarize canonical processed/context surfaces
- build_day_enrichment(): layer all sources into enriched prompt
"""

from __future__ import annotations
from collections import Counter
import logging
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from ..context.bundles import build_period_evidence_bundle
from ..context.trust import open_warehouse_read_only, render_surface_freshness_markdown
from ..periods import parse_period

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
    period = parse_period(scale, key)
    if period is None:
        return None, None
    return period.start, period.end


def format_period_bundle(
    scale: NarrativeKind,
    key: str,
    *,
    materialize_bundle: bool = False,
) -> str:
    """Build a compact evidence-bundle summary for prompt enrichment."""
    try:
        bundle = build_period_evidence_bundle(scale, key, write=materialize_bundle)
    except Exception as exc:
        log.warning("Failed to build period evidence bundle for %s %s: %s", scale, key, exc)
        return ""

    lines: list[str] = []
    if bundle.bundle_ref:
        lines.append(f"- Stored bundle: `{bundle.bundle_ref}`")

    freshness = render_surface_freshness_markdown(bundle.freshness)
    if freshness:
        lines.extend(["### Freshness", freshness])

    lines.append("### Query previews")
    for query in bundle.queries:
        lines.append(f"#### {query.title} ({query.row_count} rows)")
        if query.error:
            lines.append(f"- Error: {query.error}")
            continue
        if not query.rows:
            lines.append("- No rows returned.")
            continue
        lines.extend(_preview_rows(query.rows[:5]))

    return "\n".join(lines).strip()


def _preview_rows(rows: list[dict[str, object]]) -> list[str]:
    rendered: list[str] = []
    for row in rows:
        compact = ", ".join(
            f"{key}={_preview_value(value)}"
            for key, value in row.items()
            if value not in (None, "", [], {})
        )
        rendered.append(f"- {compact or 'n/a'}")
    return rendered


def _preview_value(value: object) -> str:
    if isinstance(value, str):
        text = value.replace("\n", " ").strip()
        if len(text) > 80:
            return text[:77] + "..."
        return text
    return str(value)


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
    """Query structured warehouse slices for focus and git evidence."""
    sections: list[str] = []
    conn = open_warehouse_read_only()
    try:
        sections.extend(_render_warehouse_section(
            conn,
            "Focus states",
            """
            SELECT date, span_kind, round(sum(duration_seconds)/3600.0, 2) AS hours,
                   sum(keypress_count) AS keypresses
            FROM processed_focus_spans
            WHERE date BETWEEN ? AND ?
            GROUP BY date, span_kind
            ORDER BY date, span_kind
            """,
            [start.isoformat(), end.isoformat()],
        ))
        sections.extend(_render_warehouse_section(
            conn,
            "Hourly focus history",
            """
            SELECT date, strftime(start, '%H:00') AS hour,
                   round(sum(CASE WHEN span_kind = 'focused' THEN duration_seconds ELSE 0 END)/60.0, 1) AS focused_minutes,
                   round(sum(CASE WHEN keylog_state = 'keyboard_active' THEN duration_seconds ELSE 0 END)/60.0, 1) AS keyboard_active_minutes,
                   round(sum(CASE WHEN span_kind = 'afk' THEN duration_seconds ELSE 0 END)/60.0, 1) AS afk_minutes
            FROM processed_focus_spans
            WHERE date BETWEEN ? AND ?
            GROUP BY date, hour
            HAVING focused_minutes > 0 OR keyboard_active_minutes > 0 OR afk_minutes > 0
            ORDER BY date, hour
            LIMIT 120
            """,
            [start.isoformat(), end.isoformat()],
        ))
        sections.extend(_render_warehouse_section(
            conn,
            "Per-project focus (>5min)",
            """
            SELECT date, project, round(sum(duration_seconds)/3600.0, 2) AS hours
            FROM processed_focus_spans
            WHERE date BETWEEN ? AND ?
              AND span_kind = 'focused'
              AND project IS NOT NULL
            GROUP BY date, project
            HAVING sum(duration_seconds) > 300
            ORDER BY date, hours DESC, project
            LIMIT 80
            """,
            [start.isoformat(), end.isoformat()],
        ))
        sections.extend(_render_warehouse_section(
            conn,
            "Alternating focus loops",
            """
            SELECT date, strftime(start, '%H:%M') AS start_hm,
                   round(duration_minutes, 1) AS duration_minutes, switch_count, cycle_count,
                   context_a_app, context_a_title, context_b_app, context_b_title, dominant_project, dominant_mode
            FROM processed_focus_loops
            WHERE date BETWEEN ? AND ?
            ORDER BY date, start
            LIMIT 80
            """,
            [start.isoformat(), end.isoformat()],
        ))
        sections.extend(_render_warehouse_section(
            conn,
            "Git change surface",
            """
            SELECT date, repo, path_root, sum(lines_changed) AS lines_changed,
                   count(*) AS file_change_events
            FROM processed_git_file_facts
            WHERE date BETWEEN ? AND ?
            GROUP BY date, repo, path_root
            ORDER BY date, lines_changed DESC, repo, path_root
            LIMIT 100
            """,
            [start.isoformat(), end.isoformat()],
        ))
    except Exception as exc:
        log.warning("Failed to build warehouse context for %s..%s: %s", start, end, exc)
        return ""
    finally:
        conn.close()

    return "\n\n".join(sections)


def _render_warehouse_section(
    conn: object,
    title: str,
    sql: str,
    params: list[object],
    *,
    preview_limit: int = 12,
) -> list[str]:
    try:
        rows = _query_warehouse_rows(conn, sql, params)
    except Exception as exc:
        log.debug("Skipping warehouse section %s: %s", title, exc)
        return []

    if not rows:
        return []

    section = [f"### {title}"]
    section.extend(_preview_rows(rows[:preview_limit]))
    if len(rows) > preview_limit:
        section.append(f"- ... {len(rows) - preview_limit} more rows")
    return section


def _query_warehouse_rows(
    conn: object,
    sql: str,
    params: list[object],
) -> list[dict[str, object]]:
    cursor = conn.execute(sql, params)
    columns = [column[0] for column in cursor.description]
    return [
        dict(zip(columns, row, strict=False))
        for row in cursor.fetchall()
    ]


def format_activity_spans(start: date, end: date, min_seconds: float = 60) -> str:
    """Format canonical focus spans with explicit AFK and keylog state.

    Uses the processed focus timeline, which treats AFK as a first-class
    focus override and attaches keyboard evidence when available.
    Falls back to activity signals if processed sessions are unavailable.
    """
    dt_start = datetime(start.year, start.month, start.day)
    dt_end = datetime(end.year, end.month, end.day) + timedelta(days=1)

    try:
        from ..sources.processed.focus_spans import iter_focus_spans

        spans = list(iter_focus_spans(
            start=dt_start, end=dt_end,
            min_duration_seconds=min_seconds,
        ))
        if spans:
            lines = [f"### Focus timeline ({len(spans)} spans)"]
            for span in spans:
                dur_m = span.duration_seconds / 60
                keylog = ""
                if span.keylog_state == "keyboard_active":
                    keylog = f" | keys={span.keypress_count}"
                elif span.keylog_state == "keyboard_silent":
                    keylog = " | keys=0"
                if span.span_kind == "focused":
                    mode = f" [{span.mode}]" if span.mode else ""
                    project = f" @{span.project}" if span.project else ""
                    label = f"{span.app} | {(span.title or '(untitled)')[:70]}{mode}{project}"
                elif span.span_kind == "afk":
                    label = "AFK / focus=nil"
                else:
                    label = "active / unattributed"
                lines.append(
                    f"{span.start.strftime('%H:%M')}–{span.end.strftime('%H:%M')} "
                    f"({dur_m:.0f}m) {label}{keylog}"
                )
            return "\n".join(lines)
    except Exception:
        pass

    # Fallback to activity signals
    try:
        from ..signals import load_signals

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
    """Query git change evidence with changed paths and diff excerpts."""
    from ..sources.processed.git_commit_facts import git_patch_excerpt, iter_git_commit_facts

    repo_paths = [Path(f"/realm/project/{repo}") for repo in GIT_REPOS if Path(f"/realm/project/{repo}").exists()]
    facts = list(iter_git_commit_facts(start=start, end=end, repos=repo_paths))
    if not facts:
        return ""

    facts.sort(key=lambda fact: (fact.repo, fact.authored_at, fact.commit))
    patch_commit_ids = {
        fact.commit
        for fact in sorted(
            facts,
            key=lambda fact: (-fact.lines_changed, fact.authored_at, fact.commit),
        )[:12]
    }

    sections: list[str] = []
    for repo in sorted({fact.repo for fact in facts}):
        repo_facts = [fact for fact in facts if fact.repo == repo]
        repo_path = Path(f"/realm/project/{repo}")
        lines = [f"### {repo}"]
        for fact in repo_facts:
            roots = f" roots={','.join(fact.path_roots[:6])}" if fact.path_roots else ""
            lines.append(
                f"{fact.commit[:7]} {fact.authored_at.isoformat(sep=' ', timespec='seconds')} "
                f"+{fact.lines_added}/-{fact.lines_deleted} files={fact.files_changed}{roots}"
            )
            if fact.subject:
                lines.append(f"subject: {fact.subject}")
            if fact.paths:
                suffix = " ..." if len(fact.paths) > 12 else ""
                lines.append(f"paths: {', '.join(fact.paths[:12])}{suffix}")
            if fact.commit in patch_commit_ids:
                patch = git_patch_excerpt(repo_path=repo_path, commit=fact.commit, max_lines=80)
                if patch.patch_excerpt:
                    note = f" [truncated 80/{patch.line_count} lines]" if patch.truncated else ""
                    lines.append(f"patch{note}:")
                    lines.append(f"```diff\n{patch.patch_excerpt}\n```")
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


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
    """Build a compact summary from canonical processed/context surfaces."""
    lines: list[str] = []
    try:
        from ..sources.processed.delivery_telemetry import iter_delivery_telemetry

        delivery = list(iter_delivery_telemetry(start=start, end=end))
        if delivery:
            repo_counter: Counter[str] = Counter()
            model_counter: Counter[str] = Counter()
            total_active = 0.0
            total_commits = 0
            total_commands = 0
            total_chat_minutes = 0.0
            for row in delivery:
                total_active += row.active_hours
                total_commits += row.total_commits
                total_commands += row.command_count
                total_chat_minutes += row.chat_engaged_minutes
                repo_counter.update(row.repos)
                model_counter.update(row.ai_models_used)
            lines.append(
                "Delivery: "
                f"{total_active:.1f}h active, {total_commits} commits, {total_commands} commands, "
                f"{total_chat_minutes:.0f} engaged chat min"
            )
            if repo_counter:
                lines.append(f"Repos: {', '.join(name for name, _ in repo_counter.most_common(4))}")
            if model_counter:
                lines.append(f"Models: {', '.join(name for name, _ in model_counter.most_common(4))}")
    except Exception:
        pass

    try:
        from ..sources.processed.context_switches import iter_context_switch_metrics

        switch_metrics = list(iter_context_switch_metrics(start=start, end=end))
        if switch_metrics:
            avg_focus = sum(row.avg_focus_minutes for row in switch_metrics) / len(switch_metrics)
            longest_focus = max(row.longest_focus_minutes for row in switch_metrics)
            avg_fragmentation = sum(row.fragmentation_score for row in switch_metrics) / len(switch_metrics)
            total_switches = sum(row.total_switches for row in switch_metrics)
            lines.append(
                "Focus: "
                f"{avg_focus:.0f}m avg focus, {longest_focus:.0f}m longest focus, "
                f"{total_switches} switches, fragmentation={avg_fragmentation:.2f}"
            )
    except Exception:
        pass

    try:
        from ..sources.processed.project_attention import iter_project_attention

        attention = list(iter_project_attention(start=start, end=end))
        if attention:
            top_projects = Counter(str(row.top_project or "unknown") for row in attention if row.top_project)
            avg_entropy = sum(row.entropy for row in attention) / len(attention)
            avg_rotation = sum(row.rotation_speed for row in attention) / len(attention)
            dominant_project = top_projects.most_common(1)[0][0] if top_projects else "n/a"
            lines.append(
                "Attention: "
                f"entropy={avg_entropy:.2f}, rotation={avg_rotation:.2f}/h, dominant project={dominant_project}"
            )
    except Exception:
        pass

    try:
        from ..sources.processed.circadian import iter_circadian

        by_hour = [
            row for row in iter_circadian(start=start, end=end)
            if row.active_minutes >= 1
        ]
        if by_hour:
            peak_hours = sorted(by_hour, key=lambda row: row.active_minutes, reverse=True)[:3]
            hour_summary = ", ".join(f"{row.hour:02d}:00 ({row.active_minutes:.0f}m)" for row in peak_hours)
            lines.append(f"Circadian peaks: {hour_summary}")
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
            f"git={b.git_lines_changed} lines/{b.git_files_changed} files cmds={b.command_count}"
        )
    return "\n".join(lines)


def format_delivery_telemetry(start: date, end: date) -> str:
    """Format daily continuous delivery telemetry without rigid workflow buckets."""
    from ..sources.processed.delivery_telemetry import iter_delivery_telemetry

    metrics = list(iter_delivery_telemetry(start=start, end=end))
    if not metrics:
        return ""
    lines = [
        "### Delivery telemetry",
        "_Continuous metrics only. Do not infer authorship or output quality from commit counts alone._",
    ]
    for m in metrics:
        lines.append(
            f"{m.date}: {m.active_hours:.1f}h active | "
            f"{m.total_commits} commits ({m.ai_ratio:.0%} AI, {m.commit_density_per_active_hour:.1f}/h) | "
            f"{m.command_count} commands ({m.command_density_per_active_hour:.1f}/h) | "
            f"{m.chat_sessions} chat sessions, {m.chat_engaged_minutes:.0f} engaged min "
            f"({m.chat_minutes_per_active_hour:.1f}/h) | models: "
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
            f"git={p.git_lines_changed} lines/{p.git_files_changed} files, "
            f"{p.command_count} commands, {p.app_switches} switches"
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


def build_day_enrichment(
    base_prompt: str,
    scale: NarrativeKind,
    key: str,
    *,
    materialize_bundle: bool = False,
) -> str:
    """Enrich a prompt with dense source data for the Agent SDK backend.

    Layers data from most structured evidence to most raw evidence:
    1. Period evidence bundle and freshness summary
    2. Processed focus + git context
    3. Canonical focus timeline
    4. Atuin shell commands
    5. Git change evidence with diff excerpts
    6. Sleep data
    """
    start, end = parse_date_range(scale, key)
    if start is None or end is None:
        return base_prompt

    enrichment_parts: list[str] = []

    bundle_summary = format_period_bundle(scale, key, materialize_bundle=materialize_bundle)
    if bundle_summary:
        enrichment_parts.append(f"## Evidence bundle\n\n{bundle_summary}")

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

    # Git change evidence with diff excerpts
    git_detailed = format_git_commits(start, end)
    if git_detailed:
        enrichment_parts.append(f"## Git changes ({start} to {end})\n\n{git_detailed}")

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
        delivery = format_delivery_telemetry(start, end)
        if delivery:
            enrichment_parts.append(f"## Delivery telemetry\n\n{delivery}")
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
