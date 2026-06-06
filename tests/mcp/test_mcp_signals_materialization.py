from __future__ import annotations

from typing import Any


def _stub_read_path(monkeypatch) -> list[str]:
    calls: list[str] = []

    def fake_ensure_substrate_materialized_for_read(*, caller: str, window=None):
        calls.append(caller)
        return {"name": "evidence_graph_substrate", "status": "ready", "caller": caller}

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

    monkeypatch.setattr(
        "lynchpin.mcp.tools.signals.ensure_substrate_materialized_for_read",
        fake_ensure_substrate_materialized_for_read,
    )
    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: "fixture.duckdb")
    monkeypatch.setattr("lynchpin.substrate.connection.connect", lambda *_args, **_kwargs: Conn())
    return calls


def test_project_health_materializes_before_required_snapshot(monkeypatch) -> None:
    calls = _stub_read_path(monkeypatch)
    monkeypatch.setattr(
        "lynchpin.mcp.tools.signals.require_best_materialized_refresh_id",
        lambda *_args, **_kwargs: "rid-signals",
    )
    monkeypatch.setattr(
        "lynchpin.substrate.readers_signals.load_project_health_rows",
        lambda *_args, **_kwargs: [("lynchpin", 2, 1, 0, None, 3, 2.0)],
    )

    from lynchpin.mcp.tools.signals import project_health

    rows = project_health()

    assert calls == ["project_health"]
    assert rows[0]["project"] == "lynchpin"


def test_cross_source_lag_reports_materialization_when_no_snapshot(monkeypatch) -> None:
    calls = _stub_read_path(monkeypatch)
    monkeypatch.setattr(
        "lynchpin.mcp.tools.signals.best_materialized_refresh_id",
        lambda *_args, **_kwargs: None,
    )

    from lynchpin.mcp.tools.signals import cross_source_lag

    result = cross_source_lag()

    assert calls == ["cross_source_lag"]
    assert result["error"] == "no data"
    assert result["materialization"]["caller"] == "cross_source_lag"
