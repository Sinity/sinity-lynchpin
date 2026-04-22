"""AI chat source: session profiles, daily activity, cost, work patterns.

Uses polylogue's Python facade API via polylogue-python subprocess.
Polylogue owns conversation semantics — lynchpin reads its materialized products.

Falls back gracefully when the facade hits schema mismatches on legacy records
(84% of records have a legacy inference schema that the current Pydantic model rejects).
Uses paginated queries (limit=1000) to maximize coverage.

Covers all providers: Claude (claude-ai, claude-code), ChatGPT, Codex, Gemini.
"""

from __future__ import annotations

import json
import logging
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterator, Optional

from ..core.parse import parse_datetime as _parse_dt

logger = logging.getLogger(__name__)

__all__ = [
    "SessionProfile",
    "ChatDayActivity",
    "CostSummary",
    "WorkPattern",
    "WorkEvent",
    "DaySessionSummary",
    "iter_session_profiles",
    "work_events",
    "day_session_summaries",
    "daily_activity",
    "cost_summary",
    "work_pattern",
    "archive_stats",
]

@dataclass(frozen=True)
class WorkEvent:
    """A temporal work segment within a polylogue session."""
    event_id: str
    conversation_id: str
    provider: str
    kind: str
    confidence: float
    start: Optional[datetime]
    end: Optional[datetime]
    duration_ms: int
    file_paths: tuple[str, ...]
    tools_used: tuple[str, ...]
    summary: str


@dataclass(frozen=True)
class DaySessionSummary:
    """Polylogue's pre-computed daily aggregation."""
    date: date
    session_count: int
    total_cost_usd: float
    total_messages: int
    total_words: int
    work_event_breakdown: dict[str, int]  # kind → count
    repos_active: tuple[str, ...]
    providers: dict[str, int]  # provider → session count


_POLYLOGUE_PYTHON = "polylogue-python"
_POLYLOGUE_CLI = "polylogue"


# ══════════════════════════════════════════════════════════════════════════════
# Data types
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class SessionProfile:
    conversation_id: str
    provider: str
    title: str
    message_count: int
    word_count: int
    first_message_at: Optional[datetime]
    last_message_at: Optional[datetime]
    engaged_duration_ms: int
    wall_duration_ms: int
    work_event_kind: Optional[str]
    work_event_projects: tuple[str, ...]
    total_cost_usd: float
    canonical_session_date: Optional[date]
    tool_use_count: int
    thinking_count: int
    auto_tags: tuple[str, ...]


@dataclass(frozen=True)
class ChatDayActivity:
    date: date
    provider: str
    session_count: int
    total_messages: int
    total_words: int
    engaged_minutes: float
    total_wall_minutes: float
    dominant_work_kind: str | None
    projects: tuple[str, ...]


# ══════════════════════════════════════════════════════════════════════════════
# Polylogue facade access via subprocess
# ══════════════════════════════════════════════════════════════════════════════

