"""DuckDB substrate review and symbol-change table tests."""

from __future__ import annotations

from datetime import date
from pathlib import Path


def _make_pr_row(
    number: int, state: str = "merged", friction_signals: tuple[str, ...] = ()
) -> dict:
    return {
        "project": "lynchpin",
        "number": number,
        "title": f"feat: PR #{number}",
        "state": state,
        "url": f"https://github.com/sinity/lynchpin/pull/{number}",
        "author": "Sinity",
        "created_at": "2026-05-01T10:00:00+00:00",
        "closed_at": "2026-05-02T10:00:00+00:00"
        if state in ("merged", "closed")
        else None,
        "merged_at": "2026-05-02T10:00:00+00:00" if state == "merged" else None,
        "review_count": 2,
        "review_decisions": ("APPROVED",),
        "review_round_count": 1,
        "reviewer_count": 1,
        "reviewers": ("reviewer-a",),
        "review_comment_count": 3,
        "top_level_comment_count": 1,
        "changes_requested_count": 0,
        "approval_count": 1,
        "dismissed_count": 0,
        "time_to_first_review_minutes": 60.0,
        "time_to_close_minutes": 1440.0,
        "time_to_merge_minutes": 1440.0 if state == "merged" else None,
        "final_decision": "APPROVED",
        "friction_signals": friction_signals,
    }


def test_promote_pr_review_rows_round_trip(tmp_path: Path) -> None:
    """Promote PrReviewRow dicts and load them back; assert structural equality."""
    from lynchpin.substrate import review as review_mod
    from lynchpin.substrate.connection import apply_schema, connect

    rows = [
        _make_pr_row(1, state="merged", friction_signals=("many_rounds",)),
        _make_pr_row(2, state="open"),
        _make_pr_row(3, state="closed"),
    ]

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        review_mod.promote_pr_review_rows(conn, rows=rows, refresh_id="r1")
        loaded = review_mod.load_pr_review_rows(conn, refresh_id="r1")

    assert len(loaded) == 3
    loaded_by_num = {r.number: r for r in loaded}
    pr1 = loaded_by_num[1]
    assert pr1.project == "lynchpin"
    assert pr1.state == "merged"
    assert tuple(pr1.friction_signals) == ("many_rounds",)
    pr2 = loaded_by_num[2]
    assert pr2.state == "open"


def test_load_pr_review_rows_only_with_friction(tmp_path: Path) -> None:
    """Load with only_with_friction=True returns only PRs with non-empty friction_signals."""
    from lynchpin.substrate import review as review_mod
    from lynchpin.substrate.connection import apply_schema, connect

    rows = [
        _make_pr_row(10, friction_signals=("many_rounds",)),
        _make_pr_row(11, friction_signals=("slow_merge",)),
        _make_pr_row(12, friction_signals=()),
        _make_pr_row(13, friction_signals=()),
        _make_pr_row(14, friction_signals=()),
    ]

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        review_mod.promote_pr_review_rows(conn, rows=rows, refresh_id="r1")
        loaded = review_mod.load_pr_review_rows(conn, only_with_friction=True)

    assert len(loaded) == 2
    assert all(len(r.friction_signals) > 0 for r in loaded)


def _make_symbol_change_row(sha: str, qualified_name: str) -> dict:
    return {
        "project": "lynchpin",
        "sha": sha,
        "date": date(2026, 5, 1),
        "path": "lynchpin/core/config.py",
        "change_type": "M",
        "qualified_name": qualified_name,
        "symbol_kind": "function",
        "exported": True,
        "breaking_candidate": False,
    }


def test_promote_symbol_changes_round_trip(tmp_path: Path) -> None:
    """Promote symbol_change dicts and load them back; assert structural equality."""
    from lynchpin.substrate import work_symbols
    from lynchpin.substrate.connection import apply_schema, connect

    rows = [
        _make_symbol_change_row("sha001", "lynchpin.core.config.get_config"),
        _make_symbol_change_row("sha001", "lynchpin.core.config.LynchpinConfig"),
        _make_symbol_change_row("sha002", "lynchpin.sources.git.commits_in_range"),
    ]

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        work_symbols.promote_symbol_changes(conn, rows=rows, refresh_id="r1")
        loaded = work_symbols.load_symbol_changes(conn, refresh_id="r1")

    assert len(loaded) == 3
    loaded_names = {
        r["qualified_name"] if isinstance(r, dict) else r.qualified_name for r in loaded
    }
    assert "lynchpin.core.config.get_config" in loaded_names
    assert "lynchpin.sources.git.commits_in_range" in loaded_names
