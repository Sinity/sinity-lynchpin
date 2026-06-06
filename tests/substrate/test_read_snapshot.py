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
    connect,
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
