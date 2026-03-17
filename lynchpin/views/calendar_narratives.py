#!/usr/bin/env python3
"""Generate narratives from trajectory-backed day/week data.

Uses the context/narrative module (claude_agent_sdk) for LLM calls,
replacing the old codex prompt subprocess approach.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from textwrap import dedent
from typing import List, Optional

import typer

from ..core.io import write_text_if_changed
from ..trajectory.chains import build_chains_from_attributed
from ..trajectory.day import TrajectoryDay, summarize_days
from ..trajectory.rules import classify_signals
from ..trajectory.signal import load_signals, resolve_window
from ..trajectory.week import TrajectoryWeek

app = typer.Typer(pretty_exceptions_show_locals=False)

MODE_PROFILES = {
    "reflective": {
        "tone": "Focus on insights, emotional texture, and lessons learned.",
        "sections": [
            "Context",
            "Work",
            "Knowledge/Chats",
            "Life & Recovery",
            "Instrumentation",
            "Reflections",
        ],
        "extra_guidance": "Highlight how focus, rest, and conversations shaped the arc.",
    },
    "executive": {
        "tone": "Deliver a crisp status update for stakeholders.",
        "sections": [
            "Summary",
            "Outcomes",
            "Risks/Blockers",
            "Metrics",
            "Next Steps",
        ],
        "extra_guidance": "Prioritize bullet points, quantify impact, and call out blockers.",
    },
    "playful": {
        "tone": "Use a lighter, narrative tone while still conveying the facts.",
        "sections": [
            "Story Beats",
            "Work Highlights",
            "Curiosities",
            "Recovery & Mood",
            "Looking Ahead",
        ],
        "extra_guidance": "Lean into vivid language, metaphors, or imagery while staying accurate.",
    },
    "retro": {
        "tone": "Structure like a retrospective.",
        "sections": [
            "What Worked",
            "What Didn't",
            "Experiments / Ideas",
            "Data Signals",
            "Action Items",
        ],
        "extra_guidance": "Tie observations back to practices, tools, or collaboration patterns.",
    },
    "tactical": {
        "tone": "Be pragmatic and action-oriented.",
        "sections": [
            "Operational Summary",
            "Completed",
            "In Progress",
            "Watch Items",
            "Todo / Follow-ups",
        ],
        "extra_guidance": "List concrete tasks, resource needs, and deadlines where possible.",
    },
}
DEFAULT_MODE = "reflective"


def _parse_iso_date(value: str) -> date:
    value = value.strip()
    if not value:
        raise typer.BadParameter("Date value cannot be empty")
    if len(value) <= 10:
        return date.fromisoformat(value)
    value = value.replace("Z", "+00:00")
    return datetime.fromisoformat(value).date()


def _daterange(start: date, end: date) -> List[date]:
    days: List[date] = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


def _load_trajectory_days(start_date: date, end_date: date) -> list[TrajectoryDay]:
    """Load trajectory days for a date range."""
    local_tz = datetime.now().astimezone().tzinfo or timezone.utc
    win_start = datetime.combine(start_date, datetime.min.time(), tzinfo=local_tz)
    win_end = datetime.combine(end_date + timedelta(days=1), datetime.min.time(), tzinfo=local_tz)
    span = (end_date - start_date).days + 1
    start, end = resolve_window(start=win_start, end=win_end, days=span)
    signals = load_signals(start=start, end=end, days=span)
    attributed = classify_signals(signals)
    chains = build_chains_from_attributed(attributed)
    return list(summarize_days(
        signals=signals, chains=chains,
        start=start, end=end, days=span,
    ))


def _fmt_top(items: tuple[tuple[str, float], ...]) -> str:
    if not items:
        return "n/a"
    return ", ".join(f"{name}: {seconds / 60:.1f}m" for name, seconds in items[:4])


def _day_prompt_block(day: TrajectoryDay) -> str:
    weekday = day.date.strftime("%A")
    highlights = "; ".join(day.highlights) or "No automatic highlights."
    projects_line = ", ".join(f"{p.project} ({p.duration_seconds / 3600:.1f}h)" for p in day.projects[:4]) or "—"
    coverage = day.signal_coverage
    coverage_str = f"{coverage.quality} ({coverage.plane_count} planes)" if coverage else "n/a"
    return dedent(f"""
        ### {day.date.isoformat()} ({weekday})
        - Trajectory: {day.active_seconds / 3600:.2f}h active / {day.recovery_seconds / 3600:.2f}h recovery
        - Mode/project/topic: {day.dominant_mode or 'n/a'} / {day.dominant_project or 'n/a'} / {day.dominant_topic or 'n/a'}
        - Modes: {_fmt_top(day.top_modes)} — Projects: {_fmt_top(day.top_projects)} — Topics: {_fmt_top(day.top_topics)}
        - Commands: {day.command_count} — Commits: {day.commit_count} — Transcripts: {day.transcript_count}
        - Per-project: {projects_line}
        - Coverage: {coverage_str}
        - Highlights: {highlights}
    """).strip()


def build_day_narrative_prompt(day: TrajectoryDay, mode: str = DEFAULT_MODE) -> str:
    """Build a narrative prompt for a single day."""
    profile = MODE_PROFILES.get(mode, MODE_PROFILES[DEFAULT_MODE])
    section_list = "\n".join(f"- {s}" for s in profile["sections"])
    block = _day_prompt_block(day)
    return dedent(f"""
        You are Sinity's retrospective co-author. Write a cohesive narrative for {day.date.isoformat()}.

        Tone guidance: {profile['tone']}
        Extra guidance: {profile['extra_guidance']}

        {block}

        Output requirements:
        - Markdown with headings (##) for:
        {section_list}
        - Keep paragraphs succinct but vivid.
    """).strip()


def build_week_narrative_prompt(
    week: TrajectoryWeek,
    days: list[TrajectoryDay],
    mode: str = DEFAULT_MODE,
) -> str:
    """Build a narrative prompt for a week."""
    profile = MODE_PROFILES.get(mode, MODE_PROFILES[DEFAULT_MODE])
    section_list = "\n".join(f"- {s}" for s in profile["sections"])
    day_blocks = "\n\n".join(_day_prompt_block(d) for d in days)
    return dedent(f"""
        You are Sinity's retrospective co-author. Write a cohesive narrative for {week.iso_week} ({week.start_date} to {week.end_date}).

        Tone guidance: {profile['tone']}
        Extra guidance: {profile['extra_guidance']}

        Week overview:
        - Active hours: {week.active_seconds / 3600:.2f}h (Recovery: {week.recovery_seconds / 3600:.2f}h)
        - Chains: {week.chain_count} — Signals: {week.signal_count} — Commands: {week.command_count}
        - Commits: {week.commit_count} — Transcripts: {week.transcript_count}
        - Day pattern: {week.day_pattern}
        - Top modes: {_fmt_top(week.top_modes)}
        - Top projects: {_fmt_top(week.top_projects)}
        - Top topics: {_fmt_top(week.top_topics)}

        {day_blocks}

        Output requirements:
        - Markdown with headings (##) for:
        {section_list}
        - Keep paragraphs succinct but vivid; weave in contrasts between days and reference specific dates.
    """).strip()


def _build_range_prompt(
    days: list[TrajectoryDay],
    start: date,
    end: date,
    mode: str,
) -> str:
    """Build a narrative prompt for an arbitrary date range."""
    profile = MODE_PROFILES.get(mode, MODE_PROFILES[DEFAULT_MODE])
    section_list = "\n".join(f"- {s}" for s in profile["sections"])
    day_blocks = "\n\n".join(_day_prompt_block(d) for d in days)

    total_active = sum(d.active_seconds for d in days) / 3600
    total_recovery = sum(d.recovery_seconds for d in days) / 3600
    total_commands = sum(d.command_count for d in days)
    total_commits = sum(d.commit_count for d in days)
    total_transcripts = sum(d.transcript_count for d in days)

    # Compute range-level mode/project aggregation
    from collections import Counter
    mode_counter: Counter[str] = Counter()
    project_counter: Counter[str] = Counter()
    for d in days:
        for name, seconds in d.top_modes:
            mode_counter[name] += seconds
        for name, seconds in d.top_projects:
            project_counter[name] += seconds
    top_modes = sorted(mode_counter.items(), key=lambda x: -x[1])[:5]
    top_projects = sorted(project_counter.items(), key=lambda x: -x[1])[:5]

    modes_line = ", ".join(f"{n} ({s / 60:.1f}m)" for n, s in top_modes) or "n/a"
    projects_line = ", ".join(f"{n} ({s / 60:.1f}m)" for n, s in top_projects) or "n/a"

    return dedent(f"""
        You are Sinity's retrospective co-author. Write a cohesive narrative covering {start.isoformat()} to {end.isoformat()} (inclusive).

        Tone guidance: {profile['tone']}

        Range overview:
        - Total days: {len(days)}
        - Active hours: {total_active:.2f}h (Recovery: {total_recovery:.2f}h)
        - Shell commands: {total_commands:,}
        - Git commits: {total_commits}
        - Transcripts: {total_transcripts}
        - Dominant modes: {modes_line}
        - Dominant projects: {projects_line}

        Extra guidance: {profile['extra_guidance']}

        {day_blocks}

        Output requirements:
        - Markdown with headings (##) for:
        {section_list}
        - Keep paragraphs succinct but vivid; weave in contrasts between days and reference specific dates.
    """).strip()


def _write_output(
    output_path: Path,
    prompt_path: Path,
    start: date,
    end: date,
    mode: str,
    narrative: str,
    *,
    generated_at: Optional[datetime] = None,
) -> bool:
    generated_at_str = (generated_at or datetime.now(timezone.utc)).isoformat()
    header = [
        "---",
        f"start_date: {start.isoformat()}",
        f"end_date: {end.isoformat()}",
        f"mode: {mode}",
        f"generated_at: {generated_at_str}",
        f"prompt_path: {prompt_path}",
        "---",
        "",
    ]
    payload = "\n".join(header) + narrative.strip() + "\n"
    return write_text_if_changed(output_path, payload)


@app.command()
def narrative(
    start: str = typer.Argument(..., help="Start date YYYY-MM-DD"),
    end: str = typer.Argument(..., help="End date YYYY-MM-DD"),
    mode: str = typer.Option(
        "reflective",
        "--mode",
        help="Narrative mode(s). Pass comma-separated list (e.g. reflective,executive).",
    ),
    calendar_dir: Path = typer.Option(
        Path("artefacts/calendar"),
        "--calendar-dir",
        dir_okay=True,
        file_okay=False,
        help="Directory containing calendar outputs.",
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        help="File to store the generated narrative.",
    ),
    prompt_only: bool = typer.Option(
        False,
        "--prompt-only",
        help="Only print the prompt; do not call LLM.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Re-run even if output already exists.",
    ),
):
    """Assemble a range prompt and generate narrative via claude_agent_sdk."""
    from ..context.narrative import NarrativeKind, generate_narrative

    start_date = _parse_iso_date(start)
    end_date = _parse_iso_date(end)
    if end_date < start_date:
        raise typer.BadParameter("END must be on or after START")

    days = _load_trajectory_days(start_date, end_date)
    calendar_dir = calendar_dir.expanduser()

    mode_tokens = [token.strip() for token in mode.split(",") if token.strip()]
    if not mode_tokens:
        mode_tokens = [DEFAULT_MODE]
    if output and len(mode_tokens) > 1:
        raise typer.BadParameter("--output can only be used when a single mode is requested.")

    prompt_dir = calendar_dir / "narratives" / "prompts"

    for mode_name in mode_tokens:
        prompt_text = _build_range_prompt(days, start_date, end_date, mode_name)
        prompt_filename = f"{start_date.isoformat()}_to_{end_date.isoformat()}_{mode_name}.prompt.txt"
        prompt_path = prompt_dir / prompt_filename
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        write_text_if_changed(prompt_path, prompt_text)

        if prompt_only:
            typer.echo(f"--- Prompt ({mode_name}) ---")
            typer.echo(prompt_text)
            continue

        output_path = output or (
            calendar_dir
            / "narratives"
            / f"{start_date.isoformat()}_to_{end_date.isoformat()}_{mode_name}.md"
        )
        if output_path.exists() and not force:
            typer.secho(
                f"Narrative already exists at {output_path}; skipping. Use --force to regenerate.",
                fg=typer.colors.YELLOW,
            )
            continue

        typer.echo(f"[narrative] Generating {mode_name} narrative via claude_agent_sdk…", err=True)
        key = f"{start_date.isoformat()}_to_{end_date.isoformat()}_{mode_name}"
        result = asyncio.run(generate_narrative(prompt_text, NarrativeKind.week, key))

        wrote = _write_output(output_path, prompt_path, start_date, end_date, mode_name, result.text)
        if wrote:
            typer.echo(f"Narrative written to {output_path}")
        else:
            typer.echo(f"Narrative unchanged at {output_path}")

        typer.echo(
            f"[tokens: in={result.input_tokens} out={result.output_tokens} cost=${result.cost_usd:.4f}]",
            err=True,
        )


if __name__ == "__main__":
    app()
