"""Personal-product partitions: bounded replacement, ancestry, atomic publication."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import pytest

from lynchpin.substrate.connection import apply_schema, connect
from lynchpin.substrate.personal import (
    _resolved_rows,
    load_personal_daily_signals,
    promote_personal_daily_signals,
    promote_title_classifications_from_path,
)


def _signal(day: int, value: float, *, month: int = 1) -> tuple[str, date, str, float, dict[str, Any]]:
    return ("web", date(2026, month, day), "visits", value, {})


def _title(title_hash: str) -> dict[str, object]:
    return {
        "title_hash": title_hash, "app": "app", "raw_title": title_hash,
        "normalized_title": title_hash, "activity": "reading", "subject": "subject",
        "content_type": "content", "attention_level": "focused", "topic_category": "topic",
        "platform": "platform", "mode": "mode", "app_kind": "kind", "tool": "tool",
        "domain": "domain", "domain_category": "category", "is_ai_tool": False,
        "is_ai_active": False, "productivity_score": 0.5, "focus_score": 0.5,
        "confidence": 0.9, "classification_source": "test", "model_version": "v1",
    }


def _signals(conn: Any, refresh_id: str) -> list[tuple[date, float]]:
    return [(row[1], row[3]) for row in load_personal_daily_signals(conn, refresh_id=refresh_id)]


def test_native_duckdb_rejects_delete_and_reinsert_of_one_key_in_a_transaction() -> None:
    """The engine limit that makes partition re-promotion two committed units."""
    conn = duckdb.connect()
    conn.execute("CREATE TABLE t (k VARCHAR, refresh_id VARCHAR, v INT, PRIMARY KEY (k, refresh_id))")
    conn.execute("INSERT INTO t VALUES ('a', 'r', 1)")
    conn.execute("BEGIN TRANSACTION")
    conn.execute("DELETE FROM t WHERE refresh_id = 'r'")
    with pytest.raises(duckdb.ConstraintException):
        conn.execute("INSERT INTO t VALUES ('a', 'r', 2)")
    conn.execute("ROLLBACK")
    assert conn.execute("SELECT v FROM t").fetchall() == [(1,)]


def test_finite_correction_keeps_later_predecessor_rows(tmp_path: Path) -> None:
    with connect(tmp_path / "sub.duckdb") as conn:
        apply_schema(conn)
        promote_personal_daily_signals(
            conn, refresh_id="base",
            rows=[_signal(10, 1.0), _signal(15, 2.0), _signal(16, 3.0), _signal(25, 4.0)],
        )
        promote_personal_daily_signals(
            conn, refresh_id="fix", previous_refresh_id="base",
            replacement_start=date(2026, 1, 15), replacement_end=date(2026, 1, 17),
            rows=[_signal(15, 20.0)],
        )
        resolved = _signals(conn, "fix")

    # Jan16 was inside the read and is gone; Jan25 was never read and stays.
    assert resolved == [
        (date(2026, 1, 10), 1.0),
        (date(2026, 1, 15), 20.0),
        (date(2026, 1, 25), 4.0),
    ]


def test_every_newer_replacement_range_hides_ancestor_rows(tmp_path: Path) -> None:
    with connect(tmp_path / "sub.duckdb") as conn:
        apply_schema(conn)
        promote_personal_daily_signals(
            conn, refresh_id="grandparent",
            rows=[_signal(5, 1.0), _signal(15, 2.0), _signal(22, 3.0)],
        )
        promote_personal_daily_signals(
            conn, refresh_id="parent", previous_refresh_id="grandparent",
            replacement_start=date(2026, 1, 20), replacement_end=date(2026, 2, 1),
            rows=[_signal(21, 30.0)],
        )
        promote_personal_daily_signals(
            conn, refresh_id="child", previous_refresh_id="parent",
            replacement_start=date(2026, 1, 10), replacement_end=date(2026, 2, 1),
            rows=[_signal(12, 40.0)],
        )
        resolved = _signals(conn, "child")

    # The grandparent's Jan15 row lies before the parent's range but inside the
    # child's; it must not reappear.
    assert resolved == [(date(2026, 1, 5), 1.0), (date(2026, 1, 12), 40.0)]


def test_legacy_open_tail_lineage_still_replaces_the_whole_tail(tmp_path: Path) -> None:
    with connect(tmp_path / "sub.duckdb") as conn:
        apply_schema(conn)
        promote_personal_daily_signals(conn, refresh_id="base", rows=[_signal(5, 1.0), _signal(25, 2.0)])
        conn.execute(
            "INSERT INTO substrate_product_lineage "
            "(product, refresh_id, predecessor_refresh_id, replacement_start, mode) "
            "VALUES ('personal_daily_signals', 'legacy', 'base', DATE '2026-01-10', 'incremental')"
        )
        resolved = _signals(conn, "legacy")

    assert resolved == [(date(2026, 1, 5), 1.0)]


def test_self_predecessor_and_half_ranges_are_rejected(tmp_path: Path) -> None:
    with connect(tmp_path / "sub.duckdb") as conn:
        apply_schema(conn)
        promote_personal_daily_signals(conn, refresh_id="base", rows=[_signal(5, 1.0)])
        with pytest.raises(ValueError, match="own predecessor"):
            promote_personal_daily_signals(
                conn, refresh_id="base", previous_refresh_id="base",
                replacement_start=date(2026, 1, 1), replacement_end=date(2026, 1, 9),
                rows=[],
            )
        with pytest.raises(ValueError, match="start and an end"):
            promote_personal_daily_signals(
                conn, refresh_id="next", previous_refresh_id="base",
                replacement_start=date(2026, 1, 1), rows=[],
            )
        assert _signals(conn, "base") == [(date(2026, 1, 5), 1.0)]


def test_failed_input_leaves_the_existing_partition_and_metadata(tmp_path: Path) -> None:
    def failing_rows():
        yield _signal(6, 9.0)
        raise OSError("input truncated")

    with connect(tmp_path / "sub.duckdb") as conn:
        apply_schema(conn)
        promote_personal_daily_signals(conn, refresh_id="r", rows=[_signal(5, 1.0)])
        lineage_before = conn.execute("SELECT * FROM substrate_product_lineage").fetchall()
        with pytest.raises(OSError, match="truncated"):
            promote_personal_daily_signals(conn, refresh_id="r", rows=failing_rows())
        assert conn.execute("SELECT * FROM substrate_product_lineage").fetchall() == lineage_before
        assert _signals(conn, "r") == [(date(2026, 1, 5), 1.0)]
        # Re-promoting the same refresh with good input replaces it wholesale.
        promote_personal_daily_signals(conn, refresh_id="r", rows=[_signal(7, 2.0)])
        assert _signals(conn, "r") == [(date(2026, 1, 7), 2.0)]


class _FailingLineage:
    """Connection proxy whose lineage write fails after rows and tombstones."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def execute(self, sql: str, *args: Any) -> Any:
        if sql.startswith("INSERT INTO substrate_product_lineage"):
            raise duckdb.IOException("disk full")
        return self._conn.execute(sql, *args)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


