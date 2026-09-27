from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _profile():
    from lynchpin.sources.polylogue_models import SessionProfile

    start = datetime(2026, 6, 6, 10, 0, tzinfo=timezone.utc)
    return SessionProfile(
        conversation_id="s1",
        provider="codex",
        title="timeline",
        message_count=3,
        word_count=12,
        first_message_at=start,
        last_message_at=start + timedelta(seconds=20),
        engaged_duration_ms=20_000,
        wall_duration_ms=20_000,
        work_event_kind="coding",
        work_event_projects=("sinity-lynchpin",),
        total_cost_usd=0.0,
        canonical_session_date=start.date(),
        tool_use_count=0,
        thinking_count=0,
        auto_tags=("test",),
    )


def test_session_composition_uses_message_transitions_and_cross_source_overlap(monkeypatch):
    import lynchpin.sources.polylogue_timeline as mod
    from lynchpin.sources.polylogue_timeline_models import PolylogueTimelineSpan

    profile = _profile()
    start = profile.first_message_at
    assert start is not None
    messages = [
        mod._RawMessage("m1", "user", start, False, False, (), 2, None),
        mod._RawMessage("m2", "assistant", start + timedelta(seconds=10), False, False, (), 4, None),
        mod._RawMessage("m3", "user", start + timedelta(seconds=20), False, False, (), 3, None),
    ]
    monkeypatch.setattr(mod, "_profile_for_session", lambda session_id: profile)
    monkeypatch.setattr(mod, "_messages", lambda session_id: messages)
    monkeypatch.setattr(mod, "_work_event_spans", lambda profile: [])
    monkeypatch.setattr(mod, "_phase_spans", lambda profile: [])

    def cross_source(_profile):
        return [
            PolylogueTimelineSpan(
                span_id="s1:aw:1",
                session_id="s1",
                provider="codex",
                lane="activitywatch",
                kind="focused",
                start=start + timedelta(seconds=5),
                end=start + timedelta(seconds=15),
                source="activitywatch.focus_timeline",
                project="sinity-lynchpin",
            )
        ]

    row = mod.session_composition("s1", cross_source_provider=cross_source)

    assert row.status == "ok"
    assert row.seconds_by_kind["assistant_response_wait"] == 10.0
    assert row.seconds_by_kind["user_gap_or_composition"] == 10.0
    assert row.cross_source_seconds["activitywatch.focus_timeline"] == 10.0
    assert row.overlap_count == 2


def test_session_composition_reports_unavailable_when_facade_fails(monkeypatch):
    import lynchpin.sources.polylogue_timeline as mod
    from lynchpin.sources.polylogue import PolylogueMaterializationError

    monkeypatch.setattr(
        mod,
        "_profile_for_session",
        lambda session_id: (_ for _ in ()).throw(
            PolylogueMaterializationError("facade empty")
        ),
    )

    row = mod.session_composition("missing")

    assert row.status == "unavailable"
    assert "facade empty" in (row.reason or "")


def test_service_spans_clip_date_wide_rows_to_session_window(monkeypatch):
    import lynchpin.sources.machine as machine
    import lynchpin.sources.polylogue_timeline as timeline
    from lynchpin.sources.machine_models import MachineServiceState

    start = datetime(2026, 6, 6, 10, 0, tzinfo=timezone.utc)
    end = start + timedelta(minutes=2)
    unit = "polylogued.service"
    rows = [
        MachineServiceState(start - timedelta(seconds=10), "host", None, unit,
                            "system", "active", "running"),
        MachineServiceState(start + timedelta(seconds=20), "host", None, unit,
                            "system", "failed", "failed"),
        MachineServiceState(end + timedelta(seconds=10), "host", None, unit,
                            "system", "active", "running"),
    ]
    monkeypatch.setattr(machine, "service_states", lambda **_: iter(rows))

    spans = [
        span for span in timeline._service_spans(_profile(), start, end)
        if span.summary == unit
    ]

    assert all(start <= span.start < span.end <= end for span in spans)
    assert [(span.kind, span.start, span.end) for span in spans] == [
        ("inactive", start + timedelta(seconds=20), start + timedelta(seconds=50)),
        ("unobserved", start + timedelta(seconds=50), end),
    ]
