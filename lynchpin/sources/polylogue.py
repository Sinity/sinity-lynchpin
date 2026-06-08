"""AI chat source: session profiles, daily activity, cost, work patterns.

Reads Polylogue's typed Python facade (SyncPolylogue). Product tables are
owned by Polylogue; Lynchpin consumes the public insight/readiness surfaces.

Covers all providers: Claude (claude-ai, claude-code), ChatGPT, Codex, Gemini.

"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections import Counter, defaultdict
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator, Optional

from ..core.coverage import CoverageBounds
from ..core.errors import MaterializationError
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


class PolylogueMaterializationError(MaterializationError):
    """Raised when required Polylogue insight products are unavailable."""

    def __init__(self, reason: str = "") -> None:
        super().__init__("polylogue", reason=reason)


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
    "PolylogueMaterializationError",
    "iter_session_profiles",
    "session_profiles_for_date",
    "conversation_transcripts",
    "conversation_lineages",
    "work_events",
    "day_session_summaries",
    "archive_readiness",
    "daily_activity",
    "coverage_bounds",
    "work_thread_activity",
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
    direct = _archive_readiness_from_sqlite(db)
    if direct is not None:
        return direct
    try:
        from polylogue.insights.readiness import InsightReadinessQuery

        report = _polylogue_client().insight_readiness_report(
            InsightReadinessQuery(
                insights=(
                    "session_profiles",
                    "archive_coverage",
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
    day = entries.get("archive_coverage")
    work_event = entries.get("session_work_events")
    profile_count = _readiness_row_count(profile)
    day_count = _readiness_row_count(day)
    work_event_count = _readiness_row_count(work_event)
    total_conversations = _readiness_total_conversations(report)
    incomplete_entries = tuple(
        entry
        for entry in (profile, day, work_event)
        if entry is None or not _readiness_entry_complete(entry)
    )

    if (
        profile_count > 0
        and day_count > 0
        and work_event_count > 0
        and not incomplete_entries
    ):
        probe_error = _probe_required_insight_reads()
        if probe_error is None:
            status = "ready"
            reason = "materialized profile, archive-coverage, and work-event products are populated"
        else:
            status = "degraded"
            reason = f"required Polylogue insight read failed: {probe_error}"
    elif (
        total_conversations > 0
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
        conversation_count=total_conversations,
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


def _archive_readiness_from_sqlite(db: Path) -> PolylogueReadiness | None:
    if not db.exists():
        return None
    try:
        with sqlite3.connect(str(db)) as conn:
            if not (
                _sqlite_has_table(conn, "session_profiles")
                and _sqlite_has_table(conn, "session_work_events")
            ):
                return None
            profile_count = _sqlite_table_count(conn, "session_profiles")
            work_event_count = _sqlite_table_count(conn, "session_work_events")
            conversation_count = (
                _sqlite_table_count(conn, "conversations")
                if _sqlite_has_table(conn, "conversations")
                else profile_count
            )
            day_count = (
                _sqlite_table_count(conn, "day_session_summaries")
                if _sqlite_has_table(conn, "day_session_summaries")
                else 0
            )
    except sqlite3.Error as exc:
        logger.warning("polylogue direct readiness read failed: %s", exc)
        return None

    if profile_count > 0 and work_event_count > 0:
        status = "ready"
        reason = "direct Polylogue session-profile and work-event products are populated"
    elif conversation_count > 0 or profile_count > 0 or work_event_count > 0:
        status = "degraded"
        missing = []
        if profile_count == 0:
            missing.append("session_profiles")
        if work_event_count == 0:
            missing.append("session_work_events")
        reason = "missing or empty products: " + ", ".join(missing)
    else:
        status = "unavailable"
        reason = "polylogue archive tables are empty"

    return PolylogueReadiness(
        db_path=db,
        status=status,
        reason=reason,
        conversation_count=conversation_count,
        message_count=None,
        conversation_stats_count=profile_count,
        session_profile_count=profile_count,
        day_summary_count=day_count,
        work_event_count=work_event_count,
        provider_event_count=None,
        derives_profiles_from_base_tables=False,
        derives_day_summaries_from_profiles=False,
    )


def _sqlite_table_count(conn: sqlite3.Connection, table: str) -> int:
    row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    return int(row[0]) if row else 0


def _readiness_total_conversations(report: object) -> int:
    """Handle Polylogue readiness field drift across archive versions."""

    for field in ("total_conversations", "total_sessions"):
        value = getattr(report, field, None)
        if isinstance(value, int):
            return value
    return 0


def _readiness_row_count(entry: Any | None) -> int:
    return int(entry.row_count) if entry is not None else 0


def _readiness_entry_complete(entry: Any) -> bool:
    row_count = _readiness_row_count(entry)
    expected = entry.expected_row_count
    if expected is None:
        return entry.verdict in {"ready", "empty"} and row_count > 0
    return row_count >= int(expected) and row_count > 0


def _readiness_reason(entries: dict[str, Any]) -> str:
    missing = []
    degraded = []
    for name in ("session_profiles", "archive_coverage", "session_work_events"):
        entry = entries.get(name)
        if entry is None or entry.row_count == 0:
            missing.append(name)
        elif not _readiness_entry_complete(entry):
            degraded.append(f"{name}={entry.verdict}")
    parts = []
    if missing:
        parts.append("missing or empty products: " + ", ".join(missing))
    if degraded:
        parts.append("degraded products: " + ", ".join(degraded))
    return "; ".join(parts) if parts else "polylogue insight readiness is degraded"


def _probe_required_insight_reads() -> str | None:
    """Verify the same insight readers used by analysis can open their products."""
    error: str | None = None
    for attempt in range(3):
        try:
            from polylogue.insights.archive import (
                ArchiveCoverageInsightQuery,
                SessionProfileInsightQuery,
                SessionWorkEventInsightQuery,
            )

            client = _polylogue_client()
            client.list_session_profile_insights(SessionProfileInsightQuery(limit=1))
            client.list_archive_coverage_insights(
                ArchiveCoverageInsightQuery(group_by="day", limit=1)
            )
            client.list_session_work_event_insights(
                SessionWorkEventInsightQuery(limit=1)
            )
            return None
        except Exception as exc:
            error = str(exc)
            if attempt < 2:
                time.sleep(0.5)
    return error


def _require_materialized_products() -> None:
    """Refuse to read profile / work-event products only when the archive
    can't serve them at all.

    ``degraded`` is treated as readable-with-caveat. Polylogue flags rows
    where its inference fell back to heuristics (work-event-weak,
    engaged-duration-session-total, etc.) as degraded, but the rows are
    still present and downstream consumers handle missing inference
    fields gracefully. Raising here instead would mean ~half of recent
    sessions are unreadable just because polylogue's quality bar isn't met.
    ``unavailable`` (empty archive, facade broken) still raises so analysis
    fails fast rather than returning silently-empty results.
    """
    readiness = archive_readiness()
    if readiness.status in {"ready", "degraded"}:
        return
    raise PolylogueMaterializationError(
        f"Polylogue insight products are not materialized: {readiness.reason}. "
        "Run `polylogue doctor --repair --target session_insights`."
    )


# ══════════════════════════════════════════════════════════════════════════════
# Data types
# ══════════════════════════════════════════════════════════════════════════════


_cached_profiles: list[SessionProfile] | None = None
_cached_profiles_signature: tuple[int, int] | None = None


def _profiles_from_facade() -> list[SessionProfile]:
    """Load session profiles via the SyncPolylogue facade.

    Maps SessionProfileInsight (evidence + inference payloads) → SessionProfile.

    work_event_kind: most-common heuristic label across
    inference.work_events documents; falls back to inference.support_level-aware
    heuristics are not used — the work_events list is the typed surface.

    work_event_projects: use inference.repo_names when the product carries
    canonical names, otherwise derive canonical names from the product's
    evidence.repo_paths / cwd_paths via _canonical_projects().
    """
    _require_materialized_products()
    try:
        from polylogue.insights.archive import SessionProfileInsightQuery

        insights = _polylogue_client().list_session_profile_insights(
            SessionProfileInsightQuery(limit=None)
        )
    except Exception as exc:
        raise PolylogueMaterializationError(
            f"Polylogue session profile product read failed: {exc}"
        ) from exc

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
    workflow_shape: Optional[str] = None
    workflow_shape_confidence = 0.0
    terminal_state: Optional[str] = None
    terminal_state_confidence = 0.0
    if inference is not None and inference.work_events:
        kinds = [
            str(ev["heuristic_label"])
            for ev in inference.work_events
            if isinstance(ev, dict) and ev.get("heuristic_label")
        ]
        if kinds:
            work_event_kind = Counter(kinds).most_common(1)[0][0]
    if inference is not None:
        workflow_shape_raw = getattr(inference, "workflow_shape", None)
        if workflow_shape_raw:
            workflow_shape = str(workflow_shape_raw)
        workflow_shape_confidence = float(
            getattr(inference, "workflow_shape_confidence", 0.0) or 0.0
        )
        terminal_state_raw = getattr(inference, "terminal_state", None)
        if terminal_state_raw:
            terminal_state = str(terminal_state_raw)
        terminal_state_confidence = float(
            getattr(inference, "terminal_state_confidence", 0.0) or 0.0
        )

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
        provider=insight.source_name,
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
        workflow_shape=workflow_shape,
        workflow_shape_confidence=workflow_shape_confidence,
        terminal_state=terminal_state,
        terminal_state_confidence=terminal_state_confidence,
    )


def _load_profiles() -> list[SessionProfile]:
    """Load session profiles from the Polylogue facade."""
    global _cached_profiles, _cached_profiles_signature
    signature = _profile_cache_signature()
    if _cached_profiles is not None and _cached_profiles_signature == signature:
        return _cached_profiles
    _cached_profiles = _profiles_from_facade()
    _cached_profiles_signature = signature
    return _cached_profiles


def _profile_cache_signature() -> tuple[int, int] | None:
    path = _default_polylogue_db_path()
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def iter_session_profiles(
    *, start: Optional[date] = None, end: Optional[date] = None
) -> Iterator[SessionProfile]:
    """Yield session profiles from polylogue archive, optionally date-bounded."""
    if start is not None and end is not None:
        yield from session_profiles_for_date(start=start, end=end)
        return
    yield from _load_profiles()


def session_profiles_for_date(*, start: date, end: date) -> list[SessionProfile]:
    """Date-bounded session profiles with graceful degradation on missing products."""
    try:
        return _session_profiles_from_facade(start=start, end=end)
    except PolylogueMaterializationError as exc:
        logger.warning("polylogue session profiles unavailable for date range: %s", exc)
        return []


def _session_profiles_from_facade(*, start: date, end: date) -> list[SessionProfile]:
    """Read date-bounded session profiles through Polylogue's public facade."""
    direct = _session_profiles_from_sqlite(start=start, end=end)
    if direct is not None:
        return direct

    _require_materialized_products()
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
        raise PolylogueMaterializationError(
            f"Polylogue bounded session profile product read failed: {exc}"
        ) from exc

    return [_session_profile_from_insight(insight) for insight in insights]


