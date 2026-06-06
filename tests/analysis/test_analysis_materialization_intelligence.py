from __future__ import annotations

from datetime import date, datetime, timezone
from os import utime

from lynchpin.analysis.core.dag import DAG, Step
from lynchpin.analysis.core.materialization_intelligence import (
    MaterializationStepPolicy,
    analysis_materialization_policies,
    executable_steps,
    materialization_plan_for_dag,
    render_materialization_plan,
)


def test_materialization_plan_runs_missing_artifacts_and_dependents(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "lynchpin.analysis.core.materialization_intelligence.resolve_analysis_path",
        lambda name: str(tmp_path / name),
    )
    dag = DAG("tiny")
    dag.add(Step("source", fn=lambda: None))
    dag.add(Step("derived", fn=lambda: None, depends_on=["source"]))

    rows = materialization_plan_for_dag(
        dag,
        policies={
            "source": MaterializationStepPolicy("source", artifacts=("source.json",), max_age_seconds=60),
            "derived": MaterializationStepPolicy("derived", artifacts=("derived.json",), max_age_seconds=60),
        },
        now=datetime(2026, 6, 5, 12, tzinfo=timezone.utc),
    )

    assert [row.action for row in rows] == ["run", "run"]
    assert rows[0].reason.startswith("missing artifact:")
    assert rows[1].reason == "dependency scheduled: source"


def test_materialization_plan_skips_current_artifacts_and_runs_expired(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "lynchpin.analysis.core.materialization_intelligence.resolve_analysis_path",
        lambda name: str(tmp_path / name),
    )
    current = tmp_path / "current.json"
    expired = tmp_path / "expired.json"
    current.write_text("{}", encoding="utf-8")
    expired.write_text("{}", encoding="utf-8")
    utime(current, (1_780_000_000, 1_780_000_000))
    utime(expired, (1_779_999_000, 1_779_999_000))
    now = datetime.fromtimestamp(1_780_000_030, tz=timezone.utc)
    dag = DAG("tiny")
    dag.add(Step("current", fn=lambda: None))
    dag.add(Step("expired", fn=lambda: None))

    rows = materialization_plan_for_dag(
        dag,
        policies={
            "current": MaterializationStepPolicy("current", artifacts=("current.json",), max_age_seconds=60),
            "expired": MaterializationStepPolicy("expired", artifacts=("expired.json",), max_age_seconds=60),
        },
        now=now,
    )

    assert rows[0].action == "skip"
    assert rows[1].action == "run"
    assert rows[1].reason.startswith("artifact outside materialization age horizon:")
    assert executable_steps(rows) == {"expired"}


def test_materialization_plan_skips_expired_artifact_when_declared_window_covers_request(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "lynchpin.analysis.core.materialization_intelligence.resolve_analysis_path",
        lambda name: str(tmp_path / name),
    )
    artifact = tmp_path / "keylog_analysis.json"
    artifact.write_text(
        '{"start": "2026-03-09", "end": "2026-06-06", "keybind_usage": []}',
        encoding="utf-8",
    )
    utime(artifact, (1_779_999_000, 1_779_999_000))
    now = datetime.fromtimestamp(1_780_000_030, tz=timezone.utc)
    dag = DAG("tiny")
    dag.add(Step("keylog_analysis", fn=lambda: None))

    rows = materialization_plan_for_dag(
        dag,
        policies={
            "keylog_analysis": MaterializationStepPolicy(
                "keylog_analysis",
                artifacts=("keylog_analysis.json",),
                max_age_seconds=60,
            ),
        },
        now=now,
        requested_window=(date(2026, 6, 1), date(2026, 6, 5)),
    )

    assert rows[0].action == "skip"
    assert rows[0].reason == "artifacts cover requested window"
    assert rows[0].artifact_statuses[0]["coverage_state"] == "declared"
    assert rows[0].artifact_statuses[0]["coverage_start"] == "2026-03-09"
    assert executable_steps(rows) == set()


