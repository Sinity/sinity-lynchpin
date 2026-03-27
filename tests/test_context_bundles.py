from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import lynchpin.context.bundles as bundles_module
import lynchpin.context.history as history_module
import lynchpin.context.trust as trust_module


def _make_warehouse(db_path: Path) -> None:
    duckdb = pytest.importorskip("duckdb")
    conn = duckdb.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE processed_delivery_telemetry (
              date DATE,
              active_hours DOUBLE,
              total_commits BIGINT,
              ai_commits BIGINT,
              human_commits BIGINT,
              ai_ratio DOUBLE,
              commit_density_per_active_hour DOUBLE,
              command_count BIGINT,
              command_density_per_active_hour DOUBLE,
              chat_sessions BIGINT,
              chat_engaged_minutes DOUBLE,
              chat_minutes_per_active_hour DOUBLE,
              repos_json VARCHAR,
              ai_models_json VARCHAR
            )
            """,
        )
        conn.execute(
            """
            INSERT INTO processed_delivery_telemetry VALUES
            ('2026-03-16', 6.5, 3, 1, 2, 0.33, 0.46, 41, 6.3, 2, 18.5, 2.8, '["sinity-lynchpin"]', '["gpt-5"]')
            """,
        )
        conn.execute(
            """
            CREATE TABLE processed_project_attention (
              date DATE,
              entropy DOUBLE,
              gini DOUBLE,
              top_project VARCHAR,
              top_project_share DOUBLE,
              project_count BIGINT,
              rotation_speed DOUBLE
            )
            """,
        )
        conn.execute(
            "INSERT INTO processed_project_attention VALUES ('2026-03-16', 0.8, 0.4, 'sinity-lynchpin', 0.75, 2, 0.2)",
        )
        conn.execute(
            """
            CREATE TABLE processed_chat_activity (
              date DATE,
              provider VARCHAR,
              session_count BIGINT,
              total_messages BIGINT,
              total_words BIGINT,
              engaged_minutes DOUBLE,
              total_wall_minutes DOUBLE,
              dominant_work_kind VARCHAR,
              projects_json VARCHAR
            )
            """,
        )
        conn.execute(
            "INSERT INTO processed_chat_activity VALUES ('2026-03-16', 'claude-code', 2, 120, 4000, 55.0, 210.0, 'implementation', '[\"sinity-lynchpin\"]')",
        )
        conn.execute(
            """
            CREATE TABLE processed_git_daily (
              date DATE,
              repo VARCHAR,
              commit_count BIGINT,
              lines_added BIGINT,
              lines_deleted BIGINT,
              churn BIGINT,
              net_loc BIGINT,
              ai_coauthored BIGINT,
              ai_ratio DOUBLE,
              dominant_prefix VARCHAR,
              commit_burst_count BIGINT
            )
            """,
        )
        conn.execute(
            "INSERT INTO processed_git_daily VALUES ('2026-03-16', '/realm/project/sinity-lynchpin', 3, 120, 40, 160, 80, 1, 0.33, 'feat', 1)",
        )
        conn.execute(
            """
            CREATE TABLE processed_git_file_facts (
              date DATE,
              repo VARCHAR,
              authored_at TIMESTAMP,
              commit_sha VARCHAR,
              path VARCHAR,
              path_root VARCHAR,
              lines_added BIGINT,
              lines_deleted BIGINT,
              lines_changed BIGINT
            )
            """,
        )
        conn.execute(
            "INSERT INTO processed_git_file_facts VALUES ('2026-03-16', 'sinity-lynchpin', '2026-03-16 12:00:00', 'abc123', 'lynchpin/context/bundles.py', 'lynchpin', 80, 10, 90)",
        )
        conn.execute(
            """
            CREATE TABLE processed_focus_spans (
              date DATE,
              start TIMESTAMP,
              end_time TIMESTAMP,
              span_kind VARCHAR,
              source_kind VARCHAR,
              app VARCHAR,
              title VARCHAR,
              mode VARCHAR,
              project VARCHAR,
              duration_seconds DOUBLE,
              keypress_count BIGINT,
              changed_keypress_count BIGINT,
              keylog_state VARCHAR
            )
            """,
        )
        conn.execute(
            "INSERT INTO processed_focus_spans VALUES ('2026-03-16', '2026-03-16 10:00:00', '2026-03-16 11:30:00', 'focused', 'activitywatch.window', 'kitty', 'context/bundles.py', 'coding', 'sinity-lynchpin', 5400, 200, 120, 'keyboard_active')",
        )
        conn.execute(
            """
            CREATE TABLE processed_focus_loops (
              date DATE,
              start TIMESTAMP,
              end_time TIMESTAMP,
              duration_minutes DOUBLE,
              span_count BIGINT,
              switch_count BIGINT,
              cycle_count BIGINT,
              context_a_app VARCHAR,
              context_a_title VARCHAR,
              context_b_app VARCHAR,
              context_b_title VARCHAR,
              dominant_project VARCHAR,
              dominant_mode VARCHAR
            )
            """,
        )
        conn.execute(
            "INSERT INTO processed_focus_loops VALUES ('2026-03-16', '2026-03-16 12:00:00', '2026-03-16 12:30:00', 30.0, 4, 3, 2, 'kitty', 'edit', 'firefox', 'docs', 'sinity-lynchpin', 'coding')",
        )
        conn.execute(
            """
            CREATE TABLE processed_context_switches (
              date DATE,
              total_switches BIGINT,
              project_switches BIGINT,
              mode_switches BIGINT,
              avg_focus_minutes DOUBLE,
              longest_focus_minutes DOUBLE,
              fragmentation_score DOUBLE
            )
            """,
        )
        conn.execute(
            "INSERT INTO processed_context_switches VALUES ('2026-03-16', 15, 6, 4, 42.0, 90.0, 0.35)",
        )
        conn.execute(
            """
            CREATE TABLE processed_circadian (
              date DATE,
              hour BIGINT,
              active_minutes DOUBLE,
              recovery_minutes DOUBLE,
              dominant_mode VARCHAR,
              dominant_project VARCHAR,
              git_lines_changed BIGINT,
              git_files_changed BIGINT,
              command_count BIGINT,
              app_switches BIGINT
            )
            """,
        )
        conn.execute(
            "INSERT INTO processed_circadian VALUES ('2026-03-16', 10, 90.0, 5.0, 'coding', 'sinity-lynchpin', 90, 3, 12, 2)",
        )
        conn.execute(
            """
            CREATE TABLE processed_deep_work (
              date DATE,
              start TIMESTAMP,
              end_time TIMESTAMP,
              duration_minutes DOUBLE,
              project VARCHAR,
              mode VARCHAR,
              app_switches BIGINT,
              git_lines_changed BIGINT,
              git_files_changed BIGINT,
              command_count BIGINT,
              focus_ratio DOUBLE
            )
            """,
        )
        conn.execute(
            """
            INSERT INTO processed_deep_work VALUES
            ('2026-03-16', '2026-03-16 10:00:00', '2026-03-16 11:30:00', 90.0, 'sinity-lynchpin',
             'coding', 2, 90, 3, 12, 0.92)
            """,
        )
        conn.execute(
            """
            CREATE TABLE polylogue_session_profile (
              conversation_id VARCHAR,
              provider VARCHAR,
              title VARCHAR,
              created_at TIMESTAMP,
              message_count BIGINT,
              substantive_count BIGINT,
              word_count BIGINT,
              cost_usd DOUBLE,
              cost_is_estimated BOOLEAN,
              work_event_count BIGINT,
              dominant_work_kind VARCHAR,
              phase_count BIGINT,
              decision_count BIGINT,
              repo_paths_json VARCHAR,
              canonical_projects_json VARCHAR,
              languages_json VARCHAR,
              is_continuation BOOLEAN,
              continuation_depth BIGINT,
              thread_id INTEGER,
              first_message_at TIMESTAMP,
              last_message_at TIMESTAMP,
              wall_duration_ms BIGINT,
              auto_tags_json VARCHAR
            )
            """,
        )
        conn.execute(
            """
            INSERT INTO polylogue_session_profile VALUES
            ('claude-code:xyz', 'claude-code', 'Narrative bundle work', '2026-03-16 09:00:00',
             50, 20, 5000, 0.0, true, 4, 'implementation', 2, 1,
             '["/realm/project/sinity-lynchpin"]', '["sinity-lynchpin"]', '["python"]',
             false, 0, NULL, '2026-03-16 09:00:00', '2026-03-16 10:00:00', 3600000, '["project:sinity-lynchpin"]')
            """,
        )
    finally:
        conn.close()


def test_build_period_evidence_bundle_reads_core_surfaces(tmp_path, monkeypatch) -> None:
    duckdb = pytest.importorskip("duckdb")
    db_path = tmp_path / "warehouse.duckdb"
    _make_warehouse(db_path)
    monkeypatch.setattr(bundles_module, "open_warehouse_read_only", lambda: duckdb.connect(str(db_path), read_only=True))

    bundle = bundles_module.build_period_evidence_bundle("day", "2026-03-16", write=False)

    assert bundle.period.key == "2026-03-16"
    assert any(row.surface == "processed_delivery_telemetry" for row in bundle.freshness)
    assert any(query.query_id == "polylogue_sessions" and query.row_count == 1 for query in bundle.queries)
    assert any(query.query_id == "context_switches" and query.row_count == 1 for query in bundle.queries)
    assert any(query.query_id == "circadian" and query.row_count == 1 for query in bundle.queries)
    assert any(query.query_id == "deep_work" and query.row_count == 1 for query in bundle.queries)


def test_build_period_evidence_bundle_writes_colocated_artifacts(tmp_path, monkeypatch) -> None:
    duckdb = pytest.importorskip("duckdb")
    db_path = tmp_path / "warehouse.duckdb"
    _make_warehouse(db_path)
    cfg = SimpleNamespace(repo_root=tmp_path, warehouse_db=db_path)
    monkeypatch.setattr(bundles_module, "open_warehouse_read_only", lambda: duckdb.connect(str(db_path), read_only=True))
    monkeypatch.setattr(history_module, "get_config", lambda: cfg)
    monkeypatch.setattr(trust_module, "get_config", lambda: cfg)

    bundle = bundles_module.build_period_evidence_bundle("day", "2026-03-16", write=True)

    assert bundle.bundle_ref is not None
    evidence_dir = tmp_path / bundle.bundle_ref
    assert evidence_dir.exists()
    assert (evidence_dir / "bundle.json").exists()
    assert (evidence_dir / "index.json").exists()
    assert (evidence_dir / "summary.md").exists()
    assert (evidence_dir / "queries" / "delivery_telemetry.json").exists()
