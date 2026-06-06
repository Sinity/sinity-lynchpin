from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

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
            materialization_hint="refresh",
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


def test_snapshot_daily_signals_ensures_products_before_promoting(monkeypatch) -> None:
    import lynchpin.cli.substrate_snapshot as snapshot

    ensure_calls: list[tuple[str, tuple[date, date] | None]] = []
    read_state = {"ensured": False}

    class Conn:
        def execute(self, *_args, **_kwargs):
            return self

        def fetchone(self):
            return (0,)

    class Connect:
        def __enter__(self):
            return Conn()

        def __exit__(self, *_args):
            return None

    def fake_ensure_materialized(name: str, *, window=None):
        ensure_calls.append((name, window))
        if name == "personal_daily_signals":
            read_state["ensured"] = True
        return SimpleNamespace(to_json=lambda: {"name": name, "status": "ready"})

    read_windows: list[tuple[str, date | None, date | None, bool]] = []

    def fake_iter_personal_daily_signals(*, start=None, end=None, ensure=True):
        assert read_state["ensured"]
        read_windows.append(("personal_daily_signals", start, end, ensure))
        yield SimpleNamespace(
            source="keylog",
            date=date(2026, 5, 1),
            metric="keypress_count",
            value=5.0,
            dimensions={},
        )

    def fake_iter_activity_content_days(*, start=None, end=None, ensure=True):
        read_windows.append(("activity_content_days", start, end, ensure))
        return iter(())

    def fake_iter_activity_title_usage(*, start=None, end=None, ensure=True):
        read_windows.append(("activity_title_usage", start, end, ensure))
        return iter(())

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)
    monkeypatch.setattr(
        "lynchpin.sources.personal_signals.iter_personal_daily_signals",
        fake_iter_personal_daily_signals,
    )
    monkeypatch.setattr("lynchpin.sources.activity_content.iter_activity_content_days", fake_iter_activity_content_days)
    monkeypatch.setattr("lynchpin.sources.activity_content.iter_activity_title_usage", fake_iter_activity_title_usage)
    monkeypatch.setattr("lynchpin.sources.title_metadata.title_metadata_path", lambda: Path("fixture.duckdb"))
    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: Path("fixture.duckdb"))
    monkeypatch.setattr("lynchpin.substrate.connection.apply_schema", lambda _conn: None)
    monkeypatch.setattr("lynchpin.substrate.connection.connect", lambda *_args, **_kwargs: Connect())
    monkeypatch.setattr(
        "lynchpin.substrate.personal.promote_title_classifications_from_path",
        lambda *_args, **_kwargs: 0,
    )
    monkeypatch.setattr("lynchpin.substrate.personal.promote_activity_content_days", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr("lynchpin.substrate.personal.promote_activity_content_buckets", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr("lynchpin.substrate.personal.promote_activity_title_usage", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr("lynchpin.substrate.personal.promote_personal_daily_signals", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(
        "lynchpin.analysis.active.substrate_promote_status.record_source_status",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        snapshot,
        "_snapshot_refresh_id",
        lambda *, start, end, projects: "snapshot-fixture",
    )

    snapshot._promote_snapshot_daily_signals(
        start=date(2026, 5, 1),
        end=date(2026, 5, 2),
        projects=(),
    )

    assert ensure_calls == [
        ("title_metadata", (date(2026, 5, 1), date(2026, 5, 2))),
        ("activity_content", (date(2026, 5, 1), date(2026, 5, 2))),
        ("personal_daily_signals", (date(2026, 5, 1), date(2026, 5, 2))),
    ]
    assert read_windows == [
        ("personal_daily_signals", date(2026, 5, 1), date(2026, 5, 2), False),
        ("activity_content_days", date(2026, 5, 1), date(2026, 5, 2), False),
        ("activity_title_usage", date(2026, 5, 1), date(2026, 5, 2), False),
    ]
