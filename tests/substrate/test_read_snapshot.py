"""Tests for the substrate read-snapshot fallback.

Pin the behavior that read tools stay usable while the canonical
substrate is under an exclusive write lock — materializations can hold the
lock for 30-60+ minutes; MCP needs a path to read regardless.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import stat
import threading
import warnings

import duckdb
import pytest

from lynchpin.substrate.connection import (
    CandidateGenerationInterrupted,
    CandidateGenerationRejected,
    apply_schema,
    candidate_generation,
    bind_candidate_publication,
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
    monkeypatch.setenv("LYNCHPIN_SUBSTRATE_LOCK_ROOT", str(tmp_path / "runtime-locks"))
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


def _contending_candidate() -> None:
    try:
        with candidate_generation():
            pass
    except CandidateGenerationRejected:
        os._exit(0)
    else:
        os._exit(1)


def _kill_candidate_at_durable_step(crash_step: str) -> None:
    import lynchpin.substrate.connection as connection

    def die(step: str) -> None:
        if step == crash_step:
            os._exit(137)

    connection._publication_step = die
    with candidate_generation() as generation:
        _record_verified_generation(generation.candidate, "current")


def _fresh_process_reconcile(path: str) -> None:
    from lynchpin.substrate.status_manifest import load_current_substrate_status_manifest

    try:
        load_current_substrate_status_manifest(Path(path))
    except Exception:  # noqa: BLE001 - report the fresh-process failure via exit status.
        os._exit(1)
    else:
        os._exit(0)


def _reader_after_publication_intent(path: str, started_fd: int, result_fd: int) -> None:
    from lynchpin.substrate.connection import generation_refresh_id
    from lynchpin.substrate.status_manifest import load_current_substrate_status_manifest

    os.write(started_fd, b"started")
    manifest = load_current_substrate_status_manifest(Path(path))
    if manifest is None:
        os._exit(1)
    os.write(result_fd, (generation_refresh_id(Path(path)) or "missing").encode())
    os._exit(0)


def _fork_for_crash_simulation() -> int:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return os.fork()


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


def test_update_read_snapshot_failure_keeps_previous_snapshot_atomic(
    isolated_substrate: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lynchpin.substrate.connection as connection

    with duckdb.connect(str(isolated_substrate)) as conn:
        conn.execute("CREATE TABLE x (v INTEGER)")
        conn.execute("INSERT INTO x VALUES (42)")
    update_read_snapshot()
    snapshot = substrate_read_snapshot_path()

    def interrupted_clone(source: Path, destination: Path) -> None:
        destination.write_bytes(source.read_bytes()[:8])
        raise OSError("clone interrupted")

    monkeypatch.setattr(connection, "_reflink_clone", interrupted_clone)
    with pytest.raises(OSError, match="clone interrupted"):
        update_read_snapshot()

    with duckdb.connect(str(snapshot), read_only=True) as conn:
        assert conn.execute("SELECT v FROM x").fetchone() == (42,)
    assert not snapshot.with_suffix(".tmp").exists()


def _allow_reflink_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    import lynchpin.substrate.connection as connection

    monkeypatch.setattr(connection, "_filesystem_type", lambda _path: connection._BTRFS_SUPER_MAGIC)
    monkeypatch.setattr(connection, "_file_flags", lambda _path: 0)


def test_reflink_clone_rejects_nocow_source_or_destination(
    isolated_substrate: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lynchpin.substrate.connection as connection

    isolated_substrate.write_bytes(b"verified bytes")
    destination = isolated_substrate.with_name("candidate.duckdb")
    _allow_reflink_preflight(monkeypatch)
    monkeypatch.setattr(connection, "_file_flags", lambda path: connection._FS_NOCOW_FL if path == isolated_substrate.parent else 0)

    with pytest.raises(CandidateGenerationRejected, match="NOCOW (source|destination) directory"):
        connection._reflink_clone(isolated_substrate, destination)

    assert not destination.exists()


def test_read_snapshot_rejects_real_duckdb_wal(
    isolated_substrate: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A committed write in DuckDB's live WAL must never disappear in a clone."""
    _allow_reflink_preflight(monkeypatch)
    writer = duckdb.connect(str(isolated_substrate))
    try:
        writer.execute("CREATE TABLE committed_wal_state (value INTEGER)")
        writer.execute("INSERT INTO committed_wal_state VALUES (7)")
        wal = isolated_substrate.with_name(f"{isolated_substrate.name}.wal")
        assert wal.exists(), "DuckDB must retain this committed state in its live WAL"

        with pytest.raises(CandidateGenerationRejected, match="uncheckpointed DuckDB WAL"):
            update_read_snapshot()
    finally:
        writer.close()

    assert not substrate_read_snapshot_path().exists()


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


