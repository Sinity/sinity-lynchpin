from __future__ import annotations

from datetime import date

from lynchpin.sources import reddit


def test_reddit_reads_comments_and_daily_activity(tmp_path, monkeypatch):
    comments = tmp_path / "comments.csv"
    comments.write_text(
        "\n".join(
            [
                "id,date,subreddit,body,permalink,parent,gildings",
                "c1,2026-05-05T12:00:00+00:00,python,hello typed world,/r/python/c1,t3_parent,1",
                "c2,2026-05-05T13:00:00+00:00,python,second comment,/r/python/c2,t3_parent,",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    rows = list(reddit.iter_comments(paths=(comments,)))
    monkeypatch.setattr(reddit, "iter_comments", lambda paths=None: iter(rows))
    monkeypatch.setattr(reddit, "iter_posts", lambda paths=None: iter(()))

    days = reddit.daily_activity(start=date(2026, 5, 5), end=date(2026, 5, 5))
    summary = reddit.summarize_activity("2026-05", "2026-05", comments_paths=(comments,))

    assert [row.id for row in rows] == ["c1", "c2"]
    assert summary.comment_counts == {"2026-05": 2}
    assert summary.comment_subreddits["2026-05"]["python"] == 2
    assert days[0].comment_count == 2
    assert days[0].top_subreddits == ("python",)


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
