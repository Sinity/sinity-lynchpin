from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from lynchpin.mcp.tools import substrate


def test_export_coverage_keeps_acquisition_gap_after_reprocessing(monkeypatch) -> None:
    row = SimpleNamespace(
        name="example_export", status="ready", row_count=4,
        first_date=date(2024, 1, 1), last_date=date(2024, 2, 1),
        reason="canonical product ready", materialization_hint="run materializer",
        to_json=lambda: {"substrate_status": "ok", "collection_model": "event_export"},
    )
    import lynchpin.materialization as materialization

    monkeypatch.setattr(materialization, "audit_materialization", lambda: [row])
    monkeypatch.setattr(materialization, "materialized_dataset_coverage", lambda *_args, **_kwargs: {"overlaps_requested_window": False})
    monkeypatch.setattr(materialization, "ensure_materialized", lambda *_args, **_kwargs: SimpleNamespace(to_json=lambda: {"status": "ready"}))
    status = substrate.contract_coverage(source="example_export", start="2026-01-01", end="2026-01-31")[0]
    assert status["materialization"] == {"status": "ready"}
    assert status["freshness"]["source_event_last_date"] == "2024-02-01"
    assert status["freshness"]["acquisition_gap"] is True
    assert status["freshness"]["needed_action"] == "acquire a newer owner export"
