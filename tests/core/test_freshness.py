from __future__ import annotations

from pathlib import Path

from lynchpin.core.freshness import (
    FreshnessReceipt,
    freshness_explain_target,
    latest_receipts,
    record_dependency,
    record_receipt,
)
from lynchpin.ingest.materialization_status import (
    compact_materialization_status,
    diagnostic_ledger_status_payload,
)


def test_records_and_filters_diagnostic_receipts(tmp_path: Path) -> None:
    ledger = tmp_path / "freshness.sqlite"
    receipt = FreshnessReceipt(
        receipt_id="fr:one",
        target="artifact:artifact.json",
        decision="fresh",
        caller="test",
        reason="artifact exists",
        artifact_paths=("/tmp/artifact.json",),
        artifact_statuses=({"path": "/tmp/artifact.json", "state": "present"},),
        created_at_utc="2026-06-05T00:00:00+00:00",
    )

    record_receipt(receipt, path=ledger)

    rows = latest_receipts(limit=5, path=ledger)
    filtered = latest_receipts(limit=5, target="artifact:artifact.json", path=ledger)
    payload = latest_receipts(
        limit=1,
        target="artifact:artifact.json",
        include_payload=True,
        path=ledger,
    )[0]

    assert rows[0]["receipt_id"] == receipt.receipt_id
    assert filtered[0]["receipt_id"] == receipt.receipt_id
    assert latest_receipts(limit=5, decision="snapshot_enqueue", path=ledger) == []
    assert payload["payload"]["artifact_statuses"][0]["state"] == "present"


def test_explain_target_reports_receipts_and_dependencies_without_queue_status(tmp_path: Path) -> None:
    ledger = tmp_path / "freshness.sqlite"
    receipt = FreshnessReceipt(
        receipt_id="fr:fixture",
        target="source:webhistory",
        decision="snapshot_enqueue",
        caller="test",
        reason="historic queued decision",
        requested_start="2026-06-01",
        requested_end="2026-06-05",
        queued_job_id="old-job",
        created_at_utc="2026-06-05T00:00:00+00:00",
    )
    record_receipt(receipt, path=ledger)
    record_dependency(
        receipt.receipt_id,
        target=receipt.target,
        depends_on="source_contract:webhistory",
        reason="fixture",
        path=ledger,
    )

    explanation = freshness_explain_target("source:webhistory", path=ledger)

    assert explanation["receipts"][0]["receipt_id"] == receipt.receipt_id
    assert "queued_jobs" not in explanation
    assert "failed_jobs" not in explanation
    assert "worker_plan" not in explanation
    assert any(row["depends_on"] == "source_contract:webhistory" for row in explanation["dependencies"])


def test_diagnostic_ledger_status_reports_product_status_without_queue(tmp_path: Path, monkeypatch) -> None:
    from tests.mcp.conftest import reload_config

    monkeypatch.setenv("LYNCHPIN_LOCAL_ROOT", str(tmp_path / "local"))
    reload_config(monkeypatch)
    receipt = FreshnessReceipt(
        receipt_id="fr:one",
        target="artifact:artifact.json",
        decision="fresh",
        caller="test",
        reason="artifact exists",
        created_at_utc="2026-06-05T00:00:00+00:00",
    )
    record_receipt(receipt)

    status = diagnostic_ledger_status_payload()

    assert status["canonical_path"].endswith("substrate.duckdb")
    assert status["canonical_present"] is False
    assert status["snapshot_present"] is False
    assert status["latest_receipts"][0]["receipt_id"] == receipt.receipt_id
    assert "queue_depth" not in status


def test_compact_materialization_status_stays_queue_free(tmp_path: Path, monkeypatch) -> None:
    from tests.mcp.conftest import reload_config

    monkeypatch.setenv("LYNCHPIN_LOCAL_ROOT", str(tmp_path / "local"))
    reload_config(monkeypatch)
    monkeypatch.setattr(
        "lynchpin.ingest.materialization_status._machine_pressure_snapshot",
        lambda: {
            "state": "ready",
            "pressure": "high",
            "blockers": ["dstate", "io_pressure", "latency", "swap"],
        },
    )

    product_status = compact_materialization_status()

    assert product_status["kind"] == "lynchpin_materialization_status"
    assert product_status["materialization"]["primary_product"] == "evidence_graph_substrate"
    assert "queue" not in product_status
    assert "queue_depth" not in product_status
    assert "latest_receipts" not in product_status
    assert product_status["machine"]["pressure"] == "high"
    assert product_status["machine"]["blockers"] == ["dstate", "io_pressure", "latency", "swap"]


def test_compact_materialization_status_requires_recorded_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tests.mcp.conftest import setup_substrate

    setup_substrate(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "lynchpin.ingest.materialization_status._machine_pressure_snapshot",
        lambda: {"state": "ready", "pressure": "normal", "blockers": []},
    )

    product_status = compact_materialization_status()

    assert product_status["materialization"]["status"] == "blocked"
    assert "no recorded promotion snapshot" in product_status["materialization"]["reason"]
    assert product_status["materialization"]["latest_materialized_refresh_id"] is None
    assert product_status["health"] == "attention"


def test_compact_materialization_status_reports_latest_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tests.mcp.conftest import setup_substrate

    setup_substrate(tmp_path, monkeypatch)
    from lynchpin.substrate.connection import connect, substrate_path

    monkeypatch.setattr(
        "lynchpin.ingest.materialization_status._machine_pressure_snapshot",
        lambda: {"state": "ready", "pressure": "normal", "blockers": []},
    )
    with connect(substrate_path()) as conn:
        conn.execute(
            """
            INSERT INTO substrate_source_status
            (refresh_id, source, kind, status, reason, row_count, recorded_at)
            VALUES ('rid-status', 'commits', 'stage', 'ok', NULL, 1,
                    TIMESTAMPTZ '2026-06-05 12:01:00+00')
            """
        )

    product_status = compact_materialization_status()

    assert product_status["health"] == "ok"
    assert product_status["materialization"]["status"] == "ready"
    assert product_status["materialization"]["latest_materialized_refresh_id"] == "rid-status"
    assert "latest_refresh_id" not in product_status["materialization"]


def test_compact_materialization_status_reports_degraded_available_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tests.mcp.conftest import setup_substrate

    setup_substrate(tmp_path, monkeypatch)
    from lynchpin.substrate.connection import connect, substrate_path

    monkeypatch.setattr(
        "lynchpin.ingest.materialization_status._machine_pressure_snapshot",
        lambda: {"state": "ready", "pressure": "normal", "blockers": []},
    )
    with connect(substrate_path()) as conn:
        conn.execute(
            """
            INSERT INTO substrate_promotion_run
            (refresh_id, status, reason, window_start, window_end, mode, counts, started_at, finished_at)
            VALUES (
                'rid-failed', 'error', 'activity_content coverage gap',
                DATE '2026-05-01', DATE '2026-05-31', 'materialized',
                '{"commits":1}', TIMESTAMPTZ '2026-06-05 12:00:00+00',
                TIMESTAMPTZ '2026-06-05 12:01:00+00'
            )
            """
        )

    product_status = compact_materialization_status()

    assert product_status["health"] == "attention"
    assert product_status["materialization"]["status"] == "failed"
    assert product_status["materialization"]["latest_materialized_refresh_id"] is None
    assert product_status["materialization"]["latest_available_refresh_id"] == "rid-failed"
    assert product_status["materialization"]["latest_available_status"] == "error"
    assert "activity_content coverage gap" in product_status["materialization"]["reason"]
