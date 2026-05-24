"""Cross-source project/day work correlation.

This module keeps the first correlation layer deliberately concrete: one row per
project per day, with source-specific evidence retained rather than collapsed
into an opaque score. It is intended as input to current-state analysis where
git, GitHub, Polylogue, ActivityWatch, and terminal evidence must be interpreted
together without double-counting any one scalar.
"""
from __future__ import annotations
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterable, Sequence
from ..core.parse import parse_datetime
from ..core.project_mentions import projects_mentioned_in_text
from ..core.primitives import logical_date
from ..core.projects import canonical_project_name
from ..sources.github import GitHubActor, GitHubComment, GitHubItem, GitHubItemKind, GitHubItemState, GitHubLabel, classify_lifecycle, extract_commit_refs

@dataclass
class _MutableCorrelatedWorkDay:
    date: date
    project: str
    commit_shas: set[str] = field(default_factory=set)
    commit_subjects: list[str] = field(default_factory=list)
    github_refs: set[str] = field(default_factory=set)
    github_lifecycle_refs: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    ai_conversation_ids: set[str] = field(default_factory=set)
    ai_event_kind_breakdown: Counter[str] = field(default_factory=Counter)
    ai_event_kind_weighted: dict[str, float] = field(default_factory=dict)
    raw_log_refs: set[str] = field(default_factory=set)
    focus_minutes: float = 0.0
    shell_minutes: float = 0.0
    shell_command_count: int = 0
    sources: set[str] = field(default_factory=set)

@dataclass(frozen=True)
class CorrelatedWorkDay:
    date: date
    project: str
    commit_count: int
    commit_shas: tuple[str, ...]
    commit_subjects: tuple[str, ...]
    github_refs: tuple[str, ...]
    github_lifecycles: dict[str, int]
    ai_session_count: int
    ai_conversation_ids: tuple[str, ...]
    raw_log_count: int
    raw_log_refs: tuple[str, ...]
    focus_minutes: float
    shell_minutes: float
    shell_command_count: int
    sources: tuple[str, ...]
    ai_kind_breakdown: tuple[tuple[str, int], ...] = ()
    ai_kind_weighted: tuple[tuple[str, float], ...] = ()

    @property
    def source_count(self) -> int:
        return len(self.sources)

    @property
    def has_cross_source_support(self) -> bool:
        return self.source_count >= 2

    @property
    def dominant_ai_kind(self) -> str | None:
        """Top kind by weighted score, breaking ties by raw count."""
        if not self.ai_kind_weighted:
            return None
        weighted = dict(self.ai_kind_weighted)
        raw = dict(self.ai_kind_breakdown)
        return max(weighted, key=lambda k: (weighted[k], raw.get(k, 0)))

@dataclass(frozen=True)
class WorkCorrelationSummary:
    row_count: int
    cross_source_row_count: int
    projects: tuple[str, ...]
    source_counts: dict[str, int]
    source_pair_counts: dict[str, int]
    git_without_ai_or_focus: int
    ai_without_git: int
    focus_without_git: int
    terminal_without_git: int

@dataclass(frozen=True)
class WorkEvidenceClaim:
    date: date
    project: str
    support_level: str
    score: float
    sources: tuple[str, ...]
    relation_count: int
    strongest_relations: tuple[str, ...]
    summary: str
    caveats: tuple[str, ...] = ()

@dataclass(frozen=True)
class DatasetCorrelation:
    sources: tuple[str, str]
    score: float
    relation_counts: dict[str, int]
    projects: tuple[str, ...]
    dates: tuple[date, ...]
    examples: tuple[str, ...]

@dataclass
class _MutableDatasetCorrelation:
    sources: tuple[str, str]
    relation_counts: Counter[str] = field(default_factory=Counter)
    projects: set[str] = field(default_factory=set)
    dates: set[date] = field(default_factory=set)
    context_weights: dict[tuple[date, str, str], float] = field(default_factory=dict)
    context_examples: dict[tuple[date, str, str], str] = field(default_factory=dict)
    weight: float = 0.0

