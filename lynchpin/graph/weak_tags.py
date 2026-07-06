"""Weak deterministic evidence tagging over evidence graphs.

This module converts low-level graph nodes into rebuildable tags, clusters, and
narrative-worthy moments. The tags are keyword/proximity evidence only; they are
not a substitute for model-assisted semantic classification or grounded content
analysis.
"""

from __future__ import annotations

import contextlib
import json
import re
import sqlite3
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal, Sequence

from ..core.config import get_config
from ..core.evidence_graph import EvidenceGraph, EvidenceNode
from ..core.projects import canonical_project_name
from ..core.serialization import jsonable
from ..core.evidence import EvidenceCaveat, EvidenceProvenance
from .evidence_graph import build_evidence_graph

WeakTagMode = Literal["deterministic"]
WeakTagCategory = Literal[
    "activity",
    "artifact",
    "intent",
    "decision",
    "blocker",
    "question",
    "risk",
    "energy",
    "workload",
    "source_quality",
]


@dataclass(frozen=True)
class WeakTagAnnotation:
    id: str
    node_id: str
    category: WeakTagCategory
    label: str
    summary: str
    confidence: float
    payload: dict[str, Any]
    provenance: EvidenceProvenance
    caveats: tuple[EvidenceCaveat, ...] = ()


@dataclass(frozen=True)
class EvidenceCluster:
    id: str
    date: date
    project: str | None
    node_ids: tuple[str, ...]
    annotation_ids: tuple[str, ...]
    labels: tuple[str, ...]
    summary: str
    support_sources: tuple[str, ...]
    score: float
    caveats: tuple[EvidenceCaveat, ...] = ()


@dataclass(frozen=True)
class NarrativeMoment:
    id: str
    date: date
    project: str | None
    cluster_id: str
    title: str
    summary: str
    score: float
    source_node_ids: tuple[str, ...]
    labels: tuple[str, ...]
    caveats: tuple[EvidenceCaveat, ...] = ()


@dataclass(frozen=True)
class WeakTagEnrichment:
    start: date
    end: date
    generated_at: datetime
    mode: WeakTagMode
    graph: EvidenceGraph
    annotations: tuple[WeakTagAnnotation, ...]
    clusters: tuple[EvidenceCluster, ...]
    moments: tuple[NarrativeMoment, ...]
    caveats: tuple[EvidenceCaveat, ...] = ()


def build_weak_tags(
    graph: EvidenceGraph,
    *,
    mode: WeakTagMode = "deterministic",
    persist: bool = False,
) -> WeakTagEnrichment:
    """Build weak evidence tags, clusters, and narrative moments from a graph."""
    annotations = _annotate_graph(graph)
    clusters = _cluster_graph(graph, annotations)
    moments = _rank_moments(graph, clusters)
    caveats = tuple(graph.caveats)
    enrichment = WeakTagEnrichment(
        start=graph.start,
        end=graph.end,
        generated_at=datetime.now(timezone.utc),
        mode=mode,
        graph=graph,
        annotations=annotations,
        clusters=clusters,
        moments=moments,
        caveats=caveats,
    )
    if persist:
        save_weak_tags(enrichment)
    return enrichment


def current_weak_tags(
    *,
    start: date,
    end: date,
    projects: Sequence[str] | None = None,
    mode: WeakTagMode = "deterministic",
    persist: bool = False,
) -> WeakTagEnrichment:
    """Build the current weak-tag product from primary sources.

    Persisted rows are a durable product surface for inspection and downstream
    tooling, not a domain-object cache yet: `WeakTagEnrichment` deliberately
    contains the source graph, so this function rebuilds from primary evidence.
    """
    selected = tuple(sorted(project for project in (canonical_project_name(p) for p in projects or ()) if project))
    graph = build_evidence_graph(start=start, end=end, projects=selected)
    return build_weak_tags(graph, mode=mode, persist=persist)


def narrative_moments(
    *,
    start: date,
    end: date,
    projects: Sequence[str] | None = None,
    limit: int = 24,
    mode: WeakTagMode = "deterministic",
    persist: bool = False,
) -> tuple[NarrativeMoment, ...]:
    enrichment = current_weak_tags(start=start, end=end, projects=projects, mode=mode, persist=persist)
    return tuple(sorted(enrichment.moments, key=lambda moment: moment.score, reverse=True)[:limit])


