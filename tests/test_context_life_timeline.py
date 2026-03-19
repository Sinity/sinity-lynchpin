from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from lynchpin.retrospective.life_timeline import (
    build_month_summary,
    build_intake_summary,
    build_location_summary,
    build_mail_summary,
    build_money_summary,
    build_notes_summary,
    build_output_summary,
    build_health_summary,
    build_recent_trajectory_summaries,
    render_markdown,
    build_work_summary,
)


def test_build_work_summary_returns_typed_month_payload() -> None:
    summary = build_work_summary(
        "2026-03",
        git_commit_counts={"2026-03": 7},
        git_commit_repos={"2026-03": Counter({"sinity-lynchpin": 5, "sinex": 2})},
    )

    assert summary.git_commits == 7
    assert summary.git_top_repos == [("sinity-lynchpin", 5), ("sinex", 2)]


def test_build_health_summary_returns_typed_month_payload() -> None:
    summary = build_health_summary(
        "2026-03",
        sleep_sessions={"2026-03": 3},
        sleep_total_hours={"2026-03": 21.0},
        weights=[81.0, 80.5, 80.0],
    )

    assert summary.sleep_sessions == 3
    assert summary.sleep_total_h == 21.0
    assert summary.sleep_avg_h == 7.0
    assert summary.weight_n == 3
    assert summary.weight_min == 80.0
    assert summary.weight_max == 81.0


def test_build_recent_trajectory_summaries_ignores_old_months_without_window() -> None:
    summaries, window = build_recent_trajectory_summaries(
        ["2020-01"],
        now=datetime(2026, 3, 16, 12, 0, tzinfo=timezone.utc),
    )

    assert summaries == {}
    assert window["month_count"] == 0
    assert window["lookback_days"] > 0


def test_build_output_and_notes_summaries_return_typed_payloads() -> None:
    output = build_output_summary(
        "2026-03",
        reddit_comment_counts={"2026-03": 5},
        reddit_post_counts={"2026-03": 2},
        reddit_message_counts={"2026-03": 1},
        wykop_link_counts={"2026-03": 3},
        wykop_entry_counts={"2026-03": 4},
        wykop_entry_comment_counts={"2026-03": 6},
        reddit_comment_subs={"2026-03": Counter({"python": 4})},
        wykop_link_tags={"2026-03": Counter({"ai": 2})},
        wykop_entry_tags={"2026-03": Counter({"notes": 3})},
        topic_tokens=Counter({"agent": 7, "trajectory": 4}),
    )
    notes = build_notes_summary(
        "2026-03",
        onenote_counts={"2026-03": 8},
        substance_headings={"2026-03": 11},
    )

    assert output.reddit_comments == 5
    assert output.output_top_topic_tokens[0] == ("agent", 7)
    assert notes.onenote_journal_entries == 8
    assert notes.substance_log_headings == 11


