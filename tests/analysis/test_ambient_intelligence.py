from datetime import date, datetime, timezone

from lynchpin.analysis.ambient_intelligence import build_ambient_intelligence


def test_one_dated_product_contains_evidence_and_nudge_policy() -> None:
    payload = build_ambient_intelligence(
        logical_day=date(2026, 8, 7),
        circadian=[{"date": "2026-08-07", "hour": 9, "active_min": 40}],
        fragmentation=[{"date": "2026-08-07", "fragmentation": 0.8}],
        anomalies=[{"signal": "active_hours", "event_date": "2026-08-07"}],
        chores=[{"id": "sinnix-x", "title": "check", "evidence": "bead:sinnix-x"}],
        generated_at=datetime(2026, 8, 7, tzinfo=timezone.utc),
        nudge_opt_in=True,
    )
    assert payload["schema"] == "lynchpin-ambient-intelligence-v1"
    assert payload["fragmentation"]["nudge"]["eligible"] is True
    assert payload["anomalies"][0]["evidence"] == "lynchpin.temporal_signals"
    assert payload["chores"][0]["evidence"] == "bead:sinnix-x"


def test_dnd_and_rate_limit_preserve_state_without_eligibility() -> None:
    now = datetime(2026, 8, 7, 12, tzinfo=timezone.utc)
    for kwargs, reason in (({"dnd": True}, "dnd"), ({"last_nudge_at": now}, "rate_limited")):
        payload = build_ambient_intelligence(
            logical_day=date(2026, 8, 7),
            fragmentation=[{"date": "2026-08-07", "fragmentation": 0.9}],
            generated_at=now,
            nudge_opt_in=True,
            **kwargs,
        )
        assert payload["fragmentation"]["risk"] == 0.9
        assert payload["fragmentation"]["nudge"]["reason"] == reason
        assert payload["fragmentation"]["nudge"]["eligible"] is False
