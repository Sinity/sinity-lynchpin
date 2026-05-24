"""Tests for terminal_patterns over the current ShellSession + AtuinCommand schema."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from lynchpin.graph.terminal_patterns import detect_patterns
from lynchpin.sources.terminal import AtuinCommand, ShellSession

UTC = timezone.utc


def _cmd(ts: datetime, command: str, exit_code: int = 0, cwd: str = "/repo", duration_ns: int | None = 1_000_000) -> AtuinCommand:
    return AtuinCommand(timestamp=ts, duration_ns=duration_ns, exit_code=exit_code, cwd=cwd, command=command)


def _session(start: datetime, end: datetime, cwd: str = "/repo", project: str = "demo", count: int = 4, errors: int = 1) -> ShellSession:
    return ShellSession(
        cwd=cwd,
        project=project,
        start=start,
        end=end,
        duration_s=(end - start).total_seconds(),
        command_count=count,
        error_count=errors,
        commands_summary=("pytest", "git"),
        category="coding",
    )


def test_detect_build_fix_loop(monkeypatch):
    base = datetime(2026, 5, 7, 12, 0, tzinfo=UTC)
    cmds = [
        _cmd(base, "pytest tests/", exit_code=1),
        _cmd(base + timedelta(seconds=10), "pytest tests/", exit_code=1),
        _cmd(base + timedelta(seconds=20), "pytest tests/", exit_code=0),  # fix
    ]
    monkeypatch.setattr(
        "lynchpin.sources.terminal.shell_sessions",
        lambda **k: [_session(base, base + timedelta(seconds=20), count=3, errors=2)],
    )
    monkeypatch.setattr(
        "lynchpin.sources.terminal.commands",
        lambda **k: iter(cmds),
    )

    patterns = detect_patterns(start=date(2026, 5, 7), end=date(2026, 5, 7))
    bf = [p for p in patterns if p.kind == "build_fix_loop"]
    assert len(bf) == 1
    assert "fix found" in bf[0].summary
    assert bf[0].error_count == 2


def test_detect_retry_spiral(monkeypatch):
    base = datetime(2026, 5, 7, 12, 0, tzinfo=UTC)
    cmds = [
        _cmd(base, "cargo build", exit_code=1),
        _cmd(base + timedelta(seconds=5), "cargo build", exit_code=1),
        _cmd(base + timedelta(seconds=10), "cargo build", exit_code=1),
        _cmd(base + timedelta(seconds=15), "cargo build", exit_code=1),
    ]
    monkeypatch.setattr(
        "lynchpin.sources.terminal.shell_sessions",
        lambda **k: [_session(base, base + timedelta(seconds=15), count=4, errors=4)],
    )
    monkeypatch.setattr(
        "lynchpin.sources.terminal.commands",
        lambda **k: iter(cmds),
    )

    patterns = detect_patterns(start=date(2026, 5, 7), end=date(2026, 5, 7))
    rs = [p for p in patterns if p.kind == "retry_spiral"]
    assert len(rs) == 1
    assert rs[0].command_count == 4
    assert "cargo" in rs[0].summary


def test_detect_retry_spiral_tolerates_blank_commands(monkeypatch):
    base = datetime(2026, 5, 7, 12, 0, tzinfo=UTC)
    cmds = [
        _cmd(base, "   ", exit_code=1),
        _cmd(base + timedelta(seconds=5), "\t", exit_code=1),
        _cmd(base + timedelta(seconds=10), "", exit_code=1),
    ]
    monkeypatch.setattr(
        "lynchpin.sources.terminal.shell_sessions",
        lambda **k: [_session(base, base + timedelta(seconds=10), count=3, errors=3)],
    )
    monkeypatch.setattr(
        "lynchpin.sources.terminal.commands",
        lambda **k: iter(cmds),
    )

    patterns = detect_patterns(start=date(2026, 5, 7), end=date(2026, 5, 7))
    rs = [p for p in patterns if p.kind == "retry_spiral"]
    assert len(rs) == 1
    assert rs[0].top_commands == ("?",)


def test_detect_long_running(monkeypatch):
    base = datetime(2026, 5, 7, 12, 0, tzinfo=UTC)
    # 90s in nanoseconds
    long_cmd = _cmd(base, "claude --resume", duration_ns=90 * 1_000_000_000)
    cmds = [
        _cmd(base - timedelta(seconds=1), "git status"),  # padding to make >=2
        long_cmd,
    ]
    monkeypatch.setattr(
        "lynchpin.sources.terminal.shell_sessions",
        lambda **k: [_session(base - timedelta(seconds=1), base + timedelta(seconds=90), count=2, errors=0)],
    )
    monkeypatch.setattr(
        "lynchpin.sources.terminal.commands",
        lambda **k: iter(cmds),
    )

    patterns = detect_patterns(start=date(2026, 5, 7), end=date(2026, 5, 7))
    lr = [p for p in patterns if p.kind == "long_running"]
    assert len(lr) == 1
    assert "claude" in lr[0].summary
    assert lr[0].duration_s == pytest.approx(90.0)


def test_detect_context_switch_high_activity(monkeypatch):
    base = datetime(2026, 5, 7, 12, 0, tzinfo=UTC)
    cmds = [_cmd(base + timedelta(seconds=i), f"cmd{i}", exit_code=0) for i in range(10)]
    monkeypatch.setattr(
        "lynchpin.sources.terminal.shell_sessions",
        lambda **k: [_session(base, base + timedelta(seconds=10), count=10, errors=0)],
    )
    monkeypatch.setattr(
        "lynchpin.sources.terminal.commands",
        lambda **k: iter(cmds),
    )

    patterns = detect_patterns(start=date(2026, 5, 7), end=date(2026, 5, 7))
    cs = [p for p in patterns if p.kind == "context_switch"]
    assert len(cs) == 1
    assert cs[0].command_count == 10


def test_empty_sessions_returns_empty(monkeypatch):
    monkeypatch.setattr("lynchpin.sources.terminal.shell_sessions", lambda **k: [])
    monkeypatch.setattr("lynchpin.sources.terminal.commands", lambda **k: iter(()))

    assert detect_patterns(start=date(2026, 5, 7), end=date(2026, 5, 7)) == ()


def test_project_filter_excludes_non_matching(monkeypatch):
    base = datetime(2026, 5, 7, 12, 0, tzinfo=UTC)
    cmds = [_cmd(base + timedelta(seconds=i), f"cmd{i}", exit_code=0) for i in range(10)]
    monkeypatch.setattr(
        "lynchpin.sources.terminal.shell_sessions",
        lambda **k: [_session(base, base + timedelta(seconds=10), count=10, errors=0, project="other")],
    )
    monkeypatch.setattr(
        "lynchpin.sources.terminal.commands",
        lambda **k: iter(cmds),
    )

    assert detect_patterns(start=date(2026, 5, 7), end=date(2026, 5, 7), projects=("demo",)) == ()
