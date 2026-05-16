"""AI chat source: session profiles, daily activity, cost, work patterns.

Reads Polylogue's typed Python facade (SyncPolylogue). Product tables are
owned by Polylogue; Lynchpin consumes the public insight/readiness surfaces.

Covers all providers: Claude (claude-ai, claude-code), ChatGPT, Codex, Gemini.

"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator, Optional

from ..core.parse import parse_datetime as _parse_dt
from ..core.projects import canonical_project_name
from .polylogue_client import _default_polylogue_db_path, _polylogue_client
from .polylogue_models import (
    ChatDayActivity,
    ConversationLineage,
    ConversationTranscript,
    CostSummary,
    DaySessionSummary,
    MessageRecord,
    PolylogueReadiness,
    SessionProfile,
    WorkEvent,
    WorkPattern,
    _WorkPatternBucket,
)

logger = logging.getLogger(__name__)

__all__ = [
    "SessionProfile",
    "ChatDayActivity",
    "CostSummary",
    "WorkPattern",
    "WorkEvent",
    "DaySessionSummary",
    "MessageRecord",
    "ConversationTranscript",
    "ConversationLineage",
    "PolylogueReadiness",
    "iter_session_profiles",
    "session_profiles_for_date",
    "conversation_transcripts",
    "conversation_lineages",
    "work_events",
    "day_session_summaries",
    "archive_readiness",
    "daily_activity",
    "cost_summary",
    "work_pattern",
    "archive_stats",
]


def _project_names_from_provider_meta(value: object) -> tuple[str, ...]:
    if not value:
        return ()
    try:
        meta = json.loads(str(value))
    except json.JSONDecodeError:
        return ()
    if not isinstance(meta, dict):
        return ()

    projects: list[str] = []
    git = meta.get("git")
    if isinstance(git, dict):
        repo_url = str(git.get("repository_url") or "")
        if repo_url:
            name = canonical_project_name(repo_url)
            if name:
                projects.append(name)

    for path in meta.get("working_directories") or []:
        project = canonical_project_name(str(path)) or _project_from_path(str(path))
        if project:
            projects.append(project)

    return tuple(dict.fromkeys(projects))


def _json_list(value: object) -> tuple[str, ...]:
    if not value:
        return ()
    try:
        decoded = json.loads(str(value))
    except json.JSONDecodeError:
        return ()
    if not isinstance(decoded, list):
        return ()
    return tuple(str(item) for item in decoded if item not in (None, ""))


def _project_from_path(path: str) -> str | None:
    project = canonical_project_name(path)
    if project:
        return project
    if path.startswith("/tmp/"):
        name = Path(path).name
        return canonical_project_name(name)
    return None


def _canonical_projects(values: object) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        return ()
    projects = [canonical_project_name(value) for value in values]
    return tuple(dict.fromkeys(project for project in projects if project))


def archive_readiness(*, include_heavy_counts: bool = False) -> PolylogueReadiness:
    """Report whether Lynchpin can use the current local Polylogue archive."""
    db = _default_polylogue_db_path()
    try:
        from polylogue.insights.readiness import InsightReadinessQuery

        report = _polylogue_client().insight_readiness_report(
            InsightReadinessQuery(
                insights=(
                    "session_profiles",
                    "day_session_summaries",
                    "session_work_events",
                )
            )
        )
    except Exception as exc:
        return PolylogueReadiness(
            db_path=db,
            status="unavailable",
            reason=f"polylogue readiness facade failed: {exc}",
            conversation_count=0,
            message_count=None,
            conversation_stats_count=0,
            session_profile_count=0,
            day_summary_count=0,
            work_event_count=0,
            provider_event_count=None,
            derives_profiles_from_base_tables=False,
            derives_day_summaries_from_profiles=False,
        )

    entries = {entry.insight_name: entry for entry in report.insights}
    profile = entries.get("session_profiles")
    day = entries.get("day_session_summaries")
    work_event = entries.get("session_work_events")
    profile_count = _readiness_row_count(profile)
    day_count = _readiness_row_count(day)
    work_event_count = _readiness_row_count(work_event)
    degraded_entries = tuple(
        entry
        for entry in (profile, day, work_event)
        if entry is not None and entry.verdict not in {"ready", "empty"}
    )

    if (
        profile_count > 0
        and day_count > 0
        and work_event_count > 0
        and not degraded_entries
    ):
        status = "ready"
        reason = (
            "materialized profile, day-summary, and work-event products are populated"
        )
    elif (
        report.total_conversations > 0
        or profile_count > 0
        or day_count > 0
        or work_event_count > 0
    ):
        status = "degraded"
        reason = _readiness_reason(entries)
    else:
        status = "unavailable"
        reason = "polylogue archive tables are empty"

    return PolylogueReadiness(
        db_path=db,
        status=status,
        reason=reason,
        conversation_count=report.total_conversations,
        message_count=None,
        conversation_stats_count=profile.expected_row_count
        if profile is not None and profile.expected_row_count is not None
        else 0,
        session_profile_count=profile_count,
        day_summary_count=day_count,
        work_event_count=work_event_count,
        provider_event_count=None,
        derives_profiles_from_base_tables=False,
        derives_day_summaries_from_profiles=False,
    )


def _readiness_row_count(entry: Any | None) -> int:
    return int(entry.row_count) if entry is not None else 0


def _readiness_reason(entries: dict[str, Any]) -> str:
    missing = []
    degraded = []
    for name in ("session_profiles", "day_session_summaries", "session_work_events"):
        entry = entries.get(name)
        if entry is None or entry.row_count == 0:
            missing.append(name)
        elif entry.verdict not in {"ready", "empty"}:
            degraded.append(f"{name}={entry.verdict}")
    parts = []
    if missing:
        parts.append("missing or empty products: " + ", ".join(missing))
    if degraded:
        parts.append("degraded products: " + ", ".join(degraded))
    return "; ".join(parts) if parts else "polylogue insight readiness is degraded"


# ══════════════════════════════════════════════════════════════════════════════
# Data types
# ══════════════════════════════════════════════════════════════════════════════


_cached_profiles: list[SessionProfile] | None = None


def _profiles_from_facade() -> list[SessionProfile] | None:
    """Load session profiles via the SyncPolylogue facade.

    Maps SessionProfileInsight (evidence + inference payloads) → SessionProfile.

    work_event_kind: most-common kind across inference.work_events documents;
    falls back to inference.support_level-aware heuristics are not used —
    the work_events list is the typed surface.

    work_event_projects: prefer inference.repo_names (inferred canonical
    names); fall back to evidence.repo_paths / cwd_paths via
    _canonical_projects().
    """
    try:
        from polylogue.insights.archive import SessionProfileInsightQuery

        insights = _polylogue_client().list_session_profile_insights(
            SessionProfileInsightQuery(limit=None)
        )
    except Exception as exc:
        logger.warning("polylogue list_session_profile_insights failed: %s", exc)
        return None

    return [_session_profile_from_insight(insight) for insight in insights]


def _session_profile_from_insight(insight: Any) -> SessionProfile:
    evidence = insight.evidence
    inference = insight.inference

    first_message_at: Optional[datetime] = None
    last_message_at: Optional[datetime] = None
    canonical_session_date: Optional[date] = None
    if evidence is not None:
        first_message_at = _parse_dt(evidence.first_message_at)
        last_message_at = _parse_dt(evidence.last_message_at)
        if evidence.canonical_session_date:
            try:
                canonical_session_date = date.fromisoformat(
                    evidence.canonical_session_date
                )
            except ValueError:
                canonical_session_date = None

    work_event_kind: Optional[str] = None
    if inference is not None and inference.work_events:
        kinds = [
            str(ev["kind"])
            for ev in inference.work_events
            if isinstance(ev, dict) and ev.get("kind")
        ]
        if kinds:
            work_event_kind = Counter(kinds).most_common(1)[0][0]

    projects: tuple[str, ...] = ()
    if inference is not None:
        projects = _canonical_projects(inference.repo_names)
    if not projects and evidence is not None:
        projects = _canonical_projects(evidence.repo_paths or evidence.cwd_paths)

    auto_tags: tuple[str, ...] = ()
    if inference is not None:
        auto_tags = tuple(str(tag) for tag in inference.auto_tags if tag)

    message_count = 0
    word_count = 0
    total_cost_usd = 0.0
    cost_is_estimated = False
    tool_use_count = 0
    thinking_count = 0
    substantive_count = 0
    attachment_count = 0
    wall_duration_ms = 0
    if evidence is not None:
        message_count = evidence.message_count
        word_count = evidence.word_count
        total_cost_usd = evidence.total_cost_usd
        cost_is_estimated = evidence.cost_is_estimated
        tool_use_count = evidence.tool_use_count
        thinking_count = evidence.thinking_count
        substantive_count = evidence.substantive_count
        attachment_count = evidence.attachment_count
        wall_duration_ms = evidence.wall_duration_ms

    engaged_duration_ms = 0
    work_event_count = 0
    phase_count = 0
    if inference is not None:
        engaged_duration_ms = inference.engaged_duration_ms
        work_event_count = inference.work_event_count
        phase_count = inference.phase_count

    return SessionProfile(
        conversation_id=insight.conversation_id,
        provider=insight.provider_name,
        title=str(insight.title or ""),
        message_count=message_count,
        word_count=word_count,
        first_message_at=first_message_at,
        last_message_at=last_message_at,
        engaged_duration_ms=engaged_duration_ms,
        wall_duration_ms=wall_duration_ms,
        work_event_kind=work_event_kind,
        work_event_projects=projects,
        total_cost_usd=total_cost_usd,
        canonical_session_date=canonical_session_date,
        tool_use_count=tool_use_count,
        thinking_count=thinking_count,
        auto_tags=auto_tags,
        substantive_count=substantive_count,
        attachment_count=attachment_count,
        work_event_count=work_event_count,
        phase_count=phase_count,
        cost_is_estimated=cost_is_estimated,
    )


def _load_profiles() -> list[SessionProfile]:
    """Load session profiles from the Polylogue facade."""
    global _cached_profiles
    if _cached_profiles is not None:
        return _cached_profiles
    _cached_profiles = _profiles_from_facade() or []
    return _cached_profiles


def iter_session_profiles() -> Iterator[SessionProfile]:
    """Yield all session profiles from polylogue archive."""
    yield from _load_profiles()


def session_profiles_for_date(*, start: date, end: date) -> list[SessionProfile]:
    bounded = _session_profiles_from_facade(start=start, end=end)
    if bounded is not None:
        return bounded

    result: list[SessionProfile] = []
    for profile in iter_session_profiles():
        session_date = profile.canonical_session_date
        if session_date is None:
            stamp = profile.last_message_at or profile.first_message_at
            if stamp is None:
                continue
            session_date = stamp.date()
        if start <= session_date <= end:
            result.append(profile)
    return result


def _session_profiles_from_facade(
    *, start: date, end: date
) -> list[SessionProfile] | None:
    """Read date-bounded session profiles through Polylogue's public facade."""
    try:
        from polylogue.insights.archive import SessionProfileInsightQuery

        insights = _polylogue_client().list_session_profile_insights(
            SessionProfileInsightQuery(
                session_date_since=start.isoformat(),
                session_date_until=end.isoformat(),
                limit=None,
            )
        )
    except Exception as exc:
        logger.warning("polylogue bounded session profile facade read failed: %s", exc)
        return None

    return [_session_profile_from_insight(insight) for insight in insights]


