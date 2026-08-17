"""The coverage report must count records, never events.

The fixture reproduces the two real failure shapes the product exists to
catch: a flood of duplicate emissions of one record (the 2026-08-17
empty-pageToken loop emitted one heart-rate record 473 times), and a
corrupt epoch-era measurement timestamp that would otherwise stretch
coverage bounds back to 1970.
"""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace


def _event(kind: str, ts: str, **payload):
    parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return SimpleNamespace(
        kind=kind,
        ts=parsed,
        date=parsed.date(),
        payload={"kind": kind, "ts": ts, **payload},
    )


def _fixture_events():
    hr = dict(
        source="com.xiaomi.wearable",
        device_model="Smart Band 10",
        recording_method="automatically_recorded",
    )
    events = [
        # One record emitted three times (flood shape) -- one unique record.
        _event("health_heart_rate", "2026-08-16T22:31:42Z", record_id="hr-1",
               start="2026-08-16T18:00:00Z", modified="2026-08-16T18:33:14Z", **hr),
        _event("health_heart_rate", "2026-08-16T22:32:00Z", record_id="hr-1",
               start="2026-08-16T18:00:00Z", modified="2026-08-16T18:33:14Z", **hr),
        _event("health_heart_rate", "2026-08-16T22:32:18Z", record_id="hr-1",
               start="2026-08-16T18:00:00Z", modified="2026-08-16T18:33:14Z", **hr),
        # A second record one hour later, then a third after a six-day gap.
        _event("health_heart_rate", "2026-08-16T22:40:00Z", record_id="hr-2",
               start="2026-08-16T19:00:00Z", modified="2026-08-16T19:33:00Z", **hr),
        _event("health_heart_rate", "2026-08-16T22:41:00Z", record_id="hr-3",
               start="2026-08-22T19:00:00Z", modified="2026-08-22T19:33:00Z", **hr),
        # Epoch-era corruption: surfaced as an anomaly, excluded from bounds.
        _event("health_heart_rate", "2026-08-16T22:42:00Z", record_id="hr-epoch",
               start="1970-01-01T00:00:00Z", modified="2026-04-15T08:41:13Z",
               source="com.sec.android.app.shealth", device_model=None,
               recording_method="unknown"),
        # A pre-metadata-era event: counted, but not id-attributable.
        _event("health_sleep", "2026-08-14T20:00:00Z",
               start="2026-08-14T00:21:00Z", source="com.xiaomi.wearable"),
        # Exercise with a pending route consent.
        _event("health_exercise", "2026-08-16T22:43:00Z", record_id="ex-1",
               start="2026-08-10T10:00:00Z", route="consent_required",
               source="com.sec.android.app.shealth"),
        # Sweep lifecycle receipts.
        _event("health_backfill", "2026-08-16T23:34:44Z", type="HeartRateRecord",
               records=1342881, pages=1572, generation=3, window="all"),
        _event("health_sweep_failed", "2026-08-16T18:14:43Z", type="StepsRecord",
               pages_read=1592, records_emitted=254720, reason="quota"),
        _event("health_deletion", "2026-08-16T23:00:00Z", type="StepsRecord",
               record_id="gone-1"),
        _event("lane_blocked", "2026-08-16T23:34:44Z", lane="health",
               reason="token HydrationRecord: missing READ_HYDRATION"),
        # Non-health noise the scan must skip.
        _event("ambient_context", "2026-08-16T22:31:00Z", lux_mean=0),
    ]
    return events


def _rows_by_kind(path):
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    by = {}
    for row in rows:
        by.setdefault(row["row"], []).append(row)
    return by


