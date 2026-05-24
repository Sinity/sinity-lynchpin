import json
from pathlib import Path

import pytest

from lynchpin.analysis.code_index.symbol_changes import build_active_symbol_changes
from lynchpin.analysis.code_index.symbol_diffs import build_active_symbol_diffs


def _write_json(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_symbol_changes_requires_materialized_symbol_index(tmp_path: Path) -> None:
    file_changes = _write_json(tmp_path / "file_changes.json", {"file_changes": []})

    with pytest.raises(FileNotFoundError, match="active symbol index is missing"):
        build_active_symbol_changes(
            symbol_index_file=tmp_path / "missing-symbols.json",
            file_changes_file=file_changes,
        )


def test_symbol_diffs_requires_materialized_commit_facts(tmp_path: Path) -> None:
    symbols = _write_json(tmp_path / "symbols.json", {"projects": []})
    snapshot = _write_json(tmp_path / "snapshot.json", {"projects": []})

    with pytest.raises(FileNotFoundError, match="active commit facts is missing"):
        build_active_symbol_diffs(
            commit_facts_file=tmp_path / "missing-commits.json",
            symbol_index_file=symbols,
            snapshot_file=snapshot,
        )


def test_symbol_diffs_allows_valid_empty_symbol_index(tmp_path: Path) -> None:
    commits = _write_json(tmp_path / "commits.json", {"commits": []})
    symbols = _write_json(tmp_path / "symbols.json", {"projects": []})
    snapshot = _write_json(tmp_path / "snapshot.json", {"projects": []})

    payload = build_active_symbol_diffs(
        commit_facts_file=commits,
        symbol_index_file=symbols,
        snapshot_file=snapshot,
    )

    assert payload["events"] == []
    assert any("active_symbol_index has no usable symbols" in c for c in payload["caveats"])
