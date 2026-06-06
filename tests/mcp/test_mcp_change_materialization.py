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
        "lynchpin.mcp.tools.change.ensure_substrate_materialized_for_read",
        fake_ensure_substrate_materialized_for_read,
    )
    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: "fixture.duckdb")
    monkeypatch.setattr("lynchpin.substrate.connection.connect", lambda *_args, **_kwargs: Conn())
    return calls


def test_file_hotspots_materializes_substrate_before_snapshot_selection(monkeypatch) -> None:
    calls = _stub_read_path(monkeypatch)
    monkeypatch.setattr(
        "lynchpin.mcp.tools.change.best_materialized_refresh_id",
        lambda *_args, **_kwargs: "rid-change",
    )
    monkeypatch.setattr(
        "lynchpin.substrate.readers_change.load_file_churn_hotspots",
        lambda *_args, **_kwargs: [("src", 2, 3, 1, "lynchpin")],
    )

    from lynchpin.mcp.tools.change import file_hotspots

    rows = file_hotspots()

    assert calls == ["mcp.change.file_hotspots"]
    assert rows == [
        {
            "path_root": "src",
            "commits": 2,
            "file_changes": 3,
            "project_count": 1,
            "top_project": "lynchpin",
        }
    ]


def test_commit_kind_attribution_reports_materialization_when_no_snapshot(monkeypatch) -> None:
    calls = _stub_read_path(monkeypatch)
    monkeypatch.setattr(
        "lynchpin.mcp.tools.change.best_materialized_refresh_id",
        lambda *_args, **_kwargs: None,
    )

    from lynchpin.mcp.tools.change import commit_kind_attribution

    result = commit_kind_attribution()

    assert calls == ["mcp.change.commit_kind_attribution"]
    assert result["rows"] == []
    assert result["materialization"]["caller"] == "mcp.change.commit_kind_attribution"