def test_materialization_plan_runs_expired_artifact_when_declared_window_misses_request(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "lynchpin.analysis.core.materialization_intelligence.resolve_analysis_path",
        lambda name: str(tmp_path / name),
    )
    artifact = tmp_path / "keylog_analysis.json"
    artifact.write_text(
        '{"start": "2026-03-09", "end": "2026-06-01", "keybind_usage": []}',
        encoding="utf-8",
    )
    utime(artifact, (1_779_999_000, 1_779_999_000))
    now = datetime.fromtimestamp(1_780_000_030, tz=timezone.utc)
    dag = DAG("tiny")
    dag.add(Step("keylog_analysis", fn=lambda: None))

    rows = materialization_plan_for_dag(
        dag,
        policies={
            "keylog_analysis": MaterializationStepPolicy(
                "keylog_analysis",
                artifacts=("keylog_analysis.json",),
                max_age_seconds=60,
            ),
        },
        now=now,
        requested_window=(date(2026, 6, 1), date(2026, 6, 5)),
    )

    assert rows[0].action == "run"
    assert rows[0].reason.startswith("artifact outside materialization age horizon:")
    assert rows[0].artifact_statuses[0]["coverage_state"] == "declared"
    assert executable_steps(rows) == {"keylog_analysis"}


def test_materialization_plan_names_artifacts_without_age_horizon(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "lynchpin.analysis.core.materialization_intelligence.resolve_analysis_path",
        lambda name: str(tmp_path / name),
    )
    current = tmp_path / "current.json"
    current.write_text("{}", encoding="utf-8")
    dag = DAG("tiny")
    dag.add(Step("current", fn=lambda: None))

    rows = materialization_plan_for_dag(
        dag,
        policies={
            "current": MaterializationStepPolicy("current", artifacts=("current.json",)),
        },
    )

    assert rows[0].action == "inspect"
    assert rows[0].reason == (
        f"artifact exists but no materialization age horizon is defined: {current}"
    )
    assert executable_steps(rows) == set()


def test_default_materialization_policy_names_same_step_artifact(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "lynchpin.analysis.core.materialization_intelligence.resolve_analysis_path",
        lambda name: str(tmp_path / name),
    )
    artifact = tmp_path / "same_name.json"
    artifact.write_text("{}", encoding="utf-8")
    dag = DAG("tiny")
    dag.add(Step("same_name", fn=lambda: None))

    rows = materialization_plan_for_dag(dag)

    assert rows[0].action == "inspect"
    assert rows[0].reason == (
        f"artifact exists but no materialization age horizon is defined: {artifact}"
    )
    assert executable_steps(rows) == set()


def test_materialization_plan_renders_cost_and_mode(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "lynchpin.analysis.core.materialization_intelligence.resolve_analysis_path",
        lambda name: str(tmp_path / name),
    )
    dag = DAG("tiny")
    dag.add(Step("telemetry", fn=lambda: None))

    rows = materialization_plan_for_dag(
        dag,
        policies={
            "telemetry": MaterializationStepPolicy(
                "telemetry",
                artifacts=(),
                cost="cheap",
                mode="realtime",
                reason="table-level signal",
            )
        },
    )

    rendered = render_materialization_plan(rows)
    assert "Materialization plan: 1 steps (inspect=1)" in rendered
    assert "inspect telemetry [cheap/realtime] table-level signal" in rendered


def test_materialization_plan_uses_substrate_source_status(monkeypatch) -> None:
    dag = DAG("tiny")
    dag.add(Step("promote", fn=lambda: None))
    monkeypatch.setattr(
        "lynchpin.analysis.core.materialization_intelligence._substrate_source_status",
        lambda source, *, now: {
            "source": source,
            "state": "present",
            "status": "ok",
            "age_seconds": 30,
        },
    )

    rows = materialization_plan_for_dag(
        dag,
        policies={
            "promote": MaterializationStepPolicy(
                "promote",
                substrate_sources=("machine",),
                max_age_seconds=60,
            )
        },
    )

    assert rows[0].action == "skip"


def test_analysis_materialization_policies_map_active_git_outputs() -> None:
    policies = analysis_materialization_policies(("active_git_facts", "active_project_snapshot"))

    assert policies["active_git_facts"].artifacts == (
        "active_commit_facts.json",
        "active_file_change_facts.json",
    )
    assert "active_project_snapshot" not in policies


