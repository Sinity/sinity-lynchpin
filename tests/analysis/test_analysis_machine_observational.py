from datetime import date

import pytest

from lynchpin.analysis.machine.observational import analyze_observational_command_deltas


def test_observational_command_deltas_require_command_windows(tmp_path):
    with pytest.raises(FileNotFoundError, match="command performance windows is missing"):
        analyze_observational_command_deltas(command_path=tmp_path / "missing.json")


def test_observational_command_deltas_match_by_tool_and_work_state(tmp_path):
    commands = tmp_path / "command_performance_windows.json"
    commands.write_text(
        """{
          "windows": [
            {"tool": "pytest", "machine_work_state": "test_workload", "machine_pressure_state": "quiet", "duration_seconds": 2, "exit_code": 0},
            {"tool": "pytest", "machine_work_state": "test_workload", "machine_pressure_state": "quiet", "duration_seconds": 4, "exit_code": 0},
            {"tool": "pytest", "machine_work_state": "test_workload", "machine_pressure_state": "io_pressure", "duration_seconds": 10, "exit_code": 0},
            {"tool": "pytest", "machine_work_state": "test_workload", "machine_pressure_state": "io_pressure", "duration_seconds": 12, "exit_code": 1},
            {"tool": "nix", "machine_work_state": "nix_workload", "machine_pressure_state": "io_pressure", "duration_seconds": 20, "exit_code": 0}
          ]
        }""",
        encoding="utf-8",
    )

    analysis = analyze_observational_command_deltas(
        start=date(2026, 5, 1),
        end=date(2026, 5, 1),
        command_path=commands,
        min_cohort_size=2,
    )

    assert analysis.cohort_count == 3
    assert analysis.delta_count == 1
    assert len(analysis.cohorts) == 3
    assert len(analysis.deltas) == 1
    delta = analysis.deltas[0]
    assert delta.tool == "pytest"
    assert delta.work_state == "test_workload"
    assert delta.pressure_state == "io_pressure"
    assert delta.baseline_state == "quiet"
    assert delta.median_delta_seconds == 8.0
    assert delta.error_rate_delta == 0.5
    assert any("observational association only" in caveat for caveat in analysis.caveats)


def test_observational_command_deltas_refuse_cross_work_state_baseline(tmp_path):
    commands = tmp_path / "command_performance_windows.json"
    commands.write_text(
        """{
          "windows": [
            {"tool": "pytest", "machine_work_state": "test_workload", "machine_pressure_state": "io_pressure", "duration_seconds": 10, "exit_code": 0},
            {"tool": "pytest", "machine_work_state": "test_workload", "machine_pressure_state": "io_pressure", "duration_seconds": 12, "exit_code": 0},
            {"tool": "pytest", "machine_work_state": "build_workload", "machine_pressure_state": "quiet", "duration_seconds": 2, "exit_code": 0},
            {"tool": "pytest", "machine_work_state": "build_workload", "machine_pressure_state": "quiet", "duration_seconds": 4, "exit_code": 0}
          ]
        }""",
        encoding="utf-8",
    )

    analysis = analyze_observational_command_deltas(command_path=commands, min_cohort_size=2)

    assert analysis.delta_count == 0
    assert analysis.deltas == []
    assert "no tool/work-state cohort had both pressure and quiet baseline samples" in analysis.caveats
