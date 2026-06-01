from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

UTC = timezone.utc


def reload_config(monkeypatch: pytest.MonkeyPatch) -> None:
    import lynchpin.core.config as cfg_mod

    cfg_mod._CONFIG = None
    monkeypatch.setattr(cfg_mod, "_CONFIG", None, raising=False)


def stub_live_promote_sources(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("lynchpin.sources.polylogue.work_events", lambda *args, **kwargs: [])
    monkeypatch.setattr("lynchpin.sources.spotify.iter_streams", lambda *args, **kwargs: iter(()))
    monkeypatch.setattr("lynchpin.sources.machine.metric_samples", lambda *args, **kwargs: iter(()))
    monkeypatch.setattr("lynchpin.sources.machine.service_states", lambda *args, **kwargs: iter(()))
    monkeypatch.setattr("lynchpin.sources.machine.gpu_samples", lambda *args, **kwargs: iter(()))
    monkeypatch.setattr("lynchpin.sources.machine.network_samples", lambda *args, **kwargs: iter(()))
    monkeypatch.setattr(
        "lynchpin.sources.machine.readiness",
        lambda: type(
            "MachineReadiness",
            (),
            {
                "status": "empty",
                "reason": "test fixture",
                "live_db": tmp_path / "telemetry.sqlite",
                "live_rows": 0,
            },
        )(),
    )
    monkeypatch.setattr("lynchpin.sources.machine_experiments.experiment_runs", lambda *args, **kwargs: iter(()))
    monkeypatch.setattr("lynchpin.sources.machine_experiments.experiment_root", lambda: tmp_path / "experiments")
    monkeypatch.setattr("lynchpin.sources.xtask_history.iter_all_invocations", lambda *args, **kwargs: iter(()))
    monkeypatch.setattr("lynchpin.sources.xtask_history.iter_all_stage_timings", lambda *args, **kwargs: iter(()))
    monkeypatch.setattr("lynchpin.sources.xtask_history.iter_all_test_results", lambda *args, **kwargs: iter(()))


def dt(y: int, m: int, d: int, h: int = 12) -> datetime:
    return datetime(y, m, d, h, 0, 0, tzinfo=UTC)


def make_commit_entry(sha: str, project: str = "lynchpin") -> dict[str, Any]:
    return {
        "sha": sha,
        "short_sha": sha[:7],
        "project": project,
        "author": "Sinity",
        "timestamp": "2026-05-01T12:00:00+00:00",
        "date": "2026-05-01",
        "subject": "feat: test",
        "parent_count": 1,
        "default_branch": "master",
        "head": None,
        "conventional_kind": "feat",
        "conventional_scope": None,
        "conventional_signature": "feat",
        "conventional_description": "test",
        "breaking_change": False,
        "github_refs": {"prs": [], "issues": []},
        "files_changed": 2,
        "classified_files_changed": 2,
        "categories": {},
        "path_roots": {"src": 2},
        "change_types": {"modified": 2},
        "paths": ["src/a.py", "src/b.py"],
    }


def make_pr_dict(project: str = "lynchpin", state: str = "merged") -> dict[str, Any]:
    return {
        "project": project,
        "number": 1,
        "title": "feat: test PR",
        "state": state,
        "url": f"https://github.com/sinity/{project}/pull/1",
        "author": "Sinity",
        "created_at": "2026-05-01T10:00:00+00:00",
        "closed_at": "2026-05-01T12:00:00+00:00",
        "merged_at": "2026-05-01T12:00:00+00:00" if state == "merged" else None,
        "review_count": 1,
        "review_decisions": ["approved"],
        "review_round_count": 1,
        "reviewer_count": 1,
        "reviewers": ["reviewer1"],
        "review_comment_count": 2,
        "top_level_comment_count": 1,
        "changes_requested_count": 0,
        "approval_count": 1,
        "dismissed_count": 0,
        "time_to_first_review_minutes": 30.0,
        "time_to_close_minutes": 120.0,
        "time_to_merge_minutes": 120.0 if state == "merged" else None,
        "final_decision": "approved",
        "friction_signals": [],
    }


def setup_substrate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import lynchpin.substrate.connection as duck_conn

    db_path = tmp_path / "substrate.duckdb"

    monkeypatch.setenv("LYNCHPIN_LOCAL_ROOT", str(tmp_path))
    reload_config(monkeypatch)
    monkeypatch.setattr(duck_conn, "substrate_path", lambda: db_path)

    from lynchpin.substrate.connection import apply_schema, connect

    with connect(db_path) as conn:
        apply_schema(conn)

    return db_path
