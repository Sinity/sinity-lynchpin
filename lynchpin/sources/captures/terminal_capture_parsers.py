from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Optional

from .terminal_capture_support import (
    ACTIVE_GAP_SECONDS,
    _assess_session_quality,
    _duration_between,
    _guess_project_root,
    _is_blank,
    _load_json_file,
    _local_iso_from_epoch_ms,
    _local_iso_from_epoch_seconds,
    _manifest_time,
    _ms_to_seconds,
    _parse_iso_datetime,
    _read_cast_full_timing,
    _read_cast_header,
    _schema_generation,
    _session_id,
    _session_time_from_id,
    _sidecar_paths,
    _to_bool,
    _to_float,
    _to_int,
    _to_text,
)
from .terminal_capture_types import (
    TerminalAuditEntry,
    TerminalAuditSummary,
    TerminalSessionEvent,
    TerminalSessionMetadata,
    _SessionEventSummary,
)

def _parse_session_fast(cast_path: Path) -> Optional[TerminalSessionMetadata]:
    """Parse only the manifest sidecar (session.json), with a minimal cast-header fallback.

    This avoids _scan_cast_timings on the (potentially large) cast file.
    Suitable for the activity-signal pipeline where timing precision is secondary.
    """
    manifest_path, events_path = _sidecar_paths(cast_path)
    session_id = _session_id(cast_path)

    manifest = _load_json_file(manifest_path)
    if manifest is None:
        # No manifest — fall back to reading just the cast header line (cheap)
        try:
            with cast_path.open("r", encoding="utf-8", errors="ignore") as fh:
                header_line = fh.readline()
            if not header_line:
                return None
            header = json.loads(header_line)
        except (OSError, json.JSONDecodeError):
            return None
        start_ts = _to_float(header.get("timestamp"))
        created_at = _local_iso_from_epoch_seconds(start_ts)
        if not created_at:
            return None
        schema_gen = _schema_generation(None, header)
        env = header.get("env") or {}
        return TerminalSessionMetadata(
            session_id=session_id,
            path=str(cast_path),
            manifest_path=None,
            events_path=str(events_path) if events_path.exists() else None,
            size_bytes=cast_path.stat().st_size,
            created_at=created_at,
            finished_at=None,
            duration_seconds=None,
            active_seconds=None,
            idle_seconds=None,
            command_count=None,
            event_count=None,
            command=_to_text(header.get("command")),
            title=_to_text(header.get("title")),
            shell=_to_text(env.get("SHELL")),
            term=_to_text(env.get("TERM")),
            term_type=None,
            term_cols=None,
            term_rows=None,
            host=_to_text(env.get("SINNIX_CAPTURE_HOST") or env.get("HOSTNAME")),
            user=_to_text(env.get("SINNIX_CAPTURE_USER")),
            tty=_to_text(env.get("SINNIX_CAPTURE_TTY")),
            terminal=_to_text(env.get("SINNIX_CAPTURE_TERMINAL")),
            start_cwd=_to_text(env.get("SINNIX_CAPTURE_START_CWD")),
            final_cwd=None,
            project_root=_to_text(env.get("SINNIX_CAPTURE_PROJECT_ROOT")),
            final_project_root=None,
            repo_root=None,
            final_repo_root=None,
            repo_branch=None,
            final_repo_branch=None,
            repo_commit=None,
            final_repo_commit=None,
            repo_dirty=None,
            final_repo_dirty=None,
            exit_code=None,
            exit_reason=None,
            recorder_exit_code=None,
            cleanup_escalated=None,
            has_events=events_path.exists(),
            timing_source="header_only",
            schema_generation=schema_gen,
            quality_status="unknown",
        )

    # Manifest path — read all fields from the JSON (fast, small file)
    schema_gen = _schema_generation(manifest, None)
    has_events = bool(manifest.get("has_events")) or events_path.exists()

    return TerminalSessionMetadata(
        session_id=_to_text(manifest.get("session_id")) or session_id,
        path=str(cast_path),
        manifest_path=str(manifest_path),
        events_path=str(events_path) if events_path.exists() else None,
        size_bytes=cast_path.stat().st_size,
        created_at=_manifest_time(manifest, "started_at", "started_at_ms"),
        finished_at=_manifest_time(manifest, "finished_at", "finished_at_ms"),
        duration_seconds=_ms_to_seconds(manifest.get("duration_ms")),
        active_seconds=_ms_to_seconds(manifest.get("active_ms")),
        idle_seconds=_ms_to_seconds(manifest.get("idle_ms")),
        command_count=_to_int(manifest.get("command_count")),
        event_count=_to_int(manifest.get("event_count")),
        command=_to_text(manifest.get("command")),
        title=_to_text(manifest.get("title")),
        shell=_to_text(manifest.get("shell")),
        term=None,
        term_type=None,
        term_cols=None,
        term_rows=None,
        host=_to_text(manifest.get("host")),
        user=_to_text(manifest.get("user")),
        tty=_to_text(manifest.get("tty")),
        terminal=_to_text(manifest.get("terminal")),
        start_cwd=_to_text(manifest.get("start_cwd")),
        final_cwd=_to_text(manifest.get("final_cwd")),
        project_root=_to_text(manifest.get("project_root")),
        final_project_root=_to_text(manifest.get("final_project_root")),
        repo_root=_to_text(manifest.get("repo_root") or manifest.get("start_repo_root")),
        final_repo_root=_to_text(manifest.get("final_repo_root")),
        repo_branch=_to_text(manifest.get("repo_branch")),
        final_repo_branch=_to_text(manifest.get("final_repo_branch")),
        repo_commit=_to_text(manifest.get("repo_commit")),
        final_repo_commit=_to_text(manifest.get("final_repo_commit")),
        repo_dirty=_to_bool(manifest.get("repo_dirty")),
        final_repo_dirty=_to_bool(manifest.get("final_repo_dirty")),
        exit_code=_to_int(manifest.get("exit_code")),
        exit_reason=_to_text(manifest.get("exit_reason")),
        recorder_exit_code=_to_int(manifest.get("recorder_exit_code")),
        cleanup_escalated=_to_bool(manifest.get("cleanup_escalated")),
        has_events=has_events,
        timing_source="manifest_fast",
        schema_generation=schema_gen,
        quality_status=_to_text(manifest.get("quality_status")) or "unknown",
        quality_flags=manifest.get("quality_flags") or [],
        field_sources={},
    )


