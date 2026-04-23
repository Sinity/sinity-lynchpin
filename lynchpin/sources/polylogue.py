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
import os
import sqlite3
import subprocess
from functools import lru_cache
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
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
    "MessageRecord",
    "ConversationTranscript",
    "iter_session_profiles",
    "session_profiles_for_date",
    "conversation_transcripts",
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


@dataclass(frozen=True)
class MessageRecord:
    conversation_id: str
    provider: str
    role: str
    kind: str
    ordinal: int
    text: str
    word_count: int
    has_tool_use: bool
    has_thinking: bool
    approx_tokens: int


@dataclass(frozen=True)
class ConversationTranscript:
    conversation_id: str
    provider: str
    title: str
    canonical_session_date: Optional[date]
    first_message_at: Optional[datetime]
    last_message_at: Optional[datetime]
    messages: tuple[MessageRecord, ...]
    user_prompt_count: int
    user_prompt_tokens: int
    dialogue_tokens: int
    all_message_tokens: int


_POLYLOGUE_PYTHON = "polylogue-python"
_POLYLOGUE_CLI = "polylogue"


def _default_polylogue_db_path() -> Path:
    from ..core.config import get_config

    return get_config().polylogue_db


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
    substantive_count: int = 0
    attachment_count: int = 0
    work_event_count: int = 0
    phase_count: int = 0
    cost_is_estimated: bool = False


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
                    "substantive_count": evd.get("substantive_count", 0),
                    "attachment_count": evd.get("attachment_count", 0),
                    "work_event_count": evd.get("work_event_count", 0),
                    "phase_count": evd.get("phase_count", 0),
                    "word_count": evd.get("word_count", 0),
                    "first_message_at": str(evd.get("first_message_at")) if evd.get("first_message_at") else None,
                    "last_message_at": str(evd.get("last_message_at")) if evd.get("last_message_at") else None,
                    "engaged_duration_ms": inf_engaged_ms,
                    "wall_duration_ms": evd.get("wall_duration_ms", 0) or 0,
                    "work_event_kind": inf_kind,
                    "work_event_projects": inf_projects,
                    "total_cost_usd": evd.get("total_cost_usd", 0) or 0,
                    "cost_is_estimated": bool(evd.get("cost_is_estimated", False)),
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


def _profiles_from_sqlite() -> list[SessionProfile] | None:
    db = _default_polylogue_db_path()
    if not db.exists():
        return None
    try:
        with sqlite3.connect(str(db)) as conn:
            conn.row_factory = sqlite3.Row
            table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='session_profiles'"
            ).fetchone()
            if table is None:
                return None
            rows = conn.execute(
                """
                SELECT conversation_id, provider_name, title, message_count,
                       substantive_count, attachment_count, work_event_count,
                       phase_count, word_count, first_message_at, last_message_at,
                       engaged_duration_ms, wall_duration_ms, total_cost_usd,
                       cost_is_estimated, canonical_session_date, tool_use_count,
                       thinking_count, repo_names_json, repo_paths_json, auto_tags_json,
                       evidence_payload_json, inference_payload_json
                FROM session_profiles
                ORDER BY canonical_session_date DESC, first_message_at DESC, conversation_id
                """
            ).fetchall()
    except sqlite3.Error as exc:
        logger.warning("polylogue session_profiles sqlite read failed: %s", exc)
        return None

    profiles: list[SessionProfile] = []
    for row in rows:
        session_date = None
        if row["canonical_session_date"]:
            try:
                session_date = date.fromisoformat(str(row["canonical_session_date"]))
            except ValueError:
                session_date = None
        repo_names = _json_list(row["repo_names_json"])
        repo_paths = _json_list(row["repo_paths_json"])
        projects = repo_names or repo_paths
        auto_tags = _json_list(row["auto_tags_json"])
        work_event_kind = None
        if row["inference_payload_json"]:
            try:
                inference = json.loads(row["inference_payload_json"] or "{}")
            except json.JSONDecodeError:
                inference = {}
            work_event_kind = str(
                inference.get("primary_work_kind")
                or inference.get("kind")
                or ""
            ) or None
            if not auto_tags:
                auto_tags = tuple(str(tag) for tag in (inference.get("auto_tags") or []) if tag)
            if not projects:
                projects = tuple(
                    str(path)
                    for path in (inference.get("repo_names") or inference.get("canonical_projects") or [])
                    if path
                )
        if row["evidence_payload_json"] and not projects:
            try:
                evidence = json.loads(row["evidence_payload_json"] or "{}")
            except json.JSONDecodeError:
                evidence = {}
            projects = tuple(
                str(path)
                for path in (evidence.get("repo_names") or evidence.get("repo_paths") or evidence.get("cwd_paths") or [])
                if path
            )

        profiles.append(SessionProfile(
            conversation_id=str(row["conversation_id"]),
            provider=str(row["provider_name"] or ""),
            title=str(row["title"] or ""),
            message_count=int(row["message_count"] or 0),
            word_count=int(row["word_count"] or 0),
            first_message_at=_parse_dt(row["first_message_at"]),
            last_message_at=_parse_dt(row["last_message_at"]),
            engaged_duration_ms=int(row["engaged_duration_ms"] or 0),
            wall_duration_ms=int(row["wall_duration_ms"] or 0),
            work_event_kind=work_event_kind,
            work_event_projects=tuple(str(project) for project in projects if project),
            total_cost_usd=float(row["total_cost_usd"] or 0),
            canonical_session_date=session_date,
            tool_use_count=int(row["tool_use_count"] or 0),
            thinking_count=int(row["thinking_count"] or 0),
            auto_tags=tuple(str(tag) for tag in auto_tags if tag),
            substantive_count=int(row["substantive_count"] or 0),
            attachment_count=int(row["attachment_count"] or 0),
            work_event_count=int(row["work_event_count"] or 0),
            phase_count=int(row["phase_count"] or 0),
            cost_is_estimated=bool(row["cost_is_estimated"]),
        ))
    return profiles


