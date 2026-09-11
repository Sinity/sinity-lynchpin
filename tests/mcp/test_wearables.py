import json
from datetime import date
from types import SimpleNamespace

import pytest

from lynchpin.mcp.tools.wearables import wearable_records


def test_phone_health_bounds_capture_dates_and_limits(monkeypatch):
    calls = []

    def events(**kwargs):
        calls.append(kwargs)
        return iter([
            SimpleNamespace(kind="power", payload={"kind": "power"}),
            *[SimpleNamespace(kind="health_steps", payload={"kind": "health_steps", "source": "vendor", "count": n}) for n in range(3)],
        ])

    monkeypatch.setattr("lynchpin.sources.phone_events.phone_events", events)
    result = wearable_records("phone_health", "2026-01-01", "2026-01-02", source="vendor", limit=2)
    assert calls == [{"start": date(2026, 1, 1), "end": date(2026, 1, 2)}]
    assert [row["count"] for row in result["rows"]] == [0, 1]
    assert result["truncated"] is True


def test_cloud_filters_measurement_days(monkeypatch):
    monkeypatch.setattr("lynchpin.sources.xiaomi_cloud.latest_envelopes", lambda: {
        ("vendor_sleep", date(2026, 1, day)): SimpleNamespace(payload={"day": str(day)})
        for day in (1, 2, 3)
    })
    result = wearable_records("xiaomi", "2026-01-02", "2026-01-02")
    assert result["rows"] == [{"day": "2"}]
    assert result["truncated"] is False


def test_coverage_reads_materialized_product_without_refresh(tmp_path, monkeypatch):
    path = tmp_path / "health.ndjson"
    path.write_text(json.dumps({"row": "summary", "unique_records_total": 7}) + "\n")
    monkeypatch.setattr("lynchpin.ingest.health_coverage_materialize.health_coverage_path", lambda: path)
    before = path.read_bytes()
    result = wearable_records("coverage")
    assert result["rows"][0]["unique_records_total"] == 7
    assert result["artifact_modified_at"]
    assert path.read_bytes() == before
    with pytest.raises(ValueError, match="whole-history"):
        wearable_records("coverage", start="2026-01-01")


def test_public_router_exposes_phone_and_vendor_reads(monkeypatch):
    from lynchpin.mcp.tools import public
    calls = []
    monkeypatch.setattr(public, "_internal_call", lambda module, fn, **kwargs: calls.append(kwargs) or {"ok": True})
    assert public.lynchpin_personal(action="phone", limit=5)["ok"]
    assert public.lynchpin_personal(action="health", view="xiaomi", start="2026-01-01", limit=8)["ok"]
    assert calls[0]["view"] == "phone"
    assert calls[1]["view"] == "xiaomi"
    assert calls[1]["limit"] == 8
