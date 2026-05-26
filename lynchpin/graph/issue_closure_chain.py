"""Issue closure-chain detection (Arc C.2).

Walks the evidence graph and synthesizes a typed view of how each GitHub
issue closed (or didn't): linked PRs, closing commits, lifecycle status.
Surfaces closure-integrity gaps the original prep doc explicitly anticipated:

- ``complete``  — issue is closed AND has a merged PR/commit referencing it
- ``partial``   — open issue with linked but unmerged PR, or closed issue
                  whose linked PR closed-without-merge but a commit
                  references it
- ``broken``    — closed issue with **only** non-merged PR closure (i.e.,
                  ``Closes #N`` reference but the PR was closed without
                  merge), or an open issue with stale closing reference
                  (commit referenced ≥30 days ago, issue still open)
- ``orphaned``  — closed issue with **no** linked PR or closing commit

Inputs are pulled from ``EvidenceGraph`` nodes:
- ``github_issue`` / ``github_pr`` nodes carry state + lifecycle (Arc K-
  agnostic; lifecycle comes from ``classify_lifecycle``).
- ``commit`` nodes carry ``payload.github_refs.{prs, issues}``.
- ``references`` edges already link commits to github_refs.

Output is purely derivative — does not write JSON artifacts in this
revision; consumers are the context-pack renderer (this commit) and a
follow-up analysis-artifact promoter (M.9 / E.1).
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Literal, Sequence

from ..core.evidence import EvidenceCaveat
from ..core.evidence_graph import EvidenceGraph, EvidenceNode


ClosureStatus = Literal["complete", "partial", "broken", "orphaned"]


@dataclass(frozen=True)
class IssueClosureChain:
    project: str
    issue_ref: str
    issue_state: str           # "open" or "closed"
    issue_lifecycle: str       # github_frontier classification
    opened_at: datetime | None
    closed_at: datetime | None
    linked_pr_refs: tuple[str, ...]
    closing_commit_shas: tuple[str, ...]
    closure_status: ClosureStatus
    evidence_node_ids: tuple[str, ...]
    caveats: tuple[EvidenceCaveat, ...]


_STALE_REFERENCE_DAYS = 30


def detect_closure_chains(
    graph: EvidenceGraph,
    *,
    reference: date | None = None,
) -> tuple[IssueClosureChain, ...]:
    """Detect closure chains across all ``github_issue`` nodes in ``graph``.

    ``reference`` controls "stale reference" detection (defaults to today).
    Issues without project attribution are skipped — there's no useful
    cross-source chain for unattributed nodes.
    """
    ref_date = reference or datetime.now(timezone.utc).date()

    issues, prs, commits, refs_index = _index_graph(graph)
    chains: list[IssueClosureChain] = []

    for issue in issues:
        project = issue.project or "(unknown)"
        number = _payload_int(issue, "number")
        if number == 0:
            continue
        issue_ref = f"issue#{number}"

        linked_prs = _linked_prs(issue, prs, refs_index)
        closing_commits = _commits_referencing_issue(number, commits)
        evidence_ids: list[str] = [issue.id]
        evidence_ids.extend(pr.id for pr in linked_prs)
        evidence_ids.extend(c.id for c in closing_commits)

        closure_status, caveats = _classify_closure(
            issue=issue,
            linked_prs=linked_prs,
            closing_commits=closing_commits,
            reference_date=ref_date,
        )

        chains.append(IssueClosureChain(
            project=project,
            issue_ref=issue_ref,
            issue_state=str(_payload(issue).get("state") or "unknown"),
            issue_lifecycle=str(_payload(issue).get("lifecycle") or "unclear"),
            opened_at=issue.start,
            closed_at=issue.end,
            linked_pr_refs=tuple(sorted(f"pr#{_payload_int(pr, 'number')}" for pr in linked_prs)),
            closing_commit_shas=tuple(sorted(_payload_str(c, "commit") for c in closing_commits if _payload_str(c, "commit"))),
            closure_status=closure_status,
            evidence_node_ids=tuple(evidence_ids),
            caveats=caveats,
        ))

    return tuple(chains)


def render_issue_closure_chains(
    chains: Sequence[IssueClosureChain],
    *,
    limit: int = 12,
) -> str:
    """Compact Markdown table of closure chains, prioritizing broken/partial."""
    if not chains:
        return "_No GitHub issues in the evidence graph for closure-chain analysis._"

    # Order: broken first, then partial, then orphaned, then complete; within
    # each band, prefer recent closure / opening dates.
    status_order = {"broken": 0, "partial": 1, "orphaned": 2, "complete": 3}
    ordered = sorted(
        chains,
        key=lambda c: (
            status_order.get(c.closure_status, 99),
            -(c.closed_at.timestamp() if c.closed_at else (c.opened_at.timestamp() if c.opened_at else 0)),
        ),
    )[:limit]

    lines = [
        "| Project | Issue | State | Closure | Linked PRs | Closing commits | Lifecycle | Caveats |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for chain in ordered:
        prs = ", ".join(chain.linked_pr_refs) if chain.linked_pr_refs else "—"
        shas = ", ".join(s[:8] for s in chain.closing_commit_shas) if chain.closing_commit_shas else "—"
        caveat_text = "<br>".join(c.message.replace("|", "\\|") for c in chain.caveats) or "—"
        lines.append(
            f"| {chain.project} | {chain.issue_ref} | {chain.issue_state} | "
            f"**{chain.closure_status}** | {prs} | {shas} | {chain.issue_lifecycle} | {caveat_text} |"
        )
    return "\n".join(lines)


def closure_chain_summary(chains: Sequence[IssueClosureChain]) -> dict[str, Any]:
    """Aggregate counts useful for pack-level rendering and diagnostics."""
    by_status: dict[str, int] = defaultdict(int)
    by_project_status: dict[tuple[str, str], int] = defaultdict(int)
    for chain in chains:
        by_status[chain.closure_status] += 1
        by_project_status[(chain.project, chain.closure_status)] += 1
    return {
        "total": len(chains),
        "by_status": dict(by_status),
        "broken_or_orphaned": by_status.get("broken", 0) + by_status.get("orphaned", 0),
        "by_project": {
            project: {status: count for (p, status), count in by_project_status.items() if p == project}
            for project in {p for p, _ in by_project_status}
        },
    }


# ── Internal helpers ────────────────────────────────────────────────────────


def _index_graph(
    graph: EvidenceGraph,
) -> tuple[list[EvidenceNode], list[EvidenceNode], list[EvidenceNode], dict[str, EvidenceNode]]:
    """Return (issues, prs, commits, ref_id → node) — a cheap multi-pass scan."""
    issues: list[EvidenceNode] = []
    prs: list[EvidenceNode] = []
    commits: list[EvidenceNode] = []
    refs_index: dict[str, EvidenceNode] = {}
    for node in graph.nodes:
        if node.kind == "github_issue":
            issues.append(node)
            refs_index[node.id] = node
        elif node.kind == "github_pr":
            prs.append(node)
            refs_index[node.id] = node
        elif node.kind == "github_ref":
            refs_index[node.id] = node
        elif node.kind == "commit":
            commits.append(node)
    return issues, prs, commits, refs_index


def _linked_prs(
    issue: EvidenceNode,
    prs: Sequence[EvidenceNode],
    refs_index: dict[str, EvidenceNode],
) -> list[EvidenceNode]:
    """Find PRs linked to this issue.

    The current evidence graph doesn't model an explicit "issue-references-PR"
    edge (PR-body parsing isn't wired). Use a same-project / same-window
    heuristic: any PR whose project matches and whose body or title (via
    ``payload``) suggests a linkage. As a conservative starting point we
    accept any PR whose lifecycle classification points to this issue's
    project — refined when the GitHub source layer grows explicit linkage.
    """
    issue_project = issue.project
    if not issue_project:
        return []
    return [
        pr for pr in prs
        if pr.project == issue_project and _pr_might_close(pr, issue)
    ]


def _pr_might_close(pr: EvidenceNode, issue: EvidenceNode) -> bool:
    """Heuristic: PR title or summary mentions the issue number.

    The summary on a github_pr node is the PR title (set by
    ``_github_item_node`` from ``GitHubItem.title``). PR titles in this repo
    family commonly include the issue number as ``#N``, ``(#N)`` or
    ``Closes #N``. False-positive risk is non-zero; chain caveats note the
    heuristic basis.

    Regex avoids false positives: issue #15 should NOT match PR "#150".
    """
    issue_number = _payload_int(issue, "number")
    if issue_number == 0:
        return False
    title = pr.summary or ""
    # Negative lookbehind/lookahead ensures #N is not part of a larger number
    pattern = rf"(?<!\d)#{issue_number}(?!\d)"
    return bool(re.search(pattern, title))


def _commits_referencing_issue(issue_number: int, commits: Sequence[EvidenceNode]) -> list[EvidenceNode]:
    matched: list[EvidenceNode] = []
    for commit in commits:
        refs = _payload(commit).get("github_refs") or {}
        if isinstance(refs, dict):
            issue_refs = refs.get("issues") or []
            if issue_number in {int(n) for n in issue_refs if isinstance(n, (int, str)) and str(n).isdigit()}:
                matched.append(commit)
    return matched


def _classify_closure(
    *,
    issue: EvidenceNode,
    linked_prs: Sequence[EvidenceNode],
    closing_commits: Sequence[EvidenceNode],
    reference_date: date,
) -> tuple[ClosureStatus, tuple[EvidenceCaveat, ...]]:
    payload = _payload(issue)
    issue_state = str(payload.get("state") or "open").lower()
    caveats: list[EvidenceCaveat] = []

    merged_pr = next((pr for pr in linked_prs if _pr_was_merged(pr)), None)
    closed_unmerged_pr = next(
        (pr for pr in linked_prs
         if str(_payload(pr).get("state") or "").lower() == "closed" and not _pr_was_merged(pr)),
        None,
    )

    if issue_state == "closed":
        if merged_pr or closing_commits:
            return "complete", ()
        if closed_unmerged_pr:
            caveats.append(EvidenceCaveat(
                "github",
                "partial",
                f"closed via PR #{_payload_int(closed_unmerged_pr, 'number')} which closed without merging — execution evidence is weak",
            ))
            return "broken", tuple(caveats)
        return "orphaned", (
            EvidenceCaveat("github", "partial", "closed without linked PR or closing commit"),
        )

    # Open issue
    if linked_prs and not merged_pr:
        # Linked but no merge → partial. Add caveat distinguishing
        # in-progress from stalled.
        oldest = min((pr.start or pr.end or datetime.now(timezone.utc) for pr in linked_prs), default=None)
        if oldest and (reference_date - oldest.date()).days >= _STALE_REFERENCE_DAYS:
            # Lifecycle "stalled" is a github-domain concept, not a data
            # readiness one — encode it in the message; the caveat status
            # stays in the ReadinessStatus vocabulary (partial = closure
            # evidence intersects the window but does not satisfy it).
            caveats.append(EvidenceCaveat(
                "github",
                "partial",
                f"linked PR(s) opened ≥{_STALE_REFERENCE_DAYS} days ago without merge — possibly stalled",
            ))
        return "partial", tuple(caveats)

    # Open issue, no linked PRs at all — check stale closing-commit reference.
    if closing_commits:
        latest_commit_ts = max(
            (c.start or c.end or datetime.now(timezone.utc) for c in closing_commits),
            default=None,
        )
        if latest_commit_ts and (reference_date - latest_commit_ts.date()).days >= _STALE_REFERENCE_DAYS:
            caveats.append(EvidenceCaveat(
                "github",
                "partial",
                f"commit referenced this issue ≥{_STALE_REFERENCE_DAYS}d ago but issue is still open",
            ))
            return "broken", tuple(caveats)
        return "partial", ()

    return "orphaned", (EvidenceCaveat("github", "partial", "open issue without linked PR or referencing commit"),)


def _pr_was_merged(pr: EvidenceNode) -> bool:
    payload = _payload(pr)
    state = str(payload.get("state") or "").lower()
    if state == "merged":
        return True
    # GitHubItem state encodes "merged" explicitly; some upstream variants
    # emit lifecycle "executed" for merged PRs.
    return str(payload.get("lifecycle") or "") == "executed" and state in ("closed", "merged")


def _payload(node: EvidenceNode) -> dict[str, Any]:
    return node.payload or {}


def _payload_int(node: EvidenceNode, field: str) -> int:
    value = _payload(node).get(field)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _payload_str(node: EvidenceNode, field: str) -> str:
    value = _payload(node).get(field)
    return str(value) if value else ""


__all__ = [
    "ClosureStatus",
    "IssueClosureChain",
    "closure_chain_summary",
    "detect_closure_chains",
    "render_issue_closure_chains",
]
