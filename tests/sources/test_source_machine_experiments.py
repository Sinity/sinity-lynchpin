import json
from datetime import date

from lynchpin.sources.machine_experiments import experiment_runs


def test_machine_experiments_source_reads_manifest_root(tmp_path):
    run_dir = tmp_path / "run-1"
    run_dir.mkdir()
    manifest = {
        "run_id": "run-1",
        "host": "sinnix-prime",
        "workload": "sinex.xtask",
        "command": ["xtask", "test"],
        "cwd": "/realm/project/sinex",
        "started_at": "2026-05-12T12:00:00+00:00",
        "ended_at": "2026-05-12T12:01:00+00:00",
        "exit_status": 0,
        "service_profile": "full",
        "cache_profile": "warm",
        "planned_treatment": {"turbo": "on"},
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
    assert rows[0].planned_treatment == {"turbo": "on"}
    assert rows[0].git_dirty is True
