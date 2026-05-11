"""DuckDB substrate readers — typed dataclass hydration.

Each function SELECTs from the corresponding substrate table and returns a list
of lynchpin's existing dataclasses (or list[dict] when no dataclass exists).

SQL parameters are always bound via ``?`` placeholders — never f-string
interpolated — so there is no injection surface.

Column-shape notes:
- ``paths`` / ``path_roots`` / ``file_paths`` / ``tools_used`` / ``VARCHAR[]``
  columns come back from DuckDB as Python ``list[str]``. The target dataclasses
  use ``tuple[str, ...]``; we convert with ``tuple(...)`` at hydration time.
- ``github_refs STRUCT(issues INTEGER[], prs INTEGER[])`` comes back as a
  Python ``dict`` with keys ``"issues"`` and ``"prs"``. We pass through to
  ``github_refs`` in the returned dicts (symbol_changes) or discard (commits,
  which don't expose it on ``GitCommitFact``).
- ``TIMESTAMPTZ`` columns come back as timezone-aware ``datetime`` objects.
  ``PrReviewRow.created_at`` etc. are typed ``str | None``; we call
  ``.isoformat()`` on non-None values.
- ``start_ts`` on ``ai_work_event`` is nullable. When a date filter is
  supplied, events with ``start_ts IS NULL`` are **excluded**.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Literal
if TYPE_CHECKING:
    import duckdb
_TIER_RANK_SQL = "CASE kind_tier WHEN 'high' THEN 3 WHEN 'medium' THEN 2 WHEN 'low' THEN 1 ELSE 0 END"
_TIER_RANK_VALUES: dict[str, int] = {'high': 3, 'medium': 2, 'low': 1}

def _build_where(clauses: list[str], params: list[Any]) -> str:
    """Return a WHERE clause string (including the keyword) or '' if empty."""
    if not clauses:
        return ''
    return 'WHERE ' + ' AND '.join(clauses)

def _add_date_filter(column: str, start: date | None, end: date | None, clauses: list[str], params: list[Any], *, nullable: bool=False) -> None:
    """Append date range clauses using ``column::DATE BETWEEN ? AND ?``."""
    if start is None and end is None:
        return
    if nullable:
        clauses.append(f'{column} IS NOT NULL')
    if start is not None and end is not None:
        clauses.append(f'{column}::DATE BETWEEN ? AND ?')
        params.extend([start, end])
    elif start is not None:
        clauses.append(f'{column}::DATE >= ?')
        params.append(start)
    else:
        clauses.append(f'{column}::DATE <= ?')
        params.append(end)

def _add_in_filter(column: str, values: tuple[str, ...] | None, clauses: list[str], params: list[Any]) -> None:
    """Append an IN (?, ?, …) clause for a string tuple filter."""
    if not values:
        return
    placeholders = ', '.join('?' * len(values))
    clauses.append(f'{column} IN ({placeholders})')
    params.extend(values)

def load_commit_facts(conn: 'duckdb.DuckDBPyConnection', *, start: date | None=None, end: date | None=None, projects: tuple[str, ...] | None=None, refresh_id: str | None=None) -> list[Any]:
    """SELECT and hydrate ``commit_fact`` rows to ``GitCommitFact`` instances.

    Filters compose with AND. All filters are optional.
    ``paths`` and ``path_roots`` (``VARCHAR[]``) are converted from list to
    tuple to match the frozen dataclass signature.
    """
    from lynchpin.sources.git import GitCommitFact
    clauses: list[str] = []
    params: list[Any] = []
    _add_date_filter('authored_at', start, end, clauses, params)
    _add_in_filter('project', projects, clauses, params)
    if refresh_id is not None:
        clauses.append('refresh_id = ?')
        params.append(refresh_id)
    where = _build_where(clauses, params)
    sql = f'\n        SELECT\n            sha, repo, project, authored_at, author, subject,\n            lines_added, lines_deleted, lines_changed, files_changed,\n            paths, path_roots\n        FROM commit_fact\n        {where}\n        ORDER BY authored_at\n    '
    rows = conn.execute(sql, params).fetchall()
    results: list[Any] = []
    for sha, repo, project, authored_at, author, subject, lines_added, lines_deleted, lines_changed, files_changed, paths, path_roots in rows:
        results.append(GitCommitFact(repo=repo, commit=sha, authored_at=authored_at, author=author or '', subject=subject or '', lines_added=lines_added, lines_deleted=lines_deleted, lines_changed=lines_changed, files_changed=files_changed, paths=tuple(paths) if paths else (), path_roots=tuple(path_roots) if path_roots else ()))
    return results

def read_commit_facts(conn: 'duckdb.DuckDBPyConnection', *, start: date | None=None, end: date | None=None, projects: tuple[str, ...] | None=None, refresh_id: str | None=None) -> dict[str, Any]:
    """Return a payload dict matching ``active_commit_facts.json`` shape.

    Queries ``commit_fact`` and wraps results in
    ``{"commits": [...], "projects": [...], "window": {...}}``
    so downstream consumers (ai_attribution, work_packages) see the same
    structure they get from the JSON file.
    """
    clauses: list[str] = []
    params: list[Any] = []
    _add_date_filter('authored_at', start, end, clauses, params)
    _add_in_filter('project', projects, clauses, params)
    if refresh_id is not None:
        clauses.append('refresh_id = ?')
        params.append(refresh_id)
    where = _build_where(clauses, params)
    sql = f'\n        SELECT\n            sha, repo, project, authored_at, author, subject,\n            lines_added, lines_deleted, lines_changed, files_changed,\n            paths, path_roots, conventional_kind, conventional_scope,\n            conventional_signature, github_refs, categories, change_types,\n            classified_files_changed, parent_count, default_branch,\n        FROM commit_fact\n        {where}\n        ORDER BY authored_at\n    '
    rows = conn.execute(sql, params).fetchall()
    commits: list[dict[str, Any]] = []
    seen_projects: dict[str, dict[str, Any]] = {}
    actual_start: str | None = None
    actual_end: str | None = None
    for sha, repo, project, authored_at, author, subject, lines_added, lines_deleted, lines_changed, files_changed, paths, path_roots, conv_kind, conv_scope, conv_signature, github_refs, categories, change_types, classified_files_changed, parent_count, default_branch in rows:
        ts = authored_at.isoformat() if isinstance(authored_at, datetime) else str(authored_at)
        d = authored_at.date().isoformat() if isinstance(authored_at, datetime) else ts[:10]
        if actual_start is None or d < actual_start:
            actual_start = d
        if actual_end is None or d > actual_end:
            actual_end = d
        commits.append({'project': project, 'sha': sha, 'short_sha': sha[:7], 'timestamp': ts, 'date': d, 'subject': subject or '', 'author': author or '', 'conventional_kind': conv_kind or 'other', 'conventional_scope': conv_scope or '', 'conventional_signature': conv_signature or 'other', 'paths': list(paths) if paths else [], 'path_roots': list(path_roots) if path_roots else [], 'categories': list(categories) if isinstance(categories, list) else categories if categories else [], 'github_refs': github_refs or {}, 'change_types': list(change_types) if isinstance(change_types, list) else change_types if change_types else [], 'classified_files_changed': classified_files_changed or 0, 'lines_added': lines_added or 0, 'lines_deleted': lines_deleted or 0, 'lines_changed': lines_changed or 0, 'files_changed': files_changed or 0, 'default_branch': default_branch or 'main'})
        if project and project not in seen_projects:
            seen_projects[project] = {'project': project, 'default_branch': default_branch or 'main'}
    return {'commits': commits, 'projects': list(seen_projects.values()), 'window': {'start': actual_start or (start.isoformat() if start else ''), 'end': actual_end or (end.isoformat() if end else '')}}

def load_file_change_facts(conn: 'duckdb.DuckDBPyConnection', *, start: date | None=None, end: date | None=None, projects: tuple[str, ...] | None=None, refresh_id: str | None=None) -> list[Any]:
    """SELECT and hydrate ``file_change_fact`` rows to ``GitFileChangeFact``."""
    from lynchpin.sources.git import GitFileChangeFact
    clauses: list[str] = []
    params: list[Any] = []
    _add_date_filter('authored_at', start, end, clauses, params)
    _add_in_filter('project', projects, clauses, params)
    if refresh_id is not None:
        clauses.append('refresh_id = ?')
        params.append(refresh_id)
    where = _build_where(clauses, params)
    sql = f'\n        SELECT\n            sha, repo, authored_at, path, path_root,\n            lines_added, lines_deleted, lines_changed\n        FROM file_change_fact\n        {where}\n        ORDER BY authored_at, sha, path\n    '
    rows = conn.execute(sql, params).fetchall()
    results: list[Any] = []
    for sha, repo, authored_at, path, path_root, lines_added, lines_deleted, lines_changed in rows:
        results.append(GitFileChangeFact(repo=repo, commit=sha, authored_at=authored_at, path=path, path_root=path_root or '', lines_added=lines_added, lines_deleted=lines_deleted, lines_changed=lines_changed))
    return results

def load_ai_work_events(conn: 'duckdb.DuckDBPyConnection', *, start: date | None=None, end: date | None=None, projects: tuple[str, ...] | None=None, kinds: tuple[str, ...] | None=None, min_kind_tier: Literal['high', 'medium', 'low'] | None=None, refresh_id: str | None=None) -> list[Any]:
    """SELECT and hydrate ``ai_work_event`` rows to ``WorkEvent`` instances.

    ``kind_tier`` and ``kind_source`` are substrate-only columns (not on
    ``WorkEvent``). They are used for filtering here and discarded on hydration.
    Use ``load_ai_work_event_labels`` if you need them.

    Date filtering: when ``start`` or ``end`` is given, events with
    ``start_ts IS NULL`` are **excluded** — they cannot be placed in time.
    Without a date filter, all events are returned regardless of ``start_ts``.

    ``file_paths`` and ``tools_used`` (``VARCHAR[]``) are converted from list
    to tuple.
    """
    from lynchpin.sources.polylogue import WorkEvent
    clauses: list[str] = []
    params: list[Any] = []
    _add_date_filter('start_ts', start, end, clauses, params, nullable=True)
    _add_in_filter('project', projects, clauses, params)
    _add_in_filter('kind', kinds, clauses, params)
    if min_kind_tier is not None:
        min_rank = _TIER_RANK_VALUES.get(min_kind_tier, 0)
        clauses.append(f'({_TIER_RANK_SQL}) >= ?')
        params.append(min_rank)
    if refresh_id is not None:
        clauses.append('refresh_id = ?')
        params.append(refresh_id)
    where = _build_where(clauses, params)
    sql = f'\n        SELECT\n            event_id, conversation_id, provider, kind, kind_confidence,\n            start_ts, end_ts, duration_ms, file_paths, tools_used, summary\n        FROM ai_work_event\n        {where}\n        ORDER BY start_ts NULLS LAST, event_id\n    '
    rows = conn.execute(sql, params).fetchall()
    results: list[Any] = []
    for event_id, conversation_id, provider, kind, kind_confidence, start_ts, end_ts, duration_ms, file_paths, tools_used, summary in rows:
        results.append(WorkEvent(event_id=event_id, conversation_id=conversation_id, provider=provider, kind=kind, confidence=kind_confidence, start=start_ts, end=end_ts, duration_ms=duration_ms, file_paths=tuple(file_paths) if file_paths else (), tools_used=tuple(tools_used) if tools_used else (), summary=summary or ''))
    return results

def load_ai_work_event_labels(conn: 'duckdb.DuckDBPyConnection', *, refresh_id: str | None=None) -> dict[str, Any]:
    """Return ``event_id → WorkEventKindLabel`` mapping.

    Includes the substrate-only tier/source columns that ``load_ai_work_events``
    discards. Useful for callers that want to inspect or render classification
    metadata.
    """
    from lynchpin.graph.work_event_kind import KindSource, ConfidenceTier, WorkEventKindLabel
    clauses: list[str] = []
    params: list[Any] = []
    if refresh_id is not None:
        clauses.append('refresh_id = ?')
        params.append(refresh_id)
    where = _build_where(clauses, params)
    sql = f'\n        SELECT\n            event_id, kind, kind_confidence, kind_source, kind_tier,\n            polylogue_kind, polylogue_confidence,\n            overlay_kind, overlay_confidence\n        FROM ai_work_event\n        {where}\n    '
    rows = conn.execute(sql, params).fetchall()
    out: dict[str, Any] = {}
    for event_id, kind, kind_confidence, kind_source, kind_tier, polylogue_kind, polylogue_confidence, overlay_kind, overlay_confidence in rows:
        out[event_id] = WorkEventKindLabel(kind=kind, confidence=kind_confidence, source=kind_source or 'polylogue', tier=kind_tier or 'low', polylogue_kind=polylogue_kind, polylogue_confidence=polylogue_confidence or 0.0, overlay_kind=overlay_kind, overlay_confidence=overlay_confidence or 0.0, features={})
    return out

def load_symbol_changes(conn: 'duckdb.DuckDBPyConnection', *, start: date | None=None, end: date | None=None, projects: tuple[str, ...] | None=None, paths: tuple[str, ...] | None=None, only_breaking: bool=False, refresh_id: str | None=None) -> list[dict[str, Any]]:
    """SELECT ``symbol_change`` rows.

    No lynchpin dataclass exists for symbol changes, so we return
    ``list[dict]`` matching the source-of-truth row shape from
    ``build_active_symbol_changes``. The ``date`` column is a Python
    ``date`` object (DuckDB DATE maps to ``datetime.date`` directly).
    """
    clauses: list[str] = []
    params: list[Any] = []
    if start is not None or end is not None:
        if start is not None and end is not None:
            clauses.append('date BETWEEN ? AND ?')
            params.extend([start, end])
        elif start is not None:
            clauses.append('date >= ?')
            params.append(start)
        else:
            clauses.append('date <= ?')
            params.append(end)
    _add_in_filter('project', projects, clauses, params)
    _add_in_filter('path', paths, clauses, params)
    if only_breaking:
        clauses.append('breaking_candidate = TRUE')
    if refresh_id is not None:
        clauses.append('refresh_id = ?')
        params.append(refresh_id)
    where = _build_where(clauses, params)
    sql = f'\n        SELECT\n            sha, project, date, path, change_type,\n            qualified_name, symbol_kind, exported, breaking_candidate,\n            refresh_id\n        FROM symbol_change\n        {where}\n        ORDER BY date, sha, path, qualified_name\n    '
    rows = conn.execute(sql, params).fetchall()
    return [{'sha': sha, 'project': project, 'date': row_date, 'path': path, 'change_type': change_type, 'qualified_name': qualified_name, 'symbol_kind': symbol_kind, 'exported': exported, 'breaking_candidate': breaking_candidate, 'refresh_id': refresh_id_col} for sha, project, row_date, path, change_type, qualified_name, symbol_kind, exported, breaking_candidate, refresh_id_col in rows]

def load_pr_review_rows(conn: 'duckdb.DuckDBPyConnection', *, projects: tuple[str, ...] | None=None, states: tuple[str, ...] | None=None, only_with_friction: bool=False, refresh_id: str | None=None) -> list[Any]:
    """SELECT and hydrate ``pr_review_row`` rows to ``PrReviewRow`` instances.

    ``created_at``, ``closed_at``, and ``merged_at`` are ``TIMESTAMPTZ`` in
    the substrate but ``str | None`` on ``PrReviewRow``; we call ``.isoformat()``
    on non-None DuckDB datetime values.

    ``review_decisions``, ``reviewers``, and ``friction_signals`` (``VARCHAR[]``)
    are converted from list to tuple.
    """
    from lynchpin.analysis.frontier.pr_review_topology import PrReviewRow
    clauses: list[str] = []
    params: list[Any] = []
    _add_in_filter('project', projects, clauses, params)
    _add_in_filter('state', states, clauses, params)
    if only_with_friction:
        clauses.append('len(friction_signals) > 0')
    if refresh_id is not None:
        clauses.append('refresh_id = ?')
        params.append(refresh_id)
    where = _build_where(clauses, params)
    sql = f'\n        SELECT\n            project, number, title, state, url, author,\n            created_at, closed_at, merged_at,\n            review_count, review_decisions, review_round_count,\n            reviewer_count, reviewers, review_comment_count,\n            top_level_comment_count, changes_requested_count,\n            approval_count, dismissed_count,\n            time_to_first_review_minutes, time_to_close_minutes,\n            time_to_merge_minutes, final_decision, friction_signals\n        FROM pr_review_row\n        {where}\n        ORDER BY project, number\n    '
    rows = conn.execute(sql, params).fetchall()

    def _iso(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt is not None else None
    results: list[Any] = []
    for project, number, title, state, url, author, created_at, closed_at, merged_at, review_count, review_decisions, review_round_count, reviewer_count, reviewers, review_comment_count, top_level_comment_count, changes_requested_count, approval_count, dismissed_count, time_to_first_review_minutes, time_to_close_minutes, time_to_merge_minutes, final_decision, friction_signals in rows:
        results.append(PrReviewRow(project=project, number=number, title=title or '', state=state or '', url=url, author=author, created_at=_iso(created_at), closed_at=_iso(closed_at), merged_at=_iso(merged_at), review_count=review_count, review_decisions=tuple(review_decisions) if review_decisions else (), review_round_count=review_round_count, reviewer_count=reviewer_count, reviewers=tuple(reviewers) if reviewers else (), review_comment_count=review_comment_count, top_level_comment_count=top_level_comment_count, changes_requested_count=changes_requested_count, approval_count=approval_count, dismissed_count=dismissed_count, time_to_first_review_minutes=time_to_first_review_minutes, time_to_close_minutes=time_to_close_minutes, time_to_merge_minutes=time_to_merge_minutes, final_decision=final_decision or '', friction_signals=tuple(friction_signals) if friction_signals else ()))
    return results

def _hydrate_provenance(prov: Any) -> 'Any | None':
    """Convert a DuckDB STRUCT dict to EvidenceProvenance, or None if all nulls."""
    from lynchpin.graph.evidence import EvidenceProvenance
    if prov is None:
        return None
    if not isinstance(prov, dict):
        return None
    if not any((v is not None for v in prov.values())):
        return None
    return EvidenceProvenance(source=prov.get('source') or '', cost=prov.get('cost') or 'local-fast', path=prov.get('path'), generated_at=prov.get('generated_at'), note=prov.get('note'))

def _hydrate_caveats(raw: Any) -> 'tuple[Any, ...]':
    """Convert a JSON column (list[dict] or str) to tuple[EvidenceCaveat, ...]."""
    import json as _json
    from lynchpin.graph.evidence import EvidenceCaveat
    if raw is None:
        return ()
    if isinstance(raw, str):
        raw = _json.loads(raw)
    if not isinstance(raw, list):
        return ()
    out = []
    for item in raw:
        if isinstance(item, dict):
            out.append(EvidenceCaveat(source=item.get('source') or '', status=item.get('status') or 'available', message=item.get('message') or ''))
    return tuple(out)

def _hydrate_payload(raw: Any) -> 'dict[str, Any] | None':
    """Return a dict from a JSON column (DuckDB may return dict or str)."""
    import json as _json
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        return _json.loads(raw)
    return None

def list_evidence_graph_builds(conn: 'duckdb.DuckDBPyConnection', *, start: date | None=None, end: date | None=None, mode: str | None=None) -> list[dict[str, Any]]:
    """List metadata about stored builds without hydrating nodes/edges."""
    clauses: list[str] = []
    params: list[Any] = []
    if start is not None:
        clauses.append('start_date = ?')
        params.append(start)
    if end is not None:
        clauses.append('end_date = ?')
        params.append(end)
    if mode is not None:
        clauses.append('mode = ?')
        params.append(mode)
    where = _build_where(clauses, params)
    sql = f'\n        SELECT refresh_id, start_date, end_date, mode, projects,\n               node_count, edge_count, caveats, generated_at, materialized_at\n        FROM evidence_graph_build\n        {where}\n        ORDER BY generated_at DESC\n    '
    rows = conn.execute(sql, params).fetchall()
    return [{'refresh_id': refresh_id, 'start_date': start_date, 'end_date': end_date, 'mode': mode_val, 'projects': projects, 'node_count': node_count, 'edge_count': edge_count, 'caveats': caveats, 'generated_at': generated_at, 'materialized_at': materialized_at} for refresh_id, start_date, end_date, mode_val, projects, node_count, edge_count, caveats, generated_at, materialized_at in rows]

def _format_evidence(prefix: str, items: list[str]) -> str:
    """Format the evidence string using the same truncation logic as the Python builders.

    ``prefix`` is either ``'shared paths'`` or ``'shared symbols'``.
    Items should already be sorted before being passed in.
    """
    preview = ', '.join(items[:3])
    suffix = f' (+{len(items) - 3})' if len(items) > 3 else ''
    return f'{prefix}: {preview}{suffix}'

def compute_file_overlap_edges(conn: 'duckdb.DuckDBPyConnection', *, we_refresh_id: str | None=None, commit_refresh_id: str | None=None) -> 'tuple[Any, ...]':
    """Compute file_overlap edges via SQL view; return same shape as
    the ``work_event_file_overlap`` SQL view produces.

    Calls ``ensure_views`` first (idempotent CREATE OR REPLACE).  Each
    returned ``EvidenceEdge`` has weight 0.85 and an evidence string of the
    form ``'shared paths: a, b, c'`` or ``'shared paths: a, b, c (+N)'``,
    exactly matching the Python builder.

    ``shared_paths`` from DuckDB ``list_intersect`` is returned as a Python
    list; we sort in Python to guarantee deterministic evidence strings
    (list_intersect does not guarantee order).
    """
    from lynchpin.graph.evidence_graph import EvidenceEdge
    from lynchpin.substrate.views import ensure_views
    ensure_views(conn)
    clauses: list[str] = ['overlap_count > 0']
    params: list[Any] = []
    if we_refresh_id is not None:
        clauses.append('we_refresh_id = ?')
        params.append(we_refresh_id)
    if commit_refresh_id is not None:
        clauses.append('commit_refresh_id = ?')
        params.append(commit_refresh_id)
    where = 'WHERE ' + ' AND '.join(clauses)
    sql = f'\n        SELECT source_id, target_id, shared_paths\n        FROM work_event_file_overlap\n        {where}\n    '
    rows = conn.execute(sql, params).fetchall()
    edges: list[Any] = []
    for source_id, target_id, shared_paths in rows:
        shared = sorted((p for p in shared_paths or [] if p))
        if not shared:
            continue
        evidence = _format_evidence('shared paths', shared)
        edges.append(EvidenceEdge(source_id, target_id, 'file_overlap', evidence, weight=0.85))
    return tuple(edges)

def compute_symbol_overlap_edges(conn: 'duckdb.DuckDBPyConnection', *, we_refresh_id: str | None=None, commit_refresh_id: str | None=None) -> 'tuple[Any, ...]':
    """Compute symbol_overlap edges via SQL view; return same shape as
    the ``work_event_symbol_overlap`` SQL view produces.

    Calls ``ensure_views`` first (idempotent CREATE OR REPLACE).  Each
    returned ``EvidenceEdge`` has weight 0.95 and an evidence string of the
    form ``'shared symbols: a, b, c'`` or ``'shared symbols: a, b, c (+N)'``,
    exactly matching the Python builder.

    ``shared_symbols`` from ``ARRAY_AGG(DISTINCT ...)`` is a Python list with
    non-deterministic order; we sort in Python before formatting.
    """
    from lynchpin.graph.evidence_graph import EvidenceEdge
    from lynchpin.substrate.views import ensure_views
    ensure_views(conn)
    clauses: list[str] = ['symbol_count > 0']
    params: list[Any] = []
    if we_refresh_id is not None:
        clauses.append('we_refresh_id = ?')
        params.append(we_refresh_id)
    if commit_refresh_id is not None:
        clauses.append('commit_refresh_id = ?')
        params.append(commit_refresh_id)
    where = 'WHERE ' + ' AND '.join(clauses)
    sql = f'\n        SELECT source_id, target_id, shared_symbols\n        FROM work_event_symbol_overlap\n        {where}\n    '
    rows = conn.execute(sql, params).fetchall()
    edges: list[Any] = []
    for source_id, target_id, shared_symbols in rows:
        symbol_names = sorted((s for s in shared_symbols or [] if s))
        if not symbol_names:
            continue
        evidence = _format_evidence('shared symbols', symbol_names)
        edges.append(EvidenceEdge(source_id, target_id, 'symbol_overlap', evidence, weight=0.95))
    return tuple(edges)

def load_evidence_graph(conn: 'duckdb.DuckDBPyConnection', *, refresh_id: str | None=None, start: date | None=None, end: date | None=None, mode: str | None=None, projects: tuple[str, ...] | None=None) -> 'Any | None':
    """Hydrate a previously-promoted EvidenceGraph from the substrate.

    Selection rules:
    - If refresh_id is given, return that exact build (or None if absent).
    - Otherwise pick the most recent build matching (start, end, mode);
      projects filter requires the stored projects array to contain ALL
      requested projects, or empty stored projects (= all).
    - Returns None when no matching build exists.

    Column-shape notes:
    - ``payload`` JSON column: DuckDB returns dict directly when the column
      type is JSON and the value is a JSON object.  We fall back to
      ``json.loads`` if a str arrives (older serialisation path).
    - ``provenance`` STRUCT: DuckDB returns a plain dict with the five keys
      (source, cost, path, generated_at, note); any may be None.  We build
      EvidenceProvenance only when at least one field is non-null.
    - ``caveats`` JSON: DuckDB returns a list of dicts or a JSON string;
      we normalise both paths.
    """
    from lynchpin.graph.evidence_graph import EvidenceEdge, EvidenceGraph, EvidenceNode
    if refresh_id is not None:
        build_rows = conn.execute('SELECT refresh_id, start_date, end_date, mode, generated_at, caveats FROM evidence_graph_build WHERE refresh_id = ?', [refresh_id]).fetchall()
    else:
        b_clauses: list[str] = []
        b_params: list[Any] = []
        if start is not None:
            b_clauses.append('start_date = ?')
            b_params.append(start)
        if end is not None:
            b_clauses.append('end_date = ?')
            b_params.append(end)
        if mode is not None:
            b_clauses.append('mode = ?')
            b_params.append(mode)
        if projects:
            b_clauses.append('(len(projects) = 0 OR list_has_all(projects, ?))')
            b_params.append(list(projects))
        b_where = _build_where(b_clauses, b_params)
        build_rows = conn.execute(f'SELECT refresh_id, start_date, end_date, mode, generated_at, caveats FROM evidence_graph_build {b_where} ORDER BY generated_at DESC LIMIT 1', b_params).fetchall()
    if not build_rows:
        return None
    rid, start_date, end_date, build_mode, generated_at, build_caveats = build_rows[0]
    node_rows = conn.execute('\n        SELECT id, kind, source, date, project, summary,\n               start_ts, end_ts, url, payload, provenance, caveats\n        FROM evidence_node\n        WHERE refresh_id = ?\n        ', [rid]).fetchall()
    nodes: list[EvidenceNode] = []
    for n_id, n_kind, n_source, n_date, n_project, n_summary, n_start, n_end, n_url, n_payload, n_prov, n_caveats in node_rows:
        nodes.append(EvidenceNode(id=n_id, kind=n_kind, source=n_source, date=n_date, project=n_project, summary=n_summary or '', start=n_start, end=n_end, url=n_url, payload=_hydrate_payload(n_payload), provenance=_hydrate_provenance(n_prov), caveats=_hydrate_caveats(n_caveats)))
    edge_rows = conn.execute('\n        SELECT source_id, target_id, relation, evidence, weight\n        FROM evidence_edge\n        WHERE refresh_id = ?\n        ', [rid]).fetchall()
    edges: list[EvidenceEdge] = []
    for e_source_id, e_target_id, e_relation, e_evidence, e_weight in edge_rows:
        edges.append(EvidenceEdge(source_id=e_source_id, target_id=e_target_id, relation=e_relation, evidence=e_evidence or '', weight=e_weight if e_weight is not None else 1.0))
    return EvidenceGraph(start=start_date, end=end_date, generated_at=generated_at, mode=build_mode, nodes=tuple(nodes), edges=tuple(edges), caveats=_hydrate_caveats(build_caveats))

@dataclass(frozen=True)
class ProjectDayCorrelationRow:
    project: str
    date: date
    refresh_id: str
    commit_count: int
    ai_session_count: int
    ai_work_event_count: int
    github_item_count: int
    focus_count: int
    terminal_count: int
    raw_log_count: int
    commit_shas: tuple[str, ...]
    conversation_ids: tuple[str, ...]
    github_node_ids: tuple[str, ...]
    focus_minutes: float
    shell_minutes: float
    source_count: int

def load_project_day_correlations(conn: 'duckdb.DuckDBPyConnection', *, refresh_id: str | None=None, start: date | None=None, end: date | None=None, projects: tuple[str, ...] | None=None, min_source_count: int | None=None) -> list[ProjectDayCorrelationRow]:
    """Read project_day_correlation rows. Filters compose with AND.

    Calls ``ensure_views`` first (idempotent CREATE OR REPLACE).

    ``min_source_count=2`` surfaces only project-days with cross-source support.
    ``focus_seconds`` and ``shell_seconds`` from the view are divided by 60
    to produce ``focus_minutes`` / ``shell_minutes`` on the returned dataclass.
    DuckDB ARRAY_AGG results (Python list) are converted to tuple; NULL arrays
    become empty tuples.
    """
    from lynchpin.substrate.views import ensure_views
    ensure_views(conn)
    clauses: list[str] = []
    params: list[Any] = []
    if refresh_id is not None:
        clauses.append('refresh_id = ?')
        params.append(refresh_id)
    if start is not None and end is not None:
        clauses.append('date BETWEEN ? AND ?')
        params.extend([start, end])
    elif start is not None:
        clauses.append('date >= ?')
        params.append(start)
    elif end is not None:
        clauses.append('date <= ?')
        params.append(end)
    _add_in_filter('project', projects, clauses, params)
    if min_source_count is not None:
        clauses.append('source_count >= ?')
        params.append(min_source_count)
    where = _build_where(clauses, params)
    rows = conn.execute(sql, params).fetchall()
    results: list[ProjectDayCorrelationRow] = []
    return results

@dataclass(frozen=True)
class IssueClosureChainWalkRow:
    refresh_id: str
    root_id: str
    project: str
    issue_number: str | None
    reachable_node_ids: tuple[str, ...]
    chain_depth: int
    reachable_count: int

def load_issue_closure_chain_walks(conn: 'duckdb.DuckDBPyConnection', *, refresh_id: str | None=None, project: str | None=None, min_chain_depth: int | None=None) -> list[IssueClosureChainWalkRow]:
    """Read issue_closure_chain_walk rows from the recursive CTE.

    Calls ``ensure_views`` first (idempotent CREATE OR REPLACE).

    Surfaces the structural shape of closure chains (which nodes are reachable
    from which issue) for downstream classification by
    ``lynchpin/graph/issue_closure_chain.py``.  The Python layer still owns
    status classification (complete/partial/broken/orphaned).

    ``issue_number`` is returned as VARCHAR (the view coalesces the JSON field to
    a string regardless of whether it was stored as integer or string in the
    payload).  The reachable_node_ids DuckDB list is converted to tuple; NULL
    arrays become empty tuples.
    """
    from lynchpin.substrate.views import ensure_views
    ensure_views(conn)
    clauses: list[str] = []
    params: list[Any] = []
    if refresh_id is not None:
        clauses.append('refresh_id = ?')
        params.append(refresh_id)
    if project is not None:
        clauses.append('project = ?')
        params.append(project)
    if min_chain_depth is not None:
        clauses.append('chain_depth >= ?')
        params.append(min_chain_depth)
    where = _build_where(clauses, params)
    sql = f'\n        SELECT\n            refresh_id, root_id, project, issue_number,\n            reachable_node_ids, chain_depth, reachable_count\n        FROM issue_closure_chain_walk\n        {where}\n        ORDER BY project, root_id\n    '
    rows = conn.execute(sql, params).fetchall()
    results: list[IssueClosureChainWalkRow] = []
    for rid, root_id, proj, issue_number, reachable_node_ids, chain_depth, reachable_count in rows:
        results.append(IssueClosureChainWalkRow(refresh_id=rid, root_id=root_id, project=proj, issue_number=issue_number, reachable_node_ids=tuple(reachable_node_ids or []), chain_depth=chain_depth or 0, reachable_count=reachable_count or 0))
    return results
