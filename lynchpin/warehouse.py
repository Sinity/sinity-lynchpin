from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable, Iterator, List, Tuple

import duckdb

from . import finance, polylogue, reddit, sinevec, spotify
from .config import get_config


def build(output: Path | None = None, limit: int | None = None) -> Path:
    cfg = get_config()
    db_path = Path(output or cfg.warehouse_db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(db_path))
    try:
        _create_tables(conn)
        _load_reddit(conn, limit)
        _load_spotify(conn, limit)
        _load_finance(conn, limit)
        _load_polylogue(conn, limit)
        _load_sinevec(conn)
    finally:
        conn.close()
    return db_path


def _create_tables(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reddit_comments (
            id TEXT,
            created TIMESTAMP,
            subreddit TEXT,
            body TEXT,
            permalink TEXT,
            parent TEXT,
            gildings BIGINT,
            source TEXT
        )
        """
    )
    conn.execute("DELETE FROM reddit_comments")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reddit_posts (
            id TEXT,
            created TIMESTAMP,
            subreddit TEXT,
            title TEXT,
            body TEXT,
            url TEXT,
            gildings BIGINT,
            source TEXT
        )
        """
    )
    conn.execute("DELETE FROM reddit_posts")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS spotify_streams (
            end_time TIMESTAMP,
            artist TEXT,
            track TEXT,
            ms_played BIGINT,
            platform TEXT,
            context TEXT,
            source TEXT
        )
        """
    )
    conn.execute("DELETE FROM spotify_streams")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS finance_transactions (
            date DATE,
            payee TEXT,
            narration TEXT,
            posting_index INTEGER,
            account TEXT,
            amount DOUBLE,
            currency TEXT
        )
        """
    )
    conn.execute("DELETE FROM finance_transactions")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS polylogue_markdown (
            provider TEXT,
            path TEXT,
            modified_at TIMESTAMP,
            size_bytes BIGINT
        )
        """
    )
    conn.execute("DELETE FROM polylogue_markdown")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sinevec_state (
            version INTEGER,
            created_at TIMESTAMP,
            token_total BIGINT,
            source TEXT
        )
        """
    )
    conn.execute("DELETE FROM sinevec_state")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sinevec_token_usage (
            path TEXT,
            tokens BIGINT,
            state_version INTEGER,
            created_at TIMESTAMP
        )
        """
    )
    conn.execute("DELETE FROM sinevec_token_usage")


def _load_reddit(conn: duckdb.DuckDBPyConnection, limit: int | None) -> None:
    comment_rows: Iterator[Tuple] = (
        (
            comment.id,
            comment.created,
            comment.subreddit,
            comment.body,
            comment.permalink,
            comment.parent,
            comment.gildings,
            comment.source,
        )
        for comment in _maybe_limit(reddit.iter_comments(), limit)
    )
    _batched_insert(conn, "INSERT INTO reddit_comments VALUES (?, ?, ?, ?, ?, ?, ?, ?)", comment_rows)

    if limit == 0:
        return

    post_rows: Iterator[Tuple] = (
        (
            post.id,
            post.created,
            post.subreddit,
            post.title,
            post.body,
            post.url,
            post.gildings,
            post.source,
        )
        for post in _maybe_limit(reddit.iter_posts(), limit)
    )
    _batched_insert(conn, "INSERT INTO reddit_posts VALUES (?, ?, ?, ?, ?, ?, ?, ?)", post_rows)


def _load_spotify(conn: duckdb.DuckDBPyConnection, limit: int | None) -> None:
    rows: Iterator[Tuple] = (
        (
            stream.end_time,
            stream.artist,
            stream.track,
            stream.ms_played,
            stream.platform,
            stream.context,
            stream.source_file,
        )
        for stream in _maybe_limit(spotify.iter_streams(), limit)
    )
    _batched_insert(conn, "INSERT INTO spotify_streams VALUES (?, ?, ?, ?, ?, ?, ?)", rows)


def _load_finance(conn: duckdb.DuckDBPyConnection, limit: int | None) -> None:
    rows: Iterator[Tuple] = (
        (
            tx.date,
            tx.payee,
            tx.narration,
            idx,
            posting.account,
            posting.amount,
            posting.currency,
        )
        for tx in _maybe_limit(finance.iter_transactions(), limit)
        for idx, posting in enumerate(tx.postings)
    )
    _batched_insert(
        conn,
        "INSERT INTO finance_transactions VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )


def _load_polylogue(conn: duckdb.DuckDBPyConnection, limit: int | None) -> None:
    rows = (
        (
            doc.provider,
            str(doc.path),
            doc.modified_at,
            doc.size_bytes,
        )
        for doc in _maybe_limit(polylogue.iter_documents(), limit)
    )
    _batched_insert(conn, "INSERT INTO polylogue_markdown VALUES (?, ?, ?, ?)", rows)


def _load_sinevec(conn: duckdb.DuckDBPyConnection) -> None:
    state = sinevec.load_embedding_state()
    if not state:
        return
    conn.execute(
        "INSERT INTO sinevec_state VALUES (?, ?, ?, ?)",
        (
            state.version,
            state.created_at.isoformat() if state.created_at else None,
            state.token_total,
            str(state.source_file),
        ),
    )
    rows = (
        (
            entry.path,
            entry.tokens,
            state.version,
            state.created_at.isoformat() if state.created_at else None,
        )
        for entry in state.token_usage
    )
    _batched_insert(conn, "INSERT INTO sinevec_token_usage VALUES (?, ?, ?, ?)", rows)


def _batched_insert(
    conn: duckdb.DuckDBPyConnection,
    sql: str,
    rows: Iterator[Tuple],
    batch_size: int = 1000,
) -> None:
    batch: List[Tuple] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= batch_size:
            conn.executemany(sql, batch)
            batch.clear()
    if batch:
        conn.executemany(sql, batch)


def _maybe_limit(iterator: Iterable, limit: int | None) -> Iterator:
    if limit is None:
        yield from iterator
        return
    count = 0
    for item in iterator:
        if count >= limit:
            break
        count += 1
        yield item


def cli() -> None:
    parser = argparse.ArgumentParser(description="Build Lynchpin DuckDB warehouse from cached sources.")
    parser.add_argument("--output", type=Path, help="Output DuckDB path (defaults to artefacts/lynchpin/warehouse.duckdb)")
    parser.add_argument("--limit", type=int, default=None, help="Limit rows per dataset for quick sampling.")
    args = parser.parse_args()
    db_path = build(output=args.output, limit=args.limit)
    print(f"Wrote {db_path}")


if __name__ == "__main__":
    cli()