def _load_profiles() -> list[SessionProfile]:
    """Load session profiles from Polylogue, preferring the durable sqlite product."""
    global _cached_profiles
    if _cached_profiles is not None:
        return _cached_profiles

    sqlite_profiles = _profiles_from_sqlite()
    if sqlite_profiles is not None:
        _cached_profiles = sqlite_profiles
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
            substantive_count=r.get("substantive_count", 0) or 0,
            attachment_count=r.get("attachment_count", 0) or 0,
            work_event_count=r.get("work_event_count", 0) or 0,
            phase_count=r.get("phase_count", 0) or 0,
            cost_is_estimated=bool(r.get("cost_is_estimated", False)),
        ))

    _cached_profiles = profiles
    return _cached_profiles


def iter_session_profiles() -> Iterator[SessionProfile]:
    """Yield all session profiles from polylogue archive."""
    yield from _load_profiles()


def session_profiles_for_date(*, start: date, end: date) -> list[SessionProfile]:
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


@lru_cache(maxsize=1)
def _token_encoder():
    if os.environ.get("LYNCHPIN_EXACT_TOKEN_ESTIMATES") != "1":
        return None
    try:
        import tiktoken
    except ImportError:
        return None
    try:
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
            chunk = texts[idx:idx + 256]
            try:
                counts.extend(len(tokens) for tokens in encoder.encode_ordinary_batch(chunk))
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
    profiles = session_profiles_for_date(start=start, end=end)
    if not profiles:
        return []
    by_conversation = {profile.conversation_id: profile for profile in profiles}
    db = _default_polylogue_db_path()
    if not db.exists():
        return []

    try:
        with sqlite3.connect(str(db)) as conn:
            conn.row_factory = sqlite3.Row
            if len(by_conversation) <= 500:
                placeholders = ",".join("?" for _ in by_conversation)
                rows = conn.execute(
                    f"""
                    SELECT conversation_id, provider_name, role, text, word_count,
                           has_tool_use, has_thinking, sort_key, rowid
                    FROM messages
                    WHERE conversation_id IN ({placeholders})
                    ORDER BY conversation_id, sort_key, rowid
                    """,
                    tuple(by_conversation.keys()),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT m.conversation_id, m.provider_name, m.role, m.text, m.word_count,
                           m.has_tool_use, m.has_thinking, m.sort_key, m.rowid
                    FROM messages AS m
                    JOIN session_profiles AS sp
                      ON sp.conversation_id = m.conversation_id
                    WHERE (
                        sp.canonical_session_date BETWEEN ? AND ?
                        OR (
                            sp.canonical_session_date IS NULL
                            AND date(COALESCE(sp.last_message_at, sp.first_message_at)) BETWEEN ? AND ?
                        )
                    )
                    ORDER BY m.conversation_id, m.sort_key, m.rowid
                    """,
                    (
                        start.isoformat(),
                        end.isoformat(),
                        start.isoformat(),
                        end.isoformat(),
                    ),
                ).fetchall()
    except sqlite3.Error as exc:
        logger.warning("polylogue message read failed: %s", exc)
        return []

    texts = [str(row["text"] or "") for row in rows]
    token_counts = _approx_tokens_batch(texts)

    grouped: dict[str, list[MessageRecord]] = defaultdict(list)
    for row, text, approx_tokens in zip(rows, texts, token_counts):
        kind = _classify_message_kind(str(row["role"] or "unknown"), text)
        grouped[str(row["conversation_id"])].append(MessageRecord(
            conversation_id=str(row["conversation_id"]),
            provider=str(row["provider_name"] or ""),
            role=str(row["role"] or "unknown"),
            kind=kind,
            ordinal=len(grouped[str(row["conversation_id"])]),
            text=text,
            word_count=int(row["word_count"] or 0),
            has_tool_use=bool(row["has_tool_use"]),
            has_thinking=bool(row["has_thinking"]),
            approx_tokens=approx_tokens,
        ))

    transcripts: list[ConversationTranscript] = []
    for conversation_id, profile in by_conversation.items():
        messages = tuple(grouped.get(conversation_id, ()))
        user_prompt_count = sum(1 for message in messages if message.kind == "prompt")
        user_prompt_tokens = sum(message.approx_tokens for message in messages if message.kind == "prompt")
        dialogue_tokens = sum(
            message.approx_tokens
            for message in messages
            if message.kind in {"prompt", "assistant"}
        )
        all_tokens = sum(message.approx_tokens for message in messages)
        transcripts.append(ConversationTranscript(
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
        ))
    transcripts.sort(key=lambda item: item.first_message_at.timestamp() if item.first_message_at else float("-inf"))
    return transcripts


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


def _json_list(value: object) -> tuple[str, ...]:
    if not value:
        return ()
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(str(item) for item in parsed if item)


def _work_events_from_sqlite() -> list[WorkEvent] | None:
    """Fast local read of Polylogue's durable work-event product table."""
    db = _default_polylogue_db_path()
    if not db.exists():
        return None
    try:
        with sqlite3.connect(str(db)) as conn:
            conn.row_factory = sqlite3.Row
            table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='session_work_events'"
            ).fetchone()
            if table is None:
                return None
            rows = conn.execute(
                """
                SELECT event_id, conversation_id, provider_name, kind, confidence,
                       start_time, end_time, duration_ms, summary,
                       file_paths_json, tools_used_json, evidence_payload_json
                FROM session_work_events
                ORDER BY start_time, conversation_id, event_index
                """
            ).fetchall()
    except sqlite3.Error as exc:
        logger.warning("polylogue work_events sqlite read failed: %s", exc)
        return None

    events: list[WorkEvent] = []
    for row in rows:
        file_paths = _json_list(row["file_paths_json"])
        tools_used = _json_list(row["tools_used_json"])
        if (not file_paths or not tools_used) and row["evidence_payload_json"]:
            try:
                evidence = json.loads(row["evidence_payload_json"] or "{}")
            except json.JSONDecodeError:
                evidence = {}
            if not file_paths:
                raw_paths = evidence.get("file_paths") or []
                file_paths = tuple(str(path) for path in raw_paths if path)
            if not tools_used:
                raw_tools = evidence.get("tools_used") or []
                tools_used = tuple(str(tool) for tool in raw_tools if tool)

        events.append(WorkEvent(
            event_id=str(row["event_id"]),
            conversation_id=str(row["conversation_id"]),
            provider=str(row["provider_name"]),
            kind=str(row["kind"] or "unknown"),
            confidence=float(row["confidence"] or 0),
            start=_parse_dt(row["start_time"]),
            end=_parse_dt(row["end_time"]),
            duration_ms=int(row["duration_ms"] or 0),
            file_paths=file_paths,
            tools_used=tools_used,
            summary=str(row["summary"] or ""),
        ))
    return events