def summarize_terminal_audit(entries: Iterator[TerminalAuditEntry]) -> TerminalAuditSummary:
    summary = TerminalAuditSummary()
    for entry in entries:
        summary.cast_count += 1
        if not entry.readable_header:
            summary.unreadable_header_count += 1
        if entry.has_manifest:
            summary.manifest_count += 1
        else:
            summary.missing_manifest_count += 1
        if entry.has_events:
            summary.events_count += 1
        else:
            summary.missing_events_count += 1
        if entry.has_command:
            summary.sessions_with_command_count += 1
        if entry.has_geometry:
            summary.sessions_with_geometry_count += 1
        if not entry.has_activity_estimate:
            summary.missing_activity_estimate_count += 1
        summary.counts_by_generation[entry.schema_generation] = (
            summary.counts_by_generation.get(entry.schema_generation, 0) + 1
        )
        summary.counts_by_status[entry.status] = summary.counts_by_status.get(entry.status, 0) + 1
        timing_key = entry.timing_source or "unknown"
        summary.counts_by_timing_source[timing_key] = summary.counts_by_timing_source.get(timing_key, 0) + 1
        version_key = "unknown" if entry.header_version is None else str(entry.header_version)
        summary.counts_by_header_version[version_key] = (
            summary.counts_by_header_version.get(version_key, 0) + 1
        )
        if entry.status == "header-only":
            summary.header_only_count += 1
        elif entry.status == "degraded":
            summary.degraded_count += 1
        elif entry.status == "damaged":
            summary.damaged_count += 1
            summary.quarantine_candidate_count += 1

        session_time = _session_time_from_id(entry.session_id)
        if session_time:
            if summary.date_range_start is None or session_time < summary.date_range_start:
                summary.date_range_start = session_time
            if summary.date_range_end is None or session_time > summary.date_range_end:
                summary.date_range_end = session_time

    return summary

