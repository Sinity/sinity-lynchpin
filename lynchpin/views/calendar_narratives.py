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

from . import calendar as lp_calendar
from ..core.io import write_text_if_changed

from .calendar_summary import summarize_day

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
    overview: Dict[str, float]
    command_total: int
    command_categories: Dict[str, int]
    codex_sessions: int
    atuin_commands: int
    git: Dict[str, int]
    instrumentation: Dict[str, int]
    insights: List[str]
    sessions: List[Dict[str, str]]
    transcripts: List[Dict[str, object]]
    health: Dict[str, Any]
    focus: Dict[str, Any]
    view_path: Optional[Path] = None

    def to_prompt_block(self) -> str:
        categories = ", ".join(
            f"{name}: {int(count)} cmds ({_percentage(count, self.command_total)}%)"
            for name, count in self.command_categories.items()
        ) or "No shell activity recorded."
        instrumentation = ", ".join(
            f"{kind}={count}" for kind, count in self.instrumentation.items() if count
        ) or "none"
        insights = "; ".join(self.insights) or "No automatic highlights."
        repos = self.git.get("repos") or {}
        repo_text = ", ".join(f"{name} ({count})" for name, count in repos.items()) or "—"
        lines_added = self.git.get("lines_added", 0)
        lines_deleted = self.git.get("lines_deleted", 0)
        commits = self.git.get("commits", 0)
        sleep = self.health.get("sleep") if self.health else None
        if sleep:
            sleep_hours = sleep.get("total_hours")
            sleep_line = f"Sleep {sleep_hours:.2f}h" if isinstance(sleep_hours, (int, float)) else "Sleep data"
            segments = _segment_count(sleep)
            if segments:
                sleep_line += f" ({segments} segment(s))"
            if sleep.get("avg_score") is not None:
                sleep_line += f", score {sleep['avg_score']}"
        else:
            sleep_line = "No wearable data"
        focus = self.focus or {}
        focus_summary = ""
        categories = focus.get("categories") or {}
        if categories:
            focus_summary = ", ".join(
                f"{name}: {minutes:.1f}m" for name, minutes in sorted(categories.items(), key=lambda item: item[1], reverse=True)[:4]
            )
        elif focus.get("total_focus_minutes"):
            focus_summary = f"{focus['total_focus_minutes']:.1f}m tracked"
        else:
            focus_summary = "No ActivityWatch data"
        session_summary = ", ".join(
            f"{session.get('label') or 'Session'} ({session.get('provider') or ''})"
            for session in self.sessions[:3]
        ) or "No recorded sessions."
        transcript_summary = ", ".join(
            f"{record.get('title') or record.get('slug')} ({record.get('provider')})"
            for record in self.transcripts[:3]
        ) or "No Polylogue transcripts."
        return dedent(
            f"""
            ### {self.dt.isoformat()} ({self.weekday})
            - Focus: {self.overview['active_hours']:.2f}h active / {self.overview['afk_hours']:.2f}h afk / {self.overview['window_hours']:.2f}h focused windows
            - Commands: {self.command_total} total (Atuin logged {self.atuin_commands}) — {categories}
            - Git: {commits} commits, +{lines_added:,} / -{lines_deleted:,} lines, repos: {repo_text}
            - Codex sessions: {self.codex_sessions}
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


def _segment_count(sleep: Dict[str, Any]) -> int:
    value = sleep.get("segments")
    if isinstance(value, int):
        return value
    if isinstance(value, (list, tuple)):
        return len(value)
    return 0


def _load_day_record(dt: date, calendar_dir: Path) -> DayRecord:
    snapshot = lp_calendar.load_day(dt)
    summary = summarize_day(snapshot)

    overview = {
        "active_hours": summary.overview.active_hours,
        "afk_hours": summary.overview.afk_hours,
        "window_hours": summary.overview.window_hours,
    }
    git_payload = {
        "commits": summary.git.commits,
        "lines_added": summary.git.lines_added,
        "lines_deleted": summary.git.lines_deleted,
        "repos": summary.git.repos,
    }
    health_payload: Dict[str, Any] = {}
    if summary.sleep:
        health_payload["sleep"] = {
            "total_hours": summary.sleep.total_hours,
            "segments": summary.sleep.segments,
            "avg_score": summary.sleep.avg_score,
        }
    focus_payload = {
        "categories": summary.focus.categories,
        "total_focus_minutes": summary.focus.total_focus_minutes,
    }
    sessions = list(summary.sessions)
    transcripts = list(summary.transcripts)
    view_path = calendar_dir / "views" / f"day-{dt.isoformat()}.md"
    return DayRecord(
        dt=dt,
        weekday=dt.strftime("%A"),
        overview=overview,
        command_total=summary.command_total,
        command_categories=summary.command_categories,
        codex_sessions=summary.codex_sessions,
        atuin_commands=summary.atuin_commands,
        git=git_payload,
        instrumentation={},
        insights=[],
        sessions=sessions,
        transcripts=transcripts,
        health=health_payload,
        focus=focus_payload,
        view_path=view_path if view_path.exists() else None,
    )


def _build_prompt(
    days: List[DayRecord],
    start: date,
    end: date,
    mode: str,
) -> str:
    total_days = len(days)
    total_focus = sum(day.overview["active_hours"] for day in days)
    total_afk = sum(day.overview["afk_hours"] for day in days)
    total_commands = sum(day.command_total for day in days)
    total_commits = sum(day.git["commits"] for day in days)
    total_codex = sum(day.codex_sessions for day in days)
    total_instrumentation = sum(sum(day.instrumentation.values()) for day in days)
    total_sessions = sum(len(day.sessions) for day in days)
    total_focus_minutes = sum(float(day.focus.get("total_focus_minutes", 0.0)) for day in days)
    sleep_hours_samples: List[float] = []
    total_sleep_segments = 0
    for day in days:
        sleep = day.health.get("sleep") if day.health else None
        if not sleep:
            continue
        hours = sleep.get("total_hours")
        if isinstance(hours, (int, float)):
            sleep_hours_samples.append(float(hours))
        total_sleep_segments += _segment_count(sleep)
    insight_counter = Counter()
    for day in days:
        insight_counter.update(day.insights)
    top_insights = ", ".join(f"{text}×{count}" for text, count in insight_counter.most_common(6)) or "No notable highlights captured."
    session_counter = Counter()
    for day in days:
        session_counter.update(
            (session.get("label") or "Session", session.get("provider") or "")
            for session in day.sessions
        )
    top_sessions = (
        ", ".join(
            f"{label}{' (' + provider + ')' if provider else ''}×{count}"
            for (label, provider), count in session_counter.most_common(5)
        )
        if session_counter
        else "No recorded sessions."
    )

    profile = MODE_PROFILES.get(mode, MODE_PROFILES[DEFAULT_MODE])
    section_list = "\n".join(f"- {section}" for section in profile["sections"])

    day_blocks = "\n\n".join(day.to_prompt_block() for day in days)

    if sleep_hours_samples:
        avg_sleep = sum(sleep_hours_samples) / len(sleep_hours_samples)
        sleep_line = f"{avg_sleep:.2f}h avg ({total_sleep_segments} segment(s))"
    else:
        sleep_line = "No wearable data"

    prompt = f"""
You are Sinity's retrospective co-author. Write a cohesive narrative that covers {start.isoformat()} to {end.isoformat()} (inclusive).

Tone guidance: {profile['tone']}

Range overview:
- Total days: {total_days}
- Focused hours: {total_focus:.2f}h (AFK {total_afk:.2f}h)
- Shell commands: {total_commands:,}
- Git commits: {total_commits}
- Codex sessions: {total_codex}
- Sessions logged: {total_sessions} (Top: {top_sessions})
- Instrumentation captures: {total_instrumentation}
- ActivityWatch minutes tracked: {total_focus_minutes:.1f}
- Sleep logged: {sleep_line}
- Top highlights: {top_insights}

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