def work_events(*, start: Optional[date] = None, end: Optional[date] = None) -> list[WorkEvent]:
    """Load work events from polylogue — sub-session temporal segments with kind, files, tools."""
    global _cached_work_events
    if _cached_work_events is None:
        sqlite_rows = _work_events_from_sqlite()
        if sqlite_rows is not None:
            _cached_work_events = sqlite_rows
        else:
            _cached_work_events = []

    if _cached_work_events == []:
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


def _day_summaries_from_sqlite() -> list[DaySessionSummary] | None:
    """Fast local read of Polylogue's durable day summary table.

    This is intentionally narrow. It should disappear once Polylogue exposes a
    supported bulk export/query surface for downstream tools.
    """
    db = _default_polylogue_db_path()
    if not db.exists():
        return None
    try:
        with sqlite3.connect(str(db)) as conn:
            conn.row_factory = sqlite3.Row
            table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='day_session_summaries'"
            ).fetchone()
            if table is None:
                return None
            rows = conn.execute(
                """
                SELECT day, provider_name, conversation_count, total_cost_usd,
                       total_messages, total_words, work_event_breakdown_json,
                       repos_active_json
                FROM day_session_summaries
                ORDER BY day DESC, provider_name
                """
            ).fetchall()
    except sqlite3.Error as exc:
        logger.warning("polylogue day_summaries sqlite read failed: %s", exc)
        return None

    grouped: dict[str, dict[str, object]] = {}
    for row in rows:
        day = row["day"]
        bucket = grouped.setdefault(day, {
            "session_count": 0,
            "total_cost_usd": 0.0,
            "total_messages": 0,
            "total_words": 0,
            "work_event_breakdown": Counter(),
            "repos_active": set(),
            "providers": {},
        })
        count = int(row["conversation_count"] or 0)
        bucket["session_count"] = int(bucket["session_count"]) + count
        bucket["total_cost_usd"] = float(bucket["total_cost_usd"]) + float(row["total_cost_usd"] or 0)
        bucket["total_messages"] = int(bucket["total_messages"]) + int(row["total_messages"] or 0)
        bucket["total_words"] = int(bucket["total_words"]) + int(row["total_words"] or 0)
        providers = bucket["providers"]
        assert isinstance(providers, dict)
        providers[row["provider_name"]] = count

        try:
            breakdown = json.loads(row["work_event_breakdown_json"] or "{}")
        except json.JSONDecodeError:
            breakdown = {}
        counter = bucket["work_event_breakdown"]
        assert isinstance(counter, Counter)
        counter.update({str(k): int(v) for k, v in breakdown.items()})

        try:
            repos = json.loads(row["repos_active_json"] or "[]")
        except json.JSONDecodeError:
            repos = []
        repo_set = bucket["repos_active"]
        assert isinstance(repo_set, set)
        repo_set.update(str(repo) for repo in repos if repo)

    return [
        DaySessionSummary(
            date=date.fromisoformat(day),
            session_count=int(data["session_count"]),
            total_cost_usd=float(data["total_cost_usd"]),
            total_messages=int(data["total_messages"]),
            total_words=int(data["total_words"]),
            work_event_breakdown=dict(data["work_event_breakdown"]),
            repos_active=tuple(sorted(data["repos_active"])),
            providers=dict(data["providers"]),
        )
        for day, data in grouped.items()
    ]


def day_session_summaries(*, start: Optional[date] = None, end: Optional[date] = None) -> list[DaySessionSummary]:
    """Polylogue's pre-computed daily session aggregation."""
    global _cached_day_summaries
    if _cached_day_summaries is None:
        sqlite_rows = _day_summaries_from_sqlite()
        if sqlite_rows is not None:
            _cached_day_summaries = sqlite_rows
        else:
            _cached_day_summaries = []

    if _cached_day_summaries == []:
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
