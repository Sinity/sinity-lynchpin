"""Range-scoped evidence graph for current-state and narrative analysis."""
from __future__ import annotations
import logging
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Sequence
log = logging.getLogger(__name__)
from ..core.parse import as_local, parse_datetime
from ..core.project_mentions import projects_mentioned_in_text
from ..core.primitives import date_to_dt_range, logical_date
from ..core.projects import canonical_project_name
from ..sources.analysis_artifacts import analysis_claims, latest_artifacts
from ..sources.github import GitHubActor, GitHubComment, GitHubItem, GitHubItemKind, GitHubItemState, GitHubLabel, classify_lifecycle, extract_commit_refs
from .evidence import CostClass, EvidenceCaveat, EvidenceProvenance
from .source_readiness import source_readiness
EvidenceNodeKind = Literal['commit', 'github_issue', 'github_pr', 'github_ref', 'ai_session', 'ai_work_event', 'raw_log', 'focus_day', 'focus_span', 'deep_work_block', 'circadian_profile', 'focus_loop', 'fragmentation_day', 'attention_day', 'terminal_session', 'terminal_pattern', 'web_domain_day', 'sleep_quality', 'health_metric', 'temporal_changepoint', 'temporal_trend', 'temporal_anomaly', 'temporal_rhythm', 'readiness_forecast', 'analysis_artifact', 'analysis_claim']
EvidenceRelation = Literal['references', 'same_project_day', 'temporal_overlap', 'temporal_proximity', 'mentions_project', 'file_overlap', 'tool_overlap', 'symbol_overlap']

@dataclass(frozen=True)
class EvidenceNode:
    id: str
    kind: EvidenceNodeKind
    source: str
    date: date
    project: str | None
    summary: str
    start: datetime | None = None
    end: datetime | None = None
    url: str | None = None
    payload: dict[str, Any] | None = None
    provenance: EvidenceProvenance | None = None
    caveats: tuple[EvidenceCaveat, ...] = ()

@dataclass(frozen=True)
class EvidenceEdge:
    source_id: str
    target_id: str
    relation: EvidenceRelation
    evidence: str
    weight: float = 1.0

@dataclass(frozen=True)
class EvidenceTimelineEntry:
    node_id: str
    date: date
    when: datetime | None
    project: str | None
    source: str
    kind: EvidenceNodeKind
    summary: str

@dataclass(frozen=True)
class EvidenceRelationEntry:
    source_node_id: str
    target_node_id: str
    source_source: str
    target_source: str
    relation: EvidenceRelation
    evidence: str
    weight: float
    date: date
    project: str | None
    source_summary: str
    target_summary: str

@dataclass(frozen=True)
class EvidenceGraph:
    start: date
    end: date
    generated_at: datetime
    mode: CostClass
    nodes: tuple[EvidenceNode, ...]
    edges: tuple[EvidenceEdge, ...]
    caveats: tuple[EvidenceCaveat, ...]

    def nodes_by_project_day(self) -> dict[tuple[date, str], tuple[EvidenceNode, ...]]:
        grouped: dict[tuple[date, str], list[EvidenceNode]] = defaultdict(list)
        for node in self.nodes:
            if node.project:
                grouped[node.date, node.project].append(node)
        return {key: tuple(value) for key, value in grouped.items()}

    def node_map(self) -> dict[str, EvidenceNode]:
        return {node.id: node for node in self.nodes}

@dataclass
class RefreshContext:
    """Per-refresh memoization for graph construction.

    Holds a cache of base evidence graphs keyed by ``(start, end, mode,
    projects)`` so that ``project_velocity_windows`` and
    ``current_state_context`` can share work without going through a
    global cache. Opt-in: callers must thread the same ``RefreshContext``
    through both consumers; otherwise behavior is identical to the
    pre-7E path.
    """
    _cache: dict[tuple[date, date, str, tuple[str, ...]], 'EvidenceGraph'] = None

    def __post_init__(self) -> None:
        if self._cache is None:
            object.__setattr__(self, '_cache', {})

    def base_graph(self, *, start: date, end: date, projects: Sequence[str] | None=None, mode: CostClass='local-fast') -> 'EvidenceGraph':
        key = (start, end, mode, tuple(sorted(projects)) if projects else ())
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        graph = build_base_evidence_graph(start=start, end=end, projects=projects, mode=mode)
        self._cache[key] = graph
        return graph

def build_base_evidence_graph(*, start: date, end: date, projects: Sequence[str] | None=None, mode: CostClass='local-fast') -> EvidenceGraph:
    """Build the base evidence graph: every source except generated analysis
    artifacts and claims.

    Used by callers — like ``project_velocity_windows._correlation_rows`` —
    that must not see the analysis overlay they are about to write.
    """
    selected = _selected_projects(projects)
    nodes: list[EvidenceNode] = []
    edges: list[EvidenceEdge] = []
    now = datetime.now().astimezone()
    _add_git(nodes, edges, start=start, end=end, selected=selected, mode=mode)
    _add_polylogue(nodes, start=start, end=end, selected=selected)
    _add_polylogue_work_events(nodes, start=start, end=end, selected=selected, mode=mode)
    _add_raw_log(nodes, start=start, end=end, selected=selected)
    _add_focus(nodes, start=start, end=end, selected=selected, mode=mode)
    _add_terminal(nodes, start=start, end=end, selected=selected)
    _add_web(nodes, start=start, end=end, selected=selected)
    _add_health(nodes, start=start, end=end)
    _add_temporal_signals(nodes, start=start, end=end)
    _add_readiness(nodes, end=end)
    return _finalize_graph(nodes=nodes, edges=edges, start=start, end=end, mode=mode, generated_at=now)

def build_evidence_graph(*, start: date, end: date, projects: Sequence[str] | None=None, mode: CostClass='local-fast', exclude_analysis_artifacts: Sequence[str]=(), refresh_context: RefreshContext | None=None) -> EvidenceGraph:
    """Build a local evidence graph for a date range.

    If ``refresh_context`` is supplied, the base layer is reused from the
    context's cache; otherwise the base is built fresh.
    """
    selected = _selected_projects(projects)
    if refresh_context is not None:
        base = refresh_context.base_graph(start=start, end=end, projects=projects, mode=mode)
        nodes = list(base.nodes)
        edges = list(base.edges)
    else:
        nodes = []
        edges = []
        _add_git(nodes, edges, start=start, end=end, selected=selected, mode=mode)
        _add_polylogue(nodes, start=start, end=end, selected=selected)
        _add_polylogue_work_events(nodes, start=start, end=end, selected=selected, mode=mode)
        _add_raw_log(nodes, start=start, end=end, selected=selected)
        _add_focus(nodes, start=start, end=end, selected=selected, mode=mode)
        _add_terminal(nodes, start=start, end=end, selected=selected)
        _add_web(nodes, start=start, end=end, selected=selected)
        _add_health(nodes, start=start, end=end)
        _add_temporal_signals(nodes, start=start, end=end)
        _add_readiness(nodes, end=end)
    now = datetime.now().astimezone()
    _add_analysis_artifacts(nodes, edges, end=end, selected=selected, exclude_names=frozenset(exclude_analysis_artifacts))
    _add_analysis_claims(nodes, edges, end=end, selected=selected, exclude_names=frozenset(exclude_analysis_artifacts))
    return _finalize_graph(nodes=nodes, edges=edges, start=start, end=end, mode=mode, generated_at=now)

