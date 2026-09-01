from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import duckdb
import pytest

from tests.mcp.conftest import setup_substrate


def test_nominal_mcp_read_routes_open_read_only_connections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Views and public metadata must work once the serving file is immutable."""
    db_path = setup_substrate(tmp_path, monkeypatch)
    calls: list[bool] = []

    @contextmanager
    def recording_connect(path=None, *, read_only=False, **_kwargs):
        calls.append(read_only)
        target = Path(path) if path is not None else db_path
        with duckdb.connect(str(target), read_only=read_only) as conn:
            yield conn

    monkeypatch.setattr("lynchpin.substrate.connection.connect", recording_connect)

    from lynchpin.mcp.tools.public import _project_day_timeline_meta
    from lynchpin.mcp.tools.views import (
        closure_chain_walks,
        file_overlap_edges,
        project_day_correlations,
        symbol_overlap_edges,
    )

    assert project_day_correlations(refresh_id="missing") == []
    assert closure_chain_walks(refresh_id="missing") == []
    assert file_overlap_edges(we_refresh_id="missing", commit_refresh_id="missing") == []
    assert symbol_overlap_edges(we_refresh_id="missing", commit_refresh_id="missing") == []
    _project_day_timeline_meta(refresh_id="missing", start=None, end=None, project=None)

    assert calls and all(calls)
