from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

from lynchpin.sources import reddit


def test_reddit_reads_comments_and_daily_activity(tmp_path, monkeypatch):
    comments = tmp_path / "comments.csv"
    comments.write_text(
        "\n".join(
            [
                "id,date,subreddit,body,permalink,parent,gildings",
                "c1,2026-05-05T12:00:00+00:00,python,hello typed world,/r/python/c1,t3_parent,1",
                "c2,2026-05-05T13:00:00+00:00,python,second comment,/r/python/c2,t3_parent,",
                "c3,2026-05-06T12:00:00+00:00,rust,exclusive end,/r/rust/c3,t3_parent,",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    rows = list(reddit.iter_comments(paths=(comments,)))
    def fake_comments(paths=None, **kwargs):
        start = kwargs.get("start")
        end = kwargs.get("end")
        for row in rows:
            if row.created is not None and start is not None and row.created.date() < start:
                continue
            if row.created is not None and end is not None and row.created.date() >= end:
                continue
            yield row

    monkeypatch.setattr(reddit, "iter_comments", fake_comments)
    monkeypatch.setattr(reddit, "iter_posts", lambda paths=None, **kwargs: iter(()))

    days = reddit.daily_activity(start=date(2026, 5, 5), end=date(2026, 5, 6))
    distribution = reddit.subreddit_distribution(start=date(2026, 5, 5), end=date(2026, 5, 6))
    summary = reddit.summarize_activity("2026-05", "2026-05", comments_paths=(comments,))

    assert [row.id for row in rows] == ["c1", "c2", "c3"]
    assert summary.comment_counts == {"2026-05": 3}
    assert summary.comment_subreddits["2026-05"]["python"] == 2
    assert days[0].comment_count == 2
    assert days[0].top_subreddits == ("python",)
    assert distribution == [("python", 2, 100.0)]


def test_reddit_default_comment_reader_materializes(monkeypatch, tmp_path):
    calls = []
    comments = tmp_path / "reddit/processed/canonical/comments.csv"
    comments.parent.mkdir(parents=True)
    comments.write_text(
        "\n".join(
            [
                "id,date,subreddit,body,permalink,parent,gildings",
                "c1,2026-05-05T12:00:00+00:00,python,hello,/r/python/c1,t3_parent,1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(reddit, "get_config", lambda: SimpleNamespace(exports_root=tmp_path))
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, window=None: calls.append((name, window)),
    )

    rows = list(reddit.iter_comments())

    assert calls == [("reddit", None)]
    assert [row.id for row in rows] == ["c1"]


def test_reddit_daily_uses_single_windowed_materialization(monkeypatch):
    calls = []
    comment = reddit.RedditComment(
        id="c1",
        created=datetime(2026, 5, 5, 12, tzinfo=timezone.utc),
        subreddit="python",
        body="hello",
        permalink="",
        parent="",
        gildings=None,
        source="fixture",
    )

    def fake_ensure(name, *, window=None):
        calls.append((name, window))

    def fake_comments(*, ensure=True, paths=None, start=None, end=None):
        assert ensure is False
        assert paths is None
        assert start == date(2026, 5, 5)
        assert end == date(2026, 5, 6)
        yield comment

    def fake_posts(*, ensure=True, paths=None, start=None, end=None):
        assert ensure is False
        assert paths is None
        assert start == date(2026, 5, 5)
        assert end == date(2026, 5, 6)
        yield from ()

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure)
    monkeypatch.setattr(reddit, "iter_comments", fake_comments)
    monkeypatch.setattr(reddit, "iter_posts", fake_posts)

    rows = reddit.daily_activity(start=date(2026, 5, 5), end=date(2026, 5, 6))

    assert calls == [("reddit", (date(2026, 5, 5), date(2026, 5, 6)))]
    assert rows[0].comment_count == 1


def test_reddit_iterators_filter_half_open_logical_date_window(monkeypatch):
    comments = [
        reddit.RedditComment(
            id="old",
            created=datetime(2026, 5, 4, 12, tzinfo=timezone.utc),
            subreddit="python",
            body="old",
            permalink="",
            parent="",
            gildings=None,
            source="fixture",
        ),
        reddit.RedditComment(
            id="kept",
            created=datetime(2026, 5, 5, 12, tzinfo=timezone.utc),
            subreddit="python",
            body="kept",
            permalink="",
            parent="",
            gildings=None,
            source="fixture",
        ),
        reddit.RedditComment(
            id="future",
            created=datetime(2026, 5, 6, 12, tzinfo=timezone.utc),
            subreddit="python",
            body="future",
            permalink="",
            parent="",
            gildings=None,
            source="fixture",
        ),
    ]
    monkeypatch.setattr(reddit, "_load_comments", lambda paths=None: comments)

    rows = list(reddit.iter_comments(start=date(2026, 5, 5), end=date(2026, 5, 6), ensure=False))

    assert [row.id for row in rows] == ["kept"]


def test_split_quoted_text_extracts_blockquotes():
    own, quotes = reddit.split_quoted_text(
        "> they said something\n> on two lines\n\nMy response here.\n\n> another quote\n\nMore reply."
    )
    assert quotes == ("they said something\non two lines", "another quote")
    # The non-quote lines are preserved verbatim including blank-line spacing
    # the quotes used to occupy; only leading/trailing whitespace is stripped.
    assert own == "My response here.\n\n\nMore reply."


def test_split_quoted_text_empty_body():
    assert reddit.split_quoted_text("") == ("", ())
    assert reddit.split_quoted_text("no quotes here") == ("no quotes here", ())


def test_split_quoted_text_strips_marker_only_lines():
    # ">" alone as a paragraph separator inside quotes — common reddit pattern.
    own, quotes = reddit.split_quoted_text("> first paragraph\n>\n> second paragraph\n\nmy reply")
    assert quotes == ("first paragraph\n\nsecond paragraph",)
    assert own == "my reply"


def test_split_quoted_text_handles_nested_quotes():
    # Reddit allows >> for nested quotes; collapsed since the level distinction
    # rarely matters for operator-vs-extrinsic separation.
    own, quotes = reddit.split_quoted_text(">> deeper\n> outer\n\nresponse")
    assert quotes == ("deeper\nouter",)
    assert own == "response"