def _session_profiles_from_sqlite(
    *, start: date, end: date
) -> list[SessionProfile] | None:
    db = _default_polylogue_db_path()
    if not db.exists():
        return None
    try:
        with sqlite3.connect(str(db)) as conn:
            conn.row_factory = sqlite3.Row
            if not _sqlite_has_table(conn, "session_profiles"):
                return None
            rows = conn.execute(
                """
                SELECT
                    conversation_id, source_name, title, first_message_at,
                    last_message_at, canonical_session_date, repo_names_json,
                    repo_paths_json, auto_tags_json, message_count, word_count,
                    engaged_duration_ms, wall_duration_ms, total_cost_usd,
                    tool_use_count, thinking_count, substantive_count,
                    attachment_count, work_event_count, phase_count,
                    cost_is_estimated, workflow_shape, workflow_shape_confidence,
                    terminal_state, terminal_state_confidence,
                    inference_payload_json
                FROM session_profiles
                WHERE canonical_session_date >= ? AND canonical_session_date < ?
                ORDER BY canonical_session_date, first_message_at, conversation_id
                """,
                (start.isoformat(), end.isoformat()),
            ).fetchall()
    except sqlite3.Error as exc:
        logger.warning("polylogue direct session profile read failed: %s", exc)
        return None
    return [_session_profile_from_row(row) for row in rows]