# Paginated query script — handles legacy schema validation errors gracefully
_QUERY_SCRIPT = '''
import asyncio, json, sys

async def main():
    from polylogue.facade import Polylogue
    from polylogue.archive_products import SessionProfileProductQuery

    results = []
    async with Polylogue() as p:
        # Paginate with limit=1000 to avoid schema mismatch on legacy records
        offset = 0
        page_size = 1000
        while True:
            try:
                profiles = await p.list_session_profile_products(
                    query=SessionProfileProductQuery(limit=page_size, offset=offset)
                )
            except Exception:
                # Schema mismatch on this page — skip and try next
                offset += page_size
                if offset > 20000:
                    break
                continue

            if not profiles:
                break

            for prof in profiles:
                ev = prof.evidence
                inf = prof.inference
                if ev is None:
                    continue
                evd = ev.model_dump()
                # Safe access — inference may be None or have different fields
                inf_kind = None
                inf_projects = []
                inf_engaged_ms = 0
                inf_tags = []
                if inf:
                    d = inf.model_dump()
                    events = list(d.get("work_events") or [])
                    kinds = []
                    for event in events:
                        if hasattr(event, "model_dump"):
                            event = event.model_dump()
                        if isinstance(event, dict) and event.get("kind"):
                            kinds.append(event["kind"])
                    if kinds:
                        inf_kind = max(set(kinds), key=kinds.count)
                    else:
                        inf_kind = d.get("primary_work_kind") or d.get("kind")
                    inf_projects = list(d.get("repo_names") or d.get("canonical_projects") or [])
                    inf_engaged_ms = d.get("engaged_duration_ms", 0) or 0
                    if not inf_engaged_ms and d.get("engaged_minutes") is not None:
                        inf_engaged_ms = int(float(d.get("engaged_minutes") or 0) * 60_000)
                    inf_tags = list(d.get("auto_tags") or [])
                if not inf_projects:
                    inf_projects = list(evd.get("repo_names") or [])
                if not inf_projects:
                    inf_projects = list(evd.get("repo_paths") or evd.get("cwd_paths") or [])

                results.append({
                    "conversation_id": prof.conversation_id,
                    "provider": prof.provider_name,
                    "title": prof.title or "",
                    "message_count": evd.get("message_count", 0),
                    "word_count": evd.get("word_count", 0),
                    "first_message_at": str(evd.get("first_message_at")) if evd.get("first_message_at") else None,
                    "last_message_at": str(evd.get("last_message_at")) if evd.get("last_message_at") else None,
                    "engaged_duration_ms": inf_engaged_ms,
                    "wall_duration_ms": evd.get("wall_duration_ms", 0) or 0,
                    "work_event_kind": inf_kind,
                    "work_event_projects": inf_projects,
                    "total_cost_usd": evd.get("total_cost_usd", 0) or 0,
                    "canonical_session_date": str(evd.get("canonical_session_date")) if evd.get("canonical_session_date") else None,
                    "tool_use_count": evd.get("tool_use_count", 0) or 0,
                    "thinking_count": evd.get("thinking_count", 0) or 0,
                    "auto_tags": inf_tags,
                })

            offset += page_size

    json.dump(results, sys.stdout)

asyncio.run(main())
'''

_cached_profiles: list[SessionProfile] | None = None


