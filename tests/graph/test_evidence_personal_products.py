from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from lynchpin.graph.evidence_personal_products import add_personal_products


def test_communication_evidence_does_not_advertise_teams_transcripts(monkeypatch) -> None:
    monkeypatch.setattr(
        "lynchpin.graph.evidence_personal_products._source_overlaps",
        lambda source, **kwargs: source == "communications",
    )
    monkeypatch.setattr(
        "lynchpin.sources.communications.daily_communication_activity",
        lambda **kwargs: [
            SimpleNamespace(
                date=date(2026, 5, 23),
                event_count=2,
                outbound_count=1,
                conversation_count=1,
                source_count=1,
            )
        ],
    )

    nodes = []
    add_personal_products(nodes, start=date(2026, 5, 23), end=date(2026, 5, 24))

    assert [node.kind for node in nodes] == ["communication_activity"]
    assert nodes[0].caveats == ()


def test_google_takeout_evidence_uses_typed_daily_activity(monkeypatch) -> None:
    monkeypatch.setattr(
        "lynchpin.graph.evidence_personal_products._source_overlaps",
        lambda source, **kwargs: source == "google_takeout",
    )
    monkeypatch.setattr(
        "lynchpin.sources.google_takeout_products.iter_daily_activity",
        lambda **kwargs: [
            SimpleNamespace(
                date=date(2026, 5, 23),
                product="my_activity",
                service="Search",
                event_count=3,
            )
        ],
    )

    nodes = []
    add_personal_products(nodes, start=date(2026, 5, 23), end=date(2026, 5, 24))

    assert [node.kind for node in nodes] == ["google_activity_day"]
    assert nodes[0].payload["product"] == "my_activity"
    assert nodes[0].payload["event_count"] == 3
