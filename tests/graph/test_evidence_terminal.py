from datetime import date, datetime, timezone
from types import SimpleNamespace


UTC = timezone.utc


def _session() -> SimpleNamespace:
    return SimpleNamespace(
        cwd="/realm/project/sinity-lynchpin",
        project="sinity-lynchpin",
        start=datetime(2026, 6, 1, 12, tzinfo=UTC),
        end=datetime(2026, 6, 1, 12, 5, tzinfo=UTC),
        duration_s=300.0,
        command_count=3,
        error_count=1,
        category="development:other",
        commands_summary=("pytest", "ruff"),
    )


def test_add_terminal_includes_patterns_in_materialized_mode(monkeypatch) -> None:
    from lynchpin.graph import evidence_terminal

    ensure_calls = []
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, window, budget="inline": ensure_calls.append((name, window)),
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_terminal.shell_sessions", lambda **kwargs: (_session(),)
    )
    calls = []
    monkeypatch.setattr(
        "lynchpin.graph.terminal_patterns.detect_patterns",
        lambda **kwargs: calls.append(kwargs) or (
            SimpleNamespace(
                date=date(2026, 6, 1),
                kind="retry_spiral",
                cwd="/realm/project/sinity-lynchpin",
                project="sinity-lynchpin",
                command_count=3,
                error_count=3,
                duration_s=30.0,
                top_commands=("pytest",),
                confidence=0.7,
                summary="retry spiral",
            ),
        ),
    )

    nodes = []
    evidence_terminal.add_terminal(
        nodes,
        start=date(2026, 6, 1),
        end=date(2026, 6, 1),
        selected=set(),
    )

    assert [node.kind for node in nodes] == ["terminal_session", "terminal_pattern"]
    assert ensure_calls == [("atuin", (date(2026, 6, 1), date(2026, 6, 2)))]
    assert calls[0]["sessions"] == (_session(),)


def test_add_terminal_includes_patterns_in_network_mode(monkeypatch) -> None:
    from lynchpin.graph import evidence_terminal

    ensure_calls = []
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, window, budget="inline": ensure_calls.append((name, window)),
    )
    monkeypatch.setattr(
        "lynchpin.graph.evidence_terminal.shell_sessions", lambda **kwargs: (_session(),)
    )
    monkeypatch.setattr(
        "lynchpin.graph.terminal_patterns.detect_patterns",
        lambda **kwargs: (
            SimpleNamespace(
                date=date(2026, 6, 1),
                kind="retry_spiral",
                cwd="/realm/project/sinity-lynchpin",
                project="sinity-lynchpin",
                command_count=3,
                error_count=3,
                duration_s=30.0,
                top_commands=("pytest",),
                confidence=0.7,
                summary="retry spiral",
            ),
        ),
    )

    nodes = []
    evidence_terminal.add_terminal(
        nodes,
        start=date(2026, 6, 1),
        end=date(2026, 6, 1),
        selected=set(),
    )

    assert [node.kind for node in nodes] == ["terminal_session", "terminal_pattern"]
    assert ensure_calls == [("atuin", (date(2026, 6, 1), date(2026, 6, 2)))]