def test_build_intake_mail_location_and_money_summaries_return_typed_payloads() -> None:
    intake = build_intake_summary(
        "2026-03",
        web_counts={"2026-03": 9},
        web_domains={"2026-03": Counter({"example.com": 3})},
        web_reddit_subs={"2026-03": Counter({"python": 2})},
        web_title_tokens={"2026-03": Counter({"signal": 4})},
        raindrop_counts={"2026-03": 2},
        goodreads_read_counts={"2026-03": 1},
        goodreads_added_counts={"2026-03": 5},
        goodreads_authors_read={"2026-03": Counter({"Le Guin": 1})},
        goodreads_titles_read={"2026-03": Counter({"A Wizard of Earthsea": 1})},
        google_search_counts={"2026-03": 7},
        google_search_tokens={"2026-03": Counter({"duckdb": 3})},
        google_search_phrases={"2026-03": Counter({"duckdb json": 2})},
        youtube_watch_counts={"2026-03": 4},
        youtube_search_counts={"2026-03": 6},
        youtube_search_tokens={"2026-03": Counter({"rust": 2})},
        youtube_search_phrases={"2026-03": Counter({"rust traits": 2})},
        youtube_watch_history_counts={"2026-03": 8},
        yt_watch_history_video_id_top=[("abc123xyz00", 2)],
        yt_watch_history_channels=Counter({"Some Channel": 2}),
        yt_watch_history_tokens=Counter({"compiler": 2}),
        yt_watch_history_titles=Counter({"How compilers work": 2}),
        youtube_search_history_counts={"2026-03": 3},
        youtube_search_history_tokens={"2026-03": Counter({"nix": 2})},
        youtube_search_history_phrases={"2026-03": Counter({"nix flakes": 2})},
        chrome_counts={"2026-03": 12},
        chrome_history_counts={"2026-03": 10},
        chrome_history_domains={"2026-03": Counter({"github.com": 4})},
        chrome_history_reddit_subs={"2026-03": Counter({"programming": 2})},
        chrome_history_title_tokens={"2026-03": Counter({"codex": 2})},
        maps_counts={"2026-03": 1},
        maps_tokens={"2026-03": Counter({"clinic": 1})},
        maps_phrases={"2026-03": Counter({"clinic near me": 1})},
        image_search_counts={"2026-03": 1},
        image_search_tokens={"2026-03": Counter({"diagram": 1})},
        image_search_phrases={"2026-03": Counter({"duckdb diagram": 1})},
        play_store_counts={"2026-03": 0},
        play_store_tokens={"2026-03": Counter()},
        play_store_phrases={"2026-03": Counter()},
        video_search_counts={"2026-03": 1},
        video_search_tokens={"2026-03": Counter({"timeline": 1})},
        video_search_phrases={"2026-03": Counter({"timeline app": 1})},
        shopping_counts={"2026-03": 1},
        shopping_tokens={"2026-03": Counter({"ssd": 1})},
        shopping_phrases={"2026-03": Counter({"ssd drive": 1})},
        travel_counts={"2026-03": 0},
        travel_tokens={"2026-03": Counter()},
        travel_phrases={"2026-03": Counter()},
        myactivity_other=Counter({"other": 2}),
        spotify_hours={"2026-03": 4.25},
        spotify_top_artists=["Autechre"],
        spotify_top_tracks=["Gantz Graf"],
        intake_topic_tokens=Counter({"trajectory": 5}),
    )
    mail = build_mail_summary(
        "2026-03",
        gmail_counts={"2026-03": 12},
        gmail_from_domains={"2026-03": Counter({"github.com": 4})},
        gmail_subject_tokens={"2026-03": Counter({"build": 3})},
    )
    location = build_location_summary(
        "2026-03",
        location_records={"2026-03": 6},
        semantic_place_visits={"2026-03": 2},
        semantic_activity_segments={"2026-03": 1},
        semantic_top_places={"2026-03": Counter({"home": 2})},
        semantic_top_activities={"2026-03": Counter({"walking": 1})},
    )
    money = build_money_summary(
        "2026-03",
        ledger_expenses={"2026-03": 123.45},
        revolut_out_annotated={"2026-03": 10.0},
        revolut_out_recent={"2026-03": 5.5},
        revolut_in_annotated={"2026-03": 20.0},
        revolut_in_recent={"2026-03": 1.5},
        mbank_personal_out={"2026-03": 30.0},
        mbank_personal_in={"2026-03": 40.0},
        mbank_business_out={"2026-03": 50.0},
        mbank_business_in={"2026-03": 60.0},
    )

    assert intake.webhistory_events == 9
    assert intake.spotify_hours == 4.2
    assert mail.gmail_messages == 12
    assert location.records == 6
    assert money.revolut_out_pln == 15.5


