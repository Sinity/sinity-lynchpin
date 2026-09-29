from __future__ import annotations

import sqlite3
import threading

from lynchpin.sources import chrome_profile


def test_snapshot_of_exclusively_locked_history_returns_committed_wal_rows(tmp_path) -> None:
    """Fails if the snapshot opens the live file through SQLite: a running
    Chrome holds History under an exclusive lock, and a backup retries a busy
    source forever."""
    history = tmp_path / "History"
    browser = sqlite3.connect(history, check_same_thread=False)
    browser.execute("PRAGMA journal_mode=WAL")
    browser.execute("PRAGMA locking_mode=EXCLUSIVE")
    browser.execute("CREATE TABLE urls(id INTEGER PRIMARY KEY, url TEXT)")
    browser.execute("INSERT INTO urls VALUES(1, 'https://locked.example/')")
    browser.commit()
    # Hold the write lock the way a running browser does.
    browser.execute("BEGIN EXCLUSIVE")
    assert (tmp_path / "History-wal").is_file()

    seen: list[list[str]] = []

    def snapshot() -> None:
        with chrome_profile.snapshot_history_db(history) as snap:
            conn = sqlite3.connect(snap)
            try:
                seen.append([row[0] for row in conn.execute("SELECT url FROM urls")])
            finally:
                conn.close()

    worker = threading.Thread(target=snapshot, daemon=True)
    worker.start()
    worker.join(timeout=10)
    try:
        assert not worker.is_alive(), "snapshot blocked on the browser's lock"
        assert seen == [["https://locked.example/"]]
    finally:
        browser.rollback()
        browser.close()