def _load_profiles() -> list[SessionProfile]:
    """Load session profiles from polylogue via facade API."""
    global _cached_profiles
    if _cached_profiles is not None:
        return _cached_profiles

    try:
        result = subprocess.run(
            [_POLYLOGUE_PYTHON, "-c", _QUERY_SCRIPT],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            logger.warning("polylogue facade failed: %s", result.stderr[:300])
            _cached_profiles = []
            return _cached_profiles

        raw = json.loads(result.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning("polylogue query failed: %s", e)
        _cached_profiles = []
        return _cached_profiles

    profiles: list[SessionProfile] = []
    for r in raw:
        session_date = None
        if r.get("canonical_session_date") and r["canonical_session_date"] != "None":
            try:
                session_date = date.fromisoformat(str(r["canonical_session_date"]))
            except ValueError:
                pass
        profiles.append(SessionProfile(
            conversation_id=r["conversation_id"],
            provider=r["provider"],
            title=r["title"],
            message_count=r.get("message_count", 0),
            word_count=r.get("word_count", 0),
            first_message_at=_parse_dt(r.get("first_message_at")),
            last_message_at=_parse_dt(r.get("last_message_at")),
            engaged_duration_ms=r.get("engaged_duration_ms", 0) or 0,
            wall_duration_ms=r.get("wall_duration_ms", 0) or 0,
            work_event_kind=r.get("work_event_kind"),
            work_event_projects=tuple(r.get("work_event_projects", [])),
            total_cost_usd=float(r.get("total_cost_usd", 0) or 0),
            canonical_session_date=session_date,
            tool_use_count=r.get("tool_use_count", 0) or 0,
            thinking_count=r.get("thinking_count", 0) or 0,
            auto_tags=tuple(r.get("auto_tags", [])),
        ))

    _cached_profiles = profiles
    return _cached_profiles


def iter_session_profiles() -> Iterator[SessionProfile]:
    """Yield all session profiles from polylogue archive."""
    yield from _load_profiles()


def archive_stats() -> dict[str, object]:
    """Quick stats from the polylogue archive."""
    try:
        result = subprocess.run(
            [_POLYLOGUE_CLI, "stats", "--format", "json"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            payload = json.loads(result.stdout)
            summary = payload.get("summary", payload)
            return {
                "conversation_count": summary.get("conversation_count", summary.get("conversations", 0)),
                "message_count": summary.get("message_count", summary.get("messages_total", summary.get("messages", 0))),
                "word_count": summary.get("word_count", summary.get("words_approx", 0)),
                "providers": summary.get("providers", {}),
                "date_range": summary.get("date_range"),
                "embeddings": summary.get("embeddings", {}),
            }
    except Exception as e:
        logger.warning("polylogue stats failed: %s", e)
    return {}


# ══════════════════════════════════════════════════════════════════════════════
# Work events (sub-session temporal segments)
# ══════════════════════════════════════════════════════════════════════════════

_WORK_EVENTS_SCRIPT = '''
import asyncio, json, sys

async def main():
    from polylogue.facade import Polylogue
    from polylogue.archive_products import SessionWorkEventProductQuery

    results = []
    async with Polylogue() as p:
        events = await p.list_session_work_event_products(
            query=SessionWorkEventProductQuery(limit=None)
        )
        for ev in events:
            evd = ev.evidence.model_dump() if ev.evidence else {}
            inf = ev.inference.model_dump() if ev.inference else {}
            results.append({
                "event_id": ev.event_id,
                "conversation_id": ev.conversation_id,
                "provider": ev.provider_name,
                "kind": inf.get("kind", "unknown"),
                "confidence": inf.get("confidence", 0),
                "start_time": str(evd.get("start_time")) if evd.get("start_time") else None,
                "end_time": str(evd.get("end_time")) if evd.get("end_time") else None,
                "duration_ms": evd.get("duration_ms", 0) or 0,
                "file_paths": list(evd.get("file_paths") or []),
                "tools_used": list(evd.get("tools_used") or []),
                "summary": str(inf.get("summary", ""))[:200],
            })
    json.dump(results, sys.stdout)

asyncio.run(main())
'''

_cached_work_events: list[WorkEvent] | None = None


def work_events(*, start: Optional[date] = None, end: Optional[date] = None) -> list[WorkEvent]:
    """Load work events from polylogue — sub-session temporal segments with kind, files, tools."""
    global _cached_work_events
    if _cached_work_events is None:
        try:
            result = subprocess.run(
                [_POLYLOGUE_PYTHON, "-c", _WORK_EVENTS_SCRIPT],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                logger.warning("polylogue work_events failed: %s", result.stderr[:200])
                _cached_work_events = []
            else:
                raw = json.loads(result.stdout)
                _cached_work_events = [
                    WorkEvent(
                        event_id=r["event_id"],
                        conversation_id=r["conversation_id"],
                        provider=r["provider"],
                        kind=r["kind"],
                        confidence=r.get("confidence", 0) or 0,
                        start=_parse_dt(r.get("start_time")),
                        end=_parse_dt(r.get("end_time")),
                        duration_ms=r.get("duration_ms", 0),
                        file_paths=tuple(r.get("file_paths", [])),
                        tools_used=tuple(r.get("tools_used", [])),
                        summary=r.get("summary", ""),
                    )
                    for r in raw
                ]
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError) as e:
            logger.warning("polylogue work_events failed: %s", e)
            _cached_work_events = []

    events = _cached_work_events
    if start or end:
        filtered = []
        for ev in events:
            if ev.start is None:
                continue
            d = ev.start.date()
            if start and d < start:
                continue
            if end and d > end:
                continue
            filtered.append(ev)
        return filtered
    return list(events)


# ══════════════════════════════════════════════════════════════════════════════
# Day session summaries (polylogue's pre-computed daily aggregation)
# ══════════════════════════════════════════════════════════════════════════════

_DAY_SUMMARY_SCRIPT = '''
import asyncio, json, sys

async def main():
    from polylogue.facade import Polylogue
    from polylogue.archive_products import DaySessionSummaryProductQuery

    results = []
    async with Polylogue() as p:
        days = await p.list_day_session_summary_products(
            query=DaySessionSummaryProductQuery(limit=None)
        )
        for d in days:
            s = d.summary if hasattr(d, 'summary') else d.model_dump().get('summary', {})
            if isinstance(s, dict):
                sd = s
            else:
                sd = s.model_dump() if hasattr(s, 'model_dump') else {}
            results.append({
                "date": sd.get("date", ""),
                "session_count": sd.get("session_count", 0),
                "total_cost_usd": sd.get("total_cost_usd", 0),
                "total_messages": sd.get("total_messages", 0),
                "total_words": sd.get("total_words", 0),
                "work_event_breakdown": sd.get("work_event_breakdown", {}),
                "repos_active": list(sd.get("repos_active") or sd.get("projects_active") or []),
                "providers": sd.get("providers", {}),
            })
    json.dump(results, sys.stdout)

asyncio.run(main())
'''

_cached_day_summaries: list[DaySessionSummary] | None = None


def day_session_summaries(*, start: Optional[date] = None, end: Optional[date] = None) -> list[DaySessionSummary]:
    """Polylogue's pre-computed daily session aggregation."""
    global _cached_day_summaries
    if _cached_day_summaries is None:
        try:
            result = subprocess.run(
                [_POLYLOGUE_PYTHON, "-c", _DAY_SUMMARY_SCRIPT],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                logger.warning("polylogue day_summaries failed: %s", result.stderr[:200])
                _cached_day_summaries = []
            else:
                raw = json.loads(result.stdout)
                _cached_day_summaries = [
                    DaySessionSummary(
                        date=date.fromisoformat(r["date"]) if r.get("date") else date.min,
                        session_count=r.get("session_count", 0),
                        total_cost_usd=float(r.get("total_cost_usd", 0)),
                        total_messages=r.get("total_messages", 0),
                        total_words=r.get("total_words", 0),
                        work_event_breakdown=r.get("work_event_breakdown", {}),
                        repos_active=tuple(r.get("repos_active", [])),
                        providers=r.get("providers", {}),
                    )
                    for r in raw if r.get("date")
                ]
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError) as e:
            logger.warning("polylogue day_summaries failed: %s", e)
            _cached_day_summaries = []

    summaries = _cached_day_summaries
    if start or end:
        return [s for s in summaries if (not start or s.date >= start) and (not end or s.date <= end)]
    return list(summaries)


# ══════════════════════════════════════════════════════════════════════════════
# Daily activity
# ══════════════════════════════════════════════════════════════════════════════


def daily_activity(*, start: date, end: date) -> list[ChatDayActivity]:
    """Daily AI chat activity per provider."""
    summary_result = _daily_activity_from_day_summaries(start=start, end=end)
    if summary_result:
        return summary_result

    by_key: dict[tuple[date, str], list[SessionProfile]] = defaultdict(list)

    for profile in iter_session_profiles():
        d = profile.canonical_session_date
        if d is None:
            dt = profile.last_message_at or profile.first_message_at
            if dt is None:
                continue
            d = dt.date()
        if d < start or d > end:
            continue
        by_key[(d, profile.provider)].append(profile)

    result: list[ChatDayActivity] = []
    for (day, provider), profiles in sorted(by_key.items()):
        work_kinds: Counter[str] = Counter()
        projects: set[str] = set()
        total_messages = total_words = 0
        engaged_ms = wall_ms = 0
        for p in profiles:
            total_messages += p.message_count
            total_words += p.word_count
            engaged_ms += p.engaged_duration_ms
            wall_ms += p.wall_duration_ms
            if p.work_event_kind:
                work_kinds[p.work_event_kind] += 1
            projects.update(p.work_event_projects)
        result.append(ChatDayActivity(
            date=day, provider=provider, session_count=len(profiles),
            total_messages=total_messages, total_words=total_words,
            engaged_minutes=round(engaged_ms / 60_000, 1),
            total_wall_minutes=round(wall_ms / 60_000, 1),
            dominant_work_kind=work_kinds.most_common(1)[0][0] if work_kinds else None,
            projects=tuple(sorted(projects)),
        ))
    return result


def _daily_activity_from_day_summaries(*, start: date, end: date) -> list[ChatDayActivity]:
    """Fallback daily activity from durable day summaries when profiles are unavailable."""
    result: list[ChatDayActivity] = []
    for summary in day_session_summaries(start=start, end=end):
        if not summary.providers:
            providers = {"unknown": summary.session_count}
        else:
            providers = summary.providers
        dominant = None
        if summary.work_event_breakdown:
            dominant = max(summary.work_event_breakdown, key=summary.work_event_breakdown.get)
        total_sessions = max(summary.session_count, 1)
        for provider, count in sorted(providers.items()):
            share = count / total_sessions
            result.append(ChatDayActivity(
                date=summary.date,
                provider=provider,
                session_count=count,
                total_messages=round(summary.total_messages * share),
                total_words=round(summary.total_words * share),
                engaged_minutes=0.0,
                total_wall_minutes=0.0,
                dominant_work_kind=dominant,
                projects=summary.repos_active,
            ))
    return result


# ══════════════════════════════════════════════════════════════════════════════
# Cost and work pattern analytics
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class CostSummary:
    date: date
    provider: str
    session_count: int
    total_cost_usd: float
    total_messages: int
    cost_per_message: float


def cost_summary(*, start: date, end: date) -> list[CostSummary]:
    """Daily LLM spend per provider."""
    summary_result: list[CostSummary] = []
    for day in day_session_summaries(start=start, end=end):
        if day.total_cost_usd <= 0:
            continue
        total_sessions = max(day.session_count, 1)
        for provider, count in sorted((day.providers or {"unknown": day.session_count}).items()):
            share = count / total_sessions
            messages = round(day.total_messages * share)
            cost = day.total_cost_usd * share
            summary_result.append(CostSummary(
                date=day.date,
                provider=provider,
                session_count=count,
                total_cost_usd=round(cost, 4),
                total_messages=messages,
                cost_per_message=round(cost / max(messages, 1), 4),
            ))
    if summary_result:
        return summary_result

    by_key: dict[tuple[date, str], list[SessionProfile]] = defaultdict(list)
    for p in iter_session_profiles():
        d = p.canonical_session_date
        if d is None:
            continue
        if d < start or d > end:
            continue
        if p.total_cost_usd <= 0:
            continue
        by_key[(d, p.provider)].append(p)

    result: list[CostSummary] = []
    for (d, provider), profiles in sorted(by_key.items()):
        total_cost = sum(p.total_cost_usd for p in profiles)
        total_msgs = sum(p.message_count for p in profiles)
        result.append(CostSummary(
            date=d, provider=provider, session_count=len(profiles),
            total_cost_usd=round(total_cost, 4), total_messages=total_msgs,
            cost_per_message=round(total_cost / max(total_msgs, 1), 4),
        ))
    return result


@dataclass(frozen=True)
class WorkPattern:
    work_kind: str
    session_count: int
    total_hours: float
    total_cost_usd: float
    top_projects: tuple[str, ...]


def work_pattern(*, start: date, end: date) -> list[WorkPattern]:
    """What kinds of work get AI assistance? Aggregated by work_event_kind."""
    by_kind: dict[str, dict] = defaultdict(
        lambda: {"sessions": 0, "ms": 0, "cost": 0.0, "projects": Counter()}
    )
    for day in day_session_summaries(start=start, end=end):
        for kind, count in day.work_event_breakdown.items():
            bucket = by_kind[kind]
            bucket["sessions"] += count
            bucket["ms"] += 0
            bucket["cost"] += 0.0
            for repo in day.repos_active:
                bucket["projects"][repo] += count
    if by_kind:
        return [
            WorkPattern(
                work_kind=kind,
                session_count=b["sessions"],
                total_hours=0.0,
                total_cost_usd=0.0,
                top_projects=tuple(p for p, _ in b["projects"].most_common(5)),
            )
            for kind, b in sorted(by_kind.items(), key=lambda x: -x[1]["sessions"])
        ]

    for p in iter_session_profiles():
        d = p.canonical_session_date
        if d is None:
            continue
        if d < start or d > end:
            continue
        kind = p.work_event_kind or "unclassified"
        bucket = by_kind[kind]
        bucket["sessions"] += 1
        bucket["ms"] += p.engaged_duration_ms
        bucket["cost"] += p.total_cost_usd
        for proj in p.work_event_projects:
            bucket["projects"][proj] += 1

    result: list[WorkPattern] = []
    for kind, b in sorted(by_kind.items(), key=lambda x: -x[1]["ms"]):
        result.append(WorkPattern(
            work_kind=kind, session_count=b["sessions"],
            total_hours=round(b["ms"] / 3_600_000, 2),
            total_cost_usd=round(b["cost"], 4),
            top_projects=tuple(p for p, _ in b["projects"].most_common(5)),
        ))
    return result
