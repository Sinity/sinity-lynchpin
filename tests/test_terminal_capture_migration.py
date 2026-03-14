from __future__ import annotations

import json
from pathlib import Path

from lynchpin.ingest.instrumentation import terminal_metadata


def _write_cast(path: Path) -> None:
    header = {
        "version": 2,
        "width": 120,
        "height": 40,
        "timestamp": 1745806433,
        "env": {
            "SHELL": "/bin/zsh",
            "TERM": "xterm-256color",
            "SINNIX_CAPTURE_HOST": "sinnix-prime",
            "SINNIX_CAPTURE_USER": "sinity",
        },
    }
    lines = [
        json.dumps(header),
        json.dumps([0.0, "o", "$ "]),
        json.dumps([12.5, "o", "done\r\n"]),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_session_manifest(path: Path, *, cast_path: Path, events_path: Path, session_id: str) -> None:
    manifest = {
        "schema": "terminal-session-v1",
        "schema_generation": "terminal-session-v1",
        "session_id": session_id,
        "cast_path": str(cast_path),
        "events_path": str(events_path),
        "host": "sinnix-prime",
        "user": "sinity",
        "tty": "/dev/pts/7",
        "terminal": "kitty",
        "shell": "/bin/zsh",
        "term_type": "xterm-256color",
        "start_cwd": "/realm/project/demo",
        "final_cwd": "/realm/project/demo",
        "project_root": "/realm/project/demo",
        "final_project_root": "/realm/project/demo",
        "repo_root": "/realm/project/demo",
        "final_repo_root": "/realm/project/demo",
        "repo_branch": "main",
        "final_repo_branch": "main",
        "repo_commit": "abc123",
        "final_repo_commit": "abc123",
        "repo_dirty": False,
        "final_repo_dirty": False,
        "started_at_ms": 1745806433000,
        "finished_at_ms": 1745806445500,
        "duration_ms": 12500,
        "active_ms": 12500,
        "idle_ms": 0,
        "command_count": 1,
        "event_count": 3,
        "command": "just test",
        "exit_code": 0,
        "exit_reason": "exit",
        "recorder_exit_code": 0,
        "cleanup_escalated": False,
    }
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def _write_events(path: Path, *, session_id: str) -> None:
    rows = [
        {
            "type": "session_start",
            "session_id": session_id,
            "ts_ms": 1745806433000,
            "time": "2025-04-28T04:13:53+02:00",
            "cwd": "/realm/project/demo",
            "project_root": "/realm/project/demo",
            "repo_root": "/realm/project/demo",
            "repo_branch": "main",
            "repo_commit": "abc123",
            "repo_dirty": False,
            "tty": "/dev/pts/7",
            "terminal": "kitty",
        },
        {
            "type": "command_start",
            "session_id": session_id,
            "ts_ms": 1745806440000,
            "time": "2025-04-28T04:14:00+02:00",
            "cwd": "/realm/project/demo",
            "project_root": "/realm/project/demo",
            "repo_root": "/realm/project/demo",
            "repo_branch": "main",
            "repo_commit": "abc123",
            "repo_dirty": False,
            "command": "just test",
        },
        {
            "type": "session_end",
            "session_id": session_id,
            "ts_ms": 1745806445500,
            "time": "2025-04-28T04:14:05.500000+02:00",
            "cwd": "/realm/project/demo",
            "project_root": "/realm/project/demo",
            "repo_root": "/realm/project/demo",
            "repo_branch": "main",
            "repo_commit": "abc123",
            "repo_dirty": False,
            "exit_code": 0,
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_terminal_metadata_emits_canonical_terminal_capture_contract(tmp_path: Path) -> None:
    root = tmp_path / "asciinema"
    session_id = "2025-04-28_04-13-53"
    session_dir = root / "2025" / "04" / "28" / session_id
    session_dir.mkdir(parents=True)

    cast_path = session_dir / "session.cast"
    session_path = session_dir / "session.json"
    events_path = session_dir / "events.jsonl"
    sessions_output = tmp_path / "terminal_sessions.jsonl"
    events_output = tmp_path / "terminal_events.jsonl"
    audit_output = tmp_path / "terminal_audit.json"
    audit_detail_output = tmp_path / "terminal_audit.jsonl"
    quarantine_output = tmp_path / "terminal_quarantine.jsonl"

    _write_cast(cast_path)
    _write_events(events_path, session_id=session_id)
    _write_session_manifest(session_path, cast_path=cast_path, events_path=events_path, session_id=session_id)

    terminal_metadata(
        root=root,
        sessions_output=sessions_output,
        events_output=events_output,
        audit_output=audit_output,
        audit_detail_output=audit_detail_output,
        quarantine_output=quarantine_output,
    )

    sessions = [json.loads(line) for line in sessions_output.read_text(encoding="utf-8").splitlines()]
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == session_id
    assert sessions[0]["path"] == str(cast_path)
    assert sessions[0]["manifest_path"] == str(session_path)
    assert sessions[0]["events_path"] == str(events_path)
    assert sessions[0]["schema_generation"] == "terminal-session-v1"
    assert sessions[0]["command"] == "just test"
    assert sessions[0]["command_count"] == 1
    assert sessions[0]["event_count"] == 3
    assert sessions[0]["quality_status"] == "ok"

    events = [json.loads(line) for line in events_output.read_text(encoding="utf-8").splitlines()]
    assert [event["type"] for event in events] == ["session_start", "command_start", "session_end"]
    assert events[0]["source"] == "events_jsonl"
    assert events[1]["payload"]["command"] == "just test"
    assert events[2]["exit_code"] == 0

    audit = json.loads(audit_output.read_text(encoding="utf-8"))
    assert audit["cast_count"] == 1
    assert audit["manifest_count"] == 1
    assert audit["events_count"] == 1
    assert audit.get("legacy_meta_count", 0) == 0
    assert audit["counts_by_generation"] == {"terminal-session-v1": 1}
    assert audit["counts_by_status"] == {"ok": 1}

    audit_rows = [json.loads(line) for line in audit_detail_output.read_text(encoding="utf-8").splitlines()]
    assert len(audit_rows) == 1
    assert audit_rows[0]["path"] == str(cast_path)
    assert audit_rows[0]["status"] == "ok"

    assert quarantine_output.read_text(encoding="utf-8") == ""