def _finalize_graph(*, nodes: list[EvidenceNode], edges: list[EvidenceEdge], start: date, end: date, mode: CostClass, generated_at: datetime) -> EvidenceGraph:
    node_ids = {node.id for node in nodes}
    edges.extend((edge for edge in _same_project_day_edges(nodes) if edge.source_id in node_ids and edge.target_id in node_ids))
    edges.extend((edge for edge in _temporal_overlap_edges(nodes) if edge.source_id in node_ids and edge.target_id in node_ids))
    edges.extend((edge for edge in _temporal_proximity_edges(nodes) if edge.source_id in node_ids and edge.target_id in node_ids))
    if os.environ.get('LYNCHPIN_SUBSTRATE_OVERLAP') == '0':
        edges.extend((edge for edge in _polylogue_work_event_file_overlap_edges(nodes) if edge.source_id in node_ids and edge.target_id in node_ids))
        edges.extend((edge for edge in _polylogue_work_event_symbol_overlap_edges(nodes) if edge.source_id in node_ids and edge.target_id in node_ids))
    else:
        overlap_refresh_id = f'overlap:{generated_at.isoformat()}'
        try:
            sql_edges = _overlap_edges_via_substrate(nodes, refresh_id=overlap_refresh_id)
            edges.extend((edge for edge in sql_edges if edge.source_id in node_ids and edge.target_id in node_ids))
        except Exception as exc:
            log.warning('substrate overlap path failed; falling back to Python: %s', exc)
            edges.extend((edge for edge in _polylogue_work_event_file_overlap_edges(nodes) if edge.source_id in node_ids and edge.target_id in node_ids))
            edges.extend((edge for edge in _polylogue_work_event_symbol_overlap_edges(nodes) if edge.source_id in node_ids and edge.target_id in node_ids))
    edges.extend((edge for edge in _polylogue_work_event_tool_overlap_edges(nodes) if edge.source_id in node_ids and edge.target_id in node_ids))
    readiness = source_readiness(start=start, end=end, include_heavy_counts=mode != 'local-fast', include_github_frontier=mode == 'network')
    caveats = tuple(readiness.caveats)
    if mode == 'local-fast':
        caveats += (EvidenceCaveat('evidence_graph', 'partial', 'local-fast graph uses daily focus aggregates and commit-referenced GitHub refs only.'),)
    deduped_nodes = _dedupe_nodes(nodes)
    node_ids = {node.id for node in deduped_nodes}
    deduped_edges = tuple((edge for edge in _dedupe_edges(edges) if edge.source_id in node_ids and edge.target_id in node_ids))
    graph = EvidenceGraph(start=start, end=end, generated_at=generated_at, mode=mode, nodes=tuple(sorted(deduped_nodes, key=lambda node: (node.date, node.project or '', node.source, node.id))), edges=deduped_edges, caveats=caveats)
    _promote_to_substrate(graph)
    return graph

def _promote_to_substrate(graph: 'EvidenceGraph') -> None:
    """Best-effort write of graph to DuckDB substrate. Errors logged, not raised."""
    try:
        from lynchpin.duck import connect, apply_schema
        from lynchpin.duck.promote import promote_evidence_graph
    except ImportError as exc:
        log.warning('DuckDB substrate unavailable: %s', exc)
        return
    refresh_id = f'graph:{graph.start.isoformat()}:{graph.end.isoformat()}:{graph.mode}:{graph.generated_at.isoformat()}'
    try:
        with connect() as conn:
            apply_schema(conn)
            counts = promote_evidence_graph(conn, refresh_id=refresh_id, graph=graph)
            log.info('Promoted evidence graph to substrate: %s (refresh_id=%s)', counts, refresh_id)
    except Exception as exc:
        log.warning('Failed to promote evidence graph to substrate: %s', exc)

def render_evidence_graph_summary(graph: EvidenceGraph) -> str:
    """Render compact graph coverage for prompt-facing reports."""
    by_kind: dict[str, int] = defaultdict(int)
    by_source: dict[str, int] = defaultdict(int)
    by_relation: dict[str, int] = defaultdict(int)
    for node in graph.nodes:
        by_kind[node.kind] += 1
        by_source[node.source] += 1
    for edge in graph.edges:
        by_relation[edge.relation] += 1
    return '\n'.join([f'- Nodes: {len(graph.nodes)} ({_format_counts(by_kind)})', f'- Sources: {_format_counts(by_source)}', f'- Edges: {len(graph.edges)} ({_format_counts(by_relation)})', f"- Projects: {', '.join(sorted({node.project for node in graph.nodes if node.project})) or '(none)'}"])

def evidence_timeline(graph: EvidenceGraph, *, limit: int=32, projects: Sequence[str] | None=None, include_analysis_artifacts: bool=False) -> tuple[EvidenceTimelineEntry, ...]:
    """Project the graph into chronological evidence rows.

    This is a view over first-class evidence nodes. It intentionally avoids a
    separate timeline model so prompts can inspect temporal order without
    losing node ids, source names, or caveats carried by the graph.
    """
    selected = _selected_projects(projects)
    entries = []
    for node in graph.nodes:
        if node.kind == 'analysis_artifact' and (not include_analysis_artifacts):
            continue
        if not _include_project(node.project, selected):
            continue
        entries.append(EvidenceTimelineEntry(node_id=node.id, date=node.date, when=node.start, project=node.project, source=node.source, kind=node.kind, summary=node.summary))
    return tuple(sorted(entries, key=_timeline_entry_key)[:max(0, limit)])

def render_evidence_timeline(graph: EvidenceGraph, *, limit: int=32, projects: Sequence[str] | None=None, include_analysis_artifacts: bool=False) -> str:
    """Render chronological graph evidence as a compact Markdown table."""
    rows = evidence_timeline(graph, limit=limit, projects=projects, include_analysis_artifacts=include_analysis_artifacts)
    lines = ['| When | Project | Source | Kind | Evidence |', '| --- | --- | --- | --- | --- |']
    if not rows:
        lines.append('| _none_ | _none_ | _none_ | _none_ | _No chronological evidence matched._ |')
        return '\n'.join(lines)
    for row in rows:
        cells = (_markdown_cell(_format_timeline_when(row)), _markdown_cell(row.project or 'unattributed'), _markdown_cell(row.source), _markdown_cell(row.kind), _markdown_cell(row.summary))
        lines.append(f"| {' | '.join(cells)} |")
    return '\n'.join(lines)

def evidence_relations(graph: EvidenceGraph, *, limit: int=16, projects: Sequence[str] | None=None, relation_types: Sequence[EvidenceRelation]=('references', 'temporal_overlap', 'temporal_proximity')) -> tuple[EvidenceRelationEntry, ...]:
    """Project graph edges into compact prompt-facing relationship rows."""
    selected = _selected_projects(projects)
    wanted = set(relation_types)
    nodes = graph.node_map()
    rows = []
    for edge in graph.edges:
        if wanted and edge.relation not in wanted:
            continue
        source = nodes.get(edge.source_id)
        target = nodes.get(edge.target_id)
        if source is None or target is None:
            continue
        project = source.project if source.project == target.project else source.project or target.project
        if not _include_project(project, selected):
            continue
        rows.append(EvidenceRelationEntry(source_node_id=edge.source_id, target_node_id=edge.target_id, source_source=source.source, target_source=target.source, relation=edge.relation, evidence=edge.evidence, weight=edge.weight, date=min(source.date, target.date), project=project, source_summary=source.summary, target_summary=target.summary))
    return tuple(sorted(rows, key=_relation_entry_key)[:max(0, limit)])

def render_evidence_relations(graph: EvidenceGraph, *, limit: int=16, projects: Sequence[str] | None=None, relation_types: Sequence[EvidenceRelation]=('references', 'temporal_overlap', 'temporal_proximity')) -> str:
    """Render important graph relationships as a compact Markdown table."""
    rows = evidence_relations(graph, limit=limit, projects=projects, relation_types=relation_types)
    lines = ['| Date | Project | Relation | Evidence | Source | Target |', '| --- | --- | --- | --- | --- | --- |']
    if not rows:
        lines.append('| _none_ | _none_ | _none_ | _none_ | _none_ | _none_ |')
        return '\n'.join(lines)
    for row in rows:
        cells = (row.date.isoformat(), row.project or 'unattributed', row.relation, row.evidence, row.source_summary, row.target_summary)
        lines.append(f"| {' | '.join((_markdown_cell(cell) for cell in cells))} |")
    return '\n'.join(lines)

