from __future__ import annotations

from contextlib import nullcontext
from datetime import date, timedelta
import json
from pathlib import Path
import signal
from types import SimpleNamespace

import duckdb
import pytest


def test_snapshot_promotion_counts_keep_unknown_for_partial_sources(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from lynchpin.cli import substrate_snapshot
    import lynchpin.substrate.connection as connection

    database = tmp_path / "substrate.duckdb"
    monkeypatch.setattr(connection, "substrate_path", lambda: database)
    refresh_id = substrate_snapshot._snapshot_refresh_id(
        start=date(2026, 6, 1), end=date(2026, 6, 2), projects=()
    )
    token = connection._substrate_path_override.set(database)
    try:
        with connection.connect() as conn:
            connection.apply_schema(conn)
            conn.execute(
                """
                INSERT INTO substrate_source_status
                (refresh_id, source, kind, status, reason, row_count, window_start, window_end, recorded_at)
                VALUES (?, 'evidence_graph', 'graph', 'partial', 'missing tail', 99, ?, ?, now())
                """,
                [refresh_id, date(2026, 6, 1), date(2026, 6, 2)],
            )
            conn.execute(
                """
                INSERT INTO evidence_graph_build
                (refresh_id, start_date, end_date, mode, projects, node_count, edge_count, caveats, generated_at)
                VALUES (?, ?, ?, 'materialized', [], 99, 88, '[]', now())
                """,
                [refresh_id, date(2026, 6, 1), date(2026, 6, 2)],
            )

        substrate_snapshot._record_snapshot_promotion_run(
            start=date(2026, 6, 1), end=date(2026, 6, 2), projects=()
        )

        with connection.connect(read_only=True) as conn:
            status, counts = conn.execute(
                "SELECT status, counts FROM substrate_promotion_run WHERE refresh_id = ?",
                [refresh_id],
            ).fetchone()
            typed_statuses = conn.execute(
                "SELECT source, status FROM substrate_source_status WHERE refresh_id = ? "
                "AND source IN ('commits', 'file_changes', 'symbols', 'ai_work_events', "
                "'work_observations') ORDER BY source",
                [refresh_id],
            ).fetchall()
    finally:
        connection._substrate_path_override.reset(token)
    assert status == "degraded"
    assert typed_statuses == [
        ("ai_work_events", "unavailable"),
        ("commits", "unavailable"),
        ("file_changes", "unavailable"),
        ("symbols", "unavailable"),
        ("work_observations", "unavailable"),
    ]
    payload = json.loads(counts)
    assert payload["evidence_graph_nodes"] is None
    assert payload["evidence_graph_edges"] is None
    assert payload["analysis_claims"] is None
    assert payload["personal_daily_signal"] is None


def test_plan_json_exits_before_materialization(monkeypatch, capsys) -> None:
    from lynchpin.cli import materialize

    monkeypatch.setattr(materialize, "plan_materializations", lambda **_kwargs: [])
    monkeypatch.setattr(
        materialize,
        "run_materialization_plan",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("plan must not execute")),
    )

    code = materialize.main(["--all", "--plan-json", "--progress", "quiet"])

    assert code == 0
    assert capsys.readouterr().out == "[]\n"


def test_full_event_index_option_requires_all_history() -> None:
    from lynchpin.cli import materialize

    with pytest.raises(SystemExit) as exc:
        materialize.main(
            [
                "--all",
                "--promote",
                "--start",
                "2026-09-01",
                "--end",
                "2026-09-02",
                "--full",
            ]
        )

    assert exc.value.code == 2


def test_materialize_history_all_derives_window(monkeypatch, tmp_path: Path) -> None:
    from lynchpin.cli import materialize
    from lynchpin.materialization import MaterializedDataset

    rows = [
        MaterializedDataset(
            name="webhistory",
            status="ready",
            authority="fixture",
            query_surface="fixture",
            materialized_paths=(),
            raw_roots=(),
            row_count=1,
            first_date=date(2013, 3, 27),
            last_date=date(2026, 5, 23),
            materialization_hint="refresh",
            reason="ready",
        )
    ]
    forwarded = {}
    calls: dict[str, object] = {}

    monkeypatch.setattr(materialize, "plan_materializations", lambda force=False, window=None: [])
    monkeypatch.setattr(
        materialize,
        "run_materialization_plan",
        lambda plan, window=None, continue_on_error=False, full=False: calls.update(
            continue_on_error=continue_on_error,
            full=full,
        )
        or [],
    )
    monkeypatch.setattr(materialize, "audit_materialization", lambda: rows)
    monkeypatch.setattr("lynchpin.substrate.connection.candidate_generation", nullcontext)

    def fake_snapshot(argv: list[str]) -> int:
        forwarded["argv"] = argv
        return 0

    import lynchpin.cli.substrate_snapshot as snapshot

    monkeypatch.setattr(snapshot, "main", fake_snapshot)
    monkeypatch.setenv("LYNCHPIN_LOCAL_ROOT", str(tmp_path))

    code = materialize.main(["--all", "--promote", "--history", "all", "--full"])

    assert code == 0
    assert calls["continue_on_error"] is True
    assert calls["full"] is True
    assert forwarded["argv"][:4] == [
        "--start",
        "2013-03-27",
        "--end",
        "2026-05-24",
    ]
    assert "--mode" not in forwarded["argv"]
    assert "--existing-products" not in forwarded["argv"]


# Obsolete procedural-registry test removed by the typed materializer cutover.


# Obsolete procedural-registry test removed by the typed materializer cutover.


