from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional


def _jsonify(obj: object) -> object:
    try:
        json.dumps(obj)
        return obj
    except TypeError:
        return str(obj)


class MessengerExportDb:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS threads ("
            "uid TEXT PRIMARY KEY,"
            "name TEXT,"
            "message_count INTEGER,"
            "last_message_timestamp INTEGER,"
            "data JSON"
            ")"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS messages ("
            "uid TEXT PRIMARY KEY,"
            "thread_id TEXT,"
            "author TEXT,"
            "text TEXT,"
            "timestamp INTEGER,"
            "data JSON"
            ")"
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id)")
        self._ensure_compat_columns()
        self._pending = 0

    def _ensure_compat_columns(self) -> None:
        self._ensure_column("threads", "name", "TEXT")
        self._ensure_column("messages", "author", "TEXT")
        self._ensure_column("messages", "text", "TEXT")
        self._backfill_threads()
        self._backfill_messages()
        self._conn.commit()

    def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        existing = {row[1] for row in self._conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def _backfill_threads(self) -> None:
        rows = self._conn.execute("SELECT uid, data FROM threads WHERE name IS NULL").fetchall()
        for uid, payload in rows:
            try:
                data = json.loads(payload) if payload else {}
            except json.JSONDecodeError:
                data = {}
            self._conn.execute(
                "UPDATE threads SET name=? WHERE uid=?",
                (data.get("name"), uid),
            )

    def _backfill_messages(self) -> None:
        rows = self._conn.execute(
            "SELECT uid, data FROM messages WHERE author IS NULL OR text IS NULL"
        ).fetchall()
        for uid, payload in rows:
            try:
                data = json.loads(payload) if payload else {}
            except json.JSONDecodeError:
                data = {}
            self._conn.execute(
                "UPDATE messages SET author=COALESCE(author, ?), text=COALESCE(text, ?) WHERE uid=?",
                (data.get("author"), data.get("text"), uid),
            )

    def _maybe_commit(self) -> None:
        self._pending += 1
        if self._pending >= 1000:
            self._conn.commit()
            self._pending = 0

    def insert_thread(self, thread: Any) -> None:
        payload = vars(thread).copy()
        for key in ("type", "nicknames", "admins", "approval_requests", "participants", "plan"):
            payload.pop(key, None)
        if "color" in payload and payload["color"] is not None:
            payload["color"] = getattr(payload["color"], "value", payload["color"])
        if "last_message_timestamp" in payload:
            payload["last_message_timestamp"] = int(payload["last_message_timestamp"])
        payload = {key: _jsonify(value) for key, value in payload.items()}
        self._conn.execute(
            "INSERT OR REPLACE INTO threads (uid, name, message_count, last_message_timestamp, data) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                str(thread.uid),
                payload.get("name"),
                int(thread.message_count),
                int(thread.last_message_timestamp),
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        self._maybe_commit()

    def insert_message(self, thread: Any, message: Any) -> None:
        payload = vars(message).copy()
        for key in (
            "mentions",
            "read_by",
            "attachments",
            "quick_replies",
            "reactions",
            "sticker",
            "emoji_size",
            "replied_to",
        ):
            payload.pop(key, None)
        if "timestamp" in payload:
            payload["timestamp"] = int(payload["timestamp"])
        payload["thread_id"] = thread.uid
        payload = {key: _jsonify(value) for key, value in payload.items()}
        self._conn.execute(
            "INSERT OR REPLACE INTO messages (uid, thread_id, author, text, timestamp, data) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(message.uid),
                str(thread.uid),
                payload.get("author"),
                payload.get("text"),
                int(message.timestamp),
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        self._maybe_commit()

    def get_oldest_and_newest(self, thread: Any) -> Optional[tuple[int, int]]:
        row = self._conn.execute(
            "SELECT MIN(timestamp), MAX(timestamp) FROM messages WHERE thread_id=?",
            (str(thread.uid),),
        ).fetchone()
        if not row or row[0] is None:
            return None
        return int(row[0]), int(row[1])

    def check_fetched_all(self, thread: Any):
        row = self._conn.execute(
            "SELECT COUNT(*) FROM messages WHERE thread_id=?",
            (str(thread.uid),),
        ).fetchone()
        if row and row[0] != thread.message_count:
            yield RuntimeError(
                f"Expected {thread.message_count} messages in thread {thread.name}, got {row[0]}"
            )

    @property
    def db(self) -> sqlite3.Connection:
        return self._conn

    def __del__(self) -> None:
        try:
            self._conn.commit()
            self._conn.close()
        except Exception:
            pass


def ensure_export_db_compatibility(db_path: Path) -> None:
    if not db_path.exists():
        return
    db = MessengerExportDb(db_path)
    db.db.commit()
    db.db.close()