def test_build_month_summary_and_render_markdown_use_context_contract() -> None:
    month = build_month_summary(
        output=build_output_summary(
            "2026-03",
            reddit_comment_counts={"2026-03": 5},
            reddit_post_counts={"2026-03": 2},
            reddit_message_counts={"2026-03": 1},
            wykop_link_counts={"2026-03": 3},
            wykop_entry_counts={"2026-03": 4},
            wykop_entry_comment_counts={"2026-03": 6},
            reddit_comment_subs={"2026-03": Counter({"python": 4})},
            wykop_link_tags={"2026-03": Counter({"ai": 2})},
            wykop_entry_tags={"2026-03": Counter({"notes": 3})},
            topic_tokens=Counter({"agent": 7}),
        ),
        work=build_work_summary(
            "2026-03",
            git_commit_counts={"2026-03": 7},
            git_commit_repos={"2026-03": Counter({"sinity-lynchpin": 5})},
        ),
        intake=build_intake_summary(
            "2026-03",
            web_counts={"2026-03": 9},
            web_domains={"2026-03": Counter({"example.com": 3})},
            web_reddit_subs={"2026-03": Counter({"python": 2})},
            web_title_tokens={"2026-03": Counter({"signal": 4})},
            raindrop_counts={"2026-03": 2},
            goodreads_read_counts={"2026-03": 1},
            goodreads_added_counts={"2026-03": 5},
            goodreads_authors_read={"2026-03": Counter({"Le Guin": 1})},
            goodreads_titles_read={"2026-03": Counter({"A Wizard of Earthsea": 1})},
            google_search_counts={"2026-03": 7},
            google_search_tokens={"2026-03": Counter({"duckdb": 3})},
            google_search_phrases={"2026-03": Counter({"duckdb json": 2})},
            youtube_watch_counts={"2026-03": 4},
            youtube_search_counts={"2026-03": 6},
            youtube_search_tokens={"2026-03": Counter({"rust": 2})},
            youtube_search_phrases={"2026-03": Counter({"rust traits": 2})},
            youtube_watch_history_counts={"2026-03": 8},
            yt_watch_history_video_id_top=[("abc123xyz00", 2)],
            yt_watch_history_channels=Counter({"Some Channel": 2}),
            yt_watch_history_tokens=Counter({"compiler": 2}),
            yt_watch_history_titles=Counter({"How compilers work": 2}),
            youtube_search_history_counts={"2026-03": 3},
            youtube_search_history_tokens={"2026-03": Counter({"nix": 2})},
            youtube_search_history_phrases={"2026-03": Counter({"nix flakes": 2})},
            chrome_counts={"2026-03": 12},
            chrome_history_counts={"2026-03": 10},
            chrome_history_domains={"2026-03": Counter({"github.com": 4})},
            chrome_history_reddit_subs={"2026-03": Counter({"programming": 2})},
            chrome_history_title_tokens={"2026-03": Counter({"codex": 2})},
            maps_counts={"2026-03": 1},
            maps_tokens={"2026-03": Counter({"clinic": 1})},
            maps_phrases={"2026-03": Counter({"clinic near me": 1})},
            image_search_counts={"2026-03": 1},
            image_search_tokens={"2026-03": Counter({"diagram": 1})},
            image_search_phrases={"2026-03": Counter({"duckdb diagram": 1})},
            play_store_counts={"2026-03": 0},
            play_store_tokens={"2026-03": Counter()},
            play_store_phrases={"2026-03": Counter()},
            video_search_counts={"2026-03": 1},
            video_search_tokens={"2026-03": Counter({"timeline": 1})},
            video_search_phrases={"2026-03": Counter({"timeline app": 1})},
            shopping_counts={"2026-03": 1},
            shopping_tokens={"2026-03": Counter({"ssd": 1})},
            shopping_phrases={"2026-03": Counter({"ssd drive": 1})},
            travel_counts={"2026-03": 0},
            travel_tokens={"2026-03": Counter()},
            travel_phrases={"2026-03": Counter()},
            myactivity_other=Counter({"other": 2}),
            spotify_hours={"2026-03": 4.25},
            spotify_top_artists=["Autechre"],
            spotify_top_tracks=["Gantz Graf"],
            intake_topic_tokens=Counter({"trajectory": 5}),
        ),
        mail=build_mail_summary(
            "2026-03",
            gmail_counts={"2026-03": 12},
            gmail_from_domains={"2026-03": Counter({"github.com": 4})},
            gmail_subject_tokens={"2026-03": Counter({"build": 3})},
        ),
        location=build_location_summary(
            "2026-03",
            location_records={"2026-03": 6},
            semantic_place_visits={"2026-03": 2},
            semantic_activity_segments={"2026-03": 1},
            semantic_top_places={"2026-03": Counter({"home": 2})},
            semantic_top_activities={"2026-03": Counter({"walking": 1})},
        ),
        money=build_money_summary(
            "2026-03",
            ledger_expenses={"2026-03": 123.45},
            revolut_out_annotated={"2026-03": 10.0},
            revolut_out_recent={"2026-03": 5.5},
            revolut_in_annotated={"2026-03": 20.0},
            revolut_in_recent={"2026-03": 1.5},
            mbank_personal_out={"2026-03": 30.0},
            mbank_personal_in={"2026-03": 40.0},
            mbank_business_out={"2026-03": 50.0},
            mbank_business_in={"2026-03": 60.0},
        ),
        health=build_health_summary(
            "2026-03",
            sleep_sessions={"2026-03": 3},
            sleep_total_hours={"2026-03": 21.0},
            weights=[81.0, 80.5, 80.0],
        ),
        notes=build_notes_summary(
            "2026-03",
            onenote_counts={"2026-03": 8},
            substance_headings={"2026-03": 11},
        ),
        trajectory=None,
    )

    payload = {
        "generated_at": "2026-03-16T12:00:00Z",
        "range": {"start_month": "2026-03", "end_month": "2026-03"},
        "output_path": "artefacts/lifelog/life-timeline/monthly_life_latest.json",
        "months": {"2026-03": month.to_dict()},
    }
    markdown = render_markdown(payload)

    assert month.to_dict()["work"]["git_commits"] == 7
    assert "## 2026-03" in markdown
    assert "git commits 7" in markdown
    assert "Goodreads read 1, added 5" in markdown
