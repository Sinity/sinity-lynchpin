#!/usr/bin/env python3
"""
Generate a narrative for a date range directly from Lynchpin day snapshots.

Each day is loaded via `lynchpin.views.calendar.load_day` and summarised with
`lynchpin.views.calendar_summary`. Prompts and outputs are written under
`artefacts/calendar/narratives/`, and `codex prompt` handles the LLM call.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from textwrap import dedent
from typing import Any, Dict, List, Optional, Tuple

import typer

from ..core.io import write_text_if_changed

from .calendar_summary import DaySummary, load_day_summary, summarize_range, terminal_capture_overview_line

app = typer.Typer(pretty_exceptions_show_locals=False)

LOG_PATH = Path("artefacts/calendar/narratives/logs/narrative_runs.jsonl")

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
DEFAULT_MODEL = os.environ.get("LYNCHPIN_CALENDAR_MODEL", "gpt-5-mini")
MODEL_PRICING = {
    "gpt-5-mini": {"input": 0.0000015, "output": 0.0000020},
}
TOKEN_USAGE_RE = re.compile(
    r"Token usage: total=(\d+) input=(\d+) cached_input=(\d+) output=(\d+)(?: reasoning_output=(\d+))?"
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class DayRecord:
    dt: date
    weekday: str
    summary: DaySummary
    view_path: Optional[Path] = None

    def to_prompt_block(self) -> str:
        summary = self.summary
        categories = ", ".join(
            f"{name}: {int(count)} cmds ({_percentage(count, summary.command_total)}%)"
            for name, count in summary.command_categories.items()
        ) or "No shell activity recorded."
        instrumentation = terminal_capture_overview_line(summary.instrumentation)
        insights = "; ".join(summary.insights) or "No automatic highlights."
        repos = summary.git.repos or {}
        repo_text = ", ".join(f"{name} ({count})" for name, count in repos.items()) or "—"
        lines_added = summary.git.lines_added
        lines_deleted = summary.git.lines_deleted
        commits = summary.git.commits
        if summary.sleep:
            sleep_line = f"Sleep {summary.sleep.total_hours:.2f}h"
            if summary.sleep.segments:
                sleep_line += f" ({summary.sleep.segments} segment(s))"
            if summary.sleep.avg_score is not None:
                sleep_line += f", score {summary.sleep.avg_score}"
        else:
            sleep_line = "No wearable data"
        focus_summary = ""
        if summary.focus.categories:
            focus_summary = ", ".join(
                f"{name}: {minutes:.1f}m"
                for name, minutes in sorted(summary.focus.categories.items(), key=lambda item: item[1], reverse=True)[:4]
            )
        elif summary.focus.total_focus_minutes:
            focus_summary = f"{summary.focus.total_focus_minutes:.1f}m tracked"
        else:
            focus_summary = "No ActivityWatch data"
        session_summary = ", ".join(
            f"{session.get('label') or 'Session'} ({session.get('provider') or ''})"
            for session in summary.sessions[:3]
        ) or "No recorded sessions."
        transcript_summary = ", ".join(
            f"{record.get('title') or record.get('slug')} ({record.get('provider')})"
            for record in summary.transcripts[:3]
        ) or "No Polylogue transcripts."
        return dedent(
            f"""
            ### {self.dt.isoformat()} ({self.weekday})
            - Focus: {summary.overview.active_hours:.2f}h active / {summary.overview.afk_hours:.2f}h afk / {summary.overview.window_hours:.2f}h focused windows
            - Commands: {summary.command_total} total (Atuin logged {summary.atuin_commands}) — {categories}
            - Git: {commits} commits, +{lines_added:,} / -{lines_deleted:,} lines, repos: {repo_text}
            - Codex sessions: {summary.codex_sessions}
            - Session ledger: {session_summary}
            - Polylogue transcripts: {transcript_summary}
            - Focus signals: {focus_summary}
            - Instrumentation captures: {instrumentation}
            - Health: {sleep_line}
            - Insights: {insights}
            """
        ).strip()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _percentage(value: float, total: float) -> str:
    if total <= 0:
        return "0.0"
    return f"{(value / total) * 100:.1f}"

def _load_day_record(dt: date, calendar_dir: Path) -> DayRecord:
    summary = load_day_summary(dt)
    view_path = calendar_dir / "views" / f"day-{dt.isoformat()}.md"
    return DayRecord(
        dt=dt,
        weekday=dt.strftime("%A"),
        summary=summary,
        view_path=view_path if view_path.exists() else None,
    )


def _build_prompt(
    days: List[DayRecord],
    start: date,
    end: date,
    mode: str,
) -> str:
    range_summary = summarize_range([day.summary for day in days])

    profile = MODE_PROFILES.get(mode, MODE_PROFILES[DEFAULT_MODE])
    section_list = "\n".join(f"- {section}" for section in profile["sections"])

    day_blocks = "\n\n".join(day.to_prompt_block() for day in days)

    if range_summary.average_sleep_hours is not None:
        sleep_line = f"{range_summary.average_sleep_hours:.2f}h avg ({range_summary.total_sleep_segments} segment(s))"
    else:
        sleep_line = "No wearable data"

    prompt = f"""
