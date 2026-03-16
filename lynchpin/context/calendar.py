from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

from ..metrics.analysis import CodeHealthMetrics, code_health_summary as _code_health_summary
from ..metrics.git import GitMetrics as GitSummary, git_summary as _git_summary
from ..metrics.health import SleepMetrics as SleepSummary, sleep_summary as _sleep_summary
from ..metrics.productivity import commands_by_category as _commands_by_category
from ..sources.captures import instrumentation, webhistory
from ..sources.exports import chatlog, sleep
from ..sources.exports.chatlog import ChatTranscript
from ..sources.indices import sessions
from ..sources.indices.analysis import iter_crate_metrics as _iter_crate_metrics, latest_snapshot as _latest_snapshot
from ..sources.indices.coding_sessions import CodingSession, iter_coding_sessions as _iter_coding_sessions
from ..sources.indices.sessions import SessionRecord
from ..trajectory import chains as trajectory_chains, day as trajectory_day, signal as trajectory_signal


@dataclass
class Overview:
    active_hours: float
    afk_hours: float
    window_hours: float


@dataclass
class FocusSummary:
    total_focus_minutes: float
    categories: Dict[str, float]


@dataclass
class DaySummary:
    date: str
    overview: Overview
    command_total: int
    command_categories: Dict[str, int]
    codex_sessions: int
    atuin_commands: int
    git: GitSummary
    sessions: List[Dict[str, str]]
    transcripts: List[Dict[str, object]]
    sleep: Optional[SleepSummary]
    focus: FocusSummary
    insights: List[str] = field(default_factory=list)
    instrumentation: Dict[str, Any] = field(default_factory=dict)
    top_apps: List[Tuple[str, float]] = field(default_factory=list)
    top_web_domains: List[Tuple[str, int]] = field(default_factory=list)
    code_health: Optional[CodeHealthMetrics] = None
    coding_sessions: List[CodingSession] = field(default_factory=list)
    window_event_count: int = 0
    afk_event_count: int = 0
    dominant_mode: Optional[str] = None
    dominant_project: Optional[str] = None
    top_modes: List[Tuple[str, float]] = field(default_factory=list)
    top_projects: List[Tuple[str, float]] = field(default_factory=list)
    signal_count: int = 0
    chain_count: int = 0
    source_counts: Dict[str, int] = field(default_factory=dict)
    coverage: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass
class NarrativeRangeSummary:
    total_days: int
    total_focus_hours: float
    total_afk_hours: float
    total_commands: int
    total_commits: int
    total_codex_sessions: int
    total_session_records: int
    total_terminal_sessions: int
    total_terminal_events: int
    total_terminal_commands: int
    total_terminal_active_hours: float
    total_terminal_failures: int
    total_terminal_new_model_sessions: int
    total_terminal_legacy_sessions: int
    total_focus_minutes: float
    average_sleep_hours: Optional[float]
    total_sleep_segments: int
    code_health: Optional[CodeHealthMetrics]
    coding_session_count: int
    coding_session_hours: float
    top_insights: str
    top_sessions: str
    top_modes: List[Tuple[str, float]] = field(default_factory=list)
    top_projects: List[Tuple[str, float]] = field(default_factory=list)


def load_day_summary(target: date) -> DaySummary:
    return load_day_summaries(target, target)[0]


