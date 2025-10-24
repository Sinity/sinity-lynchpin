import json
from datetime import date, datetime, timezone
from types import SimpleNamespace

from typer.testing import CliRunner

from lynchpin.analysis.cli import _run_dag, build_app
from lynchpin.analysis.core.dag import DAG, Step


def test_run_dag_reports_selected_step_count_for_up_to(capsys, monkeypatch, tmp_path):
    ran: list[str] = []
    dag = DAG("tiny")
    dag.add(Step("first", fn=lambda: ran.append("first")))
    dag.add(Step("second", fn=lambda: ran.append("second"), depends_on=["first"]))
    dag.add(Step("third", fn=lambda: ran.append("third"), depends_on=["second"]))
    config = type("Config", (), {"analysis_output_dir": tmp_path / "analysis"})()
    monkeypatch.setattr("lynchpin.core.io.get_config", lambda: config)
    monkeypatch.setattr("lynchpin.analysis.cli.resolve_analysis_path", lambda name: str(config.analysis_output_dir / name))

    code = _run_dag(dag, dry_run=False, up_to="second")

    # DAG banners emit to stderr (+ materialization.log) per the observability change.
    err = capsys.readouterr().err
    assert code == 0
    assert "Starting tiny DAG (2 steps; stops after: second)" in err
    assert "Materialization report:" in err
    assert "Materialization complete: 2 succeeded, 0 failed, 0 skipped" in err
    assert ran == ["first", "second"]
    assert (config.analysis_output_dir / "tiny_materialization_report.json").exists()


def test_run_dag_does_not_duplicate_materialization_suffix(capsys, monkeypatch, tmp_path):
    dag = DAG("machine-analysis-materialization")
    dag.add(Step("only", fn=lambda: {"row_count": 1}))
    config = type("Config", (), {"analysis_output_dir": tmp_path / "analysis"})()
    monkeypatch.setattr("lynchpin.core.io.get_config", lambda: config)
    monkeypatch.setattr("lynchpin.analysis.cli.resolve_analysis_path", lambda name: str(config.analysis_output_dir / name))

    code = _run_dag(dag, dry_run=False, up_to=None)

    assert code == 0
    assert (config.analysis_output_dir / "machine_analysis_materialization_report.json").exists()
    assert not (config.analysis_output_dir / "machine_analysis_refresh_materialization_report.json").exists()


def test_run_dag_can_explain_materialization_dry_run(capsys, monkeypatch, tmp_path):
    dag = DAG("machine-analysis-materialization")
    dag.add(Step("machine_analysis_substrate_promote", fn=lambda: None))
    config = type("Config", (), {"analysis_output_dir": tmp_path / "analysis"})()
    monkeypatch.setattr("lynchpin.core.io.get_config", lambda: config)
    monkeypatch.setattr("lynchpin.analysis.cli.resolve_analysis_path", lambda name: str(config.analysis_output_dir / name))
    monkeypatch.setattr(
        "lynchpin.analysis.core.materialization_intelligence.resolve_analysis_path",
        lambda name: str(config.analysis_output_dir / name),
    )

    code = _run_dag(dag, dry_run=True, up_to=None, explain_materialization=True)

    out = capsys.readouterr().out
    assert code == 0
    assert "DAG: machine-analysis-materialization" in out
    assert "Materialization plan: 1 steps (run=1)" in out
    assert "machine_analysis_substrate_promote [moderate/incremental]" in out


def test_run_dag_explanation_uses_generic_policy_for_non_machine_dags(capsys, monkeypatch, tmp_path):
    dag = DAG("analysis-materialization")
    dag.add(Step("machine_telemetry_analysis", fn=lambda: None))
    config = type("Config", (), {"analysis_output_dir": tmp_path / "analysis"})()
    monkeypatch.setattr("lynchpin.core.io.get_config", lambda: config)
    monkeypatch.setattr("lynchpin.analysis.cli.resolve_analysis_path", lambda name: str(config.analysis_output_dir / name))
    monkeypatch.setattr(
        "lynchpin.analysis.core.materialization_intelligence.resolve_analysis_path",
        lambda name: str(config.analysis_output_dir / name),
    )

    code = _run_dag(dag, dry_run=True, up_to=None, explain_materialization=True)

    out = capsys.readouterr().out
    assert code == 0
    assert "DAG: analysis-materialization" in out
    assert "machine_telemetry_analysis [moderate/batch]" in out
    assert "machine_telemetry_analysis [cheap/realtime]" not in out