@lru_cache(maxsize=1)
def _token_encoder() -> Any | None:
    try:
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None


def _approx_tokens(text: str) -> int:
    if not text:
        return 0
    encoder = _token_encoder()
    if encoder is None:
        return max(len(text.split()), 1)
    try:
        return len(encoder.encode(text))
    except Exception:
        return max(len(text.split()), 1)


def _approx_tokens_batch(texts: list[str]) -> list[int]:
    if not texts:
        return []
    encoder = _token_encoder()
    if encoder is None:
        return [max(len(text.split()), 1) if text else 0 for text in texts]
    if hasattr(encoder, "encode_ordinary_batch"):
        counts: list[int] = []
        for idx in range(0, len(texts), 256):
            chunk = texts[idx : idx + 256]
            try:
                counts.extend(
                    len(tokens) for tokens in encoder.encode_ordinary_batch(chunk)
                )
                continue
            except Exception:
                pass
            counts.extend(_approx_tokens(text) for text in chunk)
        return counts
    return [_approx_tokens(text) for text in texts]


def _classify_message_kind(role: str, text: str) -> str:
    role_name = (role or "unknown").strip().lower()
    if role_name != "user":
        return role_name or "unknown"
    stripped = (text or "").strip()
    if not stripped:
        return "empty"
    if stripped.startswith("<local-command-caveat>"):
        return "caveat"
    if (
        stripped.startswith("<command-name>")
        or stripped.startswith("<command-message>")
        or stripped.startswith("<command-args>")
    ):
        return "control"
    return "prompt"