def _add_git(nodes: list[EvidenceNode], edges: list[EvidenceEdge], *, start: date, end: date, selected: set[str], mode: CostClass) -> None:
    from ..sources.git import commit_facts, github_context_for_commits
    facts = tuple(commit_facts(start=start, end=end + timedelta(days=1), include_paths=mode != 'local-fast'))
    selected_facts = []
    for fact in facts:
        project = canonical_project_name(fact.repo)
        if project is None:
            continue
        if not _include_project(project, selected):
            continue
        selected_facts.append(fact)
        day = logical_date(fact.authored_at)
        node_id = f'git:{project}:{fact.commit}'
        refs = extract_commit_refs(fact.subject)
        nodes.append(EvidenceNode(id=node_id, kind='commit', source='git', date=day, project=project, start=fact.authored_at, end=fact.authored_at, summary=fact.subject, payload={'commit': fact.commit, 'author': fact.author, 'lines_added': fact.lines_added, 'lines_deleted': fact.lines_deleted, 'lines_changed': fact.lines_changed, 'files_changed': fact.files_changed, 'paths': fact.paths, 'github_refs': {'prs': sorted(refs['prs']), 'issues': sorted(refs['issues'])}}, provenance=EvidenceProvenance('git', mode)))
        for kind, numbers in (('pr', refs['prs']), ('issue', refs['issues'])):
            for number in sorted(numbers):
                ref_id = _github_ref_id(project, kind, number)
                nodes.append(_github_ref_node(project=project, kind=kind, number=number, day=day))
                edges.append(EvidenceEdge(node_id, ref_id, 'references', f'commit subject references {kind} #{number}', 0.9))
    if mode != 'network':
        return
    context = github_context_for_commits(selected_facts)
    raw_items = context.get('items', ()) if isinstance(context, dict) else ()
    for item in _dict_items(raw_items):
        gh_item = _github_item_from_dict(item)
        if gh_item is None:
            continue
        nodes.append(_github_item_node(gh_item))

def _add_polylogue(nodes: list[EvidenceNode], *, start: date, end: date, selected: set[str]) -> None:
    from ..sources.polylogue import session_profiles_for_date
    for session in session_profiles_for_date(start=start, end=end + timedelta(days=1)):
        session_date = session.canonical_session_date
        if session_date is None:
            stamp = session.first_message_at or session.last_message_at
            if stamp is None:
                continue
            session_date = logical_date(stamp)
        projects = tuple((project for project in (_normalize_project(p) for p in session.work_event_projects) if project))
        if not projects:
            projects = _projects_from_text(session.title)
        for project in projects or (None,):
            if project is not None and (not _include_project(project, selected)):
                continue
            if project is None and selected:
                continue
            nodes.append(EvidenceNode(id=f"polylogue:{session.conversation_id}:{project or 'unattributed'}", kind='ai_session', source='polylogue', date=session_date, project=project, start=session.first_message_at, end=session.last_message_at, summary=session.title or f'{session.provider} session', payload={'conversation_id': session.conversation_id, 'provider': session.provider, 'message_count': session.message_count, 'word_count': session.word_count, 'engaged_duration_ms': session.engaged_duration_ms, 'tool_use_count': session.tool_use_count, 'work_event_kind': session.work_event_kind}, provenance=EvidenceProvenance('polylogue', 'local-fast'), caveats=(EvidenceCaveat('polylogue', 'partial', 'Session node may be derived from base archive tables when product rows are empty.'),)))

def _add_polylogue_work_events(nodes: list[EvidenceNode], *, start: date, end: date, selected: set[str], mode: CostClass) -> None:
    """Promote Polylogue's ``session_work_events`` rows into per-event nodes.

    Each work event becomes one node per project it touches; project attribution
    is resolved from ``file_paths`` first (most specific), then from the parent
    session's ``work_event_projects`` (built once per call), then dropped if the
    user has selected projects and we have nothing.

    ``local-fast`` filters to ``confidence >= 0.6`` to keep noisy heuristic
    events out of cheap context packs; ``local-heavy`` and ``network`` admit
    everything (the kind/confidence caveats stay attached either way).
    """
    from ..core.classify import resolve_project
    from ..sources.polylogue import session_profiles_for_date, work_events
    from .work_event_kind import overlay_label
    confidence_floor = 0.6 if mode == 'local-fast' else 0.0
    session_projects: dict[str, tuple[str, ...]] = {}
    for session in session_profiles_for_date(start=start, end=end + timedelta(days=1)):
        projects = tuple((project for project in (_normalize_project(p) for p in session.work_event_projects) if project))
        if not projects:
            projects = _projects_from_text(session.title)
        session_projects[session.conversation_id] = projects
    for event in work_events(start=start, end=end + timedelta(days=1)):
        if event.start is None:
            continue
        if event.confidence < confidence_floor:
            continue
        event_projects: list[str] = []
        for path in event.file_paths:
            project = resolve_project(path)
            if project and project not in event_projects:
                event_projects.append(project)
        if not event_projects:
            for project in session_projects.get(event.conversation_id, ()):
                if project and project not in event_projects:
                    event_projects.append(project)
        event_date = logical_date(event.start)
        label = overlay_label(polylogue_kind=event.kind, polylogue_confidence=float(event.confidence or 0.0), file_paths=event.file_paths, tools_used=event.tools_used, duration_ms=int(event.duration_ms or 0))
        target_projects: list[str | None] = list(event_projects) if event_projects else [None]
        for project in target_projects:
            if project is not None and (not _include_project(project, selected)):
                continue
            if project is None and selected:
                continue
            parent_session_id = f"polylogue:{event.conversation_id}:{project or 'unattributed'}"
            summary = (event.summary or f'{event.kind} ({event.provider})')[:240]
            event_caveats: list[EvidenceCaveat] = [EvidenceCaveat('polylogue', 'partial', 'Work-event boundaries and kind labels are heuristic; see Lynchpin re-classifier overlay (Arc K).')]
            if label.source == 'disagreement':
                event_caveats.append(EvidenceCaveat('lynchpin_overlay', 'partial', f"Polylogue says '{label.polylogue_kind}', overlay says '{label.overlay_kind}' — using overlay (stronger features)."))
            nodes.append(EvidenceNode(id=f"polylogue:we:{event.event_id}:{project or 'unattributed'}", kind='ai_work_event', source='polylogue', date=event_date, project=project, start=event.start, end=event.end, summary=summary, payload={'event_id': event.event_id, 'conversation_id': event.conversation_id, 'provider': event.provider, 'kind': label.kind, 'kind_confidence': label.confidence, 'kind_source': label.source, 'kind_tier': label.tier, 'polylogue_kind': label.polylogue_kind, 'polylogue_confidence': label.polylogue_confidence, 'overlay_kind': label.overlay_kind, 'overlay_confidence': label.overlay_confidence, 'duration_ms': event.duration_ms, 'file_paths': list(event.file_paths), 'tools_used': list(event.tools_used), 'parent_session_id': parent_session_id}, provenance=EvidenceProvenance('polylogue', mode), caveats=tuple(event_caveats)))

