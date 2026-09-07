from datetime import datetime, timedelta, timezone
import json
import sqlite3

from lynchpin import materialization
from lynchpin.core.config import get_config
from lynchpin.core.primitives import logical_date
from lynchpin.ingest.activitywatch_materialize import materialize_activitywatch_events
from lynchpin.ingest.activitywatch_event_index_materialize import materialize_activitywatch_event_index
from lynchpin.ingest.activitywatch_derived_materialize import materialize_activitywatch_derived
from lynchpin.ingest.arbtt_materialize import ARBTT_EVENTS_SCHEMA_VERSION
from lynchpin.mcp.tools.public import lynchpin_personal
from lynchpin.sources.activitywatch_raw import canonical_activitywatch_events_path
from lynchpin.sources.activitywatch_event_index import activitywatch_event_index_manifest_path
from lynchpin.sources.arbtt import arbtt_events_path, arbtt_manifest_path


def test_public_focus_query_refreshes_new_day_and_later_events(monkeypatch):
    cfg = get_config()
    db = cfg.activitywatch_db
    db.parent.mkdir(parents=True, exist_ok=True)
    wal_keeper = sqlite3.connect(db)
    wal_keeper.execute("PRAGMA journal_mode=WAL")
    with wal_keeper:
        wal_keeper.executescript("""
            CREATE TABLE buckets(id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE events(bucketrow INTEGER, starttime INTEGER, endtime INTEGER, data TEXT);
            INSERT INTO buckets VALUES (1, 'aw-watcher-window_fixture'), (2, 'aw-watcher-afk_fixture');
        """)

    def append(start):
        end = start + timedelta(minutes=30)
        with sqlite3.connect(db) as conn:
            for bucket, payload in [(1, {"app": "kitty", "title": "fixture"}), (2, {"status": "not-afk"})]:
                conn.execute("INSERT INTO events VALUES (?, ?, ?, ?)", (
                    bucket, int(start.timestamp() * 1e9), int(end.timestamp() * 1e9), json.dumps(payload),
                ))

    today = logical_date(datetime.now().astimezone())
    start = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc).replace(hour=10)
    append(start - timedelta(days=1))
    materialize_activitywatch_events()
    materialize_activitywatch_event_index()
    materialize_activitywatch_derived(start=today - timedelta(days=1), end=today)
    recovery = canonical_activitywatch_events_path().read_bytes()

    arbtt = arbtt_events_path()
    arbtt.parent.mkdir(parents=True, exist_ok=True)
    arbtt.write_text(json.dumps({"timestamp": "2022-07-01T10:00:00+00:00"}) + "\n")
    arbtt_manifest_path().write_text(json.dumps({
        "schema_version": ARBTT_EVENTS_SCHEMA_VERSION, "row_count": 1,
        "first_date": "2022-07-01", "last_date": "2022-07-01",
    }))

    clock = [1000.0]
    monkeypatch.setattr(materialization, "monotonic", lambda: clock[0])
    monkeypatch.setattr(materialization, "_PRODUCT_REFRESHED_AT", {})
    monkeypatch.setattr(materialization, "_READ_CONVERGENCE_CACHE", {})
    monkeypatch.setattr(materialization, "_READ_CONVERGENCE_FLIGHTS", {})

    def query():
        result = lynchpin_personal(action="activity", view="focus", start=str(today), end=str(today))
        assert result["ok"], result
        return next(row for row in result["data"] if row["source"] == "activitywatch")

    append(start)
    assert query()["active_hours"] == 0.5
    assert json.loads(activitywatch_event_index_manifest_path().read_text())["last_date"] == str(today)
    append(start + timedelta(hours=1))
    clock[0] += materialization.READ_CONVERGENCE_FRESHNESS_SECONDS + 1
    assert query()["active_hours"] == 1.0
    assert canonical_activitywatch_events_path().read_bytes() == recovery
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE events")
    clock[0] += materialization.READ_CONVERGENCE_FRESHNESS_SECONDS + 1
    failed = lynchpin_personal(action="activity", view="focus", start=str(today), end=str(today))
    assert failed["ok"] is False
    assert "dependency activitywatch_event_index" in failed["message"]
    wal_keeper.close()
