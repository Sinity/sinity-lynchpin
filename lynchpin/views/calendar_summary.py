"""Shared helpers for summarising Lynchpin day snapshots."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, asdict
from datetime import timedelta
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

from ..sources.exports import chatlog
from ..sources.exports.chatlog import ChatTranscript
from ..sources.indices.sessions import SessionRecord
from ..sources.exports.sleep import SleepEntry
from .calendar import DaySnapshot


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
    instrumentation: Dict[str, int] = field(default_factory=dict)
    top_apps: List[Tuple[str, float]] = field(default_factory=list)
    top_web_domains: List[Tuple[str, int]] = field(default_factory=list)
    window_event_count: int = 0
    afk_event_count: int = 0

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def summarize_day(snapshot: DaySnapshot) -> DaySummary:
    focus_counter = _focus_minutes(snapshot.windows)
    focus_categories = {name: round(minutes, 2) for name, minutes in focus_counter.items()}
    focus_minutes_total = sum(focus_counter.values())

    active_hours, afk_hours = _afk_split(snapshot.afk)
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
        instrumentation={},
        top_apps=[(name, round(minutes, 1)) for name, minutes in top_apps],
        top_web_domains=web_domains,
        window_event_count=len(snapshot.windows),
        afk_event_count=len(snapshot.afk),
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


def _afk_split(events: Sequence) -> Tuple[float, float]:
    active = 0.0
    afk = 0.0
    for event in events:
        minutes = _duration_minutes(event)
        data = getattr(event, "data", {}) or {}
        status = str(data.get("status") or "").lower()
        if status in {"afk", "away"}:
            afk += minutes
        elif status in {"not-afk", "active", "present"}:
            active += minutes
        else:
            flag = data.get("afk")
            is_afk = False
            if isinstance(flag, bool):
                is_afk = flag
            elif isinstance(flag, str):
                is_afk = flag.lower() == "true"
            if is_afk:
                afk += minutes
            else:
                active += minutes
    return _minutes_to_hours(active), _minutes_to_hours(afk)


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