You are Sinity's retrospective co-author. Write a cohesive narrative that covers {start.isoformat()} to {end.isoformat()} (inclusive).

Tone guidance: {profile['tone']}

Range overview:
- Total days: {total_days}
- Focused hours: {range_summary.total_focus_hours:.2f}h (AFK {range_summary.total_afk_hours:.2f}h)
- Shell commands: {range_summary.total_commands:,}
- Git commits: {range_summary.total_commits}
- Codex sessions: {range_summary.total_codex_sessions}
- Sessions logged: {range_summary.total_session_records} (Top: {range_summary.top_sessions})
- Terminal recording sessions: {range_summary.total_terminal_sessions} ({range_summary.total_terminal_active_hours:.2f}h active, {range_summary.total_terminal_events} events, {range_summary.total_terminal_commands} commands, {range_summary.total_terminal_failures} failures, new={range_summary.total_terminal_new_model_sessions}, legacy={range_summary.total_terminal_legacy_sessions})
- ActivityWatch minutes tracked: {range_summary.total_focus_minutes:.1f}
- Sleep logged: {sleep_line}
- Top highlights: {range_summary.top_insights}

Use the structured data below. Cite concrete numbers, weave in session insights, and follow this extra guidance: {profile['extra_guidance']}

{day_blocks}

