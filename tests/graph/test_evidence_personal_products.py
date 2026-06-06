from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from lynchpin.graph.evidence_personal_products import add_personal_products


_PERSONAL_PRODUCT_SOURCES = [
    "activity_content",
    "google_takeout",
    "browser_bookmarks",
    "communications",
    "irc",
    "arbtt",
]


def _stub_empty_personal_readers(monkeypatch) -> None:
    monkeypatch.setattr(
        "lynchpin.sources.activity_content.iter_activity_content_days",
        lambda **kwargs: (),
    )
    monkeypatch.setattr(
        "lynchpin.sources.google_takeout_products.iter_daily_activity",
        lambda **kwargs: (),
    )
    monkeypatch.setattr(
        "lynchpin.sources.bookmarks.daily_bookmark_activity",
        lambda **kwargs: (),
    )
    monkeypatch.setattr(
        "lynchpin.sources.communications.daily_communication_activity",
        lambda **kwargs: (),
    )
    monkeypatch.setattr(
        "lynchpin.sources.irc_raw.daily_irc_activity",
        lambda **kwargs: (),
    )
    monkeypatch.setattr(
        "lynchpin.sources.arbtt.daily_arbtt_activity",
        lambda **kwargs: (),
    )


def test_activity_content_evidence_converges_product(monkeypatch) -> None:
    ensure_calls = []
    _stub_empty_personal_readers(monkeypatch)
    monkeypatch.setattr(
        "lynchpin.graph.evidence_personal_products._ensure_source",
        lambda source, **kwargs: ensure_calls.append((source, kwargs)),
    )
    monkeypatch.setattr(
        "lynchpin.sources.activity_content.iter_activity_content_days",
        lambda *, start, end, ensure=True: [
            SimpleNamespace(
                date=date(2026, 5, 23),
                focused_seconds=3600.0,
                matched_seconds=2700.0,
                gpt_matched_seconds=1800.0,
                matched_ratio=0.75,
                gpt_matched_ratio=0.5,
                activity_seconds={"coding": 3000.0},
                topic_seconds={"lynchpin": 2400.0},
                source_counts={"activitywatch": 1},
            )
        ],
    )

    nodes = []
    add_personal_products(nodes, start=date(2026, 5, 23), end=date(2026, 5, 24))

    assert ensure_calls[:1] == [
        (
            "activity_content",
            {"start": date(2026, 5, 23), "end": date(2026, 5, 24)},
        )
    ]
    assert [source for source, _kwargs in ensure_calls] == _PERSONAL_PRODUCT_SOURCES
    assert [node.kind for node in nodes] == ["activity_content_day"]
    assert nodes[0].payload["focused_seconds"] == 3600.0


def test_communication_evidence_does_not_advertise_teams_transcripts(monkeypatch) -> None:
    _stub_empty_personal_readers(monkeypatch)
    monkeypatch.setattr(
        "lynchpin.graph.evidence_personal_products._ensure_source",
        lambda source, **kwargs: None,
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
    _stub_empty_personal_readers(monkeypatch)
    ensure_calls = []
    monkeypatch.setattr(
        "lynchpin.graph.evidence_personal_products._ensure_source",
        lambda source, **kwargs: ensure_calls.append((source, kwargs)),
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
    assert [source for source, _kwargs in ensure_calls] == _PERSONAL_PRODUCT_SOURCES
