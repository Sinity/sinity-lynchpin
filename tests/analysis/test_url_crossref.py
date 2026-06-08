from __future__ import annotations

from datetime import date
from datetime import datetime, timezone
from types import SimpleNamespace

from lynchpin.analysis.url_crossref import (
    URLMention,
    aggregate_by_url,
    cross_referenced_urls,
    extract_urls,
    iter_url_mentions,
)


def test_extract_urls_strips_trailing_punctuation():
    text = "see https://example.com/foo, and https://example.com/bar."
    urls = extract_urls(text)
    assert urls == ("https://example.com/foo", "https://example.com/bar")


def test_extract_urls_keeps_balanced_parens():
    # Wikipedia-style URLs with parens shouldn't have the close-paren stripped.
    urls = extract_urls("link https://en.wikipedia.org/wiki/Foo_(bar) here")
    assert urls == ("https://en.wikipedia.org/wiki/Foo_(bar)",)


def test_extract_urls_strips_unbalanced_close_paren():
    urls = extract_urls("(see https://example.com/foo) for details")
    assert urls == ("https://example.com/foo",)


def test_extract_urls_empty():
    assert extract_urls("") == ()
    assert extract_urls("no urls here just text") == ()


def test_extract_urls_multiple_separated():
    urls = extract_urls("a https://a.com b https://b.com c")
    assert urls == ("https://a.com", "https://b.com")


def _mention(url: str, source: str, role: str, ts: datetime, snippet: str = "") -> URLMention:
    return URLMention(
        url=url,
        raw_url=url,
        domain=url.split("/")[2] if "//" in url else "",
        source=source,
        role=role,  # type: ignore[arg-type]
        timestamp=ts,
        snippet=snippet or None,
        ref_id=None,
    )


def test_aggregate_by_url_sums_and_sorts():
    t1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    t2 = datetime(2026, 2, 1, tzinfo=timezone.utc)
    t3 = datetime(2026, 3, 1, tzinfo=timezone.utc)
    mentions = [
        _mention("https://a.com/x", "irc", "mention", t1),
        _mention("https://a.com/x", "irc", "own", t2),
        _mention("https://a.com/x", "reddit", "own", t3),
        _mention("https://b.com/y", "raindrop", "bookmark", t1),
    ]
    aggs = aggregate_by_url(mentions)
    assert aggs[0].url == "https://a.com/x"
    assert aggs[0].total_mentions == 3
    assert aggs[0].by_source == {"irc": 2, "reddit": 1}
    assert aggs[0].by_role == {"mention": 1, "own": 2}
    assert aggs[0].first_seen == t1
    assert aggs[0].last_seen == t3
    assert aggs[1].url == "https://b.com/y"
    assert aggs[1].total_mentions == 1


def test_cross_referenced_filters_by_source_count():
    t = datetime(2026, 1, 1, tzinfo=timezone.utc)
    mentions = [
        # only irc — single source, should be excluded with min_sources=2
        _mention("https://single.com", "irc", "mention", t),
        _mention("https://single.com", "irc", "mention", t),
        # crossed irc + reddit
        _mention("https://crossed.com", "irc", "mention", t),
        _mention("https://crossed.com", "reddit", "own", t),
    ]
    xref = cross_referenced_urls(mentions, min_sources=2)
    assert {a.url for a in xref} == {"https://crossed.com"}


def test_aggregate_collects_distinct_snippets():
    t = datetime(2026, 1, 1, tzinfo=timezone.utc)
    mentions = [
        _mention("https://a.com", "irc", "mention", t, snippet="first context"),
        _mention("https://a.com", "irc", "mention", t, snippet="second context"),
        _mention("https://a.com", "irc", "mention", t, snippet="first context"),  # dup
        _mention("https://a.com", "irc", "mention", t, snippet="third context"),
        _mention("https://a.com", "irc", "mention", t, snippet="fourth context"),
    ]
    agg = aggregate_by_url(mentions)[0]
    # capped at 3 distinct, no dupes
    assert len(agg.sample_snippets) == 3
    assert "first context" in agg.sample_snippets
    assert "second context" in agg.sample_snippets
    assert "third context" in agg.sample_snippets


