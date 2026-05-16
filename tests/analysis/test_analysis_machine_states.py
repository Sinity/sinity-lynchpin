from datetime import date

from lynchpin.analysis.machine.states import analyze_machine_work_states


def test_machine_work_states_define_pressure_and_work_states(tmp_path):
    context = tmp_path / "machine_context_windows.json"
    context.write_text(
        """{
          "windows": [
            {
              "window_id": "w1",
              "started_at": "2026-05-01T12:00:00+00:00",
              "ended_at": "2026-05-01T12:05:00+00:00",
              "duration_seconds": 300,
              "projects": ["sinity-lynchpin"],
              "source": "terminal_session",
              "work_kind": "test",
              "summary": "pytest tests/ -q",
              "episode_count": 2,
              "overlap_seconds": 240,
              "episodes": [
                {"kind": "io_pressure", "overlap_seconds": 120, "subject": null},
                {"kind": "gpu_link_regime", "overlap_seconds": 300, "subject": "gen2x16"}
              ],
              "caveats": ["observational"]
            },
            {
              "window_id": "w2",
              "started_at": "2026-05-02T12:00:00+00:00",
              "ended_at": "2026-05-02T12:01:00+00:00",
              "duration_seconds": 60,
              "projects": [],
              "source": "terminal_session",
              "summary": "direnv reload",
              "episode_count": 0,
              "overlap_seconds": 0,
              "episodes": []
            },
            {
              "window_id": "w3",
              "started_at": "2026-05-02T12:02:00+00:00",
              "ended_at": "2026-05-02T12:03:00+00:00",
              "duration_seconds": 60,
              "projects": ["sinnix"],
              "source": "terminal_session",
              "work_kind": "infrastructure:sinnix",
              "summary": "z ll cd",
              "episode_count": 0,
              "overlap_seconds": 0,
              "episodes": []
            },
            {
              "window_id": "w4",
              "started_at": "2026-05-02T12:04:00+00:00",
              "ended_at": "2026-05-02T12:05:00+00:00",
              "duration_seconds": 60,
              "projects": ["sinnix"],
              "source": "terminal_session",
              "work_kind": "infrastructure:sinnix",
              "summary": "nix build",
              "episode_count": 0,
              "overlap_seconds": 0,
              "episodes": []
            }
          ]
        }""",
        encoding="utf-8",
    )

    analysis = analyze_machine_work_states(start=date(2026, 5, 1), end=date(2026, 5, 2), context_path=context)

    assert analysis.window_count == 4
    assert analysis.pressure_state_counts == {"io_pressure": 1, "quiet": 3}
    assert analysis.work_state_counts["test_workload"] == 1
    assert analysis.work_state_counts["devshell_activation"] == 1
    assert analysis.work_state_counts["terminal_work"] == 1
    assert analysis.work_state_counts["nix_workload"] == 1
    assert analysis.repo_state_counts["sinity-lynchpin"] == 1
    assert analysis.repo_state_counts["unattributed"] == 1
    assert analysis.repo_state_counts["sinnix"] == 2
    assert analysis.hardware_regime_counts == {"gen2x16": 1}
    assert analysis.windows[0].pressure_kinds == ("io_pressure",)
    assert analysis.windows[0].hardware_regimes == ("gen2x16",)
    definitions = {(row.category, row.state): row.definition for row in analysis.state_definitions}
    assert definitions[("pressure_state", "io_pressure")] == "Pressure episode dominated by io_pressure."
    assert definitions[("pressure_state", "quiet")] == "No machine episode overlaps the work window."
    assert definitions[("work_state", "devshell_activation")] == "Direnv or nix develop environment activation/setup window."
    assert definitions[("repo_state", "sinity-lynchpin")] == "Canonical project slug for a singly attributed work window."