def test_failed_publication_commits_no_rows_tombstones_or_lineage(tmp_path: Path) -> None:
    old_path = tmp_path / "old.ndjson"
    new_path = tmp_path / "new.ndjson"
    old_path.write_text("\n".join(json.dumps(_title(k)) for k in ("a", "b")) + "\n")
    new_path.write_text(json.dumps(_title("c")) + "\n")
    with connect(tmp_path / "sub.duckdb") as conn:
        apply_schema(conn)
        promote_title_classifications_from_path(conn, refresh_id="old", path=str(old_path), input_fingerprint="f1")
        with pytest.raises(duckdb.IOException):
            promote_title_classifications_from_path(
                _FailingLineage(conn), refresh_id="new", path=str(new_path),
                previous_refresh_id="old", input_fingerprint="f2",
            )
        leftovers = [
            conn.execute(f"SELECT COUNT(*) FROM {table} WHERE refresh_id = 'new'").fetchone()[0]
            for table in ("title_classification", "substrate_product_tombstone", "substrate_product_lineage")
        ]
        resolved = _resolved_rows(
            conn, product="title_metadata", refresh_id="old", table="title_classification",
            columns=("title_hash",), key=lambda row: row[0],
        )

    assert leftovers == [0, 0, 0]
    assert resolved == [("a",), ("b",)]


def _run_title_promotion(monkeypatch: pytest.MonkeyPatch, conn: Any, tmp_path: Path, refresh_id: str) -> Any:
    from lynchpin.analysis.active.substrate_promote_personal import promote_personal_sources
    from lynchpin.analysis.active.substrate_promote_status import (
        SOURCE_TITLE_CLASSIFICATION,
        SourceSelection,
    )

    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda *_args, **_kwargs: SimpleNamespace(status="ready", reason="ready"),
    )
    monkeypatch.setattr("lynchpin.sources.title_metadata.title_metadata_path", lambda: tmp_path / "titles.ndjson")
    monkeypatch.setattr(
        "lynchpin.sources.title_metadata.title_metadata_manifest_path", lambda: tmp_path / "manifest.json"
    )
    counts: dict[str, int] = {}
    promote_personal_sources(
        conn, refresh_id=refresh_id, window_start=date(2026, 1, 1), window_end=date(2026, 1, 8),
        counts=counts, selection=SourceSelection.from_collection({SOURCE_TITLE_CLASSIFICATION}),
    )
    status = conn.execute(
        "SELECT status, row_count FROM substrate_source_status "
        "WHERE refresh_id = ? AND source = 'title_classification'",
        [refresh_id],
    ).fetchone()
    return counts.get("title_classification"), status


def test_unchanged_title_promotion_reports_inherited_titles(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "titles.ndjson").write_text("\n".join(json.dumps(_title(key)) for key in ("a", "b")) + "\n")
    (tmp_path / "manifest.json").write_text(json.dumps({"revision": 1}))
    with connect(tmp_path / "sub.duckdb") as conn:
        apply_schema(conn)
        first = _run_title_promotion(monkeypatch, conn, tmp_path, "first")
        second = _run_title_promotion(monkeypatch, conn, tmp_path, "second")
        physical = conn.execute(
            "SELECT COUNT(*) FROM title_classification WHERE refresh_id = 'second'"
        ).fetchone()[0]

    assert first == (2, ("ok", 2))
    assert physical == 0
    assert second == (0, ("ok", 2))


def test_failed_title_promotion_records_an_error_for_candidate_rejection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from lynchpin.analysis.active.substrate_promote import _run_status, _source_statuses

    (tmp_path / "titles.ndjson").write_text("not json\n")
    with connect(tmp_path / "sub.duckdb") as conn:
        apply_schema(conn)
        _, status = _run_title_promotion(monkeypatch, conn, tmp_path, "broken")
        run_status = _run_status(_source_statuses(conn, "broken"))
        lineage = conn.execute("SELECT COUNT(*) FROM substrate_product_lineage").fetchone()[0]

    assert status == ("error", 0)
    assert run_status == "error"
    assert lineage == 0
