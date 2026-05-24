from dataclasses import dataclass
from datetime import date, datetime
import json

from lynchpin.analysis.ecosystem import current_state


@dataclass(frozen=True)
class _FakePack:
    start: datetime
    mode: str
    projects: tuple[str, ...]
    graph: str = "graph"


def test_run_current_state_analysis_materializes_json_and_markdown(monkeypatch, tmp_path):
    calls = {}

    def fake_context_pack(**kwargs):
        calls.update(kwargs)
        mode = "network" if kwargs.get("include_github_frontier") else "materialized"
        return _FakePack(start=kwargs["start"], mode=mode, projects=tuple(kwargs["projects"] or ()))

    monkeypatch.setattr(current_state, "context_pack", fake_context_pack)
    monkeypatch.setattr(current_state, "render_context_pack", lambda pack: f"# pack {pack.mode}")
    promoted = {}
    monkeypatch.setattr(
        current_state,
        "_promote_current_state_graph",
        lambda graph, **kwargs: promoted.update({"graph": graph, **kwargs}),
    )

    out = tmp_path / "current_state_context_pack.json"
    markdown_out = tmp_path / "current_state_context_pack.md"
    payload = current_state.run_current_state_analysis(
        start=date(2026, 5, 1),
        end=date(2026, 5, 5),
        out_file=out,
        markdown_out=markdown_out,
        projects=("lynchpin",),
        weak_tags=True,
        persist_weak_tags=True,
    )

    assert calls["start"].date() == date(2026, 5, 1)
    assert calls["end"].date() == date(2026, 5, 5)
    assert calls["projects"] == ("lynchpin",)
    assert calls["weak_tags"] is True
    assert calls["persist_weak_tags"] is True
    assert calls["exclude_analysis_artifacts"] == current_state.CURRENT_STATE_ARTIFACT_NAMES
    assert payload["projects"] == ["lynchpin"]
    assert json.loads(out.read_text(encoding="utf-8"))["projects"] == ["lynchpin"]
    assert markdown_out.read_text(encoding="utf-8") == "# pack materialized\n"
    assert promoted["graph"] == "graph"
    assert promoted["projects"] == ("lynchpin",)


def test_current_state_analysis_github_frontier_promotes_to_network(monkeypatch, tmp_path):
    calls = {}
    monkeypatch.setattr(
        current_state,
        "context_pack",
        lambda **kwargs: calls.update(kwargs) or _FakePack(start=kwargs["start"], mode="network", projects=()),
    )
    monkeypatch.setattr(current_state, "render_context_pack", lambda pack: "# pack")
    monkeypatch.setattr(current_state, "_promote_current_state_graph", lambda *args, **kwargs: None)

    current_state.run_current_state_analysis(
        start=date(2026, 5, 1),
        end=date(2026, 5, 5),
        out_file=tmp_path / "pack.json",
        include_github_frontier=True,
    )

    assert calls["include_github_frontier"] is True
