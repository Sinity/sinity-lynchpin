"""Profile snapshots retain WAL commits, generation identity, and date selection."""

from datetime import date
import os
import sqlite3

import pytest
from lynchpin.sources import polylogue

_COLUMNS = "title first_message_at last_message_at repo_names_json repo_paths_json auto_tags_json message_count word_count engaged_duration_ms wall_duration_ms total_cost_usd tool_use_count thinking_count substantive_count attachment_count work_event_count phase_count cost_is_estimated workflow_shape workflow_shape_confidence terminal_state terminal_state_confidence inference_payload_json".split()


@pytest.fixture
def profiles_db(tmp_path, monkeypatch):
    path = tmp_path / "profiles.db"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    columns = ", ".join(f"{name} TEXT" for name in _COLUMNS)
    conn.execute(
        f"CREATE TABLE session_profiles (conversation_id TEXT PRIMARY KEY, source_name TEXT, canonical_session_date TEXT, {columns})"
    )
    conn.executemany(
        "INSERT INTO session_profiles (conversation_id, source_name, canonical_session_date) VALUES (?, 'codex', ?)",
        [("early", "2026-01-01"), ("middle", "2026-01-02"), ("late", "2026-01-03")],
    )
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    monkeypatch.setattr(polylogue, "_default_polylogue_db_path", lambda: path)
    monkeypatch.setattr(polylogue, "_cached_profiles", None)
    monkeypatch.setattr(polylogue, "_cached_profiles_signature", None)
    yield path, conn
    conn.close()


def _ids(**bounds):
    return [row.conversation_id for row in polylogue.iter_session_profiles(**bounds)]


def test_profile_cache_sees_pre_acquisition_wal_commit(profiles_db):
    path, conn = profiles_db
    assert _ids() == ["early", "middle", "late"]
    before = path.stat()
    conn.execute(
        "INSERT INTO session_profiles (conversation_id, source_name, canonical_session_date) VALUES ('new', 'codex', '2026-01-04')"
    )
    conn.commit()
    after = path.stat()
    assert (after.st_mtime_ns, after.st_size) == (before.st_mtime_ns, before.st_size)
    assert len(polylogue._session_profiles_from_sqlite()) == 4
    assert _ids() == ["early", "middle", "late", "new"]


@pytest.mark.parametrize(
    "bounds,expected",
    [
        ({}, ["early", "middle", "late"]),
        ({"start": date(2026, 1, 2)}, ["middle", "late"]),
        ({"end": date(2026, 1, 2)}, ["early", "middle"]),
        ({"start": date(2026, 1, 2), "end": date(2026, 1, 2)}, ["middle"]),
        ({"start": date(2026, 1, 5), "end": date(2026, 1, 6)}, []),
    ],
)
def test_profile_bounds_are_independently_inclusive(profiles_db, bounds, expected):
    assert _ids(**bounds) == expected


def test_reversed_profile_bounds_are_rejected(profiles_db):
    with pytest.raises(ValueError, match="start.*end"):
        _ids(start=date(2026, 1, 3), end=date(2026, 1, 1))


def test_profile_cache_tracks_replaced_generation_with_same_metadata(
    profiles_db, tmp_path, monkeypatch
):
    path, conn = profiles_db
    conn.close()
    replacement = tmp_path / "replacement.db"
    replacement.write_bytes(path.read_bytes())
    with sqlite3.connect(replacement) as other:
        other.execute(
            "UPDATE session_profiles SET conversation_id = 'changed' WHERE conversation_id = 'early'"
        )
    stamp = path.stat()
    os.utime(replacement, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert replacement.stat().st_size == stamp.st_size
    link = tmp_path / "selected.db"
    link.symlink_to(path)
    monkeypatch.setattr(polylogue, "_default_polylogue_db_path", lambda: link)
    assert _ids()[0] == "early"
    link.unlink()
    link.symlink_to(replacement)
    assert _ids()[0] == "changed"


def test_unknown_source_identity_does_not_cache_empty_result(tmp_path, monkeypatch):
    monkeypatch.setattr(
        polylogue, "_default_polylogue_db_path", lambda: tmp_path / "absent.db"
    )
    monkeypatch.setattr(polylogue, "_cached_profiles", None)
    monkeypatch.setattr(polylogue, "_cached_profiles_signature", None)
    calls = []

    def read():
        calls.append(1)
        return []

    monkeypatch.setattr(polylogue, "_profiles_from_facade", read)
    assert _ids() == []
    assert _ids() == []
    assert len(calls) == 2


def test_changed_input_during_acquisition_is_not_cached(profiles_db, monkeypatch):
    path, conn = profiles_db
    original = polylogue._profiles_from_facade

    def read():
        rows = original()
        conn.execute(
            "INSERT INTO session_profiles (conversation_id, source_name, canonical_session_date) VALUES ('new', 'codex', '2026-01-04')"
        )
        conn.commit()
        return rows

    monkeypatch.setattr(polylogue, "_profiles_from_facade", read)
    assert _ids() == ["early", "middle", "late"]
    assert polylogue._cached_profiles is None
    monkeypatch.setattr(polylogue, "_profiles_from_facade", original)
    assert _ids()[-1] == "new"


@pytest.mark.parametrize(
    "start,end",
    [
        (None, None),
        (date(2026, 1, 2), None),
        (None, date(2026, 1, 2)),
        (date(2026, 1, 2), date(2026, 1, 3)),
    ],
)
def test_facade_receives_each_optional_bound(monkeypatch, start, end):
    from types import SimpleNamespace

    queries = []

    def read(query):
        queries.append(query)
        return []

    monkeypatch.setattr(
        polylogue, "_session_profiles_from_sqlite", lambda **kwargs: None
    )
    monkeypatch.setattr(polylogue, "_require_materialized_products", lambda: None)
    monkeypatch.setattr(
        polylogue,
        "_polylogue_client",
        lambda: SimpleNamespace(list_session_profile_insights=read),
    )
    assert polylogue._session_profiles_from_facade(start=start, end=end) == []
    assert len(queries) == 1
    assert queries[0].session_date_since == (start.isoformat() if start else None)
    assert queries[0].session_date_until == (end.isoformat() if end else None)
