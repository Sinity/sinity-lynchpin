from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from lynchpin.analysis.machine.baselines import analyze_machine_observational_baselines
from lynchpin.substrate.connection import apply_schema, connect


def test_machine_baselines_build_robust_groups_and_context(tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        for day in range(1, 9):
            for hour in (9, 15):
                conn.execute(
                    """
                    INSERT INTO machine_metric_sample (
                        observed_at, host, source, source_schema_version,
                        load_1m, mem_avail_mb, io_psi_full_avg10,
                        latency_oversleep_ms, dstate_task_count,
                        gpu_power_w, gpu_temp_c, gpu_pcie_gen, gpu_pcie_width,
                        gap_codes, refresh_id
                    ) VALUES (?, 'host', 'machine.telemetry', 2, ?, ?, ?, ?, ?, ?, ?, ?, 16, [], 'r1')
                    """,
                    [
                        datetime(2026, 5, day, hour, tzinfo=timezone.utc),
                        10.0 + day + (hour / 100),
                        32000 - day,
                        0.1 * day,
                        float(day),
                        day % 3,
                        50.0 + day,
                        40.0 + day,
                        4 if day >= 5 else 2,
                    ],
                )
    context = tmp_path / "machine_context_windows.json"
    context.write_text(
        json.dumps(
            {
                "windows": [
                    {
                        "projects": ["sinity-lynchpin"],
                        "provider": "codex",
                        "work_kind": "implementation",
                        "episode_count": 1,
                        "episodes": [{"kind": "load_pressure"}],
                    },
                    {
                        "projects": ["sinity-lynchpin"],
                        "provider": "codex",
                        "work_kind": "research",
                        "episode_count": 0,
                        "episodes": [],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    analysis = analyze_machine_observational_baselines(path=db, context_path=context)

    assert len(analysis.by_hour) == 2
    assert {row.sample_count for row in analysis.by_hour} == {8}
    assert analysis.by_hour[0].metrics[0].metric == "load_1m"
    assert analysis.by_hour[0].metrics[0].median is not None
    assert {row.key for row in analysis.by_hardware_regime} == {"gen2x16", "gen4x16"}
    assert {row.metric for row in analysis.daily_signals} >= {"p95_load_1m", "min_mem_avail_mb"}
    assert analysis.era_comparisons[0].interpretation == "observational before/after summary"
    project = next(row for row in analysis.work_context if row.dimension == "project")
    assert project.key == "sinity-lynchpin"
    assert project.window_count == 2
    assert project.episode_overlap_rate == 0.5
    assert "observational" in analysis.caveats[0]


def test_machine_baselines_require_context_artifact(tmp_path):
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)

    with pytest.raises(FileNotFoundError, match="machine context windows is missing"):
        analyze_machine_observational_baselines(
            path=db,
            context_path=tmp_path / "missing.json",
        )
