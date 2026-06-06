"""Tests for the materialization-DAG substrate promotion step (Arc 2.6).

Covers:
- missing JSON files: run returns {} without raising
- commit facts JSON: hydrated and promoted to substrate
- file change facts JSON: hydrated and promoted to substrate
- symbol changes JSON: promoted to substrate
- idempotence: same refresh_id produces same row count, not 2x
- partition isolation: two refresh_ids both present
- run_substrate_promote records errors before re-raising
"""

from __future__ import annotations

import json
import pytest
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

UTC = timezone.utc


@pytest.fixture(autouse=True)
def _isolate_live_polylogue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Substrate-promotion unit tests must not ingest live personal archives."""
    monkeypatch.setattr("lynchpin.sources.polylogue.work_events", lambda *args, **kwargs: [])
    monkeypatch.setattr("lynchpin.sources.polylogue_devtools.available", lambda *args, **kwargs: False)
    monkeypatch.setattr("lynchpin.sources.polylogue_devtools.iter_invocations", lambda *args, **kwargs: iter(()))
    monkeypatch.setattr("lynchpin.sources.spotify.iter_streams", lambda *args, **kwargs: iter(()))
    monkeypatch.setattr("lynchpin.sources.machine.gpu_samples", lambda *args, **kwargs: iter(()))
    monkeypatch.setattr("lynchpin.sources.machine.metric_samples", lambda *args, **kwargs: iter(()))
    monkeypatch.setattr("lynchpin.sources.machine.network_samples", lambda *args, **kwargs: iter(()))
    monkeypatch.setattr(
        "lynchpin.sources.machine.readiness",
        lambda *args, **kwargs: SimpleNamespace(
            status="ready",
            reason="isolated test fixture",
        ),
    )
    monkeypatch.setattr("lynchpin.sources.machine.service_states", lambda *args, **kwargs: iter(()))


def _reload_config(monkeypatch: pytest.MonkeyPatch | None = None) -> None:
    """Clear cached config so env-var monkeypatches take effect.

    If monkeypatch is provided, also registers cleanup to clear the cache
    after the test so the next test starts fresh.
    """
    import lynchpin.core.config as cfg_mod
    cfg_mod._CONFIG = None  # clear the global cache without reload
    if monkeypatch is not None:
        monkeypatch.setattr(cfg_mod, "_CONFIG", None, raising=False)


def _dt(y: int, m: int, d: int, h: int = 12) -> datetime:
    return datetime(y, m, d, h, 0, 0, tzinfo=UTC)


def test_machine_sqlite_window_filter_uses_half_open_text_range() -> None:
    from lynchpin.analysis.active.substrate_promote_machine import _source_window_filter

    sql, params = _source_window_filter(date(2026, 5, 1), date(2026, 5, 3))

    assert sql == "WHERE observed_at >= ? AND observed_at < ?"
    assert "CAST" not in sql
    assert params == ["2026-05-01", "2026-05-04"]


def test_machine_experiment_promotion_enriches_manifest_validation(tmp_path: Path) -> None:
    from lynchpin.analysis.active.substrate_promote_machine import (
        _validated_experiment_runs,
    )
    from lynchpin.sources.machine_experiments import experiment_runs

    run_dir = tmp_path / "run-1"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(json.dumps({
        "run_id": "run-1",
        "run_group_id": "grp1",
        "host": "sinnix-prime",
        "workload": "sinex.xtask",
        "command": ["xtask", "test"],
        "started_at": "2026-05-12T12:00:00+00:00",
        "ended_at": "2026-05-12T12:01:00+00:00",
        "monotonic_started_ns": 1,
        "monotonic_ended_ns": 60_000_000_000,
        "exit_status": 0,
        "execution_outcome": {"status": "success"},
        "measurement_context": {"host_boot_id": "boot1"},
        "planned_treatment": {"selected_run": {"run_id": "run-1"}},
        "nix_internal_json_path": "/tmp/run-1/nix-internal-json.ndjson",
        "git": {
            "root": "/realm/project/sinex",
            "head": "abc123",
            "branch": "master",
            "dirty": False,
        },
        "pre_state": {},
        "post_state": {},
    }))

    source_rows = list(experiment_runs(root=tmp_path))
    validated = _validated_experiment_runs(source_rows)

    assert source_rows[0].validation_status == "unvalidated"
    assert validated[0].validation_status == "invalid"
    assert validated[0].manifest_validation["valid"] is False
    assert "missing internal-json path" not in validated[0].validation_warnings
    assert "measurement_context.system_generation missing" in validated[0].validation_issues
    assert (
        "planned_treatment not controlled-ready: missing fixed derivation set"
        in validated[0].validation_warnings
    )


# ── helpers ──────────────────────────────────────────────────────────────────


def _json_sources() -> set[str]:
    from lynchpin.analysis.active.substrate_promote import (
        SOURCE_COMMITS,
        SOURCE_FILE_CHANGES,
        SOURCE_SYMBOLS,
    )

    return {SOURCE_COMMITS, SOURCE_FILE_CHANGES, SOURCE_SYMBOLS}


def _json_pr_sources() -> set[str]:
    from lynchpin.analysis.active.substrate_promote import SOURCE_PR_REVIEW

    return _json_sources() | {SOURCE_PR_REVIEW}


def _make_commit_facts_payload(commits: list[dict]) -> dict:
    return {
        "generated_at_utc": "2026-05-08T00:00:00+00:00",
        "commits": commits,
    }


def _make_commit_entry(
    sha: str,
    project: str = "lynchpin",
    author: str = "Sinity",
    timestamp: str = "2026-05-01T12:00:00+00:00",
    subject: str = "feat: test commit",
    files_changed: int = 2,
    paths: list[str] | None = None,
    path_roots: dict | None = None,
) -> dict:
    return {
        "sha": sha,
        "short_sha": sha[:7],
        "project": project,
        "author": author,
        "timestamp": timestamp,
        "date": timestamp[:10],
        "subject": subject,
        "parent_count": 1,
        "default_branch": "master",
        "head": None,
        "conventional_kind": "feat",
        "conventional_scope": None,
        "conventional_signature": "feat",
        "conventional_description": "test commit",
        "breaking_change": False,
        "github_refs": {"prs": [], "issues": []},
        "files_changed": files_changed,
        "classified_files_changed": files_changed,
        "categories": {},
        "path_roots": path_roots or {"src": 2},
        "change_types": {"modified": files_changed},
        "paths": paths or ["src/foo.py", "src/bar.py"],
    }


def _make_file_change_payload(file_changes: list[dict]) -> dict:
    return {
        "generated_at_utc": "2026-05-08T00:00:00+00:00",
        "file_changes": file_changes,
    }


def _make_file_change_entry(
    sha: str,
    path: str = "src/foo.py",
    project: str = "lynchpin",
    timestamp: str = "2026-05-01T12:00:00+00:00",
    path_root: str = "src",
    change_type: str = "modified",
) -> dict:
    return {
        "sha": sha,
        "short_sha": sha[:7],
        "project": project,
        "timestamp": timestamp,
        "date": timestamp[:10],
        "subject": "feat: test",
        "default_branch": "master",
        "path": path,
        "previous_path": None,
        "path_root": path_root,
        "category": None,
        "classified": False,
        "status_code": "M",
        "change_type": change_type,
        "conventional_kind": "feat",
        "conventional_scope": None,
        "conventional_signature": "feat",
        "github_refs": {"prs": [], "issues": []},
    }


def _make_symbol_changes_payload(events: list[dict]) -> dict:
    return {"events": events}


def _make_symbol_entry(sha: str, qualified_name: str) -> dict:
    return {
        "sha": sha,
        "project": "lynchpin",
        "date": "2026-05-01",
        "path": "lynchpin/core/config.py",
        "change_type": "M",
        "qualified_name": qualified_name,
        "symbol_kind": "function",
        "exported": True,
        "breaking_candidate": False,
    }


# ── error-resilience tests ───────────────────────────────────────────────────


def test_substrate_promote_handles_missing_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run with non-existent JSON paths — must NOT raise; returns result object.

    The test isolates the JSON-backed source family so it does not spend time
    probing live archive/readiness sources unrelated to the assertion.
    """
    monkeypatch.setenv("LYNCHPIN_LOCAL_ROOT", str(tmp_path))
    _reload_config(monkeypatch)

    import lynchpin.sources.polylogue as polylogue_src
    monkeypatch.setattr(polylogue_src, "work_events", lambda *a, **kw: [])

    from lynchpin.analysis.active.substrate_promote import run_substrate_promote

    counts = run_substrate_promote(
        commit_facts_file=str(tmp_path / "nonexistent_commits.json"),
        file_changes_file=str(tmp_path / "nonexistent_fc.json"),
        symbol_changes_file=str(tmp_path / "nonexistent_sym.json"),
        sources=_json_sources(),
        write_evidence_graph=False,
    )
    # Must return structured status and not raise.
    assert counts.status == "degraded"
    # JSON-based sources all missing → counts should all be 0 (or absent)
    assert counts.get("commits", 0) == 0
    assert counts.get("file_changes", 0) == 0
    assert counts.get("symbols", 0) == 0


