"""Tests for the substrate read-snapshot fallback.

Pin the behavior that read tools stay usable while the canonical
substrate is under an exclusive write lock — materializations can hold the
lock for 30-60+ minutes; MCP needs a path to read regardless.
"""
from __future__ import annotations

from pathlib import Path
import signal

import duckdb
import pytest

from lynchpin.substrate.connection import (
    CandidateGenerationInterrupted,
    CandidateGenerationRejected,
    apply_schema,
    candidate_generation,
    connect,
    generation_refresh_id,
    substrate_read_snapshot_path,
    update_read_snapshot,
)
from lynchpin.substrate.status_manifest import (
    load_current_substrate_status_manifest,
    substrate_status_manifest_path,
    write_substrate_status_manifest,
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


def _base_table_counts(path: Path) -> dict[str, int]:
    with duckdb.connect(str(path), read_only=True) as conn:
        names = [
            row[0]
            for row in conn.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'main' AND table_type = 'BASE TABLE'
                ORDER BY table_name
                """
            ).fetchall()
        ]
        return {
            name: conn.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
            for name in names
        }


def test_logical_index_rebuild_seed_preserves_verified_rows(
    isolated_substrate: Path,
) -> None:
    from lynchpin.substrate.connection import SUBSTRATE_VERSION

    _record_verified_generation(isolated_substrate, "prior")
    with duckdb.connect(str(isolated_substrate)) as conn:
        conn.execute(
            "INSERT INTO activity_content_day (date, refresh_id) VALUES ('2026-08-21', 'prior')"
        )
    update_read_snapshot()
    expected_counts = _base_table_counts(isolated_substrate)

    with candidate_generation() as generation:
        assert generation.seed_source == isolated_substrate
        candidate_counts = _base_table_counts(generation.candidate)
        assert {
            table: count
            for table, count in candidate_counts.items()
            if table != "substrate_run_step"
        } == {
            table: count
            for table, count in expected_counts.items()
            if table != "substrate_run_step"
        }
        assert candidate_counts["substrate_run_step"] == (
            expected_counts["substrate_run_step"] + 1
        )
        with duckdb.connect(str(generation.candidate), read_only=True) as conn:
            assert conn.execute(
                "SELECT value FROM substrate_meta WHERE key = 'version'"
            ).fetchone() == (str(SUBSTRATE_VERSION),)
            views = {
                row[0]
                for row in conn.execute(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'main' AND table_type = 'VIEW'
                    """
                ).fetchall()
            }
            assert views == {
                "issue_closure_chain_walk",
                "project_day_correlation",
                "work_event_file_overlap",
                "work_event_symbol_overlap",
            }
            assert conn.execute("SELECT count(*) FROM duckdb_sequences()").fetchone() == (0,)
            assert conn.execute(
                """
                SELECT status, row_count
                FROM substrate_run_step
                WHERE refresh_id = ? AND step = 'candidate_index_rebuild'
                """,
                [generation.refresh_id],
            ).fetchone() == (
                "ok",
                sum(expected_counts.values()) - expected_counts["substrate_meta"],
            )
        _record_verified_generation(generation.candidate, "current")

    assert generation_refresh_id(isolated_substrate) == "current"
    with duckdb.connect(str(isolated_substrate), read_only=True) as conn:
        assert conn.execute(
            "SELECT count(*) FROM activity_content_day WHERE refresh_id = 'prior'"
        ).fetchone() == (1,)
    assert list(isolated_substrate.parent.glob("substrate.duckdb.previous-*"))


def test_candidate_generation_recovers_from_archived_verified_source(
    isolated_substrate: Path,
) -> None:
    """An unreadable serving path can be recovered from verified retained rows."""
    _record_verified_generation(isolated_substrate, "prior")
    update_read_snapshot()
    archived = isolated_substrate.with_name("substrate.candidate-recovery.duckdb.retained")
    isolated_substrate.replace(archived)

    with candidate_generation() as generation:
        assert generation.seed_source == archived
        _record_verified_generation(generation.candidate, "current")

    assert generation_refresh_id(isolated_substrate) == "current"
    assert generation_refresh_id(substrate_read_snapshot_path()) == "current"
    assert generation_refresh_id(archived) == "prior"


