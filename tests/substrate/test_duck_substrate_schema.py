"""DuckDB substrate schema and connection contract tests."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_apply_schema_creates_all_tables(tmp_path: Path) -> None:
    """apply_schema must create all domain tables + substrate_meta."""
    from lynchpin.substrate.connection import apply_schema, connect

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
        ).fetchall()

    table_names = {r[0] for r in rows}
    expected = {
        "substrate_meta",
        "commit_fact",
        "file_change_fact",
        "ai_work_event",
        "symbol_change",
        "pr_review_row",
        "evidence_graph_build",
        "evidence_node",
        "evidence_edge",
        "substrate_source_status",
        "spotify_daily",
        "personal_daily_signal",
        "machine_metric_sample",
        "machine_gpu_sample",
        "machine_network_sample",
        "machine_service_state",
        "machine_process_io_delta_sample",
        "machine_process_memory_sample",
        "machine_cgroup_memory_sample",
        "machine_experiment_run",
        "work_observation",
    }
    assert expected <= table_names
    assert {
        "project_day_correlation",
        "issue_closure_chain_walk",
        "work_event_file_overlap",
        "work_event_symbol_overlap",
    } <= table_names


def test_apply_schema_is_idempotent(tmp_path: Path) -> None:
    """Calling apply_schema twice must be a no-op; existing rows survive."""
    from lynchpin.substrate.connection import apply_schema, connect

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        conn.execute("INSERT INTO substrate_meta VALUES ('canary', 'alive')")
        apply_schema(conn)
        row = conn.execute(
            "SELECT value FROM substrate_meta WHERE key = 'canary'"
        ).fetchone()

    assert row is not None
    assert row[0] == "alive"


def test_promote_personal_daily_signal_round_trip(tmp_path: Path) -> None:
    from datetime import date

    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.personal import promote_personal_daily_signals

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        count = promote_personal_daily_signals(
            conn,
            refresh_id="r1",
            rows=[("webhistory", date(2026, 5, 23), "visit_count", 42.0, {"domain_count": 7})],
        )
        row = conn.execute(
            "SELECT source, date, metric, value, CAST(dimensions AS VARCHAR) "
            "FROM personal_daily_signal WHERE refresh_id = 'r1'"
        ).fetchone()

    assert count == 1
    assert row is not None
    assert row[0] == "webhistory"
    assert row[2] == "visit_count"
    assert row[3] == 42.0
    assert '"domain_count": 7' in row[4]


def test_load_personal_daily_signals_includes_end_date(tmp_path: Path) -> None:
    from datetime import date

    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.personal import (
        load_personal_daily_signals,
        promote_personal_daily_signals,
    )

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        promote_personal_daily_signals(
            conn,
            refresh_id="r1",
            rows=[
                ("webhistory", date(2026, 5, 22), "visit_count", 21.0, {}),
                ("webhistory", date(2026, 5, 23), "visit_count", 42.0, {}),
                ("webhistory", date(2026, 5, 24), "visit_count", 84.0, {}),
            ],
        )
        rows = load_personal_daily_signals(
            conn,
            refresh_id="r1",
            start=date(2026, 5, 23),
            end=date(2026, 5, 23),
        )

    assert [(row[1], row[3]) for row in rows] == [(date(2026, 5, 23), 42.0)]


def test_promote_personal_daily_signal_keeps_dimension_variants(tmp_path: Path) -> None:
    from datetime import date

    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.personal import promote_personal_daily_signals

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        count = promote_personal_daily_signals(
            conn,
            refresh_id="r1",
            rows=[
                ("sleep", date(2017, 1, 29), "sleep_minutes", 120.0, {"quality": "short"}),
                ("sleep", date(2017, 1, 29), "sleep_minutes", 360.0, {"quality": "long"}),
            ],
        )
        rows = conn.execute(
            "SELECT metric, value, CAST(dimensions AS VARCHAR) "
            "FROM personal_daily_signal WHERE refresh_id = 'r1' ORDER BY value"
        ).fetchall()

    assert count == 2
    assert [row[1] for row in rows] == [120.0, 360.0]
    assert '"quality": "short"' in rows[0][2]
    assert '"quality": "long"' in rows[1][2]


def test_promote_personal_daily_signal_coalesces_exact_duplicate_dimensions(tmp_path: Path) -> None:
    from datetime import date

    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.personal import promote_personal_daily_signals

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        count = promote_personal_daily_signals(
            conn,
            refresh_id="r1",
            rows=[
                ("sleep", date(2017, 1, 29), "sleep_minutes", 120.0, {"quality": "unknown"}),
                ("sleep", date(2017, 1, 29), "sleep_minutes", 360.0, {"quality": "unknown"}),
                ("sleep", date(2017, 1, 29), "sleep_score", 40.0, {"quality": "unknown"}),
                ("sleep", date(2017, 1, 29), "sleep_score", 80.0, {"quality": "unknown"}),
            ],
        )
        rows = conn.execute(
            "SELECT metric, value FROM personal_daily_signal "
            "WHERE refresh_id = 'r1' ORDER BY metric"
        ).fetchall()

    assert count == 2
    assert rows == [("sleep_minutes", 480.0), ("sleep_score", 60.0)]


def test_incremental_personal_daily_signal_preserves_history_and_matches_full_output(tmp_path: Path) -> None:
    from datetime import date

    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.personal import load_personal_daily_signals, promote_personal_daily_signals

    predecessor_rows = [
        ("webhistory", date(2026, 5, 1), "visit_count", 10.0, {}),
        ("webhistory", date(2026, 5, 5), "visit_count", 50.0, {}),
    ]
    refreshed_rows = [
        ("webhistory", date(2026, 5, 5), "visit_count", 55.0, {}),
        ("webhistory", date(2026, 5, 6), "visit_count", 60.0, {}),
    ]
    full_rows = [predecessor_rows[0], *refreshed_rows]
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        promote_personal_daily_signals(conn, refresh_id="prior", rows=predecessor_rows)
        promote_personal_daily_signals(
            conn,
            refresh_id="incremental",
            previous_refresh_id="prior",
            incremental_tail_start=date(2026, 5, 5),
            rows=refreshed_rows,
        )
        promote_personal_daily_signals(conn, refresh_id="full", rows=full_rows)
        before_rerun = conn.execute(
            "SELECT source, date, metric, value, dimensions FROM personal_daily_signal "
            "WHERE refresh_id = 'incremental' ORDER BY date, metric"
        ).fetchall()
        promote_personal_daily_signals(
            conn,
            refresh_id="incremental",
            previous_refresh_id="incremental",
            incremental_tail_start=date(2026, 5, 5),
            rows=refreshed_rows,
        )
        after_rerun = conn.execute(
            "SELECT source, date, metric, value, dimensions FROM personal_daily_signal "
            "WHERE refresh_id = 'incremental' ORDER BY date, metric"
        ).fetchall()
        full = conn.execute(
            "SELECT source, date, metric, value, dimensions FROM personal_daily_signal "
            "WHERE refresh_id = 'full' ORDER BY date, metric"
        ).fetchall()
        logical = load_personal_daily_signals(conn, refresh_id="incremental")

    assert before_rerun == after_rerun
    assert len(before_rerun) == 2
    assert logical == [row[:5] for row in full]
    assert logical[0][1] == date(2026, 5, 1)
    assert logical[1][3] == 55.0


def test_apply_schema_recreates_on_version_bump(tmp_path: Path) -> None:
    """Downgrading the stored version triggers drop+recreate; commit_fact is empty afterward."""
    from lynchpin.substrate.connection import apply_schema, connect

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        conn.execute(
            "INSERT INTO commit_fact "
            "(sha, repo, authored_at, lines_added, lines_deleted, lines_changed, "
            "files_changed, paths, path_roots, refresh_id) "
            "VALUES ('abc', 'r', '2026-01-01 00:00:00+00', 1, 0, 1, 1, [], [], 'r1')"
        )
        conn.execute("UPDATE substrate_meta SET value='0' WHERE key='version'")
        apply_schema(conn)
        count = conn.execute("SELECT COUNT(*) FROM commit_fact").fetchone()[0]

    assert count == 0


def test_apply_schema_migrates_version_43_graph_lineage_without_data_loss(
    tmp_path: Path,
) -> None:
    """The additive graph-lineage rollout must retain the verified predecessor."""
    from lynchpin.substrate.connection import SUBSTRATE_VERSION, apply_schema, connect

    assert SUBSTRATE_VERSION == 45
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        conn.execute(
            "INSERT INTO evidence_graph_build "
            "(refresh_id, start_date, end_date, mode, projects, node_count, "
            "edge_count, caveats, generated_at) VALUES "
            "('verified', DATE '2026-01-01', DATE '2026-08-25', 'current-state', "
            "[], 12, 34, '[]', now())"
        )
        views = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' AND table_type = 'VIEW'"
        ).fetchall()
        for (view_name,) in views:
            conn.execute(f'DROP VIEW "{view_name}"')
        indexes = conn.execute(
            "SELECT index_name FROM duckdb_indexes() "
            "WHERE table_name = 'evidence_graph_build'"
        ).fetchall()
        for (index_name,) in indexes:
            conn.execute(f'DROP INDEX "{index_name}"')
        conn.execute("ALTER TABLE evidence_graph_build DROP predecessor_refresh_id")
        conn.execute("ALTER TABLE evidence_graph_build DROP predecessor_tail_start")
        conn.execute("UPDATE substrate_meta SET value = '43' WHERE key = 'version'")

        apply_schema(conn)

        assert conn.execute(
            "SELECT refresh_id, node_count, edge_count, predecessor_refresh_id, "
            "predecessor_tail_start FROM evidence_graph_build"
        ).fetchall() == [("verified", 12, 34, None, None)]
        assert conn.execute(
            "SELECT value FROM substrate_meta WHERE key = 'version'"
        ).fetchone() == ("45",)
        migrated_indexes = {
            row[0]
            for row in conn.execute(
                "SELECT index_name FROM duckdb_indexes() "
                "WHERE table_name IN ('substrate_product_lineage', "
                "'substrate_product_tombstone')"
            ).fetchall()
        }
        assert {
            "substrate_product_lineage_predecessor",
            "substrate_product_tombstone_key",
        } <= migrated_indexes


def test_signal_lineage_rejects_cycles_and_missing_predecessors(tmp_path: Path) -> None:
    from datetime import date

    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.personal import load_personal_daily_signals

    with connect(tmp_path / "sub.duckdb") as conn:
        apply_schema(conn)
        conn.execute(
            "INSERT INTO substrate_product_lineage "
            "(product, refresh_id, predecessor_refresh_id, replacement_start, mode) "
            "VALUES ('personal_daily_signals', 'a', 'b', ?, 'incremental'), "
            "('personal_daily_signals', 'b', 'a', ?, 'incremental')",
            [date(2026, 1, 1), date(2026, 1, 1)],
        )
        with pytest.raises(ValueError, match="cycle"):
            load_personal_daily_signals(conn, refresh_id="a")
        conn.execute("DELETE FROM substrate_product_lineage")
        conn.execute(
            "INSERT INTO substrate_product_lineage "
            "(product, refresh_id, predecessor_refresh_id, replacement_start, mode) "
            "VALUES ('personal_daily_signals', 'orphan', 'missing', ?, 'incremental')",
            [date(2026, 1, 1)],
        )
        with pytest.raises(ValueError, match="missing predecessor"):
            load_personal_daily_signals(conn, refresh_id="orphan")


def test_title_overlay_writes_changes_and_reuses_unchanged_input(tmp_path: Path) -> None:
    import json

    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.personal import _resolved_rows, promote_title_classifications_from_path

    def payload(title_hash: str, activity: str) -> dict[str, object]:
        return {
            "title_hash": title_hash, "app": "app", "raw_title": title_hash,
            "normalized_title": title_hash, "activity": activity, "subject": "subject",
            "content_type": "content", "attention_level": "focused", "topic_category": "topic",
            "platform": "platform", "mode": "mode", "app_kind": "kind", "tool": "tool",
            "domain": "domain", "domain_category": "category", "is_ai_tool": False,
            "is_ai_active": False, "productivity_score": 0.5, "focus_score": 0.5,
            "confidence": 0.9, "classification_source": "test", "model_version": "v1",
        }

    old_path = tmp_path / "old.ndjson"
    new_path = tmp_path / "new.ndjson"
    old_path.write_text("\n".join(json.dumps(payload(k, "old")) for k in ("a", "b")) + "\n")
    new_path.write_text("\n".join([json.dumps(payload("a", "new")), json.dumps(payload("c", "new"))]) + "\n")
    with connect(tmp_path / "sub.duckdb") as conn:
        apply_schema(conn)
        promote_title_classifications_from_path(conn, refresh_id="old", path=str(old_path), input_fingerprint="f1")
        assert promote_title_classifications_from_path(
            conn, refresh_id="new", path=str(new_path), previous_refresh_id="old", input_fingerprint="f2"
        ) == 2
        assert conn.execute("SELECT COUNT(*) FROM title_classification WHERE refresh_id='new'").fetchone()[0] == 2
        assert _resolved_rows(conn, product="title_metadata", refresh_id="new", table="title_classification",
                              columns=("title_hash",), key=lambda row: row[0]) == [("a",), ("c",)]
        assert promote_title_classifications_from_path(
            conn, refresh_id="same", path=str(new_path), previous_refresh_id="new", input_fingerprint="f2"
        ) == 0
        assert conn.execute("SELECT COUNT(*) FROM title_classification WHERE refresh_id='same'").fetchone()[0] == 0


def test_newer_row_reintroduces_key_tombstoned_by_older_partition(tmp_path: Path) -> None:
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.personal import _resolved_rows

    with connect(tmp_path / "sub.duckdb") as conn:
        apply_schema(conn)
        conn.execute(
            "INSERT INTO substrate_product_lineage "
            "(product, refresh_id, predecessor_refresh_id, mode) VALUES "
            "('title_metadata', 'base', NULL, 'full'), "
            "('title_metadata', 'deleted', 'base', 'incremental'), "
            "('title_metadata', 'restored', 'deleted', 'incremental')"
        )
        conn.execute(
            "INSERT INTO substrate_product_tombstone "
            "(product, refresh_id, natural_key) "
            "VALUES ('title_metadata', 'deleted', 'restored-key')"
        )
        conn.execute(
            "INSERT INTO title_classification "
            "(title_hash, app, normalized_title, refresh_id) "
            "VALUES ('restored-key', 'app', 'restored', 'restored')"
        )

        rows = _resolved_rows(
            conn,
            product="title_metadata",
            refresh_id="restored",
            table="title_classification",
            columns=("title_hash", "normalized_title"),
            key=lambda row: row[0],
        )

    assert rows == [("restored-key", "restored")]


def test_substrate_path_uses_local_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """substrate_path() must return a path under LynchpinConfig.local_root."""
    monkeypatch.setenv("LYNCHPIN_LOCAL_ROOT", str(tmp_path / "local"))
    import importlib
    import lynchpin.core.config as cfg_mod

    importlib.reload(cfg_mod)

    from lynchpin.substrate.connection import substrate_path

    path = substrate_path()
    assert str(tmp_path / "local") in str(path)
    assert path.suffix == ".duckdb"


def test_concurrent_writers_documented_constraint(tmp_path: Path) -> None:
    """Single-writer-many-readers constraint: open read_only after writer creates the file."""
    from lynchpin.substrate.connection import apply_schema, connect

    db = tmp_path / "sub.duckdb"
    with connect(db) as writer:
        apply_schema(writer)

    with connect(db, read_only=True) as reader:
        tables = reader.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
        ).fetchall()
    assert any("commit_fact" in r[0] for r in tables)