def test_aggregate_handles_naive_timestamps():
    # Some sources emit naive timestamps; aggregate should coerce to UTC for
    # comparison instead of crashing on mixed-tz comparisons.
    aware = datetime(2026, 1, 1, tzinfo=timezone.utc)
    naive = datetime(2026, 6, 1)  # no tzinfo
    mentions = [
        _mention("https://a.com", "irc", "mention", aware),
        _mention("https://a.com", "reddit", "own", naive),
    ]
    agg = aggregate_by_url(mentions)[0]
    assert agg.first_seen == aware
    # last_seen should be the naive one coerced to UTC
    assert agg.last_seen is not None
    assert agg.last_seen.month == 6


def test_irc_mentions_use_bounded_reader(monkeypatch):
    calls: list[tuple[date, date]] = []

    def fake_iter_messages_in_range(*, start: date, end: date, **_kwargs):
        calls.append((start, end))
        yield SimpleNamespace(
            timestamp=datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc),
            is_meta=False,
            speaker="sinity",
            text="see https://example.com/path",
            channel="#x",
            line_no=9,
        )

    monkeypatch.setattr("lynchpin.sources.irc_raw.iter_messages_in_range", fake_iter_messages_in_range)

    mentions = list(iter_url_mentions(start=date(2026, 6, 1), end=date(2026, 6, 3), sources={"irc"}))

    assert calls == [(date(2026, 6, 1), date(2026, 6, 3))]
    assert [m.url for m in mentions] == ["https://example.com/path"]


def test_reddit_mentions_use_half_open_bounded_readers(monkeypatch):
    comment_calls: list[tuple[date | None, date | None]] = []
    post_calls: list[tuple[date | None, date | None]] = []

    def fake_comments(*, start: date | None = None, end: date | None = None, **_kwargs):
        comment_calls.append((start, end))
        yield SimpleNamespace(
            created=datetime(2026, 6, 2, 12, tzinfo=timezone.utc),
            body="see https://example.com/comment",
            permalink="/r/demo/comment",
            id="c1",
        )

    def fake_posts(*, start: date | None = None, end: date | None = None, **_kwargs):
        post_calls.append((start, end))
        yield SimpleNamespace(
            created=datetime(2026, 6, 2, 13, tzinfo=timezone.utc),
            url="https://example.com/post",
            title="",
            body="",
            permalink="/r/demo/post",
            id="p1",
        )

    monkeypatch.setattr("lynchpin.sources.reddit.iter_comments", fake_comments)
    monkeypatch.setattr("lynchpin.sources.reddit.iter_posts", fake_posts)

    mentions = list(iter_url_mentions(start=date(2026, 6, 1), end=date(2026, 6, 3), sources={"reddit"}))

    assert comment_calls == [(date(2026, 6, 1), date(2026, 6, 4))]
    assert post_calls == [(date(2026, 6, 1), date(2026, 6, 4))]
    assert {m.url for m in mentions} == {"https://example.com/comment", "https://example.com/post"}


def test_web_mentions_use_source_date_filter(monkeypatch):
    calls: list[tuple[date | None, date | None]] = []

    def fake_iter_entries(start: date | None = None, end: date | None = None, **_kwargs):
        calls.append((start, end))
        yield {
            "url": "https://example.com/visited",
            "iso_time": "2026-06-02T12:00:00+00:00",
            "title": "Visited",
        }

    monkeypatch.setattr("lynchpin.sources.web.iter_entries", fake_iter_entries)

    mentions = list(iter_url_mentions(start=date(2026, 6, 1), end=date(2026, 6, 3), sources={"web"}))

    assert calls == [(date(2026, 6, 1), date(2026, 6, 3))]
    assert [m.url for m in mentions] == ["https://example.com/visited"]


def test_raindrop_mentions_use_half_open_bounded_reader(monkeypatch):
    calls: list[tuple[date | None, date | None]] = []

    def fake_bookmarks(*, start: date | None = None, end: date | None = None, **_kwargs):
        calls.append((start, end))
        yield SimpleNamespace(
            id=1,
            title="Bookmark",
            created=datetime(2026, 6, 2, 12, tzinfo=timezone.utc),
            url="https://example.com/bookmark",
        )

    monkeypatch.setattr("lynchpin.sources.exports.iter_raindrop_bookmarks", fake_bookmarks)

    mentions = list(iter_url_mentions(start=date(2026, 6, 1), end=date(2026, 6, 3), sources={"raindrop"}))

    assert calls == [(date(2026, 6, 1), date(2026, 6, 4))]
    assert [m.url for m in mentions] == ["https://example.com/bookmark"]
