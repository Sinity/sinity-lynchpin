"""Tests for additive SQL views over evidence_node + evidence_edge.

Covers Arc 2.4 (project_day_correlation) and Arc 2.5 (issue_closure_chain_walk).
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


UTC = timezone.utc


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _dt(y: int, m: int, d: int, h: int = 12) -> datetime:
    return datetime(y, m, d, h, 0, 0, tzinfo=UTC)


def _insert_node(
    conn,
    *,
    refresh_id: str,
    node_id: str,
    kind: str,
    source: str,
    date_val: date,
    project: str | None,
    payload: dict[str, Any] | None = None,
) -> None:
    payload_json = json.dumps(payload) if payload is not None else None
    conn.execute(
        """
        INSERT INTO evidence_node (
            refresh_id, id, kind, source, date, project,
            summary, payload, caveats
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            refresh_id,
            node_id,
            kind,
            source,
            date_val,
            project,
            f"{kind} summary",
            payload_json,
            "[]",
        ],
    )


def _insert_edge(
    conn,
    *,
    refresh_id: str,
    source_id: str,
    target_id: str,
    relation: str,
) -> None:
    conn.execute(
        """
        INSERT INTO evidence_edge (refresh_id, source_id, target_id, relation, evidence, weight)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [refresh_id, source_id, target_id, relation, "test edge", 1.0],
    )


def _insert_build(conn, *, refresh_id: str) -> None:
    conn.execute(
        """
        INSERT INTO evidence_graph_build (
            refresh_id, start_date, end_date, mode, projects,
            node_count, edge_count, caveats, generated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            refresh_id,
            date(2026, 5, 1),
            date(2026, 5, 7),
            "materialized",
            [],
            0,
            0,
            "[]",
            _dt(2026, 5, 7),
        ],
    )


# ---------------------------------------------------------------------------
# Arc 2.4 — project_day_correlation
# ---------------------------------------------------------------------------