def conversation_transcripts(*, start: date, end: date) -> list[ConversationTranscript]:
    """Build message-level transcripts for conversations in the date range.

    Uses bulk_get_messages() (Polylogue P.2) to fetch all messages in one batch
    read rather than per-conversation calls. Messages within each conversation are
    returned in sort_key order by the archive layer.
    """
    profiles = session_profiles_for_date(start=start, end=end)
    if not profiles:
        return []
    by_conversation = {profile.conversation_id: profile for profile in profiles}

    start_iso = start.isoformat()
    end_iso = end.isoformat()
    try:
        messages_by_id = _polylogue_client().bulk_get_messages(
            list(by_conversation.keys()),
            since=start_iso,
            until=end_iso,
        )
    except Exception as exc:
        logger.warning("polylogue bulk_get_messages failed: %s", exc)
        return []

    transcripts: list[ConversationTranscript] = []
    for conversation_id, profile in by_conversation.items():
        raw_messages = messages_by_id.get(conversation_id)
        if raw_messages is None:
            # Profile exists but bulk fetch returned no entry — emit empty transcript.
            transcripts.append(
                ConversationTranscript(
                    conversation_id=conversation_id,
                    provider=profile.provider,
                    title=profile.title,
                    canonical_session_date=profile.canonical_session_date,
                    first_message_at=profile.first_message_at,
                    last_message_at=profile.last_message_at,
                    messages=(),
                    user_prompt_count=0,
                    user_prompt_tokens=0,
                    dialogue_tokens=0,
                    all_message_tokens=0,
                )
            )
            continue

        texts = [str(msg.text or "") for msg in raw_messages]
        token_counts = _approx_tokens_batch(texts)

        message_records: list[MessageRecord] = []
        for ordinal, (msg, text, approx_tokens) in enumerate(
            zip(raw_messages, texts, token_counts)
        ):
            role_str = str(msg.role)
            kind = _classify_message_kind(role_str, text)
            # has_tool_use / has_thinking: derive from content_blocks presence
            has_tool_use = any(
                b.get("type") in {"tool_use", "tool_result"}
                for b in (msg.content_blocks or [])
                if isinstance(b, dict)
            )
            has_thinking = any(
                b.get("type") == "thinking"
                for b in (msg.content_blocks or [])
                if isinstance(b, dict)
            )
            word_count = len(text.split()) if text else 0
            message_records.append(
                MessageRecord(
                    conversation_id=conversation_id,
                    provider=profile.provider,
                    role=role_str,
                    kind=kind,
                    ordinal=ordinal,
                    text=text,
                    word_count=word_count,
                    has_tool_use=has_tool_use,
                    has_thinking=has_thinking,
                    approx_tokens=approx_tokens,
                )
            )

        messages = tuple(message_records)
        user_prompt_count = sum(1 for m in messages if m.kind == "prompt")
        user_prompt_tokens = sum(
            m.approx_tokens for m in messages if m.kind == "prompt"
        )
        dialogue_tokens = sum(
            m.approx_tokens for m in messages if m.kind in {"prompt", "assistant"}
        )
        all_tokens = sum(m.approx_tokens for m in messages)
        transcripts.append(
            ConversationTranscript(
                conversation_id=conversation_id,
                provider=profile.provider,
                title=profile.title,
                canonical_session_date=profile.canonical_session_date,
                first_message_at=profile.first_message_at,
                last_message_at=profile.last_message_at,
                messages=messages,
                user_prompt_count=user_prompt_count,
                user_prompt_tokens=user_prompt_tokens,
                dialogue_tokens=dialogue_tokens,
                all_message_tokens=all_tokens,
            )
        )

    transcripts.sort(
        key=lambda item: item.first_message_at.timestamp()
        if item.first_message_at
        else float("-inf")
    )
    return transcripts


