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