def test_incremental_rebuild_candidate_indexes_is_explicit(monkeypatch, tmp_path: Path) -> None:
    from lynchpin.cli import materialize
    from lynchpin.materialization import MaterializedDataset

    rows = [
        MaterializedDataset(
            name="fixture",
            status="ready",
            authority="fixture",
            query_surface="fixture",
            materialized_paths=(),
            raw_roots=(),
            row_count=1,
            first_date=date(2026, 5, 1),
            last_date=date(2026, 5, 2),
            materialization_hint="refresh",
            reason="ready",
        )
    ]
    calls: dict[str, object] = {}
    monkeypatch.setattr(materialize, "plan_materializations", lambda **_kwargs: [])
    monkeypatch.setattr(materialize, "run_materialization_plan", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(materialize, "audit_materialization", lambda: rows)
    monkeypatch.setattr(materialize, "_record_incremental_phase", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "lynchpin.substrate.connection.candidate_generation",
        lambda **kwargs: calls.update(kwargs) or nullcontext(),
    )
    import lynchpin.cli.substrate_snapshot as snapshot

    monkeypatch.setattr(snapshot, "main", lambda _argv: 0)
    monkeypatch.setenv("LYNCHPIN_LOCAL_ROOT", str(tmp_path))

    assert materialize.main([
        "--all", "--promote", "--history", "incremental",
        "--rebuild-candidate-indexes", "--progress", "quiet",
    ]) == 0
    assert calls == {
        "rebuild_indexes": True,
        "receipt_refresh_id": "current-state:2026-05-01:2026-05-03:all",
    }


def test_rebuild_candidate_indexes_requires_promoted_materialization() -> None:
    from lynchpin.cli import materialize

    with pytest.raises(SystemExit, match="2"):
        materialize.main(["--all", "--rebuild-candidate-indexes"])


def test_all_requires_candidate_publication() -> None:
    from lynchpin.cli import materialize

    with pytest.raises(SystemExit, match="2"):
        materialize.main(["--all"])


def test_bootstrap_uses_the_strict_empty_generation_route(monkeypatch, tmp_path: Path) -> None:
    from lynchpin.cli import materialize
    from lynchpin.materialization import MaterializedDataset

    calls: dict[str, object] = {}
    rows = [
        MaterializedDataset(
            name="fixture", status="ready", authority="fixture", query_surface="fixture",
            materialized_paths=(), raw_roots=(), row_count=1, first_date=date(2026, 5, 1),
            last_date=date(2026, 5, 2), materialization_hint="refresh", reason="ready",
        )
    ]
    monkeypatch.setattr(materialize, "plan_materializations", lambda **_kwargs: [])
    monkeypatch.setattr(materialize, "run_materialization_plan", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(materialize, "audit_materialization", lambda: rows)
    monkeypatch.setattr(materialize, "_record_incremental_phase", lambda *_args, **_kwargs: None)
    candidate = SimpleNamespace(candidate=tmp_path / "candidate.duckdb", refresh_id="candidate")
    monkeypatch.setattr(
        "lynchpin.substrate.connection.bootstrap_candidate_generation",
        lambda: calls.setdefault("bootstrap", True) and nullcontext(candidate),
    )
    monkeypatch.setattr("lynchpin.substrate.connection.generation_refresh_id", lambda _path: "current")
    import lynchpin.cli.substrate_snapshot as snapshot

    monkeypatch.setattr(snapshot, "main", lambda _argv: 0)
    assert materialize.main([
        "--all", "--promote", "--bootstrap", "--start", "2026-05-01", "--end", "2026-05-02",
        "--progress", "quiet",
    ]) == 0
    assert calls == {"bootstrap": True}


def test_promote_without_all_uses_existing_canonical_products(monkeypatch, tmp_path: Path) -> None:
    from lynchpin.cli import materialize
    from lynchpin.materialization import MaterializedDataset

    rows = [
        MaterializedDataset(
            name="webhistory",
            status="ready",
            authority="fixture",
            query_surface="fixture",
            materialized_paths=(),
            raw_roots=(),
            row_count=1,
            first_date=date(2013, 3, 27),
            last_date=date(2026, 5, 23),
            materialization_hint="refresh",
            reason="ready",
        )
    ]
    forwarded: dict[str, list[str]] = {}
    monkeypatch.setattr(
        materialize,
        "plan_materializations",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("promotion-only must not plan rebuilds")),
    )
    monkeypatch.setattr(materialize, "audit_materialization", lambda: rows)
    monkeypatch.setattr("lynchpin.substrate.connection.candidate_generation", nullcontext)

    import lynchpin.cli.substrate_snapshot as snapshot

    monkeypatch.setattr(
        snapshot,
        "main",
        lambda argv: forwarded.setdefault("argv", argv) and 0,
    )
    monkeypatch.setenv("LYNCHPIN_LOCAL_ROOT", str(tmp_path))

    code = materialize.main(["--promote", "--history", "all"])

    assert code == 0
    assert forwarded["argv"][:4] == [
        "--start",
        "2013-03-27",
        "--end",
        "2026-05-24",
    ]
    assert "--existing-products" in forwarded["argv"]
    assert "--graph-only" in forwarded["argv"]


def test_promote_sigterm_archives_candidate_and_returns_signal_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lynchpin.cli import materialize
    from lynchpin.substrate import connection
    from lynchpin.substrate.status_manifest import (
        substrate_status_manifest_path,
        write_substrate_status_manifest,
    )

    canonical = connection.substrate_path()
    with duckdb.connect(str(canonical)) as conn:
        connection.apply_schema(conn)
        conn.execute(
            """
            INSERT INTO substrate_promotion_run
            (refresh_id, status, reason, window_start, window_end, mode, counts, started_at, finished_at)
            VALUES ('prior', 'ok', NULL, NULL, NULL, 'test', '{}', now(), now())
            """
        )
        conn.execute(
            """
            INSERT INTO substrate_source_status
            (refresh_id, source, kind, status, reason, row_count, window_start, window_end, recorded_at)
            VALUES ('prior', 'fixture', 'stage', 'ok', NULL, 1, NULL, NULL, now())
            """
        )
    connection.update_read_snapshot()
    assert write_substrate_status_manifest(canonical) is not None
    serving_paths = (
        canonical,
        connection.substrate_read_snapshot_path(),
        substrate_status_manifest_path(canonical),
    )
    serving_contents = {path: path.read_bytes() for path in serving_paths}
    candidate_path: Path | None = None

    def interrupt_materialization(*_args, **_kwargs) -> list[object]:
        nonlocal candidate_path
        candidate_path = connection.substrate_path()
        candidate_path.with_name(f"{candidate_path.name}.wal").write_bytes(b"candidate wal")
        signal.raise_signal(signal.SIGTERM)
        raise AssertionError("SIGTERM should have interrupted the candidate context")

    monkeypatch.setattr(materialize, "run_materialization_plan", interrupt_materialization)

    code = materialize.main(
        [
            "--promote",
            "--start",
            "2026-05-01",
            "--end",
            "2026-05-02",
            "--progress",
            "quiet",
        ]
    )

    assert code == 128 + signal.SIGTERM
    assert candidate_path is not None
    assert not candidate_path.exists()
    assert not candidate_path.with_name(f"{candidate_path.name}.wal").exists()
    receipts = list(candidate_path.parent.glob("candidate-receipt-*.json"))
    assert len(receipts) == 1
    assert json.loads(receipts[0].read_text())["status"] == "interrupted"
    assert all(path.read_bytes() == serving_contents[path] for path in serving_paths)
    assert connection.generation_refresh_id(canonical) == "prior"
    assert connection.generation_refresh_id(connection.substrate_read_snapshot_path()) == "prior"


def test_materialize_rejects_mode_option(monkeypatch) -> None:
    from lynchpin.cli import materialize

    monkeypatch.setattr(materialize, "plan_materializations", lambda force=False, window=None: [])
    monkeypatch.setattr(materialize, "run_materialization_plan", lambda plan, window=None: [])

    with pytest.raises(SystemExit) as exc:
        materialize.main(["--all", "--mode", "local-heavy"])

    assert exc.value.code == 2


# Obsolete procedural-registry test removed by the typed materializer cutover.


# Obsolete procedural-registry test removed by the typed materializer cutover.


# Obsolete procedural-registry test removed by the typed materializer cutover.


def test_standalone_materialization_does_not_mutate_published_substrate(monkeypatch) -> None:
    from lynchpin import materialization
    from lynchpin.substrate import connection

    monkeypatch.setattr(connection, "in_candidate_generation", lambda: False)
    monkeypatch.setattr(
        connection,
        "connect",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("published substrate must stay untouched")),
    )

    materialization._record_materialization_step("refresh", "fixture", "ok", "materialized")