def test_substrate_promote_raises_infrastructure_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken substrate path must raise so materialization cannot look successful."""
    import lynchpin.substrate.connection as duck_conn

    # Point substrate to an unwriteable path.
    unwriteable = tmp_path / "no_such_dir" / "substrate.duckdb"
    monkeypatch.setattr(duck_conn, "substrate_path", lambda: unwriteable)

    from lynchpin.analysis.active.substrate_promote import run_substrate_promote

    with pytest.raises(Exception, match="Cannot open file"):
        run_substrate_promote(
            commit_facts_file=str(tmp_path / "nope.json"),
            file_changes_file=str(tmp_path / "nope.json"),
            symbol_changes_file=str(tmp_path / "nope.json"),
            sources=_json_sources(),
            write_evidence_graph=False,
        )


def test_substrate_promote_uses_derived_personal_products(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LYNCHPIN_LOCAL_ROOT", str(tmp_path / "local"))
    monkeypatch.setenv("LYNCHPIN_DERIVED_ROOT", str(tmp_path / "derived"))
    _reload_config(monkeypatch)
    personal = tmp_path / "derived" / "personal"
    spotify = tmp_path / "derived" / "spotify"
    personal.mkdir(parents=True)
    spotify.mkdir(parents=True)
    (personal / "daily_signals.ndjson").write_text(
        '{"source":"webhistory","date":"2026-05-01","metric":"visit_count","value":3,"dimensions":{"top_domain":"example.com"}}\n',
        encoding="utf-8",
    )
    (spotify / "daily.ndjson").write_text(
        '{"date":"2026-05-01","track_count":2,"minutes_played":5.5,"unique_artists":1,"unique_tracks":2,"top_artists":["A"],"top_tracks":["T"]}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "lynchpin.sources.spotify.iter_streams",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("raw streams must not be read")),
    )
    monkeypatch.setattr(
        "lynchpin.analysis.operator_daily.operator_daily_matrix",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("targeted promote must not build operator_day")),
    )
    ensure_calls = []

    def fake_ensure_materialized(name: str, *, window=None):
        ensure_calls.append((name, window))
        return SimpleNamespace(status="ready", reason="ready")

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)

    from lynchpin.analysis.active.substrate_promote import run_substrate_promote
    from lynchpin.analysis.active.substrate_promote_status import (
        SOURCE_PERSONAL_DAILY_SIGNAL,
        SOURCE_SPOTIFY_DAILY,
    )
    from lynchpin.substrate.connection import connect, substrate_path

    result = run_substrate_promote(
        commit_facts_file=str(tmp_path / "missing.json"),
        file_changes_file=str(tmp_path / "missing.json"),
        symbol_changes_file=str(tmp_path / "missing.json"),
        sources={SOURCE_PERSONAL_DAILY_SIGNAL, SOURCE_SPOTIFY_DAILY},
        refresh_id="fixture",
        write_evidence_graph=False,
        window_start=date(2026, 5, 1),
        window_end=date(2026, 5, 2),
    )

    assert result.status == "ok"
    assert ensure_calls == [
        ("spotify_daily", (date(2026, 5, 1), date(2026, 5, 2))),
        ("personal_daily_signals", (date(2026, 5, 1), date(2026, 5, 2))),
    ]
    assert result.counts["personal_daily_signal"] == 1
    assert result.counts["spotify_daily"] == 1
    with connect(substrate_path(), read_only=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM personal_daily_signal").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM spotify_daily").fetchone()[0] == 1


# ── commit facts hydration + promotion ───────────────────────────────────────


def test_substrate_promote_loads_commit_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Write active_commit_facts.json, run promote, assert 3 rows in substrate."""
    monkeypatch.setenv("LYNCHPIN_LOCAL_ROOT", str(tmp_path))
    _reload_config(monkeypatch)

    cf_file = tmp_path / "commit_facts.json"
    cf_file.write_text(json.dumps(_make_commit_facts_payload([
        _make_commit_entry("abc" + "0" * 37),
        _make_commit_entry("def" + "0" * 37, project="sinex"),
        _make_commit_entry("ghi" + "0" * 37, project="polylogue"),
    ])))
    fc_file = tmp_path / "fc.json"
    fc_file.write_text(json.dumps(_make_file_change_payload([])))
    sym_file = tmp_path / "sym.json"
    sym_file.write_text(json.dumps(_make_symbol_changes_payload([])))

    from lynchpin.analysis.active.substrate_promote import run_substrate_promote
    from lynchpin.substrate.connection import apply_schema, connect, substrate_path

    counts = run_substrate_promote(
        commit_facts_file=str(cf_file),
        file_changes_file=str(fc_file),
        symbol_changes_file=str(sym_file),
        sources=_json_sources(),
        write_evidence_graph=False,
    )

    assert counts.get("commits") == 3

    # Verify the rows landed in DuckDB.
    with connect(substrate_path()) as conn:
        apply_schema(conn)
        total = conn.execute("SELECT COUNT(*) FROM commit_fact").fetchone()[0]
    assert total == 3