def _session_profile_from_row(row: sqlite3.Row) -> SessionProfile:
    canonical_session_date = None
    if row["canonical_session_date"]:
        try:
            canonical_session_date = date.fromisoformat(row["canonical_session_date"])
        except ValueError:
            canonical_session_date = None
    projects = _canonical_projects(_json_list(row["repo_names_json"]))
    if not projects:
        projects = _canonical_projects(_json_list(row["repo_paths_json"]))
    return SessionProfile(
        conversation_id=str(row["conversation_id"]),
        provider=str(row["source_name"] or ""),
        title=str(row["title"] or ""),
        message_count=int(row["message_count"] or 0),
        word_count=int(row["word_count"] or 0),
        first_message_at=_parse_dt(row["first_message_at"]),
        last_message_at=_parse_dt(row["last_message_at"]),
        engaged_duration_ms=int(row["engaged_duration_ms"] or 0),
        wall_duration_ms=int(row["wall_duration_ms"] or 0),
        work_event_kind=_work_event_kind_from_inference_json(
            row["inference_payload_json"]
        ),
        work_event_projects=projects,
        total_cost_usd=float(row["total_cost_usd"] or 0.0),
        canonical_session_date=canonical_session_date,
        tool_use_count=int(row["tool_use_count"] or 0),
        thinking_count=int(row["thinking_count"] or 0),
        auto_tags=_json_list(row["auto_tags_json"]),
        substantive_count=int(row["substantive_count"] or 0),
        attachment_count=int(row["attachment_count"] or 0),
        work_event_count=int(row["work_event_count"] or 0),
        phase_count=int(row["phase_count"] or 0),
        cost_is_estimated=bool(row["cost_is_estimated"]),
        workflow_shape=str(row["workflow_shape"] or "") or None,
        workflow_shape_confidence=float(row["workflow_shape_confidence"] or 0.0),
        terminal_state=str(row["terminal_state"] or "") or None,
        terminal_state_confidence=float(row["terminal_state_confidence"] or 0.0),
    )


