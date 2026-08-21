"""Tests for the substrate read-snapshot fallback.

Pin the behavior that read tools stay usable while the canonical
substrate is under an exclusive write lock — materializations can hold the
lock for 30-60+ minutes; MCP needs a path to read regardless.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from lynchpin.substrate.connection import (
    apply_schema,
    candidate_generation,
    connect,
    generation_refresh_id,
    substrate_read_snapshot_path,
    update_read_snapshot,
)


@pytest.fixture
def isolated_substrate(monkeypatch, tmp_path: Path) -> Path:
    """Point substrate_path at an isolated tmp file for this test."""
    target = tmp_path / "substrate.duckdb"
    monkeypatch.setattr(
        "lynchpin.substrate.connection.substrate_path",
        lambda: target,
    )
    return target


def _record_verified_generation(path: Path, refresh_id: str) -> None:
    with duckdb.connect(str(path)) as conn:
        apply_schema(conn)
        conn.execute(
            """
            INSERT INTO substrate_promotion_run
            (refresh_id, status, reason, window_start, window_end, mode, counts, started_at, finished_at)
            VALUES (?, 'ok', NULL, NULL, NULL, 'test', '{}', now(), now())
            """,
            [refresh_id],
        )
        conn.execute(
            """
            INSERT INTO substrate_source_status
            (refresh_id, source, kind, status, reason, row_count, window_start, window_end, recorded_at)
            VALUES (?, 'fixture', 'stage', 'ok', NULL, 1, NULL, NULL, now())
            """,
            [refresh_id],
        )


def test_update_read_snapshot_creates_copy(isolated_substrate: Path) -> None:
    """A canonical with data → snapshot file appears alongside it with
    the same data accessible read-only."""
    canonical = isolated_substrate
    with duckdb.connect(str(canonical)) as conn:
        conn.execute("CREATE TABLE x (v INTEGER)")
        conn.execute("INSERT INTO x VALUES (42)")

    snapshot_path = update_read_snapshot()
    assert snapshot_path is not None
    assert snapshot_path.exists()
    assert snapshot_path == substrate_read_snapshot_path()

    with duckdb.connect(str(snapshot_path), read_only=True) as conn:
        assert conn.execute("SELECT v FROM x").fetchone() == (42,)


def test_update_read_snapshot_skips_missing_canonical(
    isolated_substrate: Path,
) -> None:
    """No canonical → no snapshot, no error."""
    assert update_read_snapshot() is None


def test_connect_falls_back_to_snapshot_on_lock(isolated_substrate: Path) -> None:
    """Simulate write-locked canonical: hold an exclusive connection,
    then assert read_only connect falls back to the snapshot."""
    canonical = isolated_substrate
    # Seed canonical with data
    with duckdb.connect(str(canonical)) as conn:
        conn.execute("CREATE TABLE x (v INTEGER)")
        conn.execute("INSERT INTO x VALUES (7)")
    # Snapshot the data
    update_read_snapshot()
    # Modify canonical to a different value (without snapshotting)
    with duckdb.connect(str(canonical)) as conn:
        conn.execute("UPDATE x SET v = 99")

    # Now hold a write lock on canonical
    writer = duckdb.connect(str(canonical))
    try:
        # MCP-style read should fall back to snapshot (which has v=7)
        with connect(read_only=True) as reader:
            (val,) = reader.execute("SELECT v FROM x").fetchone()
            assert val == 7, "fell back to snapshot (pre-update)"
    finally:
        writer.close()


def test_connect_strict_mode_raises_on_lock(isolated_substrate: Path) -> None:
    """``snapshot_fallback=False`` preserves the historical strict behavior
    for callers that must distinguish canonical availability from snapshot availability."""
    canonical = isolated_substrate
    with duckdb.connect(str(canonical)) as conn:
        conn.execute("CREATE TABLE x (v INTEGER)")
    update_read_snapshot()
    writer = duckdb.connect(str(canonical))
    try:
        # Same-process: ConnectionException. Cross-process: IOException.
        # Either signals "canonical unavailable in our mode".
        with pytest.raises((duckdb.IOException, duckdb.ConnectionException)):
            with connect(read_only=True, snapshot_fallback=False) as reader:
                reader.execute("SELECT 1").fetchone()
    finally:
        writer.close()


def test_connect_raises_when_no_snapshot_and_locked(
    isolated_substrate: Path,
) -> None:
    """No snapshot exists at all → connect should raise the original
    lock error rather than silently returning an absent snapshot path."""
    canonical = isolated_substrate
    with duckdb.connect(str(canonical)) as conn:
        conn.execute("CREATE TABLE x (v INTEGER)")
    writer = duckdb.connect(str(canonical))
    try:
        with pytest.raises((duckdb.IOException, duckdb.ConnectionException)):
            with connect(read_only=True) as reader:
                reader.execute("SELECT 1").fetchone()
    finally:
        writer.close()


def test_connect_read_only_falls_back_to_snapshot_on_internal_open_error(
    isolated_substrate: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DuckDB internal open failures on the canonical should still leave
    read-only callers on the last known-good snapshot."""
    canonical = isolated_substrate
    with duckdb.connect(str(canonical)) as conn:
        conn.execute("CREATE TABLE x (v INTEGER)")
        conn.execute("INSERT INTO x VALUES (11)")
    update_read_snapshot()

    real_connect = duckdb.connect
    calls = 0

    def flaky_connect(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise duckdb.InternalException("metadata pointer failed")
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(duckdb, "connect", flaky_connect)

    with connect(read_only=True) as reader:
        assert reader.execute("SELECT v FROM x").fetchone() == (11,)


def test_connect_write_can_rebuild_corrupt_canonical(
    isolated_substrate: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Opt-in write recovery quarantines an unreadable derived canonical and
    resumes from a clean schema rather than reusing a suspect snapshot."""
    canonical = isolated_substrate
    with duckdb.connect(str(canonical)) as conn:
        conn.execute("CREATE TABLE x (v INTEGER)")
        conn.execute("INSERT INTO x VALUES (17)")
    update_read_snapshot()
    canonical.write_bytes(b"not a duckdb database")

    real_connect = duckdb.connect
    calls = 0

    def flaky_connect(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise duckdb.InternalException("metadata pointer failed")
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(duckdb, "connect", flaky_connect)

    with connect(rebuild_corrupt=True) as writer:
        assert writer.execute(
            "SELECT value FROM substrate_meta WHERE key = 'version'"
        ).fetchone() is not None

    quarantined = list(canonical.parent.glob("substrate.duckdb.corrupt-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == b"not a duckdb database"
    with duckdb.connect(str(canonical), read_only=True) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables"
        ).fetchone()[0] > 1


def test_rebuild_corrupt_substrate_installs_clean_schema(
    isolated_substrate: Path,
) -> None:
    from lynchpin.substrate.connection import rebuild_corrupt_substrate

    canonical = isolated_substrate
    with duckdb.connect(str(canonical)) as conn:
        conn.execute("CREATE TABLE x (v INTEGER)")
        conn.execute("INSERT INTO x VALUES (17)")
    canonical.write_bytes(b"broken canonical")

    quarantine = rebuild_corrupt_substrate(canonical)

    assert quarantine.read_bytes() == b"broken canonical"
    with duckdb.connect(str(canonical), read_only=True) as conn:
        assert conn.execute(
            "SELECT value FROM substrate_meta WHERE key = 'version'"
        ).fetchone() is not None
        assert conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables"
        ).fetchone()[0] > 1


def test_rebuild_keeps_canonical_when_clean_schema_creation_fails(
    isolated_substrate: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lynchpin.substrate.connection import rebuild_corrupt_substrate

    canonical = isolated_substrate
    canonical.write_bytes(b"canonical retained")
    monkeypatch.setattr(
        "lynchpin.substrate.connection.apply_schema",
        lambda _conn: (_ for _ in ()).throw(RuntimeError("schema failed")),
    )

    with pytest.raises(RuntimeError, match="failed before canonical replacement"):
        rebuild_corrupt_substrate(canonical)

    assert canonical.read_bytes() == b"canonical retained"
    assert not list(canonical.parent.glob("substrate.duckdb.corrupt-*"))
    assert not canonical.with_suffix(".rebuild.tmp").exists()


def test_candidate_failure_retains_verified_serving_generation(
    isolated_substrate: Path,
) -> None:
    _record_verified_generation(isolated_substrate, "prior")
    update_read_snapshot()

    with pytest.raises(RuntimeError, match="injected failure"):
        with candidate_generation():
            raise RuntimeError("injected failure")

    assert generation_refresh_id(isolated_substrate) == "prior"
    assert generation_refresh_id(substrate_read_snapshot_path()) == "prior"
    assert list(isolated_substrate.parent.glob("substrate.candidate-*.failed-*"))


def test_candidate_generation_archives_interrupted_candidate_sidecars(
    isolated_substrate: Path,
) -> None:
    _record_verified_generation(isolated_substrate, "prior")
    update_read_snapshot()
    interrupted = isolated_substrate.with_name("substrate.candidate-interrupted.duckdb")
    interrupted.write_bytes(b"interrupted candidate")
    interrupted.with_name(f"{interrupted.name}.wal").write_bytes(b"candidate wal")
    interrupted.with_suffix(".read-snapshot.duckdb").write_bytes(b"candidate snapshot")

    with pytest.raises(RuntimeError, match="injected failure"):
        with candidate_generation():
            raise RuntimeError("injected failure")

    assert generation_refresh_id(isolated_substrate) == "prior"
    assert not interrupted.exists()
    assert not interrupted.with_name(f"{interrupted.name}.wal").exists()
    assert not interrupted.with_suffix(".read-snapshot.duckdb").exists()
    assert list(interrupted.parent.glob(f"{interrupted.name}.interrupted-*"))
    assert list(interrupted.parent.glob(f"{interrupted.name}.wal.interrupted-*"))
    assert list(
        interrupted.parent.glob(
            f"{interrupted.with_suffix('.read-snapshot.duckdb').name}.interrupted-*"
        )
    )


def test_candidate_publication_replaces_only_verified_generation(
    isolated_substrate: Path,
) -> None:
    _record_verified_generation(isolated_substrate, "prior")
    update_read_snapshot()

    with candidate_generation() as generation:
        _record_verified_generation(generation.candidate, "current")

    assert generation_refresh_id(isolated_substrate) == "current"
    assert generation_refresh_id(substrate_read_snapshot_path()) == "current"
    archived = list(isolated_substrate.parent.glob("substrate.duckdb.previous-*"))
    assert len(archived) == 1
    assert generation_refresh_id(archived[0]) == "prior"


def test_connect_prefers_verified_snapshot_to_unready_canonical(
    isolated_substrate: Path,
) -> None:
    _record_verified_generation(isolated_substrate, "prior")
    update_read_snapshot()
    with duckdb.connect(str(isolated_substrate)) as conn:
        conn.execute("DELETE FROM substrate_source_status")
        conn.execute("DELETE FROM substrate_promotion_run")

    with connect(isolated_substrate, read_only=True) as reader:
        assert reader.execute(
            "SELECT refresh_id FROM substrate_promotion_run"
        ).fetchone() == ("prior",)
