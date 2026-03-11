"""Shared helpers for summarising Lynchpin day snapshots."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

from ..sources.exports import chatlog
from ..sources.exports.chatlog import ChatTranscript
from ..sources.exports.sleep import SleepEntry
from ..sources.indices.sessions import SessionRecord
from ..sources.indices import gitstats, sessions
from ..sources.captures import activitywatch, atuin
from .calendar import DaySnapshot, load_day

FALSE_ACTIVE_APPS = {"gcr-prompter"}


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
class GitSummary:
    commits: int
    lines_added: int
    lines_deleted: int
    repos: Dict[str, int]


@dataclass
class SleepSummary:
    total_hours: float
    segments: int
    avg_score: Optional[float]


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
    window_event_count: int = 0
    afk_event_count: int = 0

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass
class DashboardDayMetrics:
    date: str
    active_hours: float
    afk_hours: float
    window_hours: float
    command_total: int
    codex_sessions: int
    git_commits: int
    focus_minutes: float
    top_apps: List[str]

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
    top_insights: str
    top_sessions: str


def summarize_day(snapshot: DaySnapshot) -> DaySummary:
    focus_counter = _focus_minutes(snapshot.windows)
    focus_categories = {name: round(minutes, 2) for name, minutes in focus_counter.items()}
    focus_minutes_total = sum(focus_counter.values())

    active_hours, afk_hours = _afk_split(snapshot.afk, snapshot.windows)
    window_hours = _minutes_to_hours(sum(focus_counter.values()))

    command_categories = _commands_by_category(snapshot.atuin_commands)
    sessions = [_session_to_dict(record) for record in snapshot.session_records]
    transcripts = [_transcript_to_dict(item) for item in chatlog.transcripts_by_date(snapshot.date)]
    codex_sessions = sum(
        1 for record in sessions if "codex" in record.get("provider", "").lower()
    ) + sum(1 for transcript in transcripts if transcript.get("provider") == "codex")

    git_summary = _git_summary(snapshot.git_commits)
    sleep_summary = _sleep_summary(snapshot.sleep)
    top_apps = list(focus_counter.most_common(5))
    web_domains = _top_web_domains(snapshot.webhistory)
    instrumentation_summary = _instrumentation_summary(snapshot.terminal_sessions, snapshot.terminal_events)

    return DaySummary(
        date=snapshot.date.isoformat(),
        overview=Overview(
            active_hours=round(active_hours, 2),
            afk_hours=round(afk_hours, 2),
            window_hours=round(window_hours, 2),
        ),
        command_total=len(snapshot.atuin_commands),
        command_categories=command_categories,
        codex_sessions=codex_sessions,
        atuin_commands=len(snapshot.atuin_commands),
        git=git_summary,
        sessions=sessions,
        transcripts=transcripts,
        sleep=sleep_summary,
        focus=FocusSummary(
            total_focus_minutes=round(focus_minutes_total, 2),
            categories=focus_categories,
        ),
        insights=[],
        instrumentation=instrumentation_summary,
        top_apps=[(name, round(minutes, 1)) for name, minutes in top_apps],
        top_web_domains=web_domains,
        window_event_count=len(snapshot.windows),
        afk_event_count=len(snapshot.afk),
    )


def load_day_summary(target: date) -> DaySummary:
    return summarize_day(load_day(target))


def dashboard_day_metrics(summary: DaySummary) -> DashboardDayMetrics:
    return DashboardDayMetrics(
        date=summary.date,
        active_hours=summary.overview.active_hours,
        afk_hours=summary.overview.afk_hours,
        window_hours=summary.overview.window_hours,
        command_total=summary.command_total,
        codex_sessions=summary.codex_sessions,
        git_commits=summary.git.commits,
        focus_minutes=round(summary.focus.total_focus_minutes, 1),
        top_apps=[name for name, _minutes in summary.top_apps[:3]],
    )


def dashboard_day_metrics_from_inputs(
    target: date,
    *,
    windows: Sequence,
    afk: Sequence,
    commands: Sequence,
    session_records: Sequence[SessionRecord],
    git_commits: Sequence,
) -> DashboardDayMetrics:
    focus_counter = _focus_minutes(windows)
    active_hours, afk_hours = _afk_split(afk, windows)
    git_summary = _git_summary(list(git_commits))
    codex_sessions = sum(1 for record in session_records if "codex" in record.provider.lower())
    return DashboardDayMetrics(
        date=target.isoformat(),
        active_hours=round(active_hours, 2),
        afk_hours=round(afk_hours, 2),
        window_hours=round(sum(focus_counter.values()) / 60.0, 2),
        command_total=len(commands),
        codex_sessions=codex_sessions,
        git_commits=git_summary.commits,
        focus_minutes=round(sum(focus_counter.values()), 1),
        top_apps=[name for name, _minutes in focus_counter.most_common(3)],
    )


def load_dashboard_day_metrics(target: date) -> DashboardDayMetrics:
    local_tz = datetime.now().astimezone().tzinfo
    start = datetime.combine(target, datetime.min.time(), tzinfo=local_tz)
    end = start + timedelta(days=1)
    windows = list(activitywatch.window_events(day=target))
    afk = list(activitywatch.afk_events(day=target))
    commands = list(atuin.iter_commands(start=start, end=end))
    return dashboard_day_metrics_from_inputs(
        target,
        windows=windows,
        afk=afk,
        commands=commands,
        session_records=sessions.sessions_by_date(target),
        git_commits=gitstats.commits_by_date(target),
    )


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

    insight_counter: Counter = Counter()
    session_counter: Counter = Counter()
    for summary in summaries:
        insight_counter.update(summary.insights)
        session_counter.update(
            (session.get("label") or "Session", session.get("provider") or "")
            for session in summary.sessions
        )

    top_insights = ", ".join(f"{text}×{count}" for text, count in insight_counter.most_common(6)) or "No notable highlights captured."
    top_sessions = (
        ", ".join(
            f"{label}{' (' + provider + ')' if provider else ''}×{count}"
            for (label, provider), count in session_counter.most_common(5)
        )
        if session_counter
        else "No recorded sessions."
    )

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
        top_insights=top_insights,
        top_sessions=top_sessions,
    )


def terminal_capture_overview_line(instrumentation: Dict[str, Any]) -> str:
    session_count = int(instrumentation.get("terminal_sessions", 0) or 0)
    if session_count <= 0:
        return "absent"
    capture_mode = str(instrumentation.get("terminal_capture_mode") or "unknown")
    event_count = int(instrumentation.get("terminal_events", 0) or 0)
    command_count = int(instrumentation.get("terminal_command_count", 0) or 0)
    duration_hours = float(instrumentation.get("terminal_duration_hours", 0.0) or 0.0)
    active_hours = float(instrumentation.get("terminal_active_hours", 0.0) or 0.0)
    idle_hours = float(instrumentation.get("terminal_idle_hours", 0.0) or 0.0)
    command_failures = int(instrumentation.get("terminal_command_failures", 0) or 0)
    session_failures = int(instrumentation.get("terminal_session_failures", 0) or 0)
    manifest_gaps = int(instrumentation.get("terminal_sessions_missing_manifest", 0) or 0)
    event_gaps = int(instrumentation.get("terminal_sessions_missing_events", 0) or 0)
    degraded_sessions = int(instrumentation.get("terminal_degraded_sessions", 0) or 0)
    damaged_sessions = int(instrumentation.get("terminal_damaged_sessions", 0) or 0)
    estimated_timing_sessions = int(instrumentation.get("terminal_estimated_timing_sessions", 0) or 0)
    unknown_activity_sessions = int(instrumentation.get("terminal_unknown_activity_sessions", 0) or 0)
    unknown_activity_hours = float(instrumentation.get("terminal_unknown_activity_hours", 0.0) or 0.0)
    repo_map = instrumentation.get("terminal_repos") or {}
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


def terminal_capture_gaps_line(instrumentation: Dict[str, Any]) -> str:
    return (
        f"{int(instrumentation.get('terminal_sessions_missing_manifest', 0) or 0)} manifestless, "
        f"{int(instrumentation.get('terminal_sessions_missing_events', 0) or 0)} eventless, "
        f"{int(instrumentation.get('terminal_unknown_activity_sessions', 0) or 0)} without activity estimate, "
        f"{float(instrumentation.get('terminal_unknown_activity_hours', 0.0) or 0.0):.2f}h uncertain duration, "
        f"{int(instrumentation.get('terminal_command_failures', 0) or 0)} non-zero command(s), "
        f"{int(instrumentation.get('terminal_session_failures', 0) or 0)} non-zero session(s)"
    )


def _focus_minutes(events: Sequence) -> Counter:
    counter: Counter = Counter()
    for event in events:
        minutes = _duration_minutes(event)
        if minutes <= 0:
            continue
        data = getattr(event, "data", {}) or {}
        label = _window_label(data) or "unknown"
        counter[label] += minutes
    return counter


def _instrumentation_summary(terminal_sessions: Sequence, terminal_events: Sequence) -> Dict[str, Any]:
    session_count = len(terminal_sessions)
    event_count = len(terminal_events)
    duration_seconds = 0.0
    active_seconds = 0.0
    idle_seconds = 0.0
    repo_counter: Counter = Counter()
    command_counter: Counter = Counter()
    event_type_counter: Counter = Counter()
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
            repo_label = Path(repo).name or repo
            repo_counter[repo_label] += 1
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


def _window_label(data: Dict[str, object]) -> str:
    for key in ("app", "application", "appname", "bundle"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    title = data.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()[:80]
    return "unknown"


def _duration_minutes(event) -> float:
    start = getattr(event, "start", None)
    end = getattr(event, "end", None)
    if not start or not end:
        return 0.0
    delta = end - start
    if not isinstance(delta, timedelta):
        return 0.0
    return max(delta.total_seconds() / 60.0, 0.0)


def _minutes_to_hours(minutes: float) -> float:
    return minutes / 60.0


def _afk_split(events: Sequence, windows: Sequence = ()) -> Tuple[float, float]:
    active_minutes = 0.0
    afk_minutes = 0.0
    active_intervals = []

    for event in events:
        minutes = _duration_minutes(event)
        data = getattr(event, "data", {}) or {}
        status = str(data.get("status") or "").lower()

        is_afk = False
        if status in {"afk", "away"}:
            is_afk = True
        elif status in {"not-afk", "active", "present"}:
            is_afk = False
        else:
            flag = data.get("afk")
            if isinstance(flag, bool):
                is_afk = flag
            elif isinstance(flag, str):
                is_afk = flag.lower() == "true"

        if is_afk:
            afk_minutes += minutes
        else:
            active_minutes += minutes
            # Collect active interval
            start = getattr(event, "start", None)
            end = getattr(event, "end", None)
            if start and end:
                active_intervals.append((start, end))

    if windows and active_intervals:
        false_active = _calculate_false_active_minutes(active_intervals, windows)
        # Cap false active at total active?
        false_active = min(false_active, active_minutes)
        active_minutes -= false_active
        afk_minutes += false_active

    return _minutes_to_hours(active_minutes), _minutes_to_hours(afk_minutes)


def _calculate_false_active_minutes(active_intervals: List[Tuple[datetime, datetime]], windows: Sequence) -> float:
    total_false_active = 0.0

    # We only care about windows that are "bad".
    bad_windows = [
        w for w in windows
        if (_window_label(w.data or {}) in FALSE_ACTIVE_APPS or (w.data or {}).get("app") in FALSE_ACTIVE_APPS)
    ]

    if not bad_windows:
        return 0.0

    for w in bad_windows:
        w_start = getattr(w, "start", None)
        w_end = getattr(w, "end", None)
        if not w_start or not w_end:
            continue

        # Check against all active intervals
        for (a_start, a_end) in active_intervals:
            # Overlap?
            latest_start = max(w_start, a_start)
            earliest_end = min(w_end, a_end)
            if earliest_end > latest_start:
                duration = (earliest_end - latest_start).total_seconds() / 60.0
                total_false_active += duration

    return total_false_active


def _commands_by_category(commands: Sequence) -> Dict[str, int]:
    bucket: Counter = Counter()
    for command in commands:
        cwd = getattr(command, "cwd", None)
        cmd = getattr(command, "command", "")
        category = _categorise_command(cwd, cmd)
        bucket[category] += 1
    return dict(sorted(bucket.items()))


def _categorise_command(cwd: Optional[str], command: str) -> str:
    if not cwd or not isinstance(cwd, str):
        return "misc"
    path = cwd.strip()
    lowered = path.lower()
    if "project/sinex" in lowered or lowered.rstrip("/").endswith("sinex"):
        return "development:sinex"
    if "sinnix" in lowered:
        return "infrastructure:sinnix"
    if "/realm/project/" in lowered:
        return "development:other"
    if lowered.startswith("/realm/home") or lowered.startswith("/home"):
        return "home"
    return "misc"


def _session_to_dict(record: SessionRecord) -> Dict[str, str]:
    return {
        "date": record.date.isoformat(),
        "provider": record.provider,
        "label": record.label,
        "doc_path": record.doc_path,
        "highlights": record.highlights,
    }


def _transcript_to_dict(record: ChatTranscript) -> Dict[str, object]:
    return {
        "provider": record.provider,
        "slug": record.slug,
        "title": record.title,
        "path": str(record.path),
        "started_at": record.started_at.isoformat(),
        "tokens": record.tokens,
        "words": record.words,
        "attachment_count": record.attachment_count,
        "attachment_bytes": record.attachment_bytes,
    }


def _git_summary(commits: Sequence) -> GitSummary:
    total = len(commits)
    added = sum(getattr(commit, "lines_added", 0) for commit in commits)
    deleted = sum(getattr(commit, "lines_deleted", 0) for commit in commits)
    repos: Counter = Counter(getattr(commit, "repo", "") or "" for commit in commits)
    return GitSummary(
        commits=total,
        lines_added=int(added),
        lines_deleted=int(deleted),
        repos={name: count for name, count in repos.items() if name},
    )


def _sleep_summary(entry: Optional[SleepEntry]) -> Optional[SleepSummary]:
    if entry is None:
        return None
    total_hours = (entry.total_minutes or 0.0) / 60.0
    segments = len(entry.segments)
    score = entry.avg_score
    return SleepSummary(total_hours=round(total_hours, 2), segments=segments, avg_score=score)


def _top_web_domains(entries: Iterable[Dict[str, object]], limit: int = 5) -> List[Tuple[str, int]]:
    counter: Counter = Counter()
    for record in entries:
        url = record.get("url") or record.get("pageUrl")
        if not isinstance(url, str) or not url.strip():
            continue
        domain = urlparse(url).netloc or "unknown"
        counter[domain.lower()] += 1
    return counter.most_common(limit)
