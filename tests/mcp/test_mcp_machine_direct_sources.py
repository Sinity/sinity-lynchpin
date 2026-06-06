from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class FakeReport:
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return self.payload


def test_machine_service_io_for_xtask_invocation_reads_direct_sources(
    monkeypatch,
) -> None:
    from lynchpin.analysis.machine import service_io
    from lynchpin.mcp.tools.machine import machine_service_io_for_xtask_invocation

    calls: list[dict[str, Any]] = []

    def fake_report(invocation_id: int, **kwargs: Any) -> FakeReport:
        calls.append({"invocation_id": invocation_id, **kwargs})
        return FakeReport(
            {
                "target": {"source_id": f"xtask:live:{invocation_id}"},
                "services": [],
                "caveats": ["direct source test"],
            }
        )

    monkeypatch.setattr(
        service_io, "analyze_machine_service_io_for_xtask_invocation", fake_report
    )

    result = machine_service_io_for_xtask_invocation(
        42,
        limit=5,
        min_total_mib=1.5,
        include_below_processes=True,
        below_top_per_sample=7,
    )

    assert calls == [
        {
            "invocation_id": 42,
            "limit": 5,
            "min_total_mib": 1.5,
            "include_below_processes": True,
            "below_top_per_sample": 7,
        }
    ]
    assert result["source_mode"] == "direct_live_sources"
    assert result["substrate_promotion_required"] is False
    assert "sinnix machine telemetry SQLite" in result["source_databases"]
    assert result["target"]["source_id"] == "xtask:live:42"


def test_machine_xtask_contention_reads_direct_sources(monkeypatch) -> None:
    from lynchpin.analysis.machine import xtask_contention
    from lynchpin.mcp.tools.machine import machine_xtask_contention

    calls: list[dict[str, Any]] = []

    def fake_report(**kwargs: Any) -> FakeReport:
        calls.append(kwargs)
        return FakeReport(
            {
                "generated_at": datetime(2026, 6, 6, tzinfo=timezone.utc),
                "rows": [{"source_id": "xtask:live:1"}],
            }
        )

    monkeypatch.setattr(xtask_contention, "analyze_xtask_contention", fake_report)

    result = machine_xtask_contention(
        start="2026-06-06T10:00:00+00:00",
        end="2026-06-06T11:00:00+00:00",
        command="test",
        limit=3,
        min_duration_s=45.0,
        min_io_full_max=10.0,
        success_only=True,
        include_below_processes=True,
        below_top_per_sample=4,
    )

    assert calls[0]["start"] == datetime(2026, 6, 6, 10, tzinfo=timezone.utc)
    assert calls[0]["end"] == datetime(2026, 6, 6, 11, tzinfo=timezone.utc)
    assert calls[0]["command"] == "test"
    assert calls[0]["limit"] == 3
    assert calls[0]["min_duration_s"] == 45.0
    assert calls[0]["min_io_full_max"] == 10.0
    assert calls[0]["include_failures"] is False
    assert calls[0]["include_below_processes"] is True
    assert calls[0]["below_top_per_sample"] == 4
    assert result["source_mode"] == "direct_live_sources"
    assert result["substrate_promotion_required"] is False
    assert result["rows"] == [{"source_id": "xtask:live:1"}]
