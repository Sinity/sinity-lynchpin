from __future__ import annotations

from ...sources.captures import activitywatch

from .core import TableSpec

from .rows_sources_captures import (
    _activitywatch_events,
    _activitywatch_rows,
    _atuin_rows,
    _codex_rows,
    _instrumentation_audio_rows,
    _instrumentation_screen_rows,
    _instrumentation_terminal_event_rows,
    _instrumentation_terminal_session_rows,
    _webhistory_raw_rows,
    _webhistory_rows,
)
from .rows_sources_exports import (
    _chatlog_rows,
    _fbmessenger_messages_rows,
    _fbmessenger_threads_rows,
    _goodreads_rows,
    _health_sleep_rows,
    _health_weight_rows,
    _polylogue_docs_rows,
    _polylogue_runs_rows,
    _polylogue_session_profile_rows,
    _polylogue_session_tag_rows,
    _polylogue_work_event_rows,
    _polylogue_work_thread_rows,
    _raindrop_rows,
    _reddit_comment_rows,
    _reddit_message_rows,
    _reddit_post_rows,
    _reddit_saved_rows,
    _reddit_votes_rows,
    _sleep_entries_rows,
    _sleep_segments_rows,
    _spotify_rows,
    _takeout_archives_rows,
    _wykop_entries_rows,
    _wykop_entry_comments_rows,
    _wykop_link_comments_rows,
)
from .rows_sources_indices import _gitstats_rows, _session_summaries_rows, _sessions_rows
from .rows_sources_libraries import _dendron_rows, _finance_rows, _substack_rows