def test_connect_write_archives_corrupt_canonical_and_rejects_empty_rebuild(
    isolated_substrate: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recovery must never make a clean-but-empty schema look usable."""
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

    with pytest.raises(CandidateGenerationRejected, match="rebuild-candidate-indexes"):
        with connect(rebuild_corrupt=True):
            pass

    quarantined = list(canonical.parent.glob("substrate.duckdb.corrupt-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == b"not a duckdb database"
    assert not canonical.exists()
    assert generation_refresh_id(substrate_read_snapshot_path()) is None


def test_rebuild_corrupt_substrate_archives_and_never_installs_empty_schema(
    isolated_substrate: Path,
) -> None:
    from lynchpin.substrate.connection import rebuild_corrupt_substrate

    canonical = isolated_substrate
    with duckdb.connect(str(canonical)) as conn:
        conn.execute("CREATE TABLE x (v INTEGER)")
        conn.execute("INSERT INTO x VALUES (17)")
    canonical.write_bytes(b"broken canonical")

    with pytest.raises(CandidateGenerationRejected, match="verified logical predecessor"):
        rebuild_corrupt_substrate(canonical)

    quarantined = list(canonical.parent.glob("substrate.duckdb.corrupt-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == b"broken canonical"
    assert not canonical.exists()


def test_rebuild_corrupt_archives_without_attempting_schema_creation(
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

    with pytest.raises(CandidateGenerationRejected, match="verified logical predecessor"):
        rebuild_corrupt_substrate(canonical)

    assert not canonical.exists()
    assert len(list(canonical.parent.glob("substrate.duckdb.corrupt-*"))) == 1


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

    with candidate_generation(rebuild_indexes=True) as generation:
        assert generation.seed_source == isolated_substrate
        assert generation.seed_mode == "logical-index-rebuild"
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
            expected_counts["substrate_run_step"] + 2
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
        bind_candidate_publication(generation, "current", require_graph=False)

    assert generation_refresh_id(isolated_substrate) == "current"
    with duckdb.connect(str(isolated_substrate), read_only=True) as conn:
        attempt = json.loads(
            conn.execute(
                """
                SELECT message FROM substrate_run_step
                WHERE refresh_id = 'current' AND step = 'candidate_attempt_evidence'
                """
            ).fetchone()[0]
        )
    assert attempt["candidate_seed"]["mode"] == "logical-index-rebuild"
    assert attempt["candidate_seed"]["source"] == str(isolated_substrate)
    assert attempt["candidate_seed"]["logical_rows_reconstructed"] == (
        sum(expected_counts.values()) - expected_counts["substrate_meta"]
    )
    assert attempt["candidate_seed"]["candidate_bytes"] > 0
    with duckdb.connect(str(isolated_substrate), read_only=True) as conn:
        assert conn.execute(
            "SELECT count(*) FROM activity_content_day WHERE refresh_id = 'prior'"
        ).fetchone() == (1,)
    # The fixture has no serving manifest, so publication cannot retain a
    # complete rollback triple.
    assert not list(isolated_substrate.parent.glob("substrate.duckdb.previous-*"))


def test_candidate_generation_recovers_from_archived_verified_source(
    isolated_substrate: Path,
) -> None:
    """An unreadable serving path can be recovered from verified retained rows."""
    _record_verified_generation(isolated_substrate, "prior")
    update_read_snapshot()
    assert write_substrate_status_manifest(isolated_substrate) is not None
    archived = isolated_substrate.with_name(
        "substrate.duckdb.previous-20260101T000000+0000-" + "a" * 32
    )
    isolated_substrate.replace(archived)
    snapshot = substrate_read_snapshot_path()
    snapshot.replace(archived.with_name("substrate.read-snapshot.duckdb.previous-20260101T000000+0000-" + "a" * 32))
    manifest = substrate_status_manifest_path(isolated_substrate)
    assert manifest.exists()
    manifest.replace(archived.with_name("substrate.manifest.json.previous-20260101T000000+0000-" + "a" * 32))

    with candidate_generation(rebuild_indexes=True) as generation:
        assert generation.seed_source == archived
        _record_verified_generation(generation.candidate, "current")

    assert generation_refresh_id(isolated_substrate) == "current"
    assert generation_refresh_id(substrate_read_snapshot_path()) == "current"
    assert generation_refresh_id(archived) == "prior"


@pytest.mark.parametrize("failure_call", (1, 2, 3))
def test_candidate_publication_rolls_back_every_rename_failure(
    isolated_substrate: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_call: int,
) -> None:
    """Every injected serving rename failure preserves the serving triple."""
    import lynchpin.substrate.connection as connection

    _allow_reflink_preflight(monkeypatch)
    _record_verified_generation(isolated_substrate, "prior")
    update_read_snapshot()
    assert write_substrate_status_manifest(isolated_substrate) is not None
    serving_paths = (
        isolated_substrate,
        substrate_read_snapshot_path(),
        substrate_status_manifest_path(isolated_substrate),
    )
    before = {path: path.read_bytes() for path in serving_paths}
    real_replace = connection._replace
    calls = 0

    def fail_once(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == failure_call:
            raise OSError("injected publication rename failure")
        real_replace(source, destination)

    monkeypatch.setattr(connection, "_replace", fail_once)

    with pytest.raises(OSError, match="injected publication rename failure"):
        with candidate_generation():
            pass

    assert {path: path.read_bytes() for path in serving_paths} == before
    assert generation_refresh_id(isolated_substrate) == "prior"


def test_candidate_rejects_inherited_refresh_when_expected_refresh_is_missing(
    isolated_substrate: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A verified predecessor cannot stand in for this invocation's graph refresh."""
    _allow_reflink_preflight(monkeypatch)
    _record_verified_generation(isolated_substrate, "prior")
    before = isolated_substrate.read_bytes()

    with pytest.raises(CandidateGenerationRejected, match="requested promotion refresh"):
        with candidate_generation() as generation:
            bind_candidate_publication(generation, "new-refresh")

    assert isolated_substrate.read_bytes() == before
    assert generation_refresh_id(isolated_substrate) == "prior"


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
        with candidate_generation(rebuild_indexes=True):
            pass

    assert all(path.read_bytes() == serving_contents[path] for path in serving_paths)
    assert generation_refresh_id(isolated_substrate) == "prior"
    assert generation_refresh_id(substrate_read_snapshot_path()) == "prior"
    assert list(isolated_substrate.parent.glob("candidate-receipt-*.json"))


def test_ordinary_candidate_reflinks_verified_predecessor_without_logical_reseed(
    isolated_substrate: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lynchpin.substrate.connection as connection

    _record_verified_generation(isolated_substrate, "prior")
    with duckdb.connect(str(isolated_substrate)) as conn:
        conn.execute(
            "INSERT INTO activity_content_day (date, refresh_id) VALUES ('2026-08-21', 'prior')"
        )
    update_read_snapshot()
    monkeypatch.setattr(
        connection,
        "_logical_index_rebuild_seed",
        lambda *_args: (_ for _ in ()).throw(AssertionError("ordinary maintenance must not reseed logical rows")),
    )

    with candidate_generation() as generation:
        assert generation.seed_mode == "reflink"
        assert generation.seed_logical_rows == 0
        with duckdb.connect(str(generation.candidate), read_only=True) as conn:
            assert conn.execute(
                "SELECT COUNT(*) FROM activity_content_day WHERE refresh_id = 'prior'"
            ).fetchone() == (1,)
        _record_verified_generation(generation.candidate, "current")
        bind_candidate_publication(generation, "current", require_graph=False)

    assert generation_refresh_id(isolated_substrate) == "current"
    with duckdb.connect(str(isolated_substrate), read_only=True) as conn:
        attempt = json.loads(
            conn.execute(
                """
                SELECT message FROM substrate_run_step
                WHERE refresh_id = 'current' AND step = 'candidate_attempt_evidence'
                """
            ).fetchone()[0]
        )
        phase = json.loads(
            conn.execute(
                """
                SELECT message FROM substrate_run_step
                WHERE refresh_id = ? AND step = 'incremental_candidate_write'
                """,
                [generation.refresh_id],
            ).fetchone()[0]
        )
    assert attempt["candidate_seed"]["mode"] == "reflink"
    assert attempt["candidate_seed"]["logical_rows_reconstructed"] == 0
    assert {
        metric["name"]: metric["value"] for metric in phase["metrics"]
    }["candidate_seed_logical_rows"] == 0
    with duckdb.connect(str(isolated_substrate), read_only=True) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM activity_content_day WHERE refresh_id = 'prior'"
        ).fetchone() == (1,)


def test_ordinary_candidate_rejects_missing_verified_predecessor(
    isolated_substrate: Path,
) -> None:
    with pytest.raises(CandidateGenerationRejected, match="requires a verified canonical"):
        with candidate_generation():
            pass

    assert not isolated_substrate.exists()


def test_bootstrap_candidate_requires_an_empty_serving_artifact_set(
    isolated_substrate: Path,
) -> None:
    from lynchpin.substrate.connection import bootstrap_candidate_generation

    with bootstrap_candidate_generation() as generation:
        assert generation.seed_mode == "bootstrap"
        _record_verified_generation(generation.candidate, "initial")

    assert generation_refresh_id(isolated_substrate) == "initial"
    assert generation_refresh_id(substrate_read_snapshot_path()) == "initial"
    assert load_current_substrate_status_manifest(isolated_substrate) is not None
    with pytest.raises(CandidateGenerationRejected, match="requires no serving"):
        with bootstrap_candidate_generation():
            pass


def test_bootstrap_rejects_a_stale_canonical_wal(
    isolated_substrate: Path,
) -> None:
    """Bootstrap must never let a prior WAL replay into a new generation."""
    from lynchpin.substrate.connection import bootstrap_candidate_generation

    wal = isolated_substrate.with_name(f"{isolated_substrate.name}.wal")
    wal.write_bytes(b"unverified canonical WAL")

    with pytest.raises(CandidateGenerationRejected, match="WAL"):
        with bootstrap_candidate_generation():
            pass

    assert wal.exists()
    assert not isolated_substrate.exists()
    assert not list(isolated_substrate.parent.glob("substrate.candidate-*.duckdb"))


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
    assert list(isolated_substrate.parent.glob("candidate-receipt-*.json"))


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
    assert list(isolated_substrate.parent.glob("candidate-receipt-*.json"))


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
        assert not list(artifact.parent.glob(f"{artifact.name}.cancelled-*"))
    assert list(isolated_substrate.parent.glob("candidate-receipt-*.json"))


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
    assert list(isolated_substrate.parent.glob("candidate-receipt-*.json"))


def test_candidate_generation_archives_interrupted_candidate_sidecars(
    isolated_substrate: Path,
) -> None:
    _record_verified_generation(isolated_substrate, "prior")
    update_read_snapshot()
    interrupted = isolated_substrate.with_name(
        "substrate.candidate-" + "b" * 32 + ".duckdb"
    )
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
    assert list(interrupted.parent.glob("candidate-receipt-*.json"))


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
    real_replace = connection._replace
    signal_injected = False

    def replace_with_signal(source: Path, destination: Path) -> None:
        nonlocal signal_injected
        if destination == serving_manifest and not signal_injected:
            signal_injected = True
            signal.raise_signal(signal.SIGTERM)
        real_replace(source, destination)

    monkeypatch.setattr(connection, "_replace", replace_with_signal)

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


@pytest.mark.parametrize(
    "crash_step",
    (
        "intent-durable",
        "replacement-0-durable",
        "replacement-1-durable",
        "replacement-2-durable",
        "intent-cleared",
    ),
)
def test_fresh_read_reconciles_every_interrupted_publication_step(
    isolated_substrate: Path,
    crash_step: str,
) -> None:
    """The next process sees one complete prior or current triple, never a mix."""
    _record_verified_generation(isolated_substrate, "prior")
    update_read_snapshot()
    assert write_substrate_status_manifest(isolated_substrate) is not None

    writer = _fork_for_crash_simulation()
    if writer == 0:
        _kill_candidate_at_durable_step(crash_step)
        os._exit(0)
    _, writer_status = os.waitpid(writer, 0)
    assert os.waitstatus_to_exitcode(writer_status) == 137

    reader = _fork_for_crash_simulation()
    if reader == 0:
        _fresh_process_reconcile(str(isolated_substrate))
    _, reader_status = os.waitpid(reader, 0)
    assert os.waitstatus_to_exitcode(reader_status) == 0

    manifest = load_current_substrate_status_manifest(isolated_substrate)
    assert manifest is not None
    refreshes = {
        generation_refresh_id(isolated_substrate),
        generation_refresh_id(substrate_read_snapshot_path()),
        manifest["latest_refresh_id"],
    }
    assert refreshes == {"current"}


def test_reader_waits_for_live_publisher_intent_without_moving_candidate(
    isolated_substrate: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reader waits behind the publisher lock and sees one complete triple."""
    import lynchpin.substrate.connection as connection

    _allow_reflink_preflight(monkeypatch)
    _record_verified_generation(isolated_substrate, "prior")
    update_read_snapshot()
    assert write_substrate_status_manifest(isolated_substrate) is not None
    ready_read, ready_write = os.pipe()
    release_read, release_write = os.pipe()
    started_read, started_write = os.pipe()
    result_read, result_write = os.pipe()
    writer = _fork_for_crash_simulation()
    if writer == 0:
        def pause_at_intent(step: str) -> None:
            if step == "intent-durable":
                os.write(ready_write, b"ready")
                os.read(release_read, 1)

        connection._publication_step = pause_at_intent
        with candidate_generation() as generation:
            _record_verified_generation(generation.candidate, "current")
        os._exit(0)
    assert os.read(ready_read, 5) == b"ready"
    assert connection._publication_intent_path(isolated_substrate).exists()
    assert list(isolated_substrate.parent.glob("substrate.candidate-*.duckdb"))

    reader = _fork_for_crash_simulation()
    if reader == 0:
        _reader_after_publication_intent(
            str(isolated_substrate), started_write, result_write
        )
    assert os.read(started_read, 7) == b"started"
    os.set_blocking(result_read, False)
    with pytest.raises(BlockingIOError):
        os.read(result_read, 16)
    assert list(isolated_substrate.parent.glob("substrate.candidate-*.duckdb"))

    os.write(release_write, b"go")
    _, writer_status = os.waitpid(writer, 0)
    _, reader_status = os.waitpid(reader, 0)
    assert os.waitstatus_to_exitcode(writer_status) == 0
    assert os.waitstatus_to_exitcode(reader_status) == 0
    assert os.read(result_read, 16) == b"current"
    manifest = load_current_substrate_status_manifest(isolated_substrate)
    assert manifest is not None
    assert {
        generation_refresh_id(isolated_substrate),
        generation_refresh_id(substrate_read_snapshot_path()),
        manifest["latest_refresh_id"],
    } == {"current"}


def test_candidate_rejects_stale_predecessor_before_overwriting_newer_generation(
    isolated_substrate: Path,
) -> None:
    _record_verified_generation(isolated_substrate, "prior")
    update_read_snapshot()

    with pytest.raises(CandidateGenerationRejected, match="predecessor changed"):
        with candidate_generation() as generation:
            newer = isolated_substrate.with_name("newer.duckdb")
            _record_verified_generation(newer, "newer")
            newer.replace(isolated_substrate)
            _record_verified_generation(generation.candidate, "stale")

    assert generation_refresh_id(isolated_substrate) == "newer"


def test_independent_promoter_cannot_clone_or_publish_while_lock_is_held(
    isolated_substrate: Path,
) -> None:
    """The OS lock spans candidate setup, not only the final publication."""
    import lynchpin.substrate.connection as connection

    _record_verified_generation(isolated_substrate, "prior")
    with connection._promotion_lock(isolated_substrate):
        process = _fork_for_crash_simulation()
        if process == 0:
            _contending_candidate()
        _, status = os.waitpid(process, 0)
    assert os.waitstatus_to_exitcode(status) == 0


def test_direct_writer_is_rejected_when_a_read_snapshot_is_serving(
    isolated_substrate: Path,
) -> None:
    _record_verified_generation(isolated_substrate, "prior")
    update_read_snapshot()

    with pytest.raises(CandidateGenerationRejected, match="candidate_generation"):
        with connect():
            pass

    with pytest.raises(CandidateGenerationRejected, match="candidate_generation"):
        with connect(isolated_substrate):
            pass


def test_direct_writer_is_rejected_before_the_first_snapshot(
    isolated_substrate: Path,
) -> None:
    """An empty serving path is never a loophole for canonical mutation."""
    with pytest.raises(CandidateGenerationRejected, match="direct canonical"):
        with connect():
            pass
    with pytest.raises(CandidateGenerationRejected, match="direct canonical"):
        with connect(isolated_substrate):
            pass


def test_candidate_build_does_not_block_snapshot_readers(
    isolated_substrate: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Writer reservation is independent from the short publication lock."""
    _allow_reflink_preflight(monkeypatch)
    _record_verified_generation(isolated_substrate, "prior")
    update_read_snapshot()
    assert write_substrate_status_manifest(isolated_substrate) is not None
    completed = threading.Event()
    observed: list[str] = []

    def read_serving_generation() -> None:
        with connect(read_only=True) as conn:
            observed.append(
                str(
                    conn.execute(
                        "SELECT refresh_id FROM substrate_promotion_run "
                        "WHERE status = 'ok' ORDER BY finished_at DESC LIMIT 1"
                    ).fetchone()[0]
                )
            )
        completed.set()

    with candidate_generation() as generation:
        reader = threading.Thread(target=read_serving_generation)
        reader.start()
        assert completed.wait(timeout=2), "reader blocked behind candidate construction"
        reader.join(timeout=2)
        _record_verified_generation(generation.candidate, "current")

    assert observed == ["prior"]


def test_serving_generation_holds_publication_lock_for_full_observation(
    isolated_substrate: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A publisher cannot interleave a DB read with its matching manifest read."""
    import lynchpin.substrate.connection as connection
    from lynchpin.substrate.connection import serving_generation

    _allow_reflink_preflight(monkeypatch)
    _record_verified_generation(isolated_substrate, "prior")
    update_read_snapshot()
    assert write_substrate_status_manifest(isolated_substrate) is not None
    publisher_entered = threading.Event()
    release_publisher = threading.Event()

    def contend_for_publication() -> None:
        with connection._publication_write_lock(isolated_substrate):
            publisher_entered.set()
            release_publisher.wait(timeout=2)

    with serving_generation() as generation:
        assert generation.manifest is not None
        assert generation.manifest["latest_refresh_id"] == "prior"
        assert generation.connection.execute(
            "SELECT refresh_id FROM substrate_promotion_run "
            "WHERE status = 'ok' ORDER BY finished_at DESC LIMIT 1"
        ).fetchone() == ("prior",)
        publisher = threading.Thread(target=contend_for_publication)
        publisher.start()
        assert not publisher_entered.wait(timeout=0.1)
    assert publisher_entered.wait(timeout=2)
    release_publisher.set()
    publisher.join(timeout=2)


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


def test_serving_generation_omits_manifest_for_snapshot_fallback(
    isolated_substrate: Path,
) -> None:
    """A fallback snapshot never inherits the canonical database manifest."""
    from lynchpin.substrate.connection import serving_generation

    _record_verified_generation(isolated_substrate, "prior")
    update_read_snapshot()
    assert write_substrate_status_manifest(isolated_substrate) is not None
    with duckdb.connect(str(isolated_substrate)) as conn:
        conn.execute("DELETE FROM substrate_source_status")
        conn.execute("DELETE FROM substrate_promotion_run")

    with serving_generation() as generation:
        assert generation.database_path == substrate_read_snapshot_path()
        assert generation.manifest is None
        assert generation.connection.execute(
            "SELECT refresh_id FROM substrate_promotion_run"
        ).fetchone() == ("prior",)


def test_serving_generation_normalizes_string_configured_path(
    isolated_substrate: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Configured substrate paths may be strings in established MCP callers."""
    import lynchpin.substrate.connection as connection

    _record_verified_generation(isolated_substrate, "prior")
    update_read_snapshot(isolated_substrate)
    monkeypatch.setattr(connection, "substrate_path", lambda: str(isolated_substrate))

    with connection.serving_generation() as generation:
        assert generation.database_path == isolated_substrate
        assert generation.connection.execute(
            "SELECT refresh_id FROM substrate_promotion_run"
        ).fetchone() == ("prior",)


def test_read_only_canonical_uses_writable_runtime_publication_lock(
    isolated_substrate: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Serving reads retain publication consistency without checkout writes."""
    from lynchpin.substrate.locking import publication_lock_path

    canonical = isolated_substrate.parent / "canonical" / isolated_substrate.name
    canonical.parent.mkdir()
    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: canonical)
    lock_root = isolated_substrate.parent / "runtime-locks"
    lock_root.mkdir()
    _record_verified_generation(canonical, "prior")
    original_mode = stat.S_IMODE(canonical.parent.stat().st_mode)
    os.chmod(canonical.parent, 0o555)
    try:
        with connect(read_only=True) as conn:
            assert conn.execute(
                "SELECT refresh_id FROM substrate_promotion_run"
            ).fetchone() == ("prior",)
    finally:
        os.chmod(canonical.parent, original_mode)

    lock_path = publication_lock_path(canonical)
    assert lock_path.parent == lock_root
    assert lock_path.exists()
    assert not canonical.with_name(
        f".{canonical.name}.promotion.lock"
    ).exists()
