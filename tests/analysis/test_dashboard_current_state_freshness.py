from __future__ import annotations

from lynchpin.analysis.ecosystem.dashboard_current_state import current_state_payload


def test_current_state_dashboard_includes_artifact_materialization(monkeypatch, tmp_path) -> None:
    analysis = tmp_path / "analysis"
    analysis.mkdir()
    (analysis / "current_state_context_pack.json").write_text(
        '{"projects": [{"project": "lynchpin", "rows": []}], "claims": []}',
        encoding="utf-8",
    )
    (analysis / "current_state_narrative.json").write_text(
        '{"sections": [{"title": "Now", "section_type": "summary", "summary": "ok", "score": 3}]}',
        encoding="utf-8",
    )
    config = type("Config", (), {"analysis_output_dir": analysis, "local_root": tmp_path / "local"})()
    monkeypatch.setattr("lynchpin.core.io.get_config", lambda: config)
    ensured: list[str] = []

    def fake_ensure_materialized(name: str, *, cfg):
        ensured.append(name)
        return type("Result", (), {"to_json": lambda self: {"status": "ready", "changed": False, "reason": "ok"}})()

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)

    payload = current_state_payload()

    assert ensured == ["analysis_artifacts"]
    assert payload["available"] is True
    assert payload["materialization"]["context_pack"]["status"] == "ready"
    assert payload["materialization"]["narrative"]["status"] == "ready"