def _work_event_kind_from_inference_json(value: object) -> str | None:
    if not value:
        return None
    try:
        payload = json.loads(str(value))
    except json.JSONDecodeError:
        return None
    events = payload.get("work_events") if isinstance(payload, dict) else None
    if not isinstance(events, list):
        return None
    kinds = [
        str(event["heuristic_label"])
        for event in events
        if isinstance(event, dict) and event.get("heuristic_label")
    ]
    return Counter(kinds).most_common(1)[0][0] if kinds else None


def _sqlite_has_table(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        is not None
    )


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
        raise PolylogueMaterializationError(
            f"Polylogue message product read failed: {exc}"
        ) from exc

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
        key=lambda item: (
            item.first_message_at.timestamp()
            if item.first_message_at
            else float("-inf")
        )
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
) -> list[WorkEvent]:
    """Load Polylogue's durable work-event insights via the typed facade."""
    direct = _work_events_from_sqlite(start=start, end=end)
    if direct is not None:
        return direct

    _require_materialized_products()
    from polylogue.insights.archive import SessionWorkEventInsightQuery

    profile_context = {
        profile.conversation_id: profile
        for profile in (
            session_profiles_for_date(start=start, end=end)
            if start is not None and end is not None
            else _load_profiles()
        )
    }

    query_model: Any = SessionWorkEventInsightQuery
    query = query_model(
        session_date_since=start.isoformat() if start else None,
        session_date_until=end.isoformat() if end else None,
        limit=None,
    )
    try:
        insights = _polylogue_client().list_session_work_event_insights(query)
    except Exception as exc:
        raise PolylogueMaterializationError(
            f"Polylogue session work-event product read failed: {exc}"
        ) from exc

    events: list[WorkEvent] = []
    for insight in insights:
        ev = insight.evidence
        inf = insight.inference
        profile = profile_context.get(str(insight.conversation_id))
        start = _parse_dt(ev.start_time) if ev.start_time else None
        end = _parse_dt(ev.end_time) if ev.end_time else None
        events.append(
            WorkEvent(
                event_id=insight.event_id,
                conversation_id=insight.conversation_id,
                provider=insight.source_name,
                kind=str(inf.heuristic_label or "unknown"),
                confidence=float(inf.confidence),
                start=start,
                end=end,
                duration_ms=int(ev.duration_ms),
                file_paths=tuple(ev.file_paths),
                tools_used=tuple(ev.tools_used),
                summary=str(inf.summary or ""),
                workflow_shape=profile.workflow_shape if profile else None,
                workflow_shape_confidence=profile.workflow_shape_confidence
                if profile
                else 0.0,
                terminal_state=profile.terminal_state if profile else None,
                terminal_state_confidence=profile.terminal_state_confidence
                if profile
                else 0.0,
            )
        )
    return events


