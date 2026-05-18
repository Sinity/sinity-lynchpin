from __future__ import annotations

import json
from datetime import datetime, timezone

from lynchpin.sources.sinnix_generations import (
    SinnixGenerationRecord,
    generation_records,
    readiness,
)
from lynchpin.substrate.connection import apply_schema, connect
from lynchpin.substrate.personal import promote_sinnix_generations


def _write_jsonl(path, records):
    with path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


def test_readiness_missing_when_file_absent(tmp_path):
    r = readiness(path=tmp_path / "absent.jsonl")
    assert r.status == "missing"
    assert r.row_count == 0


def test_readiness_empty_when_file_has_no_lines(tmp_path):
    p = tmp_path / "empty.jsonl"
    p.touch()
    r = readiness(path=p)
    assert r.status == "empty"
    assert r.row_count == 0


def test_readiness_ok_counts_rows(tmp_path):
    p = tmp_path / "g.jsonl"
    _write_jsonl(p, [
        {"generation": "45", "activated_at": "2026-05-18T15:32:39+00:00",
         "store_path": "/nix/store/abc", "sinnix_revision": "deadbeef",
         "nixos_label": "26.05", "host": "sinnix-prime"},
        {"generation": "46", "activated_at": "2026-05-18T16:00:00+00:00",
         "store_path": "/nix/store/def", "sinnix_revision": "cafebabe",
         "nixos_label": "26.05", "host": "sinnix-prime"},
    ])
    r = readiness(path=p)
    assert r.status == "ok"
    assert r.row_count == 2


def test_generation_records_yields_typed_records(tmp_path):
    p = tmp_path / "g.jsonl"
    _write_jsonl(p, [
        {"generation": "45", "activated_at": "2026-05-18T15:32:39+00:00",
         "store_path": "/nix/store/abc", "sinnix_revision": "deadbeef",
         "nixos_label": "26.05", "host": "sinnix-prime"},
    ])
    rows = list(generation_records(path=p))
    assert len(rows) == 1
    assert isinstance(rows[0], SinnixGenerationRecord)
    assert rows[0].generation == "45"
    assert rows[0].sinnix_revision == "deadbeef"
    assert rows[0].activated_at == datetime(2026, 5, 18, 15, 32, 39, tzinfo=timezone.utc)


def test_generation_records_skips_unparseable_lines(tmp_path):
    p = tmp_path / "g.jsonl"
    with p.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"generation": "1", "activated_at": "2026-05-18T00:00:00+00:00",
                             "store_path": "/n", "sinnix_revision": "a", "nixos_label": "x",
                             "host": "h"}) + "\n")
        fh.write("{ partial-write-interrupted-by-reboot\n")
        fh.write(json.dumps({"generation": "2", "activated_at": "2026-05-19T00:00:00+00:00",
                             "store_path": "/n2", "sinnix_revision": "b", "nixos_label": "x",
                             "host": "h"}) + "\n")
    rows = list(generation_records(path=p))
    assert [r.generation for r in rows] == ["1", "2"]


def test_promote_sinnix_generations_into_substrate(tmp_path):
    db = tmp_path / "sub.duckdb"
    records = [
        SinnixGenerationRecord(
            generation="45",
            activated_at=datetime(2026, 5, 18, 15, 32, 39, tzinfo=timezone.utc),
            store_path="/nix/store/abc",
            sinnix_revision="deadbeef",
            nixos_label="26.05",
            host="sinnix-prime",
        ),
        SinnixGenerationRecord(
            generation="46",
            activated_at=datetime(2026, 5, 18, 16, 0, 0, tzinfo=timezone.utc),
            store_path="/nix/store/def",
            sinnix_revision="cafebabe",
            nixos_label="26.05",
            host="sinnix-prime",
        ),
    ]
    with connect(db) as conn:
        apply_schema(conn)
        count = promote_sinnix_generations(conn, refresh_id="r1", records=records)
        assert count == 2

        # Idempotent: re-promoting same refresh_id deletes old rows first
        count2 = promote_sinnix_generations(conn, refresh_id="r1", records=records[:1])
        assert count2 == 1

        rows = conn.execute(
            "SELECT generation, sinnix_revision FROM sinnix_generation ORDER BY activated_at"
        ).fetchall()
        assert rows == [("45", "deadbeef")]
