from datetime import date
import json
from pathlib import Path
import subprocess

from lynchpin.cli import current_state
from lynchpin.graph.context_pack import ContextPackSubstrateRequiredError


def test_render_current_state_delegates_to_context_pack(monkeypatch):
    calls = {}

    def fake_context_pack(**kwargs):
        calls.update(kwargs)
        return object()

    monkeypatch.setattr(current_state, "context_pack", fake_context_pack)
    monkeypatch.setattr(current_state, "render_context_pack", lambda pack: "rendered")

    rendered = current_state.render_current_state(
        start=date(2026, 5, 1),
        end=date(2026, 5, 5),
        include_github_frontier=True,
    )

    assert rendered == "rendered"
    assert calls["start"].date() == date(2026, 5, 1)
    assert calls["end"].date() == date(2026, 5, 5)
    assert calls["include_github_frontier"] is True
    assert calls["prefer_substrate"] is True


def test_current_state_script_writes_output(monkeypatch, tmp_path):
    monkeypatch.setattr(current_state, "render_current_state", lambda **kwargs: "rendered")
    output = tmp_path / "pack.md"

    code = current_state.main(
        [
            "--start",
            "2026-05-01",
            "--end",
            "2026-05-05",
            "--github-frontier",
            "--output",
            str(output),
        ]
    )

    assert code == 0
    assert output.read_text(encoding="utf-8") == "rendered\n"


def test_render_current_state_can_render_context_pack(monkeypatch):
    calls = {}

    def fake_context_pack(**kwargs):
        calls.update(kwargs)
        return object()

    monkeypatch.setattr(current_state, "context_pack", fake_context_pack)
    monkeypatch.setattr(current_state, "render_context_pack", lambda pack: "context")

    rendered = current_state.render_current_state(
        start=date(2026, 5, 1),
        end=date(2026, 5, 5),
        projects=["lynchpin"],
        weak_tags=True,
        persist_weak_tags=True,
    )

    assert rendered == "context"
    assert calls["projects"] == ["lynchpin"]
    assert calls["weak_tags"] is True
    assert calls["persist_weak_tags"] is True


def test_current_state_tool_targets_cli_module():
    result = subprocess.run(
        [str(Path("tool/current-state")), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0
    assert "lynchpin.cli.current_state" in result.stdout
    assert "lynchpin.scripts" not in result.stderr


def test_render_current_state_github_frontier_promotes_context_pack_to_network(monkeypatch):
    calls = {}
    monkeypatch.setattr(current_state, "context_pack", lambda **kwargs: calls.update(kwargs) or object())
    monkeypatch.setattr(current_state, "render_context_pack", lambda pack: "context")

    current_state.render_current_state(
        start=date(2026, 5, 1),
        end=date(2026, 5, 5),
        include_github_frontier=True,
    )

    assert calls["include_github_frontier"] is True


def test_weak_tags_flag_promotes_to_context_pack(monkeypatch):
    calls = {}
    monkeypatch.setattr(current_state, "context_pack", lambda **kwargs: calls.update(kwargs) or object())
    monkeypatch.setattr(current_state, "render_context_pack", lambda pack: "context")

    rendered = current_state.render_current_state(
        start=date(2026, 5, 1),
        end=date(2026, 5, 5),
        weak_tags=True,
    )

    assert rendered == "context"
    assert calls["weak_tags"] is True


def test_render_current_state_can_render_json(monkeypatch):
    monkeypatch.setattr(current_state, "context_pack", lambda **kwargs: {"date": date(2026, 5, 1)})

    rendered = current_state.render_current_state(
        start=date(2026, 5, 1),
        end=date(2026, 5, 5),
        json_output=True,
    )

    assert json.loads(rendered) == {"date": "2026-05-01"}


def test_render_current_state_can_materialize_substrate(monkeypatch):
    calls = {}
    monkeypatch.setattr(current_state, "context_pack", lambda **kwargs: calls.update(kwargs) or object())
    monkeypatch.setattr(current_state, "render_context_pack", lambda pack: "context")

    rendered = current_state.render_current_state(
        start=date(2026, 5, 1),
        end=date(2026, 5, 5),
        materialize_substrate=True,
    )

    assert rendered == "context"
    assert calls["materialize_substrate"] is True


def test_current_state_script_reports_required_substrate_miss(monkeypatch, capsys):
    def fail_render(**kwargs):
        raise ContextPackSubstrateRequiredError("No materialized DuckDB graph matched.")

    monkeypatch.setattr(current_state, "render_current_state", fail_render)

    code = current_state.main(
        [
            "--start",
            "2026-05-01",
            "--end",
            "2026-05-05",
        ]
    )

    captured = capsys.readouterr()
    assert code == 1
    assert "No materialized DuckDB graph matched" in captured.err