def load_day_summaries(start: date, end: date) -> List[DaySummary]:
    if end < start:
        raise ValueError("end must be >= start")

    local_tz = _local_tz()
    window_start = datetime.combine(start, time.min, tzinfo=local_tz)
    window_end = datetime.combine(end + timedelta(days=1), time.min, tzinfo=local_tz)
    span_days = (end - start).days + 1

    signals = trajectory_signal.load_signals(start=window_start, end=window_end, days=span_days)
    chains = trajectory_chains.build_chains(signals)
    trajectory_days = trajectory_day.summarize_days(
        signals=signals,
        chains=chains,
        start=window_start,
        end=window_end,
        days=span_days,
    )

    signal_by_day: dict[date, list[trajectory_signal.TrajectorySignal]] = defaultdict(list)
    for signal in signals:
        signal_by_day[signal.start.date()].append(signal)

    transcripts_by_day: dict[date, list[ChatTranscript]] = defaultdict(list)
    for transcript in chatlog.iter_transcripts(start=window_start, end=window_end):
        transcripts_by_day[transcript.started_at.date()].append(transcript)

    web_domains_by_day = _top_web_domains_by_day(start, end)

    coding_sessions_by_day: dict[str, list[CodingSession]] = defaultdict(list)
    for session in _iter_coding_sessions():
        if start.isoformat() <= session.start[:10] <= end.isoformat():
            coding_sessions_by_day[session.start[:10]].append(session)

    code_health = _load_code_health()
    summaries: List[DaySummary] = []
    for traj in trajectory_days:
        day_signals = signal_by_day.get(traj.date, [])
        command_signals = [signal for signal in day_signals if signal.source == "atuin.command"]
        window_signals = [signal for signal in day_signals if signal.source == "activitywatch.window"]
        afk_signals = [signal for signal in day_signals if signal.source == "activitywatch.afk"]
        transcripts = [_transcript_to_dict(item) for item in transcripts_by_day.get(traj.date, [])]
        session_records = [_session_to_dict(record) for record in sessions.sessions_by_date(traj.date)]
        sleep_summary = _sleep_summary(sleep.sleep_by_date(traj.date.isoformat()))
        top_apps, focus_categories, focus_minutes_total = _top_apps(window_signals), _focus_categories(traj), _focus_minutes(window_signals)
        instrumentation_summary = _instrumentation_summary(
            list(instrumentation.terminal_sessions_by_date(traj.date)),
            list(instrumentation.terminal_session_events_by_date(traj.date)),
        )
        summary = DaySummary(
            date=traj.date.isoformat(),
            overview=Overview(
                active_hours=round(traj.active_seconds / 3600.0, 2),
                afk_hours=round(traj.recovery_seconds / 3600.0, 2),
                window_hours=round(focus_minutes_total / 60.0, 2),
            ),
            command_total=len(command_signals),
            command_categories=_command_categories(command_signals),
            codex_sessions=sum(
                1 for record in session_records if "codex" in record.get("provider", "").lower()
            ) + sum(1 for transcript in transcripts if transcript.get("provider") == "codex"),
            atuin_commands=len(command_signals),
            git=_git_summary_from_signals(day_signals),
            sessions=session_records,
            transcripts=transcripts,
            sleep=sleep_summary,
            focus=FocusSummary(
                total_focus_minutes=round(focus_minutes_total, 2),
                categories=focus_categories,
            ),
            insights=list(traj.highlights),
            instrumentation=instrumentation_summary,
            top_apps=top_apps,
            top_web_domains=web_domains_by_day.get(traj.date, []),
            code_health=code_health,
            coding_sessions=list(coding_sessions_by_day.get(traj.date.isoformat(), [])),
            window_event_count=len(window_signals),
            afk_event_count=len(afk_signals),
            dominant_mode=traj.dominant_mode,
            dominant_project=traj.dominant_project,
            top_modes=[(mode, round(seconds / 60.0, 1)) for mode, seconds in traj.top_modes],
            top_projects=[(project, round(seconds / 60.0, 1)) for project, seconds in traj.top_projects],
            signal_count=traj.signal_count,
            chain_count=traj.chain_count,
            source_counts=dict(traj.source_counts),
            coverage=dict(traj.coverage),
        )
        summaries.append(summary)
    return summaries