def test_substrate_promote_records_stage_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A normal promote records durable stage progress rows with row deltas."""
    monkeypatch.setenv("LYNCHPIN_LOCAL_ROOT", str(tmp_path))
    _reload_config(monkeypatch)

    sha = "stg" + "0" * 37
    cf_file = tmp_path / "commit_facts.json"
    cf_file.write_text(json.dumps(_make_commit_facts_payload([
        _make_commit_entry(sha),
    ])))
    fc_file = tmp_path / "fc.json"
    fc_file.write_text(json.dumps(_make_file_change_payload([])))
    sym_file = tmp_path / "sym.json"
    sym_file.write_text(json.dumps(_make_symbol_changes_payload([])))

    from lynchpin.analysis.active.substrate_promote import (
        SOURCE_COMMITS,
        run_substrate_promote,
    )
    from lynchpin.substrate.connection import apply_schema, connect, substrate_path

    refresh_id = "dag:test-stage-steps"
    run_substrate_promote(
        commit_facts_file=str(cf_file),
        file_changes_file=str(fc_file),
        symbol_changes_file=str(sym_file),
        refresh_id=refresh_id,
        sources={SOURCE_COMMITS},
        write_evidence_graph=False,
    )

    with connect(substrate_path()) as conn:
        apply_schema(conn)
        rows = conn.execute(
            """
            SELECT step, status, message, row_count, started_at, finished_at
            FROM substrate_run_step
            WHERE refresh_id = ? AND step = 'promote_artifacts'
            ORDER BY rowid
            """,
            [refresh_id],
        ).fetchall()

    assert [(row[0], row[1], row[2], row[3]) for row in rows] == [
        ("promote_artifacts", "running", "started", None),
        ("promote_artifacts", "success", "finished", 1),
    ]
    assert rows[0][4] is not None
    assert rows[0][5] is None
    assert rows[1][4] is not None
    assert rows[1][5] is not None


def test_substrate_promote_records_stage_error_before_reraising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing stage leaves a run-step error row for post-mortem inspection."""
    monkeypatch.setenv("LYNCHPIN_LOCAL_ROOT", str(tmp_path))
    _reload_config(monkeypatch)

    from lynchpin.analysis.active import substrate_promote
    from lynchpin.substrate.connection import apply_schema, connect, substrate_path

    def fail_promote_artifacts(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("stage exploded")

    monkeypatch.setattr(substrate_promote, "promote_artifact_sources", fail_promote_artifacts)
    refresh_id = "dag:test-stage-error"

    with pytest.raises(RuntimeError, match="stage exploded"):
        substrate_promote.run_substrate_promote(
            commit_facts_file=str(tmp_path / "commit_facts.json"),
            file_changes_file=str(tmp_path / "fc.json"),
            symbol_changes_file=str(tmp_path / "sym.json"),
            refresh_id=refresh_id,
            sources={substrate_promote.SOURCE_COMMITS},
            write_evidence_graph=False,
        )

    with connect(substrate_path()) as conn:
        apply_schema(conn)
        rows = conn.execute(
            """
            SELECT step, status, message, row_count, finished_at
            FROM substrate_run_step
            WHERE refresh_id = ? AND step = 'promote_artifacts'
            ORDER BY rowid
            """,
            [refresh_id],
        ).fetchall()

    assert [(row[0], row[1], row[2], row[3]) for row in rows] == [
        ("promote_artifacts", "running", "started", None),
        ("promote_artifacts", "error", "RuntimeError: stage exploded", 0),
    ]
    assert rows[0][4] is None
    assert rows[1][4] is not None


def test_substrate_promote_merges_active_ai_attribution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """High/medium active_ai_attribution rows land in commit_fact.ai_attribution."""
    monkeypatch.setenv("LYNCHPIN_LOCAL_ROOT", str(tmp_path))
    _reload_config(monkeypatch)

    sha_medium = "abc" + "0" * 37
    sha_none = "def" + "0" * 37
    cf_file = tmp_path / "commit_facts.json"
    cf_file.write_text(json.dumps(_make_commit_facts_payload([
        _make_commit_entry(sha_medium),
        _make_commit_entry(sha_none),
    ])))
    ai_file = tmp_path / "ai.json"
    ai_file.write_text(json.dumps({
        "commits": [
            {
                "sha": sha_medium,
                "ai_attribution": "medium",
                "supporting_session_ids": ["s1"],
                "supporting_providers": ["claude-code"],
                "supporting_session_count": 1,
            },
            {
                "sha": sha_none,
                "ai_attribution": "none",
                "supporting_session_ids": [],
                "supporting_providers": [],
                "supporting_session_count": 0,
            },
        ]
    }))
    fc_file = tmp_path / "fc.json"
    fc_file.write_text(json.dumps(_make_file_change_payload([])))
    sym_file = tmp_path / "sym.json"
    sym_file.write_text(json.dumps(_make_symbol_changes_payload([])))

    from lynchpin.analysis.active.substrate_promote import run_substrate_promote
    from lynchpin.substrate.connection import apply_schema, connect, substrate_path

    run_substrate_promote(
        commit_facts_file=str(cf_file),
        file_changes_file=str(fc_file),
        symbol_changes_file=str(sym_file),
        ai_attribution_file=str(ai_file),
        refresh_id="r-ai",
        sources=_json_sources(),
        write_evidence_graph=False,
    )

    with connect(substrate_path()) as conn:
        apply_schema(conn)
        rows = conn.execute(
            """
            SELECT sha, CAST(ai_attribution AS VARCHAR)
            FROM commit_fact
            WHERE refresh_id = 'r-ai'
            ORDER BY sha
            """
        ).fetchall()

    by_sha = {sha: value for sha, value in rows}
    assert '"classification": "medium"' in by_sha[sha_medium]
    assert by_sha[sha_none] is None


def test_substrate_promote_loads_file_change_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Write active_file_change_facts.json, run promote, assert rows in substrate."""
    monkeypatch.setenv("LYNCHPIN_LOCAL_ROOT", str(tmp_path))
    _reload_config(monkeypatch)

    sha = "abc" + "0" * 37
    cf_file = tmp_path / "commit_facts.json"
    cf_file.write_text(json.dumps(_make_commit_facts_payload([])))
    fc_file = tmp_path / "fc.json"
    fc_file.write_text(json.dumps(_make_file_change_payload([
        _make_file_change_entry(sha, path="src/a.py"),
        _make_file_change_entry(sha, path="src/b.py"),
    ])))
    sym_file = tmp_path / "sym.json"
    sym_file.write_text(json.dumps(_make_symbol_changes_payload([])))

    from lynchpin.analysis.active.substrate_promote import run_substrate_promote
    from lynchpin.substrate.connection import apply_schema, connect, substrate_path

    counts = run_substrate_promote(
        commit_facts_file=str(cf_file),
        file_changes_file=str(fc_file),
        symbol_changes_file=str(sym_file),
        sources=_json_sources(),
        write_evidence_graph=False,
    )

    assert counts.get("file_changes") == 2

    with connect(substrate_path()) as conn:
        apply_schema(conn)
        total = conn.execute("SELECT COUNT(*) FROM file_change_fact").fetchone()[0]
    assert total == 2


def test_substrate_promote_loads_symbol_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Write active_symbol_changes.json, run promote, assert rows in substrate."""
    monkeypatch.setenv("LYNCHPIN_LOCAL_ROOT", str(tmp_path))
    _reload_config(monkeypatch)

    cf_file = tmp_path / "commit_facts.json"
    cf_file.write_text(json.dumps(_make_commit_facts_payload([])))
    fc_file = tmp_path / "fc.json"
    fc_file.write_text(json.dumps(_make_file_change_payload([])))
    sym_file = tmp_path / "sym.json"
    sym_file.write_text(json.dumps(_make_symbol_changes_payload([
        _make_symbol_entry("sha001" + "0" * 34, "lynchpin.core.config.get_config"),
        _make_symbol_entry("sha001" + "0" * 34, "lynchpin.core.config.LynchpinConfig"),
    ])))

    from lynchpin.analysis.active.substrate_promote import run_substrate_promote
    from lynchpin.substrate.connection import apply_schema, connect, substrate_path

    counts = run_substrate_promote(
        commit_facts_file=str(cf_file),
        file_changes_file=str(fc_file),
        symbol_changes_file=str(sym_file),
        sources=_json_sources(),
        write_evidence_graph=False,
    )

    assert counts.get("symbols") == 2

    with connect(substrate_path()) as conn:
        apply_schema(conn)
        total = conn.execute("SELECT COUNT(*) FROM symbol_change").fetchone()[0]
    assert total == 2


# ── idempotence and partition isolation ──────────────────────────────────────


def test_substrate_promote_idempotent_same_refresh_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run twice with the same refresh_id — substrate has same row count, not 2x."""
    monkeypatch.setenv("LYNCHPIN_LOCAL_ROOT", str(tmp_path))
    _reload_config(monkeypatch)

    sha = "abc" + "0" * 37
    cf_file = tmp_path / "commit_facts.json"
    cf_file.write_text(json.dumps(_make_commit_facts_payload([
        _make_commit_entry(sha),
    ])))
    fc_file = tmp_path / "fc.json"
    fc_file.write_text(json.dumps(_make_file_change_payload([])))
    sym_file = tmp_path / "sym.json"
    sym_file.write_text(json.dumps(_make_symbol_changes_payload([])))

    from lynchpin.analysis.active.substrate_promote import run_substrate_promote
    from lynchpin.substrate.connection import apply_schema, connect, substrate_path

    rid = "dag:test-idempotent"

    run_substrate_promote(
        commit_facts_file=str(cf_file),
        file_changes_file=str(fc_file),
        symbol_changes_file=str(sym_file),
        refresh_id=rid,
        sources=_json_sources(),
        write_evidence_graph=False,
    )
    run_substrate_promote(
        commit_facts_file=str(cf_file),
        file_changes_file=str(fc_file),
        symbol_changes_file=str(sym_file),
        refresh_id=rid,
        sources=_json_sources(),
        write_evidence_graph=False,
    )

    with connect(substrate_path()) as conn:
        apply_schema(conn)
        count = conn.execute("SELECT COUNT(*) FROM commit_fact").fetchone()[0]

    assert count == 1  # not 2 — idempotent on refresh_id


def test_substrate_promote_isolated_per_refresh_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two runs with different refresh_ids — both partitions present, total = 2."""
    monkeypatch.setenv("LYNCHPIN_LOCAL_ROOT", str(tmp_path))
    _reload_config(monkeypatch)

    sha_a = "aaa" + "0" * 37
    sha_b = "bbb" + "0" * 37

    def _write_commit(sha: str) -> tuple[str, str, str]:
        cf = tmp_path / f"cf_{sha[:3]}.json"
        cf.write_text(json.dumps(_make_commit_facts_payload([_make_commit_entry(sha)])))
        fc = tmp_path / f"fc_{sha[:3]}.json"
        fc.write_text(json.dumps(_make_file_change_payload([])))
        sym = tmp_path / f"sym_{sha[:3]}.json"
        sym.write_text(json.dumps(_make_symbol_changes_payload([])))
        return str(cf), str(fc), str(sym)

    cf_a, fc_a, sym_a = _write_commit(sha_a)
    cf_b, fc_b, sym_b = _write_commit(sha_b)

    from lynchpin.analysis.active.substrate_promote import run_substrate_promote
    from lynchpin.substrate.connection import apply_schema, connect, substrate_path

    run_substrate_promote(
        commit_facts_file=cf_a,
        file_changes_file=fc_a,
        symbol_changes_file=sym_a,
        refresh_id="dag:run-1",
        sources=_json_sources(),
        write_evidence_graph=False,
    )
    run_substrate_promote(
        commit_facts_file=cf_b,
        file_changes_file=fc_b,
        symbol_changes_file=sym_b,
        refresh_id="dag:run-2",
        sources=_json_sources(),
        write_evidence_graph=False,
    )

    with connect(substrate_path()) as conn:
        apply_schema(conn)
        total = conn.execute("SELECT COUNT(*) FROM commit_fact").fetchone()[0]
        r1 = conn.execute(
            "SELECT COUNT(*) FROM commit_fact WHERE refresh_id = 'dag:run-1'"
        ).fetchone()[0]
        r2 = conn.execute(
            "SELECT COUNT(*) FROM commit_fact WHERE refresh_id = 'dag:run-2'"
        ).fetchone()[0]

    assert total == 2
    assert r1 == 1
    assert r2 == 1


# ── timestamp and field name contracts ───────────────────────────────────────


def test_commit_facts_timestamp_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Confirm that `timestamp` (not `authored_at`) is the field used in JSON."""
    monkeypatch.setenv("LYNCHPIN_LOCAL_ROOT", str(tmp_path))
    _reload_config(monkeypatch)

    sha = "ccc" + "0" * 37
    # Entry uses `timestamp` key — the active facts JSON schema.
    entry = _make_commit_entry(sha, timestamp="2026-04-15T09:30:00+00:00")
    assert "timestamp" in entry
    assert "authored_at" not in entry

    cf_file = tmp_path / "commit_facts.json"
    cf_file.write_text(json.dumps(_make_commit_facts_payload([entry])))
    fc_file = tmp_path / "fc.json"
    fc_file.write_text(json.dumps(_make_file_change_payload([])))
    sym_file = tmp_path / "sym.json"
    sym_file.write_text(json.dumps(_make_symbol_changes_payload([])))

    from lynchpin.analysis.active.substrate_promote import run_substrate_promote
    from lynchpin.substrate.connection import apply_schema, connect, substrate_path

    counts = run_substrate_promote(
        commit_facts_file=str(cf_file),
        file_changes_file=str(fc_file),
        symbol_changes_file=str(sym_file),
        sources=_json_sources(),
        write_evidence_graph=False,
    )

    # Must have promoted the row successfully.
    assert counts.get("commits") == 1

    with connect(substrate_path()) as conn:
        apply_schema(conn)
        row = conn.execute("SELECT authored_at FROM commit_fact").fetchone()
    assert row is not None
    # DuckDB returns a timezone-aware datetime (possibly in local tz).
    # Normalize to UTC before asserting date components.
    authored_at = row[0]
    if hasattr(authored_at, "astimezone"):
        authored_at_utc = authored_at.astimezone(UTC)
        assert authored_at_utc.month == 4
        assert authored_at_utc.year == 2026


def test_same_sha_across_refresh_ids_does_not_collide(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B-1 regression: promote the SAME sha under two refresh_ids.

    Previously commit_fact PK was (sha, repo) so the second insert collided
    with the first. With refresh_id added to PK, both partitions coexist —
    enabling D.1 longitudinal snapshots.
    """
    monkeypatch.setenv("LYNCHPIN_LOCAL_ROOT", str(tmp_path))
    _reload_config(monkeypatch)

    sha = "abc" + "0" * 37
    cf_file = tmp_path / "commit_facts.json"
    cf_file.write_text(json.dumps(_make_commit_facts_payload([
        _make_commit_entry(sha),
    ])))
    fc_file = tmp_path / "fc.json"
    fc_file.write_text(json.dumps(_make_file_change_payload([
        _make_file_change_entry(sha, path="src/x.py"),
    ])))
    sym_file = tmp_path / "sym.json"
    sym_file.write_text(json.dumps(_make_symbol_changes_payload([
        _make_symbol_entry(sha, "lynchpin.foo.bar"),
    ])))

    from lynchpin.analysis.active.substrate_promote import run_substrate_promote
    from lynchpin.substrate.connection import apply_schema, connect, substrate_path

    # First promote: refresh_A
    run_substrate_promote(
        commit_facts_file=str(cf_file),
        file_changes_file=str(fc_file),
        symbol_changes_file=str(sym_file),
        refresh_id="dag:snapshot-A",
        sources=_json_sources(),
        write_evidence_graph=False,
    )

    # Second promote: refresh_B with the SAME sha — must NOT crash on PK.
    run_substrate_promote(
        commit_facts_file=str(cf_file),
        file_changes_file=str(fc_file),
        symbol_changes_file=str(sym_file),
        refresh_id="dag:snapshot-B",
        sources=_json_sources(),
        write_evidence_graph=False,
    )

    with connect(substrate_path()) as conn:
        apply_schema(conn)
        commit_count = conn.execute(
            "SELECT COUNT(*) FROM commit_fact WHERE sha = ?", [sha],
        ).fetchone()[0]
        fc_count = conn.execute(
            "SELECT COUNT(*) FROM file_change_fact WHERE sha = ?", [sha],
        ).fetchone()[0]
        sym_count = conn.execute(
            "SELECT COUNT(*) FROM symbol_change WHERE sha = ?", [sha],
        ).fetchone()[0]
        partitions = conn.execute(
            "SELECT COUNT(DISTINCT refresh_id) FROM commit_fact WHERE sha = ?",
            [sha],
        ).fetchone()[0]

    # Both snapshots survived for every table — D.1 longitudinal works.
    assert commit_count == 2
    assert fc_count == 2
    assert sym_count == 2
    assert partitions == 2


def test_source_status_recorded_on_missing_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When all source files are missing, every source gets a status row.

    Distinguishes 'unavailable' (file/source genuinely missing) from 'empty'
    (file present but no rows), so consumers can tell silent failures from
    legitimate empty windows.
    """
    monkeypatch.setenv("LYNCHPIN_LOCAL_ROOT", str(tmp_path))
    _reload_config(monkeypatch)

    from lynchpin.analysis.active.substrate_promote import run_substrate_promote
    from lynchpin.substrate.connection import apply_schema, connect, substrate_path

    run_substrate_promote(
        commit_facts_file=str(tmp_path / "missing_commits.json"),
        file_changes_file=str(tmp_path / "missing_fc.json"),
        symbol_changes_file=str(tmp_path / "missing_sym.json"),
        refresh_id="dag:test-status-missing",
        sources=_json_sources(),
        write_evidence_graph=False,
    )

    with connect(substrate_path()) as conn:
        apply_schema(conn)
        rows = conn.execute(
            "SELECT source, status, reason FROM substrate_source_status "
            "WHERE refresh_id = ? ORDER BY source",
            ["dag:test-status-missing"],
        ).fetchall()

    by_source = {row[0]: row for row in rows}

    # commits / file_changes / symbols all missing → unavailable
    for src in ("commits", "file_changes", "symbols"):
        assert src in by_source, f"missing status row for {src}"
        assert by_source[src][1] == "unavailable", f"{src} should be unavailable"
        assert by_source[src][2] is not None  # reason populated


def test_source_status_recorded_on_successful_promote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful commit promote records status='ok' with the correct row count."""
    monkeypatch.setenv("LYNCHPIN_LOCAL_ROOT", str(tmp_path))
    _reload_config(monkeypatch)

    cf_file = tmp_path / "commit_facts.json"
    cf_file.write_text(json.dumps(_make_commit_facts_payload([
        _make_commit_entry("aaa" + "0" * 37),
        _make_commit_entry("bbb" + "0" * 37, project="sinex"),
    ])))
    fc_file = tmp_path / "fc.json"
    fc_file.write_text(json.dumps(_make_file_change_payload([])))
    sym_file = tmp_path / "sym.json"
    sym_file.write_text(json.dumps(_make_symbol_changes_payload([])))

    from lynchpin.analysis.active.substrate_promote import run_substrate_promote
    from lynchpin.substrate.connection import apply_schema, connect, substrate_path

    run_substrate_promote(
        commit_facts_file=str(cf_file),
        file_changes_file=str(fc_file),
        symbol_changes_file=str(sym_file),
        refresh_id="dag:test-status-ok",
        sources=_json_sources(),
        write_evidence_graph=False,
    )

    with connect(substrate_path()) as conn:
        apply_schema(conn)
        commits_row = conn.execute(
            "SELECT status, row_count FROM substrate_source_status "
            "WHERE refresh_id = ? AND source = 'commits'",
            ["dag:test-status-ok"],
        ).fetchone()
        # File-changes file present but empty → status='empty', not 'unavailable'.
        fc_row = conn.execute(
            "SELECT status FROM substrate_source_status "
            "WHERE refresh_id = ? AND source = 'file_changes'",
            ["dag:test-status-ok"],
        ).fetchone()

    assert commits_row == ("ok", 2)
    assert fc_row == ("empty",)


def test_evidence_graph_empty_promote_is_not_reported_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A graph build with zero nodes is a materialized empty state, not success."""
    monkeypatch.setenv("LYNCHPIN_LOCAL_ROOT", str(tmp_path))
    _reload_config(monkeypatch)

    from lynchpin.analysis.active.substrate_promote_graph import promote_graph_source
    from lynchpin.analysis.active.substrate_promote_status import (
        SOURCE_EVIDENCE_GRAPH,
        SourceSelection,
    )
    from lynchpin.core.evidence_graph import EvidenceGraph
    from lynchpin.materialization import audit_materialization
    from lynchpin.substrate.connection import apply_schema, connect, substrate_path

    graph = EvidenceGraph(
        start=date(2026, 5, 1),
        end=date(2026, 5, 2),
        generated_at=datetime(2026, 5, 3, tzinfo=UTC),
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_graph.build_evidence_graph",
        lambda *args, **kwargs: graph,
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_graph.analysis_claim_rows",
        lambda *args, **kwargs: [],
    )

    with connect(substrate_path()) as conn:
        apply_schema(conn)
        counts: dict[str, int] = {}
        promote_graph_source(
            conn,
            refresh_id="dag:test-empty-graph",
            window_start=date(2026, 5, 1),
            window_end=date(2026, 5, 2),
            counts=counts,
            selection=SourceSelection.from_collection({SOURCE_EVIDENCE_GRAPH}),
            write_evidence_graph=True,
        )
        status_row = conn.execute(
            "SELECT status, reason, row_count FROM substrate_source_status "
            "WHERE refresh_id = ? AND source = ?",
            ["dag:test-empty-graph", SOURCE_EVIDENCE_GRAPH],
        ).fetchone()

    audit = {row.name: row for row in audit_materialization()}

    assert counts["evidence_graph_nodes"] == 0
    assert status_row == ("empty", "evidence graph build produced no nodes", 0)
    assert audit["evidence_graph_substrate"].status == "empty"


def test_source_status_idempotent_on_re_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same refresh_id re-run replaces, doesn't duplicate, status rows."""
    monkeypatch.setenv("LYNCHPIN_LOCAL_ROOT", str(tmp_path))
    _reload_config(monkeypatch)

    cf_file = tmp_path / "commit_facts.json"
    cf_file.write_text(json.dumps(_make_commit_facts_payload([
        _make_commit_entry("aaa" + "0" * 37),
    ])))
    fc_file = tmp_path / "fc.json"
    fc_file.write_text(json.dumps(_make_file_change_payload([])))
    sym_file = tmp_path / "sym.json"
    sym_file.write_text(json.dumps(_make_symbol_changes_payload([])))

    from lynchpin.analysis.active.substrate_promote import run_substrate_promote
    from lynchpin.substrate.connection import apply_schema, connect, substrate_path

    rid = "dag:test-status-idempotent"

    for _ in range(2):
        run_substrate_promote(
            commit_facts_file=str(cf_file),
            file_changes_file=str(fc_file),
            symbol_changes_file=str(sym_file),
            refresh_id=rid,
            sources=_json_sources(),
            write_evidence_graph=False,
        )

    with connect(substrate_path()) as conn:
        apply_schema(conn)
        # Exactly one row per (refresh_id, source) — primary key prevents dupes.
        commits_count = conn.execute(
            "SELECT COUNT(*) FROM substrate_source_status "
            "WHERE refresh_id = ? AND source = 'commits'",
            [rid],
        ).fetchone()[0]

    assert commits_count == 1


def test_substrate_promote_selected_sources_do_not_probe_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A selective promote only touches the named source family."""
    monkeypatch.setenv("LYNCHPIN_LOCAL_ROOT", str(tmp_path))
    _reload_config(monkeypatch)

    cf_file = tmp_path / "commit_facts.json"
    cf_file.write_text(json.dumps(_make_commit_facts_payload([
        _make_commit_entry("sel" + "0" * 37),
    ])))
    missing = tmp_path / "missing.json"

    def fail_unselected(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("unselected source was probed")

    monkeypatch.setattr("lynchpin.sources.polylogue.work_events", fail_unselected)
    monkeypatch.setattr("lynchpin.sources.spotify.iter_streams", fail_unselected)
    monkeypatch.setattr("lynchpin.sources.machine.metric_samples", fail_unselected)

    from lynchpin.analysis.active.substrate_promote import (
        SOURCE_COMMITS,
        run_substrate_promote,
    )
    from lynchpin.substrate.connection import apply_schema, connect, substrate_path

    counts = run_substrate_promote(
        commit_facts_file=str(cf_file),
        file_changes_file=str(missing),
        symbol_changes_file=str(missing),
        refresh_id="dag:test-selected-source",
        sources={SOURCE_COMMITS},
    )

    assert counts.get("commits") == 1
    assert "file_changes" not in counts
    assert "spotify_daily" not in counts

    with connect(substrate_path()) as conn:
        apply_schema(conn)
        rows = conn.execute(
            "SELECT source FROM substrate_source_status WHERE refresh_id = ? ORDER BY source",
            ["dag:test-selected-source"],
        ).fetchall()

    assert rows == [("commits",)]


def test_substrate_promote_rejects_unknown_source_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LYNCHPIN_LOCAL_ROOT", str(tmp_path))
    _reload_config(monkeypatch)

    from lynchpin.analysis.active.substrate_promote import run_substrate_promote

    try:
        run_substrate_promote(
            commit_facts_file=str(tmp_path / "commits.json"),
            file_changes_file=str(tmp_path / "files.json"),
            symbol_changes_file=str(tmp_path / "symbols.json"),
            sources={"machine_gpu"},
        )
    except ValueError as exc:
        assert "unknown substrate promote source(s): machine_gpu" in str(exc)
    else:
        raise AssertionError("expected unknown source selection to fail")


def test_work_source_promotion_streams_source_iterables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Work promotion must not materialize high-volume source rows as lists."""
    from lynchpin.analysis.active import substrate_promote_work as work_promote
    from lynchpin.analysis.active.substrate_promote_status import (
        SOURCE_WORK_OBSERVATIONS,
        SourceSelection,
    )

    class Conn:
        def __init__(self) -> None:
            self.deletes = 0

        def execute(self, sql: str, params: list[object]) -> None:
            assert sql == "DELETE FROM work_observation WHERE refresh_id = ?"
            assert params == ["rid"]
            self.deletes += 1

    def source_rows(label: str):
        yield label

    seen: dict[str, str] = {}

    def consume(name: str, rows: object, **_kwargs: object) -> int:
        assert not isinstance(rows, list), f"{name} rows were eagerly listed"
        seen[name] = next(iter(rows))  # type: ignore[arg-type]
        return 1

    monkeypatch.setattr(
        "lynchpin.sources.xtask_history.xtask_history_paths",
        lambda: (("live", tmp_path / "xtask.db"),),
    )
    (tmp_path / "xtask.db").write_text("", encoding="utf-8")
    monkeypatch.setattr("lynchpin.sources.polylogue_devtools.available", lambda: True)
    monkeypatch.setattr(
        "lynchpin.sources.xtask_history.iter_all_invocations",
        lambda **kwargs: source_rows("xtask"),
    )
    monkeypatch.setattr(
        "lynchpin.sources.polylogue_devtools.iter_invocations",
        lambda **kwargs: source_rows("polylogue"),
    )
    monkeypatch.setattr(
        "lynchpin.sources.xtask_history.iter_all_stage_timings",
        lambda **kwargs: source_rows("stage"),
    )
    monkeypatch.setattr(
        "lynchpin.sources.xtask_history.iter_all_test_results",
        lambda **kwargs: source_rows("test"),
    )
    monkeypatch.setattr(
        "lynchpin.substrate.work_observations.promote_work_observations",
        lambda conn, refresh_id, rows, delete_existing=True: consume("xtask", rows),
    )
    monkeypatch.setattr(
        "lynchpin.substrate.work_observations.promote_polylogue_devtools_observations",
        lambda conn, refresh_id, rows, delete_existing=True: consume("polylogue", rows),
    )
    monkeypatch.setattr(
        "lynchpin.substrate.work_observations.promote_work_observation_stages",
        lambda conn, refresh_id, rows: consume("stage", rows),
    )
    monkeypatch.setattr(
        "lynchpin.substrate.work_observations.promote_work_observation_test_results",
        lambda conn, refresh_id, rows: consume("test", rows),
    )
    statuses = []
    monkeypatch.setattr(
        work_promote,
        "record_source_status",
        lambda *args, **kwargs: statuses.append(kwargs),
    )

    counts: dict[str, int] = {}
    conn = Conn()
    work_promote.promote_work_sources(
        conn,
        refresh_id="rid",
        window_start=date(2026, 5, 1),
        window_end=date(2026, 5, 2),
        counts=counts,
        selection=SourceSelection.from_collection({SOURCE_WORK_OBSERVATIONS}),
    )

    assert conn.deletes == 1
    assert seen == {
        "xtask": "xtask",
        "polylogue": "polylogue",
        "stage": "stage",
        "test": "test",
    }
    assert counts["work_observations"] == 2
    assert counts["work_observation_test_results"] == 1
    assert statuses[0]["status"] == "ok"
    assert statuses[0]["row_count"] == 4


def test_pr_review_promotion_when_payload_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When pr_review_file points to a populated payload, rows land in pr_review_row."""
    monkeypatch.setenv("LYNCHPIN_LOCAL_ROOT", str(tmp_path))
    _reload_config(monkeypatch)

    cf_file = tmp_path / "commit_facts.json"
    cf_file.write_text(json.dumps(_make_commit_facts_payload([])))
    fc_file = tmp_path / "fc.json"
    fc_file.write_text(json.dumps(_make_file_change_payload([])))
    sym_file = tmp_path / "sym.json"
    sym_file.write_text(json.dumps(_make_symbol_changes_payload([])))

    pr_file = tmp_path / "pr_review.json"
    pr_payload = {
        "prs": [
            {
                "project": "lynchpin",
                "number": 7,
                "title": "feat: test",
                "state": "merged",
                "url": "https://github.com/sinity/lynchpin/pull/7",
                "author": "Sinity",
                "created_at": "2026-05-01T10:00:00+00:00",
                "merged_at": "2026-05-01T12:00:00+00:00",
                "review_count": 1,
                "review_decisions": ["approved"],
                "reviewer_count": 1,
                "reviewers": ["alice"],
            },
        ]
    }
    pr_file.write_text(json.dumps(pr_payload))

    from lynchpin.analysis.active.substrate_promote import run_substrate_promote
    from lynchpin.substrate.connection import apply_schema, connect, substrate_path

    counts = run_substrate_promote(
        commit_facts_file=str(cf_file),
        file_changes_file=str(fc_file),
        symbol_changes_file=str(sym_file),
        pr_review_file=str(pr_file),
        refresh_id="dag:test-pr-review",
        sources=_json_pr_sources(),
        write_evidence_graph=False,
    )

    assert counts.get("pr_review_rows") == 1

    with connect(substrate_path()) as conn:
        apply_schema(conn)
        row = conn.execute(
            "SELECT project, number, state, refresh_id FROM pr_review_row"
        ).fetchone()
        status_row = conn.execute(
            "SELECT status, row_count FROM substrate_source_status "
            "WHERE refresh_id = ? AND source = 'pr_review'",
            ["dag:test-pr-review"],
        ).fetchone()

    assert row == ("lynchpin", 7, "merged", "dag:test-pr-review")
    assert status_row == ("ok", 1)


def test_pr_review_marked_unavailable_when_file_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing pr_review_file → substrate_source_status records unavailable.

    Distinguishes 'M.7 hasn't been run yet' from 'M.7 ran and produced empty
    output'. Without this signal, agents can't tell whether to expect data.
    """
    monkeypatch.setenv("LYNCHPIN_LOCAL_ROOT", str(tmp_path))
    _reload_config(monkeypatch)

    cf_file = tmp_path / "cf.json"
    cf_file.write_text(json.dumps(_make_commit_facts_payload([])))
    fc_file = tmp_path / "fc.json"
    fc_file.write_text(json.dumps(_make_file_change_payload([])))
    sym_file = tmp_path / "sym.json"
    sym_file.write_text(json.dumps(_make_symbol_changes_payload([])))

    from lynchpin.analysis.active.substrate_promote import run_substrate_promote
    from lynchpin.substrate.connection import apply_schema, connect, substrate_path

    run_substrate_promote(
        commit_facts_file=str(cf_file),
        file_changes_file=str(fc_file),
        symbol_changes_file=str(sym_file),
        pr_review_file=str(tmp_path / "missing_pr.json"),
        refresh_id="dag:test-pr-missing",
        sources=_json_pr_sources(),
        write_evidence_graph=False,
    )

    with connect(substrate_path()) as conn:
        apply_schema(conn)
        row = conn.execute(
            "SELECT status FROM substrate_source_status "
            "WHERE refresh_id = ? AND source = 'pr_review'",
            ["dag:test-pr-missing"],
        ).fetchone()

    assert row == ("unavailable",)


def test_path_roots_dict_keys_extracted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """path_roots dict keys become the tuple stored in commit_fact.path_roots."""
    monkeypatch.setenv("LYNCHPIN_LOCAL_ROOT", str(tmp_path))
    _reload_config(monkeypatch)

    sha = "ddd" + "0" * 37
    entry = _make_commit_entry(sha, path_roots={"src": 5, "tests": 2})

    cf_file = tmp_path / "commit_facts.json"
    cf_file.write_text(json.dumps(_make_commit_facts_payload([entry])))
    fc_file = tmp_path / "fc.json"
    fc_file.write_text(json.dumps(_make_file_change_payload([])))
    sym_file = tmp_path / "sym.json"
    sym_file.write_text(json.dumps(_make_symbol_changes_payload([])))

    from lynchpin.analysis.active.substrate_promote import run_substrate_promote
    from lynchpin.substrate.connection import apply_schema, connect, substrate_path

    run_substrate_promote(
        commit_facts_file=str(cf_file),
        file_changes_file=str(fc_file),
        symbol_changes_file=str(sym_file),
        sources=_json_sources(),
        write_evidence_graph=False,
    )

    with connect(substrate_path()) as conn:
        apply_schema(conn)
        row = conn.execute("SELECT path_roots FROM commit_fact").fetchone()
    assert row is not None
    # path_roots is a VARCHAR[] in DuckDB.
    path_roots_val = row[0]
    assert set(path_roots_val) == {"src", "tests"}
