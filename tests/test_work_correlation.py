from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from lynchpin.composite.work_correlation import (
    correlate_work_days,
    render_work_correlation_summary,
    render_work_day_correlations,
    strongest_work_correlations,
    summarize_work_correlations,
    work_day_correlations,
)
from lynchpin.sources.github import GitHubActor, GitHubItem


UTC = timezone.utc


def test_correlate_work_days_joins_project_day_evidence() -> None:
    when = datetime(2026, 5, 5, 12, tzinfo=UTC)
    issue = GitHubItem(
        repo="sinity-lynchpin",
        slug="Sinity/sinity-lynchpin",
        kind="issue",
        number=17,
        title="Fix correlation",
        state="closed",
        url="https://github.com/Sinity/sinity-lynchpin/issues/17",
        author=GitHubActor("Sinity"),
        labels=(),
        body="implemented by commit",
        comments=(),
        created_at=when,
        updated_at=when,
        closed_at=when,
    )

    rows = correlate_work_days(
        git_facts=[
            SimpleNamespace(
                repo="sinity-lynchpin",
                commit="abc1234",
                authored_at=when,
                subject="fix: correlate work evidence closes #17",
            )
        ],
        github_items=[issue],
        ai_sessions=[
            SimpleNamespace(
                conversation_id="conv-1",
                canonical_session_date=when.date(),
                work_event_projects=("lynchpin",),
            )
        ],
        raw_log_entries=[
            SimpleNamespace(
                timestamp=when,
                text="lynchpin current-state correlation work",
                source_path="/realm/project/knowledgebase/logs.raw-log.md",
                line_no=42,
            )
        ],
        focus_spans=[
            SimpleNamespace(
                project="sinity-lynchpin",
                start=when,
                duration_s=3600,
            )
        ],
        shell_sessions=[
            SimpleNamespace(
                project="sinity-lynchpin",
                start=when,
                duration_s=600,
                command_count=4,
            )
        ],
    )

    assert len(rows) == 1
    row = rows[0]
    assert row.project == "sinity-lynchpin"
    assert row.commit_count == 1
    assert row.github_refs == ("issue#17",)
    assert row.github_lifecycles["executed"] == 1
    assert row.ai_session_count == 1
    assert row.raw_log_count == 1
    assert row.raw_log_refs == ("/realm/project/knowledgebase/logs.raw-log.md:42",)
    assert row.focus_minutes == 60
    assert row.shell_command_count == 4
    assert row.sources == ("activitywatch", "git", "github", "polylogue", "raw_log", "terminal")
    assert row.has_cross_source_support is True


def test_render_work_day_correlations_keeps_source_columns() -> None:
    row = correlate_work_days(
        ai_sessions=[
            SimpleNamespace(
                conversation_id="conv-1",
                canonical_session_date=datetime(2026, 5, 5, tzinfo=UTC).date(),
                work_event_projects=("polylogue",),
            )
        ]
    )[0]

    rendered = render_work_day_correlations([row])

    assert "AI Sessions" in rendered
    assert "Raw Log" in rendered
    assert "polylogue" in rendered


def test_render_work_day_correlations_compacts_large_github_ref_lists() -> None:
    when = datetime(2026, 5, 5, 12, tzinfo=UTC)
    rows = correlate_work_days(
        git_facts=[
            SimpleNamespace(
                repo="polylogue",
                commit=str(number),
                authored_at=when,
                subject=f"fix: compact refs (#{number})",
            )
            for number in range(1, 11)
        ],
    )

    rendered = render_work_day_correlations(rows)

    assert "pr#1, pr#2, pr#3, pr#4, pr#5, pr#6, pr#7, pr#8 (+2 more)" in rendered
    assert "pr#9" not in rendered


def test_correlate_work_days_extracts_raw_log_project_mentions() -> None:
    when = datetime(2026, 5, 5, 12, tzinfo=UTC)
    rows = correlate_work_days(
        raw_log_entries=[
            SimpleNamespace(
                timestamp=when,
                text="polylogue and raw-log analysis for current state",
                source_path="logs.raw-log.md",
                line_no=7,
            )
        ]
    )

    assert [(row.project, row.raw_log_count) for row in rows] == [
        ("knowledgebase", 1),
        ("polylogue", 1),
    ]


def test_correlate_work_days_uses_bounded_raw_log_project_mentions() -> None:
    when = datetime(2026, 5, 5, 12, tzinfo=UTC)
    rows = correlate_work_days(
        raw_log_entries=[
            SimpleNamespace(
                timestamp=when,
                text="sinexical phrasing should not count, but sinex-target-vision should",
                source_path="logs.raw-log.md",
                line_no=7,
            )
        ]
    )

    assert [(row.project, row.raw_log_count) for row in rows] == [
        ("sinex-target-vision", 1),
    ]


def test_correlate_work_days_uses_logical_day_for_timestamped_sources() -> None:
    overnight = datetime(2026, 5, 6, 3, tzinfo=UTC)

    rows = correlate_work_days(
        git_facts=[
            SimpleNamespace(
                repo="sinity-lynchpin",
                commit="abc1234",
                authored_at=overnight,
                subject="fix: overnight work",
            )
        ],
        focus_spans=[
            SimpleNamespace(project="sinity-lynchpin", start=overnight, duration_s=60)
        ],
        shell_sessions=[
            SimpleNamespace(
                project="sinity-lynchpin",
                start=overnight,
                duration_s=60,
                command_count=1,
            )
        ],
    )

    assert len(rows) == 1
    assert rows[0].date.isoformat() == "2026-05-05"
    assert rows[0].sources == ("activitywatch", "git", "terminal")


