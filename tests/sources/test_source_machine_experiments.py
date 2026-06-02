import json
from datetime import date

from lynchpin.sources.machine_experiments import experiment_runs


def test_machine_experiments_source_reads_manifest_root(tmp_path):
    run_dir = tmp_path / "run-1"
    run_dir.mkdir()
    manifest = {
        "run_id": "run-1",
        "run_group_id": "grp1",
        "host": "sinnix-prime",
        "workload": "sinex.xtask",
        "command": ["xtask", "test"],
        "cwd": "/realm/project/sinex",
        "started_at": "2026-05-12T12:00:00+00:00",
        "ended_at": "2026-05-12T12:01:00+00:00",
        "monotonic_started_ns": 1,
        "monotonic_ended_ns": 60_000_000_000,
        "exit_status": 0,
        "execution_outcome": {"status": "success"},
        "service_profile": "full",
        "cache_profile": "warm",
        "measurement_context": {"host_boot_id": "boot1"},
        "planned_treatment": {"turbo": "on"},
        "nix_internal_json_path": "/tmp/run-1/nix-internal-json.ndjson",
        "git": {
            "root": "/realm/project/sinex",
            "head": "abc123",
            "branch": "master",
            "dirty": True,
        },
        "pre_state": {"cpu": {"governor": "performance"}},
        "post_state": {"cpu": {"governor": "performance"}},
        "notes": ["smoke"],
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest))

    rows = list(
        experiment_runs(
            start=date(2026, 5, 12),
            end=date(2026, 5, 12),
            root=tmp_path,
        )
    )

    assert len(rows) == 1
    assert rows[0].run_id == "run-1"
    assert rows[0].workload == "sinex.xtask"
    assert rows[0].command == ("xtask", "test")
    assert rows[0].run_group_id == "grp1"
    assert rows[0].monotonic_started_ns == 1
    assert rows[0].monotonic_ended_ns == 60_000_000_000
    assert rows[0].execution_outcome == {"status": "success"}
    assert rows[0].measurement_context == {"host_boot_id": "boot1"}
    assert rows[0].nix_internal_json_path == "/tmp/run-1/nix-internal-json.ndjson"
    assert rows[0].planned_treatment == {"turbo": "on"}
    assert rows[0].git_dirty is True
    assert rows[0].validation_status == "unvalidated"
    assert rows[0].validation_issues == ()
    assert rows[0].manifest_validation == {}


def test_machine_experiments_source_ignores_exported_templates(tmp_path):
    run_dir = tmp_path / "run-template"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(json.dumps({
        "schema": "lynchpin.machine_experiment.template.v1",
        "template_status": "planned_not_executed",
        "run_id": "run-template",
        "started_at": "2026-05-12T12:00:00+00:00",
    }))

    assert list(experiment_runs(root=tmp_path)) == []


def test_machine_experiments_source_reads_nested_exported_run_manifest(tmp_path):
    run_dir = tmp_path / "grp1" / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(json.dumps({
        "schema": "lynchpin.machine_experiment.run.v1",
        "run_id": "run-1",
        "run_group_id": "grp1",
        "host": "sinnix-prime",
        "workload": "xtask-stage:test",
        "command": ["nix", "build"],
        "started_at": "2026-05-12T12:00:00+00:00",
        "ended_at": "2026-05-12T12:01:00+00:00",
        "exit_status": 0,
        "monotonic_started_ns": 1,
        "monotonic_ended_ns": 2,
        "execution_outcome": {"status": "success"},
        "measurement_context": {"host_boot_id": "boot1"},
        "nix_internal_json_path": "/tmp/run-1/nix-internal-json.ndjson",
        "planned_treatment": {"selected_run": {"run_id": "run-1"}},
        "git": {"root": "/realm/project/sinex", "head": "abc123", "branch": "master", "dirty": False},
        "pre_state": {},
        "post_state": {},
    }))

    rows = list(experiment_runs(root=tmp_path))

    assert len(rows) == 1
    assert rows[0].run_id == "run-1"
    assert rows[0].run_group_id == "grp1"
    assert rows[0].nix_internal_json_path == "/tmp/run-1/nix-internal-json.ndjson"
    assert rows[0].manifest_path == run_dir / "manifest.json"
    assert rows[0].validation_status == "unvalidated"
