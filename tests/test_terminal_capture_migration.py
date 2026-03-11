from __future__ import annotations

import json
from pathlib import Path

from lynchpin.ingest.instrumentation import migrate_terminal_corpus
from lynchpin.sources.captures.instrumentation import _session_time_from_id


def _write_cast(path: Path) -> None:
    header = {
        "version": 2,
        "width": 120,
        "height": 40,
        "timestamp": 1745806433,
        "env": {
            "SHELL": "/bin/zsh",
            "TERM": "xterm-256color",
        },
    }
    lines = [
        json.dumps(header),
        json.dumps([0.0, "o", "$ "]),
        json.dumps([12.5, "o", "done\r\n"]),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_legacy_meta(path: Path) -> None:
    rows = [
        {
            "time": "2025-04-28T04:13:53+02:00",
            "type": "session_start",
            "pwd": "/realm/project/demo",
            "project_root": "/realm/project/demo",
            "repo_root": "/realm/project/demo",
            "repo_branch": "main",
            "repo_commit": "abc123",
            "repo_dirty": "false",
        },
        {
            "time": "2025-04-28T04:14:00+02:00",
            "type": "command_start",
            "pwd": "/realm/project/demo",
            "project_root": "/realm/project/demo",
            "repo_root": "/realm/project/demo",
            "repo_branch": "main",
            "repo_commit": "abc123",
            "repo_dirty": "false",
            "command": "just test",
        },
        {
            "time": "2025-04-28T04:14:06+02:00",
            "type": "session_end",
            "pwd": "/realm/project/demo",
            "project_root": "/realm/project/demo",
            "repo_root": "/realm/project/demo",
            "repo_branch": "main",
            "repo_commit": "abc123",
            "repo_dirty": "false",
            "exit_code": "0",
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_session_time_from_id_supports_flat_legacy_timestamp_ids() -> None:
    parsed = _session_time_from_id("2025-04-28_04-13-53")

    assert parsed is not None
    assert parsed.startswith("2025-04-28T04:13:53")


def test_migrate_terminal_corpus_normalizes_flat_legacy_session(tmp_path: Path) -> None:
    root = tmp_path / "asciinema"
    root.mkdir()
    source_cast = root / "2025-04-28_04-13-53.cast"
    source_meta = root / "2025-04-28_04-13-53.cast.meta"
    report_output = tmp_path / "report.json"
    manifest_output = tmp_path / "manifest.jsonl"

    _write_cast(source_cast)
    _write_legacy_meta(source_meta)

    migrate_terminal_corpus(
        root=root,
        report_output=report_output,
        manifest_output=manifest_output,
        apply=True,
    )

    target_dir = root / "2025" / "04" / "28" / "2025-04-28_04-13-53"
    target_cast = target_dir / "session.cast"
    target_session = target_dir / "session.json"
    target_events = target_dir / "events.jsonl"

    assert not source_cast.exists()
    assert not source_meta.exists()
    assert target_cast.exists()
    assert target_session.exists()
    assert target_events.exists()

    report = json.loads(report_output.read_text(encoding="utf-8"))
    assert report == {
        "root": str(root),
        "sessions_to_migrate": 1,
        "synthetic_event_sessions": 0,
        "legacy_meta_sidecars": 1,
        "apply": True,
    }

    manifest_rows = [json.loads(line) for line in manifest_output.read_text(encoding="utf-8").splitlines()]
    assert len(manifest_rows) == 1
    assert manifest_rows[0]["target_cast_path"] == str(target_cast)
    assert manifest_rows[0]["synthetic_events"] is False

    session = json.loads(target_session.read_text(encoding="utf-8"))
    assert session["schema_generation"] == "terminal-session-v1"
    assert session["session_id"] == "2025-04-28_04-13-53"
    assert session["cast_path"] == str(target_cast)
    assert session["events_path"] == str(target_events)
    assert session["started_at_ms"] == 1745806433000
    assert session["command_count"] == 1
    assert session["event_count"] == 3

    events = [json.loads(line) for line in target_events.read_text(encoding="utf-8").splitlines()]
    assert [event["type"] for event in events] == ["session_start", "command_start", "session_end"]
    assert events[0]["cwd"] == "/realm/project/demo"
    assert events[1]["command"] == "just test"
    assert events[2]["exit_code"] == 0
