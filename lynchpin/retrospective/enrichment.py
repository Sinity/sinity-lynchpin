"""Data enrichment functions for building rich agent prompts.

This module formats canonical evidence for retrospective prompting. Structured
period evidence comes from `lynchpin.context.bundles` and
`lynchpin.context.reports`; raw shell/git/sleep surfaces stay here because they
are only used as prompt-local supporting evidence.
"""

from __future__ import annotations
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..context.bundles import EvidenceBundle, EvidenceQuery, build_period_evidence_bundle
from ..context.reports import summarize_evidence_bundle
from ..context.trust import render_surface_freshness_markdown
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
    bundle: EvidenceBundle | None = None,
) -> str:
    """Build a compact evidence-bundle summary for prompt enrichment."""
    try:
        current_bundle = bundle or build_period_evidence_bundle(scale, key, write=materialize_bundle)
    except Exception as exc:
        log.warning("Failed to build period evidence bundle for %s %s: %s", scale, key, exc)
        return ""

    lines: list[str] = []
    if current_bundle.bundle_ref:
        lines.append(f"- Stored bundle: `{current_bundle.bundle_ref}`")

    freshness = render_surface_freshness_markdown(current_bundle.freshness)
    if freshness:
        lines.extend(["### Freshness", freshness])

    lines.append("### Query previews")
    for query in current_bundle.queries:
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


def format_evidence_summary(bundle: EvidenceBundle) -> str:
    """Render canonical bundle/report aggregates for prompt enrichment."""
    summary = summarize_evidence_bundle(bundle)
    lines = [
        "### Evidence coverage",
        (
            f"- Days with evidence: {summary['evidence']['days_with_evidence']} / "
            f"{summary['evidence']['period_days']}"
        ),
        f"- Surfaces present: {', '.join(summary['evidence']['surfaces_present']) or 'n/a'}",
        f"- Surfaces with errors: {', '.join(summary['evidence']['surfaces_with_errors']) or 'n/a'}",
        "",
        "### Delivery",
        (
            f"- {summary['delivery']['active_hours']}h active, "
            f"{summary['delivery']['total_commits']} commits, "
            f"{summary['delivery']['command_count']} commands, "
            f"{summary['delivery']['chat_engaged_minutes']} engaged chat min"
        ),
        f"- Repos: {_format_pairs(summary['delivery']['top_repos'])}",
        f"- Models: {_format_pairs(summary['delivery']['top_models'])}",
        "",
        "### Attention",
        (
            f"- Entropy={_value(summary['attention']['avg_entropy'])}, "
            f"rotation={_value(summary['attention']['avg_rotation_speed'])}/h"
        ),
        f"- Top projects: {_format_pairs(summary['attention']['top_projects'])}",
        "",
        "### Conversation",
        f"- Providers: {_format_pairs(summary['chat']['providers'])}",
        f"- Work kinds: {_format_pairs(summary['chat']['work_kinds'])}",
        (
            f"- Messages={summary['chat']['total_messages']}, "
            f"words={summary['chat']['total_words']}, "
            f"engaged={summary['chat']['engaged_minutes']}m, "
            f"cost={_value(summary['chat']['total_cost_usd'])}"
        ),
        f"- Projects: {_format_pairs(summary['chat']['projects'])}",
        f"- Session titles: {_format_pairs(summary['chat']['top_session_titles'])}",
        "",
        "### Git",
        f"- Repos by commits: {_format_pairs(summary['git']['repos'])}",
        f"- Repos by churn: {_format_pairs(summary['git']['churn'])}",
        f"- Hot paths: {_format_pairs(summary['git']['top_paths'])}",
        "",
        "### Focus",
        f"- Top spans: {_format_pairs(summary['focus']['top_spans'], suffix='m')}",
        f"- Top loops: {_format_pairs(summary['focus']['top_loops'], suffix='m')}",
        (
            f"- Switches={summary['focus']['total_switches']} "
            f"(project {summary['focus']['project_switches']}, mode {summary['focus']['mode_switches']}), "
            f"avg focus={_value(summary['focus']['avg_focus_minutes'])}m, "
            f"longest={_value(summary['focus']['longest_focus_minutes'])}m, "
            f"fragmentation={_value(summary['focus']['avg_fragmentation'])}"
        ),
        "",
        "### Patterns",
        f"- Episodes: {summary['patterns']['episode_count']} ({_format_labels(summary['patterns']['episode_labels'])})",
        f"- Anomalies: {summary['patterns']['anomaly_count']} ({_format_labels(summary['patterns']['anomaly_kinds'])})",
        f"- Recent focus loops: {_format_focus_loops(summary['patterns']['recent_focus_loops'])}",
        "",
        "### Circadian",
        f"- Active hours: {_format_pairs(summary['circadian']['active_minutes'], suffix='m')}",
        f"- Recovery minutes total: {_value(summary['circadian']['recovery_minutes_total'])}",
        f"- Dominant modes: {_format_pairs(summary['circadian']['dominant_modes'], suffix='m')}",
        f"- Dominant projects: {_format_pairs(summary['circadian']['dominant_projects'], suffix='m')}",
    ]
    return "\n".join(lines)


