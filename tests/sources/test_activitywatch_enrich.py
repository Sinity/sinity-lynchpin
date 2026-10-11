"""Regression test for polylogue project enrichment of focus spans.

`FocusSpan` is a frozen dataclass. The enrichment path used to mutate it in
place (`span.project = ...`), which raises `FrozenInstanceError` at runtime the
moment a span resolves to a project — silently crashing `focus_spans`. The fix
rebuilds the span via `dataclasses.replace`. This test pins that behavior.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from lynchpin.sources import activitywatch
from lynchpin.sources import polylogue, window_session_attribution
from lynchpin.sources import polylogue_session_attribution
from lynchpin.sources.activitywatch_models import FocusSpan
from lynchpin.sources.window_session_attribution import SpanAttribution


def _span(project: str | None = None) -> FocusSpan:
    start = datetime(2026, 4, 21, 10, 0, tzinfo=timezone.utc)
    end = datetime(2026, 4, 21, 10, 30, tzinfo=timezone.utc)
    return FocusSpan(
        start=start, end=end, kind="focused",
        app="kitty", title="codex resume --last", mode="code", project=project,
    )


def test_session_overlap_unavailability_is_scoped_to_operation(monkeypatch, tmp_path, caplog):
    from lynchpin.core.input_snapshot import input_snapshot
    from lynchpin.core.errors import SourceUnavailableError

    path = tmp_path / "index.db"
    path.touch()
    config = SimpleNamespace(polylogue_db=path)
    monkeypatch.setattr("lynchpin.core.config.get_config", lambda: config)
    monkeypatch.setattr(activitywatch, "get_config", lambda: config)
    activitywatch._session_overlap_context.cache_clear()
    calls = 0
    unavailable = True
    span = _span()

    def read(_path):
        nonlocal calls
        calls += 1
        if unavailable:
            raise SourceUnavailableError("polylogue", path=_path, reason="synthetic unavailable")
        return (polylogue_session_attribution.SessionRepoInterval(
            session_id="synthetic", project="fixture", start=span.start, end=span.end,
        ),)

    monkeypatch.setattr(polylogue_session_attribution, "session_repo_intervals", read)
    with input_snapshot():
        for _ in range(3):
            assert activitywatch._enrich_with_session_overlap([span], [0])[0] is span
        assert calls == 1
        assert sum("session overlap unavailable" in r.message for r in caplog.records) == 1
        path.write_bytes(b"new revision")
        assert activitywatch._enrich_with_session_overlap([span], [0])[0] is span
        assert calls == 2

    # Unchanged input identity must not poison recovery in the next operation.
    unavailable = False
    with input_snapshot():
        assert activitywatch._enrich_with_session_overlap([span], [0])[0].project == "fixture"
        assert calls == 3
    unavailable = True
    for _ in range(2):
        assert activitywatch._enrich_with_session_overlap([span], [0])[0] is span
    assert calls == 5


def test_enrich_with_polylogue_assigns_project_without_mutating_frozen_span(monkeypatch):
    activitywatch._polylogue_attribution_context.cache_clear()
    span = _span(project=None)
    spans = [span]
    start = datetime(2026, 4, 21, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 4, 21, 23, 59, tzinfo=timezone.utc)

    # Non-empty work_events so enrichment proceeds; content is irrelevant
    # because attribute_spans is stubbed below.
    monkeypatch.setattr(polylogue, "work_events", lambda *, start, end: [object()])
    monkeypatch.setattr(
        window_session_attribution, "attribute_spans",
        lambda spans, events, **kw: [
            SpanAttribution(conversation_id="c1", overlap_s=600.0, confidence=0.9)
        ],
    )

    class _Profile:
        conversation_id = "c1"
        work_event_projects = ("sinity-lynchpin",)

    monkeypatch.setattr(
        polylogue, "session_profiles_for_date", lambda *, start, end: [_Profile()]
    )

    result = activitywatch._enrich_with_polylogue(spans, start, end)

    # Project resolved on the returned span...
    assert result[0].project == "sinity-lynchpin"
    # ...via replacement, leaving the original frozen instance untouched.
    assert span.project is None
    assert result[0] is not span


def test_enrich_with_polylogue_caches_unavailable_products(monkeypatch):
    activitywatch._polylogue_attribution_context.cache_clear()
    span = _span(project=None)
    start = datetime(2026, 4, 21, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 4, 21, 23, 59, tzinfo=timezone.utc)
    calls = 0

    def fail_work_events(*, start, end):
        nonlocal calls
        calls += 1
        raise RuntimeError("session insights unavailable")

    monkeypatch.setattr(polylogue, "work_events", fail_work_events)

    assert activitywatch._enrich_with_polylogue([span], start, end)[0] is span
    assert activitywatch._enrich_with_polylogue([span], start, end)[0] is span
    assert calls == 1


def test_enrich_falls_back_to_session_overlap_when_work_events_unavailable(monkeypatch, tmp_path):
    """Fallback tier fires when the primary (work_events) tier no-ops entirely."""
    activitywatch._polylogue_attribution_context.cache_clear()
    span = _span(project=None)
    start = datetime(2026, 4, 21, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 4, 21, 23, 59, tzinfo=timezone.utc)

    monkeypatch.setattr(
        polylogue, "work_events",
        lambda *, start, end: (_ for _ in ()).throw(RuntimeError("insight products missing")),
    )

    db_path = tmp_path / "index.db"
    db_path.touch()
    fake_config = type("Config", (), {"polylogue_db": db_path})()
    monkeypatch.setattr(
        "lynchpin.core.config.get_config", lambda: fake_config
    )
    polylogue_session_attribution._session_repo_intervals_cached.cache_clear()
    monkeypatch.setattr(
        polylogue_session_attribution, "_polylogue_client",
        lambda: SimpleNamespace(list_summaries=lambda *, limit: [SimpleNamespace(
            id="s1", working_directories=("/realm/project/sinity-lynchpin",),
            created_at=start.replace(hour=9), updated_at=start.replace(hour=11),
        )]),
    )

    result = activitywatch._enrich_with_polylogue([span], start, end)

    assert result[0].project == "sinity-lynchpin"
    assert span.project is None
    assert result[0] is not span


def test_focus_span_is_frozen():
    """Guards the invariant that makes the in-place mutation a bug."""
    with pytest.raises(FrozenInstanceError):
        _span().project = "x"  # type: ignore[misc]
