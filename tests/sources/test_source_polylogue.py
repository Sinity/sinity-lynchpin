"""Tests for the lynchpin Polylogue adapter contract."""

import sqlite3
from datetime import date

from lynchpin.sources import polylogue
from lynchpin.sources.polylogue import DaySessionSummary


def _write_base_polylogue_db(path):
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE conversations (
                conversation_id TEXT PRIMARY KEY,
                provider_name TEXT NOT NULL,
                provider_conversation_id TEXT NOT NULL,
                title TEXT,
                created_at TEXT,
                updated_at TEXT,
                sort_key REAL,
                content_hash TEXT NOT NULL,
                provider_meta TEXT,
                metadata TEXT DEFAULT '{}',
                version INTEGER NOT NULL
            );
            CREATE TABLE conversation_stats (
                conversation_id TEXT PRIMARY KEY,
                provider_name TEXT NOT NULL DEFAULT '',
                message_count INTEGER NOT NULL DEFAULT 0,
                word_count INTEGER NOT NULL DEFAULT 0,
                tool_use_count INTEGER NOT NULL DEFAULT 0,
                thinking_count INTEGER NOT NULL DEFAULT 0,
                paste_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE session_profiles (
                conversation_id TEXT,
                provider_name TEXT,
                title TEXT,
                message_count INTEGER,
                substantive_count INTEGER,
                attachment_count INTEGER,
                work_event_count INTEGER,
                phase_count INTEGER,
                word_count INTEGER,
                first_message_at TEXT,
                last_message_at TEXT,
                engaged_duration_ms INTEGER,
                wall_duration_ms INTEGER,
                total_cost_usd REAL,
                cost_is_estimated INTEGER,
                canonical_session_date TEXT,
                tool_use_count INTEGER,
                thinking_count INTEGER,
                repo_names_json TEXT,
                repo_paths_json TEXT,
                auto_tags_json TEXT,
                evidence_payload_json TEXT,
                inference_payload_json TEXT
            );
            CREATE TABLE day_session_summaries (
                day TEXT,
                provider_name TEXT,
                conversation_count INTEGER,
                total_cost_usd REAL,
                total_messages INTEGER,
                total_words INTEGER,
                work_event_breakdown_json TEXT,
                repos_active_json TEXT
            );
            CREATE TABLE session_work_events (
                event_id TEXT,
                conversation_id TEXT,
                provider_name TEXT,
                kind TEXT,
                confidence REAL,
                start_time TEXT,
                end_time TEXT,
                duration_ms INTEGER,
                summary TEXT,
                file_paths_json TEXT,
                tools_used_json TEXT,
                evidence_payload_json TEXT
            );
            """
        )
        conn.execute(
            """
            INSERT INTO conversations (
                conversation_id, provider_name, provider_conversation_id, title,
                created_at, updated_at, sort_key, content_hash, provider_meta,
                version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "codex:1",
                "codex",
                "1",
                "Work on Lynchpin",
                "2026-04-22T10:00:00+00:00",
                "2026-04-22T10:30:00+00:00",
                1.0,
                "hash",
                '{"working_directories":["/realm/project/sinity-lynchpin"],'
                '"git":{"repository_url":"https://github.com/Sinity/sinity-lynchpin"}}',
                1,
            ),
        )
        conn.execute(
            """
            INSERT INTO conversation_stats (
                conversation_id, provider_name, message_count, word_count,
                tool_use_count, thinking_count, paste_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("codex:1", "codex", 7, 700, 2, 1, 0),
        )


def test_daily_activity_uses_day_summaries_without_profile_query(monkeypatch):
    def fake_summaries(*, start=None, end=None):
        return [
            DaySessionSummary(
                date=date(2026, 4, 22),
                session_count=3,
                total_cost_usd=0.0,
                total_messages=30,
                total_words=300,
                work_event_breakdown={"implementation": 2, "review": 1},
                repos_active=("sinity-lynchpin", "polylogue"),
                providers={"claude-code": 3},
            )
        ]

    def fail_profiles():
        raise AssertionError("daily_activity should prefer day summaries")

    monkeypatch.setattr(polylogue, "day_session_summaries", fake_summaries)
    monkeypatch.setattr(polylogue, "iter_session_profiles", fail_profiles)

    result = polylogue.daily_activity(start=date(2026, 4, 22), end=date(2026, 4, 22))
    assert len(result) == 1
    assert result[0].provider == "claude-code"
    assert result[0].session_count == 3
    assert result[0].dominant_work_kind == "implementation"
    assert result[0].projects == ("sinity-lynchpin", "polylogue")


def test_work_pattern_uses_repos_active_from_day_summaries(monkeypatch):
    monkeypatch.setattr(polylogue, "day_session_summaries", lambda *, start=None, end=None: [
        DaySessionSummary(
            date=date(2026, 4, 22),
            session_count=1,
            total_cost_usd=0.0,
            total_messages=10,
            total_words=100,
            work_event_breakdown={"debugging": 2},
            repos_active=("sinity-lynchpin",),
            providers={"codex": 1},
        )
    ])

    result = polylogue.work_pattern(start=date(2026, 4, 22), end=date(2026, 4, 22))
    assert len(result) == 1
    assert result[0].work_kind == "debugging"
    assert result[0].session_count == 2
    assert result[0].top_projects == ("sinity-lynchpin",)


def test_archive_readiness_reports_derived_base_table_mode(monkeypatch, tmp_path):
    db = tmp_path / "polylogue.db"
    _write_base_polylogue_db(db)
    monkeypatch.setattr(polylogue, "_default_polylogue_db_path", lambda: db)

    readiness = polylogue.archive_readiness()

    assert readiness.status == "degraded"
    assert readiness.conversation_count == 1
    assert readiness.message_count is None
    assert readiness.conversation_stats_count == 1
    assert readiness.session_profile_count == 0
    assert readiness.derives_profiles_from_base_tables is True
    assert readiness.derives_day_summaries_from_profiles is True
    assert "session_profiles" in readiness.reason


def test_archive_readiness_reports_missing_database(monkeypatch, tmp_path):
    db = tmp_path / "missing.db"
    monkeypatch.setattr(polylogue, "_default_polylogue_db_path", lambda: db)

    readiness = polylogue.archive_readiness()

    assert readiness.status == "unavailable"
    assert readiness.reason == "polylogue database does not exist"


def test_archive_readiness_can_include_heavy_counts(monkeypatch, tmp_path):
    db = tmp_path / "polylogue.db"
    _write_base_polylogue_db(db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE messages (
                message_id TEXT PRIMARY KEY,
                conversation_id TEXT,
                content_hash TEXT,
                version INTEGER
            )
            """
        )
        conn.execute(
            "INSERT INTO messages (message_id, conversation_id, content_hash, version) VALUES (?, ?, ?, ?)",
            ("msg:1", "codex:1", "hash", 1),
        )
        conn.execute(
            """
            CREATE TABLE provider_events (
                event_id TEXT PRIMARY KEY,
                conversation_id TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO provider_events (event_id, conversation_id) VALUES (?, ?)",
            ("event:1", "codex:1"),
        )
    monkeypatch.setattr(polylogue, "_default_polylogue_db_path", lambda: db)

    readiness = polylogue.archive_readiness(include_heavy_counts=True)

    assert readiness.message_count == 1
    assert readiness.provider_event_count == 1