def render_weak_tag_summary(enrichment: WeakTagEnrichment, *, moment_limit: int = 12) -> str:
    """Render compact weak-tag coverage."""
    categories = Counter(annotation.category for annotation in enrichment.annotations)
    labels = Counter(label for cluster in enrichment.clusters for label in cluster.labels)
    category_counts: dict[object, int] = {key: value for key, value in categories.items()}
    lines = [
        "- Caveat: keyword/proximity tags only; not model-assisted content classification.",
        f"- Annotations: {len(enrichment.annotations)} ({_format_counts(category_counts)})",
        f"- Clusters: {len(enrichment.clusters)}",
        f"- Narrative moments: {len(enrichment.moments)}",
        f"- Top labels: {_format_counts(dict(labels.most_common(10)))}",
        "",
        "| Date | Project | Score | Labels | Moment |",
        "|---|---:|---:|---|---|",
    ]
    for moment in sorted(enrichment.moments, key=lambda item: item.score, reverse=True)[:moment_limit]:
        labels_s = ", ".join(moment.labels)
        summary = moment.summary.replace("|", "\\|")
        lines.append(f"| {moment.date.isoformat()} | {moment.project or ''} | {moment.score:.2f} | {labels_s} | {summary} |")
    if not enrichment.moments:
        lines.append("|  |  | 0.00 |  | no narrative moments |")
    return "\n".join(lines)


def save_weak_tags(enrichment: WeakTagEnrichment) -> None:
    """Persist rebuildable weak-tag products to the local SQLite cache."""
    db = _weak_tags_db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    project_key = _project_key({node.project for node in enrichment.graph.nodes if node.project})
    with contextlib.closing(sqlite3.connect(db)) as conn:
        with conn:
            _ensure_schema(conn)
            params = (enrichment.start.isoformat(), enrichment.end.isoformat(), project_key, enrichment.mode)
            conn.execute("DELETE FROM weak_tags_annotations WHERE start_date = ? AND end_date = ? AND project_key = ? AND mode = ?", params)
            conn.execute("DELETE FROM weak_tags_clusters WHERE start_date = ? AND end_date = ? AND project_key = ? AND mode = ?", params)
            conn.execute("DELETE FROM weak_tags_moments WHERE start_date = ? AND end_date = ? AND project_key = ? AND mode = ?", params)
            for annotation in enrichment.annotations:
                conn.execute(
                    "INSERT INTO weak_tags_annotations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        enrichment.start.isoformat(),
                        enrichment.end.isoformat(),
                        project_key,
                        enrichment.mode,
                        annotation.id,
                        annotation.node_id,
                        annotation.category,
                        annotation.label,
                        annotation.summary,
                        annotation.confidence,
                        _json(annotation.payload),
                        _json(annotation.caveats),
                    ),
                )
            for cluster in enrichment.clusters:
                conn.execute(
                    "INSERT INTO weak_tags_clusters VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        enrichment.start.isoformat(),
                        enrichment.end.isoformat(),
                        project_key,
                        enrichment.mode,
                        cluster.id,
                        cluster.date.isoformat(),
                        cluster.project,
                        _json(cluster.node_ids),
                        _json(cluster.annotation_ids),
                        _json(cluster.labels),
                        cluster.summary,
                        _json(cluster.support_sources),
                        cluster.score,
                    ),
                )
            for moment in enrichment.moments:
                conn.execute(
                    "INSERT INTO weak_tags_moments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        enrichment.start.isoformat(),
                        enrichment.end.isoformat(),
                        project_key,
                        enrichment.mode,
                        moment.id,
                        moment.date.isoformat(),
                        moment.project,
                        moment.cluster_id,
                        moment.title,
                        moment.summary,
                        moment.score,
                        _json({"source_node_ids": moment.source_node_ids, "labels": moment.labels, "caveats": moment.caveats}),
                    ),
                )


def _annotate_graph(graph: EvidenceGraph) -> tuple[WeakTagAnnotation, ...]:
    annotations: list[WeakTagAnnotation] = []
    for node in graph.nodes:
        annotations.extend(_annotations_for_node(node))
    return tuple(sorted(annotations, key=lambda item: item.id))


