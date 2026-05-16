"""Git and GitHub source-node builders for the evidence graph."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ..core.evidence import CostClass, EvidenceCaveat, EvidenceProvenance
from ..core.evidence_graph import EvidenceEdge, EvidenceNode
from ..core.parse import parse_datetime
from ..core.primitives import logical_date
from ..sources.github import (
    GitHubActor,
    GitHubComment,
    GitHubItem,
    GitHubItemKind,
    GitHubItemState,
    GitHubLabel,
    classify_lifecycle,
    extract_commit_refs,
)
from .evidence_projects import include_project, normalize_project


def commit_facts(*args: Any, **kwargs: Any) -> Any:
    from ..sources.git import commit_facts as impl

    return impl(*args, **kwargs)


def github_context_for_commits(*args: Any, **kwargs: Any) -> Any:
    from ..sources.git import github_context_for_commits as impl

    return impl(*args, **kwargs)


def add_git(
    nodes: list[EvidenceNode],
    edges: list[EvidenceEdge],
    *,
    start: date,
    end: date,
    selected: set[str],
    mode: CostClass,
) -> None:
    facts = tuple(
        commit_facts(
            start=start, end=end + timedelta(days=1), include_paths=mode != "local-fast"
        )
    )
    selected_facts = []
    for fact in facts:
        project = normalize_project(fact.repo)
        if project is None:
            continue
        if not include_project(project, selected):
            continue
        selected_facts.append(fact)
        day = logical_date(fact.authored_at)
        node_id = f"git:{project}:{fact.commit}"
        refs = extract_commit_refs(fact.subject)
        nodes.append(
            EvidenceNode(
                id=node_id,
                kind="commit",
                source="git",
                date=day,
                project=project,
                start=fact.authored_at,
                end=fact.authored_at,
                summary=fact.subject,
                payload={
                    "commit": fact.commit,
                    "author": fact.author,
                    "lines_added": fact.lines_added,
                    "lines_deleted": fact.lines_deleted,
                    "lines_changed": fact.lines_changed,
                    "files_changed": fact.files_changed,
                    "paths": fact.paths,
                    "github_refs": {
                        "prs": sorted(refs["prs"]),
                        "issues": sorted(refs["issues"]),
                    },
                },
                provenance=EvidenceProvenance("git", mode),
            )
        )
        for kind, numbers in (("pr", refs["prs"]), ("issue", refs["issues"])):
            for number in sorted(numbers):
                ref_id = _github_ref_id(project, kind, number)
                nodes.append(
                    _github_ref_node(project=project, kind=kind, number=number, day=day)
                )
                edges.append(
                    EvidenceEdge(
                        node_id,
                        ref_id,
                        "references",
                        f"commit subject references {kind} #{number}",
                        0.9,
                    )
                )

    if mode != "network":
        return
    context = github_context_for_commits(selected_facts)
    raw_items = context.get("items", ()) if isinstance(context, dict) else ()
    for item in _dict_items(raw_items):
        gh_item = _github_item_from_dict(item)
        if gh_item is None:
            continue
        nodes.append(_github_item_node(gh_item))


def _github_ref_node(
    *, project: str, kind: str, number: int, day: date
) -> EvidenceNode:
    return EvidenceNode(
        id=_github_ref_id(project, kind, number),
        kind="github_ref",
        source="github_ref",
        date=day,
        project=project,
        summary=f"{kind} #{number}",
        payload={"kind": kind, "number": number, "lifecycle": "referenced"},
        provenance=EvidenceProvenance("github_ref", "local-fast"),
        caveats=(
            EvidenceCaveat(
                "github",
                "partial",
                "Commit referenced this GitHub item, but full issue/PR lifecycle may not be fetched.",
            ),
        ),
    )


def _github_item_node(item: GitHubItem) -> EvidenceNode:
    project = normalize_project(
        item.repo or (item.slug.rsplit("/", 1)[-1] if item.slug else None)
    )
    stamp = item.closed_at or item.merged_at or item.updated_at or item.created_at
    day = logical_date(stamp) if stamp is not None else date.today()
    lifecycle = classify_lifecycle(item)
    return EvidenceNode(
        id=_github_ref_id(project or item.repo, item.kind, item.number),
        kind="github_pr" if item.kind == "pr" else "github_issue",
        source="github",
        date=day,
        project=project,
        start=item.created_at,
        end=item.closed_at or item.merged_at,
        url=item.url,
        summary=item.title,
        payload={
            "kind": item.kind,
            "number": item.number,
            "state": item.state,
            "lifecycle": lifecycle.lifecycle,
            "lifecycle_confidence": lifecycle.confidence,
            "comment_count": len(item.comments),
        },
        provenance=EvidenceProvenance("github", "network", path=item.slug),
    )


def _github_item_from_dict(item: dict[str, object]) -> GitHubItem | None:
    number = _int(item.get("number"))
    if number == 0:
        return None
    comments = []
    for raw_comment in _dict_items(item.get("comments")):
        raw_author = raw_comment.get("author") or {}
        comments.append(
            GitHubComment(
                author=GitHubActor(
                    raw_author.get("login") if isinstance(raw_author, dict) else None
                ),
                body=str(raw_comment.get("body") or ""),
                created_at=parse_datetime(raw_comment.get("createdAt")),
                url=str(raw_comment.get("url")) if raw_comment.get("url") else None,
            )
        )
    labels = tuple(
        GitHubLabel(str(label)) for label in _list_items(item.get("labels")) if label
    )
    kind: GitHubItemKind = "pr" if item.get("kind") == "pr" else "issue"
    raw_state = str(item.get("state") or "open").lower()
    item_state: GitHubItemState
    if raw_state == "open":
        item_state = "open"
    elif raw_state == "closed":
        item_state = "closed"
    elif raw_state == "merged":
        item_state = "merged"
    else:
        item_state = "unknown"
    return GitHubItem(
        repo=str(item.get("repo") or ""),
        slug=str(item.get("slug") or ""),
        kind=kind,
        number=number,
        title=str(item.get("title") or ""),
        state=item_state,
        url=str(item.get("url")) if item.get("url") else None,
        author=GitHubActor(str(item.get("author") or "") or None),
        labels=labels,
        body=str(item.get("body") or ""),
        comments=tuple(comments),
        created_at=parse_datetime(item.get("created_at") or item.get("createdAt")),
        updated_at=parse_datetime(item.get("updated_at") or item.get("updatedAt")),
        closed_at=parse_datetime(item.get("closed_at") or item.get("closedAt")),
        merged_at=parse_datetime(item.get("merged_at") or item.get("mergedAt")),
        merge_commit=str(item.get("merge_commit"))
        if item.get("merge_commit")
        else None,
    )


def _github_ref_id(project: str, kind: str, number: int) -> str:
    return f"github:{project}:{kind}:{number}"


def _dict_items(value: object) -> tuple[dict[str, object], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, dict))


def _list_items(value: object) -> tuple[object, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(value)


def _int(value: object) -> int:
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0
