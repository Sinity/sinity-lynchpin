from __future__ import annotations

from contextlib import nullcontext
from datetime import date
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
    finally:
        connection._substrate_path_override.reset(token)
    assert status == "degraded"
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
        lambda plan, window=None, continue_on_error=False: calls.update(
            continue_on_error=continue_on_error
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

    code = materialize.main(["--all", "--promote", "--history", "all"])

    assert code == 0
    assert calls["continue_on_error"] is True
    assert forwarded["argv"][:4] == [
        "--start",
        "2013-03-27",
        "--end",
        "2026-05-24",
    ]
    assert "--mode" not in forwarded["argv"]
    assert "--existing-products" not in forwarded["argv"]


def test_incremental_history_uses_per_step_tails_and_incremental_graph(monkeypatch, tmp_path: Path) -> None:
    from lynchpin.cli import materialize
    from lynchpin.materialization import MaterializationPlanStep, MaterializedDataset

    before = MaterializedDataset(
        name="activity_content",
        status="partial",
        authority="fixture",
        query_surface="fixture",
        materialized_paths=(),
        raw_roots=(),
        row_count=1,
        first_date=date(2026, 5, 1),
        last_date=date(2026, 5, 10),
        materialization_hint="refresh",
        reason="tail stale",
        tail_stale=True,
    )
    step = MaterializationPlanStep(
        name="activity_content",
        before=before,
        action="materialize",
        materialization_hint="refresh",
        reason="incremental tail",
        window=(date(2026, 5, 8), date(2026, 5, 12)),
    )
    rows = [
        MaterializedDataset(
            name="webhistory",
            status="ready",
            authority="fixture",
            query_surface="fixture",
            materialized_paths=(),
            raw_roots=(),
            row_count=1,
            first_date=date(2026, 5, 1),
            last_date=date(2026, 5, 12),
            materialization_hint="refresh",
            reason="ready",
        )
    ]
    forwarded: dict[str, list[str]] = {}
    calls: dict[str, object] = {}
    phase_order: list[str] = []
    phase_evidence: list[tuple[str, tuple[object, ...]]] = []

    monkeypatch.setattr(
        materialize,
        "plan_materializations",
        lambda **kwargs: calls.update(kwargs) or [step],
    )
    monkeypatch.setattr(
        materialize,
        "run_materialization_plan",
        lambda plan, window=None, continue_on_error=False, **kwargs: calls.update(
            run_window=window,
            continue_on_error=continue_on_error,
            run_kwargs=kwargs,
        ) or phase_order.append("source_reads") or list(plan),
    )
    monkeypatch.setattr(materialize, "audit_materialization", lambda: rows)
    candidate = SimpleNamespace(candidate=tmp_path / "candidate.duckdb", refresh_id="candidate")
    monkeypatch.setattr(
        "lynchpin.substrate.connection.candidate_generation",
        lambda **kwargs: calls.update(candidate_kwargs=kwargs) or nullcontext(candidate),
    )
    monkeypatch.setattr("lynchpin.substrate.connection.generation_refresh_id", lambda _path: "current")
    monkeypatch.setattr(
        materialize,
        "_record_incremental_phase",
        lambda _generation, measurement, *, metrics, **_kwargs: phase_evidence.append((measurement.phase, metrics)),
    )
    import lynchpin.cli.substrate_snapshot as snapshot

    monkeypatch.setattr(
        snapshot,
        "main",
        lambda argv: phase_order.append("graph_compute") or forwarded.setdefault("argv", argv) and 0,
    )
    monkeypatch.setenv("LYNCHPIN_LOCAL_ROOT", str(tmp_path))

    code = materialize.main(["--all", "--promote", "--history", "incremental"])

    assert code == 0
    assert calls["maintenance"] is True
    assert calls["run_window"] is None
    assert calls["run_kwargs"]["refresh_id"] == "current-state:2026-05-01:2026-05-13:all"
    assert calls["candidate_kwargs"]["receipt_refresh_id"] == "current-state:2026-05-01:2026-05-13:all"
    assert "--incremental-tail-start" in forwarded["argv"]
    tail_index = forwarded["argv"].index("--incremental-tail-start")
    assert forwarded["argv"][tail_index + 1] == "2026-05-08"
    assert "--existing-products" in forwarded["argv"]
    assert "--graph-only" in forwarded["argv"]
    assert phase_order == ["source_reads", "graph_compute"]
    assert [phase for phase, _metrics in phase_evidence] == [
        "source_reads",
        "graph_compute",
        "verification",
    ]
    assert phase_evidence[0][1][0]["unit"] == "steps"
    assert phase_evidence[1][1][0]["unit"] == "refreshes"
    assert phase_evidence[2][1][0]["unit"] == "datasets"


def test_incremental_history_reflinks_real_candidate_without_logical_reseed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Routine CLI maintenance reaches the real reflink candidate builder.

    The logical seed helper is deliberately fatal here.  This joins the CLI
    routing contract to the real candidate builder, rather than proving the
    two halves independently with a mocked candidate context.
    """
    from lynchpin.cli import materialize
    from lynchpin.materialization import MaterializationPlanStep, MaterializedDataset
    import lynchpin.substrate.connection as connection

    canonical = tmp_path / "duck" / "substrate.duckdb"
    canonical.parent.mkdir()
    monkeypatch.setattr(
        connection,
        "substrate_path",
        lambda: connection._substrate_path_override.get() or canonical,
    )
    monkeypatch.setattr(
        connection,
        "_filesystem_type",
        lambda _path: connection._BTRFS_SUPER_MAGIC,
    )
    monkeypatch.setattr(connection, "_file_flags", lambda _path: 0)
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

    before = MaterializedDataset(
        name="activity_content",
        status="partial",
        authority="fixture",
        query_surface="fixture",
        materialized_paths=(),
        raw_roots=(),
        row_count=1,
        first_date=date(2026, 5, 1),
        last_date=date(2026, 5, 12),
        materialization_hint="refresh",
        reason="tail stale",
        tail_stale=True,
    )
    step = MaterializationPlanStep(
        name="activity_content",
        before=before,
        action="materialize",
        materialization_hint="refresh",
        reason="incremental tail",
        window=(date(2026, 5, 8), date(2026, 5, 13)),
    )
    ready_rows = [
        MaterializedDataset(
            name="activity_content",
            status="ready",
            authority="fixture",
            query_surface="fixture",
            materialized_paths=(),
            raw_roots=(),
            row_count=1,
            first_date=date(2026, 5, 1),
            last_date=date(2026, 5, 12),
            materialization_hint="refresh",
            reason="ready",
        )
    ]
    monkeypatch.setattr(materialize, "plan_materializations", lambda **_kwargs: [step])
    monkeypatch.setattr(materialize, "run_materialization_plan", lambda *_args, **_kwargs: [step])
    monkeypatch.setattr(materialize, "audit_materialization", lambda: ready_rows)
    monkeypatch.setattr(
        connection,
        "_logical_index_rebuild_seed",
        lambda *_args: (_ for _ in ()).throw(AssertionError("routine CLI must not reseed logical rows")),
    )

    from lynchpin.cli import substrate_snapshot

    def fake_snapshot(argv: list[str]) -> int:
        start = date.fromisoformat(argv[argv.index("--start") + 1])
        end = date.fromisoformat(argv[argv.index("--end") + 1])
        refresh_id = substrate_snapshot._snapshot_refresh_id(start=start, end=end, projects=())
        with connection.connect() as conn:
            conn.execute(
                """
                INSERT INTO evidence_graph_build
                (refresh_id, start_date, end_date, mode, projects, node_count, edge_count, caveats, generated_at)
                VALUES (?, ?, ?, 'materialized', [], 0, 0, '[]', now())
                """,
                [refresh_id, start, end],
            )
            conn.execute(
                """
                INSERT INTO substrate_source_status
                (refresh_id, source, kind, status, reason, row_count, window_start, window_end, recorded_at)
                VALUES (?, 'evidence_graph', 'graph', 'ok', NULL, 0, ?, ?, now())
                """,
                [refresh_id, start, end],
            )
            conn.execute(
                """
                INSERT INTO substrate_promotion_run
                (refresh_id, status, reason, window_start, window_end, mode, counts, started_at, finished_at)
                VALUES (?, 'ok', NULL, ?, ?, 'test', '{}', now(), now())
                """,
                [refresh_id, start, end],
            )
        return 0

    monkeypatch.setattr(substrate_snapshot, "main", fake_snapshot)

    assert materialize.main(["--all", "--promote", "--history", "incremental", "--progress", "quiet"]) == 0

    with duckdb.connect(str(canonical), read_only=True) as conn:
        message = conn.execute(
            """
            SELECT message FROM substrate_run_step
            WHERE step = 'candidate_attempt_evidence'
            ORDER BY recorded_at DESC
            LIMIT 1
            """
        ).fetchone()[0]
    assert json.loads(message)["candidate_seed"]["mode"] == "reflink"
    assert json.loads(message)["candidate_seed"]["logical_rows_reconstructed"] == 0


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
    assert list(candidate_path.parent.glob(f"{candidate_path.name}.cancelled-*"))
    assert list(candidate_path.parent.glob(f"{candidate_path.name}.wal.cancelled-*"))
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


def test_maintenance_plan_never_dispatches_unscoped_materializers(monkeypatch) -> None:
    from lynchpin import materialization

    windowed = materialization.MaterializedDataset(
        name="windowed",
        status="partial",
        authority="fixture",
        query_surface="fixture",
        materialized_paths=(),
        raw_roots=(),
        row_count=1,
        first_date=date(2026, 5, 1),
        last_date=date(2026, 5, 10),
        materialization_hint="refresh",
        reason="tail stale",
    )
    stale_windowed = materialization.MaterializedDataset(
        name="stale_windowed",
        status="partial",
        authority="fixture",
        query_surface="fixture",
        materialized_paths=(),
        raw_roots=(),
        row_count=1,
        first_date=date(2026, 1, 1),
        last_date=date(2026, 3, 1),
        materialization_hint="repair",
        reason="old product",
    )
    unwindowed = materialization.MaterializedDataset(
        name="unwindowed",
        status="partial",
        authority="fixture",
        query_surface="fixture",
        materialized_paths=(),
        raw_roots=(),
        row_count=1,
        first_date=date(2026, 5, 1),
        last_date=date(2026, 5, 10),
        materialization_hint="repair",
        reason="requires repair",
    )
    monkeypatch.setattr(materialization, "audit_materialization", lambda cfg=None: [windowed, stale_windowed, unwindowed])
    monkeypatch.setattr(materialization, "source_contract", lambda name: SimpleNamespace(materialization_hint=name))
    monkeypatch.setattr(
        materialization,
        "_materializers",
        lambda: {
            "windowed": lambda *, start=None, end=None: {"start": start, "end": end},
            "stale_windowed": lambda *, start=None, end=None: {"start": start, "end": end},
            "unwindowed": lambda: None,
        },
    )

    plan = materialization.plan_materializations(
        maintenance=True,
        maintenance_end=date(2026, 5, 12),
    )

    by_name = {step.name: step for step in plan}
    assert by_name["windowed"].action == "materialize"
    assert by_name["windowed"].window == (date(2026, 5, 5), date(2026, 5, 12))
    assert by_name["stale_windowed"].action == "check-only"
    assert "catch-up" in by_name["stale_windowed"].reason
    assert by_name["unwindowed"].action == "check-only"
    assert by_name["unwindowed"].window is None


def test_materializer_dependency_model_requires_one_writer_wave() -> None:
    from lynchpin.materialization import (
        MaterializationPlanStep,
        MaterializedDataset,
        materializer_dependency_model,
        materializer_execution_waves,
    )

    before = MaterializedDataset(
        name="fixture",
        status="partial",
        authority="fixture",
        query_surface="fixture",
        materialized_paths=(),
        raw_roots=(),
        row_count=1,
        first_date=date(2026, 5, 1),
        last_date=date(2026, 5, 2),
        materialization_hint="refresh",
        reason="tail stale",
    )
    plan = (
        MaterializationPlanStep("one", before, "materialize", "refresh", "one"),
        MaterializationPlanStep("two", before, "materialize", "refresh", "two"),
    )

    model = materializer_dependency_model(plan)
    waves = materializer_execution_waves(model)

    assert all(dependency.writes.isdisjoint(dependency.reads) for dependency in model)
    assert [[dependency.name for dependency in wave] for wave in waves] == [["one", "two"]]


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


def test_promote_continues_after_one_materializer_failure(monkeypatch) -> None:
    from lynchpin import materialization
    from threading import Barrier

    before = materialization.MaterializedDataset(
        name="fixture",
        status="ready",
        authority="fixture",
        query_surface="fixture",
        materialized_paths=(),
        raw_roots=(),
        row_count=1,
        first_date=None,
        last_date=None,
        materialization_hint="refresh",
        reason="ready",
    )
    steps = [
        materialization.MaterializationPlanStep(
            "broken", before, "materialize", "refresh", "fixture"
        ),
        materialization.MaterializationPlanStep(
            "healthy", before, "materialize", "refresh", "fixture"
        ),
    ]
    events: list[tuple[str, str]] = []
    barrier = Barrier(2)
    def broken():
        barrier.wait(timeout=2)
        raise RuntimeError("repomix unavailable")

    def healthy():
        barrier.wait(timeout=2)
        return {"row_count": 4}

    monkeypatch.setattr(
        materialization,
        "_materializers",
        lambda: {
            "broken": broken,
            "healthy": healthy,
        },
    )
    monkeypatch.setattr(
        materialization,
        "_record_materialization_step",
        lambda _refresh, name, status, *_args, **_kwargs: events.append((name, status)),
    )

    ran = materialization.run_materialization_plan(steps, continue_on_error=True)

    assert [step.name for step in ran] == ["healthy"]
    assert sorted(events) == sorted([
        ("broken", "started"),
        ("broken", "error"),
        ("healthy", "started"),
        ("healthy", "ok"),
    ])


def test_snapshot_daily_signals_ensures_products_before_promoting(monkeypatch) -> None:
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

    def fake_iter_activity_content_days(*, start=None, end=None, ensure=True):
        read_windows.append(("activity_content_days", start, end, ensure))
        return iter(())

    def fake_iter_activity_title_usage(*, start=None, end=None, ensure=True):
        read_windows.append(("activity_title_usage", start, end, ensure))
        return iter(())

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)
    monkeypatch.setattr(
        "lynchpin.sources.personal_signals.iter_personal_daily_signals",
        fake_iter_personal_daily_signals,
    )
    monkeypatch.setattr("lynchpin.sources.activity_content.iter_activity_content_days", fake_iter_activity_content_days)
    monkeypatch.setattr("lynchpin.sources.activity_content.iter_activity_title_usage", fake_iter_activity_title_usage)
    monkeypatch.setattr("lynchpin.sources.title_metadata.title_metadata_path", lambda: Path("fixture.duckdb"))
    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: Path("fixture.duckdb"))
    monkeypatch.setattr("lynchpin.substrate.connection.apply_schema", lambda _conn: None)
    monkeypatch.setattr("lynchpin.substrate.connection.connect", lambda *_args, **_kwargs: Connect())
    monkeypatch.setattr(
        "lynchpin.substrate.personal.promote_title_classifications_from_path",
        lambda *_args, **_kwargs: 0,
    )
    monkeypatch.setattr("lynchpin.substrate.personal.promote_activity_content_days", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr("lynchpin.substrate.personal.promote_activity_content_buckets", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr("lynchpin.substrate.personal.promote_activity_title_usage", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        "lynchpin.substrate.personal.promote_personal_daily_signals",
        lambda _conn, *, rows, **_kwargs: len(list(rows)),
    )
    monkeypatch.setattr(
        "lynchpin.analysis.active.substrate_promote_status.record_source_status",
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
        ("activity_title_usage", date(2026, 5, 1), date(2026, 5, 2), False),
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
    assert materialized == {
        "start": date(2026, 5, 1),
        "end": date(2026, 5, 2),
        "projects": (),
    }
    assert promoted["ensure_products"] is False


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
    assert promoted["incremental_tail_start"] == date(2026, 5, 8)