def _add_raw_log(nodes: list[EvidenceNode], *, start: date, end: date, selected: set[str]) -> None:
    from ..sources.raw_log import entries_in_range
    for entry in entries_in_range(start=start, end=end):
        for project in _projects_from_text(entry.text):
            if not _include_project(project, selected):
                continue
            nodes.append(EvidenceNode(id=f'raw-log:{entry.source_path}:{entry.line_no}:{project}', kind='raw_log', source='raw_log', date=logical_date(entry.timestamp), project=project, start=entry.timestamp, end=entry.timestamp, summary=entry.text[:240], payload={'line_no': entry.line_no, 'source_path': entry.source_path, 'text': entry.text}, provenance=EvidenceProvenance('raw_log', 'local-fast', path=entry.source_path)))

def _add_focus(nodes: list[EvidenceNode], *, start: date, end: date, selected: set[str], mode: CostClass) -> None:
    from ..sources.activitywatch import attention, circadian, deep_work, focus_timeline, fragmentation, loops, project_focus_days
    start_dt, end_dt = date_to_dt_range(start, end)
    if mode != 'local-fast':
        for idx, span in enumerate(focus_timeline(start=start_dt, end=end_dt, min_duration_s=60.0)):
            project = _normalize_project(span.project)
            if span.kind != 'focused' or not _include_project(project, selected):
                continue
            title = str(span.title or '').strip()
            app = str(span.app or '').strip()
            summary_bits = [f'{span.duration_s / 60:.0f}m focus']
            if app:
                summary_bits.append(app)
            if title:
                summary_bits.append(title[:120])
            nodes.append(EvidenceNode(id=f'aw-focus-span:{span.start.isoformat()}:{idx}:{project}', kind='focus_span', source='activitywatch', date=logical_date(span.start), project=project, start=span.start, end=span.end, summary=' — '.join(summary_bits), payload={'duration_s': span.duration_s, 'app': span.app, 'title': span.title, 'mode': span.mode, 'span_source': span.source, 'keypress_count': span.keypress_count, 'keylog_state': span.keylog_state}, provenance=EvidenceProvenance('activitywatch', 'local-heavy')))
        for idx, block in enumerate(deep_work(start=start_dt, end=end_dt)):
            project = _normalize_project(block.project)
            if block.focus_ratio < 0.5 or not _include_project(project, selected):
                continue
            nodes.append(EvidenceNode(id=f'aw-deep-work:{block.start.isoformat()}:{idx}', kind='deep_work_block', source='activitywatch', date=logical_date(block.start), project=project, start=block.start, end=block.end, summary=f'deep work {block.duration_min:.0f}m ({block.mode}, ratio={block.focus_ratio:.2f})', payload={'duration_min': round(block.duration_min, 1), 'focus_ratio': round(block.focus_ratio, 2), 'mode': block.mode, 'app_switches': block.app_switches}, provenance=EvidenceProvenance('activitywatch', 'local-heavy')))
        for idx, profile in enumerate(circadian(start=start, end=end)):
            project = _normalize_project(profile.dominant_project)
            if not _include_project(project, selected):
                continue
            nodes.append(EvidenceNode(id=f'aw-circadian:{profile.date.isoformat()}:{project}', kind='circadian_profile', source='activitywatch', date=profile.date, project=project, summary=f'circadian: peak hour={profile.hour}, dominant={profile.dominant_mode}', payload={'peak_hour': profile.hour, 'active_min': profile.active_min, 'dominant_mode': profile.dominant_mode}, provenance=EvidenceProvenance('activitywatch', 'local-heavy')))
        for idx, loop in enumerate(loops(start=start_dt, end=end_dt)):
            project = _normalize_project(loop.dominant_project)
            if loop.span_count < 2 or not _include_project(project, selected):
                continue
            nodes.append(EvidenceNode(id=f'aw-loop:{loop.date.isoformat()}:{idx}', kind='focus_loop', source='activitywatch', date=loop.date, project=project, summary=f'focus loop: {loop.switch_count} switches {loop.context_a}↔{loop.context_b}, {loop.duration_min:.0f}m', payload={'switch_count': loop.switch_count, 'span_count': loop.span_count, 'context_a': loop.context_a, 'context_b': loop.context_b, 'duration_min': round(loop.duration_min, 1)}, provenance=EvidenceProvenance('activitywatch', 'local-heavy')))
        for frag in fragmentation(start=start, end=end):
            nodes.append(EvidenceNode(id=f'aw-frag:{frag.date.isoformat()}', kind='fragmentation_day', source='activitywatch', date=frag.date, project=None, summary=f'fragmentation: {frag.total_switches} switches, avg focus={frag.avg_focus_min:.0f}m, longest={frag.longest_focus_min:.0f}m', payload={'total_switches': frag.total_switches, 'avg_focus_min': round(frag.avg_focus_min, 1), 'longest_focus_min': round(frag.longest_focus_min, 1), 'fragmentation_index': round(frag.fragmentation, 2)}, provenance=EvidenceProvenance('activitywatch', 'local-heavy')))
        for attn in attention(start=start, end=end):
            project = _normalize_project(attn.top_project)
            if not _include_project(project, selected):
                continue
            nodes.append(EvidenceNode(id=f'aw-attn:{attn.date.isoformat()}:{project}', kind='attention_day', source='activitywatch', date=attn.date, project=project, summary=f'attention: entropy={attn.entropy:.2f}, gini={attn.gini:.2f}, top={attn.top_project}', payload={'entropy': round(attn.entropy, 2), 'gini': round(attn.gini, 2), 'top_project': attn.top_project, 'project_count': attn.project_count}, provenance=EvidenceProvenance('activitywatch', 'local-heavy')))
        return
    for focus in project_focus_days(start=start_dt, end=end_dt):
        project = _normalize_project(focus.project)
        if not _include_project(project, selected):
            continue
        nodes.append(EvidenceNode(id=f'aw-focus:{focus.date}:{project}', kind='focus_day', source='activitywatch', date=focus.date, project=project, summary=f'{project} focus {focus.duration_s / 3600:.2f}h', payload={'duration_s': focus.duration_s}, provenance=EvidenceProvenance('activitywatch', 'local-fast')))

def _add_web(nodes: list[EvidenceNode], *, start: date, end: date, selected: set[str]) -> None:
    from ..sources.web import daily_browsing
    try:
        days = daily_browsing(start=start, end=end)
    except Exception:
        return
    for day in days:
        if day.visit_count == 0:
            continue
        top_domains = [(d, round(p, 3)) for d, p in day.top_domains[:5]]
        domain_names = [d for d, _ in top_domains]
        project = _domain_project(domain_names[0]) if domain_names else None
        if not _include_project(project, selected):
            project = None
        nodes.append(EvidenceNode(id=f'web:{day.date.isoformat()}', kind='web_domain_day', source='web', date=day.date, project=project, summary=f"{day.visit_count} visits, {day.unique_domains} domains, top: {', '.join(domain_names[:3])}", payload={'visit_count': day.visit_count, 'unique_domains': day.unique_domains, 'top_domains': top_domains, 'top_titles': list(day.top_titles[:3])}, provenance=EvidenceProvenance('web', 'local-fast'), caveats=(EvidenceCaveat('web', 'partial', 'Web domain data is domain-level; individual page content is not inspected.'),)))

def _domain_project(domain: str) -> str | None:
    mapping = {'github.com': None, 'gitlab.com': None, 'chatgpt.com': None, 'claude.ai': None, 'aistudio.google.com': None, 'lesswrong.com': None, 'stackoverflow.com': None, 'reddit.com': None, 'youtube.com': None, 'docs.rs': None, 'pypi.org': None, 'crates.io': None, 'nixos.org': None}
    return mapping.get(domain)