@dataclass(frozen=True)
class _RelationSupport:
    dimension: str
    evidence: str
    weight: float

def correlate_work_days(*, git_facts: Sequence[object]=(), github_items: Sequence[GitHubItem | dict[str, object]]=(), ai_sessions: Sequence[object]=(), raw_log_entries: Sequence[object]=(), focus_spans: Sequence[object]=(), shell_sessions: Sequence[object]=()) -> tuple[CorrelatedWorkDay, ...]:
    """Correlate heterogeneous work evidence by normalized project and date."""
    rows: dict[tuple[date, str], _MutableCorrelatedWorkDay] = {}
    normalized_github_items = tuple((_github_item_from_context(item) for item in github_items))
    github_by_ref = _github_index(normalized_github_items)

    def row(day: date, project: str) -> _MutableCorrelatedWorkDay:
        key = (day, project)
        if key not in rows:
            rows[key] = _MutableCorrelatedWorkDay(date=day, project=project)
        return rows[key]
    for fact in git_facts:
        project = _normalize_project(getattr(fact, 'repo', None))
        authored_at = getattr(fact, 'authored_at', None)
        if project is None or authored_at is None:
            continue
        bucket = row(logical_date(authored_at), project)
        bucket.sources.add('git')
        bucket.commit_shas.add(str(getattr(fact, 'commit', '')))
        subject = str(getattr(fact, 'subject', '') or '')
        if subject:
            bucket.commit_subjects.append(subject)
        refs = extract_commit_refs(subject)
        for kind, numbers in refs.items():
            singular = kind[:-1] if kind.endswith('s') else kind
            for number in numbers:
                ref = f'{singular}#{number}'
                bucket.github_refs.add(ref)
                item = github_by_ref.get((project, singular, number)) or github_by_ref.get((project, 'any', number))
                if item is not None:
                    bucket.github_lifecycle_refs[classify_lifecycle(item).lifecycle].add(ref)
                    bucket.sources.add('github')
    for item in normalized_github_items:
        project = _normalize_project(item.repo or (item.slug.rsplit('/', 1)[-1] if item.slug else None))
        day = _github_item_date(item)
        if project is None or day is None:
            continue
        bucket = row(day, project)
        bucket.sources.add('github')
        ref = f'{item.kind}#{item.number}'
        bucket.github_refs.add(ref)
        bucket.github_lifecycle_refs[classify_lifecycle(item).lifecycle].add(ref)
    for session in ai_sessions:
        first_message_at = getattr(session, 'first_message_at', None)
        session_date = logical_date(first_message_at) if first_message_at is not None else getattr(session, 'canonical_session_date', None)
        if session_date is None:
            continue
        conv_id = str(getattr(session, 'conversation_id', ''))
        for project in _projects_from_ai_session(session):
            bucket = row(session_date, project)
            bucket.sources.add('polylogue')
            bucket.ai_conversation_ids.add(conv_id)
    for entry in raw_log_entries:
        timestamp = getattr(entry, 'timestamp', None)
        if timestamp is None:
            continue
        for project in _projects_from_text(str(getattr(entry, 'text', '') or '')):
            bucket = row(logical_date(timestamp), project)
            bucket.sources.add('raw_log')
            source_path = getattr(entry, 'source_path', '')
            line_no = getattr(entry, 'line_no', '')
            bucket.raw_log_refs.add(f'{source_path}:{line_no}' if source_path and line_no else timestamp.isoformat())
    for span in focus_spans:
        project = _normalize_project(getattr(span, 'project', None))
        start = getattr(span, 'start', None)
        span_date = getattr(span, 'date', None)
        if project is None or (start is None and span_date is None):
            continue
        day = logical_date(start) if isinstance(start, datetime) else span_date if isinstance(span_date, date) else None
        if day is None:
            continue
        bucket = row(day, project)
        bucket.sources.add('activitywatch')
        bucket.focus_minutes += float(getattr(span, 'duration_s', 0.0) or 0.0) / 60.0
    for session in shell_sessions:
        project = _normalize_project(getattr(session, 'project', None))
        start = getattr(session, 'start', None)
        if project is None or start is None:
            continue
        bucket = row(logical_date(start), project)
        bucket.sources.add('terminal')
        bucket.shell_minutes += float(getattr(session, 'duration_s', 0.0) or 0.0) / 60.0
        bucket.shell_command_count += int(getattr(session, 'command_count', 0) or 0)
    return tuple((_freeze(row) for row in sorted(rows.values(), key=lambda item: (item.date, item.project))))

