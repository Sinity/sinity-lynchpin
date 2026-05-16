from datetime import date

from lynchpin.analysis.machine.devshell import analyze_devshell_performance


def test_devshell_performance_filters_nix_and_direnv_commands(tmp_path):
    commands = tmp_path / "command_performance_windows.json"
    commands.write_text(
        """{
          "windows": [
            {
              "started_at": "2026-05-01T12:00:00+00:00",
              "ended_at": "2026-05-01T12:00:30+00:00",
              "duration_seconds": 30,
              "exit_code": 0,
              "project": "sinity-lynchpin",
              "command": "direnv reload",
              "machine_pressure_state": "io_pressure",
              "machine_work_state": "devshell_activation",
              "machine_overlap_seconds": 30
            },
            {
              "started_at": "2026-05-01T12:01:00+00:00",
              "ended_at": "2026-05-01T12:01:45+00:00",
              "duration_seconds": 45,
              "exit_code": 1,
              "project": "sinity-lynchpin",
              "command": "nix develop",
              "machine_pressure_state": "quiet",
              "machine_work_state": "nix_workload",
              "machine_overlap_seconds": 0
            },
            {
              "started_at": "2026-05-01T12:02:00+00:00",
              "ended_at": "2026-05-01T12:02:01+00:00",
              "duration_seconds": 1,
              "exit_code": 0,
              "command": "git status"
            }
          ]
        }""",
        encoding="utf-8",
    )

    analysis = analyze_devshell_performance(
        start=date(2026, 5, 1),
        end=date(2026, 5, 1),
        command_path=commands,
    )

    assert analysis.command_count == 2
    summaries = {row.command_class: row for row in analysis.summaries}
    assert summaries["direnv_activation"].median_duration_seconds == 30
    assert summaries["direnv_activation"].pressure_overlap_count == 1
    assert summaries["nix_develop"].error_count == 1
    assert any("command-text based" in caveat for caveat in analysis.caveats)
