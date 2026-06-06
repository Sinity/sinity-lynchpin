from datetime import date
from types import SimpleNamespace


def test_add_health_materialized_uses_direct_health_and_sleep(monkeypatch) -> None:
    from lynchpin.graph import evidence_system_signals

    monkeypatch.setattr(
        "lynchpin.graph.health_bridge.build_sleep_evidence",
        lambda **kwargs: (
            SimpleNamespace(
                id="sleep:2026-06-01",
                date=date(2026, 6, 1),
                summary="sleep summary",
                payload={"sleep_hours": 7.0},
            ),
        ),
    )
    monkeypatch.setattr(
        "lynchpin.graph.health_bridge.build_health_evidence",
        lambda **kwargs: (
            SimpleNamespace(
                id="health:steps:2026-06-01",
                date=date(2026, 6, 1),
                summary="steps summary",
                payload={"value": 1234},
            ),
        ),
    )
    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", lambda name, *, window: None)
    monkeypatch.setattr("lynchpin.sources.sleep_productivity.iter_sleep_productivity", lambda *, start, end, ensure=True: ())

    nodes = []
    evidence_system_signals.add_health(nodes, start=date(2026, 6, 1), end=date(2026, 6, 1))

    assert [node.id for node in nodes] == [
        "sleep:2026-06-01",
        "health:steps:2026-06-01",
    ]


def test_add_health_materialized_includes_ensured_sleep_productivity(monkeypatch) -> None:
    from lynchpin.graph import evidence_system_signals

    calls: list[tuple[str, tuple[date, date]]] = []
    monkeypatch.setattr("lynchpin.graph.health_bridge.build_sleep_evidence", lambda **kwargs: ())
    monkeypatch.setattr("lynchpin.graph.health_bridge.build_health_evidence", lambda **kwargs: ())
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, window: calls.append((name, window)),
    )
    monkeypatch.setattr(
        "lynchpin.sources.sleep_productivity.iter_sleep_productivity",
        lambda *, start, end, ensure=True: (
            SimpleNamespace(
                sleep_date=date(2026, 6, 1),
                sleep_hours=7.0,
                sleep_score=80.0,
                sleep_quality="good",
                workday_active_hours=5.0,
                workday_deep_work_min=42.0,
                productivity_vs_baseline=1.2,
            ),
        ),
    )

    nodes = []
    evidence_system_signals.add_health(nodes, start=date(2026, 6, 1), end=date(2026, 6, 1))

    assert calls == [
        ("personal_daily_signals", (date(2026, 6, 1), date(2026, 6, 2))),
        ("sleep_productivity", (date(2026, 6, 1), date(2026, 6, 2))),
    ]
    assert [node.kind for node in nodes] == ["sleep_productivity_link"]
    assert nodes[0].payload["workday_deep_work_min"] == 42.0


def test_add_health_uses_sleep_productivity_product_in_network_mode(monkeypatch) -> None:
    from lynchpin.graph import evidence_system_signals

    monkeypatch.setattr(
        "lynchpin.graph.health_bridge.build_sleep_evidence",
        lambda **kwargs: (
            SimpleNamespace(
                id="sleep:2026-06-01",
                date=date(2026, 6, 1),
                summary="sleep summary",
                payload={"sleep_hours": 7.0},
            ),
        ),
    )
    monkeypatch.setattr(
        "lynchpin.graph.health_bridge.build_health_evidence",
        lambda **kwargs: (
            SimpleNamespace(
                id="health:steps:2026-06-01",
                date=date(2026, 6, 1),
                summary="steps summary",
                payload={"value": 1234},
            ),
        ),
    )
    calls: list[tuple[str, tuple[date, date]]] = []
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, window: calls.append((name, window)),
    )
    monkeypatch.setattr(
        "lynchpin.sources.sleep_productivity.iter_sleep_productivity",
        lambda *, start, end, ensure=True: (
            SimpleNamespace(
                sleep_date=date(2026, 6, 1),
                sleep_hours=7.0,
                sleep_score=80.0,
                sleep_quality="good",
                workday_active_hours=5.0,
                workday_deep_work_min=42.0,
                productivity_vs_baseline=1.2,
            ),
        ),
    )

    nodes = []
    evidence_system_signals.add_health(nodes, start=date(2026, 6, 1), end=date(2026, 6, 1))

    assert [node.id for node in nodes] == [
        "sleep:2026-06-01",
        "health:steps:2026-06-01",
        "sleep-prod:2026-06-01",
    ]
    assert calls == [
        ("personal_daily_signals", (date(2026, 6, 1), date(2026, 6, 2))),
        ("sleep_productivity", (date(2026, 6, 1), date(2026, 6, 2))),
    ]
    assert nodes[-1].kind == "sleep_productivity_link"


def test_health_bridge_reads_personal_daily_signal_product(monkeypatch) -> None:
    from lynchpin.graph.health_bridge import build_health_evidence, build_sleep_evidence

    rows = (
        SimpleNamespace(source="health", date=date(2026, 6, 1), metric="steps", value=1234.0, dimensions={}),
        SimpleNamespace(source="health", date=date(2026, 6, 1), metric="stress_avg", value=41.5, dimensions={"count": 3}),
        SimpleNamespace(source="health", date=date(2026, 6, 1), metric="resting_heart_rate", value=62.0, dimensions={}),
        SimpleNamespace(source="health", date=date(2026, 6, 1), metric="avg_heart_rate", value=71.0, dimensions={}),
        SimpleNamespace(source="health", date=date(2026, 6, 1), metric="hrv_rmssd", value=38.0, dimensions={"count": 2}),
        SimpleNamespace(source="sleep", date=date(2026, 6, 1), metric="sleep_minutes", value=420.0, dimensions={"quality": "good"}),
        SimpleNamespace(source="sleep", date=date(2026, 6, 1), metric="sleep_score", value=82.0, dimensions={}),
        SimpleNamespace(source="sleep", date=date(2026, 6, 1), metric="sleep_deep_pct", value=18.5, dimensions={"sleep_id": "abc"}),
        SimpleNamespace(source="sleep", date=date(2026, 6, 1), metric="sleep_rem_pct", value=22.0, dimensions={"sleep_id": "abc"}),
        SimpleNamespace(source="sleep", date=date(2026, 6, 1), metric="sleep_stage_transitions", value=7.0, dimensions={"sleep_id": "abc"}),
    )
    calls = []

    def fake_iter_personal_daily_signals(**kwargs):
        calls.append(kwargs)
        return iter(rows)

    monkeypatch.setattr(
        "lynchpin.sources.personal_signals.iter_personal_daily_signals",
        fake_iter_personal_daily_signals,
    )

    health = build_health_evidence(start=date(2026, 6, 1), end=date(2026, 6, 1))
    sleep = build_sleep_evidence(start=date(2026, 6, 1), end=date(2026, 6, 1))

    assert [node.metric for node in health] == ["steps", "stress", "heart_rate", "hrv"]
    assert health[1].payload == {"avg": 41.5, "count": 3}
    assert sleep[0].sleep_hours == 7.0
    assert sleep[0].sleep_score == 82
    assert sleep[0].deep_pct == 18.5
    assert sleep[0].stage_transitions == 7
    assert calls == [
        {"start": date(2026, 6, 1), "end": date(2026, 6, 2), "ensure": True},
        {"start": date(2026, 6, 1), "end": date(2026, 6, 2), "ensure": True},
    ]
