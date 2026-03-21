"""Narrative prompt builders and backend adapters for trajectory retrospectives."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
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

ANALYTICAL_SYSTEM_PROMPT = """\
You are Sinity's analytical retrospective agent. You receive pre-aggregated
trajectory data as a rich starting context and have tools to deepen the
analysis with targeted investigations.

## Your approach
The prompt already contains the core trajectory data (active hours, modes,
projects, topics, per-day breakdowns). Do NOT re-query this baseline data —
it's already in front of you. Instead, use your tools to:
1. Check git commit messages for projects that show significant activity —
   what was actually shipped/changed? Use `git -C /realm/project/<name> log
   --oneline --after=YYYY-MM-DD --before=YYYY-MM-DD` for relevant repos.
2. Investigate anomalies: unusual mode switches, activity spikes/dips,
   high recovery following intense days.
3. Cross-reference project time with commit density — high time + few commits
   suggests deep work or debugging; low time + many commits suggests batch/AI work.
4. Check the DuckDB warehouse for targeted queries only when the pre-aggregated
   data raises questions you can't answer from context.

## Data sources (for targeted follow-ups only)

DuckDB warehouse: `duckdb artefacts/lynchpin/warehouse.duckdb -c "..."`
- trajectory_day_project: per-project duration breakdown per day
- trajectory_day_topic: per-topic breakdown per day
- trajectory_episode: detected multi-day episodes
- trajectory_signal: individual activity signals (timestamp, plane, duration)
- webhistory_entries: browser history

SQLite caches (under artefacts/lynchpin/cache/):
- chatlog_transcripts.sqlite, session_records.sqlite, samsung_sleep_sessions.sqlite

Git repos: /realm/project/{sinex,sinnix,polylogue,sinity-lynchpin,knowledgebase,...}

## Analysis heuristics
- Cross-reference trajectory project dominance with actual git commits
- If recovery dominates, check what preceded it
- Chat session counts often correlate with work complexity
- AFK data in ActivityWatch only reflects last focus event — don't assume multitasking

## Output
Write a retrospective narrative in Markdown. Be specific — cite actual commits,
project names, concrete time ranges. Prefer observations grounded in evidence
over vague summaries. Structure with ## headings appropriate to the scale.
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
        enriched_prompt = _enrich_prompt_for_sdk(prompt, kind, key)
        resolved_model, text, input_tokens, output_tokens, cost_usd = await _generate_via_claude_agent_sdk(enriched_prompt, model)
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


_GIT_REPOS = [
    "sinex", "sinnix", "polylogue", "sinity-lynchpin", "knowledgebase",
    "scribe-tap", "intercept-bounce", "knowledge-extract",
]


def _query_git_commits(start: date, end: date) -> str:
    """Pre-query git commit logs for all active repos in a date range."""
    after = (start - timedelta(days=1)).isoformat()
    before = (end + timedelta(days=1)).isoformat()
    sections: list[str] = []
    for repo in _GIT_REPOS:
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


def _query_duckdb_context(start: date, end: date) -> str:
    """Pre-query DuckDB for per-project breakdowns and episodes."""
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


def _parse_date_range(scale: NarrativeKind, key: str) -> tuple[date | None, date | None]:
    """Extract start/end dates from a scale + key."""
    try:
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


def _coalesce_aw_spans(start: date, end: date, min_seconds: float = 10) -> str:
    """Coalesce raw ActivityWatch window events into meaningful focus spans.

    Merges consecutive same-app-title point events into spans with computed
    duration. Filters to spans > min_seconds. Returns formatted text.
    """
    from ..sources.captures.activitywatch import window_events

    dt_start = datetime(start.year, start.month, start.day)
    dt_end = datetime(end.year, end.month, end.day) + timedelta(days=1)

    try:
        events = list(window_events(start=dt_start, end=dt_end))
    except Exception:
        return ""

    if not events:
        return ""

    # Build spans from consecutive same-app-title events
    spans: list[dict] = []
    for e in events:
        app = e.data.get("app", "")
        title = e.data.get("title", "")[:120]
        if spans and spans[-1]["app"] == app and spans[-1]["title"] == title:
            spans[-1]["end"] = e.start
        else:
            if spans:
                spans[-1]["end"] = e.start
            spans.append({"app": app, "title": title, "start": e.start, "end": e.start})

    # Filter to meaningful spans
    meaningful = [
        s for s in spans
        if (s["end"] - s["start"]).total_seconds() >= min_seconds
    ]
    if not meaningful:
        return ""

    # Format as timeline
    lines = [f"### Activity spans ({len(meaningful)} spans from {len(events)} raw events)"]
    for s in meaningful:
        dur_m = (s["end"] - s["start"]).total_seconds() / 60
        lines.append(
            f"{s['start'].strftime('%H:%M')}–{s['end'].strftime('%H:%M')} "
            f"({dur_m:.0f}m) {s['app']} | {s['title']}"
        )
    return "\n".join(lines)


def _query_atuin_commands(start: date, end: date) -> str:
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


def _query_git_detailed(start: date, end: date) -> str:
    """Query git commits with full messages and stat summaries."""
    after = (start - timedelta(days=1)).isoformat()
    before = (end + timedelta(days=1)).isoformat()
    sections: list[str] = []
    for repo in _GIT_REPOS:
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


def _query_sleep_data(start: date, end: date) -> str:
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


def _enrich_prompt_for_sdk(base_prompt: str, scale: NarrativeKind, key: str) -> str:
    """Enrich a prompt with dense source data for the Agent SDK backend.

    Layers data from most processed (orientation) to most raw (evidence):
    1. Base prompt (trajectory summary — orientation)
    2. Per-project time + episode context (structured processed)
    3. Coalesced ActivityWatch spans (what was on screen)
    4. Atuin shell commands (what was typed)
    5. Git commits with messages + stats (what changed)
    6. Sleep data (physiological context)
    """
    start, end = _parse_date_range(scale, key)
    if start is None or end is None:
        return base_prompt

    enrichment_parts: list[str] = []

    # Structured processed data (DuckDB warehouse)
    duckdb_context = _query_duckdb_context(start, end)
    if duckdb_context:
        enrichment_parts.append(f"## Warehouse context\n\n{duckdb_context}")

    # ActivityWatch focus spans (coalesced from raw events)
    aw_spans = _coalesce_aw_spans(start, end)
    if aw_spans:
        enrichment_parts.append(f"## Desktop activity\n\n{aw_spans}")

    # Shell commands
    atuin_cmds = _query_atuin_commands(start, end)
    if atuin_cmds:
        enrichment_parts.append(f"## Shell history\n\n{atuin_cmds}")

    # Git commits with full messages and stats
    git_detailed = _query_git_detailed(start, end)
    if git_detailed:
        enrichment_parts.append(f"## Git commits ({start} to {end})\n\n{git_detailed}")

    # Sleep data
    sleep = _query_sleep_data(start, end)
    if sleep:
        enrichment_parts.append(f"## Health\n\n{sleep}")

    # Temporal context: neighbor days + higher-scale narratives
    if scale is NarrativeKind.day and start is not None:
        from .synthesis import load_narratives

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


async def _generate_via_claude_agent_sdk(
    prompt: str, model: str | None = None,
) -> tuple[str, str, int, int, float]:
    from ..core.claude_sdk import run_claude_sdk

    result = await run_claude_sdk(
        prompt,
        system_prompt=ANALYTICAL_SYSTEM_PROMPT,
        model=model,
        allowed_tools=["Read", "Bash", "Glob", "Grep"],
    )
    return result.model, result.text, result.input_tokens, result.output_tokens, result.cost_usd


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