# ── M.15: conversation fork / branch lineage ─────────────────────────────────


def conversation_lineages(
    *,
    start: Optional[date] = None,
    end: Optional[date] = None,
    branch_types: Optional[tuple[str, ...]] = None,
) -> list[ConversationLineage]:
    """Read parent/branch attribution from the durable archive.

    Uses list_summaries() (ConversationSummary objects) which carry parent_id
    and branch_type without materialising the full message collection.

    ``branch_types`` filter examples:
      - ``("fork", "sidechain")`` — only the divergence cases
      - ``("subagent",)`` — agent-spawned children
      - ``None`` — every row, including ``branch_type=NULL`` ("normal")

    Date bounds filter on ``created_at``; rows without a timestamp pass
    through when bounds aren't supplied.
    """
    try:
        since = start.isoformat() if start is not None else None
        until = end.isoformat() if end is not None else None
        summaries = _polylogue_client().list_summaries(since=since, until=until)
    except Exception as exc:
        logger.warning("polylogue list_summaries for lineages failed: %s", exc)
        return []

    result: list[ConversationLineage] = []
    for summary in summaries:
        # Apply branch_types filter Python-side.
        if branch_types is not None:
            branch_val = (
                str(summary.branch_type) if summary.branch_type is not None else None
            )
            if branch_val not in branch_types:
                continue
        result.append(
            ConversationLineage(
                conversation_id=str(summary.id),
                parent_conversation_id=(
                    str(summary.parent_id) if summary.parent_id is not None else None
                ),
                branch_type=str(summary.branch_type)
                if summary.branch_type is not None
                else None,
                provider=str(summary.provider),
                title=str(summary.title or ""),
                created_at=summary.created_at,
            )
        )

    result.sort(
        key=lambda item: (item.created_at or datetime.min, item.conversation_id)
    )
    return result


