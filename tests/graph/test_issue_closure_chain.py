"""Tests for IssueClosureChain detection (Arc C.2)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from lynchpin.core.evidence_graph import EvidenceGraph, EvidenceNode
from lynchpin.graph.issue_closure_chain import (
    _pr_might_close,
    closure_chain_summary,
    detect_closure_chains,
    render_issue_closure_chains,
)

UTC = timezone.utc


def _graph(nodes: list[EvidenceNode]) -> EvidenceGraph:
    return EvidenceGraph(
        start=date(2026, 5, 1),
        end=date(2026, 5, 7),
        generated_at=datetime(2026, 5, 7, tzinfo=UTC),
        nodes=tuple(nodes),
        edges=(),
        caveats=(),
    )


def _issue(*, number: int, project: str = "demo", state: str = "open",
           opened: datetime | None = None, closed: datetime | None = None,
           lifecycle: str = "open_frontier") -> EvidenceNode:
    return EvidenceNode(
        id=f"github:{project}:issue:{number}",
        kind="github_issue",
        source="github",
        date=(closed or opened or datetime(2026, 5, 1, tzinfo=UTC)).date(),
        project=project,
        start=opened,
        end=closed,
        summary=f"Issue title #{number}",
        payload={"kind": "issue", "number": number, "state": state, "lifecycle": lifecycle},
    )


def _pr(*, number: int, project: str = "demo", title: str = "feat: do thing #1",
        state: str = "open", opened: datetime | None = None, closed: datetime | None = None,
        merged: bool = False) -> EvidenceNode:
    final_state = "merged" if merged else state
    return EvidenceNode(
        id=f"github:{project}:pr:{number}",
        kind="github_pr",
        source="github",
        date=(closed or opened or datetime(2026, 5, 1, tzinfo=UTC)).date(),
        project=project,
        start=opened,
        end=closed,
        summary=title,
        payload={
            "kind": "pr", "number": number, "state": final_state,
            "lifecycle": "executed" if merged else "open_frontier",
        },
    )


def _commit(*, sha: str, project: str = "demo", issue_refs: tuple[int, ...] = (),
            authored: datetime | None = None, closing: bool = True) -> EvidenceNode:
    verb = "fixes" if closing else "refs"
    subject = f"feat: do thing, {verb} #{issue_refs[0]}" if issue_refs else "feat: do thing"
    return EvidenceNode(
        id=f"git:{project}:{sha}",
        kind="commit",
        source="git",
        date=(authored or datetime(2026, 5, 1, tzinfo=UTC)).date(),
        project=project,
        start=authored,
        end=authored,
        summary=subject,
        payload={
            "commit": sha,
            "github_refs": {"issues": list(issue_refs), "prs": []},
            "paths": (),
        },
    )


def test_complete_when_closed_issue_has_merged_pr_and_commit():
    base = datetime(2026, 5, 1, 10, tzinfo=UTC)
    nodes = [
        _issue(number=1, state="closed", opened=base, closed=base + timedelta(days=2),
               lifecycle="executed"),
        _pr(number=10, title="feat: implement, closes #1", merged=True,
            opened=base + timedelta(hours=1), closed=base + timedelta(days=2)),
        _commit(sha="abc123", issue_refs=(1,), authored=base + timedelta(days=2)),
    ]
    chains = detect_closure_chains(_graph(nodes), reference=date(2026, 5, 7))
    assert len(chains) == 1
    assert chains[0].closure_status == "complete"
    assert chains[0].linked_pr_refs == ("pr#10",)
    assert chains[0].closing_commit_shas == ("abc123",)


def test_orphaned_when_closed_issue_has_no_pr_or_commit():
    base = datetime(2026, 5, 1, 10, tzinfo=UTC)
    nodes = [
        _issue(number=2, state="closed", opened=base, closed=base + timedelta(days=1),
               lifecycle="unclear"),
    ]
    chains = detect_closure_chains(_graph(nodes), reference=date(2026, 5, 7))
    assert chains[0].closure_status == "orphaned"
    assert "without linked PR" in chains[0].caveats[0].message


def test_broken_when_closed_issue_only_has_unmerged_pr():
    base = datetime(2026, 5, 1, 10, tzinfo=UTC)
    nodes = [
        _issue(number=3, state="closed", opened=base, closed=base + timedelta(days=2)),
        _pr(number=11, title="wip: try, fixes #3", state="closed", merged=False,
            opened=base + timedelta(hours=1), closed=base + timedelta(days=2)),
    ]
    chains = detect_closure_chains(_graph(nodes), reference=date(2026, 5, 7))
    assert chains[0].closure_status == "broken"
    assert any("closed without merging" in c.message for c in chains[0].caveats)


def test_partial_when_open_issue_has_unmerged_linked_pr():
    base = datetime(2026, 5, 1, 10, tzinfo=UTC)
    nodes = [
        _issue(number=4, state="open", opened=base),
        _pr(number=12, title="feat: in-progress, resolves #4", state="open", opened=base + timedelta(hours=1)),
    ]
    chains = detect_closure_chains(_graph(nodes), reference=date(2026, 5, 7))
    assert chains[0].closure_status == "partial"


def test_stale_pr_for_open_issue_adds_caveat():
    base = datetime(2026, 4, 1, 10, tzinfo=UTC)  # > 30 days before reference
    nodes = [
        _issue(number=5, state="open", opened=base),
        _pr(number=13, title="wip: closes #5", state="open", opened=base),
    ]
    chains = detect_closure_chains(_graph(nodes), reference=date(2026, 5, 7))
    assert chains[0].closure_status == "partial"
    assert any("stalled" in c.message or "≥30" in c.message for c in chains[0].caveats)


def test_open_issue_with_only_stale_referencing_commit_is_broken():
    base = datetime(2026, 4, 1, 10, tzinfo=UTC)
    nodes = [
        _issue(number=6, state="open", opened=base),
        _commit(sha="def456", issue_refs=(6,), authored=base),
    ]
    chains = detect_closure_chains(_graph(nodes), reference=date(2026, 5, 7))
    assert chains[0].closure_status == "broken"


def test_pr_might_close_requires_closing_keyword_and_exact_number():
    issue_15 = _issue(number=15)
    issue_150 = _issue(number=150)

    assert not _pr_might_close(_pr(number=1, title="feat: closes #150"), issue_15)
    assert _pr_might_close(_pr(number=2, title="fix: off-by-one, fixes #15"), issue_15)
    both = _pr(number=3, title="chore: closes #15, resolves #150")
    assert _pr_might_close(both, issue_15)
    assert _pr_might_close(both, issue_150)
    # A bare or parenthesized number is a mention, not a closing reference.
    assert not _pr_might_close(_pr(number=4, title="fix: off-by-one (#15)"), issue_15)


def test_other_repository_evidence_cannot_close_or_stale_flag_an_issue():
    base = datetime(2026, 3, 1, 10, tzinfo=UTC)
    nodes = [
        _issue(number=42, project="demo", state="closed", opened=base,
               closed=base + timedelta(days=2), lifecycle="unclear"),
        _issue(number=42, project="other", state="open", opened=base),
        _pr(number=7, project="other", title="feat: closes #42", merged=True,
            opened=base, closed=base + timedelta(days=1)),
        _commit(sha="c0ffee", project="other", issue_refs=(42,), authored=base),
    ]
    chains = {c.project: c for c in detect_closure_chains(_graph(nodes), reference=date(2026, 5, 7))}

    # demo#42 has no evidence in its own repository.
    assert chains["demo"].closure_status == "orphaned"
    assert chains["demo"].linked_pr_refs == ()
    assert chains["demo"].closing_commit_shas == ()
    # other#42 keeps its own repository's evidence.
    assert chains["other"].linked_pr_refs == ("pr#7",)
    assert chains["other"].closing_commit_shas == ("c0ffee",)


def test_mentions_are_reported_but_never_complete_a_chain():
    base = datetime(2026, 5, 1, 10, tzinfo=UTC)
    nodes = [
        _issue(number=8, state="closed", opened=base, closed=base + timedelta(days=2),
               lifecycle="unclear"),
        _pr(number=30, title="docs: follow-up to #8", merged=True,
            opened=base, closed=base + timedelta(days=1)),
        _commit(sha="feed", issue_refs=(8,), authored=base, closing=False),
    ]
    chain = detect_closure_chains(_graph(nodes), reference=date(2026, 5, 7))[0]

    assert chain.closure_status == "partial"
    assert chain.linked_pr_refs == ()
    assert chain.closing_commit_shas == ()
    assert chain.mentioning_pr_refs == ("pr#30",)
    assert chain.mentioning_commit_shas == ("feed",)


def test_old_mention_of_open_issue_is_not_stale_closure():
    base = datetime(2026, 3, 1, 10, tzinfo=UTC)
    nodes = [
        _issue(number=9, state="open", opened=base),
        _commit(sha="beef", issue_refs=(9,), authored=base, closing=False),
    ]
    chain = detect_closure_chains(_graph(nodes), reference=date(2026, 5, 7))[0]

    assert chain.closure_status == "partial"
    assert chain.caveats == ()


def test_superseded_or_retired_issue_is_dispositioned_not_failed():
    base = datetime(2026, 5, 1, 10, tzinfo=UTC)
    nodes = [
        _issue(number=40, state="closed", opened=base, closed=base + timedelta(days=1),
               lifecycle="folded_or_consolidated"),
        _issue(number=41, state="closed", opened=base, closed=base + timedelta(days=1),
               lifecycle="retired_stale"),
        _pr(number=140, title="wip: fixes #41", state="closed", merged=False,
            opened=base, closed=base + timedelta(days=1)),
    ]
    chains = detect_closure_chains(_graph(nodes), reference=date(2026, 5, 7))

    assert [c.closure_status for c in chains] == ["dispositioned", "dispositioned"]
    summary = closure_chain_summary(chains)
    assert summary["broken_or_orphaned"] == 0


def test_summary_aggregates_status_counts():
    base = datetime(2026, 5, 1, 10, tzinfo=UTC)
    nodes = [
        _issue(number=10, state="closed", opened=base, closed=base + timedelta(days=1)),
        _issue(number=11, state="closed", opened=base, closed=base + timedelta(days=1)),
        _issue(number=12, state="open", opened=base),
    ]
    chains = detect_closure_chains(_graph(nodes), reference=date(2026, 5, 7))
    summary = closure_chain_summary(chains)
    assert summary["total"] == 3
    # All three are orphaned (no PRs / commits in fixture).
    assert summary["by_status"]["orphaned"] == 3


def test_render_prioritizes_broken_then_partial():
    base = datetime(2026, 5, 1, 10, tzinfo=UTC)
    nodes = [
        # broken
        _issue(number=20, state="closed", opened=base, closed=base + timedelta(days=2)),
        _pr(number=120, title="wip: fixes #20", state="closed", merged=False,
            opened=base, closed=base + timedelta(days=2)),
        # complete
        _issue(number=21, state="closed", opened=base, closed=base + timedelta(days=2)),
        _pr(number=121, title="feat: closes #21", merged=True, opened=base, closed=base + timedelta(days=2)),
        _commit(sha="x", issue_refs=(21,), authored=base + timedelta(days=2)),
    ]
    chains = detect_closure_chains(_graph(nodes), reference=date(2026, 5, 7))
    rendered = render_issue_closure_chains(chains)
    # Broken row appears before complete in output.
    broken_pos = rendered.find("issue#20")
    complete_pos = rendered.find("issue#21")
    assert broken_pos != -1 and complete_pos != -1
    assert broken_pos < complete_pos