def _add_health(nodes: list[EvidenceNode], *, start: date, end: date) -> None:
    from .health_bridge import build_health_evidence, build_sleep_evidence, build_sleep_productivity_links
    for sq in build_sleep_evidence(start=start, end=end):
        nodes.append(EvidenceNode(id=sq.id, kind='sleep_quality', source='sleep', date=sq.date, project=None, summary=sq.summary, payload=sq.payload, provenance=EvidenceProvenance('sleep', 'local-heavy')))
    for hm in build_health_evidence(start=start, end=end):
        nodes.append(EvidenceNode(id=hm.id, kind='health_metric', source='health', date=hm.date, project=None, summary=hm.summary, payload=hm.payload, provenance=EvidenceProvenance('health', 'local-heavy')))
    for link in build_sleep_productivity_links(start=start, end=end):
        nodes.append(EvidenceNode(id=link.id, kind='sleep_quality', source='sleep', date=link.sleep_date, project=None, summary=link.summary, payload=link.payload, provenance=EvidenceProvenance('sleep', 'local-heavy')))

def _add_readiness(nodes: list[EvidenceNode], *, end: date) -> None:
    """Build a forecast for the day after ``end`` and emit it as a graph node.

    Failures and degraded fits surface as a ``readiness_forecast`` node with
    ``status="unavailable"`` so the consumer always sees source-readiness
    context, never a silent gap.
    """
    from .readiness import build_readiness_forecast, readiness_payload
    target = end + timedelta(days=1)
    try:
        result = build_readiness_forecast(target_date=target)
    except Exception as exc:
        nodes.append(EvidenceNode(id=f'readiness:{target.isoformat()}:error', kind='readiness_forecast', source='readiness', date=target, project=None, summary=f'readiness forecast unavailable ({type(exc).__name__})', payload={'status': 'error', 'reason': str(exc)[:200]}, provenance=EvidenceProvenance('readiness', 'local-fast')))
        return
    payload = readiness_payload(result)
    if payload['status'] == 'available':
        summary = f"forecast: {payload['predicted_deep_work_min']:.0f} min deep work on {target.isoformat()} (95% CI {payload['ci_low']:.0f}–{payload['ci_high']:.0f}, r²={payload['r_squared']:.2f}, n={payload['sample_n']})"
    else:
        summary = f"readiness forecast {payload['status']}: {payload.get('reason', '')}"
    nodes.append(EvidenceNode(id=f"readiness:{target.isoformat()}:{payload['status']}", kind='readiness_forecast', source='readiness', date=target, project=None, summary=summary, payload=payload, provenance=EvidenceProvenance('readiness', 'local-fast')))

def _add_temporal_signals(nodes: list[EvidenceNode], *, start: date, end: date) -> None:
    from .temporal_signals import detect_temporal_signals
    kind_map: dict[str, EvidenceNodeKind] = {'temporal_changepoint': 'temporal_changepoint', 'temporal_trend': 'temporal_trend', 'temporal_anomaly': 'temporal_anomaly', 'temporal_rhythm': 'temporal_rhythm'}
    for idx, event in enumerate(detect_temporal_signals(start=start, end=end)):
        node_kind = kind_map.get(event.kind)
        if node_kind is None:
            continue
        nodes.append(EvidenceNode(id=f'temporal:{event.kind}:{event.signal}:{event.event_date.isoformat()}:{idx}', kind=node_kind, source='temporal', date=event.event_date, project=None, summary=event.summary, payload=event.payload, provenance=EvidenceProvenance('temporal', 'local-fast')))

def _add_terminal(nodes: list[EvidenceNode], *, start: date, end: date, selected: set[str]) -> None:
    from ..sources.terminal import shell_sessions
    from .terminal_patterns import detect_patterns
    start_dt, end_dt = date_to_dt_range(start, end)
    for idx, session in enumerate(shell_sessions(start=start_dt, end=end_dt)):
        project = _normalize_project(session.project)
        if not _include_project(project, selected):
            continue
        nodes.append(EvidenceNode(id=f'terminal:{session.start.isoformat()}:{idx}:{project}', kind='terminal_session', source='terminal', date=logical_date(session.start), project=project, start=session.start, end=session.end, summary=f'{session.command_count} commands in {session.cwd}', payload={'cwd': session.cwd, 'duration_s': session.duration_s, 'command_count': session.command_count, 'error_count': session.error_count, 'category': session.category, 'commands_summary': list(session.commands_summary)}, provenance=EvidenceProvenance('terminal', 'local-fast')))
    for idx, pattern in enumerate(detect_patterns(start=start, end=end, projects=tuple(selected) if selected else None)):
        nodes.append(EvidenceNode(id=f'terminal-pattern:{pattern.date.isoformat()}:{idx}:{pattern.kind}', kind='terminal_pattern', source='terminal', date=pattern.date, project=_normalize_project(pattern.project), summary=pattern.summary, payload={'kind': pattern.kind, 'cwd': pattern.cwd, 'command_count': pattern.command_count, 'error_count': pattern.error_count, 'duration_s': pattern.duration_s, 'top_commands': pattern.top_commands, 'confidence': pattern.confidence}, provenance=EvidenceProvenance('terminal', 'local-fast')))

def _add_analysis_artifacts(nodes: list[EvidenceNode], edges: list[EvidenceEdge], *, end: date, selected: set[str], exclude_names: frozenset[str]) -> None:
    projects = selected or None
    artifacts = tuple((artifact for artifact in latest_artifacts(projects=projects) if artifact.name not in exclude_names))
    by_name = {artifact.name: artifact for artifact in artifacts}
    for artifact in artifacts:
        generated_at = artifact.generated_at.isoformat() if artifact.generated_at is not None else None
        for project in artifact.projects:
            if not _include_project(project, selected):
                continue
            node_id = f'analysis:{artifact.name}:{project}'
            nodes.append(EvidenceNode(id=node_id, kind='analysis_artifact', source='analysis', date=end, project=project, summary=f'{artifact.name} ({artifact.kind}, {artifact.size_bytes} bytes)', payload={'name': artifact.name, 'kind': artifact.kind, 'projects': artifact.projects, 'size_bytes': artifact.size_bytes, 'modified_at': artifact.modified_at.isoformat(), 'generated_at': generated_at, 'top_level_keys': artifact.top_level_keys, 'brief': artifact.brief, 'references': artifact.references}, provenance=EvidenceProvenance('analysis', 'local-fast', path=str(artifact.path))))
            for reference in artifact.references:
                referenced = by_name.get(reference)
                if referenced is None:
                    continue
                reference_projects = referenced.projects or (project,)
                for reference_project in reference_projects:
                    if project != reference_project and project not in referenced.projects:
                        continue
                    if not _include_project(reference_project, selected):
                        continue
                    edges.append(EvidenceEdge(node_id, f'analysis:{reference}:{reference_project}', 'references', f'analysis artifact references {reference}', 0.8))

def _add_analysis_claims(nodes: list[EvidenceNode], edges: list[EvidenceEdge], *, end: date, selected: set[str], exclude_names: frozenset[str]) -> None:
    projects = selected or None
    for claim in analysis_claims(projects=projects, exclude_names=exclude_names):
        if not _include_project(claim.project, selected):
            continue
        node_id = f'analysis-claim:{claim.id}'
        nodes.append(EvidenceNode(id=node_id, kind='analysis_claim', source='analysis', date=end, project=claim.project, summary=claim.summary, payload={'claim_type': claim.claim_type, 'artifact_name': claim.artifact_name, 'confidence': claim.confidence, 'generated_at': claim.generated_at.isoformat() if claim.generated_at is not None else None, **claim.payload}, provenance=EvidenceProvenance('analysis', 'local-fast', path=claim.artifact_name)))
        artifact_node_id = f'analysis:{claim.artifact_name}:{claim.project}'
        edges.append(EvidenceEdge(node_id, artifact_node_id, 'references', f'analysis claim extracted from {claim.artifact_name}', claim.confidence))