def _work_events_from_sqlite(
    *,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> list[WorkEvent] | None:
    db = _default_polylogue_db_path()
    if not db.exists():
        return None
    try:
        with sqlite3.connect(str(db)) as conn:
            conn.row_factory = sqlite3.Row
            if not _sqlite_has_table(conn, "session_work_events"):
                return None
            if start is not None and end is not None:
                rows = conn.execute(
                    """
                    SELECT
                        event_id, conversation_id, source_name, heuristic_label,
                        confidence, start_time, end_time, duration_ms,
                        summary, file_paths_json, tools_used_json
                    FROM session_work_events
                    WHERE start_time >= ?
                      AND start_time < ?
                    ORDER BY start_time, event_index
                    """,
                    (start.isoformat(), end.isoformat()),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT
                        event_id, conversation_id, source_name, heuristic_label,
                        confidence, start_time, end_time, duration_ms,
                        summary, file_paths_json, tools_used_json
                    FROM session_work_events
                    ORDER BY canonical_session_date, start_time, event_index
                    """
                ).fetchall()
    except sqlite3.Error as exc:
        logger.warning("polylogue direct work-event read failed: %s", exc)
        return None

    profile_context = {
        profile.conversation_id: profile
        for profile in (
            session_profiles_for_date(start=start, end=end)
            if start is not None and end is not None
            else _load_profiles()
        )
    }
    return [_work_event_from_row(row, profile_context) for row in rows]


def _work_event_from_row(
    row: sqlite3.Row, profile_context: dict[str, SessionProfile]
) -> WorkEvent:
    profile = profile_context.get(str(row["conversation_id"]))
    return WorkEvent(
        event_id=str(row["event_id"]),
        conversation_id=str(row["conversation_id"]),
        provider=str(row["source_name"] or ""),
        kind=str(row["heuristic_label"] or "unknown"),
        confidence=float(row["confidence"] or 0.0),
        start=_parse_dt(row["start_time"]),
        end=_parse_dt(row["end_time"]),
        duration_ms=int(row["duration_ms"] or 0),
        file_paths=_json_list(row["file_paths_json"]),
        tools_used=_json_list(row["tools_used_json"]),
        summary=str(row["summary"] or ""),
        workflow_shape=profile.workflow_shape if profile else None,
        workflow_shape_confidence=profile.workflow_shape_confidence
        if profile
        else 0.0,
        terminal_state=profile.terminal_state if profile else None,
        terminal_state_confidence=profile.terminal_state_confidence
        if profile
        else 0.0,
    )


def work_events(
    *, start: Optional[date] = None, end: Optional[date] = None
) -> list[WorkEvent]:
    """Load work events from the Polylogue facade."""
    if start is not None or end is not None:
        return _work_events_from_facade(start=start, end=end)

    global _cached_work_events
    if _cached_work_events is None:
        _cached_work_events = _work_events_from_facade()

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


def _day_summaries_from_facade() -> list[DaySessionSummary]:
    """Read daily archive coverage rows via the typed Polylogue facade.

    Lynchpin keeps its DaySessionSummary dataclass as a local reader shape,
    but Polylogue no longer exposes a separate day-summary product.
    This is a required materialized product; unavailable products raise instead
    of being interpreted as zero AI activity.
    """
    _require_materialized_products()
    try:
        from polylogue.insights.archive import ArchiveCoverageInsightQuery

        insights = _polylogue_client().list_archive_coverage_insights(
            ArchiveCoverageInsightQuery(group_by="day", limit=None)
        )
    except Exception as exc:
        raise PolylogueMaterializationError(
            f"Polylogue day archive coverage read failed: {exc}"
        ) from exc

    summaries: list[DaySessionSummary] = []
    for insight in insights:
        try:
            day = date.fromisoformat(str(insight.bucket))
        except (ValueError, TypeError):
            logger.debug("skipping day coverage row with unparseable date: %r", insight.bucket)
            continue
        summaries.append(
            DaySessionSummary(
                date=day,
                session_count=int(insight.conversation_count),
                total_cost_usd=float(insight.total_cost_usd),
                total_messages=int(insight.message_count),
                total_words=int(insight.total_words),
                work_event_breakdown=dict(insight.work_event_breakdown),
                repos_active=tuple(insight.repos_active),
                providers=dict(insight.provider_breakdown),
            )
        )
    return summaries


def day_session_summaries(
    *, start: Optional[date] = None, end: Optional[date] = None
) -> list[DaySessionSummary]:
    """Daily session aggregation from Polylogue's durable product tables."""
    global _cached_day_summaries
    if _cached_day_summaries is None:
        try:
            _cached_day_summaries = _day_summaries_from_facade()
        except PolylogueMaterializationError as exc:
            logger.warning("polylogue day summaries unavailable: %s", exc)
            _cached_day_summaries = []

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

    try:
        profiles = iter_session_profiles(start=start, end=end)
    except PolylogueMaterializationError as exc:
        logger.warning("polylogue daily activity profiles unavailable: %s", exc)
        return []

    for profile in profiles:
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
    for (day, provider), day_profiles in sorted(by_key.items()):
        work_kinds: Counter[str] = Counter()
        projects: set[str] = set()
        total_messages = total_words = 0
        engaged_ms = wall_ms = 0
        for p in day_profiles:
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
                session_count=len(day_profiles),
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


def coverage_bounds() -> CoverageBounds | None:
    import sqlite3
    db = _default_polylogue_db_path()
    if not db.exists():
        return None
    try:
        conn = sqlite3.connect(str(db))
        row = conn.execute(
            "SELECT MIN(created_at), MAX(created_at) FROM conversations"
        ).fetchone()
        conn.close()
    except Exception:
        return None
    if not row or row[0] is None:
        return None
    from datetime import datetime
    first = datetime.fromisoformat(row[0]).date()
    last = datetime.fromisoformat(row[1]).date()
    return CoverageBounds(source="polylogue", first=first, last=last, kind="capture")


def work_thread_activity(*, start: date, end: date) -> list[ChatDayActivity]:
    """Daily AI chat activity aggregated from work threads instead of sessions.

    Work threads group related sessions (resumes, compactions, subagent spawns) and
    provide more accurate wall-clock duration (wall_duration_ms) compared to the
    per-session engaged_duration_ms which often undercounts by 10-14×.

    This function is an alternative to daily_activity() that uses work_threads as the
    aggregation unit. Returns the same ChatDayActivity structure but sourced from
    work_threads rather than session_profiles.

    On missing or degraded Polylogue work-thread products, returns empty list and logs warning.
    """
    try:
        _require_materialized_products()
    except PolylogueMaterializationError as exc:
        logger.warning("polylogue work-thread activity unavailable: %s", exc)
        return []

    try:
        from polylogue.insights.archive import WorkThreadInsightQuery

        insights = _polylogue_client().list_work_thread_insights(
            WorkThreadInsightQuery(limit=None)
        )
    except Exception as exc:
        logger.warning("polylogue work-thread product read failed: %s", exc)
        return []

    by_key: dict[tuple[date, str], list[dict[str, Any]]] = defaultdict(list)

    for insight in insights:
        thread_payload = insight.thread
        # Extract date from thread start_time or fallback to a sentinel
        start_dt = _parse_dt(thread_payload.start_time) if thread_payload.start_time else None
        if start_dt is None:
            # Work threads should have start_time; skip rows without it
            continue
        thread_date = start_dt.date()
        if thread_date < start or thread_date > end:
            continue

        # Extract work event breakdown from the work_event_breakdown
        work_kinds: Counter[str] = Counter()
        if thread_payload.work_event_breakdown:
            work_kinds.update(thread_payload.work_event_breakdown)

        # Provider is inferred from provider_breakdown; use the most common provider
        provider = "unknown"
        if thread_payload.provider_breakdown:
            # Get the provider with the most sessions in this thread
            provider = max(
                thread_payload.provider_breakdown.keys(),
                key=lambda p: thread_payload.provider_breakdown[p],
            )

        # Use wall_duration_ms as the primary duration metric
        wall_ms = thread_payload.wall_duration_ms or 0

        by_key[(thread_date, provider)].append({
            "thread_id": insight.thread_id,
            "wall_ms": wall_ms,
            "work_kinds": work_kinds,
            "dominant_repo": thread_payload.dominant_repo,
            "session_count": thread_payload.session_count,
            "message_count": thread_payload.total_messages,
            "provider_breakdown": thread_payload.provider_breakdown,
        })

    result: list[ChatDayActivity] = []
    for (day, provider), thread_data in sorted(by_key.items()):
        total_wall_ms = sum(t["wall_ms"] for t in thread_data)
        combined_work_kinds: Counter[str] = Counter()
        for t in thread_data:
            combined_work_kinds.update(t["work_kinds"])
        total_messages = sum(t["message_count"] for t in thread_data)

        result.append(
            ChatDayActivity(
                date=day,
                provider=provider,
                session_count=sum(t["session_count"] for t in thread_data),
                total_messages=total_messages,
                total_words=0,  # work_threads don't track word count
                engaged_minutes=0.0,  # use total_wall_minutes instead
                total_wall_minutes=round(total_wall_ms / 60_000, 1),
                dominant_work_kind=combined_work_kinds.most_common(1)[0][0]
                if combined_work_kinds
                else None,
                projects=(),  # would need to aggregate from constituent sessions; left as TODO
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
    for p in iter_session_profiles(start=start, end=end):
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

    for p in iter_session_profiles(start=start, end=end):
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