class TestProjectDayCorrelation:
    def test_counts_by_kind(self, tmp_path: Path) -> None:
        """Promote a graph with mixed kinds; assert counts come back accurate."""
        from lynchpin.substrate.connection import apply_schema, connect
        from lynchpin.substrate.derived import load_project_day_correlations

        db = tmp_path / "sub.duckdb"
        with connect(db) as conn:
            apply_schema(conn)
            _insert_build(conn, refresh_id="r1")

            day = date(2026, 5, 3)
            _insert_node(
                conn,
                refresh_id="r1",
                node_id="c1",
                kind="commit",
                source="git",
                date_val=day,
                project="lynchpin",
            )
            _insert_node(
                conn,
                refresh_id="r1",
                node_id="c2",
                kind="commit",
                source="git",
                date_val=day,
                project="lynchpin",
            )
            _insert_node(
                conn,
                refresh_id="r1",
                node_id="ai1",
                kind="ai_work_event",
                source="polylogue",
                date_val=day,
                project="lynchpin",
            )
            _insert_node(
                conn,
                refresh_id="r1",
                node_id="gh1",
                kind="github_issue",
                source="github",
                date_val=day,
                project="lynchpin",
            )
            _insert_node(
                conn,
                refresh_id="r1",
                node_id="t1",
                kind="terminal_session",
                source="terminal",
                date_val=day,
                project="lynchpin",
            )

            rows = load_project_day_correlations(conn, refresh_id="r1")

        assert len(rows) == 1
        row = rows[0]
        assert row.project == "lynchpin"
        assert row.date == day
        assert row.commit_count == 2
        assert row.ai_work_event_count == 1
        assert row.github_item_count == 1
        assert row.terminal_count == 1
        assert row.ai_session_count == 0

    def test_includes_direct_commit_and_ai_work_event_facts(
        self, tmp_path: Path
    ) -> None:
        """project_day_correlation includes fact tables, not only graph nodes."""
        from lynchpin.sources.git import GitCommitFact
        from lynchpin.sources.polylogue import WorkEvent
        from lynchpin.substrate.connection import apply_schema, connect
        from lynchpin.substrate.work_ai import promote_ai_work_events
        from lynchpin.substrate.work_commits import promote_commits
        from lynchpin.substrate.derived import load_project_day_correlations

        db = tmp_path / "sub.duckdb"
        with connect(db) as conn:
            apply_schema(conn)
            promote_commits(
                conn,
                refresh_id="r1",
                facts=[
                    GitCommitFact(
                        repo="lynchpin",
                        commit="a" * 40,
                        authored_at=_dt(2026, 5, 3, 12),
                        author="Sinity",
                        subject="feat: test",
                        lines_added=1,
                        lines_deleted=0,
                        lines_changed=1,
                        files_changed=1,
                        paths=("lynchpin/mcp/tools/views.py",),
                        path_roots=("lynchpin",),
                    )
                ],
                project_lookup=lambda repo: "lynchpin",
            )
            promote_ai_work_events(
                conn,
                refresh_id="r1",
                events=[
                    WorkEvent(
                        event_id="we1",
                        conversation_id="conv1",
                        provider="claude-code",
                        kind="implementation",
                        confidence=0.8,
                        start=_dt(2026, 5, 3, 10),
                        end=_dt(2026, 5, 3, 11),
                        duration_ms=3_600_000,
                        file_paths=(
                            "/realm/project/sinity-lynchpin/lynchpin/mcp/tools/views.py",
                        ),
                        tools_used=("Edit",),
                        summary="test",
                    )
                ],
                project_resolver=lambda _event: "lynchpin",
            )

            rows = load_project_day_correlations(conn, refresh_id="r1")

        assert len(rows) == 1
        assert rows[0].commit_count == 1
        assert rows[0].ai_work_event_count == 1
        assert rows[0].source_count == 2

    def test_aggregates_commit_shas(self, tmp_path: Path) -> None:
        """commit nodes' payload.commit values aggregate into commit_shas."""
        from lynchpin.substrate.connection import apply_schema, connect
        from lynchpin.substrate.derived import load_project_day_correlations

        db = tmp_path / "sub.duckdb"
        with connect(db) as conn:
            apply_schema(conn)
            _insert_build(conn, refresh_id="r1")

            day = date(2026, 5, 4)
            _insert_node(
                conn,
                refresh_id="r1",
                node_id="c1",
                kind="commit",
                source="git",
                date_val=day,
                project="sinex",
                payload={"commit": "abc123"},
            )
            _insert_node(
                conn,
                refresh_id="r1",
                node_id="c2",
                kind="commit",
                source="git",
                date_val=day,
                project="sinex",
                payload={"commit": "def456"},
            )

            rows = load_project_day_correlations(
                conn, refresh_id="r1", projects=("sinex",)
            )

        assert len(rows) == 1
        row = rows[0]
        assert row.commit_count == 2
        assert set(row.commit_shas) == {"abc123", "def456"}

    def test_filters_null_project(self, tmp_path: Path) -> None:
        """Nodes with project=NULL are excluded from the view."""
        from lynchpin.substrate.connection import apply_schema, connect
        from lynchpin.substrate.derived import load_project_day_correlations

        db = tmp_path / "sub.duckdb"
        with connect(db) as conn:
            apply_schema(conn)
            _insert_build(conn, refresh_id="r1")

            day = date(2026, 5, 5)
            _insert_node(
                conn,
                refresh_id="r1",
                node_id="c1",
                kind="commit",
                source="git",
                date_val=day,
                project=None,
            )  # NULL project
            _insert_node(
                conn,
                refresh_id="r1",
                node_id="c2",
                kind="commit",
                source="git",
                date_val=day,
                project="sinex",
            )

            rows = load_project_day_correlations(conn, refresh_id="r1")

        assert len(rows) == 1
        assert rows[0].project == "sinex"

    def test_min_source_count_filter(self, tmp_path: Path) -> None:
        """min_source_count=2 returns only cross-source project-days."""
        from lynchpin.substrate.connection import apply_schema, connect
        from lynchpin.substrate.derived import load_project_day_correlations

        db = tmp_path / "sub.duckdb"
        with connect(db) as conn:
            apply_schema(conn)
            _insert_build(conn, refresh_id="r1")

            day = date(2026, 5, 6)
            # lynchpin: git only (source_count=1)
            _insert_node(
                conn,
                refresh_id="r1",
                node_id="c1",
                kind="commit",
                source="git",
                date_val=day,
                project="lynchpin",
            )
            # sinex: git + polylogue (source_count=2)
            _insert_node(
                conn,
                refresh_id="r1",
                node_id="c2",
                kind="commit",
                source="git",
                date_val=day,
                project="sinex",
            )
            _insert_node(
                conn,
                refresh_id="r1",
                node_id="ai1",
                kind="ai_work_event",
                source="polylogue",
                date_val=day,
                project="sinex",
            )

            rows = load_project_day_correlations(
                conn, refresh_id="r1", min_source_count=2
            )

        assert len(rows) == 1
        assert rows[0].project == "sinex"
        assert rows[0].source_count == 2

    def test_focus_minutes_aggregation(self, tmp_path: Path) -> None:
        """focus_day nodes with payload.duration_s sum into focus_minutes (÷60)."""
        from lynchpin.substrate.connection import apply_schema, connect
        from lynchpin.substrate.derived import load_project_day_correlations

        db = tmp_path / "sub.duckdb"
        with connect(db) as conn:
            apply_schema(conn)
            _insert_build(conn, refresh_id="r1")

            day = date(2026, 5, 7)
            _insert_node(
                conn,
                refresh_id="r1",
                node_id="f1",
                kind="focus_day",
                source="activitywatch",
                date_val=day,
                project="lynchpin",
                payload={"duration_s": 3600.0},
            )  # 60 minutes
            _insert_node(
                conn,
                refresh_id="r1",
                node_id="f2",
                kind="focus_day",
                source="activitywatch",
                date_val=day,
                project="lynchpin",
                payload={"duration_s": 1800.0},
            )  # 30 minutes

            rows = load_project_day_correlations(conn, refresh_id="r1")

        assert len(rows) == 1
        row = rows[0]
        assert row.focus_count == 2
        assert abs(row.focus_minutes - 90.0) < 0.01

    def test_date_range_filter(self, tmp_path: Path) -> None:
        """Date range filter excludes out-of-window project-days."""
        from lynchpin.substrate.connection import apply_schema, connect
        from lynchpin.substrate.derived import load_project_day_correlations

        db = tmp_path / "sub.duckdb"
        with connect(db) as conn:
            apply_schema(conn)
            _insert_build(conn, refresh_id="r1")

            for d_val, proj in [
                (date(2026, 5, 1), "p1"),
                (date(2026, 5, 3), "p2"),
                (date(2026, 5, 7), "p3"),
            ]:
                _insert_node(
                    conn,
                    refresh_id="r1",
                    node_id=f"c_{proj}",
                    kind="commit",
                    source="git",
                    date_val=d_val,
                    project=proj,
                )

            rows = load_project_day_correlations(
                conn,
                refresh_id="r1",
                start=date(2026, 5, 2),
                end=date(2026, 5, 5),
            )

        assert len(rows) == 1
        assert rows[0].project == "p2"


