from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class FakeExplainReport:
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return self.payload


def test_machine_explain_reads_direct_live_sources(monkeypatch) -> None:
    from lynchpin.analysis.machine import explain
    from lynchpin.mcp.tools.machine_status import machine_explain

    calls: list[dict[str, Any]] = []

    def fake_machine_explain(**kwargs: Any) -> FakeExplainReport:
        calls.append(kwargs)
        return FakeExplainReport({"host": "sinnix-prime", "signatures": [], "signature_headline": None})

    monkeypatch.setattr(explain, "machine_explain", fake_machine_explain)

    result = machine_explain(window_hours=6.0)

    assert calls == [{"window_hours": 6.0, "end": None}]
    assert result["source_mode"] == "direct_live_sources"
    assert result["substrate_promotion_required"] is False
    assert result["host"] == "sinnix-prime"
    assert "text" not in result


def test_machine_explain_text_flag_renders_narrative(monkeypatch) -> None:
    from lynchpin.analysis.machine import explain
    from lynchpin.mcp.tools.machine_status import machine_explain

    monkeypatch.setattr(explain, "machine_explain", lambda **_: FakeExplainReport({"ok": True}))
    monkeypatch.setattr(explain, "render_machine_explain_text", lambda report: "rendered narrative")

    result = machine_explain(text=True)

    assert result["text"] == "rendered narrative"


def test_machine_explain_parses_end_timestamp(monkeypatch) -> None:
    from lynchpin.analysis.machine import explain
    from lynchpin.mcp.tools.machine_status import machine_explain

    calls: list[dict[str, Any]] = []

    def fake_machine_explain(**kwargs: Any) -> FakeExplainReport:
        calls.append(kwargs)
        return FakeExplainReport({})

    monkeypatch.setattr(explain, "machine_explain", fake_machine_explain)

    machine_explain(end="2026-08-18T12:00:00Z")

    assert calls[0]["end"] == datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


def test_lynchpin_machine_router_pressure_narrative_calls_machine_explain(monkeypatch) -> None:
    from lynchpin.mcp.tools import machine_status
    from lynchpin.mcp.tools.public import lynchpin_machine

    calls: list[dict[str, Any]] = []

    def fake_machine_explain(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"host": "sinnix-prime", "source_mode": "direct_live_sources"}

    monkeypatch.setattr(machine_status, "machine_explain", fake_machine_explain)

    result = lynchpin_machine(action="pressure", view="narrative", end="2026-08-18T12:00:00Z")

    assert result["ok"] is True
    assert calls == [{"end": "2026-08-18T12:00:00Z"}]
    assert result["data"]["source_mode"] == "direct_live_sources"