def _annotations_for_node(node: EvidenceNode) -> tuple[WeakTagAnnotation, ...]:
    payload = node.payload or {}
    result: list[WeakTagAnnotation] = []
    add = result.append
    text = f"{node.summary}\n{payload.get('text', '')}\n{payload.get('excerpt', '')}"
    if node.kind == "commit":
        prefix = _commit_prefix(node.summary)
        add(_annotation(node, "activity", f"code_{prefix or 'change'}", f"Code change: {node.summary}", 0.72, {"prefix": prefix}))
        if payload.get("files_changed") or payload.get("lines_changed"):
            add(_annotation(node, "artifact", "code_delta", "Commit changed files/lines", 0.7, {
                "files_changed": payload.get("files_changed"),
                "lines_changed": payload.get("lines_changed"),
            }))
        refs = payload.get("github_refs") if isinstance(payload, dict) else {}
        if isinstance(refs, dict) and (refs.get("prs") or refs.get("issues")):
            add(_annotation(node, "workload", "github_referenced_change", "Commit references GitHub work", 0.82, refs))
    elif node.kind in {"github_issue", "github_pr", "github_ref"}:
        lifecycle = str(payload.get("lifecycle") or "referenced")
        add(_annotation(node, "workload", f"github_{lifecycle}", f"GitHub lifecycle: {lifecycle}", 0.78, payload))
    elif node.kind == "ai_session":
        add(_annotation(node, "activity", "ai_assisted_work", f"AI session: {node.summary}", 0.65, {
            "message_count": payload.get("message_count"),
            "tool_use_count": payload.get("tool_use_count"),
            "work_event_kind": payload.get("work_event_kind"),
        }, caveats=node.caveats))
    elif node.kind == "raw_log":
        for category, label, confidence in _text_weak_tags(text):
            add(_annotation(node, category, label, node.summary, confidence, {"source": "raw_log"}))
    elif node.kind == "focus_day":
        add(_annotation(node, "activity", "focus_support", node.summary, 0.55, {"duration_s": payload.get("duration_s")}))
    elif node.kind == "terminal_session":
        add(_annotation(node, "activity", "terminal_execution", node.summary, 0.58, {
            "command_count": payload.get("command_count"),
            "error_count": payload.get("error_count"),
        }))
        if int(payload.get("error_count") or 0) > 0:
            add(_annotation(node, "blocker", "terminal_errors", "Terminal session had non-zero exits", 0.52, {"error_count": payload.get("error_count")}))
    elif node.kind == "analysis_artifact":
        add(_annotation(node, "source_quality", "generated_analysis_product", node.summary, 0.62, {
            "kind": payload.get("kind"),
            "generated_at": payload.get("generated_at"),
            "top_level_keys": payload.get("top_level_keys"),
            "brief": payload.get("brief"),
        }))
    elif node.kind == "analysis_claim":
        add(_annotation(node, "artifact", f"analysis_{payload.get('claim_type') or 'claim'}", node.summary, 0.72, {
            "artifact_name": payload.get("artifact_name"),
            "confidence": payload.get("confidence"),
        }))
    elif node.kind == "machine_pressure_incident":
        # sinnix-kx4: sustained memory/io PSI-spike windows enriched with
        # reclaim/kill telemetry (see analysis.machine.pressure_incidents).
        # Reuses the existing blocker_signal/risk_signal vocabulary rather
        # than inventing a machine-specific label, so a kill-bearing incident
        # gets the same high_value cluster-score bonus and moment title
        # ("blocker or repair loop") as any other blocker evidence.
        kill_events = payload.get("kill_events") or []
        if kill_events:
            add(_annotation(node, "blocker", "blocker_signal", node.summary, 0.88, {
                "focus": payload.get("focus"),
                "kill_event_count": len(kill_events),
                "peak_memory_psi_some_avg10": payload.get("peak_memory_psi_some_avg10"),
                "peak_io_psi_some_avg10": payload.get("peak_io_psi_some_avg10"),
            }, caveats=node.caveats))
        else:
            add(_annotation(node, "risk", "risk_signal", node.summary, 0.6, {
                "focus": payload.get("focus"),
                "peak_memory_psi_some_avg10": payload.get("peak_memory_psi_some_avg10"),
                "peak_io_psi_some_avg10": payload.get("peak_io_psi_some_avg10"),
            }, caveats=node.caveats))
    if not result:
        result.append(_annotation(node, "source_quality", "unclassified_evidence", node.summary, 0.3, {}))
    return tuple(result)