# ---------------------------------------------------------------------------
# Arc 2.5 — issue_closure_chain_walk
# ---------------------------------------------------------------------------


class TestIssueClosureChainWalk:
    def test_simple_two_hop(self, tmp_path: Path) -> None:
        """issue → PR (references) → commit (references): all 3 reach from root."""
        from lynchpin.substrate.connection import apply_schema, connect
        from lynchpin.substrate.derived import load_issue_closure_chain_walks

        db = tmp_path / "sub.duckdb"
        with connect(db) as conn:
            apply_schema(conn)
            _insert_build(conn, refresh_id="r1")

            day = date(2026, 5, 1)
            _insert_node(
                conn,
                refresh_id="r1",
                node_id="issue:1",
                kind="github_issue",
                source="github",
                date_val=day,
                project="lynchpin",
                payload={"number": "42"},
            )
            _insert_node(
                conn,
                refresh_id="r1",
                node_id="pr:99",
                kind="github_pr",
                source="github",
                date_val=day,
                project="lynchpin",
            )
            _insert_node(
                conn,
                refresh_id="r1",
                node_id="commit:abc",
                kind="commit",
                source="git",
                date_val=day,
                project="lynchpin",
            )

            _insert_edge(
                conn,
                refresh_id="r1",
                source_id="issue:1",
                target_id="pr:99",
                relation="references",
            )
            _insert_edge(
                conn,
                refresh_id="r1",
                source_id="pr:99",
                target_id="commit:abc",
                relation="references",
            )

            rows = load_issue_closure_chain_walks(conn, refresh_id="r1")

        assert len(rows) == 1
        row = rows[0]
        assert row.root_id == "issue:1"
        assert row.issue_number == "42"
        assert row.chain_depth == 2
        assert row.reachable_count == 3
        assert set(row.reachable_node_ids) == {"issue:1", "pr:99", "commit:abc"}

    def test_orphaned_issue(self, tmp_path: Path) -> None:
        """github_issue with no outgoing references: reachable_count=1 (itself)."""
        from lynchpin.substrate.connection import apply_schema, connect
        from lynchpin.substrate.derived import load_issue_closure_chain_walks

        db = tmp_path / "sub.duckdb"
        with connect(db) as conn:
            apply_schema(conn)
            _insert_build(conn, refresh_id="r1")

            _insert_node(
                conn,
                refresh_id="r1",
                node_id="issue:7",
                kind="github_issue",
                source="github",
                date_val=date(2026, 5, 2),
                project="sinex",
                payload={"number": "7"},
            )

            rows = load_issue_closure_chain_walks(conn, refresh_id="r1")

        assert len(rows) == 1
        row = rows[0]
        assert row.root_id == "issue:7"
        assert row.chain_depth == 0
        assert row.reachable_count == 1
        assert row.reachable_node_ids == ("issue:7",)

    def test_depth_cap_prevents_infinite_loop(self, tmp_path: Path) -> None:
        """Cycle: issue → A → B → issue. CTE terminates; depth cap at 5."""
        from lynchpin.substrate.connection import apply_schema, connect
        from lynchpin.substrate.derived import load_issue_closure_chain_walks

        db = tmp_path / "sub.duckdb"
        with connect(db) as conn:
            apply_schema(conn)
            _insert_build(conn, refresh_id="r1")

            day = date(2026, 5, 3)
            _insert_node(
                conn,
                refresh_id="r1",
                node_id="issue:99",
                kind="github_issue",
                source="github",
                date_val=day,
                project="lynchpin",
                payload={"number": "99"},
            )
            _insert_node(
                conn,
                refresh_id="r1",
                node_id="nodeA",
                kind="github_pr",
                source="github",
                date_val=day,
                project="lynchpin",
            )
            _insert_node(
                conn,
                refresh_id="r1",
                node_id="nodeB",
                kind="commit",
                source="git",
                date_val=day,
                project="lynchpin",
            )

            # Create a cycle: issue:99 → nodeA → nodeB → issue:99
            _insert_edge(
                conn,
                refresh_id="r1",
                source_id="issue:99",
                target_id="nodeA",
                relation="references",
            )
            _insert_edge(
                conn,
                refresh_id="r1",
                source_id="nodeA",
                target_id="nodeB",
                relation="references",
            )
            _insert_edge(
                conn,
                refresh_id="r1",
                source_id="nodeB",
                target_id="issue:99",
                relation="references",
            )

            # Must not hang; depth cap keeps it bounded at max depth 5
            rows = load_issue_closure_chain_walks(conn, refresh_id="r1")

        assert len(rows) == 1
        row = rows[0]
        assert row.chain_depth <= 5
        # All 3 nodes are reachable (ARRAY_AGG DISTINCT deduplicates cycle revisits)
        assert row.reachable_count >= 3

    def test_filter_by_project(self, tmp_path: Path) -> None:
        """Only chains for project='lynchpin' returned when filter is set."""
        from lynchpin.substrate.connection import apply_schema, connect
        from lynchpin.substrate.derived import load_issue_closure_chain_walks

        db = tmp_path / "sub.duckdb"
        with connect(db) as conn:
            apply_schema(conn)
            _insert_build(conn, refresh_id="r1")

            day = date(2026, 5, 4)
            _insert_node(
                conn,
                refresh_id="r1",
                node_id="issue:lp1",
                kind="github_issue",
                source="github",
                date_val=day,
                project="lynchpin",
                payload={"number": "1"},
            )
            _insert_node(
                conn,
                refresh_id="r1",
                node_id="issue:sx1",
                kind="github_issue",
                source="github",
                date_val=day,
                project="sinex",
                payload={"number": "1"},
            )

            rows = load_issue_closure_chain_walks(
                conn, refresh_id="r1", project="lynchpin"
            )

        assert len(rows) == 1
        assert rows[0].root_id == "issue:lp1"
        assert rows[0].project == "lynchpin"

    def test_min_chain_depth_filter(self, tmp_path: Path) -> None:
        """min_chain_depth=1 excludes orphaned issues."""
        from lynchpin.substrate.connection import apply_schema, connect
        from lynchpin.substrate.derived import load_issue_closure_chain_walks

        db = tmp_path / "sub.duckdb"
        with connect(db) as conn:
            apply_schema(conn)
            _insert_build(conn, refresh_id="r1")

            day = date(2026, 5, 5)
            # Orphaned (depth=0)
            _insert_node(
                conn,
                refresh_id="r1",
                node_id="issue:orphan",
                kind="github_issue",
                source="github",
                date_val=day,
                project="lynchpin",
                payload={"number": "0"},
            )
            # Has a reference (depth=1)
            _insert_node(
                conn,
                refresh_id="r1",
                node_id="issue:linked",
                kind="github_issue",
                source="github",
                date_val=day,
                project="lynchpin",
                payload={"number": "10"},
            )
            _insert_node(
                conn,
                refresh_id="r1",
                node_id="pr:linked",
                kind="github_pr",
                source="github",
                date_val=day,
                project="lynchpin",
            )
            _insert_edge(
                conn,
                refresh_id="r1",
                source_id="issue:linked",
                target_id="pr:linked",
                relation="references",
            )

            rows = load_issue_closure_chain_walks(
                conn, refresh_id="r1", min_chain_depth=1
            )

        assert len(rows) == 1
        assert rows[0].root_id == "issue:linked"
        assert rows[0].chain_depth == 1