def _same_project_day_edges(nodes: Sequence[EvidenceNode]) -> tuple[EvidenceEdge, ...]:
    grouped: dict[tuple[date, str], list[EvidenceNode]] = defaultdict(list)
    for node in nodes:
        if node.kind in {'analysis_artifact', 'analysis_claim'}:
            continue
        if node.project:
            grouped[node.date, node.project].append(node)
    edges: list[EvidenceEdge] = []
    for (day, project), group in grouped.items():
        if len(group) < 2:
            continue
        ordered = sorted(group, key=lambda node: (node.source, node.id))
        for left, right in zip(ordered, ordered[1:]):
            edges.append(EvidenceEdge(left.id, right.id, 'same_project_day', f'{project} on {day}', 0.4))
    return tuple(edges)

def _temporal_overlap_edges(nodes: Sequence[EvidenceNode]) -> tuple[EvidenceEdge, ...]:
    timed = [node for node in nodes if node.project and node.start is not None and (node.end is not None) and (node.end > node.start)]
    edges: list[EvidenceEdge] = []
    for idx, left in enumerate(timed):
        for right in timed[idx + 1:]:
            if left.project != right.project or left.source == right.source:
                continue
            if left.start is None or left.end is None or right.start is None or (right.end is None):
                continue
            if left.end > right.start and right.end > left.start:
                edges.append(EvidenceEdge(left.id, right.id, 'temporal_overlap', f'{left.source} overlaps {right.source}', 0.7))
    return tuple(edges)
_TOOL_COMMAND_TOKENS: dict[str, tuple[str, ...]] = {'Bash': ('pytest', 'cargo', 'just', 'npm', 'git', 'make', 'nix', 'ruff', 'mypy'), 'Edit': (), 'Read': (), 'Write': ()}

def _extract_overlap_sources_from_nodes(nodes: Sequence[EvidenceNode]) -> 'tuple[list[Any], list[Any], list[dict[str, Any]]]':
    """Extract typed source rows from EvidenceNode payloads for substrate promotion.

    Returns (work_events, commit_facts, symbol_change_rows). Skips nodes whose
    payload doesn't carry enough data — partial promotion is fine; SQL views
    will simply not produce edges for missing rows, matching Python semantics.
    """
    from datetime import datetime as _dt
    from ..sources.git import GitCommitFact
    from ..sources.polylogue import WorkEvent
    work_events: list[WorkEvent] = []
    commit_facts: list[GitCommitFact] = []
    for node in nodes:
        payload = node.payload or {}
        if node.kind == 'ai_work_event':
            event_id = payload.get('event_id')
            conversation_id = payload.get('conversation_id')
            if not event_id or not conversation_id:
                continue
            file_paths = tuple((p for p in payload.get('file_paths') or [] if p))
            tools_used = tuple((t for t in payload.get('tools_used') or [] if t))
            duration_ms = int(payload.get('duration_ms') or 0)
            kind = str(payload.get('kind') or 'unknown')
            confidence = float(payload.get('kind_confidence') or payload.get('confidence') or 0.0)
            provider = str(payload.get('provider') or '')
            summary = node.summary or ''
            work_events.append(WorkEvent(event_id=str(event_id), conversation_id=str(conversation_id), provider=provider, kind=kind, confidence=confidence, start=node.start, end=node.end, duration_ms=duration_ms, file_paths=file_paths, tools_used=tools_used, summary=summary))
        elif node.kind == 'commit':
            sha = payload.get('commit') or payload.get('sha')
            if not sha or not node.project:
                continue
            paths = tuple((p for p in payload.get('paths') or [] if p))
            authored_at = node.start or _dt.combine(node.date, _dt.min.time())
            commit_facts.append(GitCommitFact(repo=node.project, commit=str(sha), authored_at=authored_at, author=str(payload.get('author') or ''), subject=str(payload.get('subject') or node.summary or ''), lines_added=int(payload.get('lines_added') or 0), lines_deleted=int(payload.get('lines_deleted') or 0), lines_changed=int(payload.get('lines_changed') or 0), files_changed=int(payload.get('files_changed') or len(paths)), paths=paths, path_roots=tuple((p for p in payload.get('path_roots') or [] if p))))
    symbol_changes = _load_symbol_changes_index()
    symbol_rows: list[dict[str, Any]] = []
    for entries in symbol_changes.values():
        symbol_rows.extend(entries)
    return (work_events, commit_facts, symbol_rows)

def _overlap_edges_via_substrate(nodes: Sequence[EvidenceNode], *, refresh_id: str) -> tuple[EvidenceEdge, ...]:
    """Promote overlap-source data + compute file/symbol overlap edges via SQL.

    Used when LYNCHPIN_SUBSTRATE_OVERLAP=1. Produces edges equivalent to:
        _polylogue_work_event_file_overlap_edges(nodes)
      + _polylogue_work_event_symbol_overlap_edges(nodes)

    The third overlap (tool_overlap) is unchanged — terminal_session is not
    yet a substrate table.
    """
    from lynchpin.duck import connect, apply_schema
    from lynchpin.duck.promote import promote_ai_work_events, promote_commits, promote_symbol_changes
    from lynchpin.duck.reader import compute_file_overlap_edges, compute_symbol_overlap_edges
    work_events, commit_facts, symbol_rows = _extract_overlap_sources_from_nodes(nodes)
    if not work_events or not commit_facts:
        return ()
    project_by_event_id: dict[str, str | None] = {}
    for node in nodes:
        if node.kind == 'ai_work_event':
            event_id = (node.payload or {}).get('event_id')
            if event_id:
                project_by_event_id[str(event_id)] = node.project

    def _project_resolver(ev: Any) -> str | None:
        return project_by_event_id.get(ev.event_id)
    edges: list[EvidenceEdge] = []
    with connect() as conn:
        apply_schema(conn)
        promote_commits(conn, refresh_id=refresh_id, facts=commit_facts)
        promote_ai_work_events(conn, refresh_id=refresh_id, events=work_events, project_resolver=_project_resolver)
        if symbol_rows:
            promote_symbol_changes(conn, refresh_id=refresh_id, rows=symbol_rows)
        edges.extend(compute_file_overlap_edges(conn, we_refresh_id=refresh_id, commit_refresh_id=refresh_id))
        edges.extend(compute_symbol_overlap_edges(conn, we_refresh_id=refresh_id, commit_refresh_id=refresh_id))
        conn.execute('DELETE FROM symbol_change WHERE refresh_id = ?', [refresh_id])
        conn.execute('DELETE FROM ai_work_event WHERE refresh_id = ?', [refresh_id])
        conn.execute('DELETE FROM commit_fact WHERE refresh_id = ?', [refresh_id])
    return tuple(edges)

def _polylogue_work_event_file_overlap_edges(nodes: Sequence[EvidenceNode], *, max_gap_hours: float=24.0) -> tuple[EvidenceEdge, ...]:
    """Bridge ai_work_event ↔ commit on shared file paths within ±max_gap_hours.

    Only emits when both endpoints share a project. Weight is high (0.85)
    because file-path overlap is a strong corroboration signal — much tighter
    than the same_project_day fallback. Caveat for the heuristic kind labels
    rides on the work-event node itself.
    """
    commits_by_project: dict[str, list[EvidenceNode]] = defaultdict(list)
    for node in nodes:
        if node.kind != 'commit' or node.project is None:
            continue
        commits_by_project[node.project].append(node)
    if not commits_by_project:
        return ()
    edges: list[EvidenceEdge] = []
    max_gap_s = max_gap_hours * 3600
    for we_node in nodes:
        if we_node.kind != 'ai_work_event' or we_node.project is None or we_node.start is None:
            continue
        we_files = {str(p) for p in (we_node.payload or {}).get('file_paths', []) if p}
        if not we_files:
            continue
        we_at = _node_anchor_time(we_node)
        if we_at is None:
            continue
        for commit_node in commits_by_project.get(we_node.project, ()):
            commit_at = _node_anchor_time(commit_node)
            if commit_at is None:
                continue
            if abs((commit_at - we_at).total_seconds()) > max_gap_s:
                continue
            commit_files = {str(p) for p in (commit_node.payload or {}).get('paths', []) if p}
            shared = we_files & commit_files
            if not shared:
                continue
            preview = ', '.join(sorted(shared)[:3])
            evidence = f'shared paths: {preview}' + ('' if len(shared) <= 3 else f' (+{len(shared) - 3})')
            edges.append(EvidenceEdge(we_node.id, commit_node.id, 'file_overlap', evidence, weight=0.85))
    return tuple(edges)

