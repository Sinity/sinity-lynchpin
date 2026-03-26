from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from types import SimpleNamespace

import lynchpin.retrospective.life_summary as life_summary_module

from lynchpin.retrospective.life_summary import (
    LifeMonthContextSummary,
    build_month_summary,
    build_intake_summary,
    build_location_summary,
    build_mail_summary,
    build_money_summary,
    build_notes_summary,
    build_output_summary,
    build_health_summary,
    build_recent_context_summaries,
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


def test_build_recent_context_summaries_ignores_old_months_without_window() -> None:
    summaries, window = build_recent_context_summaries(
        ["2020-01"],
        now=datetime(2026, 3, 16, 12, 0, tzinfo=timezone.utc),
    )

    assert summaries == {}
    assert window["month_count"] == 0
    assert window["lookback_days"] > 0


def test_build_recent_context_summaries_use_period_reports(monkeypatch) -> None:
    calls: list[tuple[str, str, object, bool]] = []

    def fake_build_period_report(scale, key, *, output_root=None, write_files=True):
        calls.append((scale, key, output_root, write_files))
        return SimpleNamespace(
            payload={
                "period": {"start": "2026-03-01", "end": "2026-03-31"},
                "bundle_ref": "artefacts/context/evidence/2026/2026-03",
                "summary": {
                    "evidence": {
                        "days_with_evidence": 12,
                        "period_days": 31,
                        "query_rows": {"focus_loops": 4, "focus_spans": 18, "polylogue_sessions": 2},
                        "surfaces_present": ["focus_loops", "focus_spans", "polylogue_sessions"],
                    },
                    "delivery": {
                        "active_hours": 42.5,
                        "total_commits": 7,
                        "command_count": 123,
                        "chat_sessions": 5,
                        "top_repos": [("sinity-lynchpin", 5)],
                    },
                    "focus": {
                        "top_modes": [("coding", 600)],
                        "top_projects": [("sinity-lynchpin", 480)],
                        "avg_fragmentation": 0.33,
                    },
                    "chat": {
                        "work_kinds": [("implementation", 3)],
                        "top_session_titles": [("March architecture pass", 1)],
                        "total_cost_usd": 1.25,
                    },
                    "git": {"top_paths": [("lynchpin/context", 200)]},
                    "patterns": {
                        "episode_count": 2,
                        "episode_labels": ["sinity-lynchpin coding", "anomaly cluster"],
                        "anomaly_count": 1,
                        "anomaly_kinds": ["project_attention_shift"],
                    },
                    "circadian": {
                        "recovery_minutes_total": 180.0,
                        "dominant_modes": [("coding", 600)],
                        "dominant_projects": [("sinity-lynchpin", 480)],
                    },
                },
            }
        )

    monkeypatch.setattr(life_summary_module, "build_period_report", fake_build_period_report)
    summaries, window = build_recent_context_summaries(
        ["2026-03"],
        now=datetime(2026, 3, 16, 12, 0, tzinfo=timezone.utc),
    )

    assert calls == [("month", "2026-03", None, False)]
    assert window["source"] == "context-reports"
    assert window["month_count"] == 1
    context = summaries["2026-03"]
    assert context.days == 12
    assert context.active_hours == 42.5
    assert context.recovery_hours == 3.0
    assert context.chain_count == 4
    assert context.signal_count == 18
    assert context.transcript_count == 2
    assert context.chat_work_events == {"implementation": 3}
    assert context.chat_cost_usd == 1.25
    assert context.episode_count == 2
    assert context.episode_labels == ["sinity-lynchpin coding", "anomaly cluster"]
    assert context.anomaly_count == 1
    assert context.anomaly_kinds == ["project_attention_shift"]
    assert context.top_repos == [("sinity-lynchpin", 5)]
    assert context.top_paths == [("lynchpin/context", 200)]
    assert context.top_session_titles == [("March architecture pass", 1)]
    assert context.avg_fragmentation == 0.33
    assert context.evidence_bundle == "artefacts/context/evidence/2026/2026-03"
    assert "Repos: sinity-lynchpin 5" in context.highlights[0]
    assert any("Episodes: 2" in item for item in context.highlights)


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
        context=None,
    )

    payload = {
        "generated_at": "2026-03-16T12:00:00Z",
        "range": {"start_month": "2026-03", "end_month": "2026-03"},
        "output_path": "artefacts/retrospective/life-range/monthly_life_latest.json",
        "months": {"2026-03": month.to_dict()},
    }
    markdown = render_markdown(payload)

    assert month.to_dict()["work"]["git_commits"] == 7
    assert "## 2026-03" in markdown
    assert "git commits 7" in markdown
    assert "Goodreads read 1, added 5" in markdown