def summarize_range(summaries: Sequence[DaySummary]) -> NarrativeRangeSummary:
    total_days = len(summaries)
    total_focus_hours = sum(summary.overview.active_hours for summary in summaries)
    total_afk_hours = sum(summary.overview.afk_hours for summary in summaries)
    total_commands = sum(summary.command_total for summary in summaries)
    total_commits = sum(summary.git.commits for summary in summaries)
    total_codex_sessions = sum(summary.codex_sessions for summary in summaries)
    total_session_records = sum(len(summary.sessions) for summary in summaries)
    total_terminal_sessions = sum(int(summary.instrumentation.get("terminal_sessions", 0) or 0) for summary in summaries)
    total_terminal_events = sum(int(summary.instrumentation.get("terminal_events", 0) or 0) for summary in summaries)
    total_terminal_commands = sum(int(summary.instrumentation.get("terminal_command_count", 0) or 0) for summary in summaries)
    total_terminal_active_hours = sum(
        float(summary.instrumentation.get("terminal_active_hours", 0.0) or 0.0) for summary in summaries
    )
    total_terminal_failures = sum(
        int(summary.instrumentation.get("terminal_command_failures", 0) or 0)
        + int(summary.instrumentation.get("terminal_session_failures", 0) or 0)
        for summary in summaries
    )
    total_terminal_new_model_sessions = sum(
        int(summary.instrumentation.get("terminal_new_model_sessions", 0) or 0) for summary in summaries
    )
    total_terminal_legacy_sessions = sum(
        int(summary.instrumentation.get("terminal_legacy_sessions", 0) or 0) for summary in summaries
    )
    total_focus_minutes = sum(float(summary.focus.total_focus_minutes) for summary in summaries)

    sleep_hours_samples: List[float] = []
    total_sleep_segments = 0
    for summary in summaries:
        if not summary.sleep:
            continue
        sleep_hours_samples.append(float(summary.sleep.total_hours))
        total_sleep_segments += summary.sleep.segments

    insight_counter: Counter[str] = Counter()
    session_counter: Counter[tuple[str, str]] = Counter()
    mode_counter: Counter[str] = Counter()
    project_counter: Counter[str] = Counter()
    for summary in summaries:
        insight_counter.update(summary.insights)
        session_counter.update(
            (session.get("label") or "Session", session.get("provider") or "")
            for session in summary.sessions
        )
        mode_counter.update({mode: minutes for mode, minutes in summary.top_modes})
        project_counter.update({project: minutes for project, minutes in summary.top_projects})

    top_insights = ", ".join(f"{text}×{count}" for text, count in insight_counter.most_common(6)) or "No notable highlights captured."
    top_sessions = (
        ", ".join(
            f"{label}{' (' + provider + ')' if provider else ''}×{count}"
            for (label, provider), count in session_counter.most_common(5)
        )
        if session_counter
        else "No recorded sessions."
    )

    coding_session_count = sum(len(summary.coding_sessions) for summary in summaries)
    coding_session_hours = round(
        sum(session.duration_hours for summary in summaries for session in summary.coding_sessions), 2
    )

    range_code_health = None
    for summary in reversed(summaries):
        if summary.code_health is not None:
            range_code_health = summary.code_health
            break

    return NarrativeRangeSummary(
        total_days=total_days,
        total_focus_hours=total_focus_hours,
        total_afk_hours=total_afk_hours,
        total_commands=total_commands,
        total_commits=total_commits,
        total_codex_sessions=total_codex_sessions,
        total_session_records=total_session_records,
        total_terminal_sessions=total_terminal_sessions,
        total_terminal_events=total_terminal_events,
        total_terminal_commands=total_terminal_commands,
        total_terminal_active_hours=total_terminal_active_hours,
        total_terminal_failures=total_terminal_failures,
        total_terminal_new_model_sessions=total_terminal_new_model_sessions,
        total_terminal_legacy_sessions=total_terminal_legacy_sessions,
        total_focus_minutes=total_focus_minutes,
        average_sleep_hours=(
            sum(sleep_hours_samples) / len(sleep_hours_samples) if sleep_hours_samples else None
        ),
        total_sleep_segments=total_sleep_segments,
        code_health=range_code_health,
        coding_session_count=coding_session_count,
        coding_session_hours=coding_session_hours,
        top_insights=top_insights,
        top_sessions=top_sessions,
        top_modes=[(mode, round(minutes, 1)) for mode, minutes in mode_counter.most_common(5)],
        top_projects=[(project, round(minutes, 1)) for project, minutes in project_counter.most_common(5)],
    )