def _parse_terminal_session(cast_path: Path) -> Optional[TerminalSessionMetadata]:
    header, duration_seconds, active_seconds, idle_seconds, timing_source = _read_cast_header(cast_path)
    if header is None:
        return None

    session_id = _session_id(cast_path)
    manifest_path, events_path = _sidecar_paths(cast_path)
    manifest = _load_json_file(manifest_path)
    event_summary = (
        _summarize_session_events(_parse_terminal_session_events(cast_path))
        if events_path.exists()
        else _SessionEventSummary()
    )

    field_sources: dict[str, str] = {}
    values: dict[str, Any] = {
        "session_id": session_id,
        "path": str(cast_path),
        "manifest_path": str(manifest_path) if manifest_path.exists() else None,
        "events_path": str(events_path) if events_path.exists() else None,
        "size_bytes": cast_path.stat().st_size,
        "created_at": None,
        "finished_at": None,
        "duration_seconds": None,
        "active_seconds": None,
        "idle_seconds": None,
        "command_count": None,
        "event_count": None,
        "command": None,
        "title": None,
        "shell": None,
        "term": None,
        "term_type": None,
        "term_cols": None,
        "term_rows": None,
        "host": None,
        "user": None,
        "tty": None,
        "terminal": None,
        "start_cwd": None,
        "final_cwd": None,
        "project_root": None,
        "final_project_root": None,
        "repo_root": None,
        "final_repo_root": None,
        "repo_branch": None,
        "final_repo_branch": None,
        "repo_commit": None,
        "final_repo_commit": None,
        "repo_dirty": None,
        "final_repo_dirty": None,
        "exit_code": None,
        "exit_reason": None,
        "recorder_exit_code": None,
        "cleanup_escalated": None,
    }

    def assign(field: str, value: Any, source: str) -> None:
        if _is_blank(value):
            return
        if values.get(field) is None:
            values[field] = value
            field_sources[field] = source

    if manifest:
        assign("session_id", manifest.get("session_id"), "manifest")
        assign("created_at", _manifest_time(manifest, "started_at", "started_at_ms"), "manifest")
        assign("finished_at", _manifest_time(manifest, "finished_at", "finished_at_ms"), "manifest")
        assign("duration_seconds", _ms_to_seconds(manifest.get("duration_ms")), "manifest")
        assign("active_seconds", _ms_to_seconds(manifest.get("active_ms")), "manifest")
        assign("idle_seconds", _ms_to_seconds(manifest.get("idle_ms")), "manifest")
        assign("command_count", _to_int(manifest.get("command_count")), "manifest")
        assign("event_count", _to_int(manifest.get("event_count")), "manifest")
        assign("command", _to_text(manifest.get("command")), "manifest")
        assign("title", _to_text(manifest.get("title")), "manifest")
        assign("shell", _to_text(manifest.get("shell")), "manifest")
        assign("host", _to_text(manifest.get("host")), "manifest")
        assign("user", _to_text(manifest.get("user")), "manifest")
        assign("tty", _to_text(manifest.get("tty")), "manifest")
        assign("terminal", _to_text(manifest.get("terminal")), "manifest")
        assign("start_cwd", _to_text(manifest.get("start_cwd")), "manifest")
        assign("final_cwd", _to_text(manifest.get("final_cwd")), "manifest")
        assign("project_root", _to_text(manifest.get("project_root")), "manifest")
        assign("final_project_root", _to_text(manifest.get("final_project_root")), "manifest")
        assign("repo_root", _to_text(manifest.get("repo_root") or manifest.get("start_repo_root")), "manifest")
        assign("final_repo_root", _to_text(manifest.get("final_repo_root")), "manifest")
        assign("repo_branch", _to_text(manifest.get("repo_branch")), "manifest")
        assign("final_repo_branch", _to_text(manifest.get("final_repo_branch")), "manifest")
        assign("repo_commit", _to_text(manifest.get("repo_commit")), "manifest")
        assign("final_repo_commit", _to_text(manifest.get("final_repo_commit")), "manifest")
        assign("repo_dirty", _to_bool(manifest.get("repo_dirty")), "manifest")
        assign("final_repo_dirty", _to_bool(manifest.get("final_repo_dirty")), "manifest")
        assign("exit_code", _to_int(manifest.get("exit_code")), "manifest")
        assign("exit_reason", _to_text(manifest.get("exit_reason")), "manifest")
        assign("recorder_exit_code", _to_int(manifest.get("recorder_exit_code")), "manifest")
        assign("cleanup_escalated", _to_bool(manifest.get("cleanup_escalated")), "manifest")

    start_ts = _to_float(header.get("timestamp"))
    created_at = _local_iso_from_epoch_seconds(start_ts)
    finished_at = _local_iso_from_epoch_seconds(start_ts + duration_seconds) if start_ts is not None else None
    env = header.get("env") or {}
    term = header.get("term") or {}

    assign("created_at", created_at, "cast_header")
    assign("finished_at", finished_at, "cast_header")
    assign("duration_seconds", duration_seconds, "cast_header")
    assign("active_seconds", active_seconds, "cast_header")
    assign("idle_seconds", idle_seconds, "cast_header")
    assign("command", _to_text(header.get("command")), "cast_header")
    assign("title", _to_text(header.get("title")), "cast_header")
    assign("shell", _to_text(env.get("SHELL")), "cast_header")
    assign("term", _to_text(env.get("TERM")), "cast_header")
    assign("term_type", _to_text(term.get("type")), "cast_header")
    assign("term_cols", _to_int(term.get("cols") or header.get("width")), "cast_header")
    assign("term_rows", _to_int(term.get("rows") or header.get("height")), "cast_header")
    assign("host", _to_text(env.get("SINNIX_CAPTURE_HOST") or env.get("HOSTNAME")), "cast_header")
    assign("user", _to_text(env.get("SINNIX_CAPTURE_USER")), "cast_header")
    assign("tty", _to_text(env.get("SINNIX_CAPTURE_TTY")), "cast_header")
    assign("terminal", _to_text(env.get("SINNIX_CAPTURE_TERMINAL")), "cast_header")
    assign("start_cwd", _to_text(env.get("SINNIX_CAPTURE_START_CWD")), "cast_header")
    assign("project_root", _to_text(env.get("SINNIX_CAPTURE_PROJECT_ROOT")), "cast_header")
    assign("repo_root", _to_text(env.get("SINNIX_CAPTURE_START_REPO_ROOT") or env.get("SINNIX_CAPTURE_REPO_ROOT")), "cast_header")
    assign("repo_branch", _to_text(env.get("SINNIX_CAPTURE_START_REPO_BRANCH")), "cast_header")
    assign("repo_commit", _to_text(env.get("SINNIX_CAPTURE_START_REPO_COMMIT")), "cast_header")
    assign("repo_dirty", _to_bool(env.get("SINNIX_CAPTURE_START_REPO_DIRTY")), "cast_header")
    assign("session_id", _to_text(env.get("SINNIX_CAPTURE_SESSION_ID")), "cast_header")

    assign("created_at", event_summary.started_at, "events")
    assign("finished_at", event_summary.finished_at, "events")
    assign("command_count", event_summary.command_count, "events")
    assign("event_count", event_summary.event_count, "events")
    assign("active_seconds", event_summary.active_seconds, "events")
    assign("idle_seconds", event_summary.idle_seconds, "events")
    assign("command", event_summary.first_command, "events")
    assign("start_cwd", event_summary.start_cwd, "events")
    assign("final_cwd", event_summary.final_cwd, "events")
    assign("project_root", event_summary.project_root, "events")
    assign("final_project_root", event_summary.final_project_root, "events")
    assign("repo_root", event_summary.repo_root, "events")
    assign("final_repo_root", event_summary.final_repo_root, "events")
    assign("repo_branch", event_summary.repo_branch, "events")
    assign("final_repo_branch", event_summary.final_repo_branch, "events")
    assign("repo_commit", event_summary.repo_commit, "events")
    assign("final_repo_commit", event_summary.final_repo_commit, "events")
    assign("repo_dirty", event_summary.repo_dirty, "events")
    assign("final_repo_dirty", event_summary.final_repo_dirty, "events")
    assign("exit_code", event_summary.exit_code, "events")

    if values["project_root"] is None:
        assign("project_root", _guess_project_root(values["start_cwd"] or values["repo_root"]), "derived")
    if values["final_project_root"] is None:
        assign("final_project_root", _guess_project_root(values["final_cwd"] or values["final_repo_root"]), "derived")
    if values["repo_root"] is None:
        assign("repo_root", _guess_project_root(values["start_cwd"]), "derived")
    if values["final_repo_root"] is None:
        assign("final_repo_root", _guess_project_root(values["final_cwd"]), "derived")

    schema_generation = _schema_generation(manifest, header)
    has_events = events_path.exists()

    if values["finished_at"] is None and values["created_at"] is not None and values["duration_seconds"] is not None:
        created_dt = _parse_iso_datetime(values["created_at"])
        if created_dt is not None:
            derived_finished = (created_dt + timedelta(seconds=float(values["duration_seconds"]))).isoformat()
            assign("finished_at", derived_finished, "derived")

    if values["duration_seconds"] is None:
        assign(
            "duration_seconds",
            _duration_between(values["created_at"], values["finished_at"]),
            "derived",
        )

    if values["active_seconds"] is None and timing_source in {"tail", "full-fallback"}:
        full_timing = _read_cast_full_timing(cast_path)
        if full_timing.active_seconds is not None:
            values["duration_seconds"] = full_timing.duration_seconds
            values["active_seconds"] = full_timing.active_seconds
            values["idle_seconds"] = full_timing.idle_seconds
            field_sources["duration_seconds"] = "cast_full_timing"
            field_sources["active_seconds"] = "cast_full_timing"
            field_sources["idle_seconds"] = "cast_full_timing"
            timing_source = "full-backfill"

    quality_status, quality_flags = _assess_session_quality(
        manifest_exists=manifest_path.exists(),
        has_events=has_events,
        schema_generation=schema_generation,
        created_at=values["created_at"],
        finished_at=values["finished_at"],
        duration_seconds=values["duration_seconds"],
        active_seconds=values["active_seconds"],
        command=values["command"],
        timing_source=timing_source,
    )

    return TerminalSessionMetadata(
        session_id=str(values["session_id"] or session_id),
        path=str(cast_path),
        manifest_path=values["manifest_path"],
        events_path=values["events_path"],
        size_bytes=values["size_bytes"],
        created_at=values["created_at"],
        finished_at=values["finished_at"],
        duration_seconds=values["duration_seconds"],
        active_seconds=values["active_seconds"],
        idle_seconds=values["idle_seconds"],
        command_count=values["command_count"],
        event_count=values["event_count"],
        command=values["command"],
        title=values["title"],
        shell=values["shell"],
        term=values["term"],
        term_type=values["term_type"],
        term_cols=values["term_cols"],
        term_rows=values["term_rows"],
        host=values["host"],
        user=values["user"],
        tty=values["tty"],
        terminal=values["terminal"],
        start_cwd=values["start_cwd"],
        final_cwd=values["final_cwd"],
        project_root=values["project_root"],
        final_project_root=values["final_project_root"],
        repo_root=values["repo_root"],
        final_repo_root=values["final_repo_root"],
        repo_branch=values["repo_branch"],
        final_repo_branch=values["final_repo_branch"],
        repo_commit=values["repo_commit"],
        final_repo_commit=values["final_repo_commit"],
        repo_dirty=values["repo_dirty"],
        final_repo_dirty=values["final_repo_dirty"],
        exit_code=values["exit_code"],
        exit_reason=values["exit_reason"],
        recorder_exit_code=values["recorder_exit_code"],
        cleanup_escalated=values["cleanup_escalated"],
        has_events=has_events,
        timing_source=timing_source,
        schema_generation=schema_generation,
        quality_status=quality_status,
        quality_flags=quality_flags,
        field_sources=field_sources,
    )


