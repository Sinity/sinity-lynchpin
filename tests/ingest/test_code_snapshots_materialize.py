"""Tests for the code_snapshots materializer's substrate publication route.

The two code_snapshot tables are part of the serving substrate, so the
materializer must stage its promotion inside a candidate generation instead of
writing the canonical DuckDB file directly. Writing directly is not merely
untidy: `connect()` rejects it, and because `run_materialization_plan` defaults
to `continue_on_error=False`, the rejection aborts a whole `agentctl converge`
run at this step.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lynchpin.substrate.connection import (
    bind_candidate_publication,
    bootstrap_candidate_generation,
    candidate_generation,
    connect,
)


@pytest.fixture
def isolated_substrate(monkeypatch, tmp_path: Path) -> Path:
    """Point substrate_path at an isolated tmp file for this test."""
    target = tmp_path / "substrate.duckdb"
    monkeypatch.setenv("LYNCHPIN_SUBSTRATE_LOCK_ROOT", str(tmp_path / "runtime-locks"))
    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: target)
    return target


def _stub_chisel(monkeypatch, tmp_path: Path) -> Path:
    """Stub build_chisel_bundles with one successful project on disk."""
    output_root = tmp_path / "code-snapshots"
    project_dir = output_root / "demo"
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "demo.repomix.xml").write_text("<repo/>", encoding="utf-8")

    def fake_bundles(*, output_root: Path, **_: Any) -> dict[str, Any]:
        return {
            "projects": {
                "demo": {
                    "status": "ok",
                    "git": {"commit": "abc123", "branch": "master", "dirty": False},
                    "xml_valid": True,
                    "total_bytes": 7,
                }
            }
        }

    monkeypatch.setattr(
        "lynchpin.sources.code_snapshots.build_chisel_bundles", fake_bundles
    )
    monkeypatch.setattr(
        "lynchpin.sources.code_snapshots.code_snapshots_path", lambda: output_root
    )
    return output_root


def _record_promotion(conn, refresh_id: str) -> None:
    """Write the promotion-run/coverage rows a publication receipt requires."""
    conn.execute(
        """
        INSERT INTO substrate_promotion_run
        (refresh_id, status, reason, window_start, window_end, mode, counts,
         started_at, finished_at)
        VALUES (?, 'ok', NULL, NULL, NULL, 'test', '{}', now(), now())
        """,
        [refresh_id],
    )
    conn.execute(
        """
        INSERT INTO substrate_source_status
        (refresh_id, source, kind, status, reason, row_count, window_start,
         window_end, recorded_at)
        VALUES (?, 'fixture', 'stage', 'ok', NULL, 1, NULL, NULL, now())
        """,
        [refresh_id],
    )


def _seed_published_substrate() -> None:
    """Publish one verified generation so steady-state staging is possible."""
    with bootstrap_candidate_generation(receipt_refresh_id="seed") as generation:
        with connect() as conn:
            _record_promotion(conn, "seed")
            conn.execute("CHECKPOINT")
        bind_candidate_publication(generation, "seed", require_graph=False)


def test_materialize_promotes_through_a_candidate_generation(
    isolated_substrate: Path, monkeypatch, tmp_path: Path
) -> None:
    """With a published substrate and no active generation, the materializer
    stages its own generation and the rows reach the canonical database.

    Anti-vacuity: reverting the body to a bare ``with connect() as conn:``
    makes this red with CandidateGenerationRejected, which is exactly the
    failure that aborted the converge run.
    """
    from lynchpin.ingest.code_snapshots_materialize import materialize_code_snapshots

    _stub_chisel(monkeypatch, tmp_path)
    _seed_published_substrate()

    manifest = materialize_code_snapshots()

    assert manifest["substrate_promotion_status"] == "ok"
    assert manifest["run_count"] == 1
    assert manifest["slice_count"] == 1

    with connect(read_only=True) as conn:
        runs = conn.execute(
            "SELECT project FROM code_snapshot_run WHERE refresh_id = 'latest'"
        ).fetchall()
    assert [r[0] for r in runs] == ["demo"]


def test_materialize_reuses_an_active_candidate_generation(
    isolated_substrate: Path, monkeypatch, tmp_path: Path
) -> None:
    """Inside an existing generation the materializer promotes into it rather
    than opening a nested one (which the promotion lock would deadlock on).

    Anti-vacuity: dropping the ``in_candidate_generation()`` branch makes this
    hang or fail instead of returning an ok promotion.
    """
    from lynchpin.ingest.code_snapshots_materialize import materialize_code_snapshots

    _stub_chisel(monkeypatch, tmp_path)
    _seed_published_substrate()

    with candidate_generation(receipt_refresh_id="outer") as generation:
        manifest = materialize_code_snapshots()
        with connect() as conn:
            _record_promotion(conn, "outer")
        bind_candidate_publication(generation, "outer", require_graph=False)

    assert manifest["substrate_promotion_status"] == "ok"
    assert manifest["run_count"] == 1


def test_materialize_defers_when_no_serving_substrate_exists(
    isolated_substrate: Path, monkeypatch, tmp_path: Path
) -> None:
    """Two tables cannot bootstrap a complete generation, so promotion defers
    and reports it instead of raising or claiming success.

    Anti-vacuity: promoting anyway (or raising) changes the reported status
    away from "deferred".
    """
    from lynchpin.ingest.code_snapshots_materialize import materialize_code_snapshots

    output_root = _stub_chisel(monkeypatch, tmp_path)
    assert not isolated_substrate.exists()

    manifest = materialize_code_snapshots()

    assert manifest["substrate_promotion_status"] == "deferred"
    assert manifest["row_count"] == 0
    # The bundles themselves are still produced and retained on disk.
    assert (output_root / "demo" / "demo.repomix.xml").exists()