def _cluster_graph(graph: EvidenceGraph, annotations: Sequence[WeakTagAnnotation]) -> tuple[EvidenceCluster, ...]:
    node_map = graph.node_map()
    annotation_ids_by_node: dict[str, list[str]] = defaultdict(list)
    labels_by_node: dict[str, list[str]] = defaultdict(list)
    for annotation in annotations:
        annotation_ids_by_node[annotation.node_id].append(annotation.id)
        labels_by_node[annotation.node_id].append(annotation.label)

    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in graph.edges:
        if edge.relation in {"references", "temporal_overlap", "temporal_proximity", "same_project_day"}:
            adjacency[edge.source_id].add(edge.target_id)
            adjacency[edge.target_id].add(edge.source_id)
    seen: set[str] = set()
    clusters: list[EvidenceCluster] = []
    for node in graph.nodes:
        if node.id in seen:
            continue
        component = _component(node.id, adjacency, seen)
        nodes = [node_map[node_id] for node_id in component if node_id in node_map]
        if not nodes:
            continue
        projects = [n.project for n in nodes if n.project]
        project = Counter(projects).most_common(1)[0][0] if projects else None
        cluster_date = min(n.date for n in nodes)
        labels = tuple(sorted(set(label for n in nodes for label in labels_by_node.get(n.id, []))))
        sources = tuple(sorted({n.source for n in nodes}))
        annotation_ids = tuple(sorted(annotation_id for n in nodes for annotation_id in annotation_ids_by_node.get(n.id, [])))
        score = _cluster_score(nodes, labels, sources)
        cluster_id = f"cluster:{cluster_date}:{project or 'unattributed'}:{len(clusters)}"
        clusters.append(
            EvidenceCluster(
                id=cluster_id,
                date=cluster_date,
                project=project,
                node_ids=tuple(sorted(n.id for n in nodes)),
                annotation_ids=annotation_ids,
                labels=labels,
                summary=_cluster_summary(nodes, labels),
                support_sources=sources,
                score=score,
                caveats=tuple(c for n in nodes for c in n.caveats),
            )
        )
    return tuple(sorted(clusters, key=lambda item: (item.date, item.project or "", -item.score)))


def _rank_moments(graph: EvidenceGraph, clusters: Sequence[EvidenceCluster]) -> tuple[NarrativeMoment, ...]:
    moments = []
    for cluster in clusters:
        if cluster.score < 1.5 and len(cluster.support_sources) < 2:
            continue
        title = _moment_title(cluster)
        moments.append(
            NarrativeMoment(
                id=f"moment:{cluster.id}",
                date=cluster.date,
                project=cluster.project,
                cluster_id=cluster.id,
                title=title,
                summary=cluster.summary,
                score=cluster.score,
                source_node_ids=cluster.node_ids,
                labels=cluster.labels,
                caveats=cluster.caveats + tuple(graph.caveats),
            )
        )
    return tuple(sorted(moments, key=lambda item: item.score, reverse=True))


def _annotation(
    node: EvidenceNode,
    category: WeakTagCategory,
    label: str,
    summary: str,
    confidence: float,
    payload: dict[str, Any],
    *,
    caveats: tuple[EvidenceCaveat, ...] = (),
) -> WeakTagAnnotation:
    return WeakTagAnnotation(
        id=f"ann:{node.id}:{category}:{label}",
        node_id=node.id,
        category=category,
        label=label,
        summary=summary,
        confidence=confidence,
        payload=payload,
        provenance=EvidenceProvenance("weak_tags", "materialized", note="deterministic"),
        caveats=caveats,
    )


def _text_weak_tags(text: str) -> tuple[tuple[WeakTagCategory, str, float], ...]:
    lowered = text.lower()
    result: list[tuple[WeakTagCategory, str, float]] = []
    patterns: tuple[tuple[WeakTagCategory, str, tuple[str, ...], float], ...] = (
        ("decision", "decision_signal", ("decided", "decision", "will move", "settled"), 0.72),
        ("blocker", "blocker_signal", ("blocked", "stuck", "annoyance", "broken", "fails", "failure"), 0.66),
        ("question", "open_question", ("?", "uncertain", "not sure", "whether", "should i", "idk"), 0.58),
        ("risk", "risk_signal", ("risk", "worry", "concern", "danger", "misleading"), 0.6),
        ("intent", "intent_signal", ("want", "need", "goal", "should", "priority", "critical path"), 0.62),
        ("energy", "energy_context", ("sleep", "tired", "energy", "stress", "substance", "dose"), 0.55),
    )
    for category, label, needles, confidence in patterns:
        if any(needle in lowered for needle in needles):
            result.append((category, label, confidence))
    return tuple(result)


