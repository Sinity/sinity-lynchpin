"""Instrumentation metadata collection."""

from __future__ import annotations

from collections import defaultdict
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import typer

from ..sources.captures.instrumentation import (
    TerminalSessionEvent,
    TerminalSessionMetadata,
    _summarize_session_events,
    iter_audio_recordings,
    iter_screenshots,
    iter_terminal_audit,
    iter_terminal_session_events,
    iter_terminal_sessions,
    summarize_terminal_audit,
)

app = typer.Typer(help="Instrumentation metadata collection")


def _write_jsonl(output: Path, records) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
            count += 1
    return count


def _write_json(output: Path, payload: object) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _iso_to_epoch_ms(value: str | None) -> int | None:
    value = _clean_text(value)
    if not value:
        return None
    return int(datetime.fromisoformat(value).timestamp() * 1000)


def _seconds_to_ms(value: float | None) -> int | None:
    if value is None:
        return None
    return int(value * 1000)


def _jsonl_payload(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, sort_keys=False)


def _coalesce(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    while len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1].strip()
    return text or None


def _clean_int(value: Any) -> int | None:
    text = _clean_text(value)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _canonical_event_payload(
    session: TerminalSessionMetadata,
    *,
    event_type: str,
    time: str | None,
    cwd: str | None,
    project_root: str | None,
    repo_root: str | None,
    repo_branch: str | None,
    repo_commit: str | None,
    repo_dirty: bool | None,
    exit_code: int | None = None,
    command: str | None = None,
    duration_ms: int | None = None,
    exit_reason: str | None = None,
    active_ms: int | None = None,
    idle_ms: int | None = None,
    tty: str | None = None,
    terminal: str | None = None,
    synthetic: bool = False,
) -> dict[str, Any]:
    event_type = _clean_text(event_type) or "unknown"
    time = _clean_text(time)
    cwd = _clean_text(cwd)
    project_root = _clean_text(project_root)
    repo_root = _clean_text(repo_root)
    repo_branch = _clean_text(repo_branch)
    repo_commit = _clean_text(repo_commit)
    command = _clean_text(command)
    exit_reason = _clean_text(exit_reason)
    tty = _clean_text(tty)
    terminal = _clean_text(terminal)
    duration_ms = _clean_int(duration_ms)
    active_ms = _clean_int(active_ms)
    idle_ms = _clean_int(idle_ms)
    payload: dict[str, Any] = {
        "type": event_type,
        "session_id": session.session_id,
        "ts_ms": _iso_to_epoch_ms(time),
        "time": time,
    }
    if cwd is not None:
        payload["cwd"] = cwd
    if project_root is not None:
        payload["project_root"] = project_root
    if repo_root is not None:
        payload["repo_root"] = repo_root
    if repo_branch is not None:
        payload["repo_branch"] = repo_branch
    if repo_commit is not None:
        payload["repo_commit"] = repo_commit
    if repo_dirty is not None:
        payload["repo_dirty"] = repo_dirty
    if exit_code is not None:
        payload["exit_code"] = exit_code
    if command is not None:
        payload["command"] = command
    if duration_ms is not None:
        payload["duration_ms"] = duration_ms
    if exit_reason is not None:
        payload["exit_reason"] = exit_reason
    if active_ms is not None:
        payload["active_ms"] = active_ms
    if idle_ms is not None:
        payload["idle_ms"] = idle_ms
    if tty is not None:
        payload["tty"] = tty
    if terminal is not None:
        payload["terminal"] = terminal
    if synthetic:
        payload["synthetic"] = True
    return payload


