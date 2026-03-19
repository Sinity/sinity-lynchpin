"""Narrative prompt builders and backend adapters for trajectory retrospectives."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from textwrap import dedent
from typing import TYPE_CHECKING

from ..core.codex_exec import run_codex_exec
from ..trajectory.window import load_date_window

if TYPE_CHECKING:
    from .life_timeline import LifeMonthTrajectorySummary

log = logging.getLogger(__name__)

_NARRATIVE_LOG_DIR = Path("artefacts/retrospective/narratives/logs")
DEFAULT_NARRATIVE_BACKEND = os.environ.get("LYNCHPIN_NARRATIVE_BACKEND", "codex-exec")
DEFAULT_CODEX_MODEL = os.environ.get("LYNCHPIN_NARRATIVE_CODEX_MODEL", "")

NARRATIVE_SYSTEM_PROMPT = """\
You are a concise personal retrospective analyst. You receive structured
summaries of a person's digital activity for a calendar period (trajectory
signals, git commits, chat sessions, episodes) and produce a tight, factual
retrospective.

Rules:
- Use only the information in the prompt.
- Be specific about projects, modes, topics, or episodes when present.
- Prefer concrete contrasts over vague praise.
- Do not speculate beyond the data.
"""

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


class NarrativeKind(str, Enum):
    day = "day"
    week = "week"
    range = "range"
    month = "month"
    episode = "episode"
    quarter = "quarter"
    contrast = "contrast"


class NarrativeBackend(str, Enum):
    claude_agent_sdk = "claude-agent-sdk"
    codex_exec = "codex-exec"


@dataclass(frozen=True)
class Narrative:
    kind: str
    key: str
    text: str
    generated_at: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    backend: str = "unknown"


def _fmt_top(items: tuple[tuple[str, float], ...]) -> str:
    if not items:
        return "n/a"
    return ", ".join(f"{name}: {seconds / 60:.1f}m" for name, seconds in items[:4])


def _day_prompt_block(day) -> str:
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


def build_day_narrative_prompt(day, mode: str = DEFAULT_MODE) -> str:
    profile = MODE_PROFILES.get(mode, MODE_PROFILES[DEFAULT_MODE])
    section_list = "\n".join(f"- {section}" for section in profile["sections"])
    return dedent(f"""
        You are Sinity's retrospective co-author. Write a cohesive narrative for {day.date.isoformat()}.

        Tone guidance: {profile['tone']}
        Extra guidance: {profile['extra_guidance']}

        {_day_prompt_block(day)}

        Output requirements:
        - Markdown with headings (##) for:
        {section_list}
        - Keep paragraphs succinct but vivid.
    """).strip()


def build_week_narrative_prompt(week, days: list | None = None, mode: str = DEFAULT_MODE) -> str:
    profile = MODE_PROFILES.get(mode, MODE_PROFILES[DEFAULT_MODE])
    section_list = "\n".join(f"- {section}" for section in profile["sections"])
    day_blocks = "\n\n".join(_day_prompt_block(day) for day in (days or []))
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


def build_range_prompt(days: list, start: date, end: date, mode: str = DEFAULT_MODE) -> str:
    profile = MODE_PROFILES.get(mode, MODE_PROFILES[DEFAULT_MODE])
    section_list = "\n".join(f"- {section}" for section in profile["sections"])
    day_blocks = "\n\n".join(_day_prompt_block(day) for day in days)

    total_active = sum(day.active_seconds for day in days) / 3600
    total_recovery = sum(day.recovery_seconds for day in days) / 3600
    total_commands = sum(day.command_count for day in days)
    total_commits = sum(day.commit_count for day in days)
    total_transcripts = sum(day.transcript_count for day in days)

    from collections import Counter

    mode_counter: Counter[str] = Counter()
    project_counter: Counter[str] = Counter()
    for day in days:
        for name, seconds in day.top_modes:
            mode_counter[name] += seconds
        for name, seconds in day.top_projects:
            project_counter[name] += seconds
    top_modes = sorted(mode_counter.items(), key=lambda item: -item[1])[:5]
    top_projects = sorted(project_counter.items(), key=lambda item: -item[1])[:5]

    modes_line = ", ".join(f"{name} ({seconds / 60:.1f}m)" for name, seconds in top_modes) or "n/a"
    projects_line = ", ".join(f"{name} ({seconds / 60:.1f}m)" for name, seconds in top_projects) or "n/a"

    return dedent(f"""
        You are Sinity's retrospective co-author. Write a cohesive narrative covering {start.isoformat()} to {end.isoformat()} (inclusive).

        Tone guidance: {profile['tone']}
        Extra guidance: {profile['extra_guidance']}

        Range overview:
        - Total days: {len(days)}
        - Active hours: {total_active:.2f}h (Recovery: {total_recovery:.2f}h)
        - Shell commands: {total_commands:,}
        - Git commits: {total_commits}
        - Transcripts: {total_transcripts}
        - Dominant modes: {modes_line}
        - Dominant projects: {projects_line}

        {day_blocks}

        Output requirements:
        - Markdown with headings (##) for:
        {section_list}
        - Keep paragraphs succinct but vivid; weave in contrasts between days and reference specific dates.
    """).strip()


def build_month_prompt(traj: LifeMonthTrajectorySummary, *, month_key: str) -> str:
    lines = [
        f"Generate a retrospective for: {month_key}",
        "",
        f"Period: {traj.start_date} – {traj.end_date} ({traj.days} days)",
        f"Active: {traj.active_hours}h | Recovery: {traj.recovery_hours}h",
        f"Chains: {traj.chain_count} | Signals: {traj.signal_count} | Commits: {traj.commit_count}",
    ]
    if traj.dominant_modes:
        lines.append(f"Dominant modes: {', '.join(f'{mode}({hours}h)' for mode, hours in traj.dominant_modes[:4])}")
    if traj.dominant_projects:
        lines.append(f"Dominant projects: {', '.join(f'{project}({hours}h)' for project, hours in traj.dominant_projects[:4])}")
    if traj.dominant_topics:
        lines.append(f"Dominant topics: {', '.join(f'{topic}({hours}h)' for topic, hours in traj.dominant_topics[:4])}")
    if traj.highlights:
        lines.append(f"Highlights: {'; '.join(traj.highlights[:5])}")
    if traj.chat_session_count:
        work_events = ", ".join(
            f"{name}×{count}"
            for name, count in sorted(traj.chat_work_events.items(), key=lambda item: -item[1])[:5]
        )
        lines.append(f"Chat sessions: {traj.chat_session_count} (cost ${traj.chat_cost_usd:.2f})")
        if work_events:
            lines.append(f"Work event breakdown: {work_events}")
    if traj.episode_count:
        lines.append(f"Episodes ({traj.episode_count}): {', '.join(str(label) for label in traj.episode_labels[:5])}")
    return "\n".join(lines)


def build_day_prompt(day) -> str:
    topics = ", ".join(f"{topic}({seconds / 3600:.1f}h)" for topic, seconds in day.top_topics[:4])
    modes = ", ".join(f"{mode}({seconds / 3600:.1f}h)" for mode, seconds in day.top_modes[:4])
    projects = ", ".join(f"{project}({seconds / 3600:.1f}h)" for project, seconds in day.top_projects[:4])
    highlights = "; ".join(day.highlights[:5]) or "none"
    anomalies_str = ", ".join(day.anomalies) if day.anomalies else "none"
    return "\n".join(
        [
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
    )


def build_week_prompt(week, days=None) -> str:
    modes = ", ".join(f"{mode}({seconds / 3600:.1f}h)" for mode, seconds in week.top_modes[:4])
    projects = ", ".join(f"{project}({seconds / 3600:.1f}h)" for project, seconds in week.top_projects[:4])
    topics = ", ".join(f"{topic}({seconds / 3600:.1f}h)" for topic, seconds in week.top_topics[:4])
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
        lines.append(f"Activity delta vs prior week: {week.active_delta_vs_prior / 3600:+.1f}h")
    if days:
        lines.append("")
        lines.append("Day-by-day:")
        for day in days:
            lines.append(
                f"  {day.date}: {day.active_seconds / 3600:.1f}h active, "
                f"mode={day.dominant_mode or 'n/a'}, project={day.dominant_project or 'n/a'}, "
                f"topic={day.dominant_topic or 'n/a'}"
            )
    return "\n".join(lines)


def build_episode_prompt(episode, days=None) -> str:
    lines = [
        f"Generate a retrospective for episode: {episode.label}",
        "",
        f"Period: {episode.start_date} – {episode.end_date} ({episode.days} days)",
        f"Active: {episode.active_seconds / 3600:.1f}h",
        f"Trigger: {episode.trigger} | Confidence: {episode.confidence:.2f}",
        f"Mode: {episode.dominant_mode or 'n/a'} | Project: {episode.dominant_project or 'n/a'} | Topic: {episode.dominant_topic or 'n/a'}",
    ]
    if episode.mode_distribution:
        lines.append(
            "Mode distribution: "
            + ", ".join(
                f"{name}({seconds / 3600:.1f}h)"
                for name, seconds in sorted(episode.mode_distribution.items(), key=lambda item: -item[1])[:4]
            )
        )
    if episode.project_distribution:
        lines.append(
            "Project distribution: "
            + ", ".join(
                f"{name}({seconds / 3600:.1f}h)"
                for name, seconds in sorted(episode.project_distribution.items(), key=lambda item: -item[1])[:4]
            )
        )
    if days:
        lines.append("")
        lines.append("Day-by-day:")
        for day in days:
            lines.append(
                f"  {day.date}: {day.active_seconds / 3600:.1f}h, "
                f"mode={day.dominant_mode or 'n/a'}, project={day.dominant_project or 'n/a'}"
            )
    return "\n".join(lines)


def build_quarter_prompt(quarter) -> str:
    modes = ", ".join(f"{mode}({seconds / 3600:.1f}h)" for mode, seconds in quarter.top_modes[:4])
    projects = ", ".join(f"{project}({seconds / 3600:.1f}h)" for project, seconds in quarter.top_projects[:4])
    topics = ", ".join(f"{topic}({seconds / 3600:.1f}h)" for topic, seconds in quarter.top_topics[:4])
    trend = ", ".join(f"{seconds / 3600:.0f}h" for seconds in quarter.month_active_trend)
    return "\n".join(
        [
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
    )


def build_contrast_prompt(current, prior, scale: str) -> str:
    def _fmt(period):
        key = (
            getattr(period, "iso_week", None)
            or getattr(period, "month", None)
            or getattr(period, "quarter", None)
            or getattr(period, "year", None)
            or "unknown"
        )
        modes = ", ".join(f"{mode}({seconds / 3600:.1f}h)" for mode, seconds in period.top_modes[:3])
        projects = ", ".join(f"{project}({seconds / 3600:.1f}h)" for project, seconds in period.top_projects[:3])
        return f"{key}: {period.active_seconds / 3600:.1f}h active, modes=[{modes}], projects=[{projects}]"

    return "\n".join(
        [
            f"Generate a contrast narrative between two {scale} periods:",
            "",
            f"Prior: {_fmt(prior)}",
            f"Current: {_fmt(current)}",
            "",
            "Describe what changed: mode shifts, project changes, intensity differences.",
            "Be specific about the delta and what it might indicate.",
        ]
    )


def build_scale_prompts(
    keys: list[str],
    *,
    scale: str | NarrativeKind,
) -> list[tuple[str, NarrativeKind, str]]:
    resolved_scale = NarrativeKind(scale)

    if resolved_scale is NarrativeKind.month:
        from .life_timeline import build_recent_trajectory_summaries

        trajectory_months, _ = build_recent_trajectory_summaries(
            keys,
            lookback_days=365 * 10,
        )
        prompts = []
        for key in sorted(keys):
            traj = trajectory_months.get(key)
            if traj is not None:
                prompts.append((build_month_prompt(traj, month_key=key), resolved_scale, key))
        return prompts

    if resolved_scale is NarrativeKind.quarter:
        from ..trajectory import summarize_quarters, summarize_trajectory_months
        from ..trajectory.day import summarize_days

        days = summarize_days()
        months = summarize_trajectory_months(days)
        quarters = summarize_quarters(months)
        quarter_by_key = {quarter.quarter: quarter for quarter in quarters}
        return [
            (build_quarter_prompt(quarter_by_key[key]), resolved_scale, key)
            for key in sorted(keys)
            if key in quarter_by_key
        ]

    if resolved_scale is NarrativeKind.week:
        from ..trajectory.day import summarize_days
        from ..trajectory.week import summarize_weeks

        days = summarize_days()
        weeks = summarize_weeks(days)
        week_by_key = {week.iso_week: week for week in weeks}
        day_by_week: dict[str, list] = {}
        for day in days:
            iso = day.date.isocalendar()
            key = f"{iso[0]}-W{iso[1]:02d}"
            day_by_week.setdefault(key, []).append(day)
        return [
            (build_week_prompt(week_by_key[key], days=day_by_week.get(key, [])), resolved_scale, key)
            for key in sorted(keys)
            if key in week_by_key
        ]

    if resolved_scale is NarrativeKind.day:
        from ..trajectory.day import summarize_days

        day_by_key = {str(day.date): day for day in summarize_days()}
        return [
            (build_day_prompt(day_by_key[key]), resolved_scale, key)
            for key in sorted(keys)
            if key in day_by_key
        ]

    if resolved_scale is NarrativeKind.episode:
        from ..trajectory.day import summarize_days
        from ..trajectory.episode import detect_episodes

        days = summarize_days()
        episodes = detect_episodes(days)
        episode_by_key = {episode.episode_id: episode for episode in episodes}
        day_by_episode = {
            episode.episode_id: [day for day in days if episode.start_date <= day.date <= episode.end_date]
            for episode in episodes
        }
        return [
            (build_episode_prompt(episode_by_key[key], days=day_by_episode.get(key, [])), resolved_scale, key)
            for key in sorted(keys)
            if key in episode_by_key
        ]

    raise ValueError(
        f"Unsupported narrative scale {scale!r}. Choose from: day, week, episode, quarter, month."
    )


async def generate_batch(
    prompts: list[tuple[str, NarrativeKind, str]],
    *,
    max_concurrent: int = 3,
    backend: str | NarrativeBackend | None = None,
    model: str | None = None,
) -> list[Narrative]:
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _run(prompt: str, kind: NarrativeKind, key: str) -> Narrative:
        async with semaphore:
            return await generate_narrative(prompt, kind, key, backend=backend, model=model)

    return list(await asyncio.gather(*[_run(prompt, kind, key) for prompt, kind, key in prompts]))


async def generate_scale_narratives(
    keys: list[str],
    *,
    scale: str | NarrativeKind,
    batch: bool = False,
    backend: str | NarrativeBackend | None = None,
    model: str | None = None,
) -> list[Narrative]:
    prompts = build_scale_prompts(keys, scale=scale)
    if not prompts:
        return []
    if batch and len(prompts) > 1:
        return await generate_batch(prompts, backend=backend, model=model)
    results: list[Narrative] = []
    for prompt_text, kind, key in prompts:
        results.append(await generate_narrative(prompt_text, kind, key, backend=backend, model=model))
    return results


async def generate_narrative(
    prompt: str,
    kind: NarrativeKind,
    key: str,
    *,
    backend: str | NarrativeBackend | None = None,
    model: str | None = None,
) -> Narrative:
    backend_kind = _resolve_backend(backend)
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    if backend_kind is NarrativeBackend.claude_agent_sdk:
        resolved_model, text, input_tokens, output_tokens, cost_usd = await _generate_via_claude_agent_sdk(prompt)
    else:
        resolved_model, text, input_tokens, output_tokens, cost_usd = await asyncio.to_thread(
            _generate_via_codex_exec,
            prompt,
            model or (DEFAULT_CODEX_MODEL or None),
        )

    narrative = Narrative(
        kind=kind.value,
        key=key,
        text=text.strip(),
        generated_at=generated_at,
        model=resolved_model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        backend=backend_kind.value,
    )
    _log_narrative(narrative)
    return narrative


async def generate_date_range_narrative(
    start: date,
    end: date,
    *,
    mode: str = DEFAULT_MODE,
    backend: str | NarrativeBackend | None = None,
    model: str | None = None,
    key: str | None = None,
) -> Narrative:
    if end < start:
        raise ValueError("end must be on or after start")
    days = list(load_date_window(start, end).days)
    prompt = build_range_prompt(days, start, end, mode)
    range_key = key or f"{start.isoformat()}_to_{end.isoformat()}_{mode}"
    return await generate_narrative(
        prompt,
        NarrativeKind.range,
        range_key,
        backend=backend,
        model=model,
    )


def _resolve_backend(backend: str | NarrativeBackend | None) -> NarrativeBackend:
    if isinstance(backend, NarrativeBackend):
        return backend
    value = (backend or DEFAULT_NARRATIVE_BACKEND).strip().lower()
    for candidate in NarrativeBackend:
        if candidate.value == value:
            return candidate
    raise ValueError(
        "Unknown narrative backend "
        f"{backend!r}. Choose from: {', '.join(option.value for option in NarrativeBackend)}"
    )


async def _generate_via_claude_agent_sdk(prompt: str) -> tuple[str, str, int, int, float]:
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

    text = ""
    input_tokens = 0
    output_tokens = 0
    cost_usd = 0.0

    options = ClaudeAgentOptions(
        system_prompt=NARRATIVE_SYSTEM_PROMPT,
        allowed_tools=[],
        env={"ANTHROPIC_API_KEY": ""},
    )
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, ResultMessage):
            text = message.result or ""
            if message.total_cost_usd is not None:
                cost_usd = float(message.total_cost_usd)
            if message.usage:
                fresh = message.usage.get("input_tokens", 0)
                cached_create = message.usage.get("cache_creation_input_tokens", 0)
                cached_read = message.usage.get("cache_read_input_tokens", 0)
                input_tokens = fresh + cached_create + cached_read
                output_tokens = message.usage.get("output_tokens", 0)
    return "claude-agent-sdk", text, input_tokens, output_tokens, cost_usd


def _generate_via_codex_exec(prompt: str, model: str | None) -> tuple[str, str, int, int, float]:
    result = run_codex_exec(
        prompt,
        model=model,
        system_prompt=NARRATIVE_SYSTEM_PROMPT,
    )
    return result.model, result.text, 0, 0, 0.0


def _log_narrative(narrative: Narrative) -> None:
    log_dir = _NARRATIVE_LOG_DIR
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"narrative_{narrative.generated_at[:10]}.jsonl"
        entry = {
            "kind": narrative.kind,
            "key": narrative.key,
            "generated_at": narrative.generated_at,
            "backend": narrative.backend,
            "model": narrative.model,
            "input_tokens": narrative.input_tokens,
            "output_tokens": narrative.output_tokens,
            "cost_usd": narrative.cost_usd,
            "text": narrative.text,
        }
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("Failed to write narrative log: %s", exc)