def test_logical_index_rebuild_schema_mismatch_retains_serving_triple(
    isolated_substrate: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lynchpin.substrate.connection as connection

    _record_verified_generation(isolated_substrate, "prior")
    update_read_snapshot()
    assert write_substrate_status_manifest(isolated_substrate) is not None
    serving_paths = (
        isolated_substrate,
        substrate_read_snapshot_path(),
        substrate_status_manifest_path(isolated_substrate),
    )
    serving_contents = {path: path.read_bytes() for path in serving_paths}
    real_base_table_names = connection._base_table_names

    def incompatible_tables(conn, catalog=None):
        if catalog is None:
            return set()
        return real_base_table_names(conn, catalog)

    monkeypatch.setattr(connection, "_base_table_names", incompatible_tables)

    with pytest.raises(CandidateGenerationRejected, match="matching source"):
        with candidate_generation():
            pass

    assert all(path.read_bytes() == serving_contents[path] for path in serving_paths)
    assert generation_refresh_id(isolated_substrate) == "prior"
    assert generation_refresh_id(substrate_read_snapshot_path()) == "prior"
    assert list(isolated_substrate.parent.glob("substrate.candidate-*.failed-*"))


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


def test_candidate_generation_rejects_explicit_serving_writer(
    isolated_substrate: Path,
) -> None:
    """An accidental canonical path cannot bypass a live candidate context."""
    _record_verified_generation(isolated_substrate, "prior")
    update_read_snapshot()
    serving_contents = isolated_substrate.read_bytes()

    with pytest.raises(CandidateGenerationRejected, match="outside its staged substrate"):
        with candidate_generation():
            with connect(isolated_substrate):
                pass

    assert isolated_substrate.read_bytes() == serving_contents
    assert generation_refresh_id(isolated_substrate) == "prior"
    assert generation_refresh_id(substrate_read_snapshot_path()) == "prior"
    assert list(isolated_substrate.parent.glob("substrate.candidate-*.failed-*"))


@pytest.mark.parametrize("signal_number", (signal.SIGINT, signal.SIGTERM))
def test_candidate_signal_archives_every_sidecar_and_retains_serving_triple(
    isolated_substrate: Path,
    signal_number: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _record_verified_generation(isolated_substrate, "prior")
    update_read_snapshot()
    assert write_substrate_status_manifest(isolated_substrate) is not None
    serving_paths = (
        isolated_substrate,
        substrate_read_snapshot_path(),
        substrate_status_manifest_path(isolated_substrate),
    )
    serving_contents = {path: path.read_bytes() for path in serving_paths}
    original_handler = signal.getsignal(signal_number)

    with caplog.at_level("WARNING", logger="lynchpin.substrate.connection"):
        with pytest.raises(CandidateGenerationInterrupted) as interrupted:
            with candidate_generation() as generation:
                candidate_snapshot = generation.candidate.with_suffix(".read-snapshot.duckdb")
                candidate_manifest = substrate_status_manifest_path(generation.candidate)
                generation.candidate.with_name(f"{generation.candidate.name}.wal").write_bytes(
                    b"candidate wal"
                )
                candidate_snapshot.write_bytes(b"candidate snapshot")
                candidate_snapshot.with_suffix(".tmp").write_bytes(b"candidate snapshot temp")
                candidate_manifest.write_text("{}")
                candidate_manifest.with_name(f".{candidate_manifest.name}.tmp").write_text("{}")
                signal.raise_signal(signal_number)

    assert interrupted.value.signal_number == signal_number
    assert signal.getsignal(signal_number) == original_handler
    assert all(path.read_bytes() == serving_contents[path] for path in serving_paths)
    assert generation_refresh_id(isolated_substrate) == "prior"
    assert generation_refresh_id(substrate_read_snapshot_path()) == "prior"
    for artifact in (
        generation.candidate,
        generation.candidate.with_name(f"{generation.candidate.name}.wal"),
        generation.candidate.with_suffix(".read-snapshot.duckdb"),
        generation.candidate.with_suffix(".read-snapshot.duckdb").with_suffix(".tmp"),
        substrate_status_manifest_path(generation.candidate),
        substrate_status_manifest_path(generation.candidate).with_name(
            f".{substrate_status_manifest_path(generation.candidate).name}.tmp"
        ),
    ):
        assert not artifact.exists()
        assert list(artifact.parent.glob(f"{artifact.name}.cancelled-*"))
    assert any(
        "cancelled candidate generation artifacts; canonical generation was not modified"
        in message
        for message in caplog.messages
    )


def test_candidate_manifest_failure_retains_verified_serving_generation(
    isolated_substrate: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _record_verified_generation(isolated_substrate, "prior")
    update_read_snapshot()
    monkeypatch.setattr(
        "lynchpin.substrate.status_manifest.write_substrate_status_manifest",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(RuntimeError, match="manifest publication"):
        with candidate_generation() as generation:
            _record_verified_generation(generation.candidate, "current")

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
    interrupted.with_suffix(".manifest.json").write_text("{}")

    with pytest.raises(RuntimeError, match="injected failure"):
        with candidate_generation():
            raise RuntimeError("injected failure")

    assert generation_refresh_id(isolated_substrate) == "prior"
    assert not interrupted.exists()
    assert not interrupted.with_name(f"{interrupted.name}.wal").exists()
    assert not interrupted.with_suffix(".read-snapshot.duckdb").exists()
    assert not interrupted.with_suffix(".manifest.json").exists()
    assert list(interrupted.parent.glob(f"{interrupted.name}.interrupted-*"))
    assert list(interrupted.parent.glob(f"{interrupted.name}.wal.interrupted-*"))
    assert list(
        interrupted.parent.glob(
            f"{interrupted.with_suffix('.read-snapshot.duckdb').name}.interrupted-*"
        )
    )
    assert list(
        interrupted.parent.glob(
            f"{interrupted.with_suffix('.manifest.json').name}.interrupted-*"
        )
    )


def test_candidate_publication_replaces_only_verified_generation(
    isolated_substrate: Path,
) -> None:
    _record_verified_generation(isolated_substrate, "prior")
    update_read_snapshot()
    assert write_substrate_status_manifest(isolated_substrate) is not None

    with candidate_generation() as generation:
        _record_verified_generation(generation.candidate, "current")

    assert generation_refresh_id(isolated_substrate) == "current"
    assert generation_refresh_id(substrate_read_snapshot_path()) == "current"
    manifest = load_current_substrate_status_manifest(isolated_substrate)
    assert manifest is not None
    assert manifest["substrate_path"] == str(isolated_substrate)
    assert manifest["latest_refresh_id"] == "current"
    assert manifest["latest_graph_refresh_id"] is None
    assert manifest["latest_promotion_status"] == "ok"
    assert manifest["substrate_size_bytes"] == isolated_substrate.stat().st_size
    assert manifest["substrate_mtime_ns"] == isolated_substrate.stat().st_mtime_ns
    assert manifest["promotion_count"] == 2
    assert not substrate_status_manifest_path(generation.candidate).exists()
    assert list(isolated_substrate.parent.glob("substrate.manifest.json.previous-*"))
    archived = list(isolated_substrate.parent.glob("substrate.duckdb.previous-*"))
    assert len(archived) == 1
    assert generation_refresh_id(archived[0]) == "prior"


def test_candidate_publication_defers_signal_until_serving_triple_is_complete(
    isolated_substrate: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lynchpin.substrate.connection as connection

    _record_verified_generation(isolated_substrate, "prior")
    update_read_snapshot()
    assert write_substrate_status_manifest(isolated_substrate) is not None
    serving_manifest = substrate_status_manifest_path(isolated_substrate)
    real_archive_generation = connection._archive_generation
    signal_injected = False

    def archive_with_signal(path: Path, label: str) -> Path | None:
        nonlocal signal_injected
        if path == serving_manifest and not signal_injected:
            signal_injected = True
            signal.raise_signal(signal.SIGTERM)
        return real_archive_generation(path, label)

    monkeypatch.setattr(connection, "_archive_generation", archive_with_signal)

    with pytest.raises(CandidateGenerationInterrupted) as interrupted:
        with candidate_generation() as generation:
            _record_verified_generation(generation.candidate, "current")

    assert signal_injected
    assert interrupted.value.signal_number == signal.SIGTERM
    assert generation_refresh_id(isolated_substrate) == "current"
    assert generation_refresh_id(substrate_read_snapshot_path()) == "current"
    manifest = load_current_substrate_status_manifest(isolated_substrate)
    assert manifest is not None
    assert manifest["latest_refresh_id"] == "current"
    assert not list(isolated_substrate.parent.glob("substrate.candidate-*"))


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
