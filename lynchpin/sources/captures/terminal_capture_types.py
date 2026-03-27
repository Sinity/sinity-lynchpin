from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class TerminalSessionMetadata:
    """Normalized terminal-session metadata derived from cast + sidecars."""

    session_id: str
    path: str
    manifest_path: Optional[str]
    events_path: Optional[str]
    size_bytes: int
    created_at: Optional[str]
    finished_at: Optional[str]
    duration_seconds: Optional[float]
    active_seconds: Optional[float]
    idle_seconds: Optional[float]
    command_count: Optional[int]
    event_count: Optional[int]
    command: Optional[str]
    title: Optional[str]
    shell: Optional[str]
    term: Optional[str]
    term_type: Optional[str]
    term_cols: Optional[int]
    term_rows: Optional[int]
    host: Optional[str]
    user: Optional[str]
    tty: Optional[str]
    terminal: Optional[str]
    start_cwd: Optional[str]
    final_cwd: Optional[str]
    project_root: Optional[str]
    final_project_root: Optional[str]
    repo_root: Optional[str]
    final_repo_root: Optional[str]
    repo_branch: Optional[str]
    final_repo_branch: Optional[str]
    repo_commit: Optional[str]
    final_repo_commit: Optional[str]
    repo_dirty: Optional[bool]
    final_repo_dirty: Optional[bool]
    exit_code: Optional[int]
    exit_reason: Optional[str]
    recorder_exit_code: Optional[int]
    cleanup_escalated: Optional[bool]
    has_events: bool
    timing_source: Optional[str]
    schema_generation: str
    quality_status: str
    quality_flags: list[str] = field(default_factory=list)
    field_sources: dict[str, str] = field(default_factory=dict)


@dataclass
class TerminalSessionEvent:
    """Low-frequency terminal session events from events.jsonl."""

    session_id: str
    cast_path: str
    schema_generation: str
    source: str
    time: Optional[str]
    type: str
    pwd: Optional[str]
    project_root: Optional[str]
    repo_root: Optional[str]
    repo_branch: Optional[str]
    repo_commit: Optional[str]
    repo_dirty: Optional[bool]
    exit_code: Optional[int]
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class TerminalAuditEntry:
    path: str
    session_id: str
    schema_generation: str
    readable_header: bool
    header_version: Optional[int]
    has_manifest: bool
    has_events: bool
    has_command: bool
    has_geometry: bool
    has_activity_estimate: bool
    duration_seconds: Optional[float]
    timing_source: Optional[str]
    status: str
    issues: list[str] = field(default_factory=list)


@dataclass
class TerminalAuditSummary:
    cast_count: int = 0
    manifest_count: int = 0
    events_count: int = 0
    unreadable_header_count: int = 0
    counts_by_generation: dict[str, int] = field(default_factory=dict)
    counts_by_header_version: dict[str, int] = field(default_factory=dict)
    counts_by_status: dict[str, int] = field(default_factory=dict)
    counts_by_timing_source: dict[str, int] = field(default_factory=dict)
    missing_manifest_count: int = 0
    missing_events_count: int = 0
    sessions_with_command_count: int = 0
    sessions_with_geometry_count: int = 0
    missing_activity_estimate_count: int = 0
    header_only_count: int = 0
    degraded_count: int = 0
    damaged_count: int = 0
    quarantine_candidate_count: int = 0
    date_range_start: Optional[str] = None
    date_range_end: Optional[str] = None


@dataclass
class _CastHeaderSummary:
    header_json: str
    duration_seconds: float
    active_seconds: Optional[float]
    idle_seconds: Optional[float]
    timing_source: str


@dataclass
class _CastTimingSummary:
    duration_seconds: float
    active_seconds: Optional[float]
    idle_seconds: Optional[float]


@dataclass
class _SessionEventSummary:
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    first_event_time: Optional[str] = None
    last_event_time: Optional[str] = None
    start_cwd: Optional[str] = None
    final_cwd: Optional[str] = None
    project_root: Optional[str] = None
    final_project_root: Optional[str] = None
    repo_root: Optional[str] = None
    final_repo_root: Optional[str] = None
    repo_branch: Optional[str] = None
    final_repo_branch: Optional[str] = None
    repo_commit: Optional[str] = None
    final_repo_commit: Optional[str] = None
    repo_dirty: Optional[bool] = None
    final_repo_dirty: Optional[bool] = None
    command_count: Optional[int] = None
    event_count: Optional[int] = None
    first_command: Optional[str] = None
    exit_code: Optional[int] = None
    active_seconds: Optional[float] = None
    idle_seconds: Optional[float] = None