Output requirements:
- Markdown with headings (##) for:
{section_list}
- Keep paragraphs succinct but vivid; weave in contrasts between days and reference specific dates.
"""
    return dedent(prompt).strip()


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
    generated_at = (generated_at or datetime.now(timezone.utc)).isoformat()
    header = [
        "---",
        f"start_date: {start.isoformat()}",
        f"end_date: {end.isoformat()}",
        f"mode: {mode}",
        f"generated_at: {generated_at}",
        f"prompt_path: {prompt_path}",
        "---",
        "",
    ]
    payload = "\n".join(header) + narrative.strip() + "\n"
    return write_text_if_changed(output_path, payload)


def _write_prompt(prompt_path: Path, prompt_text: str) -> bool:
    return write_text_if_changed(prompt_path, prompt_text)


def _log_run(entry: Dict[str, Any]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _parse_usage(text: str) -> Tuple[Optional[Dict[str, int]], str]:
    if not text:
        return None, text
    cleaned = text.replace("\r", "\n")
    match = TOKEN_USAGE_RE.search(cleaned)
    if not match:
        return None, cleaned
    usage = {
        "total_tokens": int(match.group(1)),
        "input_tokens": int(match.group(2)),
        "cached_input_tokens": int(match.group(3)),
        "output_tokens": int(match.group(4)),
        "reasoning_tokens": int(match.group(5) or 0),
    }
    cleaned = cleaned[: match.start()] + cleaned[match.end() :]
    return usage, cleaned


def _strip_verbose_lines(text: str) -> str:
    lines: List[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            lines.append(line)
            continue
        if stripped.startswith("Rate limits:"):
            continue
        if stripped.startswith("(reasoning summary)"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _estimate_cost(model: str, input_tokens: Optional[int], output_tokens: Optional[int]) -> Optional[float]:
    pricing = MODEL_PRICING.get(model)
    if not pricing or input_tokens is None or output_tokens is None:
        return None
    return input_tokens * pricing["input"] + output_tokens * pricing["output"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


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
        help="File to store the generated narrative (defaults to artefacts/calendar/narratives/<range>.md).",
    ),
    prompt_only: bool = typer.Option(
        False,
        "--prompt-only",
        help="Only print the prompt; do not call codex.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Re-run even if output already exists.",
    ),
    model: Optional[str] = typer.Option(
        None,
        "--model",
        help="Override default Codex model (passed to `codex prompt --model ...`).",
    ),
):
    """Assemble a range prompt and run it through `codex prompt`."""
    start_date = _parse_iso_date(start)
    end_date = _parse_iso_date(end)
    if end_date < start_date:
        raise typer.BadParameter("END must be on or after START")

    dates = _daterange(start_date, end_date)
    calendar_dir = calendar_dir.expanduser()
    day_records = [_load_day_record(dt, calendar_dir) for dt in dates]

    mode_tokens = [token.strip() for token in mode.split(",") if token.strip()]
    if not mode_tokens:
        mode_tokens = [DEFAULT_MODE]
    if output and len(mode_tokens) > 1:
        raise typer.BadParameter("--output can only be used when a single mode is requested.")

    prompt_dir = calendar_dir / "narratives" / "prompts"

    for mode_name in mode_tokens:
        prompt_text = _build_prompt(day_records, start_date, end_date, mode_name)
        prompt_filename = f"{start_date.isoformat()}_to_{end_date.isoformat()}_{mode_name}.prompt.txt"
        prompt_path = prompt_dir / prompt_filename
        prompt_written = _write_prompt(prompt_path, prompt_text)

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
            if prompt_written:
                typer.secho(f"Prompt updated at {prompt_path}", fg=typer.colors.GREEN)
            continue

        model_name = model or DEFAULT_MODEL
        cmd = ["codex", "prompt", "--verbose", "--model", model_name, prompt_text]
        started = datetime.now(timezone.utc)
        result = subprocess.run(cmd, capture_output=True, text=True)
        duration_ms = (datetime.now(timezone.utc) - started).total_seconds() * 1000.0
        if result.returncode != 0:
            typer.secho(f"codex prompt failed for mode {mode_name}", fg=typer.colors.RED, err=True)
            typer.echo(result.stderr, err=True)
            raise typer.Exit(code=result.returncode)

        usage_stdout, cleaned_stdout = _parse_usage(result.stdout)
        usage_stderr, _ = _parse_usage(result.stderr)
        usage = usage_stdout or usage_stderr
        narrative_text = _strip_verbose_lines(cleaned_stdout).strip()
        wrote = _write_output(output_path, prompt_path, start_date, end_date, mode_name, narrative_text)
        if wrote:
            typer.echo(f"Narrative written to {output_path}")
        else:
            typer.echo(f"Narrative unchanged at {output_path}")

        cost = None
        if usage:
            cost = _estimate_cost(model_name, usage.get("input_tokens"), usage.get("output_tokens"))

        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "mode": mode_name,
            "model": model_name,
            "prompt_path": str(prompt_path),
            "output_path": str(output_path),
            "prompt_written": prompt_written,
            "output_written": wrote,
            "duration_ms": duration_ms,
            "token_usage": usage,
            "cost_usd": cost,
        }
        _log_run(log_entry)


if __name__ == "__main__":
    app()