def _polylogue_work_event_symbol_overlap_edges(nodes: Sequence[EvidenceNode], *, max_gap_hours: float=24.0) -> tuple[EvidenceEdge, ...]:
    """M.14 — symbol-level cousin of file_overlap.

    Bridges ``ai_work_event`` ↔ ``commit`` only when the commit actually
    modified a symbol in a file the AI touched. This is tighter than
    file_overlap, which fires on any shared file path regardless of whether
    that file's symbols changed. Edge weight 0.95 (highest in the graph)
    because a same-symbol intersection is the strongest causal-shape
    evidence Lynchpin can produce.

    Reads ``active_symbol_changes`` rows from the most recent run when
    available; gracefully no-ops when the artifact is empty (e.g.
    tree-sitter grammars unavailable). Path-level overlap STILL fires via
    ``_polylogue_work_event_file_overlap_edges`` so callers don't lose
    coverage when symbol data is missing.
    """
    symbol_changes_by_commit = _load_symbol_changes_index()
    if not symbol_changes_by_commit:
        return ()
    commits_by_project: dict[str, list[EvidenceNode]] = defaultdict(list)
    for node in nodes:
        if node.kind != 'commit' or node.project is None:
            continue
        commits_by_project[node.project].append(node)
    if not commits_by_project:
        return ()
    edges: list[EvidenceEdge] = []
    max_gap_s = max_gap_hours * 3600
    for we_node in nodes:
        if we_node.kind != 'ai_work_event' or we_node.project is None or we_node.start is None:
            continue
        we_files = {str(p) for p in (we_node.payload or {}).get('file_paths', []) if p}
        if not we_files:
            continue
        we_at = _node_anchor_time(we_node)
        if we_at is None:
            continue
        for commit_node in commits_by_project.get(we_node.project, ()):
            commit_at = _node_anchor_time(commit_node)
            if commit_at is None:
                continue
            if abs((commit_at - we_at).total_seconds()) > max_gap_s:
                continue
            sha = (commit_node.payload or {}).get('commit') or ''
            changed_symbols = symbol_changes_by_commit.get(sha) or []
            if not changed_symbols:
                continue
            overlapping = [sym for sym in changed_symbols if any((_path_match(sym.get('path'), p) for p in we_files))]
            if not overlapping:
                continue
            symbol_names = sorted({s.get('qualified_name', '?') for s in overlapping if s.get('qualified_name')})
            preview = ', '.join(symbol_names[:3])
            evidence = f'shared symbols: {preview}'
            if len(symbol_names) > 3:
                evidence += f' (+{len(symbol_names) - 3})'
            edges.append(EvidenceEdge(we_node.id, commit_node.id, 'symbol_overlap', evidence, weight=0.95))
    return tuple(edges)

def _load_symbol_changes_index() -> dict[str, list[dict[str, Any]]]:
    """Load and index ``active_symbol_changes.json`` events by commit SHA.

    Returns empty when the artifact is missing or empty (e.g. tree-sitter
    grammars unavailable). Caller must be prepared for an empty result.
    """
    from ..analysis.core.io import load_json_if_exists, resolve_analysis_path
    try:
        payload = load_json_if_exists(resolve_analysis_path('active_symbol_changes.json')) or {}
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    events = payload.get('events') or []
    if not isinstance(events, list):
        return {}
    by_sha: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in events:
        if not isinstance(entry, dict):
            continue
        sha = entry.get('sha')
        if sha:
            by_sha[str(sha)].append(entry)
    return dict(by_sha)

def _path_match(symbol_path: object, ai_file_path: str) -> bool:
    """True when the AI's reference resolves to the symbol's path.

    AI work-event file_paths can be absolute (``/realm/project/x/src/foo.py``)
    or repo-relative (``src/foo.py``); the symbol-changes artifact carries
    repo-relative paths. Match by suffix to bridge both shapes.
    """
    if not symbol_path or not ai_file_path:
        return False
    sym = str(symbol_path).strip().lstrip('/')
    ai = str(ai_file_path).strip().lstrip('/')
    if not sym or not ai:
        return False
    return ai.endswith(sym) or sym.endswith(ai)

def _polylogue_work_event_tool_overlap_edges(nodes: Sequence[EvidenceNode], *, max_gap_min: float=60.0) -> tuple[EvidenceEdge, ...]:
    """Bridge ai_work_event ↔ terminal_session when AI tools and shell commands
    plausibly co-occur within ±max_gap_min, on the same project.

    Heuristic: AI used Bash and the terminal session's top commands include a
    matching token (pytest/cargo/just/...). Co-occurrence — not authorship.
    Weight is intentionally lower than file_overlap (0.5) to reflect that.
    """
    terminals_by_project: dict[str, list[EvidenceNode]] = defaultdict(list)
    for node in nodes:
        if node.kind != 'terminal_session' or node.project is None or node.start is None:
            continue
        terminals_by_project[node.project].append(node)
    if not terminals_by_project:
        return ()
    edges: list[EvidenceEdge] = []
    max_gap_s = max_gap_min * 60
    for we_node in nodes:
        if we_node.kind != 'ai_work_event' or we_node.project is None or we_node.start is None:
            continue
        tools_used = {str(t) for t in (we_node.payload or {}).get('tools_used', []) if t}
        candidate_tokens: set[str] = set()
        for tool in tools_used:
            candidate_tokens.update(_TOOL_COMMAND_TOKENS.get(tool, ()))
        if not candidate_tokens:
            continue
        we_at = _node_anchor_time(we_node)
        if we_at is None:
            continue
        for term_node in terminals_by_project.get(we_node.project, ()):
            term_at = _node_anchor_time(term_node)
            if term_at is None:
                continue
            if abs((term_at - we_at).total_seconds()) > max_gap_s:
                continue
            commands = {str(c).split()[0] for c in (term_node.payload or {}).get('commands_summary', []) if c}
            shared = candidate_tokens & commands
            if not shared:
                continue
            preview = ', '.join(sorted(shared)[:3])
            edges.append(EvidenceEdge(we_node.id, term_node.id, 'tool_overlap', f'co-occurring commands: {preview} (heuristic, not authorship)', weight=0.5))
    return tuple(edges)

def _temporal_proximity_edges(nodes: Sequence[EvidenceNode], *, max_gap_min: int=90) -> tuple[EvidenceEdge, ...]:
    grouped: dict[tuple[date, str], list[EvidenceNode]] = defaultdict(list)
    for node in nodes:
        if node.kind in {'analysis_artifact', 'analysis_claim'} or node.project is None or node.start is None:
            continue
        grouped[node.date, node.project].append(node)
    edges: list[EvidenceEdge] = []
    max_gap_s = max_gap_min * 60
    for group in grouped.values():
        timed = sorted(group, key=_node_time_sort_key)
        for idx, left in enumerate(timed):
            left_at = _node_anchor_time(left)
            if left_at is None:
                continue
            for right in timed[idx + 1:]:
                right_at = _node_anchor_time(right)
                if right_at is None:
                    continue
                gap_s = abs((right_at - left_at).total_seconds())
                if gap_s > max_gap_s:
                    break
                if left.source == right.source:
                    continue
                if left.start is not None and right.start is not None and (left.end is not None) and (right.end is not None) and (as_local(left.end) > as_local(right.start)) and (as_local(right.end) > as_local(left.start)):
                    continue
                gap_min = round(gap_s / 60)
                edges.append(EvidenceEdge(left.id, right.id, 'temporal_proximity', f'{left.source} within {gap_min}m of {right.source}', _proximity_weight(gap_min)))
    return tuple(edges)