# Obsolete procedural-registry test removed by the typed materializer cutover.


def test_snapshot_daily_signals_ensures_products_before_promoting(monkeypatch, tmp_path) -> None:
    pinned = (tmp_path / "generation" / "daily.ndjson", tmp_path / "generation" / "title_usage.ndjson")
    next_selection = (tmp_path / "later" / "daily.ndjson", tmp_path / "later" / "title_usage.ndjson")
    selection = [pinned]
    monkeypatch.setattr("lynchpin.sources.activity_content.activity_content_bundle_paths", lambda: selection[0])
    import lynchpin.cli.substrate_snapshot as snapshot

    ensure_calls: list[tuple[str, tuple[date, date] | None]] = []
    read_state = {"ensured": False}
    executed_sql: list[object] = []

    class Conn:
        def execute(self, *_args, **_kwargs):
            executed_sql.append(_args[0] if _args else None)
            return self

        def fetchone(self):
            return (0,)

    class Connect:
        def __enter__(self):
            return Conn()

        def __exit__(self, *_args):
            return None

    def fake_ensure_materialized(name: str, *, window=None):
        ensure_calls.append((name, window))
        if name == "personal_daily_signals":
            read_state["ensured"] = True
        return SimpleNamespace(to_json=lambda: {"name": name, "status": "ready"})

    read_windows: list[tuple[str, date | None, date | None, bool]] = []

    def fake_iter_personal_daily_signals(*, start=None, end=None, ensure=True):
        assert read_state["ensured"]
        read_windows.append(("personal_daily_signals", start, end, ensure))
        yield SimpleNamespace(
            source="keylog",
            date=date(2026, 5, 1),
            metric="keypress_count",
            value=5.0,
            dimensions={},
        )

    def fake_iter_activity_content_days(*, path=None, start=None, end=None, ensure=True):
        assert path == pinned[0]
        selection[0] = next_selection
        read_windows.append(("activity_content_days", start, end, ensure))
        return iter(())

    def fake_iter_activity_title_usage(*, path=None, start=None, end=None, ensure=True):
        assert path == pinned[1]
        read_windows.append(("activity_title_usage", start, end, ensure))
        return iter(())

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)
    monkeypatch.setattr(
        "lynchpin.sources.personal_signals.iter_personal_daily_signals",
        fake_iter_personal_daily_signals,
    )
    monkeypatch.setattr("lynchpin.sources.activity_content.iter_activity_content_days", fake_iter_activity_content_days)
    monkeypatch.setattr("lynchpin.sources.activity_content.iter_activity_title_usage", fake_iter_activity_title_usage)
    monkeypatch.setattr(
        "lynchpin.sources.title_metadata.title_metadata_path",
        lambda _root=None: Path("fixture.duckdb"),
    )
    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: Path("fixture.duckdb"))
    monkeypatch.setattr("lynchpin.substrate.connection.apply_schema", lambda _conn: None)
    monkeypatch.setattr("lynchpin.substrate.connection.connect", lambda *_args, **_kwargs: Connect())
    monkeypatch.setattr(
        "lynchpin.substrate.personal.promote_title_classifications_from_path",
        lambda *_args, **_kwargs: SimpleNamespace(written=0, logical=0),
    )
    monkeypatch.setattr("lynchpin.substrate.personal.promote_activity_content_days", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr("lynchpin.substrate.personal.promote_activity_content_buckets", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr("lynchpin.substrate.personal.promote_activity_title_usage", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        "lynchpin.substrate.personal.promote_personal_daily_signals",
        lambda _conn, *, rows, **_kwargs: len(list(rows)),
    )
    monkeypatch.setattr(
        "lynchpin.substrate.source_status.record_source_status",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        snapshot,
        "_snapshot_refresh_id",
        lambda *, start, end, projects: "snapshot-fixture",
    )

    snapshot._promote_snapshot_daily_signals(
        start=date(2026, 5, 1),
        end=date(2026, 5, 2),
        projects=(),
    )

    assert ensure_calls == [
        ("title_metadata", (date(2026, 5, 1), date(2026, 5, 2))),
        ("activity_content", (date(2026, 5, 1), date(2026, 5, 2))),
        ("personal_daily_signals", (date(2026, 5, 1), date(2026, 5, 2))),
    ]
    assert read_windows == [
        ("activity_content_days", date(2026, 5, 1), date(2026, 5, 2), False),
        ("activity_title_usage", None, None, False),
        ("personal_daily_signals", date(2026, 5, 1), date(2026, 5, 2), False),
    ]
    assert "BEGIN TRANSACTION" not in executed_sql


def test_snapshot_uses_current_state_default_graph_materialization(monkeypatch) -> None:
    import lynchpin.cli.substrate_snapshot as snapshot

    forwarded: dict[str, list[str]] = {}
    monkeypatch.setattr(
        snapshot,
        "current_state_main",
        lambda argv: forwarded.setdefault("argv", argv) and 0,
    )
    monkeypatch.setattr(snapshot, "_record_run_step", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(snapshot, "_record_snapshot_materialization_statuses", lambda **_kwargs: None)
    monkeypatch.setattr(snapshot, "_promote_snapshot_daily_signals", lambda **_kwargs: None)
    monkeypatch.setattr(snapshot, "_record_snapshot_promotion_run", lambda **_kwargs: None)

    code = snapshot.main(
        ["--start", "2026-05-01", "--end", "2026-05-02", "--progress", "quiet"]
    )

    assert code == 0
    assert "--materialize-substrate" not in forwarded["argv"]
    assert "--no-materialize-substrate" not in forwarded["argv"]


def test_snapshot_uses_existing_products_when_requested(monkeypatch) -> None:
    import lynchpin.cli.substrate_snapshot as snapshot

    promoted: dict[str, object] = {}
    materialized: dict[str, object] = {}
    monkeypatch.setattr(
        snapshot,
        "current_state_main",
        lambda _argv: pytest.fail("graph-only snapshot must not render a context pack"),
    )
    monkeypatch.setattr(
        "lynchpin.graph.context_pack.materialize_evidence_graph",
        lambda **kwargs: materialized.update(kwargs),
    )
    monkeypatch.setattr(snapshot, "_record_run_step", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(snapshot, "_record_snapshot_materialization_statuses", lambda **_kwargs: None)
    monkeypatch.setattr(
        snapshot,
        "_promote_snapshot_daily_signals",
        lambda **kwargs: promoted.update(kwargs),
    )
    monkeypatch.setattr(snapshot, "_record_snapshot_promotion_run", lambda **_kwargs: None)

    code = snapshot.main(
        [
            "--start",
            "2026-05-01",
            "--end",
            "2026-05-02",
            "--existing-products",
            "--graph-only",
            "--progress",
            "quiet",
        ]
    )

    assert code == 0
    assert materialized["start"] == date(2026, 5, 1)
    assert materialized["end"] == date(2026, 5, 2)
    assert materialized["projects"] == ()
    assert materialized["generation"] is None
    assert len(materialized["input_fingerprint"]) == 64
    assert promoted == {}


def test_snapshot_threads_incremental_tail_to_daily_signal_promotion(monkeypatch) -> None:
    import lynchpin.cli.substrate_snapshot as snapshot

    promoted: dict[str, object] = {}
    incremental: dict[str, object] = {}
    monkeypatch.setattr(snapshot, "_record_run_step", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(snapshot, "_record_snapshot_materialization_statuses", lambda **_kwargs: None)
    monkeypatch.setattr(snapshot, "_record_snapshot_promotion_run", lambda **_kwargs: None)
    monkeypatch.setattr(
        "lynchpin.graph.context_pack.materialize_incremental_evidence_graph",
        lambda **kwargs: incremental.update(kwargs),
    )
    monkeypatch.setattr(
        snapshot,
        "_promote_snapshot_daily_signals",
        lambda **kwargs: promoted.update(kwargs),
    )

    assert snapshot.main(
        [
            "--start", "2026-05-01", "--end", "2026-05-10",
            "--incremental-tail-start", "2026-05-08",
            "--existing-products", "--graph-only", "--progress", "quiet",
        ]
    ) == 0
    assert incremental["tail_start"] == date(2026, 5, 8)
    assert promoted == {}


def test_selected_plan_includes_dependencies_and_excludes_unrelated_writers(monkeypatch, capsys):
    from lynchpin.cli import materialize

    def step(name, dependencies=()):
        return SimpleNamespace(product=name, dependencies=dependencies, to_json=lambda: {"product": name})

    steps = [step("index"), step("activity", ("index",)), step("health"), step("code_snapshots")]
    monkeypatch.setattr(materialize, "plan_materializations", lambda **kwargs: steps)
    monkeypatch.setattr(materialize, "materializer_dependency_model", lambda plan: ())
    monkeypatch.setattr(materialize, "materializer_execution_waves", lambda model: ())
    monkeypatch.setattr(materialize, "run_materialization_plan", lambda *a, **k: pytest.fail("plan wrote data"))
    assert materialize.main(["--all", "--plan-json", "--only", "activity", "health", "--progress", "quiet"]) == 0
    assert json.loads(capsys.readouterr().out) == [
        {"product": "index"}, {"product": "activity"}, {"product": "health"},
    ]


def test_selected_plan_rejects_unknown_products(monkeypatch):
    from lynchpin.cli import materialize

    monkeypatch.setattr(materialize, "plan_materializations", lambda **kwargs: [])
    with pytest.raises(SystemExit, match="2"):
        materialize.main(["--all", "--plan-json", "--only", "misspelled"])


@pytest.mark.parametrize(
    ("source", "expected"),
    (("polylogue_verification", "degraded"), ("polylogue", "error"), ("evidence_graph", "error"), ("unknown_source", "error")),
)
def test_snapshot_promotion_preserves_optional_errors_without_vetoing_graph(monkeypatch, tmp_path, source, expected):
    from lynchpin.cli import substrate_snapshot
    import lynchpin.substrate.connection as connection

    database = tmp_path / "substrate.duckdb"
    monkeypatch.setattr(connection, "substrate_path", lambda: database)
    start, end = date(2026, 1, 1), date(2026, 1, 2)
    rid = substrate_snapshot._snapshot_refresh_id(start=start, end=end, projects=())
    token = connection._substrate_path_override.set(database)
    try:
        with connection.connect(database) as conn:
            connection.apply_schema(conn)
            conn.execute(
                "INSERT INTO evidence_graph_build "
                "(refresh_id,start_date,end_date,mode,projects,node_count,edge_count,caveats,generated_at) "
                "VALUES (?, ?, ?, 'materialized', [], 1, 0, '[]', now())",
                [rid, start, end],
            )
            for name, status in (("evidence_graph", "ok"), (source, "error")):
                conn.execute("DELETE FROM substrate_source_status WHERE refresh_id=? AND source=?", [rid, name])
                conn.execute(
                    "INSERT INTO substrate_source_status "
                    "(refresh_id,source,kind,status,reason,row_count,window_start,window_end,recorded_at) "
                    "VALUES (?, ?, 'dataset', ?, 'neutral source failure', NULL, ?, ?, now())",
                    [rid, name, status, start, end],
                )
        substrate_snapshot._record_snapshot_promotion_run(start=start, end=end, projects=())
        with connection.connect(database, read_only=True) as conn:
            assert conn.execute("SELECT status FROM substrate_promotion_run WHERE refresh_id=?", [rid]).fetchone()[0] == expected
            assert conn.execute("SELECT status,row_count FROM substrate_source_status WHERE refresh_id=? AND source=?",
                                [rid, source]).fetchone() == ("error", None)
    finally:
        connection._substrate_path_override.reset(token)


def test_incremental_graph_tail_covers_publication_gap_without_accepting_failed_build(tmp_path):
    from lynchpin.cli.materialize import _incremental_graph_tail_start
    from lynchpin.substrate.connection import apply_schema

    with duckdb.connect(str(tmp_path / "candidate.duckdb")) as conn:
        apply_schema(conn)
        for rid, end, status in (
            ("serving", date(2026, 1, 10), "degraded"),
            ("failed-attempt", date(2026, 1, 30), "error"),
        ):
            conn.execute(
                "INSERT INTO evidence_graph_build "
                "(refresh_id,start_date,end_date,mode,projects,node_count,edge_count,caveats,generated_at) "
                "VALUES (?, ?, ?, 'materialized', [], 1, 0, '[]', now())",
                [rid, date(2026, 1, 1), end],
            )
            conn.execute(
                "INSERT INTO substrate_promotion_run "
                "(refresh_id,status,counts,started_at,finished_at) VALUES (?, ?, '{}', now(), now())",
                [rid, status],
            )
            conn.execute(
                "INSERT INTO substrate_source_status "
                "(refresh_id,source,kind,status,reason,row_count,recorded_at) "
                "VALUES (?, 'evidence_graph', 'graph', 'ok', NULL, 1, now())",
                [rid],
            )
        assert _incremental_graph_tail_start(
            conn, refresh_id="new", full_start=date(2026, 1, 1),
            requested_tail_start=date(2026, 1, 20),
        ) == date(2026, 1, 10)
        assert _incremental_graph_tail_start(
            conn, refresh_id="new", full_start=date(2026, 1, 1),
            requested_tail_start=date(2026, 1, 5),
        ) == date(2026, 1, 5)


@pytest.mark.parametrize("typed_status", ["ok", "error"])
def test_incremental_candidate_identity_is_shared_by_sources_graph_and_publication(monkeypatch, tmp_path, typed_status):
    from lynchpin.cli import materialize, substrate_snapshot
    from lynchpin.substrate import connection
    from lynchpin.analysis.active import substrate_promote

    generation = connection.CandidateGeneration(
        candidate=tmp_path / "candidate.duckdb", canonical=tmp_path / "serving.duckdb",
        refresh_id="fixture-attempt", seed_source=tmp_path / "serving.duckdb",
        seed_mode="reflink", seed_logical_rows=0,
    )
    observed = {}
    monkeypatch.setattr(materialize, "_all_history_window", lambda: (date(2026, 1, 1), date(2026, 1, 3)))
    monkeypatch.setattr(materialize, "plan_materializations", lambda **kwargs: [])
    monkeypatch.setattr(materialize, "audit_materialization", lambda: [])
    monkeypatch.setattr(materialize, "_record_incremental_phase", lambda *args, **kwargs: None)
    monkeypatch.setattr(materialize, "_incremental_graph_tail_start", lambda *args, **kwargs: date(2026, 1, 2))
    monkeypatch.setattr(materialize, "run_materialization_plan", lambda *args, **kwargs: observed.update(source=kwargs["refresh_id"]) or [])
    monkeypatch.setattr(connection, "candidate_generation", lambda **kwargs: nullcontext(generation))
    monkeypatch.setattr(connection, "connect", lambda *args, **kwargs: nullcontext(None))
    monkeypatch.setattr(connection, "generation_refresh_id", lambda *args: observed["source"])
    monkeypatch.setattr(substrate_snapshot, "main", lambda argv: observed.update(argv=argv) or 0)
    monkeypatch.setattr(
        substrate_promote, "run_substrate_promote",
        lambda **kwargs: observed.update(typed=kwargs) or SimpleNamespace(status=typed_status, reason="fixture failure"),
    )
    assert materialize.main(["--all", "--promote", "--history", "incremental", "--progress", "quiet"]) == (0 if typed_status == "ok" else 1)
    argv = observed["argv"]
    assert argv[argv.index("--graph-generation")+1] == generation.refresh_id
    expected = substrate_snapshot._snapshot_refresh_id(
        start=date(2026, 1, 1), end=date(2026, 1, 3), projects=(), generation=generation.refresh_id,
    )
    assert observed["source"] == generation.receipt_refresh_id == observed["typed"]["refresh_id"] == expected
    assert observed["typed"]["write_evidence_graph"] is False
    assert observed["typed"]["window_start"] == date(2026, 1, 1)
    assert observed["typed"]["partition_window_start"] == date(2026, 1, 2)
    assert observed["typed"]["window_end"] == date(2026, 1, 3)
    assert generation.expected_refresh_id == (expected if typed_status == "ok" else None)


def test_incremental_promotion_uses_completed_rebuild_window(monkeypatch):
    from lynchpin.cli import materialize, substrate_snapshot
    from lynchpin.substrate import connection
    observed = {}
    monkeypatch.setattr(materialize, "_all_history_window", lambda: (date(2020, 5, 1), date(2020, 5, 10)))
    monkeypatch.setattr(materialize, "plan_materializations", lambda **kwargs: [])
    monkeypatch.setattr(materialize, "audit_materialization", lambda: [])
    monkeypatch.setattr(materialize, "_record_incremental_phase", lambda *args, **kwargs: None)
    monkeypatch.setattr(materialize, "run_materialization_plan", lambda *args, **kwargs: [SimpleNamespace(action="materialize", window=(date(2020, 5, 2), date(2020, 5, 10)))])
    monkeypatch.setattr(connection, "candidate_generation", lambda **kwargs: nullcontext())
    monkeypatch.setattr(substrate_snapshot, "main", lambda argv: observed.update(argv=argv) or 0)
    assert materialize.main(["--all", "--promote", "--history", "incremental", "--progress", "quiet"]) == 0
    argv = observed["argv"]
    assert argv[argv.index("--incremental-tail-start") + 1] == "2020-05-02"


@pytest.mark.parametrize("mode", ["materialize", "reuse", "older_gap"])
def test_incremental_prepares_dependent_products_for_graph_replacement_before_writers(monkeypatch, tmp_path, mode):
    from dataclasses import replace
    from lynchpin import materialization as audit
    from lynchpin.cli import materialize, substrate_snapshot
    from lynchpin.substrate import connection
    from lynchpin.analysis.active import substrate_promote
    from lynchpin.ingest import activity_content_materialize as content
    from lynchpin.materializers import ConvergencePlanner, ConvergenceRequest
    from lynchpin.materializers.catalog import PRODUCT_CATALOG
    from lynchpin.materializers import handlers, production

    end, source_start, graph_start = date(2026, 1, 15), date(2026, 1, 13), date(2026, 1, 8)
    plan = ConvergencePlanner(PRODUCT_CATALOG.values()).plan(
        ConvergenceRequest(("personal_daily_signals", "temporal_signals"), (source_start, end))
    ).steps
    plan = [replace(step, effective_window=(graph_start, end)) if step.product == "temporal_signals" else step for step in plan]
    generation = connection.CandidateGeneration(
        candidate=tmp_path / "candidate.duckdb", canonical=tmp_path / "serving.duckdb",
        refresh_id="fixture-prepared", seed_source=tmp_path / "serving.duckdb",
        seed_mode="reflink", seed_logical_rows=0,
    )
    aw, titles = tmp_path / "events.ndjson", tmp_path / "titles.ndjson"
    aw.write_text('{}\n')
    titles.write_text('{}\n')
    output = tmp_path / "daily.ndjson"
    monkeypatch.setattr(content, "activity_content_input_files", lambda: (aw, titles))
    monkeypatch.setattr(content, "load_title_classification_map", lambda: {})
    monkeypatch.setattr(content, "focus_spans", lambda **_kwargs: iter(()))
    monkeypatch.setattr(content, "activity_content_daily_path", lambda: output)
    monkeypatch.setattr(audit, "activity_content_input_files", lambda: (aw, titles))
    monkeypatch.setattr(audit, "activity_content_daily_path", lambda: output)
    monkeypatch.setattr(audit, "activity_content_manifest_path", lambda: output.with_suffix('.manifest.json'))
    monkeypatch.setattr(audit, "activity_title_usage_path", lambda: tmp_path / "title_usage.ndjson")
    monkeypatch.setattr(audit, "_record_materialization_step", lambda *_args, **_kwargs: None)
    cfg = SimpleNamespace(derived_root=tmp_path, data_root=tmp_path)
    monkeypatch.setattr(handlers, "activitywatch_derived_refresh_window", lambda window: window)
    observed = {}
    source_domains = {"activitywatch_derived": (source_start, end), "personal_daily_signals": (source_start, end)}
    if mode == "reuse":
        content.materialize_activity_content(start=source_start, end=end, output=output)

    def require_content():
        with audit.prepared_product_reads():
            result = audit.ensure_materialized("activity_content", cfg=cfg, window=(graph_start, end))
            assert result.status == "ready"
            assert result.coverage["fully_covers_requested_window"] is True

    def handler(name):
        def run(**kwargs):
            observed[name] = kwargs
            if name == "activity_content":
                assert kwargs["allow_history_bootstrap"] is False
                return content.materialize_activity_content(output=output, **kwargs)
            if name == "personal_daily_signals":
                require_content()  # Real predecessor receipt must already cover the consumer window.
            if name in source_domains:
                source_domains[name] = (kwargs["start"], kwargs["end"])
            return {"row_count": 0}
        return run

    for step in plan:
        monkeypatch.setitem(handlers._SOURCE_HANDLERS, step.product, handler(step.product))
    monkeypatch.setattr(materialize, "_all_history_window", lambda: (date(2026, 1, 1), end))
    def rows(**_kwargs):
        result = []
        for step in plan:
            if step.product == "activity_content":
                result.append(audit._activity_content_dataset(cfg))
                continue
            lower, upper = source_domains.get(step.product, (date(2026, 1, 1), end))
            result.append(audit.MaterializedDataset(
                name=step.product, status="ready", authority="fixture", query_surface="fixture",
                materialized_paths=(), raw_roots=(), row_count=1, first_date=lower,
                last_date=upper - timedelta(days=1), materialization_hint="fixture", reason="fixture",
                covered_dates=tuple(lower + timedelta(days=offset) for offset in range((upper - lower).days)),
            ))
        return result

    def planning(**kwargs):
        if mode != "reuse":
            return plan
        if kwargs.get("maintenance"):
            # A fresh but too-narrow receipt can enter the initial plan as reuse.
            return [replace(step, action="skip", status="ready") if step.product != "temporal_signals" else step
                    for step in plan if step.product in {"activity_content", "personal_daily_signals", "temporal_signals"}]
        return production.plan_materializations(**kwargs)

    if mode == "reuse":
        monkeypatch.setattr(audit, "audit_materialization", rows)
        monkeypatch.setattr(audit, "get_config", lambda: cfg)
    monkeypatch.setattr(materialize, "plan_materializations", planning)
    monkeypatch.setattr(materialize, "audit_materialization", lambda: [])
    monkeypatch.setattr(materialize, "_record_incremental_phase", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(materialize, "_incremental_graph_tail_start", lambda *_args, **_kwargs: date(2026, 1, 5) if mode == "older_gap" else graph_start)
    monkeypatch.setattr(connection, "candidate_generation", lambda **_kwargs: nullcontext(generation))
    monkeypatch.setattr(connection, "connect", lambda *_args, **_kwargs: nullcontext(None))
    monkeypatch.setattr(connection, "generation_refresh_id", lambda *_args: generation.receipt_refresh_id)

    def snapshot(argv):
        assert argv[argv.index("--incremental-tail-start") + 1] == graph_start.isoformat()
        require_content()
        observed["graph"] = True
        return 0

    def typed(**kwargs):
        assert kwargs["partition_window_start"] == graph_start
        require_content()
        observed["typed"] = True
        return SimpleNamespace(status="ok", reason=None)

    monkeypatch.setattr(substrate_snapshot, "main", snapshot)
    monkeypatch.setattr(substrate_promote, "run_substrate_promote", typed)
    result = materialize.main(["--all", "--promote", "--history", "incremental", "--snapshot-output", str(tmp_path / "snapshot.md"), "--progress", "quiet"])
    if mode == "older_gap":
        assert result == 1
        assert observed == {}  # No hidden historical catch-up starts before the explicit repair.
    else:
        assert result == 0
        for name in ("activity_content", "personal_daily_signals"):
            assert (observed[name]["start"], observed[name]["end"]) == (graph_start, end)
        if mode == "materialize":
            assert (observed["activitywatch_event_index"]["start"], observed["activitywatch_event_index"]["end"]) == (source_start, end)
        else:
            # This dependency was absent from the maintenance plan and must run
            # before the newly required consumer, under the explicit window.
            assert (observed["activitywatch_derived"]["start"], observed["activitywatch_derived"]["end"]) == (graph_start, end)
        assert observed["graph"] and observed["typed"]
        if mode == "reuse":
            selected = output.resolve().parent
            observed.clear()
            assert materialize.main(["--all", "--promote", "--history", "incremental", "--snapshot-output", str(tmp_path / "repeat.md"), "--progress", "quiet"]) == 0
            assert "activity_content" not in observed and "personal_daily_signals" not in observed
            assert "activitywatch_derived" not in observed
            assert output.resolve().parent == selected
            assert observed["graph"] and observed["typed"]


@pytest.mark.parametrize("reuse_action", ["skip", "check-only"])
def test_graph_window_does_not_rebuild_a_verified_dependency_or_its_siblings(monkeypatch, reuse_action):
    from dataclasses import replace
    from lynchpin.cli import materialize
    from lynchpin.materializers import ConvergencePlanner, ConvergenceRequest
    from lynchpin.materializers.catalog import PRODUCT_CATALOG

    window = (date(2030, 1, 1), date(2030, 1, 8))
    steps = ConvergencePlanner(PRODUCT_CATALOG.values()).plan(
        ConvergenceRequest(("personal_daily_signals", "temporal_signals", "sleep_productivity"), window)
    ).steps
    products = {"activity_content", "personal_daily_signals", "title_metadata", "activitywatch_derived", "temporal_signals", "sleep_productivity"}
    initial = [replace(step, action="materialize" if step.product in {"activity_content", "personal_daily_signals", "title_metadata"} else reuse_action,
                       status="ready") for step in steps if step.product in products]
    monkeypatch.setattr(materialize, "plan_materializations", lambda **_kwargs: initial)
    aligned = materialize._align_incremental_product_windows(initial, window)
    writers = {step.product for step in aligned if step.action == "materialize"}
    assert writers == {"activity_content", "personal_daily_signals", "title_metadata"}
    assert {step.product: step.action for step in aligned}["activitywatch_derived"] == reuse_action


@pytest.mark.parametrize("native_action", ["check-only", "materialize"])
def test_missing_native_window_dependency_remains_nonwriting_and_blocks_its_consumer(monkeypatch, native_action):
    from dataclasses import replace
    from lynchpin import materialization as audit
    from lynchpin.cli import materialize
    from lynchpin.core.errors import MaterializationError
    from lynchpin.materializers import ConvergencePlanner, ConvergenceRequest, production
    from lynchpin.materializers.catalog import PRODUCT_CATALOG

    window = (date(2030, 1, 1), date(2030, 1, 8))
    steps = {step.product: step for step in ConvergencePlanner(PRODUCT_CATALOG.values()).plan(
        ConvergenceRequest(("personal_daily_signals",), window)
    ).steps}
    initial = [replace(steps[name], action="materialize" if name == "personal_daily_signals" else "check-only", status="ready")
               for name in ("personal_daily_signals", "activity_content", "activitywatch_derived", "title_metadata")]
    native = replace(steps["communications"], action=native_action, status="partial", reason="explicit import required")
    assert native.raw_read_permission != "none"
    monkeypatch.setattr(materialize, "plan_materializations", lambda **_kwargs: [*initial, native])
    aligned = materialize._align_incremental_product_windows(initial, window)
    assert next(step for step in aligned if step.product == "communications").action == "check-only"
    assert {step.product for step in aligned if step.action == "materialize"} == {"personal_daily_signals"}
    monkeypatch.setattr(audit, "_record_materialization_step", lambda *_args, **_kwargs: None)
    # The actual executor must see the unavailable prerequisite, rather than
    # silently ignoring the omitted native dependency and running its consumer.
    with pytest.raises(MaterializationError, match="dependency communications unavailable"):
        production.run_materialization_plan(aligned)


@pytest.mark.parametrize("baseline", ["ready", "missing", "older_gap"])
def test_incremental_preview_aligns_effective_windows_without_candidate_or_product_writes(monkeypatch, tmp_path, capsys, baseline):
    from contextlib import contextmanager
    from dataclasses import replace
    from lynchpin.cli import materialize
    from lynchpin.materializers import ConvergencePlanner, ConvergenceRequest
    from lynchpin.materializers.catalog import PRODUCT_CATALOG
    from lynchpin.substrate import connection

    end, graph_start = date(2026, 1, 15), date(2026, 1, 8)
    source_window = (date(2026, 1, 13), end)
    steps = ConvergencePlanner(PRODUCT_CATALOG.values()).plan(
        ConvergenceRequest(("personal_daily_signals", "temporal_signals"), source_window)
    ).steps
    plan = [replace(step, effective_window=(graph_start, end)) if step.product == "temporal_signals" else step for step in steps]
    from lynchpin.cli.substrate_snapshot import _snapshot_refresh_id
    published = _snapshot_refresh_id(start=date(2026, 1, 1), end=end, projects=())
    database = tmp_path / "serving.duckdb"
    with duckdb.connect(str(database)) as conn:
        connection.apply_schema(conn)
        if baseline != "missing":
            conn.execute("INSERT INTO evidence_graph_build "
                         "(refresh_id,start_date,end_date,mode,projects,node_count,edge_count,caveats,generated_at) "
                         "VALUES (?, ?, ?, 'materialized', [], 1, 0, '[]', now())",
                         [published, date(2026, 1, 1), date(2026, 1, 5) if baseline == "older_gap" else end])
            conn.execute("INSERT INTO substrate_promotion_run "
                         "(refresh_id,status,counts,started_at,finished_at) "
                         "VALUES (?, 'ok', '{}', now(), now())", [published])
            conn.execute("INSERT INTO substrate_source_status "
                         "(refresh_id,source,kind,status,reason,row_count,recorded_at) "
                         "VALUES (?, 'evidence_graph', 'graph', 'ok', NULL, 1, now())", [published])
    before = database.read_bytes()
    real_serving = connection.serving_generation
    reads = []

    @contextmanager
    def selected():
        with real_serving(database) as serving:
            reads.append(serving.database_path)
            yield serving

    monkeypatch.setattr(connection, "serving_generation", selected)
    monkeypatch.setattr(connection, "candidate_generation", lambda **_kwargs: pytest.fail("preview cloned a candidate"))
    monkeypatch.setattr(connection, "bootstrap_candidate_generation", lambda **_kwargs: pytest.fail("preview bootstrapped a candidate"))
    monkeypatch.setattr(materialize, "run_materialization_plan", lambda *_args, **_kwargs: pytest.fail("preview wrote products"))
    monkeypatch.setattr(materialize, "_all_history_window", lambda: (date(2026, 1, 1), end))
    monkeypatch.setattr(materialize, "plan_materializations", lambda **_kwargs: plan)
    result = materialize.main(["--all", "--promote", "--history", "incremental", "--plan-json", "--progress", "quiet"])
    captured = capsys.readouterr()
    assert reads == [database]
    assert database.read_bytes() == before
    if baseline == "ready":
        assert result == 0
        rendered = {step["product"]: step for step in json.loads(captured.out)}
        for name in ("activity_content", "personal_daily_signals"):
            assert rendered[name]["effective_window"] == {"start": graph_start.isoformat(), "end": end.isoformat()}
        assert rendered["activitywatch_event_index"]["effective_window"] == {"start": source_window[0].isoformat(), "end": source_window[1].isoformat()}
    else:
        assert result == 1
        assert captured.out == ""
        assert "incremental plan unavailable" in captured.err
        assert ("no compatible published graph predecessor" if baseline == "missing" else "explicit bounded catch-up") in captured.err
