from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from tests.mcp.conftest import setup_substrate


def test_mcp_runtime_status_reports_source_and_substrate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_substrate(tmp_path, monkeypatch)
    monkeypatch.setenv("LYNCHPIN_REPO_ROOT", str(Path(__file__).resolve().parents[2]))

    from lynchpin.mcp.tools.runtime import mcp_runtime_status

    status = mcp_runtime_status()

    assert status["repo_root"]
    assert "package_root" in status
    assert status["substrate"]["path"].endswith("substrate.duckdb")
    assert "latest_materialized_refresh_id" in status["substrate"]
    assert "latest_refresh_id" not in status["substrate"]
    assert status["substrate"]["materialization"]["name"] == "evidence_graph_substrate"
    assert status["mcp"]["registered_tool_count"] > 0


def test_mcp_surface_self_check_reports_contract_tool_alignment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_substrate(tmp_path, monkeypatch)
    monkeypatch.setenv("LYNCHPIN_REPO_ROOT", str(Path(__file__).resolve().parents[2]))

    from lynchpin.mcp.tools.runtime import mcp_surface_self_check

    status = mcp_surface_self_check()

    assert status["declared_tool_count"] > 0
    assert status["registered_tool_count"] >= status["declared_tool_count"]
    assert status["missing_declared_tools"] == []
    assert status["ok"] is True


def test_diagnostic_mcp_tools_report_ledger_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.core.freshness import FreshnessReceipt, record_receipt
    from lynchpin.mcp.tools.runtime import (
        diagnostic_ledger_dependency_edges,
        diagnostic_ledger_explain,
        diagnostic_ledger_receipts,
        diagnostic_source_materialization_decision,
        diagnostic_ledger_status,
        observability_status,
        registered_tool_names,
    )

    materialization_calls: list[tuple[str, tuple[object, object] | None]] = []

    class MaterializationResult:
        def __init__(self, name: str, window=None):
            self.name = name
            self.window = window

        def to_json(self) -> dict[str, object]:
            return {
                "name": self.name,
                "status": "ready",
                "changed": False,
                "reason": "fixture",
                "coverage": {"relation": "covers_window"},
                "window": [str(part) for part in self.window] if self.window else None,
            }

    def fake_ensure_materialized(name: str, *, window=None):
        materialization_calls.append((name, window))
        return MaterializationResult(name, window)

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)

    record_receipt(
        FreshnessReceipt(
            receipt_id="fr:test",
            target="artifact:fixture.json",
            decision="snapshot_enqueue",
            caller="test",
            reason="fixture",
            queued_job_id="job",
            created_at_utc="2026-06-05T00:00:00+00:00",
        )
    )

    status = diagnostic_ledger_status()
    receipts = diagnostic_ledger_receipts(limit=1)
    filtered_receipts = diagnostic_ledger_receipts(limit=1, target="artifact:fixture.json", decision="snapshot_enqueue")
    payload_receipts = diagnostic_ledger_receipts(
        limit=1,
        target="artifact:fixture.json",
        include_payload=True,
    )

    assert "queue_depth" not in status
    assert diagnostic_ledger_explain("artifact:fixture.json")["target"] == "artifact:fixture.json"
    assert receipts[0]["receipt_id"] == "fr:test"
    assert filtered_receipts[0]["receipt_id"] == "fr:test"
    assert payload_receipts[0]["payload"]["queued_job_id"] == "job"
    source_result = diagnostic_source_materialization_decision("reddit", start="2026-06-01", end="2026-06-05")
    assert source_result["status"] == "ready"
    assert source_result["changed"] is False
    assert source_result["window"] == ["2026-06-01", "2026-06-06"]
    assert materialization_calls == [("reddit", (date(2026, 6, 1), date(2026, 6, 6)))]
    assert diagnostic_ledger_dependency_edges(target="source:reddit") == []
    panel = observability_status()
    assert panel["kind"] == "lynchpin_observability_status"
    assert panel["materialization"]["primary_product"] == "evidence_graph_substrate"
    assert "queue" not in panel
    registered = set(registered_tool_names())
    assert "diagnostic_ledger" in registered
    assert "diagnostic_queue" not in registered
    assert "diagnostic_queue_summary" not in registered
    assert "diagnostic_queue_worker_once" not in registered
    assert "diagnostic_queue_drain_plan" not in registered
    assert "diagnostic_panel_status" not in registered
    assert "freshness_queue" not in registered
    assert "freshness_status" not in registered


def test_mcp_capability_matrix_reports_contract_capabilities_without_stale_scoring(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_substrate(tmp_path, monkeypatch)
    monkeypatch.setenv("LYNCHPIN_REPO_ROOT", str(Path(__file__).resolve().parents[2]))

    from lynchpin.mcp.tools.capability import mcp_capability_matrix

    rows = {row["source"]: row for row in mcp_capability_matrix()}

    takeout = rows["google_takeout"]
    assert "gmail_takeout" in takeout["source_keys"]
    assert "google_takeout" in takeout["mcp_tools"]
    assert "google_activity_day" in takeout["graph_node_kinds"]
    assert "freshness" not in takeout
    assert "last_date" in takeout["date_bounds"]

    terminal = rows["atuin"]
    assert terminal["collection_model"] == "continuous"
    assert terminal["materialization_mode"] == "local"
    assert "freshness_target" not in terminal
    assert terminal["materialization_target"] == "source:atuin"
    assert "refresh_executor" not in terminal
    assert terminal["materialization_executor"]["kind"] == "materializer"
    assert "terminal" in terminal["mcp_tools"]

    artifacts = rows["analysis_artifacts"]
    assert artifacts["collection_model"] == "derived"
    assert artifacts["materialization_mode"] == "derived"
    assert artifacts["default_max_age_seconds"] == 1800
    assert "analysis_artifact_inventory" in artifacts["mcp_tools"]
    assert "read_analysis_artifact" in artifacts["mcp_tools"]
    assert "diagnostic_ledger_status" not in artifacts["mcp_tools"]

    messenger = rows["facebook_messenger"]
    assert "fbmessenger" in messenger["source_keys"]

    raw_log = rows["raw_log"]
    assert raw_log["graph_node_kinds"] == ["raw_log"]