def _github_ref_node(*, project: str, kind: str, number: int, day: date) -> EvidenceNode:
    return EvidenceNode(id=_github_ref_id(project, kind, number), kind='github_ref', source='github_ref', date=day, project=project, summary=f'{kind} #{number}', payload={'kind': kind, 'number': number, 'lifecycle': 'referenced'}, provenance=EvidenceProvenance('github_ref', 'local-fast'), caveats=(EvidenceCaveat('github', 'partial', 'Commit referenced this GitHub item, but full issue/PR lifecycle may not be fetched.'),))

def _github_item_node(item: GitHubItem) -> EvidenceNode:
    project = _normalize_project(item.repo or (item.slug.rsplit('/', 1)[-1] if item.slug else None))
    stamp = item.closed_at or item.merged_at or item.updated_at or item.created_at
    day = logical_date(stamp) if stamp is not None else date.today()
    lifecycle = classify_lifecycle(item)
    return EvidenceNode(id=_github_ref_id(project or item.repo, item.kind, item.number), kind='github_pr' if item.kind == 'pr' else 'github_issue', source='github', date=day, project=project, start=item.created_at, end=item.closed_at or item.merged_at, url=item.url, summary=item.title, payload={'kind': item.kind, 'number': item.number, 'state': item.state, 'lifecycle': lifecycle.lifecycle, 'lifecycle_confidence': lifecycle.confidence, 'comment_count': len(item.comments)}, provenance=EvidenceProvenance('github', 'network', path=item.slug))

def _github_item_from_dict(item: dict[str, object]) -> GitHubItem | None:
    number = _int(item.get('number'))
    if number == 0:
        return None
    comments = []
    for raw_comment in _dict_items(item.get('comments')):
        raw_author = raw_comment.get('author') or {}
        comments.append(GitHubComment(author=GitHubActor(raw_author.get('login') if isinstance(raw_author, dict) else None), body=str(raw_comment.get('body') or ''), created_at=parse_datetime(raw_comment.get('createdAt')), url=str(raw_comment.get('url')) if raw_comment.get('url') else None))
    labels = tuple((GitHubLabel(str(label)) for label in _list_items(item.get('labels')) if label))
    kind: GitHubItemKind = 'pr' if item.get('kind') == 'pr' else 'issue'
    raw_state = str(item.get('state') or 'open').lower()
    item_state: GitHubItemState
    if raw_state == 'open':
        item_state = 'open'
    elif raw_state == 'closed':
        item_state = 'closed'
    elif raw_state == 'merged':
        item_state = 'merged'
    else:
        item_state = 'unknown'
    return GitHubItem(repo=str(item.get('repo') or ''), slug=str(item.get('slug') or ''), kind=kind, number=number, title=str(item.get('title') or ''), state=item_state, url=str(item.get('url')) if item.get('url') else None, author=GitHubActor(str(item.get('author') or '') or None), labels=labels, body=str(item.get('body') or ''), comments=tuple(comments), created_at=parse_datetime(item.get('created_at') or item.get('createdAt')), updated_at=parse_datetime(item.get('updated_at') or item.get('updatedAt')), closed_at=parse_datetime(item.get('closed_at') or item.get('closedAt')), merged_at=parse_datetime(item.get('merged_at') or item.get('mergedAt')), merge_commit=str(item.get('merge_commit')) if item.get('merge_commit') else None)

def _frontmatter(text: str) -> dict[str, str]:
    if not text.startswith('---'):
        return {}
    end = text.find('\n---', 3)
    if end < 0:
        return {}
    result: dict[str, str] = {}
    for line in text[3:end].splitlines():
        if ':' not in line or line.startswith(' '):
            continue
        key, value = line.split(':', 1)
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result

def _markdown_title(text: str) -> str | None:
    for line in text.splitlines():
        if line.startswith('# '):
            return line[2:].strip()
    return None

def _excerpt(text: str, *, limit: int=360) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip() and (not line.startswith('---'))]
    return ' '.join(lines)[:limit]

def _projects_from_text(text: str) -> tuple[str, ...]:
    return projects_mentioned_in_text(text)

def _selected_projects(projects: Sequence[str] | None) -> set[str]:
    if not projects:
        return set()
    return {project for project in (_normalize_project(value) for value in projects) if project is not None}

def _include_project(project: str | None, selected: set[str]) -> bool:
    if project is None:
        return not selected
    return not selected or project in selected

def _normalize_project(value: object) -> str | None:
    return canonical_project_name(value)

def _github_ref_id(project: str, kind: str, number: int) -> str:
    return f'github:{project}:{kind}:{number}'

def _format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return '(none)'
    return ', '.join((f'{key}={value}' for key, value in sorted(counts.items())))

def _timeline_entry_key(entry: EvidenceTimelineEntry) -> tuple[date, int, str, str, str, str]:
    timed = entry.when is not None
    when = _timeline_sort_stamp(entry)
    return (entry.date, 0 if timed else 1, when, entry.project or '', entry.source, entry.node_id)

def _format_timeline_when(entry: EvidenceTimelineEntry) -> str:
    if entry.when is None:
        return f'{entry.date.isoformat()} (logical day)'
    return as_local(entry.when).isoformat(timespec='minutes')

def _timeline_sort_stamp(entry: EvidenceTimelineEntry) -> str:
    if entry.when is None:
        return datetime.combine(entry.date, datetime.min.time()).isoformat()
    return as_local(entry.when).isoformat()

def _relation_entry_key(entry: EvidenceRelationEntry) -> tuple[date, float, str, str, str]:
    return (entry.date, -entry.weight, entry.project or '', entry.relation, f'{entry.source_node_id}:{entry.target_node_id}')

def _node_time_sort_key(node: EvidenceNode) -> tuple[str, str, str]:
    anchor = _node_anchor_time(node)
    return (anchor.isoformat() if anchor is not None else '', node.source, node.id)

def _node_anchor_time(node: EvidenceNode) -> datetime | None:
    if node.start is None:
        return None
    return as_local(node.start)

def _proximity_weight(gap_min: int) -> float:
    if gap_min <= 15:
        return 0.82
    if gap_min <= 60:
        return 0.7
    return 0.58

def _markdown_cell(value: object) -> str:
    return str(value).replace('\n', ' ').replace('|', '\\|')

def _dict_items(value: object) -> tuple[dict[str, object], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple((item for item in value if isinstance(item, dict)))

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

def _dedupe_nodes(nodes: Sequence[EvidenceNode]) -> tuple[EvidenceNode, ...]:
    by_id: dict[str, EvidenceNode] = {}
    for node in nodes:
        by_id[node.id] = node
    return tuple(by_id.values())

def _dedupe_edges(edges: Sequence[EvidenceEdge]) -> tuple[EvidenceEdge, ...]:
    by_key: dict[tuple[str, str, str], EvidenceEdge] = {}
    for edge in edges:
        left, right = sorted((edge.source_id, edge.target_id))
        by_key[left, right, edge.relation] = edge
    return tuple(by_key.values())
__all__ = ['EvidenceEdge', 'EvidenceGraph', 'EvidenceNode', 'EvidenceNodeKind', 'EvidenceRelationEntry', 'EvidenceTimelineEntry', 'EvidenceRelation', 'build_evidence_graph', 'evidence_relations', 'evidence_timeline', 'render_evidence_graph_summary', 'render_evidence_relations', 'render_evidence_timeline']
