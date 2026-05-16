from __future__ import annotations

import pytest


def test_best_refresh_id_rejects_non_identifier_table_name() -> None:
    from lynchpin.mcp.tools._utils import best_refresh_id

    class Conn:
        def execute(self, _sql: str):
            raise AssertionError("invalid table names must not reach SQL")

    with pytest.raises(ValueError, match="invalid substrate table identifier"):
        best_refresh_id(Conn(), "commit_fact; DROP TABLE commit_fact")