def _canonical_events_for_session(
    session: TerminalSessionMetadata,
    events: list[TerminalSessionEvent],
) -> list[dict[str, Any]]:
    if events:
        canonical: list[dict[str, Any]] = []
        for event in events:
            payload = event.payload or {}
            canonical.append(
                _canonical_event_payload(
                    session,
                    event_type=event.type,
                    time=event.time,
                    cwd=event.pwd,
                    project_root=event.project_root,
                    repo_root=event.repo_root,
                    repo_branch=event.repo_branch,
                    repo_commit=event.repo_commit,
                    repo_dirty=event.repo_dirty,
                    exit_code=event.exit_code,
                    command=payload.get("command") or payload.get("cmd"),
                    duration_ms=payload.get("duration_ms"),
                    exit_reason=payload.get("exit_reason"),
                    active_ms=payload.get("active_ms"),
                    idle_ms=payload.get("idle_ms"),
                    tty=payload.get("tty"),
                    terminal=payload.get("terminal"),
                )
            )
        return canonical

    return [
        _canonical_event_payload(
            session,
            event_type="session_start",
            time=session.created_at,
            cwd=session.start_cwd,
            project_root=session.project_root,
            repo_root=session.repo_root,
            repo_branch=session.repo_branch,
            repo_commit=session.repo_commit,
            repo_dirty=session.repo_dirty,
            tty=session.tty,
            terminal=session.terminal,
            synthetic=True,
        ),
        _canonical_event_payload(
            session,
            event_type="session_end",
            time=session.finished_at or session.created_at,
            cwd=session.final_cwd or session.start_cwd,
            project_root=session.final_project_root or session.project_root,
            repo_root=session.final_repo_root or session.repo_root,
            repo_branch=session.final_repo_branch or session.repo_branch,
            repo_commit=session.final_repo_commit or session.repo_commit,
            repo_dirty=session.final_repo_dirty if session.final_repo_dirty is not None else session.repo_dirty,
            exit_code=session.exit_code,
            exit_reason=session.exit_reason,
            active_ms=_seconds_to_ms(session.active_seconds),
            idle_ms=_seconds_to_ms(session.idle_seconds),
            synthetic=True,
        ),
    ]