def test_run_dag_explanation_uses_analysis_artifact_policy(capsys, monkeypatch, tmp_path):
    dag = DAG("analysis-materialization")
    dag.add(Step("active_git_facts", fn=lambda: None))
    config = type("Config", (), {"analysis_output_dir": tmp_path / "analysis"})()
    monkeypatch.setattr("lynchpin.core.io.get_config", lambda: config)
    monkeypatch.setattr("lynchpin.analysis.cli.resolve_analysis_path", lambda name: str(config.analysis_output_dir / name))
    monkeypatch.setattr(
        "lynchpin.analysis.core.materialization_intelligence.resolve_analysis_path",
        lambda name: str(config.analysis_output_dir / name),
    )

    code = _run_dag(dag, dry_run=True, up_to=None, explain_materialization=True)

    out = capsys.readouterr().out
    assert code == 0
    assert "active_commit_facts.json" in out
    assert "active_file_change_facts.json" in out
    assert "active_git_facts.json" not in out


def test_run_dag_explanation_names_default_artifact(capsys, monkeypatch, tmp_path):
    artifact = tmp_path / "analysis" / "active_project_snapshot.json"
    artifact.parent.mkdir()
    artifact.write_text("{}", encoding="utf-8")
    dag = DAG("current-state-materialization")
    dag.add(Step("active_project_snapshot", fn=lambda: None))
    config = type("Config", (), {"analysis_output_dir": tmp_path / "analysis"})()
    monkeypatch.setattr("lynchpin.core.io.get_config", lambda: config)
    monkeypatch.setattr("lynchpin.analysis.cli.resolve_analysis_path", lambda name: str(config.analysis_output_dir / name))
    monkeypatch.setattr(
        "lynchpin.analysis.core.materialization_intelligence.resolve_analysis_path",
        lambda name: str(config.analysis_output_dir / name),
    )

    code = _run_dag(dag, dry_run=True, up_to=None, explain_materialization=True)

    out = capsys.readouterr().out
    assert code == 0
    assert "active_project_snapshot.json" in out
    assert "default artifact mapping" not in out


def test_run_dag_incremental_skips_current_steps(capsys, monkeypatch, tmp_path):
    ran: list[str] = []
    dag = DAG("machine-analysis-materialization")
    dag.add(Step("machine_telemetry_analysis", fn=lambda: ran.append("telemetry")))
    artifact = tmp_path / "analysis" / "machine_telemetry_analysis.json"
    artifact.parent.mkdir()
    artifact.write_text("{}", encoding="utf-8")
    config = type("Config", (), {"analysis_output_dir": tmp_path / "analysis", "local_root": tmp_path / "local"})()
    monkeypatch.setattr("lynchpin.core.io.get_config", lambda: config)
    monkeypatch.setattr("lynchpin.core.freshness.get_config", lambda: config)
    monkeypatch.setattr("lynchpin.analysis.cli.resolve_analysis_path", lambda name: str(config.analysis_output_dir / name))
    monkeypatch.setattr(
        "lynchpin.analysis.core.materialization_intelligence.resolve_analysis_path",
        lambda name: str(config.analysis_output_dir / name),
    )

    code = _run_dag(dag, dry_run=False, up_to=None, incremental=True)

    assert code == 0
    assert ran == []
    assert "Materialization plan:" in capsys.readouterr().err
    report = json.loads((config.analysis_output_dir / "machine_analysis_materialization_report.json").read_text())
    assert report["materialization_plan"][0]["step"] == "machine_telemetry_analysis"
    assert report["steps"][0]["status"] == "skipped"
    assert report["steps"][0]["degraded_reasons"] == []
    assert "freshness_receipts" not in report
    assert "freshness_dependencies" not in report
    assert "queued_refreshes" not in report


def test_run_dag_explain_execution_skips_inspect_only_steps(capsys, monkeypatch, tmp_path):
    ran: list[str] = []
    dag = DAG("analysis-materialization")
    dag.add(Step("active_project_snapshot", fn=lambda: ran.append("snapshot")))
    dag.add(Step("active_git_facts", fn=lambda: ran.append("git"), depends_on=["active_project_snapshot"]))
    artifact = tmp_path / "analysis" / "active_project_snapshot.json"
    artifact.parent.mkdir()
    artifact.write_text("{}", encoding="utf-8")
    config = type("Config", (), {"analysis_output_dir": tmp_path / "analysis", "local_root": tmp_path / "local"})()
    monkeypatch.setattr("lynchpin.core.io.get_config", lambda: config)
    monkeypatch.setattr("lynchpin.analysis.cli.resolve_analysis_path", lambda name: str(config.analysis_output_dir / name))
    monkeypatch.setattr(
        "lynchpin.analysis.core.materialization_intelligence.resolve_analysis_path",
        lambda name: str(config.analysis_output_dir / name),
    )

    code = _run_dag(dag, dry_run=False, up_to=None, explain_materialization=True)

    assert code == 0
    assert ran == ["git"]
    err = capsys.readouterr().err
    assert "Materialization plan:" in err
    report = json.loads((config.analysis_output_dir / "analysis_materialization_report.json").read_text())
    assert [row["name"] for row in report["steps"]] == ["active_project_snapshot", "active_git_facts"]
    assert [row["status"] for row in report["steps"]] == ["skipped", "success"]