def _component(start: str, adjacency: dict[str, set[str]], seen: set[str]) -> tuple[str, ...]:
    queue: deque[str] = deque([start])
    result: list[str] = []
    seen.add(start)
    while queue:
        item = queue.popleft()
        result.append(item)
        for neighbor in sorted(adjacency.get(item, ())):
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append(neighbor)
    return tuple(result)


def _cluster_score(nodes: Sequence[EvidenceNode], labels: Sequence[str], sources: Sequence[str]) -> float:
    score = len(sources) * 0.8 + min(len(nodes), 12) * 0.12
    high_value = {"decision_signal", "recorded_decision", "blocker_signal", "github_executed", "github_pr_closed", "open_tension"}
    score += sum(0.9 for label in labels if label in high_value)
    if any(node.kind == "commit" for node in nodes):
        score += 0.35
    if any(node.kind == "ai_session" for node in nodes):
        score += 0.35
    if any(node.kind == "machine_pressure_incident" for node in nodes):
        score += 0.35
    return round(score, 3)


def _cluster_summary(nodes: Sequence[EvidenceNode], labels: Sequence[str]) -> str:
    project = Counter(n.project for n in nodes if n.project).most_common(1)
    project_s = project[0][0] if project else "unattributed"
    sources = ", ".join(sorted({n.source for n in nodes}))
    lead = max(nodes, key=lambda n: _node_weight(n))
    label_s = ", ".join(labels[:5]) if labels else "evidence"
    return f"{project_s}: {lead.summary} [{label_s}; sources: {sources}]"


def _moment_title(cluster: EvidenceCluster) -> str:
    project = f"{cluster.project}: " if cluster.project else ""
    if any(label in cluster.labels for label in ("decision_signal", "recorded_decision")):
        return f"{project}decision or commitment"
    if any(label in cluster.labels for label in ("blocker_signal", "terminal_errors")):
        return f"{project}blocker or repair loop"
    if any(label.startswith("github_") for label in cluster.labels):
        return f"{project}GitHub-linked work"
    return f"{project}cross-source work moment"


def _node_weight(node: EvidenceNode) -> float:
    weights = {
        "raw_log": 4,
        "commit": 3,
        "github_issue": 3,
        "github_pr": 3,
        "machine_pressure_incident": 3,
        "ai_session": 2,
    }
    return weights.get(node.kind, 1)


def _commit_prefix(summary: str) -> str | None:
    match = re.match(r"^([a-z]+)(?:\([^)]+\))?:", summary)
    return match.group(1) if match else None


def _weak_tags_db_path() -> Path:
    return get_config().cache_dir / "weak_tags.sqlite3"


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS weak_tags_annotations (
            start_date TEXT, end_date TEXT, project_key TEXT, mode TEXT,
            annotation_id TEXT, node_id TEXT, category TEXT, label TEXT,
            summary TEXT, confidence REAL, payload_json TEXT, caveats_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS weak_tags_clusters (
            start_date TEXT, end_date TEXT, project_key TEXT, mode TEXT,
            cluster_id TEXT, date TEXT, project TEXT, node_ids_json TEXT,
            annotation_ids_json TEXT, labels_json TEXT, summary TEXT,
            support_sources_json TEXT, score REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS weak_tags_moments (
            start_date TEXT, end_date TEXT, project_key TEXT, mode TEXT,
            moment_id TEXT, date TEXT, project TEXT, cluster_id TEXT,
            title TEXT, summary TEXT, score REAL, payload_json TEXT
        )
        """
    )


def _json(value: object) -> str:
    return json.dumps(jsonable(value), sort_keys=True)


def _project_key(projects: object) -> str:
    if isinstance(projects, str):
        return projects
    if not isinstance(projects, (list, tuple, set, frozenset)):
        return "*"
    return ",".join(sorted(str(project) for project in projects if project))


def _format_counts(counts: dict[object, int]) -> str:
    if not counts:
        return "(none)"
    return ", ".join(f"{key}={value}" for key, value in sorted(counts.items(), key=lambda item: str(item[0])))


__all__ = [
    "EvidenceCluster",
    "NarrativeMoment",
    "WeakTagAnnotation",
    "WeakTagEnrichment",
    "WeakTagMode",
    "build_weak_tags",
    "current_weak_tags",
    "narrative_moments",
    "render_weak_tag_summary",
    "save_weak_tags",
]