def summarize_work_correlations(rows: Sequence[CorrelatedWorkDay]) -> WorkCorrelationSummary:
    """Summarize which source joins are present and where evidence is isolated."""
    source_counts: Counter[str] = Counter()
    source_pair_counts: Counter[str] = Counter()
    git_without_ai_or_focus = 0
    ai_without_git = 0
    focus_without_git = 0
    terminal_without_git = 0
    for row in rows:
        sources = set(row.sources)
        source_counts.update(sources)
        ordered = sorted(sources)
        for idx, left in enumerate(ordered):
            for right in ordered[idx + 1:]:
                source_pair_counts[f'{left}+{right}'] += 1
        if 'git' in sources and 'polylogue' not in sources and ('activitywatch' not in sources):
            git_without_ai_or_focus += 1
        if 'polylogue' in sources and 'git' not in sources:
            ai_without_git += 1
        if 'activitywatch' in sources and 'git' not in sources:
            focus_without_git += 1
        if 'terminal' in sources and 'git' not in sources:
            terminal_without_git += 1
    return WorkCorrelationSummary(row_count=len(rows), cross_source_row_count=sum((1 for row in rows if row.has_cross_source_support)), projects=tuple(sorted({row.project for row in rows})), source_counts=dict(sorted(source_counts.items())), source_pair_counts=dict(sorted(source_pair_counts.items())), git_without_ai_or_focus=git_without_ai_or_focus, ai_without_git=ai_without_git, focus_without_git=focus_without_git, terminal_without_git=terminal_without_git)

def work_day_correlations(*, start: date, end: date, include_github_context: bool=False, graph: object | None=None) -> tuple[CorrelatedWorkDay, ...]:
    """Load local sources and return project/day correlations for a date window."""
    from ..core.evidence_graph import EvidenceGraph
    from .evidence_graph import build_evidence_graph
    evidence_graph = graph
    if evidence_graph is None:
        evidence_graph = build_evidence_graph(start=start, end=end, include_github_frontier=include_github_context)
    if not isinstance(evidence_graph, EvidenceGraph):
        raise TypeError('graph must be an EvidenceGraph')
    return _correlations_from_graph(evidence_graph, start=start, end=end)

def _correlations_from_graph(graph: object, *, start: date, end: date) -> tuple[CorrelatedWorkDay, ...]:
    rows: dict[tuple[date, str], _MutableCorrelatedWorkDay] = {}

    def row(day: date, project: str) -> _MutableCorrelatedWorkDay:
        key = (day, project)
        if key not in rows:
            rows[key] = _MutableCorrelatedWorkDay(date=day, project=project)
        return rows[key]
    return tuple((_freeze(row) for row in sorted(rows.values(), key=lambda item: (item.date, item.project))))

