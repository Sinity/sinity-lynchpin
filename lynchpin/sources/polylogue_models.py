"""Dataclasses for the Polylogue source API."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional


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
    workflow_shape: Optional[str] = None
    workflow_shape_confidence: float = 0.0
    terminal_state: Optional[str] = None
    terminal_state_confidence: float = 0.0


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


@dataclass
class _DaySummaryBucket:
    session_count: int = 0
    total_cost_usd: float = 0.0
    total_messages: int = 0
    total_words: int = 0
    work_event_breakdown: Counter[str] = field(default_factory=Counter)
    repos_active: set[str] = field(default_factory=set)
    providers: dict[str, int] = field(default_factory=dict)


@dataclass
class _ProfileDayBucket:
    session_count: int = 0
    total_messages: int = 0
    total_words: int = 0
    repos_active: set[str] = field(default_factory=set)


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


@dataclass(frozen=True)
class PolylogueReadiness:
    db_path: Path
    status: str
    reason: str
    conversation_count: int
    message_count: int | None
    conversation_stats_count: int
    session_profile_count: int
    day_summary_count: int
    work_event_count: int
    provider_event_count: int | None
    derives_profiles_from_base_tables: bool
    derives_day_summaries_from_profiles: bool


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
    workflow_shape: Optional[str] = None
    workflow_shape_confidence: float = 0.0
    terminal_state: Optional[str] = None
    terminal_state_confidence: float = 0.0


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


@dataclass(frozen=True)
class ConversationLineage:
    """One conversation's parent/branch attribution from Polylogue.

    Polylogue's ``conversations`` table tracks branching natively via
    ``parent_id`` and ``branch_type`` (continuation / sidechain / fork /
    subagent). This shape exposes those columns to graph consumers.
    """

    conversation_id: str
    parent_conversation_id: Optional[str]
    branch_type: Optional[str]
    provider: str
    title: str
    created_at: Optional[datetime]


@dataclass(frozen=True)
class CostSummary:
    date: date
    provider: str
    session_count: int
    total_cost_usd: float
    total_messages: int
    cost_per_message: float


@dataclass(frozen=True)
class WorkPattern:
    work_kind: str
    session_count: int
    total_hours: float
    total_cost_usd: float
    top_projects: tuple[str, ...]


@dataclass
class _WorkPatternBucket:
    sessions: int = 0
    ms: int = 0
    cost: float = 0.0
    projects: Counter[str] = field(default_factory=Counter)
