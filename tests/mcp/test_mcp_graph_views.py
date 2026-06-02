from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.mcp.conftest import make_commit_entry, setup_substrate, stub_live_promote_sources


def test_project_day_correlations_returns_empty_on_empty_substrate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.tools.views import project_day_correlations

    assert project_day_correlations() == []


def test_project_day_correlations_returns_dataclass_dict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_substrate(tmp_path, monkeypatch)
    stub_live_promote_sources(monkeypatch, tmp_path)

    cf_file = tmp_path / "commit_facts.json"
    cf_file.write_text(json.dumps({"generated_at_utc": "2026-05-08T00:00:00+00:00", "commits": [make_commit_entry("abc" + "0" * 37)]}))

    from lynchpin.analysis.active.substrate_promote import (
        SOURCE_COMMITS,
        run_substrate_promote,
    )

    run_substrate_promote(
        commit_facts_file=str(cf_file),
        file_changes_file=str(tmp_path / "no_fc.json"),
        symbol_changes_file=str(tmp_path / "no_sym.json"),
        sources={SOURCE_COMMITS},
        write_evidence_graph=False,
    )

    from lynchpin.mcp.tools.views import project_day_correlations

    result = project_day_correlations()
    assert isinstance(result, list)
    if result:
        assert {"project", "date", "commit_count", "source_count"}.issubset(result[0])


def test_closure_chain_walks_returns_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_substrate(tmp_path, monkeypatch)

    from lynchpin.mcp.tools.views import closure_chain_walks

    assert closure_chain_walks() == []