def terminal_capture_overview_line(instrumentation_payload: Dict[str, Any]) -> str:
    session_count = int(instrumentation_payload.get("terminal_sessions", 0) or 0)
    if session_count <= 0:
        return "absent"
    capture_mode = str(instrumentation_payload.get("terminal_capture_mode") or "unknown")
    event_count = int(instrumentation_payload.get("terminal_events", 0) or 0)
    command_count = int(instrumentation_payload.get("terminal_command_count", 0) or 0)
    duration_hours = float(instrumentation_payload.get("terminal_duration_hours", 0.0) or 0.0)
    active_hours = float(instrumentation_payload.get("terminal_active_hours", 0.0) or 0.0)
    idle_hours = float(instrumentation_payload.get("terminal_idle_hours", 0.0) or 0.0)
    command_failures = int(instrumentation_payload.get("terminal_command_failures", 0) or 0)
    session_failures = int(instrumentation_payload.get("terminal_session_failures", 0) or 0)
    manifest_gaps = int(instrumentation_payload.get("terminal_sessions_missing_manifest", 0) or 0)
    event_gaps = int(instrumentation_payload.get("terminal_sessions_missing_events", 0) or 0)
    degraded_sessions = int(instrumentation_payload.get("terminal_degraded_sessions", 0) or 0)
    damaged_sessions = int(instrumentation_payload.get("terminal_damaged_sessions", 0) or 0)
    estimated_timing_sessions = int(instrumentation_payload.get("terminal_estimated_timing_sessions", 0) or 0)
    unknown_activity_sessions = int(instrumentation_payload.get("terminal_unknown_activity_sessions", 0) or 0)
    unknown_activity_hours = float(instrumentation_payload.get("terminal_unknown_activity_hours", 0.0) or 0.0)
    repo_map = instrumentation_payload.get("terminal_repos") or {}
    repo_text = ", ".join(f"{name} ({count})" for name, count in list(repo_map.items())[:3]) or "none"
    parts = [
        capture_mode,
        f"{session_count} session(s)",
        f"{event_count} event(s)",
        f"{command_count} command(s)",
        f"{active_hours:.2f}h active",
        f"{idle_hours:.2f}h idle",
        f"{duration_hours:.2f}h duration",
        f"repos: {repo_text}",
    ]
    if command_failures or session_failures:
        parts.append(f"failures: {command_failures} cmd / {session_failures} session")
    if degraded_sessions or damaged_sessions:
        parts.append(f"quality: {degraded_sessions} degraded / {damaged_sessions} damaged")
    if manifest_gaps or event_gaps:
        parts.append(f"gaps: {manifest_gaps} manifestless / {event_gaps} eventless")
    if estimated_timing_sessions:
        parts.append(f"timing estimates: {estimated_timing_sessions}")
    if unknown_activity_sessions:
        parts.append(f"activity unknown: {unknown_activity_sessions} session(s) / {unknown_activity_hours:.2f}h")
    return ", ".join(parts)


def terminal_capture_gaps_line(instrumentation_payload: Dict[str, Any]) -> str:
    return (
        f"{int(instrumentation_payload.get('terminal_sessions_missing_manifest', 0) or 0)} manifestless, "
        f"{int(instrumentation_payload.get('terminal_sessions_missing_events', 0) or 0)} eventless, "
        f"{int(instrumentation_payload.get('terminal_unknown_activity_sessions', 0) or 0)} without activity estimate, "
        f"{float(instrumentation_payload.get('terminal_unknown_activity_hours', 0.0) or 0.0):.2f}h uncertain duration, "
        f"{int(instrumentation_payload.get('terminal_command_failures', 0) or 0)} non-zero command(s), "
        f"{int(instrumentation_payload.get('terminal_session_failures', 0) or 0)} non-zero session(s)"
    )


