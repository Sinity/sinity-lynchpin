from __future__ import annotations

from .core import TableSpec

from .rows_context_day import (
    _context_day_event_rows,
    _context_day_project_rows,
    _context_day_rows,
    _context_day_topic_rows,
    _context_period_rows,
    _context_signal_coverage_rows,
    _context_signal_rows,
)
from .rows_context_periods import (
    _context_month_rows,
    _context_period_project_rows,
    _context_period_topic_rows,
    _context_quarter_rows,
    _context_week_rows,
    _context_year_rows,
)

CONTEXT_TABLE_SPECS: dict[str, TableSpec] = {
    "context_signal": TableSpec(
        name="context_signal",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS context_signal ("
            "signal_id TEXT, source TEXT, kind TEXT, start_time TIMESTAMP, end_time TIMESTAMP, "
            "duration_seconds DOUBLE, mode TEXT, mode_confidence DOUBLE, project TEXT, project_confidence DOUBLE, "
            "app TEXT, title TEXT, url TEXT, domain TEXT, cwd TEXT, detail TEXT, evidence_json TEXT)"
        ),
        insert_sql="INSERT INTO context_signal VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows=_context_signal_rows,
    ),
    "context_day": TableSpec(
        name="context_day",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS context_day ("
            "date DATE, active_seconds DOUBLE, recovery_seconds DOUBLE, observed_seconds DOUBLE, "
            "chain_count BIGINT, signal_count BIGINT, command_count BIGINT, transcript_count BIGINT, "
            "commit_count BIGINT, dominant_mode TEXT, dominant_project TEXT, dominant_topic TEXT, "
            "top_modes_json TEXT, top_projects_json TEXT, top_topics_json TEXT, "
            "source_counts_json TEXT, coverage_json TEXT, highlights_json TEXT)"
        ),
        insert_sql="INSERT INTO context_day VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows=_context_day_rows,
    ),
    "context_day_project": TableSpec(
        name="context_day_project",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS context_day_project ("
            "date DATE, project TEXT, duration_seconds DOUBLE, chain_count BIGINT, top_modes_json TEXT)"
        ),
        insert_sql="INSERT INTO context_day_project VALUES (?, ?, ?, ?, ?)",
        rows=_context_day_project_rows,
    ),
    "context_period": TableSpec(
        name="context_period",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS context_period ("
            "start_date DATE, end_date DATE, total_days BIGINT, active_seconds DOUBLE, "
            "recovery_seconds DOUBLE, observed_seconds DOUBLE, chain_count BIGINT, signal_count BIGINT, "
            "command_count BIGINT, transcript_count BIGINT, commit_count BIGINT, dominant_modes_json TEXT, "
            "dominant_projects_json TEXT, source_counts_json TEXT, coverage_json TEXT, highlights_json TEXT)"
        ),
        insert_sql="INSERT INTO context_period VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows=_context_period_rows,
    ),
    "context_signal_coverage": TableSpec(
        name="context_signal_coverage",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS context_signal_coverage ("
            "date DATE, has_activitywatch BOOLEAN, has_terminal BOOLEAN, has_polylogue BOOLEAN, "
            "has_git BOOLEAN, has_atuin BOOLEAN, has_web BOOLEAN, "
            "plane_count BIGINT, observed_hours DOUBLE, quality TEXT, source_names_json TEXT)"
        ),
        insert_sql="INSERT INTO context_signal_coverage VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows=_context_signal_coverage_rows,
    ),
    "context_month": TableSpec(
        name="context_month",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS context_month ("
            "month TEXT, start_date DATE, end_date DATE, total_days BIGINT, active_days BIGINT, "
            "active_seconds DOUBLE, recovery_seconds DOUBLE, chain_count BIGINT, signal_count BIGINT, "
            "command_count BIGINT, transcript_count BIGINT, commit_count BIGINT, "
            "dominant_mode TEXT, dominant_project TEXT, dominant_topic TEXT, "
            "top_modes_json TEXT, top_projects_json TEXT, top_topics_json TEXT, "
            "source_counts_json TEXT, coverage_summary_json TEXT, highlights_json TEXT, "
            "chat_session_count BIGINT, chat_cost_usd DOUBLE, chat_work_events_json TEXT, "
            "episode_count BIGINT, episode_labels_json TEXT, week_count BIGINT, day_patterns_json TEXT)"
        ),
        insert_sql="INSERT INTO context_month VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows=_context_month_rows,
    ),
    "context_week": TableSpec(
        name="context_week",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS context_week ("
            "iso_week TEXT, start_date DATE, end_date DATE, days BIGINT, "
            "active_seconds DOUBLE, recovery_seconds DOUBLE, observed_seconds DOUBLE, "
            "chain_count BIGINT, signal_count BIGINT, command_count BIGINT, "
            "transcript_count BIGINT, commit_count BIGINT, "
            "dominant_mode TEXT, dominant_project TEXT, dominant_topic TEXT, "
            "top_modes_json TEXT, top_projects_json TEXT, top_topics_json TEXT, "
            "day_pattern TEXT, busiest_day DATE, quietest_day DATE, "
            "active_delta_vs_prior DOUBLE)"
        ),
        insert_sql="INSERT INTO context_week VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows=_context_week_rows,
    ),
    "context_day_topic": TableSpec(
        name="context_day_topic",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS context_day_topic ("
            "date DATE, topic TEXT, seconds DOUBLE)"
        ),
        insert_sql="INSERT INTO context_day_topic VALUES (?, ?, ?)",
        rows=_context_day_topic_rows,
    ),
    "context_quarter": TableSpec(
        name="context_quarter",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS context_quarter ("
            "quarter TEXT, start_date DATE, end_date DATE, total_days BIGINT, active_days BIGINT, "
            "active_seconds DOUBLE, recovery_seconds DOUBLE, chain_count BIGINT, signal_count BIGINT, "
            "command_count BIGINT, transcript_count BIGINT, commit_count BIGINT, "
            "dominant_mode TEXT, dominant_project TEXT, dominant_topic TEXT, "
            "top_modes_json TEXT, top_projects_json TEXT, top_topics_json TEXT, "
            "coverage_summary_json TEXT, chat_session_count BIGINT, chat_cost_usd DOUBLE, "
            "episode_count BIGINT, month_count BIGINT, month_active_trend_json TEXT, "
            "active_delta_vs_prior DOUBLE)"
        ),
        insert_sql="INSERT INTO context_quarter VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows=_context_quarter_rows,
    ),
    "context_year": TableSpec(
        name="context_year",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS context_year ("
            "year TEXT, start_date DATE, end_date DATE, total_days BIGINT, active_days BIGINT, "
            "active_seconds DOUBLE, recovery_seconds DOUBLE, chain_count BIGINT, signal_count BIGINT, "
            "command_count BIGINT, transcript_count BIGINT, commit_count BIGINT, "
            "dominant_mode TEXT, dominant_project TEXT, dominant_topic TEXT, "
            "top_modes_json TEXT, top_projects_json TEXT, top_topics_json TEXT, "
            "coverage_summary_json TEXT, chat_session_count BIGINT, chat_cost_usd DOUBLE, "
            "episode_count BIGINT, quarter_count BIGINT, quarter_active_trend_json TEXT, "
            "active_delta_vs_prior DOUBLE)"
        ),
        insert_sql="INSERT INTO context_year VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows=_context_year_rows,
    ),
    "context_day_event": TableSpec(
        name="context_day_event",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS context_day_event ("
            "date DATE, event_kind TEXT, label TEXT, severity DOUBLE, evidence_json TEXT)"
        ),
        insert_sql="INSERT INTO context_day_event VALUES (?, ?, ?, ?, ?)",
        rows=_context_day_event_rows,
    ),
    "context_period_project": TableSpec(
        name="context_period_project",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS context_period_project ("
            "period_key TEXT, scale TEXT, project TEXT, seconds DOUBLE, rank INT)"
        ),
        insert_sql="INSERT INTO context_period_project VALUES (?, ?, ?, ?, ?)",
        rows=_context_period_project_rows,
    ),
    "context_period_topic": TableSpec(
        name="context_period_topic",
        create_sql=(
            "CREATE TABLE IF NOT EXISTS context_period_topic ("
            "period_key TEXT, scale TEXT, topic TEXT, seconds DOUBLE, rank INT)"
        ),
        insert_sql="INSERT INTO context_period_topic VALUES (?, ?, ?, ?, ?)",
        rows=_context_period_topic_rows,
    ),
}
