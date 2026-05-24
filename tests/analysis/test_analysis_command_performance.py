from datetime import date, datetime, timezone

import pytest

from lynchpin.analysis.machine.command_performance import analyze_command_performance
from lynchpin.sources.terminal import AtuinCommand


def test_command_performance_requires_machine_work_states(tmp_path):
    with pytest.raises(FileNotFoundError, match="machine work-state windows is missing"):
        analyze_command_performance(
            start=date(2026, 5, 1),
            end=date(2026, 5, 1),
            state_path=tmp_path / "missing.json",
            commands_iterable=[],
        )


def test_command_performance_joins_commands_to_machine_states(tmp_path):
    states = tmp_path / "machine_work_state_windows.json"
    states.write_text(
        """{
          "windows": [
            {
              "started_at": "2026-05-01T12:00:00+00:00",
              "ended_at": "2026-05-01T12:10:00+00:00",
              "pressure_state": "io_pressure",
              "work_state": "test_workload"
            }
          ]
        }""",
        encoding="utf-8",
    )
    commands = [
        AtuinCommand(
            timestamp=datetime(2026, 5, 1, 12, 1, tzinfo=timezone.utc),
            duration_ns=2_000_000_000,
            exit_code=0,
            cwd="/realm/project/sinity-lynchpin",
            command="pytest tests/ -q",
        ),
        AtuinCommand(
            timestamp=datetime(2026, 5, 1, 13, 1, tzinfo=timezone.utc),
            duration_ns=1_000_000_000,
            exit_code=1,
            cwd="/realm/project/sinity-lynchpin",
            command="direnv reload",
        ),
    ]

    analysis = analyze_command_performance(
        start=date(2026, 5, 1),
        end=date(2026, 5, 1),
        state_path=states,
        commands_iterable=commands,
    )

    assert analysis.command_count == 2
    assert analysis.windows[0].tool == "pytest"
    assert analysis.windows[0].machine_pressure_state == "io_pressure"
    assert analysis.windows[0].machine_work_state == "test_workload"
    assert analysis.windows[0].machine_overlap_seconds == 2.0
    assert analysis.windows[1].tool == "direnv"
    summaries = {row.tool: row for row in analysis.tools}
    assert summaries["pytest"].pressure_overlap_count == 1
    assert summaries["direnv"].error_count == 1


def test_command_performance_classifies_normalized_command_prefixes(tmp_path):
    states = tmp_path / "machine_work_state_windows.json"
    states.write_text('{"windows":[]}', encoding="utf-8")
    commands = [
        ("ANTHROPIC_API_KEY= hermes", "ai_agent", "hermes"),
        ("! rclone config reconnect gdrive:", "file_transfer", "rclone"),
        ("sudo systemctl start transmission.service", "system", "systemctl"),
        ("sudo mkdir /cache", "shell_utility", "mkdir"),
        ("yazi /realm/project", "navigation", "yazi"),
        ("nvim logs.raw-log.md", "editor", "nvim"),
        ("tldr rg", "docs", "tldr"),
    ]
    atuin = [
        AtuinCommand(
            timestamp=datetime(2026, 5, 1, 12, idx, tzinfo=timezone.utc),
            duration_ns=1_000_000_000,
            exit_code=0,
            cwd="/realm/project/sinity-lynchpin",
            command=command,
        )
        for idx, (command, _, _) in enumerate(commands)
    ]

    analysis = analyze_command_performance(
        start=date(2026, 5, 1),
        end=date(2026, 5, 1),
        state_path=states,
        commands_iterable=atuin,
    )

    by_command = {row.command: row for row in analysis.windows}
    for command, tool, prefix in commands:
        assert by_command[command].tool == tool
        assert by_command[command].command_prefix == prefix
