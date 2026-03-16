"""LLM narrative generation for trajectory periods.

Generates prose retrospectives from structured trajectory data using
claude_agent_sdk (subscription-based, no API key required).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..context.life_timeline import LifeMonthTrajectorySummary

log = logging.getLogger(__name__)

_NARRATIVE_LOG_DIR = Path("artefacts/knowledge/sessions/logs")

NARRATIVE_SYSTEM_PROMPT = """\
You are a concise personal retrospective analyst. You receive structured
summaries of a person's digital activity for a calendar period (trajectory
signals, git commits, chat sessions, episodes) and produce a 2–4 sentence
prose paragraph that synthesises the key themes and activity patterns.

Rules:
- Write in third person ("The period saw...", "Work focused on...", etc.)
- Be specific: name projects, dominant modes, and episode labels where present
- One tight paragraph only — no headers, no bullet points, no preamble
- If data is sparse, say so concisely rather than padding
- Do not speculate beyond what the data shows
"""


class NarrativeKind(str, Enum):
    day = "day"
    week = "week"
    month = "month"
    episode = "episode"
    quarter = "quarter"
    contrast = "contrast"


@dataclass(frozen=True)
class Narrative:
    kind: str  # NarrativeKind value
    key: str  # e.g. "2026-03"
    text: str
    generated_at: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


def build_month_prompt(traj: LifeMonthTrajectorySummary, *, month_key: str) -> str:
    """Assemble a structured prompt for a month retrospective."""
    lines = [
        f"Generate a retrospective for: {month_key}",
        "",
        f"Period: {traj.start_date} – {traj.end_date} ({traj.days} days)",
        f"Active: {traj.active_hours}h | Recovery: {traj.recovery_hours}h",
        f"Chains: {traj.chain_count} | Signals: {traj.signal_count} | Commits: {traj.commit_count}",
    ]

    if traj.dominant_modes:
        modes_str = ", ".join(f"{m}({h}h)" for m, h in traj.dominant_modes[:4])
        lines.append(f"Dominant modes: {modes_str}")

    if traj.dominant_projects:
        projects_str = ", ".join(f"{p}({h}h)" for p, h in traj.dominant_projects[:4])
        lines.append(f"Dominant projects: {projects_str}")

    if traj.dominant_topics:
        topics_str = ", ".join(f"{t}({h}h)" for t, h in traj.dominant_topics[:4])
        lines.append(f"Dominant topics: {topics_str}")

    if traj.highlights:
        lines.append(f"Highlights: {'; '.join(traj.highlights[:5])}")

    if traj.chat_session_count:
        we_str = ", ".join(f"{k}×{v}" for k, v in sorted(traj.chat_work_events.items(), key=lambda x: -x[1])[:5])
        lines.append(f"Chat sessions: {traj.chat_session_count} (cost ${traj.chat_cost_usd:.2f})")
        if we_str:
            lines.append(f"Work event breakdown: {we_str}")

    if traj.episode_count:
        labels_str = ", ".join(str(l) for l in traj.episode_labels[:5])
        lines.append(f"Episodes ({traj.episode_count}): {labels_str}")

    return "\n".join(lines)


def build_day_prompt(day) -> str:
    """Build a narrative prompt for a single TrajectoryDay."""
    topics = ", ".join(f"{t}({s / 3600:.1f}h)" for t, s in day.top_topics[:4])
    modes = ", ".join(f"{m}({s / 3600:.1f}h)" for m, s in day.top_modes[:4])
    projects = ", ".join(f"{p}({s / 3600:.1f}h)" for p, s in day.top_projects[:4])
    highlights = "; ".join(day.highlights[:5]) or "none"
    anomalies_str = ", ".join(day.anomalies) if day.anomalies else "none"
    lines = [
        f"Generate a retrospective for: {day.date.isoformat()}",
        "",
        f"Active: {day.active_seconds / 3600:.1f}h | Recovery: {day.recovery_seconds / 3600:.1f}h",
        f"Chains: {day.chain_count} | Signals: {day.signal_count} | Commands: {day.command_count} | Commits: {day.commit_count}",
        f"Mode: {day.dominant_mode or 'n/a'} — Modes: {modes or 'n/a'}",
        f"Project: {day.dominant_project or 'n/a'} — Projects: {projects or 'n/a'}",
        f"Topic: {day.dominant_topic or 'n/a'} — Topics: {topics or 'n/a'}",
        f"Highlights: {highlights}",
        f"Anomalies: {anomalies_str}",
    ]
    return "\n".join(lines)


def build_week_prompt(week, days=None) -> str:
    """Build a narrative prompt for a TrajectoryWeek."""
    modes = ", ".join(f"{m}({s / 3600:.1f}h)" for m, s in week.top_modes[:4])
    projects = ", ".join(f"{p}({s / 3600:.1f}h)" for p, s in week.top_projects[:4])
    topics = ", ".join(f"{t}({s / 3600:.1f}h)" for t, s in week.top_topics[:4])
    lines = [
        f"Generate a retrospective for: {week.iso_week}",
        "",
        f"Period: {week.start_date} – {week.end_date} ({week.days} days)",
        f"Active: {week.active_seconds / 3600:.1f}h | Recovery: {week.recovery_seconds / 3600:.1f}h",
        f"Chains: {week.chain_count} | Signals: {week.signal_count} | Commands: {week.command_count} | Commits: {week.commit_count}",
        f"Day pattern: {week.day_pattern}",
        f"Modes: {modes or 'n/a'}",
        f"Projects: {projects or 'n/a'}",
        f"Topics: {topics or 'n/a'}",
    ]
    if week.active_delta_vs_prior is not None:
        delta_h = week.active_delta_vs_prior / 3600
        lines.append(f"Activity delta vs prior week: {delta_h:+.1f}h")
    if days:
        lines.append("")
        lines.append("Day-by-day:")
        for d in days:
            lines.append(
                f"  {d.date}: {d.active_seconds / 3600:.1f}h active, "
                f"mode={d.dominant_mode or 'n/a'}, project={d.dominant_project or 'n/a'}, "
                f"topic={d.dominant_topic or 'n/a'}"
            )
    return "\n".join(lines)


def build_episode_prompt(episode, days=None) -> str:
    """Build a narrative prompt for a TrajectoryEpisode."""
    lines = [
        f"Generate a retrospective for episode: {episode.label}",
        "",
        f"Period: {episode.start_date} – {episode.end_date} ({episode.days} days)",
        f"Active: {episode.active_seconds / 3600:.1f}h",
        f"Trigger: {episode.trigger} | Confidence: {episode.confidence:.2f}",
        f"Mode: {episode.dominant_mode or 'n/a'} | Project: {episode.dominant_project or 'n/a'} | Topic: {episode.dominant_topic or 'n/a'}",
    ]
    if episode.mode_distribution:
        mode_str = ", ".join(f"{k}({v / 3600:.1f}h)" for k, v in sorted(episode.mode_distribution.items(), key=lambda x: -x[1])[:4])
        lines.append(f"Mode distribution: {mode_str}")
    if episode.project_distribution:
        proj_str = ", ".join(f"{k}({v / 3600:.1f}h)" for k, v in sorted(episode.project_distribution.items(), key=lambda x: -x[1])[:4])
        lines.append(f"Project distribution: {proj_str}")
    if days:
        lines.append("")
        lines.append("Day-by-day:")
        for d in days:
            lines.append(
                f"  {d.date}: {d.active_seconds / 3600:.1f}h, "
                f"mode={d.dominant_mode or 'n/a'}, project={d.dominant_project or 'n/a'}"
            )
    return "\n".join(lines)


def build_quarter_prompt(quarter) -> str:
    """Build a narrative prompt for a TrajectoryQuarter."""
    modes = ", ".join(f"{m}({s / 3600:.1f}h)" for m, s in quarter.top_modes[:4])
    projects = ", ".join(f"{p}({s / 3600:.1f}h)" for p, s in quarter.top_projects[:4])
    topics = ", ".join(f"{t}({s / 3600:.1f}h)" for t, s in quarter.top_topics[:4])
    trend = ", ".join(f"{s / 3600:.0f}h" for s in quarter.month_active_trend)
    lines = [
        f"Generate a retrospective for: {quarter.quarter}",
        "",
        f"Period: {quarter.start_date} – {quarter.end_date} ({quarter.total_days} days, {quarter.active_days} active)",
        f"Active: {quarter.active_seconds / 3600:.1f}h | Recovery: {quarter.recovery_seconds / 3600:.1f}h",
        f"Chains: {quarter.chain_count} | Signals: {quarter.signal_count} | Commands: {quarter.command_count} | Commits: {quarter.commit_count}",
        f"Chat sessions: {quarter.chat_session_count} (cost ${quarter.chat_cost_usd:.2f})",
        f"Episodes: {quarter.episode_count}",
        f"Modes: {modes or 'n/a'}",
        f"Projects: {projects or 'n/a'}",
        f"Topics: {topics or 'n/a'}",
        f"Monthly active trend: {trend}",
    ]
    return "\n".join(lines)


def build_contrast_prompt(current, prior, scale: str) -> str:
    """Build a prompt contrasting current vs prior period at a given scale."""
    def _fmt(period):
        key = getattr(period, "iso_week", None) or getattr(period, "month", None) or getattr(period, "quarter", None) or "unknown"
        modes = ", ".join(f"{m}({s / 3600:.1f}h)" for m, s in period.top_modes[:3])
        projects = ", ".join(f"{p}({s / 3600:.1f}h)" for p, s in period.top_projects[:3])
        return (
            f"{key}: {period.active_seconds / 3600:.1f}h active, "
            f"modes=[{modes}], projects=[{projects}]"
        )

    lines = [
        f"Generate a contrast narrative between two {scale} periods:",
        "",
        f"Prior: {_fmt(prior)}",
        f"Current: {_fmt(current)}",
        "",
        "Describe what changed: mode shifts, project changes, intensity differences.",
        "Be specific about the delta and what it might indicate.",
    ]
    return "\n".join(lines)


async def generate_batch(
    prompts: list[tuple[str, NarrativeKind, str]],
    *,
    max_concurrent: int = 3,
) -> list[Narrative]:
    """Generate multiple narratives concurrently with semaphore control.

    Each entry in prompts is (prompt_text, kind, key).
    """
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _run(prompt: str, kind: NarrativeKind, key: str) -> Narrative:
        async with semaphore:
            return await generate_narrative(prompt, kind, key)

    tasks = [_run(prompt, kind, key) for prompt, kind, key in prompts]
    return list(await asyncio.gather(*tasks))


async def generate_narrative(
    prompt: str,
    kind: NarrativeKind,
    key: str,
) -> Narrative:
    """Generate a narrative using claude_agent_sdk (Claude Max subscription).

    Logs token usage and cost to artefacts/knowledge/sessions/logs/narrative_{date}.jsonl.
    """
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    text = ""
    model = "unknown"
    input_tokens = 0
    output_tokens = 0
    cost_usd = 0.0

    options = ClaudeAgentOptions(
        system_prompt=NARRATIVE_SYSTEM_PROMPT,
        allowed_tools=[],
        env={"ANTHROPIC_API_KEY": ""},  # force subscription auth, never API billing
    )

    async for message in query(prompt=prompt, options=options):
        if isinstance(message, ResultMessage):
            text = message.result or ""
            if message.total_cost_usd is not None:
                cost_usd = float(message.total_cost_usd)
            if message.usage:
                # input_tokens = new non-cached input; cache_creation covers system context
                fresh = message.usage.get("input_tokens", 0)
                cached_create = message.usage.get("cache_creation_input_tokens", 0)
                cached_read = message.usage.get("cache_read_input_tokens", 0)
                input_tokens = fresh + cached_create + cached_read
                output_tokens = message.usage.get("output_tokens", 0)

    narrative = Narrative(
        kind=kind.value,
        key=key,
        text=text.strip(),
        generated_at=generated_at,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
    )
    _log_narrative(narrative)
    return narrative


def _log_narrative(narrative: Narrative) -> None:
    log_dir = _NARRATIVE_LOG_DIR
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        date_str = narrative.generated_at[:10]
        log_path = log_dir / f"narrative_{date_str}.jsonl"
        entry = {
            "kind": narrative.kind,
            "key": narrative.key,
            "generated_at": narrative.generated_at,
            "model": narrative.model,
            "input_tokens": narrative.input_tokens,
            "output_tokens": narrative.output_tokens,
            "cost_usd": narrative.cost_usd,
            "text": narrative.text,
        }
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("Failed to write narrative log: %s", exc)
