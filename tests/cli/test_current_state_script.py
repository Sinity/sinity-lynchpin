from datetime import date
import json
from pathlib import Path
import subprocess

from lynchpin.cli import current_state


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
    assert calls["mode"] == "network"
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
        mode="local-heavy",
        projects=["lynchpin"],
        semantic=True,
        persist_semantic=True,
    )

    assert rendered == "context"
    assert calls["mode"] == "local-heavy"
    assert calls["projects"] == ["lynchpin"]
    assert calls["semantic"] is True
    assert calls["persist_semantic"] is True


def test_current_state_tool_targets_cli_module():
    result = subprocess.run(
        [str(Path("tool/current-state")), "--help"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0
    assert "current-state" in result.stdout
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

    assert calls["mode"] == "network"


def test_semantic_flag_promotes_to_context_pack(monkeypatch):
    calls = {}
    monkeypatch.setattr(current_state, "context_pack", lambda **kwargs: calls.update(kwargs) or object())
    monkeypatch.setattr(current_state, "render_context_pack", lambda pack: "context")

    rendered = current_state.render_current_state(
        start=date(2026, 5, 1),
        end=date(2026, 5, 5),
        semantic=True,
    )

    assert rendered == "context"
    assert calls["semantic"] is True


def test_render_current_state_can_render_json(monkeypatch):
    monkeypatch.setattr(current_state, "context_pack", lambda **kwargs: {"date": date(2026, 5, 1)})

    rendered = current_state.render_current_state(
        start=date(2026, 5, 1),
        end=date(2026, 5, 5),
        json_output=True,
    )

    assert json.loads(rendered) == {"date": "2026-05-01"}
