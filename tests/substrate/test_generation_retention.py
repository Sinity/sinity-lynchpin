"""Focused tests for bounded candidate and publication-generation retention."""
from __future__ import annotations

import json
from pathlib import Path
import shutil

import duckdb
import pytest

import lynchpin.substrate.connection as connection
from lynchpin.substrate.connection import (
    CandidateGenerationRejected,
    apply_schema,
    apply_substrate_retention,
    candidate_generation,
    substrate_read_snapshot_path,
    substrate_retention_census,
)
from lynchpin.substrate.status_manifest import (
    substrate_status_manifest_path,
    write_substrate_status_manifest,
)


@pytest.fixture
def isolated_substrate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    target = tmp_path / "substrate.duckdb"
    monkeypatch.setattr(connection, "substrate_path", lambda: target)
    monkeypatch.setattr(connection, "_filesystem_type", lambda _path: connection._BTRFS_SUPER_MAGIC)
    monkeypatch.setattr(connection, "_file_flags", lambda _path: 0)
    return target


def _verified(path: Path, refresh_id: str) -> None:
    with duckdb.connect(str(path)) as conn:
        apply_schema(conn)
        conn.execute(
            "INSERT INTO substrate_promotion_run "
            "(refresh_id, status, mode, counts, started_at, finished_at) "
            "VALUES (?, 'ok', 'test', '{}', now(), now())",
            [refresh_id],
        )
        conn.execute(
            "INSERT INTO substrate_source_status "
            "(refresh_id, source, kind, status, row_count, recorded_at) "
            "VALUES (?, 'fixture', 'stage', 'ok', 1, now())",
            [refresh_id],
        )


def _previous(canonical: Path, token: str, refresh_id: str) -> None:
    db = canonical.with_name(f"{canonical.name}.previous-{token}")
    snapshot = canonical.with_suffix(".read-snapshot.duckdb").with_name(
        f"substrate.read-snapshot.duckdb.previous-{token}"
    )
    manifest = canonical.with_suffix(".manifest.json").with_name(
        f"substrate.manifest.json.previous-{token}"
    )
    shutil.copy2(canonical, db)
    shutil.copy2(substrate_read_snapshot_path(), snapshot)
    assert write_substrate_status_manifest(canonical, output_path=manifest) is not None
    with duckdb.connect(str(db)) as conn:
        conn.execute("UPDATE substrate_promotion_run SET refresh_id = ?", [refresh_id])
        conn.execute("UPDATE substrate_source_status SET refresh_id = ?", [refresh_id])


def test_retention_keeps_one_verified_previous_and_never_serving_names(
    isolated_substrate: Path,
) -> None:
    _verified(isolated_substrate, "serving")
    from lynchpin.substrate.connection import update_read_snapshot

    update_read_snapshot()
    assert write_substrate_status_manifest(isolated_substrate) is not None
    _previous(isolated_substrate, "20260101T000000+0000-" + "a" * 32, "old")
    _previous(isolated_substrate, "20260102T000000+0000-" + "b" * 32, "new")

    plan = apply_substrate_retention(dry_run=True)
    assert len(plan["keep_previous"]) == 1
    assert len(plan["delete"]) == 3
    serving = {
        isolated_substrate,
        substrate_read_snapshot_path(),
        substrate_status_manifest_path(isolated_substrate),
    }
    assert not serving.intersection({Path(p) for p in plan["delete"]})
    applied = apply_substrate_retention(dry_run=False)
    assert len(applied["deleted"]) == 3
    assert len(substrate_retention_census()["verified_previous"]) == 1
    assert all(path.exists() for path in serving)


def test_failed_candidate_leaves_only_small_receipt_unless_retained(
    isolated_substrate: Path,
) -> None:
    _verified(isolated_substrate, "serving")
    from lynchpin.substrate.connection import update_read_snapshot

    update_read_snapshot()
    with pytest.raises(RuntimeError, match="boom"):
        with candidate_generation(receipt_refresh_id="logical"):
            raise RuntimeError("boom")
    receipts = list(isolated_substrate.parent.glob("candidate-receipt-*.json"))
    assert len(receipts) == 1
    payload = json.loads(receipts[0].read_text())
    assert payload["logical_refresh_id"] == "logical"
    assert payload["candidate_attempt_id"] != payload["logical_refresh_id"]
    assert not list(isolated_substrate.parent.glob("substrate.candidate-*.duckdb"))

    with pytest.raises(RuntimeError, match="keep"):
        with candidate_generation(retain_failed=True):
            raise RuntimeError("keep")
    assert list(isolated_substrate.parent.glob("substrate.candidate-*.duckdb"))
    assert list(isolated_substrate.parent.glob("candidate-receipt-*.json"))


def test_startup_cleanup_is_bounded_and_rejects_path_escape(
    isolated_substrate: Path,
) -> None:
    _verified(isolated_substrate, "serving")
    candidate = isolated_substrate.with_name(
        "substrate.candidate-" + "c" * 32 + ".duckdb"
    )
    candidate.write_bytes(b"interrupted")
    candidate.with_name(f"{candidate.name}.wal").write_bytes(b"wal")
    candidate.with_suffix(".read-snapshot.duckdb").write_bytes(b"snapshot")
    candidate.with_suffix(".manifest.json").write_text("{}")
    connection._archive_interrupted_candidates(isolated_substrate)
    assert not candidate.exists()
    assert list(isolated_substrate.parent.glob("candidate-receipt-*.json"))
    assert not list(isolated_substrate.parent.glob("substrate.candidate-*.wal"))

    outside = isolated_substrate.parent.parent / (
        "substrate.candidate-" + "d" * 32 + ".duckdb"
    )
    with pytest.raises(CandidateGenerationRejected, match="outside"):
        connection._remove_candidate(outside, isolated_substrate)