def test_correlate_work_days_accepts_preaggregated_focus_days() -> None:
    rows = correlate_work_days(
        focus_spans=[
            SimpleNamespace(
                project="sinity-lynchpin",
                date=datetime(2026, 5, 5, tzinfo=UTC).date(),
                duration_s=1800,
            )
        ]
    )

    assert len(rows) == 1
    assert rows[0].date.isoformat() == "2026-05-05"
    assert rows[0].focus_minutes == 30


def test_summarize_work_correlations_reports_join_strength_and_gaps() -> None:
    when = datetime(2026, 5, 5, 12, tzinfo=UTC)
    rows = correlate_work_days(
        git_facts=[
            SimpleNamespace(
                repo="sinity-lynchpin",
                commit="abc1234",
                authored_at=when,
                subject="feat: correlated",
            ),
            SimpleNamespace(
                repo="polylogue",
                commit="def5678",
                authored_at=when,
                subject="fix: git only",
            ),
        ],
        ai_sessions=[
            SimpleNamespace(
                conversation_id="conv-1",
                canonical_session_date=when.date(),
                work_event_projects=("sinity-lynchpin",),
            )
        ],
        focus_spans=[
            SimpleNamespace(project="sinity-lynchpin", start=when, duration_s=60),
            SimpleNamespace(project="sinnix", start=when, duration_s=60),
        ],
    )

    summary = summarize_work_correlations(rows)

    assert summary.row_count == 3
    assert summary.cross_source_row_count == 1
    assert summary.projects == ("polylogue", "sinity-lynchpin", "sinnix")
    assert summary.source_pair_counts["activitywatch+git"] == 1
    assert summary.source_pair_counts["activitywatch+polylogue"] == 1
    assert summary.git_without_ai_or_focus == 1
    assert summary.focus_without_git == 1

    rendered = render_work_correlation_summary(summary)
    assert "Source counts: activitywatch=2, git=2, polylogue=1" in rendered
    assert "Source pair counts: activitywatch+git=1, activitywatch+polylogue=1, git+polylogue=1" in rendered


def test_strongest_work_correlations_orders_richest_rows() -> None:
    when = datetime(2026, 5, 5, 12, tzinfo=UTC)
    rows = correlate_work_days(
        git_facts=[
            SimpleNamespace(repo="polylogue", commit="abc", authored_at=when, subject="fix: one"),
            SimpleNamespace(repo="sinity-lynchpin", commit="def", authored_at=when, subject="fix: two"),
        ],
        ai_sessions=[
            SimpleNamespace(
                conversation_id="conv-1",
                canonical_session_date=when.date(),
                work_event_projects=("sinity-lynchpin",),
            )
        ],
        focus_spans=[SimpleNamespace(project="sinity-lynchpin", start=when, duration_s=60)],
    )

    ordered = strongest_work_correlations(rows)

    assert ordered[0].project == "sinity-lynchpin"
    assert ordered[0].source_count == 3


def test_correlate_work_days_accepts_github_context_dicts() -> None:
    when = datetime(2026, 5, 5, 12, tzinfo=UTC)
    rows = correlate_work_days(
        git_facts=[
            SimpleNamespace(
                repo="polylogue",
                commit="abc1234",
                authored_at=when,
                subject="fix: stabilize daemon (#843)",
            )
        ],
        github_items=[
            {
                "repo": "polylogue",
                "slug": "Sinity/polylogue",
                "kind": "pr",
                "number": 843,
                "status": "ok",
                "title": "fix: stabilize daemon",
                "state": "closed",
                "author": "Sinity",
                "url": "https://github.com/Sinity/polylogue/pull/843",
                "merged_at": when.isoformat(),
                "body": "Merged implementation.",
                "labels": ["bug"],
                "comments": [
                    {
                        "author": {"login": "Sinity"},
                        "body": "implemented and merged",
                        "createdAt": when.isoformat(),
                        "url": None,
                    }
                ],
            }
        ],
    )

    assert rows[0].sources == ("git", "github")
    assert rows[0].github_refs == ("pr#843",)
    assert rows[0].github_lifecycles["pr_closed"] == 1


def test_work_day_correlations_distinguish_local_refs_from_fetched_github_items() -> None:
    when = datetime(2026, 5, 5, 12, tzinfo=UTC)
    from lynchpin.composite.evidence_graph import EvidenceGraph, EvidenceNode

    graph = EvidenceGraph(
        start=when.date(),
        end=when.date(),
        generated_at=when,
        mode="local-fast",
        nodes=(
            EvidenceNode(
                id="github:sinity-lynchpin:pr:3",
                kind="github_ref",
                source="github_ref",
                date=when.date(),
                project="sinity-lynchpin",
                summary="pr #3",
                payload={"kind": "pr", "number": 3, "lifecycle": "referenced"},
            ),
        ),
        edges=(),
        caveats=(),
    )

    rows = work_day_correlations(start=when.date(), end=when.date(), graph=graph)

    assert rows[0].sources == ("github_ref",)
    assert rows[0].github_refs == ("pr#3",)
