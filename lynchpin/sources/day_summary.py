"""Day summary: the LLM narrative generator's entry point.

Produces a structured, two-track summary of a day:
- Human focus track: what had window focus (from activity_segments)
- AI activity track: what Claude/Codex were doing (from polylogue work events)
- Overlap analysis: what the human was doing while AI produced output
- Metrics: commits, messages, shell commands, sleep

This is what the narrative LLM should consume instead of raw focus spans.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

from ..core.primitives import date_to_dt_range

__all__ = [
    "DaySummary",
    "HumanSegment",
    "AIBlock",
    "OverlapInsight",
    "day_summary",
    "render_day_summary",
]


@dataclass(frozen=True)
class HumanSegment:
    start: datetime
    end: datetime
    duration_min: float
    context: str
    projects: tuple[str, ...]


@dataclass(frozen=True)
class AIBlock:
    start: datetime
    end: datetime
    duration_min: float
    kinds: tuple[str, ...]
    file_count: int
    commit_count: int


@dataclass(frozen=True)
class OverlapInsight:
    """What the human was doing while AI produced output."""
    ai_start: datetime
    ai_end: datetime
    ai_kinds: tuple[str, ...]
    ai_commits: int
    human_contexts: tuple[str, ...]


@dataclass(frozen=True)
class DaySummary:
    date: date
    # Human focus track
    human_segments: tuple[HumanSegment, ...]
    # AI activity track
    ai_blocks: tuple[AIBlock, ...]
    # Overlap
    overlaps: tuple[OverlapInsight, ...]
    # Metrics
    active_hours: float
    commit_count: int
    commit_repos: tuple[str, ...]
    lines_added: int
    lines_deleted: int
    ai_session_count: int
    ai_message_count: int
    shell_commands: int
    shell_error_rate: float
    # Sleep (previous night)
    sleep_hours: Optional[float]
    sleep_score: Optional[float]
    sleep_stages: Optional[dict[str, float]]
    # Health/recovery signals
    stress_avg: Optional[float]
    heart_rate_avg: Optional[float]
    hrv_rmssd: Optional[float]
    spo2_avg: Optional[float]
    respiratory_avg: Optional[float]
    calories: Optional[float]
    nap_minutes: float


def day_summary(d: date) -> Optional[DaySummary]:
    """Build a complete day summary from all available sources."""
    from .activity_segments import segment_day
    from .polylogue import work_events, day_session_summaries
    from .git import commit_facts, daily_activity
    from .terminal import shell_sessions
    from .sleep_infer import infer_sleep
    from .activitywatch import active_seconds_by_date
    from .health import daily_health_summary, nap_sessions

    def strip(dt: datetime) -> datetime:
        return dt.replace(tzinfo=None) if dt.tzinfo else dt

    # ── Human focus track ──
    seg = segment_day(d)
    human_segments: tuple[HumanSegment, ...] = ()
    if seg:
        human_segments = tuple(
            HumanSegment(
                start=s.start, end=s.end, duration_min=s.duration_min,
                context=s.context, projects=s.projects,
            )
            for s in seg.segments
        )

    # ── AI activity track ──
    poly_events = work_events(start=d, end=d)
    git_facts_list = list(commit_facts(start=d, end=d))

    # Group consecutive work events into blocks (max 10min gap)
    raw_blocks: list[dict] = []
    for ev in sorted(poly_events, key=lambda e: strip(e.start) if e.start else datetime.min):
        if ev.start is None:
            continue
        ev_s = strip(ev.start)
        ev_e = strip(ev.end) if ev.end else ev_s + timedelta(minutes=5)
        if raw_blocks and (ev_s - raw_blocks[-1]['end']).total_seconds() < 600:
            raw_blocks[-1]['end'] = max(raw_blocks[-1]['end'], ev_e)
            raw_blocks[-1]['kinds'].add(ev.kind)
            raw_blocks[-1]['files'] += len(ev.file_paths)
        else:
            raw_blocks.append({
                'start': ev_s, 'end': ev_e,
                'kinds': {ev.kind}, 'files': len(ev.file_paths),
            })

    ai_blocks_list: list[AIBlock] = []
    for block in raw_blocks:
        commits = sum(1 for f in git_facts_list
                      if strip(f.authored_at) >= block['start'] and strip(f.authored_at) < block['end'])
        dur = (block['end'] - block['start']).total_seconds() / 60
        ai_blocks_list.append(AIBlock(
            start=block['start'], end=block['end'],
            duration_min=round(dur, 1),
            kinds=tuple(sorted(block['kinds'])),
            file_count=block['files'],
            commit_count=commits,
        ))

    # ── Overlap analysis ──
    overlaps_list: list[OverlapInsight] = []
    for block in ai_blocks_list:
        if block.commit_count == 0:
            continue
        human_contexts = []
        if seg:
            for s in seg.segments:
                if strip(s.end) > strip(block.start) and strip(s.start) < strip(block.end):
                    if s.context not in human_contexts:
                        human_contexts.append(s.context)
        overlaps_list.append(OverlapInsight(
            ai_start=block.start, ai_end=block.end,
            ai_kinds=block.kinds, ai_commits=block.commit_count,
            human_contexts=tuple(human_contexts),
        ))

    # ── Git metrics ──
    git_daily_list = daily_activity(start=d, end=d)
    total_commits = sum(g.commit_count for g in git_daily_list)
    repos = tuple(sorted(set(g.repo.split('/')[-1] for g in git_daily_list)))
    lines_added = sum(g.lines_added for g in git_daily_list)
    lines_deleted = sum(g.lines_deleted for g in git_daily_list)

    # ── AI metrics ──
    poly_days = day_session_summaries(start=d, end=d)
    ai_sessions = poly_days[0].session_count if poly_days else 0
    ai_messages = poly_days[0].total_messages if poly_days else 0

    # ── Terminal ──
    s_dt, e_dt = date_to_dt_range(d, d)
    shells = shell_sessions(start=s_dt, end=e_dt)
    shell_cmds = sum(s.command_count for s in shells)
    shell_errs = sum(s.error_count for s in shells)

    # ── Sleep ──
    sleep_hours = None
    sleep_score = None
    sleep_stages = None
    try:
        inferred = infer_sleep(start=d - timedelta(days=1), end=d)
        if inferred:
            best = max(inferred, key=lambda s: s.bed_duration_min)
            sleep_hours = round(best.bed_duration_min / 60, 1)
            sleep_score = best.sleep_score
            sleep_stages = best.sleep_stages
    except Exception:
        pass

    # ── Health ──
    try:
        health_rows = daily_health_summary(start=d, end=d)
        health = health_rows[0] if health_rows else None
        naps = nap_sessions(start=d, end=d)
        nap_minutes = round(sum(n.duration_min for n in naps), 1)
    except Exception:
        health = None
        nap_minutes = 0.0

    # ── Active hours ──
    active_s = active_seconds_by_date(d, d).get(d, 0)

    return DaySummary(
        date=d,
        human_segments=human_segments,
        ai_blocks=tuple(ai_blocks_list),
        overlaps=tuple(overlaps_list),
        active_hours=round(active_s / 3600, 1),
        commit_count=total_commits,
        commit_repos=repos,
        lines_added=lines_added,
        lines_deleted=lines_deleted,
        ai_session_count=ai_sessions,
        ai_message_count=ai_messages,
        shell_commands=shell_cmds,
        shell_error_rate=round(shell_errs / max(shell_cmds, 1), 3),
        sleep_hours=sleep_hours,
        sleep_score=sleep_score,
        sleep_stages=sleep_stages,
        stress_avg=round(health.stress_avg, 1) if health and health.stress_avg is not None else None,
        heart_rate_avg=round(health.heart_rate_avg, 1) if health and health.heart_rate_avg is not None else None,
        hrv_rmssd=round(health.hrv_rmssd_avg, 2) if health and health.hrv_rmssd_avg is not None else None,
        spo2_avg=round(health.spo2_avg, 1) if health and health.spo2_avg is not None else None,
        respiratory_avg=round(health.respiratory_avg, 1) if health and health.respiratory_avg is not None else None,
        calories=round(health.calories, 1) if health and health.calories is not None else None,
        nap_minutes=nap_minutes,
    )


def render_day_summary(summary: DaySummary) -> str:
    """Render a DaySummary as Markdown optimized for LLM narrative consumption."""
    lines: list[str] = []

    lines.append(f"## {summary.date.strftime('%A, %B %d, %Y')}")
    lines.append("")

    # Overview line
    parts = [f"{summary.active_hours}h active"]
    if summary.commit_count:
        parts.append(f"{summary.commit_count} commits")
    if summary.ai_session_count:
        parts.append(f"{summary.ai_session_count} AI sessions")
    if summary.sleep_hours:
        parts.append(f"{summary.sleep_hours}h sleep")
    lines.append(" | ".join(parts))
    lines.append("")

    # Human focus track
    lines.append("### Human Focus")
    lines.append("")
    for seg in summary.human_segments:
        proj = f" [{', '.join(seg.projects[:2])}]" if seg.projects else ""
        lines.append(f"- {seg.start.strftime('%H:%M')}–{seg.end.strftime('%H:%M')} "
                     f"({seg.duration_min:.0f}min) **{seg.context}**{proj}")
    lines.append("")

    # AI activity track
    if summary.ai_blocks:
        lines.append("### AI Activity")
        lines.append("")
        for block in summary.ai_blocks:
            kinds = ', '.join(block.kinds)
            parts = [f"**{kinds}**"]
            if block.file_count:
                parts.append(f"{block.file_count} files")
            if block.commit_count:
                parts.append(f"{block.commit_count} commits")
            lines.append(f"- {block.start.strftime('%H:%M')}–{block.end.strftime('%H:%M')} "
                         f"({block.duration_min:.0f}min) {', '.join(parts)}")
        lines.append("")

    # Overlap
    if summary.overlaps:
        lines.append("### What Happened (AI + Human)")
        lines.append("")
        for ov in summary.overlaps:
            kinds = ', '.join(ov.ai_kinds)
            human = ' + '.join(ov.human_contexts)
            lines.append(f"- {ov.ai_start.strftime('%H:%M')}–{ov.ai_end.strftime('%H:%M')}: "
                         f"AI **{kinds}** ({ov.ai_commits} commits) "
                         f"while human **{human}**")
        lines.append("")

    # Metrics
    lines.append("### Metrics")
    lines.append("")
    if summary.commit_count:
        lines.append(f"- Commits: {summary.commit_count} across {', '.join(summary.commit_repos)} "
                     f"(+{summary.lines_added:,} / -{summary.lines_deleted:,})")
    if summary.ai_message_count:
        lines.append(f"- AI: {summary.ai_session_count} sessions, {summary.ai_message_count:,} messages")
    if summary.shell_commands:
        lines.append(f"- Shell: {summary.shell_commands} commands "
                     f"({summary.shell_error_rate*100:.0f}% error rate)")
    recovery = []
    if summary.sleep_score is not None:
        recovery.append(f"sleep score {summary.sleep_score:.0f}")
    if summary.sleep_stages:
        deep = summary.sleep_stages.get("deep")
        rem = summary.sleep_stages.get("rem")
        if deep is not None or rem is not None:
            stage_parts = []
            if deep is not None:
                stage_parts.append(f"deep {deep:.0f}m")
            if rem is not None:
                stage_parts.append(f"REM {rem:.0f}m")
            recovery.append(f"sleep stages {', '.join(stage_parts)}")
    if summary.heart_rate_avg is not None:
        recovery.append(f"HR {summary.heart_rate_avg:.0f} bpm")
    if summary.hrv_rmssd is not None:
        recovery.append(f"HRV RMSSD {summary.hrv_rmssd:.1f}")
    if summary.stress_avg is not None:
        recovery.append(f"stress {summary.stress_avg:.0f}")
    if summary.spo2_avg is not None:
        recovery.append(f"SpO2 {summary.spo2_avg:.0f}%")
    if summary.respiratory_avg is not None:
        recovery.append(f"resp {summary.respiratory_avg:.1f}/min")
    if summary.nap_minutes:
        recovery.append(f"naps {summary.nap_minutes:.0f}m")
    if recovery:
        lines.append(f"- Recovery: {', '.join(recovery)}")

    return "\n".join(lines)
