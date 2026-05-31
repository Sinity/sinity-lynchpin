"""Tests for daily web-category aggregation.

Both the web source iterator and the domain classifier are monkeypatched so no
real browser data or LLM call is touched.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from lynchpin.analysis import web_category_daily as wcd
from lynchpin.analysis.web_category_daily import (
    WebCategoryDay,
    daily_web_categories,
)
from lynchpin.sources.web_categories import DomainCategory
from lynchpin.sources.web_models import WebHistoryVisit


def _visit(ts: datetime, url: str) -> WebHistoryVisit:
    return WebHistoryVisit(timestamp=ts, url=url, title="", source="test")


def _patch(monkeypatch, visits, classes):
    monkeypatch.setattr(
        wcd, "_iter_all_visits", lambda *, start, end: iter(visits)
    )
    monkeypatch.setattr(
        wcd,
        "classify_domains",
        lambda domains: {d: classes[d] for d in domains if d in classes},
    )


def _dc(category, nsfw=False, ct="general"):
    return DomainCategory(domain="x", category=category, nsfw=nsfw, content_type=ct)


def test_empty_history_yields_no_rows(monkeypatch):
    _patch(monkeypatch, [], {})
    out = daily_web_categories(start=date(2026, 1, 1), end=date(2026, 1, 31))
    assert out == []


def test_missing_is_not_zero(monkeypatch):
    # Only Jan 10 has a visit; we don't get rows for other days in the range.
    visits = [_visit(datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc), "https://github.com/x")]
    _patch(monkeypatch, visits, {"github.com": _dc("dev")})
    out = daily_web_categories(start=date(2026, 1, 1), end=date(2026, 1, 31))
    assert len(out) == 1
    assert out[0].date == date(2026, 1, 10)


def test_per_category_counts_and_nsfw_share(monkeypatch):
    base = datetime(2026, 2, 1, 12, 0, tzinfo=timezone.utc)
    visits = [
        _visit(base, "https://github.com/a"),
        _visit(base.replace(minute=5), "https://reddit.com/r/x"),
        _visit(base.replace(minute=10), "https://adult.tld/v"),
        _visit(base.replace(minute=15), "https://github.com/b"),
    ]
    classes = {
        "github.com": _dc("dev"),
        "reddit.com": _dc("social"),
        "adult.tld": _dc("adult", nsfw=True, ct="video"),
    }
    _patch(monkeypatch, visits, classes)
    out = daily_web_categories(start=date(2026, 2, 1), end=date(2026, 2, 1))
    assert len(out) == 1
    day = out[0]
    assert day.total_visits == 4
    assert day.visits_by_category == {"dev": 2, "social": 1, "adult": 1}
    assert day.nsfw_visits == 1
    assert day.nsfw_visit_share == pytest.approx(0.25)
    # distraction = social + adult = 2 of 4
    assert day.distraction_ratio == pytest.approx(0.5)


def test_estimated_minutes_capped(monkeypatch):
    base = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
    # Two visits 10 minutes apart, then a final visit (no dwell).
    visits = [
        _visit(base, "https://github.com/a"),
        _visit(base.replace(minute=10), "https://github.com/b"),
    ]
    _patch(monkeypatch, visits, {"github.com": _dc("dev")})
    out = daily_web_categories(start=date(2026, 3, 1), end=date(2026, 3, 1))
    # First visit dwell = 10 min; last visit dwell = 0 (no next on day).
    assert out[0].minutes_by_category["dev"] == pytest.approx(10.0)


def test_dwell_does_not_cross_day_boundary(monkeypatch):
    # A visit late on logical-day D and the next on day D+1: no cross-day dwell.
    v1 = _visit(datetime(2026, 3, 1, 23, 0, tzinfo=timezone.utc), "https://github.com/a")
    v2 = _visit(datetime(2026, 3, 3, 12, 0, tzinfo=timezone.utc), "https://github.com/b")
    _patch(monkeypatch, [v1, v2], {"github.com": _dc("dev")})
    out = daily_web_categories(start=date(2026, 3, 1), end=date(2026, 3, 3))
    assert len(out) == 2
    # First day's single visit gets 0 dwell because next visit is a different day.
    assert out[0].total_minutes == 0.0


def test_dwell_capped_at_max(monkeypatch):
    base = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)
    # 2 hours apart -> capped at MAX_DWELL_MINUTES.
    visits = [
        _visit(base, "https://github.com/a"),
        _visit(base.replace(hour=14), "https://github.com/b"),
    ]
    _patch(monkeypatch, visits, {"github.com": _dc("dev")})
    out = daily_web_categories(start=date(2026, 4, 1), end=date(2026, 4, 1))
    assert out[0].minutes_by_category["dev"] == pytest.approx(wcd.MAX_DWELL_MINUTES)


def test_unclassified_domain_falls_back_to_other(monkeypatch):
    base = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    visits = [_visit(base, "https://unknown.tld/x")]
    _patch(monkeypatch, visits, {})  # classify returns nothing
    out = daily_web_categories(start=date(2026, 5, 1), end=date(2026, 5, 1))
    assert out[0].visits_by_category == {"other": 1}


def test_web_category_day_shape():
    day = WebCategoryDay(
        date=date(2026, 1, 1),
        total_visits=1,
        total_minutes=1.0,
        visits_by_category={"dev": 1},
        minutes_by_category={"dev": 1.0},
        nsfw_visits=0,
        nsfw_visit_share=0.0,
        distraction_ratio=0.0,
    )
    assert day.total_visits == 1
