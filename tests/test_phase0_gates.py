from __future__ import annotations

import importlib
import sqlite3
import sys
from pathlib import Path

from typer.testing import CliRunner

from lynchpin.ingest.fbmessenger_export import _LiteExportDb
from lynchpin.system import validate


def test_validate_cli_exits_nonzero_on_missing_dependency(tmp_path: Path) -> None:
    output = tmp_path / "validation.jsonl"
    runner = CliRunner()

    original_run_check = validate._run_check

    def fake_run_check(name, fn):
        return validate.CheckResult(
            name=name,
            status="missing",
            count=None,
            detail="module missing: imaginary_dep",
            duration_ms=1.0,
            error="No module named imaginary_dep",
        )

    validate._run_check = fake_run_check
    try:
        result = runner.invoke(
            validate.app,
            ["lynchpin", "--quick", "--output", str(output), "--no-progress"],
        )
    finally:
        validate._run_check = original_run_check

    assert result.exit_code == 1
    assert output.exists()
    payload = output.read_text(encoding="utf-8")
    assert '"status": "missing"' in payload


def test_importing_lynchpin_does_not_mutate_vendor_paths() -> None:
    vendor_roots = {
        str(Path("/realm/project/sinity-lynchpin/external/hpi")),
        str(Path("/realm/project/sinity-lynchpin/external/hpi/src")),
        str(Path("/realm/project/sinity-lynchpin/external/hpi-madelinecameron")),
        str(Path("/realm/project/sinity-lynchpin/external/hpi-sinity")),
        str(Path("/realm/project/sinity-lynchpin/external/hpi-purarue")),
    }
    sys.modules.pop("lynchpin", None)
    before = list(sys.path)
    importlib.import_module("lynchpin")
    after = list(sys.path)
    added = set(after) - set(before)
    assert not (added & vendor_roots)


def test_lite_fbmessenger_db_backfills_hpi_compat_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "fbmessengerexport.sqlite"
    con = sqlite3.connect(db_path)
    con.execute(
        "CREATE TABLE threads (uid TEXT PRIMARY KEY, message_count INTEGER, last_message_timestamp INTEGER, data JSON)"
    )
    con.execute(
        "CREATE TABLE messages (uid TEXT PRIMARY KEY, thread_id TEXT, timestamp INTEGER, data JSON)"
    )
    con.execute(
        "INSERT INTO threads VALUES (?, ?, ?, ?)",
        (
            "thread-1",
            1,
            123,
            '{"uid":"thread-1","name":"Example Thread","message_count":1,"last_message_timestamp":123}',
        ),
    )
    con.execute(
        "INSERT INTO messages VALUES (?, ?, ?, ?)",
        (
            "message-1",
            "thread-1",
            123,
            '{"uid":"message-1","thread_id":"thread-1","author":"author-1","text":"hello","timestamp":123}',
        ),
    )
    con.commit()
    con.close()

    db = _LiteExportDb(db_path)
    thread_columns = {
        row[1] for row in db.db.execute("PRAGMA table_info(threads)")
    }
    message_columns = {
        row[1] for row in db.db.execute("PRAGMA table_info(messages)")
    }
    thread_row = db.db.execute(
        "SELECT name FROM threads WHERE uid='thread-1'"
    ).fetchone()
    message_row = db.db.execute(
        "SELECT author, text FROM messages WHERE uid='message-1'"
    ).fetchone()

    assert "name" in thread_columns
    assert {"author", "text"} <= message_columns
    assert thread_row == ("Example Thread",)
    assert message_row == ("author-1", "hello")