def test_new_retrospective_artifact_commands_are_registered():
    result = CliRunner().invoke(build_app(), ["--help"])

    assert result.exit_code == 0
    for command in (
        "claim-calibration",
        "code-history-claims",
        "google-takeout-retrospective",
        "keylog-analysis",
        "materialize-current-state",
        "materialize-machine",
        "observability-status",
        "personal-interest-trace",
        "status",
        "workflow-mechanics",
    ):
        assert command in result.output


def test_diagnostic_ledger_commands_are_hidden_from_top_level_help(monkeypatch, tmp_path):
    config = type("Config", (), {"local_root": tmp_path / "local"})()
    monkeypatch.setattr("lynchpin.core.freshness.get_config", lambda: config)
    result = CliRunner().invoke(build_app(), ["--help"])

    assert result.exit_code == 0
    assert "freshness-status" not in result.output
    assert "diagnostic-ledger-status" not in result.output
    assert "diagnostic-panel-status" not in result.output

    removed = CliRunner().invoke(build_app(), ["freshness-status", "--receipts", "0"])
    panel = CliRunner().invoke(build_app(), ["diagnostic-panel-status"])
    direct = CliRunner().invoke(build_app(), ["diagnostic-ledger-status", "--receipts", "0"])

    assert removed.exit_code != 0
    assert panel.exit_code != 0
    assert direct.exit_code == 0
    assert "diagnostic-ledger-status:" in direct.output


