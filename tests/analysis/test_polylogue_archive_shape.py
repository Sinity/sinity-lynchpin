from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

from typer.testing import CliRunner

from lynchpin.analysis.cli import build_app
from lynchpin.analysis.ecosystem.polylogue_archive_shape import (
    build_polylogue_archive_shape,
    identify_raw_file,
    measure_raw_file,
)


def _write_jsonl(path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_identifies_codex_uuid_from_filename(tmp_path):
    path = tmp_path / "2026" / "01" / "01" / "019e9951-f38f-7a60-a2f2-786511b35f39.jsonl"
    _write_jsonl(path, [])

    row = identify_raw_file(path, "codex")

    assert row.local_id == "codex:019e9951-f38f-7a60-a2f2-786511b35f39"
    assert row.id_source == "filename_uuid"


def test_identifies_claude_agent_file_from_embedded_session_id(tmp_path):
    path = tmp_path / "project" / "agent-5fc0952c.jsonl"
    _write_jsonl(
        path,
        [
            {
                "sessionId": "5c4a8307-9b90-4ccd-980d-cdf8b8fdb76d",
                "message": {"role": "assistant", "content": "hello"},
            }
        ],
    )

    row = identify_raw_file(path, "claude-code")

    assert row.local_id == "claude-code:5c4a8307-9b90-4ccd-980d-cdf8b8fdb76d:agent-5fc0952c"
    assert row.id_source == "agent_session_id"


def test_codex_renderer_counts_tool_calls_only_in_full_markdown(tmp_path):
    path = tmp_path / "019e9951-f38f-7a60-a2f2-786511b35f39.jsonl"
    _write_jsonl(
        path,
        [
            {"type": "message", "payload": {"role": "user", "content": [{"type": "input_text", "text": "do it"}]}},
            {"type": "response_item", "payload": {"type": "function_call", "name": "shell", "arguments": "{}"}},
            {"type": "response_item", "payload": {"item": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "done"}]}}},
        ],
    )
    raw = identify_raw_file(path, "codex")

    measurement = measure_raw_file(raw)

    assert measurement.rendered_messages == 3
    assert measurement.prose_messages == 2
    assert measurement.user_messages == 1
    assert measurement.full_markdown_bytes > measurement.prose_markdown_bytes > measurement.user_markdown_bytes


def test_claude_renderer_excludes_meta_and_tool_blocks_from_prose(tmp_path):
    path = tmp_path / "5c4a8307-9b90-4ccd-980d-cdf8b8fdb76d.jsonl"
    _write_jsonl(
        path,
        [
            {"message": {"role": "user", "content": "# AGENTS.md instructions\nsecret setup"}},
            {"isMeta": True, "message": {"role": "user", "content": "meta"}},
            {"message": {"role": "assistant", "content": [{"type": "tool_use", "name": "bash", "input": {}}]}},
            {"message": {"role": "assistant", "content": "plain answer"}},
        ],
    )
    raw = identify_raw_file(path, "claude-code")

    measurement = measure_raw_file(raw)

    assert measurement.rendered_messages == 4
    assert measurement.prose_messages == 1
    assert measurement.user_messages == 0
    assert measurement.prose_markdown_bytes < measurement.full_markdown_bytes


def test_build_archive_shape_reconciles_raw_ids_against_archive_db(tmp_path):
    claude_root = tmp_path / "claude"
    codex_root = tmp_path / "codex"
    db_path = tmp_path / "polylogue.db"
    _write_jsonl(
        claude_root / "project" / "5c4a8307-9b90-4ccd-980d-cdf8b8fdb76d.jsonl",
        [{"message": {"role": "assistant", "content": "hello"}}],
    )
    _write_jsonl(
        codex_root / "2026" / "019e9951-f38f-7a60-a2f2-786511b35f39.jsonl",
        [{"type": "message", "payload": {"role": "user", "content": "hello"}}],
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "create table conversations (conversation_id text primary key, source_name text not null)"
        )
        conn.executemany(
            "insert into conversations values (?, ?)",
            [
                ("claude-code:5c4a8307-9b90-4ccd-980d-cdf8b8fdb76d", "claude-code"),
                ("codex:missing", "codex"),
            ],
        )

    cfg = SimpleNamespace(codex_sessions_root=codex_root, polylogue_db=db_path)
    payload = build_polylogue_archive_shape(
        cfg=cfg,
        claude_root=claude_root,
        codex_root=codex_root,
        polylogue_db=db_path,
        sample_per_provider=None,
    )

    assert payload["id_reconciliation"]["claude-code"]["matched_ids"] == 1
    assert payload["id_reconciliation"]["codex"]["local_not_polylogue"] == 1
    assert payload["id_reconciliation"]["codex"]["polylogue_not_local"] == 1
    assert payload["measurement_scope"]["measured_files"] == 2


def test_polylogue_archive_shape_command_is_registered():
    result = CliRunner().invoke(build_app(), ["--help"])

    assert result.exit_code == 0
    assert "polylogue-archive-shape" in result.output