def _focus_minutes(signals: Sequence[trajectory_signal.TrajectorySignal]) -> float:
    return round(sum(signal.duration_seconds for signal in signals) / 60.0, 2)


def _focus_categories(traj: trajectory_day.TrajectoryDay) -> Dict[str, float]:
    return {mode: round(seconds / 60.0, 2) for mode, seconds in traj.top_modes}


def _top_apps(signals: Sequence[trajectory_signal.TrajectorySignal]) -> List[Tuple[str, float]]:
    totals: Counter[str] = Counter()
    for signal in signals:
        label = signal.app or signal.title or "unknown"
        totals[label] += signal.duration_seconds / 60.0
    return [(name, round(minutes, 1)) for name, minutes in totals.most_common(5)]


def _top_web_domains_by_day(start: date, end: date) -> Dict[date, List[Tuple[str, int]]]:
    domains_by_day: dict[date, Counter[str]] = defaultdict(Counter)
    for entry in webhistory.iter_entries(start_date=start.isoformat(), end_date=end.isoformat()):
        entry_date = str(entry.get("date") or "")[:10]
        if not entry_date:
            iso_time = entry.get("iso_time")
            if isinstance(iso_time, str) and iso_time:
                entry_date = iso_time[:10]
        if not entry_date:
            continue
        url = str(entry.get("url") or "")
        parsed = urlparse(url)
        domain = parsed.netloc.strip().lower()
        if domain.startswith("www."):
            domain = domain[4:]
        if domain:
            domains_by_day[date.fromisoformat(entry_date)][domain] += 1
    return {
        target: counter.most_common(5)
        for target, counter in domains_by_day.items()
    }


def _command_categories(signals: Sequence[trajectory_signal.TrajectorySignal]) -> Dict[str, int]:
    commands = [
        SimpleNamespace(cwd=signal.cwd, command=signal.detail or "")
        for signal in signals
    ]
    return _commands_by_category(commands)


def _git_summary_from_signals(signals: Sequence[trajectory_signal.TrajectorySignal]) -> GitSummary:
    commits = []
    for signal in signals:
        if signal.source != "git.commit":
            continue
        repo_path = str(signal.evidence.get("repo") or "")
        repo = Path(repo_path).name if repo_path else ""
        commits.append(
            SimpleNamespace(
                repo=repo,
                lines_added=int(signal.evidence.get("lines_added") or 0),
                lines_deleted=int(signal.evidence.get("lines_deleted") or 0),
            )
        )
    return _git_summary(commits)


def _load_code_health() -> Optional[CodeHealthMetrics]:
    snapshot = _latest_snapshot()
    if not snapshot:
        return None
    crate_metrics = list(_iter_crate_metrics())
    if not crate_metrics:
        return None
    return _code_health_summary(snapshot, crate_metrics)