def test_status_command_emits_materialization_first_json(monkeypatch):
    monkeypatch.setattr(
        "lynchpin.analysis.cli.compact_materialization_status",
        lambda: {
            "kind": "lynchpin_materialization_status",
            "health": "ok",
            "materialization": {
                "status": "ready",
                "primary_product": "evidence_graph_substrate",
            },
        },
    )

    result = CliRunner().invoke(build_app(), ["status", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["kind"] == "lynchpin_materialization_status"
    assert payload["materialization"]["primary_product"] == "evidence_graph_substrate"
    assert "queue" not in payload


def test_status_command_has_plain_summary(monkeypatch):
    monkeypatch.setattr(
        "lynchpin.analysis.cli.compact_materialization_status",
        lambda: {
            "health": "attention",
            "materialization": {"status": "blocked"},
        },
    )

    result = CliRunner().invoke(build_app(), ["status"])

    assert result.exit_code == 0
    assert "lynchpin-status: attention materialization=blocked" in result.output


def test_diagnostic_source_materialization_decision_uses_materialization(monkeypatch):
    calls: list[tuple[str, tuple[date, date] | None]] = []

    class Result:
        status = "ready"
        name = "reddit"
        changed = False
        reason = "fixture"

    def fake_ensure_materialized(name: str, *, window=None):
        calls.append((name, window))
        return Result()

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)

    result = CliRunner().invoke(
        build_app(),
        [
            "diagnostic-source-materialization-decision",
            "reddit",
            "--start",
            "2026-06-01",
            "--end",
            "2026-06-05",
        ],
    )

    assert result.exit_code == 0
    assert calls == [("reddit", (date(2026, 6, 1), date(2026, 6, 6)))]
    assert "diagnostic-source-materialization-decision: ready reddit changed=False reason=fixture" in result.output


def test_observability_status_omits_queue_diagnostics(monkeypatch):
    def fake_status():
        return {
            "kind": "lynchpin_observability_status",
            "health": "ok",
            "materialization": {"status": "ready"},
        }

    monkeypatch.setattr("lynchpin.analysis.cli.compact_materialization_status", fake_status)

    result = CliRunner().invoke(build_app(), ["observability-status"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["kind"] == "lynchpin_observability_status"
    assert "queue" not in payload


def test_materialize_machine_help_uses_materialization_language():
    result = CliRunner().invoke(build_app(), ["materialize-machine", "--help"])

    assert result.exit_code == 0
    assert "--explain-materialization" in result.output
    assert "Materialize machine-analysis products" in result.output


def test_materialize_current_state_help_exposes_explanation_without_incremental_execution():
    result = CliRunner().invoke(build_app(), ["materialize-current-state", "--help"])

    assert result.exit_code == 0
    assert "--explain-materialization" in result.output
    assert "--incremental" not in result.output


def test_below_pressure_export_cli_reports_failed_attempts(monkeypatch):
    plan_ok = SimpleNamespace(
        capture_id="ok",
        episode_kind="load_pressure",
        severity=1.0,
        begin=datetime(2026, 5, 1, 12, tzinfo=timezone.utc),
        end=datetime(2026, 5, 1, 12, 5, tzinfo=timezone.utc),
    )
    plan_bad = SimpleNamespace(
        capture_id="bad",
        episode_kind="io_pressure",
        severity=0.9,
        begin=datetime(2026, 5, 1, 13, tzinfo=timezone.utc),
        end=datetime(2026, 5, 1, 13, 5, tzinfo=timezone.utc),
    )
    exports = [
        SimpleNamespace(plan=plan_ok, export=SimpleNamespace(errors=())),
        SimpleNamespace(plan=plan_bad, export=SimpleNamespace(errors=("below-system.csv: No samples found",))),
    ]
    monkeypatch.setattr(
        "lynchpin.analysis.cli.export_below_windows_for_pressure_episodes",
        lambda **kwargs: exports,
    )

    result = CliRunner().invoke(
        build_app(),
        ["machine-below-export-pressure-windows", "--write"],
    )

    assert result.exit_code == 1
    assert "attempted 2 windows; succeeded=1; failed=1" in result.output
    assert "error: bad: below-system.csv: No samples found" in result.output


def test_materialization_log_path_honours_local_root(monkeypatch, tmp_path):
    """The materialization log resolves under the configured local root, not CWD.

    Regression guard for the nightly systemd unit failing with
    PermissionError when launched without a repo WorkingDirectory.
    """
    import lynchpin.core.config as config_mod
    from lynchpin.analysis.cli import _materialization_log_path

    monkeypatch.setenv("LYNCHPIN_LOCAL_ROOT", str(tmp_path / "local"))
    config_mod._CONFIG = None  # clear cached config so the env var is read
    try:
        log_path = _materialization_log_path()
    finally:
        config_mod._CONFIG = None  # avoid leaking the tmp config to other tests

    assert log_path == tmp_path / "local" / "log" / "materialization.log"


def test_run_dag_memoizes_unchanged_fingerprinted_step(monkeypatch, tmp_path):
    """A fingerprinted step runs once, then is skipped while its key is unchanged.

    This is the content-keyed memoization that lets the bulk DAG collapse to
    only-what-changed (e.g. immutable full-repository git-history metrics).
    """
    from lynchpin.analysis.cli import _run_dag
    from lynchpin.analysis.core import memo
    from lynchpin.analysis.core.dag import DAG, Step

    fp_store = tmp_path / "fp.json"
    monkeypatch.setattr(memo, "fingerprint_store_path", lambda local_root=None: fp_store)
    monkeypatch.setattr(
        "lynchpin.analysis.cli.resolve_analysis_path",
        lambda name: str(tmp_path / name),
    )

    runs = {"n": 0}

    def _work():
        runs["n"] += 1

    dag = DAG("analysis-materialization")
    dag.add(Step("memoized", fn=_work, fingerprint=lambda: "stable-key"))

    assert _run_dag(dag, dry_run=False, up_to=None) == 0
    assert runs["n"] == 1  # ran the first time (no recorded fingerprint)
    assert _run_dag(dag, dry_run=False, up_to=None) == 0
    assert runs["n"] == 1  # memoized away on the second run

    # --no-memo forces a full rebuild.
    assert _run_dag(dag, dry_run=False, up_to=None, memoize=False) == 0
    assert runs["n"] == 2


def test_run_dag_reruns_when_fingerprint_changes(monkeypatch, tmp_path):
    from lynchpin.analysis.cli import _run_dag
    from lynchpin.analysis.core import memo
    from lynchpin.analysis.core.dag import DAG, Step

    fp_store = tmp_path / "fp.json"
    monkeypatch.setattr(memo, "fingerprint_store_path", lambda local_root=None: fp_store)
    monkeypatch.setattr(
        "lynchpin.analysis.cli.resolve_analysis_path",
        lambda name: str(tmp_path / name),
    )

    key = {"v": "k1"}
    runs = {"n": 0}

    def _work():
        runs["n"] += 1

    dag = DAG("analysis-materialization")
    dag.add(Step("memoized", fn=_work, fingerprint=lambda: key["v"]))

    assert _run_dag(dag, dry_run=False, up_to=None) == 0
    assert runs["n"] == 1
    key["v"] = "k2"  # input changed → must re-run
    assert _run_dag(dag, dry_run=False, up_to=None) == 0
    assert runs["n"] == 2