SOURCE_TABLE_SPECS: dict[str, TableSpec] = {
    "activitywatch_window": TableSpec(
        name="activitywatch_window",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS activitywatch_window ("
            "bucket TEXT, start TIMESTAMP, \"end\" TIMESTAMP, data_json TEXT)"
        ),
        insert_sql="INSERT INTO activitywatch_window VALUES (?, ?, ?, ?)",
        rows=lambda ctx: _activitywatch_rows(
            _activitywatch_events(
                activitywatch.window_events,
                activitywatch.window_events_all,
                ctx,
            ),
            ctx,
        ),
    ),
    "activitywatch_afk": TableSpec(
        name="activitywatch_afk",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS activitywatch_afk ("
            "bucket TEXT, start TIMESTAMP, \"end\" TIMESTAMP, data_json TEXT)"
        ),
        insert_sql="INSERT INTO activitywatch_afk VALUES (?, ?, ?, ?)",
        rows=lambda ctx: _activitywatch_rows(
            _activitywatch_events(
                activitywatch.afk_events,
                activitywatch.afk_events_all,
                ctx,
            ),
            ctx,
        ),
    ),
    "activitywatch_web": TableSpec(
        name="activitywatch_web",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS activitywatch_web ("
            "bucket TEXT, start TIMESTAMP, \"end\" TIMESTAMP, data_json TEXT)"
        ),
        insert_sql="INSERT INTO activitywatch_web VALUES (?, ?, ?, ?)",
        rows=lambda ctx: _activitywatch_rows(
            _activitywatch_events(
                activitywatch.web_events,
                activitywatch.web_events_all,
                ctx,
            ),
            ctx,
        ),
    ),
    "atuin_commands": TableSpec(
        name="atuin_commands",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS atuin_commands ("
            "timestamp TIMESTAMP, duration_ns BIGINT, exit_code INTEGER, cwd TEXT, command TEXT)"
        ),
        insert_sql="INSERT INTO atuin_commands VALUES (?, ?, ?, ?, ?)",
        rows=_atuin_rows,
    ),
    "chatlog_transcripts": TableSpec(
        name="chatlog_transcripts",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS chatlog_transcripts ("
            "provider TEXT, slug TEXT, title TEXT, path TEXT, started_at TIMESTAMP, "
            "tokens BIGINT, words BIGINT, attachment_count BIGINT, attachment_bytes BIGINT)"
        ),
        insert_sql="INSERT INTO chatlog_transcripts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows=_chatlog_rows,
    ),
    "codex_sessions": TableSpec(
        name="codex_sessions",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS codex_sessions (start TIMESTAMP, source TEXT)"
        ),
        insert_sql="INSERT INTO codex_sessions VALUES (?, ?)",
        rows=_codex_rows,
    ),
    "dendron_notes": TableSpec(
        name="dendron_notes",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS dendron_notes ("
            "path TEXT, note_id TEXT, title TEXT, tags_json TEXT, frontmatter_json TEXT, body TEXT)"
        ),
        insert_sql="INSERT INTO dendron_notes VALUES (?, ?, ?, ?, ?, ?)",
        rows=_dendron_rows,
    ),
    "finance_transactions": TableSpec(
        name="finance_transactions",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS finance_transactions ("
            "date DATE, payee TEXT, narration TEXT, posting_index INTEGER, account TEXT, "
            "amount DOUBLE, currency TEXT, cost DOUBLE)"
        ),
        insert_sql="INSERT INTO finance_transactions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows=_finance_rows,
    ),
    "fbmessenger_threads": TableSpec(
        name="fbmessenger_threads",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS fbmessenger_threads ("
            "thread_name TEXT, participants_json TEXT, source TEXT)"
        ),
        insert_sql="INSERT INTO fbmessenger_threads VALUES (?, ?, ?)",
        rows=_fbmessenger_threads_rows,
    ),
    "fbmessenger_messages": TableSpec(
        name="fbmessenger_messages",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS fbmessenger_messages ("
            "thread_name TEXT, participants_json TEXT, sender TEXT, timestamp TIMESTAMP, text TEXT, kind TEXT, "
            "is_unsent BOOLEAN, media_count BIGINT, reaction_count BIGINT, source TEXT)"
        ),
        insert_sql="INSERT INTO fbmessenger_messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows=_fbmessenger_messages_rows,
    ),
    "gitstats_commits": TableSpec(
        name="gitstats_commits",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS gitstats_commits ("
            "date DATE, repo TEXT, commit TEXT, lines_added BIGINT, lines_deleted BIGINT, subject TEXT)"
        ),
        insert_sql="INSERT INTO gitstats_commits VALUES (?, ?, ?, ?, ?, ?)",
        rows=_gitstats_rows,
    ),
    "goodreads_library": TableSpec(
        name="goodreads_library",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS goodreads_library ("
            "book_id TEXT, title TEXT, author TEXT, additional_authors TEXT, date_read TIMESTAMP, "
            "date_added TIMESTAMP, shelves TEXT, exclusive_shelf TEXT, my_rating INTEGER, average_rating DOUBLE, "
            "pages INTEGER, year_published INTEGER, original_year_published INTEGER, publisher TEXT, binding TEXT, "
            "read_count INTEGER, owned_copies INTEGER, source TEXT)"
        ),
        insert_sql=(
            "INSERT INTO goodreads_library VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        ),
        rows=_goodreads_rows,
    ),
    "health_samsung_sleep": TableSpec(
        name="health_samsung_sleep",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS health_samsung_sleep (start_time TIMESTAMP, duration_minutes DOUBLE)"
        ),
        insert_sql="INSERT INTO health_samsung_sleep VALUES (?, ?)",
        rows=_health_sleep_rows,
    ),
    "health_samsung_weight": TableSpec(
        name="health_samsung_weight",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS health_samsung_weight (recorded_at TIMESTAMP, weight DOUBLE)"
        ),
        insert_sql="INSERT INTO health_samsung_weight VALUES (?, ?)",
        rows=_health_weight_rows,
    ),
    "instrumentation_terminal_sessions": TableSpec(
        name="instrumentation_terminal_sessions",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS instrumentation_terminal_sessions ("
            "path TEXT, size_bytes BIGINT, session_id TEXT, schema_generation TEXT, "
            "created_at TIMESTAMP, finished_at TIMESTAMP, duration_seconds DOUBLE, "
            "active_seconds DOUBLE, idle_seconds DOUBLE, command_count INTEGER, event_count INTEGER, "
            "command TEXT, title TEXT, shell TEXT, term_env TEXT, term_type TEXT, "
            "term_cols INTEGER, term_rows INTEGER, host TEXT, user_name TEXT, "
            "terminal TEXT, tty TEXT, start_cwd TEXT, final_cwd TEXT, project_root TEXT, "
            "final_project_root TEXT, repo_root TEXT, final_repo_root TEXT, repo_branch TEXT, "
            "final_repo_branch TEXT, repo_commit TEXT, final_repo_commit TEXT, repo_dirty BOOLEAN, "
            "final_repo_dirty BOOLEAN, exit_code INTEGER, exit_reason TEXT, recorder_exit_code INTEGER, "
            "cleanup_escalated BOOLEAN, manifest_path TEXT, events_path TEXT, has_events BOOLEAN, "
            "timing_source TEXT, quality_status TEXT, quality_flags_json TEXT, field_sources_json TEXT)"
        ),
        insert_sql=(
            "INSERT INTO instrumentation_terminal_sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        ),
        rows=_instrumentation_terminal_session_rows,
    ),
    "instrumentation_terminal_events": TableSpec(
        name="instrumentation_terminal_events",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS instrumentation_terminal_events ("
            "session_id TEXT, cast_path TEXT, schema_generation TEXT, source TEXT, event_time TIMESTAMP, "
            "event_type TEXT, pwd TEXT, project_root TEXT, repo_root TEXT, repo_branch TEXT, "
            "repo_commit TEXT, repo_dirty BOOLEAN, exit_code INTEGER, command_text TEXT, "
            "duration_ms BIGINT, payload_json TEXT)"
        ),
        insert_sql="INSERT INTO instrumentation_terminal_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows=_instrumentation_terminal_event_rows,
    ),
    "instrumentation_audio": TableSpec(
        name="instrumentation_audio",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS instrumentation_audio ("
            "path TEXT, size_bytes BIGINT, sha256 TEXT, created_at TIMESTAMP, duration_seconds DOUBLE, "
            "format TEXT, channels INTEGER, sample_rate INTEGER)"
        ),
        insert_sql="INSERT INTO instrumentation_audio VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows=_instrumentation_audio_rows,
    ),
    "instrumentation_screen": TableSpec(
        name="instrumentation_screen",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS instrumentation_screen ("
            "path TEXT, size_bytes BIGINT, sha256 TEXT, created_at TIMESTAMP, width INTEGER, height INTEGER, "
            "format TEXT)"
        ),
        insert_sql="INSERT INTO instrumentation_screen VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows=_instrumentation_screen_rows,
    ),
    "polylogue_markdown": TableSpec(
        name="polylogue_markdown",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS polylogue_markdown ("
            "provider TEXT, path TEXT, modified_at TIMESTAMP, size_bytes BIGINT)"
        ),
        insert_sql="INSERT INTO polylogue_markdown VALUES (?, ?, ?, ?)",
        rows=_polylogue_docs_rows,
    ),
    "polylogue_runs": TableSpec(
        name="polylogue_runs",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS polylogue_runs ("
            "run_id TEXT, timestamp TIMESTAMP, counts_json TEXT, drift_json TEXT, indexed BOOLEAN, "
            "index_error TEXT, duration_ms BIGINT, path TEXT)"
        ),
        insert_sql="INSERT INTO polylogue_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows=_polylogue_runs_rows,
    ),
    "polylogue_session_profile": TableSpec(
        name="polylogue_session_profile",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS polylogue_session_profile ("
            "conversation_id TEXT, provider TEXT, title TEXT, created_at TIMESTAMP, "
            "message_count BIGINT, substantive_count BIGINT, word_count BIGINT, "
            "cost_usd DOUBLE, cost_is_estimated BOOLEAN, "
            "work_event_count BIGINT, dominant_work_kind TEXT, "
            "phase_count BIGINT, decision_count BIGINT, "
            "repo_paths_json TEXT, canonical_projects_json TEXT, languages_json TEXT, "
            "is_continuation BOOLEAN, continuation_depth BIGINT, thread_id TEXT, "
            "first_message_at TIMESTAMP, last_message_at TIMESTAMP, wall_duration_ms BIGINT, "
            "auto_tags_json TEXT)"
        ),
        insert_sql="INSERT INTO polylogue_session_profile VALUES "
                   "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows=_polylogue_session_profile_rows,
    ),
    "polylogue_work_event": TableSpec(
        name="polylogue_work_event",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS polylogue_work_event ("
            "conversation_id TEXT, provider TEXT, session_date TIMESTAMP, "
            "event_index BIGINT, kind TEXT, confidence DOUBLE, "
            "start_index BIGINT, end_index BIGINT, "
            "summary TEXT, file_paths_json TEXT, tools_used_json TEXT)"
        ),
        insert_sql="INSERT INTO polylogue_work_event VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows=_polylogue_work_event_rows,
    ),
    "polylogue_work_thread": TableSpec(
        name="polylogue_work_thread",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS polylogue_work_thread ("
            "thread_id TEXT, root_id TEXT, session_count BIGINT, depth BIGINT, branch_count BIGINT, "
            "start_time TIMESTAMP, end_time TIMESTAMP, wall_duration_ms BIGINT, "
            "total_messages BIGINT, total_cost_usd DOUBLE, dominant_project TEXT, "
            "work_event_breakdown_json TEXT)"
        ),
        insert_sql="INSERT INTO polylogue_work_thread VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows=_polylogue_work_thread_rows,
    ),
    "polylogue_session_tag": TableSpec(
        name="polylogue_session_tag",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS polylogue_session_tag ("
            "conversation_id TEXT, tag TEXT, source TEXT)"
        ),
        insert_sql="INSERT INTO polylogue_session_tag VALUES (?, ?, ?)",
        rows=_polylogue_session_tag_rows,
    ),
    "raindrop_bookmarks": TableSpec(
        name="raindrop_bookmarks",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS raindrop_bookmarks ("
            "id BIGINT, title TEXT, url TEXT, folder TEXT, tags_json TEXT, created TIMESTAMP, note TEXT, "
            "excerpt TEXT, cover TEXT, favorite BOOLEAN, raw_json TEXT)"
        ),
        insert_sql="INSERT INTO raindrop_bookmarks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows=_raindrop_rows,
    ),
    "reddit_comments": TableSpec(
        name="reddit_comments",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS reddit_comments ("
            "id TEXT, created TIMESTAMP, subreddit TEXT, body TEXT, permalink TEXT, parent TEXT, "
            "gildings BIGINT, source TEXT)"
        ),
        insert_sql="INSERT INTO reddit_comments VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows=_reddit_comment_rows,
    ),
    "reddit_posts": TableSpec(
        name="reddit_posts",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS reddit_posts ("
            "id TEXT, created TIMESTAMP, subreddit TEXT, title TEXT, body TEXT, url TEXT, "
            "gildings BIGINT, source TEXT)"
        ),
        insert_sql="INSERT INTO reddit_posts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows=_reddit_post_rows,
    ),
    "reddit_message_headers": TableSpec(
        name="reddit_message_headers",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS reddit_message_headers ("
            "id TEXT, created TIMESTAMP, thread_id TEXT, sender TEXT, recipient TEXT, permalink TEXT, source TEXT)"
        ),
        insert_sql="INSERT INTO reddit_message_headers VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows=_reddit_message_rows,
    ),
    "reddit_saved": TableSpec(
        name="reddit_saved",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS reddit_saved ("
            "id TEXT, permalink TEXT, kind TEXT, source TEXT)"
        ),
        insert_sql="INSERT INTO reddit_saved VALUES (?, ?, ?, ?)",
        rows=_reddit_saved_rows,
    ),
    "reddit_votes": TableSpec(
        name="reddit_votes",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS reddit_votes ("
            "id TEXT, permalink TEXT, direction BIGINT, kind TEXT, source TEXT)"
        ),
        insert_sql="INSERT INTO reddit_votes VALUES (?, ?, ?, ?, ?)",
        rows=_reddit_votes_rows,
    ),
    "sessions_records": TableSpec(
        name="sessions_records",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS sessions_records ("
            "date DATE, provider TEXT, label TEXT, doc_path TEXT, highlights TEXT)"
        ),
        insert_sql="INSERT INTO sessions_records VALUES (?, ?, ?, ?, ?)",
        rows=_sessions_rows,
    ),
    "session_summaries": TableSpec(
        name="session_summaries",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS session_summaries ("
            "summary_path TEXT, source_path TEXT, provider TEXT, title TEXT, timeframe TEXT, "
            "summary TEXT, generated_at TIMESTAMP, highlight_count BIGINT, decision_count BIGINT, "
            "follow_up_count BIGINT, action_item_count BIGINT, risk_count BIGINT, "
            "highlights_json TEXT, decisions_json TEXT, follow_ups_json TEXT, "
            "action_items_json TEXT, risks_json TEXT, raw_references_json TEXT)"
        ),
        insert_sql=(
            "INSERT INTO session_summaries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        ),
        rows=_session_summaries_rows,
    ),
    "sleep_entries": TableSpec(
        name="sleep_entries",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS sleep_entries ("
            "date TEXT, total_minutes DOUBLE, avg_score DOUBLE, segment_count BIGINT)"
        ),
        insert_sql="INSERT INTO sleep_entries VALUES (?, ?, ?, ?)",
        rows=_sleep_entries_rows,
    ),
    "sleep_segments": TableSpec(
        name="sleep_segments",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS sleep_segments ("
            "start TEXT, \"end\" TEXT, duration_minutes DOUBLE, score DOUBLE, device TEXT, comment TEXT)"
        ),
        insert_sql="INSERT INTO sleep_segments VALUES (?, ?, ?, ?, ?, ?)",
        rows=_sleep_segments_rows,
    ),
    "spotify_streams": TableSpec(
        name="spotify_streams",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS spotify_streams ("
            "end_time TIMESTAMP, artist TEXT, track TEXT, ms_played BIGINT, platform TEXT, context TEXT, source TEXT)"
        ),
        insert_sql="INSERT INTO spotify_streams VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows=_spotify_rows,
    ),
    "substack_posts": TableSpec(
        name="substack_posts",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS substack_posts ("
            "source TEXT, path TEXT, published_at TIMESTAMP, slug TEXT, title TEXT, format TEXT, content TEXT)"
        ),
        insert_sql="INSERT INTO substack_posts VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows=_substack_rows,
    ),
    "takeout_archives": TableSpec(
        name="takeout_archives",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS takeout_archives ("
            "archive_path TEXT, updated_at TIMESTAMP, part_count BIGINT)"
        ),
        insert_sql="INSERT INTO takeout_archives VALUES (?, ?, ?)",
        rows=_takeout_archives_rows,
    ),
    "webhistory_entries": TableSpec(
        name="webhistory_entries",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS webhistory_entries ("
            "url TEXT, title TEXT, visited_at TIMESTAMP, source_file TEXT, payload_json TEXT)"
        ),
        insert_sql="INSERT INTO webhistory_entries VALUES (?, ?, ?, ?, ?)",
        rows=_webhistory_rows,
    ),
    "webhistory_raw_entries": TableSpec(
        name="webhistory_raw_entries",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS webhistory_raw_entries ("
            "timestamp TIMESTAMP, url TEXT, title TEXT, source_file TEXT, payload_json TEXT)"
        ),
        insert_sql="INSERT INTO webhistory_raw_entries VALUES (?, ?, ?, ?, ?)",
        rows=_webhistory_raw_rows,
    ),
    "wykop_entries": TableSpec(
        name="wykop_entries",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS wykop_entries ("
            "id BIGINT, created_at TIMESTAMP, url TEXT, content TEXT, tags_json TEXT, votes_up BIGINT, votes_down BIGINT)"
        ),
        insert_sql="INSERT INTO wykop_entries VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows=_wykop_entries_rows,
    ),
    "wykop_entry_comments": TableSpec(
        name="wykop_entry_comments",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS wykop_entry_comments ("
            "id BIGINT, created_at TIMESTAMP, entry_id BIGINT, url TEXT, content TEXT, rating BIGINT)"
        ),
        insert_sql="INSERT INTO wykop_entry_comments VALUES (?, ?, ?, ?, ?, ?)",
        rows=_wykop_entry_comments_rows,
    ),
    "wykop_link_comments": TableSpec(
        name="wykop_link_comments",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS wykop_link_comments ("
            "id BIGINT, created_at TIMESTAMP, url TEXT, content TEXT, rating BIGINT, link_id BIGINT, "
            "link_title TEXT, link_url TEXT, tags_json TEXT)"
        ),
        insert_sql="INSERT INTO wykop_link_comments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows=_wykop_link_comments_rows,
    ),
}