def format_bundle_query_preview(
    bundle: EvidenceBundle,
    query_id: str,
    title: str,
    *,
    preview_limit: int = 12,
) -> str:
    query = _bundle_query(bundle, query_id)
    if query is None:
        return ""
    lines = [f"### {title} ({query.row_count} rows)"]
    if query.error:
        lines.append(f"- Error: {query.error}")
        return "\n".join(lines)
    if not query.rows:
        return ""
    lines.extend(_preview_rows(query.rows[:preview_limit]))
    if query.row_count > preview_limit:
        lines.append(f"- ... {query.row_count - preview_limit} more rows")
    return "\n".join(lines)


def _bundle_query(bundle: EvidenceBundle, query_id: str) -> EvidenceQuery | None:
    for query in bundle.queries:
        if query.query_id == query_id:
            return query
    return None


def _format_pairs(values: list[tuple[str, Any]], *, suffix: str = "") -> str:
    if not values:
        return "n/a"
    return ", ".join(f"{label} ({value}{suffix})" for label, value in values)


def _format_labels(values: list[str]) -> str:
    return ", ".join(values) if values else "n/a"


def _format_focus_loops(values: list[dict[str, Any]]) -> str:
    if not values:
        return "n/a"
    rendered = []
    for item in values[:5]:
        label = item.get("dominant_project") or item.get("dominant_mode") or "unknown"
        duration = item.get("duration_minutes") or 0
        start = str(item.get("start") or "")[:10]
        rendered.append(f"{label} ({duration}m on {start})")
    return ", ".join(rendered)


def _value(value: Any) -> str:
    return "n/a" if value is None else str(value)


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

    try:
        bundle = build_period_evidence_bundle(scale, key, write=materialize_bundle)
    except Exception as exc:
        log.warning("Failed to build period evidence bundle for %s %s: %s", scale, key, exc)
        bundle = None

    bundle_summary = format_period_bundle(scale, key, materialize_bundle=materialize_bundle, bundle=bundle)
    if bundle_summary:
        enrichment_parts.append(f"## Evidence bundle\n\n{bundle_summary}")

    if bundle is not None:
        evidence_summary = format_evidence_summary(bundle)
        if evidence_summary:
            enrichment_parts.append(f"## Evidence summary\n\n{evidence_summary}")
        for query_id, title in [
            ("delivery_telemetry", "Delivery telemetry"),
            ("context_switches", "Focus & fragmentation"),
            ("project_attention", "Project attention"),
            ("circadian", "Circadian profile"),
            ("deep_work", "Deep work"),
            ("polylogue_sessions", "Conversation sessions"),
        ]:
            query_preview = format_bundle_query_preview(bundle, query_id, title)
            if query_preview:
                enrichment_parts.append(f"## {title}\n\n{query_preview}")

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

    # Temporal context: neighbor days + higher-scale narratives
    if getattr(scale, "value", scale) == "day" and start is not None:
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