def test_coverage_counts_records_not_events(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "lynchpin.ingest.health_coverage_materialize.phone_events",
        lambda: iter(_fixture_events()),
    )
    # No vendor lane in this fixture; without the stub the report would read
    # the live capture lane and the assertions would depend on machine state.
    monkeypatch.setattr(
        "lynchpin.sources.xiaomi_cloud.latest_envelopes",
        lambda **_kwargs: {},
    )
    output = tmp_path / "health_coverage.ndjson"

    from lynchpin.ingest.health_coverage_materialize import materialize_health_coverage

    report = materialize_health_coverage(output=output)
    assert report["row_count"] > 0
    by = _rows_by_kind(output)

    band_hr = next(
        r for r in by["group"]
        if r["kind"] == "health_heart_rate" and r["source"] == "com.xiaomi.wearable"
    )
    # Three emissions of hr-1 collapse to one record; five events, three records.
    assert band_hr["unique_records"] == 3
    assert band_hr["events"] == 5
    assert band_hr["events_per_record"] == round(5 / 3, 2)
    assert band_hr["record_type"] == "HeartRateRecord"
    assert band_hr["earliest"] == "2026-08-16T18:00:00Z"
    assert band_hr["latest"] == "2026-08-22T19:00:00Z"
    assert band_hr["last_provider_write"] == "2026-08-22T19:33:00Z"

    # The 1970 record must not stretch any group's bounds...
    shealth_hr = next(
        r for r in by["group"]
        if r["kind"] == "health_heart_rate" and r["source"] == "com.sec.android.app.shealth"
    )
    assert shealth_hr["earliest"] is None
    # ...and must be surfaced as an anomaly instead.
    assert any(a["record_id"] == "hr-epoch" for a in by["anomaly"])

    # Gap analysis runs over unique records: 1h gap and a 6-day gap.
    gap = next(
        g for g in by["gap"]
        if g["kind"] == "health_heart_rate" and g["source"] == "com.xiaomi.wearable"
    )
    assert gap["unique_records"] == 3
    assert gap["gap_histogram"]["<=1h"] == 1
    assert gap["gap_histogram"]["<=7d"] == 1
    assert gap["largest_gap_seconds"] == 6 * 86_400.0

    # Pre-id events are visible as such, not silently merged.
    sleep = next(r for r in by["group"] if r["kind"] == "health_sleep")
    assert sleep["unique_records"] == 0
    assert sleep["events_without_record_id"] == 1

    # Sweep receipts join by record type; completion is a fact, count is not.
    hr_sweep = next(s for s in by["sweep"] if s["record_type"] == "HeartRateRecord")
    assert hr_sweep["sweep_completed"] is True
    steps_sweep = next(s for s in by["sweep"] if s["record_type"] == "StepsRecord")
    assert steps_sweep["sweep_completed"] is False
    assert steps_sweep["sweep_failures"] == 1
    assert steps_sweep["deleted_records"] == 1

    assert by["lane"][0]["reason"].startswith("token HydrationRecord")

    summary = by["summary"][0]
    assert summary["routes_pending_consent"] == 1
    assert summary["unique_records_total"] == 5  # hr-1..3, hr-epoch, ex-1
    # The manifest exists and records the honest totals.
    manifest = json.loads(
        output.with_suffix(".manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["dataset"] == "health_coverage"
    assert manifest["unique_records_total"] == 5


def test_witness_cross_check_unions_duplicate_sessions(monkeypatch, tmp_path) -> None:
    """Vendor-vs-HC witness rows: legacy rid-less sessions corroborate, and
    duplicate re-emitted sessions are unioned, never summed (the defect shape
    was overlap_minutes exceeding the vendor sleep window itself)."""
    from datetime import date as date_type

    from lynchpin.sources.xiaomi_cloud import XiaomiEnvelope

    hc = [
        # The same night re-emitted twice with drifting bounds, WITHOUT
        # record_id (pre-metadata-rewrite shape): 00:30-08:30 and 00:45-08:35.
        _event("health_sleep", "2026-08-15T09:00:00Z",
               start="2026-08-15T00:30:00Z", end="2026-08-15T08:30:00Z",
               source="com.xiaomi.wearable",
               # What Mi Fitness actually writes: the session, no architecture.
               stages=[{"stage": "unknown", "start": "2026-08-15T00:30:00Z",
                        "end": "2026-08-15T08:30:00Z", "minutes": 480}]),
        _event("health_sleep", "2026-08-15T10:00:00Z",
               start="2026-08-15T00:45:00Z", end="2026-08-15T08:35:00Z",
               source="com.xiaomi.wearable"),
        # HR records: two on the witness day, one rid-less.
        _event("health_heart_rate", "2026-08-15T09:01:00Z", record_id="hr-a",
               start="2026-08-15T03:00:00Z", source="com.xiaomi.wearable"),
        _event("health_heart_rate", "2026-08-15T09:02:00Z",
               start="2026-08-15T04:00:00Z", source="com.xiaomi.wearable"),
    ]

    def _envelope(kind, day, data):
        return XiaomiEnvelope(
            kind=kind, day=day, fetched_at=None, parser_version=2,
            content_sha256=None, payload={"kind": kind, "data": data},
        )

    day = date_type(2026, 8, 15)
    vendor = {
        ("vendor_sleep", day): _envelope("vendor_sleep", day, {
            "total_duration": 473,
            "sleep_score": 80,
            "segment_details": [
                # 00:29-08:36 UTC as epoch seconds.
                {"bedtime": 1786753740, "wake_up_time": 1786782960, "timezone": 8},
            ],
        }),
        ("vendor_raw_heart_rate", day): _envelope(
            "vendor_raw_heart_rate", day, {"key": "heart_rate", "count": 488, "records": []},
        ),
        # Two stage transitions off the raw plane: 30 min deep then 60 light.
        ("vendor_raw_sleep", day): _envelope(
            "vendor_raw_sleep", day, {"key": "sleep", "count": 1, "records": [
                {"value": {"items": [
                    {"state": 2, "start_time": 1786753740, "end_time": 1786755540},
                    {"state": 3, "start_time": 1786755540, "end_time": 1786759140},
                ]}},
            ]},
        ),
    }

    monkeypatch.setattr(
        "lynchpin.ingest.health_coverage_materialize.phone_events",
        lambda: iter(hc),
    )
    monkeypatch.setattr(
        "lynchpin.sources.xiaomi_cloud.latest_envelopes",
        lambda **_kwargs: vendor,
    )
    output = tmp_path / "health_coverage.ndjson"

    from lynchpin.ingest.health_coverage_materialize import materialize_health_coverage

    materialize_health_coverage(output=output)
    by = _rows_by_kind(output)

    sleep = next(w for w in by["witness"] if w["metric"] == "sleep")
    assert sleep["both_witnesses"] is True
    assert sleep["hc_sessions"] == 2
    # Union of 00:30-08:30 and 00:45-08:35 is 00:30-08:35 = 485 min, not the
    # 960-min sum of the two overlapping emissions.
    assert sleep["hc_sleep_minutes"] == 485.0
    # Overlap is bounded by the vendor window (00:29-08:36 = 487 min).
    assert sleep["overlap_minutes"] == 485.0
    assert sleep["vendor_sleep_minutes"] == 473
    # Sleep ARCHITECTURE, in the shape Mi Fitness produces for a short
    # session: one stage-free block into Health Connect while the vendor
    # plane still resolves it. (The main night IS staged in HC; the row
    # exists to make the difference visible per day rather than assumed.)
    assert sleep["vendor_stage_minutes"] == {"deep": 30.0, "light": 60.0}
    assert sleep["hc_stage_labels"] == ["unknown"]

    hr = next(w for w in by["witness"] if w["metric"] == "heart_rate")
    assert hr["vendor_samples"] == 488
    # Both the rid-carrying and the legacy rid-less record count.
    assert hr["hc_records"] == 2
    assert hr["both_witnesses"] is True

    summary = by["summary"][0]
    assert summary["witness_days"] == 2
    assert summary["witness_corroborated"] == 2


def test_registered_in_dag() -> None:
    from lynchpin.core.source_contracts import source_contract
    from lynchpin.materialization import _dataset_builders, _materializers

    contract = source_contract("health_coverage")
    assert contract.materialization_executor.ref == "health_coverage"
    assert "health_coverage" in _materializers()
    assert "health_coverage" in _dataset_builders()
