from __future__ import annotations

import json
from datetime import datetime, timezone

from lynchpin.sources.borg_drill import BorgDrillRun, drill_runs, readiness
from lynchpin.substrate.connection import apply_schema, connect
from lynchpin.substrate.personal import promote_borg_drill_runs


def test_readiness_missing(tmp_path):
    assert readiness(path=tmp_path / "absent.jsonl").status == "missing"


def test_readiness_empty(tmp_path):
    p = tmp_path / "empty.jsonl"
    p.touch()
    assert readiness(path=p).status == "empty"


def test_drill_runs_parses_jsonl(tmp_path):
    p = tmp_path / "d.jsonl"
    with p.open("w", encoding="utf-8") as fh:
        for row in [
            {"repo": "file:///r1", "archive": "a-2026-05-22",
             "started_at": "2026-05-22T04:20:33+00:00",
             "ended_at": "2026-05-22T04:32:11+00:00",
             "duration_s": 698, "exit_code": 0, "status": "ok",
             "stderr_tail": "", "within_days": 30},
            {"repo": "file:///r2", "archive": "b-2026-05-22",
             "started_at": "2026-05-22T04:35:00+00:00",
             "ended_at": "2026-05-22T04:55:12+00:00",
             "duration_s": 1212, "exit_code": 0, "status": "ok",
             "stderr_tail": "", "within_days": 30},
        ]:
            fh.write(json.dumps(row) + "\n")
    runs = list(drill_runs(path=p))
    assert len(runs) == 2
    assert all(isinstance(r, BorgDrillRun) for r in runs)
    assert runs[0].repo == "file:///r1"
    assert runs[0].duration_s == 698
    assert runs[0].status == "ok"


def test_drill_runs_skips_unparseable(tmp_path):
    p = tmp_path / "d.jsonl"
    with p.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"repo": "f1", "archive": "a",
                             "started_at": "2026-05-22T04:00:00+00:00",
                             "ended_at": "2026-05-22T04:01:00+00:00",
                             "duration_s": 60, "exit_code": 0,
                             "status": "ok", "stderr_tail": "",
                             "within_days": 30}) + "\n")
        fh.write("{ broken\n")
        fh.write(json.dumps({"repo": "f2", "archive": "b",
                             "started_at": "2026-05-22T05:00:00+00:00",
                             "ended_at": "2026-05-22T05:01:00+00:00",
                             "duration_s": 60, "exit_code": 1,
                             "status": "failed", "stderr_tail": "x",
                             "within_days": 30}) + "\n")
    runs = list(drill_runs(path=p))
    assert [r.repo for r in runs] == ["f1", "f2"]
    assert [r.status for r in runs] == ["ok", "failed"]


def test_promote_borg_drill_runs(tmp_path):
    db = tmp_path / "sub.duckdb"
    runs = [
        BorgDrillRun(
            repo="file:///r1", archive="a",
            started_at=datetime(2026, 5, 22, 4, 20, 33, tzinfo=timezone.utc),
            ended_at=datetime(2026, 5, 22, 4, 32, 11, tzinfo=timezone.utc),
            duration_s=698, exit_code=0, status="ok",
            stderr_tail="", within_days=30,
        ),
        BorgDrillRun(
            repo="file:///r2", archive="b",
            started_at=datetime(2026, 5, 22, 4, 35, 0, tzinfo=timezone.utc),
            ended_at=datetime(2026, 5, 22, 4, 55, 12, tzinfo=timezone.utc),
            duration_s=1212, exit_code=1, status="failed",
            stderr_tail="chunk verification error at offset 12345",
            within_days=30,
        ),
    ]
    with connect(db) as conn:
        apply_schema(conn)
        assert promote_borg_drill_runs(conn, refresh_id="r1", runs=runs) == 2
        # Idempotent
        assert promote_borg_drill_runs(conn, refresh_id="r1", runs=runs[:1]) == 1
        rows = conn.execute(
            "SELECT repo, status FROM borg_drill_run ORDER BY started_at"
        ).fetchall()
        assert rows == [("file:///r1", "ok")]
