from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_machine_analysis_mcp_tools_read_materialized_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    analysis_root = tmp_path / "analysis"
    analysis_root.mkdir()
    (analysis_root / "machine_episode_analysis.json").write_text(
        json.dumps({"episodes": [{"kind": "load_pressure", "host": "host", "started_at": "2026-05-01T12:00:00+00:00", "ended_at": "2026-05-01T12:05:00+00:00", "severity": 0.2}]}),
        encoding="utf-8",
    )
    (analysis_root / "machine_context_windows.json").write_text(
        json.dumps({"windows": [{"source": "polylogue_session", "window_id": "w1", "started_at": "2026-05-01T12:01:00+00:00", "ended_at": "2026-05-01T12:02:00+00:00", "projects": ["sinity-lynchpin"], "episode_count": 1}]}),
        encoding="utf-8",
    )
    (analysis_root / "machine_below_attribution.json").write_text(
        json.dumps({
            "episode_count": 1,
            "attributed_episode_count": 1,
            "pressure_episode_count": 1,
            "unattributed_pressure_episode_count": 0,
            "capture_count": 1,
            "caveats": [],
            "attributions": [{"episode_kind": "load_pressure", "capture_id": "cap1", "episode_started_at": "2026-05-01T12:00:00+00:00", "episode_ended_at": "2026-05-01T12:05:00+00:00", "overlap_seconds": 60.0, "severity": 0.2}],
        }),
        encoding="utf-8",
    )
    (analysis_root / "machine_observational_baselines.json").write_text(
        json.dumps({"generated_for": {"metrics": ["load_1m"]}, "caveats": ["observational"], "by_hour": [{"key": "12", "sample_count": 10}], "by_source": [], "by_hardware_regime": [], "daily_signals": [{"metric": "p95_load_1m", "sample_count": 8}], "era_comparisons": [], "work_context": []}),
        encoding="utf-8",
    )
    (analysis_root / "machine_experiment_claims.json").write_text(
        json.dumps({"run_count": 1, "controlled_claim_count": 0, "observational_claim_count": 1, "caveats": ["no controlled claims"], "claim_packs": [{"run_id": "run1", "workload": "xtask", "claim_mode": "manifest_observational", "started_at": "2026-05-01T12:00:00+00:00"}]}),
        encoding="utf-8",
    )

    config = type("Config", (), {"analysis_output_dir": analysis_root})()
    monkeypatch.setattr("lynchpin.analysis.core.io.get_config", lambda: config)

    from lynchpin.mcp.tools.machine import (
        machine_below_attributions,
        machine_context_windows,
        machine_episodes,
        machine_experiment_claims,
        machine_observational_baselines,
    )

    assert len(machine_episodes(start="2026-05-01", kind="load_pressure")) == 1
    assert len(machine_context_windows(project="sinity-lynchpin", has_episodes=True)) == 1
    result = machine_below_attributions(episode_kind="load_pressure")
    assert result["summary"]["attributed_episode_count"] == 1
    assert result["attributions"][0]["capture_id"] == "cap1"
    baselines = machine_observational_baselines(dimension="hour", key="12")
    assert baselines["summary"]["family_counts"]["hour"] == 1
    assert baselines["rows"][0]["sample_count"] == 10
    claims = machine_experiment_claims(claim_mode="manifest_observational")
    assert claims["summary"]["observational_claim_count"] == 1
    assert claims["claim_packs"][0]["run_id"] == "run1"