def _parse_terminal_session_events(cast_path: Path) -> Iterator[TerminalSessionEvent]:
    session_id = _session_id(cast_path)
    manifest_path, events_path = _sidecar_paths(cast_path)
    manifest = _load_json_file(manifest_path)
    schema_generation = _schema_generation(manifest, None)

    if events_path.exists():
        try:
            with events_path.open("r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    time = _to_text(payload.get("time")) or _local_iso_from_epoch_ms(payload.get("ts_ms"))
                    pwd = _to_text(payload.get("cwd") or payload.get("pwd"))
                    repo_root = _to_text(payload.get("repo_root"))
                    project_root = _to_text(payload.get("project_root")) or _guess_project_root(pwd or repo_root)

                    yield TerminalSessionEvent(
                        session_id=str(payload.get("session_id") or session_id),
                        cast_path=str(cast_path),
                        schema_generation=schema_generation,
                        source="events_jsonl",
                        time=time,
                        type=str(payload.get("type") or "unknown"),
                        pwd=pwd,
                        project_root=project_root,
                        repo_root=repo_root,
                        repo_branch=_to_text(payload.get("repo_branch")),
                        repo_commit=_to_text(payload.get("repo_commit")),
                        repo_dirty=_to_bool(payload.get("repo_dirty")),
                        exit_code=_to_int(
                            payload.get("exit_code")
                            if payload.get("exit_code") is not None
                            else payload.get("status")
                        ),
                        payload=payload,
                    )
        except OSError:
            return


def _audit_terminal_session(cast_path: Path) -> Optional[TerminalAuditEntry]:
    session_id = _session_id(cast_path)
    manifest_path, events_path = _sidecar_paths(cast_path)
    manifest = _load_json_file(manifest_path)
    header, duration_seconds, _, _, timing_source = _read_cast_header(cast_path)
    if header is None:
        return TerminalAuditEntry(
            path=str(cast_path),
            session_id=session_id,
            schema_generation=_schema_generation(manifest, None),
            readable_header=False,
            header_version=None,
            has_manifest=manifest_path.exists(),
            has_events=events_path.exists(),
            has_command=False,
            has_geometry=False,
            has_activity_estimate=False,
            duration_seconds=None,
            timing_source=None,
            status="damaged",
            issues=["unreadable_header"],
        )

    session = _parse_terminal_session(cast_path)
    term = header.get("term") or {}

    return TerminalAuditEntry(
        path=str(cast_path),
        session_id=session_id,
        schema_generation=(session.schema_generation if session else _schema_generation(manifest, header)),
        readable_header=True,
        header_version=_to_int(header.get("version")),
        has_manifest=manifest_path.exists(),
        has_events=events_path.exists(),
        has_command=(session.command is not None) if session else (_to_text(header.get("command")) is not None),
        has_geometry=_to_int(term.get("cols") or header.get("width")) is not None and _to_int(term.get("rows") or header.get("height")) is not None,
        has_activity_estimate=session.active_seconds is not None if session else False,
        duration_seconds=session.duration_seconds if session else duration_seconds,
        timing_source=session.timing_source if session else timing_source,
        status=session.quality_status if session else "ok",
        issues=list(session.quality_flags) if session else [],
    )

def _summarize_session_events(events: Iterator[TerminalSessionEvent]) -> _SessionEventSummary:
    summary = _SessionEventSummary(command_count=0, event_count=0)
    previous_event_dt: Optional[datetime] = None
    active_seconds = 0.0
    idle_seconds = 0.0
    saw_activity_time = False

    for event in events:
        summary.event_count = (summary.event_count or 0) + 1
        if event.time:
            summary.first_event_time = summary.first_event_time or event.time
            summary.last_event_time = event.time
        event_dt = _parse_iso_datetime(event.time)
        if event_dt is not None:
            saw_activity_time = True
            if previous_event_dt is not None:
                delta = max((event_dt - previous_event_dt).total_seconds(), 0.0)
                active_seconds += min(delta, ACTIVE_GAP_SECONDS)
                idle_seconds += max(delta - ACTIVE_GAP_SECONDS, 0.0)
            previous_event_dt = event_dt
        if event.type == "session_start":
            summary.started_at = summary.started_at or event.time
            summary.start_cwd = summary.start_cwd or event.pwd
            summary.project_root = summary.project_root or event.project_root
            summary.repo_root = summary.repo_root or event.repo_root
            summary.repo_branch = summary.repo_branch or event.repo_branch
            summary.repo_commit = summary.repo_commit or event.repo_commit
            summary.repo_dirty = summary.repo_dirty if summary.repo_dirty is not None else event.repo_dirty
        elif event.type == "command_start":
            summary.command_count = (summary.command_count or 0) + 1
            command = _to_text(event.payload.get("command") or event.payload.get("cmd"))
            summary.first_command = summary.first_command or command
            summary.start_cwd = summary.start_cwd or event.pwd
            summary.final_cwd = event.pwd or summary.final_cwd
            summary.project_root = summary.project_root or event.project_root
            summary.final_project_root = event.project_root or summary.final_project_root
            summary.repo_root = summary.repo_root or event.repo_root
            summary.final_repo_root = event.repo_root or summary.final_repo_root
            summary.repo_branch = summary.repo_branch or event.repo_branch
            summary.final_repo_branch = event.repo_branch or summary.final_repo_branch
            summary.repo_commit = summary.repo_commit or event.repo_commit
            summary.final_repo_commit = event.repo_commit or summary.final_repo_commit
            summary.repo_dirty = summary.repo_dirty if summary.repo_dirty is not None else event.repo_dirty
            summary.final_repo_dirty = event.repo_dirty if event.repo_dirty is not None else summary.final_repo_dirty
        elif event.type in {"location", "cwd"}:
            summary.start_cwd = summary.start_cwd or event.pwd
            summary.final_cwd = event.pwd or summary.final_cwd
            summary.project_root = summary.project_root or event.project_root
            summary.final_project_root = event.project_root or summary.final_project_root
            summary.repo_root = summary.repo_root or event.repo_root
            summary.final_repo_root = event.repo_root or summary.final_repo_root
            summary.repo_branch = summary.repo_branch or event.repo_branch
            summary.final_repo_branch = event.repo_branch or summary.final_repo_branch
            summary.repo_commit = summary.repo_commit or event.repo_commit
            summary.final_repo_commit = event.repo_commit or summary.final_repo_commit
            summary.repo_dirty = summary.repo_dirty if summary.repo_dirty is not None else event.repo_dirty
            summary.final_repo_dirty = event.repo_dirty if event.repo_dirty is not None else summary.final_repo_dirty
        elif event.type in {"session_end", "shell_exit"}:
            summary.finished_at = event.time or summary.finished_at
            summary.final_cwd = event.pwd or summary.final_cwd
            summary.final_project_root = event.project_root or summary.final_project_root
            summary.final_repo_root = event.repo_root or summary.final_repo_root
            summary.final_repo_branch = event.repo_branch or summary.final_repo_branch
            summary.final_repo_commit = event.repo_commit or summary.final_repo_commit
            summary.final_repo_dirty = event.repo_dirty if event.repo_dirty is not None else summary.final_repo_dirty
            summary.exit_code = event.exit_code if event.exit_code is not None else summary.exit_code
        elif event.type == "command_end":
            summary.exit_code = event.exit_code if event.exit_code is not None else summary.exit_code

    if saw_activity_time:
        summary.active_seconds = active_seconds
        summary.idle_seconds = idle_seconds
    summary.started_at = summary.started_at or summary.first_event_time
    summary.finished_at = summary.finished_at or summary.last_event_time

    return summary
