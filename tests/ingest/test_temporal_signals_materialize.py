from __future__ import annotations

import json
from datetime import date, timedelta
from types import SimpleNamespace

from lynchpin.ingest.temporal_signals_materialize import (
    TEMPORAL_SIGNALS_SCHEMA_VERSION,
    materialize_temporal_signals,
)
from lynchpin.sources.temporal_signals import iter_temporal_signals


def test_materialize_temporal_signals_merges_window_and_tracks_covered_dates(monkeypatch, tmp_path) -> None:
    output = tmp_path / "signals.ndjson"
    output.write_text(
        json.dumps(
            {
                "kind": "temporal_anomaly",
                "signal": "old",
                "event_date": "2026-05-01",
                "summary": "old",
                "payload": {"value": 1},
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    output.with_suffix(".manifest.json").write_text(
        json.dumps({"covered_dates": ["2026-05-01"]}),
        encoding="utf-8",
    )
    detector_calls = []
    ensure_calls = []

    def fake_detect_temporal_signals(*, start, end, ensure_inputs=True):
        detector_calls.append((start, end, ensure_inputs))
        return (
            SimpleNamespace(
                kind="temporal_trend",
                signal="deep_work_min",
                event_date=date(2026, 5, 2),
                summary="rising",
                payload={"direction": "rising"},
            ),
        )

    monkeypatch.setattr(
        "lynchpin.ingest.temporal_signals_materialize.detect_temporal_signals",
        fake_detect_temporal_signals,
    )
    monkeypatch.setattr(
        "lynchpin.ingest.temporal_signals_materialize._temporal_input_files",
        lambda start, end: (),
    )
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, window: ensure_calls.append((name, window)),
    )
    monkeypatch.setattr("lynchpin.materialization.audit_materialization", lambda: ())

    manifest = materialize_temporal_signals(
        output=output,
        start=date(2026, 5, 2),
        end=date(2026, 5, 4),
    )
    rows = list(iter_temporal_signals(output))

    assert [(row.kind, row.signal, row.event_date) for row in rows] == [
        ("temporal_anomaly", "old", date(2026, 5, 1)),
        ("temporal_trend", "deep_work_min", date(2026, 5, 2)),
    ]
    assert manifest["covered_dates"] == ["2026-05-01", "2026-05-02", "2026-05-03"]
    assert manifest["schema_version"] == TEMPORAL_SIGNALS_SCHEMA_VERSION
    assert manifest["window_semantics"] == "[start, end) — start inclusive, end exclusive"
    assert detector_calls == [(date(2026, 5, 2), date(2026, 5, 3), False)]
    assert ensure_calls == [
        (name, (date(2026, 5, 2) - timedelta(days=28), date(2026, 5, 4)))
        for name in (
            "activitywatch_derived",
            "atuin",
            "polylogue",
            "webhistory",
            "browser_bookmarks",
            "communications",
            "arbtt",
            "google_takeout",
            "sleep",
            "health",
        )
    ]


def test_temporal_signals_empty_tail_preserves_indexed_history(monkeypatch, tmp_path) -> None:
    output = tmp_path / "signals.ndjson"
    event = SimpleNamespace(
        kind="temporal_trend",
        signal="deep_work_min",
        event_date=date(2026, 5, 1),
        summary="stable",
        payload={"value": 1},
    )
    monkeypatch.setattr(
        "lynchpin.ingest.temporal_signals_materialize._ensure_temporal_inputs",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        "lynchpin.ingest.temporal_signals_materialize.detect_temporal_signals",
        lambda **_kwargs: (event,),
    )
    materialize_temporal_signals(start=date(2026, 5, 1), end=date(2026, 5, 2), output=output)
    monkeypatch.setattr(
        "lynchpin.ingest.temporal_signals_materialize.detect_temporal_signals",
        lambda **_kwargs: (),
    )

    manifest = materialize_temporal_signals(start=date(2026, 5, 2), end=date(2026, 5, 4), output=output)

    assert len(output.read_text(encoding="utf-8").splitlines()) == 1
    assert manifest["row_count"] == 1
    assert manifest["row_counts"] == {"2026-05-01": 1}


def test_temporal_inputs_refresh_only_activitywatch_tail(monkeypatch) -> None:
    from lynchpin.ingest.temporal_signals_materialize import _ensure_temporal_inputs

    calls = []
    monkeypatch.setattr(
        "lynchpin.materialization.audit_materialization",
        lambda: (
            SimpleNamespace(
                name="activitywatch_derived",
                status="ready",
                tail_stale=True,
                first_date=date(2024, 2, 15),
                last_date=date(2026, 5, 3),
            ),
        ),
    )
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, window: calls.append((name, window)),
    )

    _ensure_temporal_inputs(date(2026, 5, 1), date(2026, 5, 5))

    assert calls[0] == ("activitywatch_derived", (date(2026, 5, 3), date(2026, 5, 6)))
    assert all(window == (date(2026, 5, 1), date(2026, 5, 6)) for _, window in calls[1:])


def test_temporal_inputs_do_not_rebuild_before_known_product_coverage(monkeypatch) -> None:
    from lynchpin.ingest.temporal_signals_materialize import _ensure_temporal_inputs

    calls = []
    rows = (
        SimpleNamespace(
            name="activitywatch_derived",
            status="ready",
            tail_stale=False,
            first_date=date(2024, 2, 15),
            last_date=date(2026, 8, 20),
        ),
        SimpleNamespace(
            name="polylogue",
            status="ready",
            tail_stale=False,
            first_date=date(2022, 12, 11),
            last_date=date(2026, 7, 31),
        ),
    )
    monkeypatch.setattr("lynchpin.materialization.audit_materialization", lambda: rows)
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, window: calls.append((name, window)),
    )

    _ensure_temporal_inputs(date(2011, 11, 1), date(2026, 8, 20))

    assert ("activitywatch_derived", (date(2024, 2, 15), date(2026, 8, 21))) in calls
    assert ("polylogue", (date(2022, 12, 11), date(2026, 8, 1))) in calls


def test_iter_temporal_signals_converges_default_materialization(monkeypatch, tmp_path) -> None:
    from lynchpin.sources import temporal_signals

    product = tmp_path / "signals.ndjson"
    product.write_text(
        json.dumps(
            {
                "kind": "changepoint",
                "signal": "focus",
                "event_date": "2026-05-24",
                "summary": "focus changed",
                "payload": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    calls = []

    def fake_ensure_materialized(name, *, window=None):
        calls.append((name, window))

    monkeypatch.setattr(temporal_signals, "temporal_signals_path", lambda root=None: product)
    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)

    rows = list(temporal_signals.iter_temporal_signals(start=date(2026, 5, 24), end=date(2026, 5, 25)))

    assert [row.summary for row in rows] == ["focus changed"]
    assert calls == [("temporal_signals", (date(2026, 5, 24), date(2026, 5, 25)))]

    calls.clear()
    assert [row.summary for row in temporal_signals.iter_temporal_signals(product)] == ["focus changed"]
    assert calls == []