def archive_stats() -> dict[str, object]:
    """Quick stats from Polylogue via the SyncPolylogue facade.

    Note: ArchiveStats does not expose word_count or date_range (Arc P.5 will
    add date_range upstream). These fields are dropped from the returned dict
    until then; no caller currently reads them.
    """
    try:
        stats = _polylogue_client().stats()
    except Exception as exc:
        logger.warning("polylogue stats failed: %s", exc)
        return {}
    return {
        "conversation_count": stats.total_conversations,
        "message_count": stats.total_messages,
        "providers": dict(stats.providers),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Work events (sub-session temporal segments)
# ══════════════════════════════════════════════════════════════════════════════

_cached_work_events: list[WorkEvent] | None = None


def _work_events_from_facade(
    *,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> list[WorkEvent] | None:
    """Load Polylogue's durable work-event insights via the typed facade."""
    from polylogue.insights.archive import SessionWorkEventInsightQuery

    query_model: Any = SessionWorkEventInsightQuery
    query = query_model(
        session_date_since=start.isoformat() if start else None,
        session_date_until=end.isoformat() if end else None,
        limit=None,
    )
    try:
        insights = _polylogue_client().list_session_work_event_insights(query)
    except Exception as exc:
        logger.warning("polylogue list_session_work_event_insights failed: %s", exc)
        return None

    events: list[WorkEvent] = []
    for insight in insights:
        ev = insight.evidence
        inf = insight.inference
        start = _parse_dt(ev.start_time) if ev.start_time else None
        end = _parse_dt(ev.end_time) if ev.end_time else None
        events.append(
            WorkEvent(
                event_id=insight.event_id,
                conversation_id=insight.conversation_id,
                provider=insight.provider_name,
                kind=str(inf.kind or "unknown"),
                confidence=float(inf.confidence),
                start=start,
                end=end,
                duration_ms=int(ev.duration_ms),
                file_paths=tuple(ev.file_paths),
                tools_used=tuple(ev.tools_used),
                summary=str(inf.summary or ""),
            )
        )
    return events


def work_events(
    *, start: Optional[date] = None, end: Optional[date] = None
) -> list[WorkEvent]:
    """Load work events from the Polylogue facade."""
    if start is not None or end is not None:
        bounded = _work_events_from_facade(start=start, end=end)
        if bounded is not None:
            return bounded

    global _cached_work_events
    if _cached_work_events is None:
        _cached_work_events = _work_events_from_facade() or []

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
# Day session summaries
# ══════════════════════════════════════════════════════════════════════════════

_cached_day_summaries: list[DaySessionSummary] | None = None


def _day_summaries_from_facade() -> list[DaySessionSummary] | None:
    """Read Polylogue's day summary insights via the typed facade.

    DaySessionSummaryPayload is 1:1 with lynchpin's DaySessionSummary.
    The facade handles the base-tables fallback internally when the product
    table is unmaterialised.
    """
    try:
        from polylogue.insights.archive import DaySessionSummaryInsightQuery

        insights = _polylogue_client().list_day_session_summary_insights(
            DaySessionSummaryInsightQuery(limit=None)
        )
    except Exception as exc:
        logger.warning("polylogue list_day_session_summary_insights failed: %s", exc)
        return None

    summaries: list[DaySessionSummary] = []
    for insight in insights:
        payload = insight.summary
        try:
            day = date.fromisoformat(payload.date)
        except (ValueError, TypeError):
            logger.debug("skipping day summary with unparseable date: %r", payload.date)
            continue
        summaries.append(
            DaySessionSummary(
                date=day,
                session_count=payload.session_count,
                total_cost_usd=payload.total_cost_usd,
                total_messages=payload.total_messages,
                total_words=payload.total_words,
                work_event_breakdown=dict(payload.work_event_breakdown),
                repos_active=tuple(payload.repos_active),
                providers=dict(payload.providers),
            )
        )
    return summaries


def day_session_summaries(
    *, start: Optional[date] = None, end: Optional[date] = None
) -> list[DaySessionSummary]:
    """Daily session aggregation from Polylogue's durable product tables."""
    global _cached_day_summaries
    if _cached_day_summaries is None:
        _cached_day_summaries = _day_summaries_from_facade() or []

    summaries = _cached_day_summaries
    if start or end:
        return [
            s
            for s in summaries
            if (not start or s.date >= start) and (not end or s.date <= end)
        ]
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
        result.append(
            ChatDayActivity(
                date=day,
                provider=provider,
                session_count=len(profiles),
                total_messages=total_messages,
                total_words=total_words,
                engaged_minutes=round(engaged_ms / 60_000, 1),
                total_wall_minutes=round(wall_ms / 60_000, 1),
                dominant_work_kind=work_kinds.most_common(1)[0][0]
                if work_kinds
                else None,
                projects=tuple(sorted(projects)),
            )
        )
    return result


def _daily_activity_from_day_summaries(
    *, start: date, end: date
) -> list[ChatDayActivity]:
    """Fallback daily activity from durable day summaries when profiles are unavailable."""
    result: list[ChatDayActivity] = []
    for summary in day_session_summaries(start=start, end=end):
        if not summary.providers:
            providers = {"unknown": summary.session_count}
        else:
            providers = summary.providers
        dominant = None
        if summary.work_event_breakdown:
            dominant = max(
                summary.work_event_breakdown,
                key=lambda kind: summary.work_event_breakdown[kind],
            )
        total_sessions = max(summary.session_count, 1)
        for provider, count in sorted(providers.items()):
            share = count / total_sessions
            result.append(
                ChatDayActivity(
                    date=summary.date,
                    provider=provider,
                    session_count=count,
                    total_messages=round(summary.total_messages * share),
                    total_words=round(summary.total_words * share),
                    engaged_minutes=0.0,
                    total_wall_minutes=0.0,
                    dominant_work_kind=dominant,
                    projects=summary.repos_active,
                )
            )
    return result


# ══════════════════════════════════════════════════════════════════════════════
# Cost and work pattern analytics
# ══════════════════════════════════════════════════════════════════════════════


def cost_summary(*, start: date, end: date) -> list[CostSummary]:
    """Daily LLM API-equivalent spend per provider.

    Policy (durable): the dollar number this returns is API-equivalent token
    cost — *not* what the user actually pays. Real work runs on flat-rate
    subscriptions (Claude Max, ChatGPT Plus/Pro, etc.) where this estimate is
    decoupled from billing. Treat output as ad-hoc curiosity. Never collapse
    into context packs, movement summaries, or velocity windows; tokens (Arc L
    `TokenEconomySummary`) and subscription quota (Polylogue #870) are the
    correct effort/intensity surfaces.
    """
    summary_result: list[CostSummary] = []
    for day in day_session_summaries(start=start, end=end):
        if day.total_cost_usd <= 0:
            continue
        total_sessions = max(day.session_count, 1)
        for provider, count in sorted(
            (day.providers or {"unknown": day.session_count}).items()
        ):
            share = count / total_sessions
            messages = round(day.total_messages * share)
            cost = day.total_cost_usd * share
            summary_result.append(
                CostSummary(
                    date=day.date,
                    provider=provider,
                    session_count=count,
                    total_cost_usd=round(cost, 4),
                    total_messages=messages,
                    cost_per_message=round(cost / max(messages, 1), 4),
                )
            )
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
        result.append(
            CostSummary(
                date=d,
                provider=provider,
                session_count=len(profiles),
                total_cost_usd=round(total_cost, 4),
                total_messages=total_msgs,
                cost_per_message=round(total_cost / max(total_msgs, 1), 4),
            )
        )
    return result


def work_pattern(*, start: date, end: date) -> list[WorkPattern]:
    """What kinds of work get AI assistance? Aggregated by work_event_kind."""
    by_kind: defaultdict[str, _WorkPatternBucket] = defaultdict(_WorkPatternBucket)
    for day in day_session_summaries(start=start, end=end):
        for kind, count in day.work_event_breakdown.items():
            bucket = by_kind[kind]
            bucket.sessions += count
            for repo in day.repos_active:
                bucket.projects[repo] += count
    if by_kind:
        return [
            WorkPattern(
                work_kind=kind,
                session_count=b.sessions,
                total_hours=0.0,
                total_cost_usd=0.0,
                top_projects=tuple(p for p, _ in b.projects.most_common(5)),
            )
            for kind, b in sorted(by_kind.items(), key=lambda x: -x[1].sessions)
        ]

    for p in iter_session_profiles():
        d = p.canonical_session_date
        if d is None:
            continue
        if d < start or d > end:
            continue
        kind = p.work_event_kind or "unclassified"
        bucket = by_kind[kind]
        bucket.sessions += 1
        bucket.ms += p.engaged_duration_ms
        bucket.cost += p.total_cost_usd
        for proj in p.work_event_projects:
            bucket.projects[proj] += 1

    result: list[WorkPattern] = []
    for kind, b in sorted(by_kind.items(), key=lambda x: -x[1].ms):
        result.append(
            WorkPattern(
                work_kind=kind,
                session_count=b.sessions,
                total_hours=round(b.ms / 3_600_000, 2),
                total_cost_usd=round(b.cost, 4),
                top_projects=tuple(p for p, _ in b.projects.most_common(5)),
            )
        )
    return result
