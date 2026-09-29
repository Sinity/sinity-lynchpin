"""Issue closure-chain detection (Arc C.2).

Walks the evidence graph and synthesizes a typed view of how each GitHub
issue closed (or didn't): closing PRs, closing commits, lifecycle status.

Only evidence from the issue's own repository counts; issue numbers are
repository-local. A closing reference uses a GitHub closing keyword
(``closes``/``fixes``/``resolves #N``); a bare ``#N`` or ``refs #N`` is an
ordinary mention and is reported separately, never as closure.

- ``complete``      — closed issue with a merged closing PR or closing commit
- ``dispositioned`` — closed issue whose lifecycle says it was folded,
                      superseded, retired or misframed, with no closing
                      evidence; no implementation is expected
- ``partial``       — open issue with an unmerged closing PR, a recent
                      closing commit, or mentions only; or a closed issue
                      with mentions but no closing reference
- ``broken``        — closed issue whose only closing PR closed without
                      merge, or an open issue with a closing commit
                      ≥30 days old
- ``orphaned``      — no closing reference or mention in the graph

Inputs are pulled from ``EvidenceGraph`` nodes:
- ``github_issue`` / ``github_pr`` nodes carry state + lifecycle
  (``classify_lifecycle``); a PR's ``summary`` is its title.
- ``commit`` nodes carry ``payload.github_refs.issues`` and the subject as
  ``summary``.

Output is purely derivative; consumers are the context-pack renderer and
the current-state timeline.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Literal, Sequence

from ..core.evidence import EvidenceCaveat
from ..core.evidence_graph import EvidenceGraph, EvidenceNode
from ..sources.github import extract_closing_refs


ClosureStatus = Literal["complete", "dispositioned", "partial", "broken", "orphaned"]

# Issue lifecycles (``classify_lifecycle``) that close work without
# implementing it.
_NON_IMPLEMENTATION_LIFECYCLES = frozenset({"folded_or_consolidated", "retired_stale", "misframed"})


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
    mentioning_pr_refs: tuple[str, ...] = ()
    mentioning_commit_shas: tuple[str, ...] = ()


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

    issues, prs, commits = _index_graph(graph)
    chains: list[IssueClosureChain] = []

    for issue in issues:
        project = issue.project or "(unknown)"
        number = _payload_int(issue, "number")
        if number == 0:
            continue
        issue_ref = f"issue#{number}"

        closing_prs, mentioning_prs = _related_prs(issue, prs)
        closing_commits, mentioning_commits = _related_commits(issue, commits)
        evidence_ids: list[str] = [issue.id]
        evidence_ids.extend(pr.id for pr in (*closing_prs, *mentioning_prs))
        evidence_ids.extend(c.id for c in (*closing_commits, *mentioning_commits))

        closure_status, caveats = _classify_closure(
            issue=issue,
            linked_prs=closing_prs,
            closing_commits=closing_commits,
            has_mentions=bool(mentioning_prs or mentioning_commits),
            reference_date=ref_date,
        )

        chains.append(IssueClosureChain(
            project=project,
            issue_ref=issue_ref,
            issue_state=str(_payload(issue).get("state") or "unknown"),
            issue_lifecycle=str(_payload(issue).get("lifecycle") or "unclear"),
            opened_at=issue.start,
            closed_at=issue.end,
            linked_pr_refs=_pr_refs(closing_prs),
            closing_commit_shas=_commit_shas(closing_commits),
            closure_status=closure_status,
            evidence_node_ids=tuple(evidence_ids),
            caveats=caveats,
            mentioning_pr_refs=_pr_refs(mentioning_prs),
            mentioning_commit_shas=_commit_shas(mentioning_commits),
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

    # Order: broken first, then partial, then orphaned, then settled chains;
    # within each band, prefer recent closure / opening dates.
    status_order = {"broken": 0, "partial": 1, "orphaned": 2, "dispositioned": 3, "complete": 4}
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
) -> tuple[list[EvidenceNode], list[EvidenceNode], list[EvidenceNode]]:
    """Return (issues, prs, commits) in one pass."""
    issues: list[EvidenceNode] = []
    prs: list[EvidenceNode] = []
    commits: list[EvidenceNode] = []
    for node in graph.nodes:
        if node.kind == "github_issue":
            issues.append(node)
        elif node.kind == "github_pr":
            prs.append(node)
        elif node.kind == "commit":
            commits.append(node)
    return issues, prs, commits


def _related_prs(
    issue: EvidenceNode,
    prs: Sequence[EvidenceNode],
) -> tuple[list[EvidenceNode], list[EvidenceNode]]:
    """Split same-repository PRs into closing references and mentions.

    The graph carries PR titles, not bodies, so a closing keyword in the
    title is the only closing link visible here.
    """
    if not issue.project:
        return [], []
    closing: list[EvidenceNode] = []
    mentioning: list[EvidenceNode] = []
    for pr in prs:
        if pr.project != issue.project:
            continue
        if _pr_might_close(pr, issue):
            closing.append(pr)
        elif _mentions_issue(pr.summary or "", _payload_int(issue, "number")):
            mentioning.append(pr)
    return closing, mentioning


def _pr_might_close(pr: EvidenceNode, issue: EvidenceNode) -> bool:
    """True when the PR title names this issue with a closing keyword.

    ``closes #15`` does not close issue #150, and a bare ``#15`` is only a
    mention.
    """
    issue_number = _payload_int(issue, "number")
    if issue_number == 0:
        return False
    return issue_number in extract_closing_refs(pr.summary or "")


def _mentions_issue(text: str, issue_number: int) -> bool:
    # ``owner/repo#N`` and ``repo#N`` name another repository's item.
    return bool(re.search(rf"(?<![\w/])#{issue_number}(?!\d)", text))


def _related_commits(
    issue: EvidenceNode,
    commits: Sequence[EvidenceNode],
) -> tuple[list[EvidenceNode], list[EvidenceNode]]:
    """Split same-repository commits referencing the issue into closing and mentions."""
    if not issue.project:
        return [], []
    issue_number = _payload_int(issue, "number")
    closing: list[EvidenceNode] = []
    mentioning: list[EvidenceNode] = []
    for commit in commits:
        if commit.project != issue.project:
            continue
        refs = _payload(commit).get("github_refs") or {}
        issue_refs = refs.get("issues") or [] if isinstance(refs, dict) else []
        numbers = {int(n) for n in issue_refs if isinstance(n, (int, str)) and str(n).isdigit()}
        if issue_number not in numbers:
            continue
        if issue_number in extract_closing_refs(commit.summary or ""):
            closing.append(commit)
        else:
            mentioning.append(commit)
    return closing, mentioning


def _classify_closure(
    *,
    issue: EvidenceNode,
    linked_prs: Sequence[EvidenceNode],
    closing_commits: Sequence[EvidenceNode],
    has_mentions: bool,
    reference_date: date,
) -> tuple[ClosureStatus, tuple[EvidenceCaveat, ...]]:
    payload = _payload(issue)
    issue_state = str(payload.get("state") or "open").lower()
    lifecycle = str(payload.get("lifecycle") or "")
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
        if lifecycle in _NON_IMPLEMENTATION_LIFECYCLES:
            return "dispositioned", (
                EvidenceCaveat("github", "partial", f"closed as {lifecycle}; no implementation expected"),
            )
        if closed_unmerged_pr:
            caveats.append(EvidenceCaveat(
                "github",
                "partial",
                f"closed via PR #{_payload_int(closed_unmerged_pr, 'number')} which closed without merging — execution evidence is weak",
            ))
            return "broken", tuple(caveats)
        if has_mentions:
            return "partial", (
                EvidenceCaveat("github", "partial", "closed issue is mentioned but has no closing PR or commit"),
            )
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

    # Open issue, no open closing PR — a closing commit that did not close it
    # is suspicious once it is old; a mention is ordinary progress.
    if closing_commits:
        latest_commit_ts = max(
            (c.start or c.end or datetime.now(timezone.utc) for c in closing_commits),
            default=None,
        )
        if latest_commit_ts and (reference_date - latest_commit_ts.date()).days >= _STALE_REFERENCE_DAYS:
            caveats.append(EvidenceCaveat(
                "github",
                "partial",
                f"closing commit referenced this issue ≥{_STALE_REFERENCE_DAYS}d ago but issue is still open",
            ))
            return "broken", tuple(caveats)
        return "partial", ()
    if has_mentions:
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


def _pr_refs(prs: Sequence[EvidenceNode]) -> tuple[str, ...]:
    return tuple(sorted(f"pr#{_payload_int(pr, 'number')}" for pr in prs))


def _commit_shas(commits: Sequence[EvidenceNode]) -> tuple[str, ...]:
    return tuple(sorted(_payload_str(c, "commit") for c in commits if _payload_str(c, "commit")))


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