def render_work_day_correlations(rows: Sequence[CorrelatedWorkDay]) -> str:
    lines = ['| Date | Project | Sources | Commits | GitHub | AI Sessions | Raw Log | Focus h | Shell cmds |', '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for row in rows:
        github = _format_refs(row.github_refs)
        lines.append('| {date} | {project} | {sources} | {commits} | {github} | {ai} | {raw_log} | {focus:.2f} | {shell} |'.format(date=row.date.isoformat(), project=row.project, sources=', '.join(row.sources), commits=row.commit_count, github=github, ai=row.ai_session_count, raw_log=row.raw_log_count, focus=row.focus_minutes / 60.0, shell=row.shell_command_count))
    return '\n'.join(lines)

def _format_refs(refs: Sequence[str], *, limit: int=8) -> str:
    ordered = tuple(sorted(refs, key=_ref_sort_key))
    if len(ordered) <= limit:
        return ', '.join(ordered)
    return f"{', '.join(ordered[:limit])} (+{len(ordered) - limit} more)"

def _ref_sort_key(ref: str) -> tuple[str, int, str]:
    kind, sep, number = ref.partition('#')
    if sep:
        try:
            return (kind, int(number), ref)
        except ValueError:
            pass
    return (ref, 0, ref)

def render_work_correlation_summary(summary: WorkCorrelationSummary) -> str:
    """Render coverage counters in a stable, compact Markdown shape."""
    lines = [f'- Project/day rows: {summary.row_count}', f'- Cross-source rows: {summary.cross_source_row_count}', f"- Projects: {', '.join(summary.projects)}", f'- Source counts: {_format_counts(summary.source_counts)}', f'- Source pair counts: {_format_counts(summary.source_pair_counts)}', f'- Git without AI or focus: {summary.git_without_ai_or_focus}', f'- AI without git: {summary.ai_without_git}', f'- Focus without git: {summary.focus_without_git}', f'- Terminal without git: {summary.terminal_without_git}']
    return '\n'.join(lines)

def strongest_work_correlations(rows: Sequence[CorrelatedWorkDay], *, limit: int=12) -> tuple[CorrelatedWorkDay, ...]:
    """Return the rows with the richest cross-source support first."""
    return tuple(sorted(rows, key=lambda row: (row.source_count, row.commit_count, row.ai_session_count, len(row.github_refs), row.raw_log_count, row.focus_minutes, row.shell_command_count), reverse=True)[:limit])

def supported_work_claims(rows: Sequence[CorrelatedWorkDay], *, graph: object | None=None, limit: int=12) -> tuple[WorkEvidenceClaim, ...]:
    """Return project/day claims with source and relation support kept explicit."""
    relations_by_key = _relations_by_project_day(graph)
    claims = [_work_claim(row, relations_by_key.get((row.date, row.project), ())) for row in rows]
    return tuple(sorted(claims, key=lambda item: (item.score, item.date, item.project), reverse=True)[:limit])

def render_supported_work_claims(claims: Sequence[WorkEvidenceClaim]) -> str:
    lines = ['| Date | Project | Support | Score | Sources | Relations | Claim | Caveats |', '|---|---:|---:|---:|---|---|---|---|']
    for claim in claims:
        lines.append('| {date} | {project} | {support} | {score:.2f} | {sources} | {relations} | {summary} | {caveats} |'.format(date=claim.date.isoformat(), project=claim.project, support=claim.support_level, score=claim.score, sources=', '.join(claim.sources), relations='<br>'.join((_markdown_cell(value) for value in claim.strongest_relations)), summary=_markdown_cell(claim.summary), caveats='<br>'.join((_markdown_cell(value) for value in claim.caveats))))
    if not claims:
        lines.append('|  |  | weak | 0.00 |  |  | no supported work claims |  |')
    return '\n'.join(lines)

def dataset_correlations(graph: object | None, *, limit: int=12, include_analysis: bool=False) -> tuple[DatasetCorrelation, ...]:
    """Summarize direct cross-dataset support from graph relations."""
    if graph is None:
        return ()
    from ..core.evidence_graph import EvidenceGraph
    from .evidence_views import evidence_relations
    if not isinstance(graph, EvidenceGraph):
        return ()
    grouped: dict[tuple[str, str], _MutableDatasetCorrelation] = {}
    for relation in evidence_relations(graph, limit=10000):
        if relation.source_source == relation.target_source:
            continue
        if not include_analysis and (relation.source_source == 'analysis' or relation.target_source == 'analysis'):
            continue
        left, right = sorted((relation.source_source, relation.target_source))
        sources = (left, right)
        bucket = grouped.setdefault(sources, _MutableDatasetCorrelation(sources=sources))
        project = relation.project or 'unattributed'
        context_key = (relation.date, project, relation.relation)
        existing_weight = bucket.context_weights.get(context_key)
        if existing_weight is None:
            bucket.context_weights[context_key] = relation.weight
            bucket.relation_counts[relation.relation] += 1
            bucket.weight += relation.weight
        elif relation.weight > existing_weight:
            bucket.context_weights[context_key] = relation.weight
            bucket.weight += relation.weight - existing_weight
        if relation.project is not None:
            bucket.projects.add(relation.project)
        bucket.dates.add(relation.date)
        example = f'{relation.date.isoformat()} {project}: {relation.relation} - {relation.evidence} ({relation.source_summary} ↔ {relation.target_summary})'
        if existing_weight is None or relation.weight >= bucket.context_weights[context_key]:
            bucket.context_examples[context_key] = example
    rows = tuple((_freeze_dataset_correlation(bucket) for bucket in grouped.values()))
    return tuple(sorted(rows, key=lambda row: (row.score, len(row.projects), row.sources), reverse=True)[:limit])

def render_dataset_correlations(rows: Sequence[DatasetCorrelation]) -> str:
    """Render pairwise dataset corroboration as a compact Markdown table."""
    lines = ['| Sources | Score | Relations | Projects | Dates | Examples |', '|---|---:|---|---|---|---|']
    if not rows:
        lines.append('|  | 0.00 |  |  |  | no direct cross-dataset correlations |')
        return '\n'.join(lines)
    for row in rows:
        lines.append('| {sources} | {score:.2f} | {relations} | {projects} | {dates} | {examples} |'.format(sources=' + '.join(row.sources), score=row.score, relations=_format_counts(row.relation_counts), projects=', '.join(row.projects), dates=', '.join((day.isoformat() for day in row.dates[:5])), examples='<br>'.join((_markdown_cell(example) for example in row.examples))))
    return '\n'.join(lines)

def _freeze(row: _MutableCorrelatedWorkDay) -> CorrelatedWorkDay:
    return CorrelatedWorkDay(date=row.date, project=row.project, commit_count=len({sha for sha in row.commit_shas if sha}), commit_shas=tuple(sorted((sha for sha in row.commit_shas if sha))), commit_subjects=tuple(row.commit_subjects), github_refs=tuple(sorted(row.github_refs)), github_lifecycles={lifecycle: len(refs) for lifecycle, refs in sorted(row.github_lifecycle_refs.items())}, ai_session_count=len({cid for cid in row.ai_conversation_ids if cid}), ai_conversation_ids=tuple(sorted((cid for cid in row.ai_conversation_ids if cid))), ai_kind_breakdown=tuple(sorted(row.ai_event_kind_breakdown.items(), key=lambda kv: (-kv[1], kv[0]))), ai_kind_weighted=tuple(((kind, round(weight, 3)) for kind, weight in sorted(row.ai_event_kind_weighted.items(), key=lambda kv: (-kv[1], kv[0])))), raw_log_count=len(row.raw_log_refs), raw_log_refs=tuple(sorted(row.raw_log_refs)), focus_minutes=round(row.focus_minutes, 2), shell_minutes=round(row.shell_minutes, 2), shell_command_count=row.shell_command_count, sources=tuple(sorted(row.sources)))

def _freeze_dataset_correlation(bucket: _MutableDatasetCorrelation) -> DatasetCorrelation:
    relation_variety = len(bucket.relation_counts)
    project_variety = len(bucket.projects)
    day_variety = len(bucket.dates)
    score = bucket.weight + relation_variety * 0.5 + project_variety * 0.15 + day_variety * 0.1
    return DatasetCorrelation(sources=bucket.sources, score=round(score, 3), relation_counts=dict(sorted(bucket.relation_counts.items())), projects=tuple(sorted(bucket.projects)), dates=tuple(sorted(bucket.dates, reverse=True)), examples=tuple((bucket.context_examples[key] for key, _weight in sorted(bucket.context_weights.items(), key=lambda item: (item[1], item[0][0], item[0][1], item[0][2]), reverse=True)[:4])))

def _relations_by_project_day(graph: object | None) -> dict[tuple[date, str], tuple[_RelationSupport, ...]]:
    if graph is None:
        return {}
    from ..core.evidence_graph import EvidenceGraph
    from .evidence_views import evidence_relations
    if not isinstance(graph, EvidenceGraph):
        return {}
    grouped: dict[tuple[date, str], dict[str, _RelationSupport]] = defaultdict(dict)
    for relation in evidence_relations(graph, limit=10000):
        if relation.project is None:
            continue
        if relation.source_source == 'analysis' or relation.target_source == 'analysis':
            continue
        dimension = _relation_dimension(relation.relation, relation.source_source, relation.target_source)
        support = _RelationSupport(dimension=dimension, evidence=relation.evidence, weight=relation.weight)
        existing = grouped[relation.date, relation.project].get(dimension)
        if existing is None or support.weight > existing.weight:
            grouped[relation.date, relation.project][dimension] = support
    return {key: tuple(sorted(value.values(), key=lambda item: (item.weight, item.dimension), reverse=True)) for key, value in grouped.items()}

def _work_claim(row: CorrelatedWorkDay, relations: Sequence[_RelationSupport]) -> WorkEvidenceClaim:
    relation_count = len(relations)
    return WorkEvidenceClaim(date=row.date, project=row.project, support_level=_support_level(row, relation_count), score=_claim_score(row, relation_count), sources=row.sources, relation_count=relation_count, strongest_relations=tuple((f'{relation.dimension}: {relation.evidence}' for relation in relations[:3])), summary=_claim_summary(row, relation_count), caveats=_claim_caveats(row, relation_count))

def _relation_dimension(relation: str, source: str, target: str) -> str:
    left, right = sorted((source, target))
    return f'{relation}:{left}+{right}'

def _claim_score(row: CorrelatedWorkDay, relation_count: int) -> float:
    return round(row.source_count + min(relation_count, 6) * 0.35 + min(row.commit_count, 5) * 0.12 + min(row.ai_session_count, 3) * 0.2 + min(row.focus_minutes / 60.0, 6.0) * 0.08 + min(row.shell_command_count, 12) * 0.04, 3)

def _support_level(row: CorrelatedWorkDay, relation_count: int) -> str:
    if row.source_count >= 4 and relation_count >= 1:
        return 'strong'
    if row.source_count >= 3 and relation_count >= 1:
        return 'strong'
    if row.source_count >= 2:
        return 'moderate'
    return 'weak'

def _claim_summary(row: CorrelatedWorkDay, relation_count: int) -> str:
    parts = []
    if row.commit_count:
        parts.append(f'{row.commit_count} commits')
    if row.ai_session_count:
        parts.append(f'{row.ai_session_count} AI sessions')
    if row.focus_minutes:
        parts.append(f'{row.focus_minutes / 60.0:.2f}h focus')
    if row.shell_command_count:
        parts.append(f'{row.shell_command_count} shell commands')
    if row.raw_log_count:
        parts.append(f'{row.raw_log_count} raw-log entries')
    if row.github_refs:
        parts.append(f'{len(row.github_refs)} GitHub refs')
    if relation_count:
        noun = 'dimension' if relation_count == 1 else 'dimensions'
        parts.append(f'{relation_count} relation {noun}')
    return '; '.join(parts) if parts else 'source evidence present'

def _claim_caveats(row: CorrelatedWorkDay, relation_count: int) -> tuple[str, ...]:
    caveats: list[str] = []
    if row.source_count < 2:
        caveats.append('single-source support; do not treat as corroborated work')
    if row.ai_session_count:
        caveats.append('AI sessions indicate assistance intensity, not independent work units')
    if row.github_refs:
        caveats.append('GitHub refs may be commit-message references unless network frontier evidence is enabled')
    if row.focus_minutes and row.commit_count == 0:
        caveats.append('focus attribution without git support can be title/project inference')
    if relation_count == 0 and row.source_count >= 2:
        caveats.append('cross-source co-presence lacks direct graph relation support')
    return tuple(caveats)

def _markdown_cell(value: object) -> str:
    return str(value).replace('\n', ' ').replace('|', '\\|')

def _github_index(items: Iterable[GitHubItem]) -> dict[tuple[str, str, int], GitHubItem]:
    index: dict[tuple[str, str, int], GitHubItem] = {}
    for item in items:
        project = _normalize_project(item.repo or (item.slug.rsplit('/', 1)[-1] if item.slug else None))
        if project is None:
            continue
        index[project, item.kind, item.number] = item
        index[project, 'any', item.number] = item
    return index

def _github_item_date(item: GitHubItem) -> date | None:
    for value in (item.closed_at, item.merged_at, item.updated_at, item.created_at):
        if value is not None:
            return logical_date(value)
    return None

def _github_item_from_context(item: object) -> GitHubItem:
    if isinstance(item, GitHubItem):
        return item
    if not isinstance(item, dict):
        return GitHubItem(repo='', slug='', kind='issue', number=0, title='', state='open', url=None, author=GitHubActor(None), labels=(), body='', comments=(), created_at=None, updated_at=None, closed_at=None)
    comments = []
    for raw_comment in item.get('comments') or []:
        if not isinstance(raw_comment, dict):
            continue
        raw_author = raw_comment.get('author') or {}
        comments.append(GitHubComment(author=GitHubActor(raw_author.get('login') if isinstance(raw_author, dict) else None), body=str(raw_comment.get('body') or ''), created_at=parse_datetime(raw_comment.get('createdAt')), url=str(raw_comment.get('url')) if raw_comment.get('url') else None))
    labels = tuple((GitHubLabel(str(label)) for label in item.get('labels') or [] if label))
    kind: GitHubItemKind = 'pr' if item.get('kind') == 'pr' else 'issue'
    state: GitHubItemState = 'closed' if item.get('state') == 'closed' else 'open'
    return GitHubItem(repo=str(item.get('repo') or ''), slug=str(item.get('slug') or ''), kind=kind, number=int(item.get('number') or 0), title=str(item.get('title') or ''), state=state, url=item.get('url'), author=GitHubActor(str(item.get('author') or '') or None), labels=labels, body=str(item.get('body') or ''), comments=tuple(comments), created_at=parse_datetime(item.get('created_at') or item.get('createdAt')), updated_at=parse_datetime(item.get('updated_at') or item.get('updatedAt')), closed_at=parse_datetime(item.get('closed_at') or item.get('closedAt')), merged_at=parse_datetime(item.get('merged_at') or item.get('mergedAt')))

def _projects_from_ai_session(session: object) -> tuple[str, ...]:
    projects = getattr(session, 'work_event_projects', ()) or ()
    result = tuple((project for project in (_normalize_project(p) for p in projects) if project))
    if result:
        return result
    title = str(getattr(session, 'title', '') or '')
    for token in title.replace('/', ' ').replace(':', ' ').split():
        project = _normalize_project(token)
        if project:
            return (project,)
    return ()

def _projects_from_text(text: str) -> tuple[str, ...]:
    return projects_mentioned_in_text(text)

def _format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return '(none)'
    return ', '.join((f'{key}={value}' for key, value in sorted(counts.items())))

def _normalize_project(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.rstrip('/').removesuffix('.git')
    if '/' in text:
        if '/realm/project/' in text:
            text = text.split('/realm/project/', 1)[1].split('/', 1)[0]
        else:
            text = text.rsplit('/', 1)[-1]
    aliases = {'lynchpin': 'sinity-lynchpin', 'sinity_lynchpin': 'sinity-lynchpin'}
    return canonical_project_name(aliases.get(text, text))
__all__ = ['CorrelatedWorkDay', 'WorkEvidenceClaim', 'WorkCorrelationSummary', 'correlate_work_days', 'dataset_correlations', 'render_work_correlation_summary', 'render_dataset_correlations', 'render_work_day_correlations', 'render_supported_work_claims', 'strongest_work_correlations', 'summarize_work_correlations', 'supported_work_claims', 'work_day_correlations']