def test_render_markdown_includes_evidence_first_recent_context() -> None:
    month = build_month_summary(
        output=build_output_summary(
            "2026-03",
            reddit_comment_counts={},
            reddit_post_counts={},
            reddit_message_counts={},
            wykop_link_counts={},
            wykop_entry_counts={},
            wykop_entry_comment_counts={},
            reddit_comment_subs={},
            wykop_link_tags={},
            wykop_entry_tags={},
            topic_tokens=Counter(),
        ),
        work=build_work_summary("2026-03", git_commit_counts={}, git_commit_repos={}),
        intake=build_intake_summary(
            "2026-03",
            web_counts={},
            web_domains={},
            web_reddit_subs={},
            web_title_tokens={},
            raindrop_counts={},
            goodreads_read_counts={},
            goodreads_added_counts={},
            goodreads_authors_read={},
            goodreads_titles_read={},
            google_search_counts={},
            google_search_tokens={},
            google_search_phrases={},
            youtube_watch_counts={},
            youtube_search_counts={},
            youtube_search_tokens={},
            youtube_search_phrases={},
            youtube_watch_history_counts={},
            yt_watch_history_video_id_top=[],
            yt_watch_history_channels=Counter(),
            yt_watch_history_tokens=Counter(),
            yt_watch_history_titles=Counter(),
            youtube_search_history_counts={},
            youtube_search_history_tokens={},
            youtube_search_history_phrases={},
            chrome_counts={},
            chrome_history_counts={},
            chrome_history_domains={},
            chrome_history_reddit_subs={},
            chrome_history_title_tokens={},
            maps_counts={},
            maps_tokens={},
            maps_phrases={},
            image_search_counts={},
            image_search_tokens={},
            image_search_phrases={},
            play_store_counts={},
            play_store_tokens={},
            play_store_phrases={},
            video_search_counts={},
            video_search_tokens={},
            video_search_phrases={},
            shopping_counts={},
            shopping_tokens={},
            shopping_phrases={},
            travel_counts={},
            travel_tokens={},
            travel_phrases={},
            myactivity_other=Counter(),
            spotify_hours={},
            spotify_top_artists=[],
            spotify_top_tracks=[],
            intake_topic_tokens=Counter(),
        ),
        mail=build_mail_summary("2026-03", gmail_counts={}, gmail_from_domains={}, gmail_subject_tokens={}),
        location=build_location_summary(
            "2026-03",
            location_records={},
            semantic_place_visits={},
            semantic_activity_segments={},
            semantic_top_places={},
            semantic_top_activities={},
        ),
        money=build_money_summary(
            "2026-03",
            ledger_expenses={},
            revolut_out_annotated={},
            revolut_out_recent={},
            revolut_in_annotated={},
            revolut_in_recent={},
            mbank_personal_out={},
            mbank_personal_in={},
            mbank_business_out={},
            mbank_business_in={},
        ),
        health=build_health_summary("2026-03", sleep_sessions={}, sleep_total_hours={}, weights=[]),
        notes=build_notes_summary("2026-03", onenote_counts={}, substance_headings={}),
        context=LifeMonthContextSummary(
            start_date="2026-03-01",
            end_date="2026-03-31",
            days=12,
            active_hours=42.5,
            recovery_hours=3.0,
            chain_count=4,
            signal_count=18,
            command_count=123,
            transcript_count=2,
            commit_count=7,
            dominant_modes=[("coding", 10.0)],
            dominant_projects=[("sinity-lynchpin", 8.0)],
            dominant_topics=[("implementation", 3)],
            source_counts={"focus_spans": 18},
            coverage={"focus_spans": {"present": True, "rows": 18}},
            highlights=["Repos: sinity-lynchpin 5", "Paths: lynchpin/context 200"],
            chat_session_count=5,
            chat_work_events={"implementation": 3},
            chat_cost_usd=1.25,
            episode_count=2,
            episode_labels=["sinity-lynchpin coding", "anomaly cluster"],
            anomaly_count=1,
            anomaly_kinds=["project_attention_shift"],
            top_repos=[("sinity-lynchpin", 5)],
            top_paths=[("lynchpin/context", 200)],
            top_session_titles=[("March architecture pass", 1)],
            avg_fragmentation=0.33,
            evidence_bundle="artefacts/context/evidence/2026/2026-03",
        ),
    )

    markdown = render_markdown(
        {
            "generated_at": "2026-03-16T12:00:00Z",
            "range": {"start_month": "2026-03", "end_month": "2026-03"},
            "months": {"2026-03": month.to_dict()},
        }
    )

    assert "focus loops" in markdown
    assert "Top repos: sinity-lynchpin 5" in markdown
    assert "Session titles: March architecture pass 1" in markdown
    assert "Avg fragmentation: 0.33" in markdown
    assert "Episodes: 2 (sinity-lynchpin coding, anomaly cluster)" in markdown
    assert "Anomalies: 1 (project_attention_shift)" in markdown
    assert "cost: $1.25" in markdown