def _instrumentation_summary(
    terminal_sessions: Sequence[instrumentation.TerminalSessionMetadata],
    terminal_events: Sequence[instrumentation.TerminalSessionEvent],
) -> Dict[str, Any]:
    session_count = len(terminal_sessions)
    event_count = len(terminal_events)
    duration_seconds = 0.0
    active_seconds = 0.0
    idle_seconds = 0.0
    repo_counter: Counter[str] = Counter()
    command_counter: Counter[str] = Counter()
    event_type_counter: Counter[str] = Counter()
    command_sessions_with_events: set[str] = set()
    command_count = 0
    command_failures = 0
    session_failures = 0
    cwd_change_count = 0
    sessions_missing_manifests = 0
    sessions_missing_events = 0
    new_model_sessions = 0
    legacy_sessions = 0
    header_only_sessions = 0
    degraded_sessions = 0
    damaged_sessions = 0
    estimated_timing_sessions = 0
    unknown_activity_sessions = 0
    unknown_activity_seconds = 0.0

    for session in terminal_sessions:
        duration_seconds += float(session.duration_seconds or 0.0)
        active_seconds += float(session.active_seconds or 0.0)
        idle_seconds += float(session.idle_seconds or 0.0)
        if session.active_seconds is None and session.duration_seconds is not None:
            unknown_activity_sessions += 1
            unknown_activity_seconds += float(session.duration_seconds)
        if session.manifest_path:
            new_model_sessions += 1
        elif session.schema_generation == "legacy-meta":
            legacy_sessions += 1
        else:
            header_only_sessions += 1
        if session.quality_status == "degraded":
            degraded_sessions += 1
        elif session.quality_status == "damaged":
            damaged_sessions += 1
        if "timing_estimated" in session.quality_flags:
            estimated_timing_sessions += 1
        if not session.manifest_path:
            sessions_missing_manifests += 1
        if not session.has_events:
            sessions_missing_events += 1
        if session.exit_code not in (None, 0):
            session_failures += 1
        repo = session.final_repo_root or session.repo_root
        if repo:
            repo_counter[Path(repo).name or repo] += 1

    for event in terminal_events:
        event_type_counter[event.type] += 1
        if event.type == "command_start":
            command_sessions_with_events.add(event.session_id)
            command_count += 1
            command = _terminal_command_label(event.payload.get("command") or event.payload.get("cmd"))
            if command:
                command_counter[command] += 1
        elif event.type == "command_end":
            if event.exit_code not in (None, 0):
                command_failures += 1
        elif event.type in {"cwd", "location"}:
            cwd_change_count += 1

    for session in terminal_sessions:
        if session.session_id in command_sessions_with_events:
            continue
        command_count += int(session.command_count or 0)

    capture_mode = "absent"
    if session_count > 0:
        if new_model_sessions == session_count:
            capture_mode = "new-model"
        elif legacy_sessions == session_count:
            capture_mode = "legacy-only"
        elif header_only_sessions == session_count:
            capture_mode = "header-only"
        else:
            capture_mode = "mixed"

    return {
        "terminal_sessions": session_count,
        "terminal_events": event_count,
        "terminal_duration_hours": round(duration_seconds / 3600.0, 2),
        "terminal_active_hours": round(active_seconds / 3600.0, 2),
        "terminal_idle_hours": round(idle_seconds / 3600.0, 2),
        "terminal_unknown_activity_sessions": unknown_activity_sessions,
        "terminal_unknown_activity_hours": round(unknown_activity_seconds / 3600.0, 2),
        "terminal_command_count": command_count,
        "terminal_command_failures": command_failures,
        "terminal_session_failures": session_failures,
        "terminal_cwd_changes": cwd_change_count,
        "terminal_sessions_missing_manifest": sessions_missing_manifests,
        "terminal_sessions_missing_events": sessions_missing_events,
        "terminal_new_model_sessions": new_model_sessions,
        "terminal_legacy_sessions": legacy_sessions,
        "terminal_header_only_sessions": header_only_sessions,
        "terminal_degraded_sessions": degraded_sessions,
        "terminal_damaged_sessions": damaged_sessions,
        "terminal_estimated_timing_sessions": estimated_timing_sessions,
        "terminal_capture_mode": capture_mode,
        "terminal_repos": dict(repo_counter.most_common(5)),
        "terminal_commands": dict(command_counter.most_common(5)),
        "terminal_event_types": dict(event_type_counter.most_common(6)),
    }


def _terminal_command_label(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    head = value.strip().split(maxsplit=1)[0]
    return head or None


def _session_to_dict(record: SessionRecord) -> Dict[str, str]:
    return {
        "date": record.date.isoformat(),
        "provider": record.provider,
        "label": record.label,
        "doc_path": record.doc_path,
        "highlights": record.highlights,
    }


def _transcript_to_dict(item: ChatTranscript) -> Dict[str, object]:
    return {
        "provider": item.provider,
        "slug": item.slug,
        "title": item.title,
        "path": str(item.path),
        "started_at": item.started_at.isoformat(),
        "tokens": item.tokens,
        "words": item.words,
        "attachment_count": item.attachment_count,
        "attachment_bytes": item.attachment_bytes,
    }


def _local_tz():
    return datetime.now().astimezone().tzinfo or timezone.utc
