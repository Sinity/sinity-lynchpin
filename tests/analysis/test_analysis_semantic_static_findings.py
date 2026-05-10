"""Tests for analysis.interpretation.semantic_static_findings.

Covers the producer's deterministic plumbing: payload shape, missing-binary
fallback, file-change matching. Live semgrep invocation is exercised only
when the binary is present (skipped otherwise) so the test suite stays
independent of devshell state.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from lynchpin.analysis.interpretation.semantic_static_findings import (
    _changed_paths,
    _is_recently_changed,
    _project_map,
    _resolve_project,
    build_active_semantic_static_findings,
)


def test_project_map_filters_by_selection() -> None:
    payload = {
        "projects": [
            {"project": "demo", "path": "/tmp/demo"},
            {"project": "other", "path": "/tmp/other"},
        ]
    }
    assert _project_map(payload, set()) == {"demo": "/tmp/demo", "other": "/tmp/other"}
    assert _project_map(payload, {"demo"}) == {"demo": "/tmp/demo"}


def test_changed_paths_groups_per_project() -> None:
    payload = {
        "file_changes": [
            {"project": "demo", "path": "src/a.py"},
            {"project": "demo", "path": "src/b.py"},
            {"project": "other", "path": "lib.rs"},
        ]
    }
    grouped = _changed_paths(payload, set())
    assert grouped["demo"] == {"src/a.py", "src/b.py"}
    assert grouped["other"] == {"lib.rs"}


def test_is_recently_changed_handles_leading_slash() -> None:
    grouped = {"demo": {"src/a.py"}}
    assert _is_recently_changed("demo", "src/a.py", grouped) is True
    assert _is_recently_changed("demo", "/src/a.py", grouped) is True
    assert _is_recently_changed("demo", "src/missing.py", grouped) is False
    assert _is_recently_changed("absent", "src/a.py", grouped) is False


def test_resolve_project_maps_absolute_path() -> None:
    project, rel = _resolve_project("/tmp/demo/src/x.py", {"demo": "/tmp/demo"})
    assert project == "demo"
    assert rel == "src/x.py"


def test_resolve_project_falls_back_to_lynchpin_self() -> None:
    project, rel = _resolve_project("/elsewhere/foo.py", {"demo": "/tmp/demo"})
    assert project == "sinity-lynchpin"
    assert rel == "/elsewhere/foo.py"


def test_build_emits_caveat_when_semgrep_missing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "lynchpin.analysis.interpretation.semantic_static_findings._which_semgrep",
        lambda: None,
    )
    monkeypatch.setattr(
        "lynchpin.analysis.interpretation.semantic_static_findings.load_json_if_exists",
        lambda _path: None,
    )
    payload = build_active_semantic_static_findings(
        repo_root=tmp_path,
    )
    assert payload["findings"] == []
    assert any("semgrep binary not found" in c for c in payload["caveats"])


@pytest.mark.skipif(shutil.which("semgrep") is None, reason="semgrep not installed")
def test_semgrep_pack_runs_against_synthetic_repo(tmp_path: Path) -> None:
    """Live semgrep invocation against a controlled synthetic file."""
    target = tmp_path / "lynchpin" / "analysis" / "bad.py"
    target.parent.mkdir(parents=True)
    target.write_text(
        "import requests\n\n"
        "def fetch():\n"
        "    return requests.get('https://api.example.com')\n"
    )

    from lynchpin.analysis.interpretation.semantic_static_findings import _run_semgrep, _RULES_DIR

    findings = _run_semgrep(
        semgrep_path=shutil.which("semgrep"),
        rules_dir=_RULES_DIR,
        target=str(tmp_path),
        recently_changed={"sinity-lynchpin": {"lynchpin/analysis/bad.py"}},
        project_root_map={},  # let fallback assign sinity-lynchpin
    )
    rule_ids = {f["rule_id"] for f in findings}
    assert any("network-call-in-analysis" in rid for rid in rule_ids)


def test_payload_carries_methodology_and_inputs(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "lynchpin.analysis.interpretation.semantic_static_findings._which_semgrep",
        lambda: None,
    )
    monkeypatch.setattr(
        "lynchpin.analysis.interpretation.semantic_static_findings.load_json_if_exists",
        lambda _path: None,
    )
    payload = build_active_semantic_static_findings(repo_root=tmp_path)
    assert "methodology" in payload
    assert "rules_dir" in payload["inputs"]
    assert payload["projects"] == []
    json.dumps(payload)  # round-trips
