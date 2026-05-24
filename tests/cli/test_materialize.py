from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

def test_materialize_history_all_derives_window(monkeypatch, tmp_path: Path) -> None:
    from lynchpin.cli import materialize
    from lynchpin.materialization import MaterializedDataset

    rows = [
        MaterializedDataset(
            name="webhistory",
            status="ready",
            authority="fixture",
            query_surface="fixture",
            materialized_paths=(),
            raw_roots=(),
            row_count=1,
            first_date=date(2013, 3, 27),
            last_date=date(2026, 5, 23),
            refresh_command="refresh",
            reason="ready",
        )
    ]
    forwarded = {}

    monkeypatch.setattr(materialize, "plan_materializations", lambda force=False: [])
    monkeypatch.setattr(materialize, "run_materialization_plan", lambda plan: [])
    monkeypatch.setattr(materialize, "audit_materialization", lambda: rows)

    def fake_snapshot(argv: list[str]) -> int:
        forwarded["argv"] = argv
        return 0

    import lynchpin.cli.substrate_snapshot as snapshot

    monkeypatch.setattr(snapshot, "main", fake_snapshot)
    monkeypatch.setenv("LYNCHPIN_LOCAL_ROOT", str(tmp_path))

    code = materialize.main(["--all", "--promote", "--history", "all"])

    assert code == 0
    assert forwarded["argv"][:4] == [
        "--start",
        "2013-03-27",
        "--end",
        "2026-05-24",
    ]
    assert "--mode" not in forwarded["argv"]


def test_materialize_rejects_mode_option(monkeypatch) -> None:
    from lynchpin.cli import materialize

    monkeypatch.setattr(materialize, "plan_materializations", lambda force=False: [])
    monkeypatch.setattr(materialize, "run_materialization_plan", lambda plan: [])

    with pytest.raises(SystemExit) as exc:
        materialize.main(["--all", "--mode", "local-heavy"])

    assert exc.value.code == 2
