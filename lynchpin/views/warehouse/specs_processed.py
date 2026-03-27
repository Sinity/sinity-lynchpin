from __future__ import annotations

from .core import TableSpec

from .rows_processed_activity import (
    _processed_app_session_rows,
    _processed_deep_work_rows,
    _processed_focus_loop_rows,
    _processed_focus_span_rows,
    _processed_shell_session_rows,
)
from .rows_processed_git import (
    _processed_git_commit_fact_rows,
    _processed_git_daily_rows,
    _processed_git_file_fact_rows,
    _processed_commit_session_rows,
)
from .rows_processed_metrics import (
    _processed_chat_activity_rows,
    _processed_circadian_rows,
    _processed_context_switch_rows,
    _processed_delivery_telemetry_rows,
    _processed_project_attention_rows,
    _processed_sleep_correlation_rows,
)

PROCESSED_TABLE_SPECS: dict[str, TableSpec] = {
    "processed_app_sessions": TableSpec(
        name="processed_app_sessions",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS processed_app_sessions ("
            "date DATE, app TEXT, start TIMESTAMP, end_time TIMESTAMP, duration_seconds DOUBLE, "
            "title_dominant TEXT, title_count BIGINT, mode TEXT, project TEXT, interruptions BIGINT)"
        ),
        insert_sql="INSERT INTO processed_app_sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows=_processed_app_session_rows,
    ),
    "processed_focus_spans": TableSpec(
        name="processed_focus_spans",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS processed_focus_spans ("
            "date DATE, start TIMESTAMP, end_time TIMESTAMP, span_kind TEXT, source_kind TEXT, "
            "app TEXT, title TEXT, mode TEXT, project TEXT, duration_seconds DOUBLE, "
            "keypress_count BIGINT, changed_keypress_count BIGINT, keylog_state TEXT)"
        ),
        insert_sql="INSERT INTO processed_focus_spans VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows=_processed_focus_span_rows,
    ),
    "processed_focus_loops": TableSpec(
        name="processed_focus_loops",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS processed_focus_loops ("
            "date DATE, start TIMESTAMP, end_time TIMESTAMP, duration_minutes DOUBLE, "
            "span_count BIGINT, switch_count BIGINT, cycle_count BIGINT, "
            "context_a_app TEXT, context_a_title TEXT, context_b_app TEXT, context_b_title TEXT, "
            "dominant_project TEXT, dominant_mode TEXT)"
        ),
        insert_sql="INSERT INTO processed_focus_loops VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows=_processed_focus_loop_rows,
    ),
    "processed_shell_sessions": TableSpec(
        name="processed_shell_sessions",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS processed_shell_sessions ("
            "date DATE, cwd TEXT, project TEXT, start TIMESTAMP, end_time TIMESTAMP, "
            "duration_seconds DOUBLE, command_count BIGINT, error_count BIGINT, category TEXT)"
        ),
        insert_sql="INSERT INTO processed_shell_sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows=_processed_shell_session_rows,
    ),
    "processed_git_daily": TableSpec(
        name="processed_git_daily",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS processed_git_daily ("
            "date DATE, repo TEXT, commit_count BIGINT, lines_added BIGINT, lines_deleted BIGINT, "
            "churn BIGINT, net_loc BIGINT, ai_coauthored BIGINT, ai_ratio DOUBLE, "
            "dominant_prefix TEXT, commit_burst_count BIGINT)"
        ),
        insert_sql="INSERT INTO processed_git_daily VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows=_processed_git_daily_rows,
    ),
    "processed_git_commit_facts": TableSpec(
        name="processed_git_commit_facts",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS processed_git_commit_facts ("
            "date DATE, repo TEXT, authored_at TIMESTAMP, commit_sha TEXT, author TEXT, "
            "subject TEXT, lines_added BIGINT, lines_deleted BIGINT, lines_changed BIGINT, "
            "files_changed BIGINT, path_roots_json TEXT, paths_json TEXT)"
        ),
        insert_sql="INSERT INTO processed_git_commit_facts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows=_processed_git_commit_fact_rows,
    ),
    "processed_git_file_facts": TableSpec(
        name="processed_git_file_facts",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS processed_git_file_facts ("
            "date DATE, repo TEXT, authored_at TIMESTAMP, commit_sha TEXT, path TEXT, "
            "path_root TEXT, lines_added BIGINT, lines_deleted BIGINT, lines_changed BIGINT)"
        ),
        insert_sql="INSERT INTO processed_git_file_facts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows=_processed_git_file_fact_rows,
    ),
    "processed_deep_work": TableSpec(
        name="processed_deep_work",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS processed_deep_work ("
            "date DATE, start TIMESTAMP, end_time TIMESTAMP, duration_minutes DOUBLE, "
            "project TEXT, mode TEXT, app_switches BIGINT, git_lines_changed BIGINT, "
            "git_files_changed BIGINT, command_count BIGINT, focus_ratio DOUBLE)"
        ),
        insert_sql="INSERT INTO processed_deep_work VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows=_processed_deep_work_rows,
    ),
    "processed_circadian": TableSpec(
        name="processed_circadian",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS processed_circadian ("
            "date DATE, hour BIGINT, active_minutes DOUBLE, recovery_minutes DOUBLE, "
            "git_lines_changed BIGINT, git_files_changed BIGINT, command_count BIGINT, app_switches BIGINT, "
            "dominant_mode TEXT, dominant_project TEXT)"
        ),
        insert_sql="INSERT INTO processed_circadian VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows=_processed_circadian_rows,
    ),
    "processed_delivery_telemetry": TableSpec(
        name="processed_delivery_telemetry",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS processed_delivery_telemetry ("
            "date DATE, active_hours DOUBLE, total_commits BIGINT, ai_commits BIGINT, "
            "human_commits BIGINT, ai_ratio DOUBLE, commit_density_per_active_hour DOUBLE, "
            "command_count BIGINT, command_density_per_active_hour DOUBLE, "
            "chat_sessions BIGINT, chat_engaged_minutes DOUBLE, "
            "chat_minutes_per_active_hour DOUBLE, repos_json TEXT, ai_models_json TEXT)"
        ),
        insert_sql="INSERT INTO processed_delivery_telemetry VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows=_processed_delivery_telemetry_rows,
    ),
    "processed_context_switches": TableSpec(
        name="processed_context_switches",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS processed_context_switches ("
            "date DATE, total_switches BIGINT, project_switches BIGINT, mode_switches BIGINT, "
            "alternation_loop_count BIGINT, alternation_switches BIGINT, alternation_minutes DOUBLE, "
            "alternation_share DOUBLE, avg_focus_minutes DOUBLE, longest_focus_minutes DOUBLE, fragmentation_score DOUBLE)"
        ),
        insert_sql="INSERT INTO processed_context_switches VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows=_processed_context_switch_rows,
    ),
    "processed_project_attention": TableSpec(
        name="processed_project_attention",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS processed_project_attention ("
            "date DATE, entropy DOUBLE, gini DOUBLE, top_project TEXT, "
            "top_project_share DOUBLE, project_count BIGINT, rotation_speed DOUBLE)"
        ),
        insert_sql="INSERT INTO processed_project_attention VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows=_processed_project_attention_rows,
    ),
    "processed_chat_activity": TableSpec(
        name="processed_chat_activity",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS processed_chat_activity ("
            "date DATE, provider TEXT, session_count BIGINT, total_messages BIGINT, "
            "total_words BIGINT, engaged_minutes DOUBLE, total_wall_minutes DOUBLE, dominant_work_kind TEXT, "
            "projects_json TEXT)"
        ),
        insert_sql="INSERT INTO processed_chat_activity VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows=_processed_chat_activity_rows,
    ),
    "processed_sleep_correlation": TableSpec(
        name="processed_sleep_correlation",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS processed_sleep_correlation ("
            "sleep_date DATE, sleep_hours DOUBLE, sleep_score DOUBLE, sleep_quality TEXT, "
            "segment_count BIGINT, workday_active_hours DOUBLE, workday_lines_changed BIGINT, "
            "workday_files_changed BIGINT, workday_dominant_mode TEXT, "
            "workday_deep_work_minutes DOUBLE, productivity_vs_baseline DOUBLE)"
        ),
        insert_sql="INSERT INTO processed_sleep_correlation VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows=_processed_sleep_correlation_rows,
    ),
    "processed_commit_sessions": TableSpec(
        name="processed_commit_sessions",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS processed_commit_sessions ("
            "repo TEXT, start TIMESTAMP, end_time TIMESTAMP, commits BIGINT, "
            "is_burst BOOLEAN, ai_fraction DOUBLE, lines_changed BIGINT)"
        ),
        insert_sql="INSERT INTO processed_commit_sessions VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows=_processed_commit_session_rows,
    ),
}
