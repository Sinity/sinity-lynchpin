"""Shared evidence graph DTOs without graph-builder dependencies."""
from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal
from .evidence import CostClass, EvidenceCaveat, EvidenceProvenance
EvidenceNodeKind = Literal['commit', 'github_issue', 'github_pr', 'github_ref', 'ai_session', 'ai_work_event', 'raw_log', 'focus_day', 'focus_span', 'deep_work_block', 'circadian_profile', 'focus_loop', 'fragmentation_day', 'attention_day', 'terminal_session', 'terminal_pattern', 'listening_session', 'web_domain_day', 'sleep_quality', 'health_metric', 'temporal_changepoint', 'temporal_trend', 'temporal_anomaly', 'temporal_rhythm', 'readiness_forecast', 'analysis_artifact', 'analysis_claim']
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
__all__ = ['EvidenceEdge', 'EvidenceGraph', 'EvidenceNode', 'EvidenceNodeKind', 'EvidenceRelation', 'EvidenceRelationEntry', 'EvidenceTimelineEntry']