def _canonical_manifest(
    session: TerminalSessionMetadata,
    *,
    cast_path: Path,
    events_path: Path,
    event_count: int,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    overrides = overrides or {}
    command_count = _coalesce(overrides.get("command_count"), session.command_count)
    if command_count is None:
        command_count = 1 if _coalesce(overrides.get("command"), session.command) else 0
    return {
        "schema": "terminal-session-v1",
        "schema_generation": "terminal-session-v1",
        "session_id": session.session_id,
        "cast_path": str(cast_path),
        "events_path": str(events_path),
        "host": session.host,
        "user": session.user,
        "tty": session.tty,
        "terminal": session.terminal,
        "shell": session.shell,
        "term_type": session.term_type,
        "start_cwd": _coalesce(overrides.get("start_cwd"), session.start_cwd),
        "final_cwd": _coalesce(overrides.get("final_cwd"), session.final_cwd),
        "project_root": _coalesce(overrides.get("project_root"), session.project_root),
        "final_project_root": _coalesce(overrides.get("final_project_root"), session.final_project_root),
        "repo_root": _coalesce(overrides.get("repo_root"), session.repo_root),
        "final_repo_root": _coalesce(overrides.get("final_repo_root"), session.final_repo_root),
        "repo_branch": _coalesce(overrides.get("repo_branch"), session.repo_branch),
        "final_repo_branch": _coalesce(overrides.get("final_repo_branch"), session.final_repo_branch),
        "repo_commit": _coalesce(overrides.get("repo_commit"), session.repo_commit),
        "final_repo_commit": _coalesce(overrides.get("final_repo_commit"), session.final_repo_commit),
        "repo_dirty": _coalesce(overrides.get("repo_dirty"), session.repo_dirty),
        "final_repo_dirty": _coalesce(overrides.get("final_repo_dirty"), session.final_repo_dirty),
        "started_at_ms": _iso_to_epoch_ms(session.created_at),
        "finished_at_ms": _iso_to_epoch_ms(session.finished_at),
        "duration_ms": _seconds_to_ms(session.duration_seconds),
        "active_ms": _seconds_to_ms(session.active_seconds),
        "idle_ms": _seconds_to_ms(session.idle_seconds),
        "command_count": command_count,
        "event_count": event_count,
        "command": _coalesce(overrides.get("command"), session.command),
        "exit_code": _coalesce(overrides.get("exit_code"), session.exit_code),
        "exit_reason": _coalesce(overrides.get("exit_reason"), session.exit_reason),
        "recorder_exit_code": session.recorder_exit_code,
        "cleanup_escalated": session.cleanup_escalated,
    }


def _migration_target_dir(root: Path, session: TerminalSessionMetadata) -> Path:
    created_at = session.created_at
    if not created_at:
        raise ValueError(f"missing created_at for {session.session_id}")
    created = datetime.fromisoformat(created_at)
    return root / created.strftime("%Y/%m/%d") / session.session_id


def _canonical_manifest_overrides(events: list[TerminalSessionEvent]) -> dict[str, Any]:
    if not events:
        return {}
    summary = _summarize_session_events(iter(events))
    return {
        "start_cwd": summary.start_cwd,
        "final_cwd": summary.final_cwd,
        "project_root": summary.project_root,
        "final_project_root": summary.final_project_root,
        "repo_root": summary.repo_root,
        "final_repo_root": summary.final_repo_root,
        "repo_branch": summary.repo_branch,
        "final_repo_branch": summary.final_repo_branch,
        "repo_commit": summary.repo_commit,
        "final_repo_commit": summary.final_repo_commit,
        "repo_dirty": summary.repo_dirty,
        "final_repo_dirty": summary.final_repo_dirty,
        "command_count": summary.command_count,
        "command": summary.first_command,
        "exit_code": summary.exit_code,
    }


def _write_audit_bundle(
    root: Path,
    *,
    sessions_output: Path,
    events_output: Path,
    audit_output: Path,
    audit_detail_output: Path,
    quarantine_output: Path,
) -> tuple[int, int, int, int]:
    session_count = _write_jsonl(sessions_output, iter_terminal_sessions(root))
    event_count = _write_jsonl(events_output, iter_terminal_session_events(root))
    entries = list(iter_terminal_audit(root))
    summary = summarize_terminal_audit(iter(entries))
    _write_json(audit_output, asdict(summary))
    audit_detail_output.parent.mkdir(parents=True, exist_ok=True)
    quarantine_output.parent.mkdir(parents=True, exist_ok=True)
    with audit_detail_output.open("w", encoding="utf-8") as audit_fh:
        with quarantine_output.open("w", encoding="utf-8") as quarantine_fh:
            quarantine_count = 0
            for entry in entries:
                payload = json.dumps(asdict(entry), ensure_ascii=False)
                audit_fh.write(payload + "\n")
                if entry.status == "damaged":
                    quarantine_fh.write(payload + "\n")
                    quarantine_count += 1
    return session_count, event_count, len(entries), quarantine_count


@app.command("migrate-terminal-corpus")
def migrate_terminal_corpus(
    root: Path = typer.Option(Path("/realm/data/captures/asciinema"), "--root"),
    report_output: Path = typer.Option(
        Path("artefacts/ingest/instrumentation/terminal_corpus_migration.json"),
        "--report-output",
    ),
    manifest_output: Path = typer.Option(
        Path("artefacts/ingest/instrumentation/terminal_corpus_migration.jsonl"),
        "--manifest-output",
    ),
    apply: bool = typer.Option(False, "--apply", help="Apply the in-place migration."),
) -> None:
    """Normalize flat historical terminal captures into canonical session directories."""

    sessions = [session for session in iter_terminal_sessions(root) if Path(session.path).name != "session.cast"]
    events_by_cast: dict[str, list[TerminalSessionEvent]] = defaultdict(list)
    for event in iter_terminal_session_events(root):
        cast_path = Path(event.cast_path)
        if cast_path.name == "session.cast":
            continue
        events_by_cast[event.cast_path].append(event)

    migration_rows: list[dict[str, Any]] = []
    synthetic_sessions = 0
    legacy_meta_paths: list[Path] = []
    for session in sessions:
        source_cast = Path(session.path)
        source_meta = source_cast.with_suffix(source_cast.suffix + ".meta")
        target_dir = _migration_target_dir(root, session)
        target_cast = target_dir / "session.cast"
        target_session = target_dir / "session.json"
        target_events = target_dir / "events.jsonl"
        session_events = events_by_cast.get(session.path, [])
        canonical_events = _canonical_events_for_session(session, session_events)
        manifest_overrides = _canonical_manifest_overrides(session_events)
        synthetic = len(session_events) == 0
        if synthetic:
            synthetic_sessions += 1

        if target_cast.exists() and target_cast != source_cast:
            raise typer.BadParameter(f"target cast already exists for {session.session_id}: {target_cast}")

        migration_rows.append(
            {
                "session_id": session.session_id,
                "source_cast_path": str(source_cast),
                "source_legacy_meta_path": str(source_meta) if source_meta.exists() else None,
                "target_dir": str(target_dir),
                "target_cast_path": str(target_cast),
                "target_session_path": str(target_session),
                "target_events_path": str(target_events),
                "schema_generation": session.schema_generation,
                "quality_status": session.quality_status,
                "timing_source": session.timing_source,
                "synthetic_events": synthetic,
                "event_count": len(canonical_events),
                "command_count": _coalesce(
                    manifest_overrides.get("command_count"),
                    session.command_count,
                    1 if _coalesce(manifest_overrides.get("command"), session.command) else 0,
                ),
                "manifest": _canonical_manifest(
                    session,
                    cast_path=target_cast,
                    events_path=target_events,
                    event_count=len(canonical_events),
                    overrides=manifest_overrides,
                ),
                "events": canonical_events,
            }
        )
        if source_meta.exists():
            legacy_meta_paths.append(source_meta)

    summary = {
        "root": str(root),
        "sessions_to_migrate": len(migration_rows),
        "synthetic_event_sessions": synthetic_sessions,
        "legacy_meta_sidecars": len(legacy_meta_paths),
        "apply": apply,
    }
    _write_json(report_output, summary)
    manifest_output.parent.mkdir(parents=True, exist_ok=True)
    with manifest_output.open("w", encoding="utf-8") as fh:
        for row in migration_rows:
            fh.write(_jsonl_payload({k: v for k, v in row.items() if k not in {"manifest", "events"}}) + "\n")

    if not apply:
        typer.secho(f"✓ Planned migration for {len(migration_rows)} sessions → {report_output}", fg=typer.colors.GREEN)
        typer.secho(f"✓ Wrote migration manifest → {manifest_output}", fg=typer.colors.GREEN)
        return

    for row in migration_rows:
        source_cast = Path(row["source_cast_path"])
        source_meta_path = row["source_legacy_meta_path"]
        target_dir = Path(row["target_dir"])
        target_cast = Path(row["target_cast_path"])
        target_session = Path(row["target_session_path"])
        target_events = Path(row["target_events_path"])

        target_dir.mkdir(parents=True, exist_ok=True)
        if source_cast.exists():
            source_cast.rename(target_cast)
        target_session.write_text(json.dumps(row["manifest"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        with target_events.open("w", encoding="utf-8") as events_fh:
            for event in row["events"]:
                events_fh.write(_jsonl_payload(event) + "\n")
        if source_meta_path:
            meta_path = Path(source_meta_path)
            if meta_path.exists():
                meta_path.unlink()

    typer.secho(f"✓ Migrated {len(migration_rows)} sessions in place", fg=typer.colors.GREEN)
    typer.secho(f"✓ Wrote migration summary → {report_output}", fg=typer.colors.GREEN)
    typer.secho(f"✓ Wrote migration manifest → {manifest_output}", fg=typer.colors.GREEN)


@app.command("terminal-sessions")
def terminal_sessions(
    root: Path = typer.Option(Path("/realm/data/captures/asciinema"), "--root"),
    output: Path = typer.Option(
        Path("artefacts/ingest/instrumentation/terminal_sessions.jsonl"),
        "--output",
    ),
) -> None:
    """Collect terminal session metadata."""

    count = _write_jsonl(output, iter_terminal_sessions(root))
    typer.secho(f"✓ Wrote {count} terminal sessions → {output}", fg=typer.colors.GREEN)


@app.command("terminal-events")
def terminal_events(
    root: Path = typer.Option(Path("/realm/data/captures/asciinema"), "--root"),
    output: Path = typer.Option(
        Path("artefacts/ingest/instrumentation/terminal_session_events.jsonl"),
        "--output",
    ),
) -> None:
    """Collect terminal session events."""

    count = _write_jsonl(output, iter_terminal_session_events(root))
    typer.secho(f"✓ Wrote {count} terminal events → {output}", fg=typer.colors.GREEN)


@app.command("audit-terminal")
def audit_terminal(
    root: Path = typer.Option(Path("/realm/data/captures/asciinema"), "--root"),
    output: Path = typer.Option(
        Path("artefacts/ingest/instrumentation/terminal_capture_audit.json"),
        "--output",
    ),
    detail_output: Path = typer.Option(
        Path("artefacts/ingest/instrumentation/terminal_capture_audit.jsonl"),
        "--detail-output",
    ),
    quarantine_output: Path = typer.Option(
        Path("artefacts/ingest/instrumentation/terminal_capture_quarantine.jsonl"),
        "--quarantine-output",
    ),
) -> None:
    """Audit terminal capture corpus health."""

    entries = list(iter_terminal_audit(root))
    summary = summarize_terminal_audit(iter(entries))
    detail_output.parent.mkdir(parents=True, exist_ok=True)
    quarantine_output.parent.mkdir(parents=True, exist_ok=True)
    _write_json(output, asdict(summary))
    quarantine_count = 0
    with detail_output.open("w", encoding="utf-8") as detail_fh:
        with quarantine_output.open("w", encoding="utf-8") as quarantine_fh:
            for entry in entries:
                payload = json.dumps(asdict(entry), ensure_ascii=False)
                detail_fh.write(payload + "\n")
                if entry.status == "damaged":
                    quarantine_fh.write(payload + "\n")
                    quarantine_count += 1
    typer.secho(f"✓ Wrote terminal audit summary → {output}", fg=typer.colors.GREEN)
    typer.secho(f"✓ Wrote {len(entries)} terminal audit rows → {detail_output}", fg=typer.colors.GREEN)
    typer.secho(f"✓ Wrote {quarantine_count} quarantine candidates → {quarantine_output}", fg=typer.colors.GREEN)


@app.command("terminal-metadata")
def terminal_metadata(
    root: Path = typer.Option(Path("/realm/data/captures/asciinema"), "--root"),
    sessions_output: Path = typer.Option(
        Path("artefacts/ingest/instrumentation/terminal_sessions.jsonl"),
        "--sessions-output",
    ),
    events_output: Path = typer.Option(
        Path("artefacts/ingest/instrumentation/terminal_session_events.jsonl"),
        "--events-output",
    ),
    audit_output: Path = typer.Option(
        Path("artefacts/ingest/instrumentation/terminal_capture_audit.json"),
        "--audit-output",
    ),
    audit_detail_output: Path = typer.Option(
        Path("artefacts/ingest/instrumentation/terminal_capture_audit.jsonl"),
        "--audit-detail-output",
    ),
    quarantine_output: Path = typer.Option(
        Path("artefacts/ingest/instrumentation/terminal_capture_quarantine.jsonl"),
        "--quarantine-output",
    ),
) -> None:
    """Collect terminal sessions/events and audit outputs in one command."""

    session_count, event_count, audit_count, quarantine_count = _write_audit_bundle(
        root,
        sessions_output=sessions_output,
        events_output=events_output,
        audit_output=audit_output,
        audit_detail_output=audit_detail_output,
        quarantine_output=quarantine_output,
    )
    typer.secho(f"✓ Wrote {session_count} terminal sessions → {sessions_output}", fg=typer.colors.GREEN)
    typer.secho(f"✓ Wrote {event_count} terminal events → {events_output}", fg=typer.colors.GREEN)
    typer.secho(f"✓ Wrote terminal audit summary → {audit_output}", fg=typer.colors.GREEN)
    typer.secho(f"✓ Wrote {audit_count} terminal audit rows → {audit_detail_output}", fg=typer.colors.GREEN)
    typer.secho(f"✓ Wrote {quarantine_count} quarantine candidates → {quarantine_output}", fg=typer.colors.GREEN)


@app.command()
def audio(
    root: Path = typer.Option(Path("/realm/data/captures/audio/raw"), "--root"),
    output: Path = typer.Option(
        Path("artefacts/ingest/instrumentation/audio_metadata.jsonl"), "--output"
    ),
) -> None:
    """Collect audio recording metadata."""

    count = _write_jsonl(output, iter_audio_recordings(root))
    typer.secho(f"✓ Wrote {count} audio metadata records → {output}", fg=typer.colors.GREEN)


@app.command()
def screen(
    root: Path = typer.Option(Path("/realm/data/captures/screenshot"), "--root"),
    output: Path = typer.Option(
        Path("artefacts/ingest/instrumentation/screen_metadata.jsonl"), "--output"
    ),
) -> None:
    """Collect screenshot/screen recording metadata."""

    count = _write_jsonl(output, iter_screenshots(root))
    typer.secho(f"✓ Wrote {count} screen metadata records → {output}", fg=typer.colors.GREEN)


if __name__ == "__main__":
    app()