def test_analysis_materialization_policy_declares_current_state_substrate_sources() -> None:
    policy = analysis_materialization_policies(("current_state_substrate_promote",))[
        "current_state_substrate_promote"
    ]

    assert policy.artifacts == ()
    assert policy.substrate_sources == (
        "commits",
        "file_changes",
        "symbols",
        "ai_work_events",
        "evidence_graph",
        "pr_review",
        "work_observations",
    )
    assert policy.max_age_seconds == 5 * 60
    assert policy.mode == "incremental"


def test_current_state_substrate_policy_uses_coverage_skip(monkeypatch) -> None:
    dag = DAG("current")
    dag.add(Step("current_state_substrate_promote", fn=lambda: None))
    sources = analysis_materialization_policies(
        ("current_state_substrate_promote",)
    )["current_state_substrate_promote"].substrate_sources
    assert sources is not None
    monkeypatch.setattr(
        "lynchpin.analysis.core.materialization_intelligence._substrate_source_status",
        lambda source, *, now: {
            "source": source,
            "state": "present",
            "status": "ok",
            "age_seconds": 600,
            "window_start": "2026-06-01",
            "window_end": "2026-06-06",
        },
    )

    rows = materialization_plan_for_dag(
        dag,
        policies=analysis_materialization_policies(("current_state_substrate_promote",)),
        requested_window=(date(2026, 6, 2), date(2026, 6, 5)),
    )

    assert rows[0].action == "skip"
    assert rows[0].reason == "substrate source statuses cover requested window"


def test_materialization_plan_runs_expired_substrate_source_status(monkeypatch) -> None:
    dag = DAG("tiny")
    dag.add(Step("promote", fn=lambda: None))
    monkeypatch.setattr(
        "lynchpin.analysis.core.materialization_intelligence._substrate_source_status",
        lambda source, *, now: {
            "source": source,
            "state": "present",
            "status": "ok",
            "age_seconds": 120,
        },
    )

    rows = materialization_plan_for_dag(
        dag,
        policies={
            "promote": MaterializationStepPolicy(
                "promote",
                substrate_sources=("machine",),
                max_age_seconds=60,
            )
        },
    )

    assert rows[0].action == "run"
    assert rows[0].reason == "substrate source status outside materialization age horizon: machine"


def test_materialization_plan_skips_expired_substrate_status_when_window_is_covered(monkeypatch) -> None:
    dag = DAG("tiny")
    dag.add(Step("promote", fn=lambda: None))
    monkeypatch.setattr(
        "lynchpin.analysis.core.materialization_intelligence._substrate_source_status",
        lambda source, *, now: {
            "source": source,
            "state": "present",
            "status": "ok",
            "age_seconds": 120,
            "window_start": "2026-06-01",
            "window_end": "2026-06-05",
        },
    )

    rows = materialization_plan_for_dag(
        dag,
        policies={
            "promote": MaterializationStepPolicy(
                "promote",
                substrate_sources=("machine",),
                max_age_seconds=60,
            )
        },
        requested_window=(date(2026, 6, 2), date(2026, 6, 4)),
    )

    assert rows[0].action == "skip"
    assert rows[0].reason == "substrate source statuses cover requested window"
    assert executable_steps(rows) == set()


def test_materialization_plan_runs_expired_substrate_status_when_window_is_not_covered(monkeypatch) -> None:
    dag = DAG("tiny")
    dag.add(Step("promote", fn=lambda: None))
    monkeypatch.setattr(
        "lynchpin.analysis.core.materialization_intelligence._substrate_source_status",
        lambda source, *, now: {
            "source": source,
            "state": "present",
            "status": "ok",
            "age_seconds": 120,
            "window_start": "2026-06-01",
            "window_end": "2026-06-03",
        },
    )

    rows = materialization_plan_for_dag(
        dag,
        policies={
            "promote": MaterializationStepPolicy(
                "promote",
                substrate_sources=("machine",),
                max_age_seconds=60,
            )
        },
        requested_window=(date(2026, 6, 2), date(2026, 6, 4)),
    )

    assert rows[0].action == "run"
    assert rows[0].reason == "substrate source status outside materialization age horizon: machine"
    assert executable_steps(rows) == {"promote"}
