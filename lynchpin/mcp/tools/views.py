"""View-backed MCP tools: project-day correlations, closure chains, overlaps, PR reviews.

NOTE: do NOT add ``from __future__ import annotations`` here.
FastMCP's Tool.from_function introspects parameter annotations at decoration
time with issubclass(param.annotation, Context); PEP 563 string annotations
cause ``issubclass('str', Context)`` → TypeError.
"""
from dataclasses import asdict
from typing import Any
from lynchpin.mcp.server import app

def _dc_to_dict(obj: Any) -> dict[str, Any]:
    """Convert a dataclass to a JSON-serialisable dict.

    Handles tuple → list, date/datetime → ISO string recursively.
    """
    from datetime import date, datetime

    def _conv(v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, (datetime, date)):
            return v.isoformat()
        if isinstance(v, (list, tuple)):
            return [_conv(i) for i in v]
        if isinstance(v, dict):
            return {k: _conv(vv) for k, vv in v.items()}
        return v
    d = asdict(obj)
    return {k: _conv(v) for k, v in d.items()}

@app.tool()
def project_day_correlations(refresh_id: str | None=None, start: str | None=None, end: str | None=None, projects: list[str] | None=None, min_source_count: int | None=None) -> list[dict[str, Any]]:
    from datetime import date as _date
    from lynchpin.duck.connection import connect, substrate_path
    from lynchpin.duck.reader import load_project_day_correlations
    start_d: _date | None = _date.fromisoformat(start) if start else None
    end_d: _date | None = _date.fromisoformat(end) if end else None
    projs: tuple[str, ...] | None = tuple(projects) if projects else None
    path = substrate_path()
    with connect(path) as conn:
        rows = load_project_day_correlations(conn, refresh_id=refresh_id, start=start_d, end=end_d, projects=projs, min_source_count=min_source_count)
    return [_dc_to_dict(row) for row in rows]

@app.tool()
def closure_chain_walks(refresh_id: str | None=None, project: str | None=None, min_chain_depth: int | None=None) -> list[dict[str, Any]]:
    """Query the issue_closure_chain_walk view.

    Wraps ``lynchpin.duck.reader.load_issue_closure_chain_walks``.

    Parameters:
        refresh_id:     filter to a specific evidence-graph build.
        project:        filter by project name.
        min_chain_depth: only return chains with depth >= N.

    Returns list of dicts with keys: refresh_id, root_id, project,
    issue_number, reachable_node_ids, chain_depth, reachable_count.
    """
    from lynchpin.duck.connection import connect, substrate_path
    from lynchpin.duck.reader import load_issue_closure_chain_walks
    path = substrate_path()
    with connect(path) as conn:
        rows = load_issue_closure_chain_walks(conn, refresh_id=refresh_id, project=project, min_chain_depth=min_chain_depth)
    return [_dc_to_dict(row) for row in rows]

@app.tool()
def file_overlap_edges(we_refresh_id: str | None=None, commit_refresh_id: str | None=None) -> list[dict[str, Any]]:
    """Query the work_event_file_overlap view and return edge dicts.

    Each row represents a file-overlap edge between an AI work-event node
    and a commit node that share file paths within ±24 h.

    Parameters:
        we_refresh_id:     filter to a specific work-event promote batch.
        commit_refresh_id: filter to a specific commit promote batch.

    Returns list of dicts with keys: source_id, target_id, relation,
    evidence, weight.
    """
    from lynchpin.duck.connection import connect, substrate_path
    from lynchpin.duck.reader import compute_file_overlap_edges
    path = substrate_path()
    with connect(path) as conn:
        edges = compute_file_overlap_edges(conn, we_refresh_id=we_refresh_id, commit_refresh_id=commit_refresh_id)
    return [{'source_id': e.source_id, 'target_id': e.target_id, 'relation': e.relation, 'evidence': e.evidence, 'weight': e.weight} for e in edges]

@app.tool()
def symbol_overlap_edges(we_refresh_id: str | None=None, commit_refresh_id: str | None=None) -> list[dict[str, Any]]:
    """Query the work_event_symbol_overlap view and return edge dicts.

    Each row represents a symbol-overlap edge between an AI work-event node
    and a commit node that reference the same qualified symbol names.

    Parameters:
        we_refresh_id:     filter to a specific work-event promote batch.
        commit_refresh_id: filter to a specific commit promote batch.

    Returns list of dicts with keys: source_id, target_id, relation,
    evidence, weight.
    """
    from lynchpin.duck.connection import connect, substrate_path
    from lynchpin.duck.reader import compute_symbol_overlap_edges
    path = substrate_path()
    with connect(path) as conn:
        edges = compute_symbol_overlap_edges(conn, we_refresh_id=we_refresh_id, commit_refresh_id=commit_refresh_id)
    return [{'source_id': e.source_id, 'target_id': e.target_id, 'relation': e.relation, 'evidence': e.evidence, 'weight': e.weight} for e in edges]

@app.tool()
def pr_review_rows(projects: list[str] | None=None, states: list[str] | None=None, only_with_friction: bool=False, refresh_id: str | None=None) -> list[dict[str, Any]]:
    """Read the pr_review_row substrate table.

    Wraps ``lynchpin.duck.reader.load_pr_review_rows``.

    Parameters:
        projects:          filter by project name list; None = all.
        states:            filter by PR state, e.g. ["merged", "open"].
        only_with_friction: when True, only return PRs with friction signals.
        refresh_id:        filter to a specific promote batch.

    Returns list of dicts matching PrReviewRow fields: project, number,
    title, state, url, author, created_at, closed_at, merged_at,
    review_count, review_decisions, review_round_count, reviewer_count,
    reviewers, review_comment_count, top_level_comment_count,
    changes_requested_count, approval_count, dismissed_count,
    time_to_first_review_minutes, time_to_close_minutes,
    time_to_merge_minutes, final_decision, friction_signals.
    """
    from lynchpin.duck.connection import connect, substrate_path
    from lynchpin.duck.reader import load_pr_review_rows
    projs: tuple[str, ...] | None = tuple(projects) if projects else None
    sts: tuple[str, ...] | None = tuple(states) if states else None
    path = substrate_path()
    with connect(path) as conn:
        rows = load_pr_review_rows(conn, projects=projs, states=sts, only_with_friction=only_with_friction, refresh_id=refresh_id)
    return [_dc_to_dict(row) for row in rows]
