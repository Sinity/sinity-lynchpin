"""Token-economy summary over Polylogue conversation transcripts (Arc L.1).

Tokens are the real intensity surface that flat-rate subscriptions hide.
This module produces a typed summary per (project, day) bucket plus a
window-level rollup, preserving every dimension (provider, kind, prompt vs
dialogue vs all-message tokens). Never collapses to a "burn rate" scalar;
never collapses to dollars (cost is curiosity per the user's clarification —
see ``polylogue.cost_summary`` docstring).

Design notes:

- Reads ``conversation_transcripts(start, end)`` for token counts and
  ``session_profiles_for_date`` for project/kind attribution. Both flow
  from the durable Polylogue product tables (Arc 0 ready).
- Project attribution uses the same fallback chain as the evidence graph:
  session profile's ``work_event_projects`` first, then any project hint
  derived from the title.
- Kind attribution uses the session-level ``work_event_kind`` only — Arc K
  tier weighting belongs to per-event consumers, not to bulk token counts.
  The per-kind breakdown is observation-counts, not weighted scores.
- Empty results when the archive is unavailable or the window has no
  conversations; never fabricates rows.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Sequence

from ..core.primitives import logical_date
from ..sources.polylogue import (
    ConversationTranscript,
    SessionProfile,
    conversation_transcripts,
    session_profiles_for_date,
)


@dataclass(frozen=True)
class TokenEconomyRow:
    """Per (project, day) token-economy slice."""
    project: str | None
    date: date
    session_count: int
    user_prompt_tokens: int
    dialogue_tokens: int
    all_message_tokens: int
    providers: dict[str, int]            # provider → session_count in this row
    kind_breakdown: dict[str, int]       # session-level work_event_kind counts
    conversation_ids: tuple[str, ...]


@dataclass(frozen=True)
class TokenEconomySummary:
    """Window-scoped rollup over per-row slices.

    `window_*_tokens` totals avoid double-counting when a transcript is
    attributed to multiple projects (each row carries the same conversation;
    the rollup sums conversations once per (project, day) appearance, which
    is the same shape used by other multi-project Polylogue surfaces). For
    cross-project totals not multi-counting conversations, see
    `unique_conversation_count` and `unique_*_tokens`.
    """
    start: date
    end: date
    rows: tuple[TokenEconomyRow, ...]
    window_session_count: int
    window_user_prompt_tokens: int
    window_dialogue_tokens: int
    window_all_message_tokens: int
    unique_conversation_count: int
    unique_user_prompt_tokens: int
    unique_dialogue_tokens: int
    unique_all_message_tokens: int
    providers: dict[str, int]
    kind_breakdown: dict[str, int]


@dataclass
class _MutableRow:
    project: str | None
    date: date
    session_count: int = 0
    user_prompt_tokens: int = 0
    dialogue_tokens: int = 0
    all_message_tokens: int = 0
    providers: Counter[str] = field(default_factory=Counter)
    kind_breakdown: Counter[str] = field(default_factory=Counter)
    conversation_ids: set[str] = field(default_factory=set)


def token_economy_summary(
    *,
    start: date,
    end: date,
    transcripts: Sequence[ConversationTranscript] | None = None,
    profiles: Sequence[SessionProfile] | None = None,
) -> TokenEconomySummary:
    """Build a `TokenEconomySummary` for the inclusive [start, end] window.

    `transcripts` and `profiles` accept caller-supplied data for tests; when
    omitted, both are loaded from the local Polylogue archive.
    """
    transcripts_iter: Sequence[ConversationTranscript] = (
        transcripts if transcripts is not None
        else tuple(conversation_transcripts(start=start, end=end))
    )
    profiles_iter: Sequence[SessionProfile] = (
        profiles if profiles is not None
        else tuple(session_profiles_for_date(start=start, end=end))
    )

    profile_by_id: dict[str, SessionProfile] = {p.conversation_id: p for p in profiles_iter}

    rows_by_key: dict[tuple[str | None, date], _MutableRow] = {}
    unique_user_tokens = 0
    unique_dialogue_tokens = 0
    unique_all_tokens = 0
    seen_conversations: set[str] = set()

    for transcript in transcripts_iter:
        ts_date = transcript.canonical_session_date
        if ts_date is None and transcript.first_message_at is not None:
            ts_date = logical_date(transcript.first_message_at)
        if ts_date is None or ts_date < start or ts_date > end:
            continue

        if transcript.conversation_id not in seen_conversations:
            seen_conversations.add(transcript.conversation_id)
            unique_user_tokens += transcript.user_prompt_tokens
            unique_dialogue_tokens += transcript.dialogue_tokens
            unique_all_tokens += transcript.all_message_tokens

        profile = profile_by_id.get(transcript.conversation_id)
        projects = _projects_for(profile, transcript)
        kind = (profile.work_event_kind if profile else None) or None

        for project in projects:
            key = (project, ts_date)
            if key not in rows_by_key:
                rows_by_key[key] = _MutableRow(project=project, date=ts_date)
            row = rows_by_key[key]
            row.session_count += 1
            row.user_prompt_tokens += transcript.user_prompt_tokens
            row.dialogue_tokens += transcript.dialogue_tokens
            row.all_message_tokens += transcript.all_message_tokens
            row.providers[transcript.provider or "unknown"] += 1
            if kind:
                row.kind_breakdown[kind] += 1
            row.conversation_ids.add(transcript.conversation_id)

    rows = tuple(_freeze_row(row) for row in sorted(rows_by_key.values(), key=lambda r: (r.date, r.project or "")))

    window_session_count = sum(row.session_count for row in rows)
    window_user_tokens = sum(row.user_prompt_tokens for row in rows)
    window_dialogue_tokens = sum(row.dialogue_tokens for row in rows)
    window_all_tokens = sum(row.all_message_tokens for row in rows)
    providers: Counter[str] = Counter()
    kind_breakdown: Counter[str] = Counter()
    for row in rows:
        providers.update(row.providers)
        kind_breakdown.update(row.kind_breakdown)

    return TokenEconomySummary(
        start=start,
        end=end,
        rows=rows,
        window_session_count=window_session_count,
        window_user_prompt_tokens=window_user_tokens,
        window_dialogue_tokens=window_dialogue_tokens,
        window_all_message_tokens=window_all_tokens,
        unique_conversation_count=len(seen_conversations),
        unique_user_prompt_tokens=unique_user_tokens,
        unique_dialogue_tokens=unique_dialogue_tokens,
        unique_all_message_tokens=unique_all_tokens,
        providers=dict(providers),
        kind_breakdown=dict(kind_breakdown),
    )


def render_token_economy_summary(summary: TokenEconomySummary, *, top_rows: int = 10) -> str:
    """Render a compact Markdown table of the heaviest project/day rows."""
    if not summary.rows:
        return "_No Polylogue conversations in window._"
    ordered = sorted(summary.rows, key=lambda r: r.all_message_tokens, reverse=True)[:top_rows]
    lines = [
        "| Project | Day | Sessions | User tokens | Dialogue tokens | All tokens | Providers | Kinds |",
        "|---|---|---:|---:|---:|---:|---|---|",
    ]
    for row in ordered:
        providers = ", ".join(f"{p}×{c}" for p, c in sorted(row.providers.items()))
        kinds = ", ".join(f"{k}×{v}" for k, v in sorted(row.kind_breakdown.items(), key=lambda kv: -kv[1])[:3])
        lines.append(
            f"| {row.project or '(unattributed)'} | {row.date.isoformat()} | "
            f"{row.session_count} | {row.user_prompt_tokens} | {row.dialogue_tokens} | "
            f"{row.all_message_tokens} | {providers} | {kinds} |"
        )
    lines.append("")
    lines.append(
        f"_Window: {summary.unique_conversation_count} unique conversations, "
        f"{summary.unique_all_message_tokens} unique tokens "
        f"(per-project rows multi-count when a session touches multiple projects). "
        f"Tokens are not dollars; subscription quota is the real constraint surface._"
    )
    return "\n".join(lines)


def _projects_for(profile: SessionProfile | None, transcript: ConversationTranscript) -> list[str | None]:
    """Project attribution: profile's work_event_projects first, else None."""
    if profile and profile.work_event_projects:
        return list(profile.work_event_projects)
    return [None]


def _freeze_row(row: _MutableRow) -> TokenEconomyRow:
    return TokenEconomyRow(
        project=row.project,
        date=row.date,
        session_count=row.session_count,
        user_prompt_tokens=row.user_prompt_tokens,
        dialogue_tokens=row.dialogue_tokens,
        all_message_tokens=row.all_message_tokens,
        providers=dict(row.providers),
        kind_breakdown=dict(row.kind_breakdown),
        conversation_ids=tuple(sorted(row.conversation_ids)),
    )


__all__ = [
    "TokenEconomyRow",
    "TokenEconomySummary",
    "render_token_economy_summary",
    "token_economy_summary",
]
