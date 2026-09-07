from datetime import date, timedelta
from pathlib import Path

import json
import pytest

from lynchpin.materialization import MaterializedDataset
from lynchpin.mcp.tools import materialization
from lynchpin.mcp.tools.public import lynchpin_status


def dataset(days: int = 2) -> MaterializedDataset:
    first = date(2020, 1, 1)
    return MaterializedDataset(
        name="activitywatch_event_index", status="partial", tail_stale=True,
        authority="fixture", query_surface="fixture", raw_roots=(),
        materialized_paths=tuple(Path(f"/fixture/partitions/{i}.ndjson") for i in range(days)),
        row_count=days, first_date=first, last_date=first + timedelta(days=days - 1),
        covered_dates=tuple(first + timedelta(days=i) for i in range(days)),
        materialization_hint="refresh the tail", reason="newer input exists",
    )


def test_default_public_status_does_not_expand_partition_lists(monkeypatch):
    row = dataset(10_000)
    monkeypatch.setattr(materialization, "audit_materialization", lambda: [row])
    result = lynchpin_status(view="materialization")
    assert result["ok"]
    assert len(json.dumps(result)) < 1500
    summary = result["data"][0]
    assert summary["materialized_path_count"] == 10_000
    assert summary["covered_date_count"] == 10_000
    assert summary["tail_stale"] is True
    assert summary["reason"] == "newer input exists"


def test_selected_detail_avoids_unrelated_audits(monkeypatch):
    row = dataset()
    monkeypatch.setattr(materialization, "audit_materialization", lambda: pytest.fail("unrelated audit"))
    monkeypatch.setattr(materialization, "_audit_one", lambda name, cfg: row if name == row.name else pytest.fail(name))
    result = lynchpin_status(view="materialization", source=row.name, detail=True)
    assert result["ok"]
    assert result["data"][0]["materialized_paths"] == [str(path) for path in row.materialized_paths]


def test_status_uses_inclusive_requested_window_and_preserves_tail_state(monkeypatch):
    monkeypatch.setattr(materialization, "audit_materialization", lambda: [dataset()])
    result = lynchpin_status(view="materialization", start="2020-01-02", end="2020-01-03")
    row = result["data"][0]
    assert row["coverage"]["requested_days"] == 2
    assert row["coverage"]["covered_days"] == 1
    assert row["coverage"]["coverage_ratio"] == 0.5
    assert row["tail_stale"] is True


@pytest.mark.parametrize("arguments", [
    {"detail": True}, {"source": "unknown"}, {"start": "2020-01-01"},
    {"start": "broken", "end": "2020-01-01"},
    {"start": "2020-01-02", "end": "2020-01-01"},
])
def test_invalid_inspection_filters_do_not_read_sources(monkeypatch, arguments):
    monkeypatch.setattr(materialization, "audit_materialization", lambda: pytest.fail("unexpected audit"))
    result = lynchpin_status(view="materialization", **arguments)
    assert result["ok"] is False
    assert result["error_code"] == "invalid_request"


def test_activitywatch_raw_status_reports_live_dates_not_recovery(monkeypatch, tmp_path):
    import sqlite3
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from lynchpin import materialization

    db = tmp_path / 'aw.db'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE events(starttime INTEGER)')
        conn.execute('CREATE INDEX events_starttime_index ON events(starttime)')
        for day in (5, 7):
            ns = int(datetime(2026, 6, day, 12, tzinfo=timezone.utc).timestamp() * 1e9)
            conn.execute('INSERT INTO events VALUES (?)', (ns,))
    recovery = tmp_path / 'events.ndjson'
    recovery.write_text('{}\n')
    recovery.with_suffix('.manifest.json').write_text(json.dumps({
        'row_count': 900, 'first_date': '2020-01-01', 'last_date': '2020-01-02',
    }))
    monkeypatch.setattr(materialization, 'canonical_activitywatch_events_path', lambda: recovery)
    monkeypatch.setattr(materialization, 'activitywatch_input_files', lambda cfg: (db,))
    row = materialization._activitywatch_dataset(SimpleNamespace(
        activitywatch_db=db, activitywatch_raw_dir=tmp_path,
    ))
    assert row.first_date.isoformat() == '2026-06-05'
    assert row.last_date.isoformat() == '2026-06-07'
    assert row.row_count is None
    assert row.materialized_paths == (db,)
