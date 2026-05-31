"""Regression test for polylogue project enrichment of focus spans.

`FocusSpan` is a frozen dataclass. The enrichment path used to mutate it in
place (`span.project = ...`), which raises `FrozenInstanceError` at runtime the
moment a span resolves to a project — silently crashing `focus_spans`. The fix
rebuilds the span via `dataclasses.replace`. This test pins that behavior.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from lynchpin.sources import activitywatch
from lynchpin.sources import polylogue, window_session_attribution
from lynchpin.sources.activitywatch_models import FocusSpan
from lynchpin.sources.window_session_attribution import SpanAttribution


def _span(project: str | None = None) -> FocusSpan:
    start = datetime(2026, 4, 21, 10, 0, tzinfo=timezone.utc)
    end = datetime(2026, 4, 21, 10, 30, tzinfo=timezone.utc)
    return FocusSpan(
        start=start, end=end, kind="focused",
        app="kitty", title="codex resume --last", mode="code", project=project,
    )


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


def test_focus_span_is_frozen():
    """Guards the invariant that makes the in-place mutation a bug."""
    with pytest.raises(FrozenInstanceError):
        _span().project = "x"  # type: ignore[misc]
