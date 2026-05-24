"""Polylogue source-node builders for the evidence graph."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ..core.evidence import CostClass, EvidenceCaveat, EvidenceProvenance
from ..core.evidence_graph import EvidenceNode
from ..core.primitives import logical_date
from ..core.project_mentions import projects_mentioned_in_text
from .evidence_projects import include_project, normalize_project


def session_profiles_for_date(*args: Any, **kwargs: Any) -> Any:
    from ..sources.polylogue import session_profiles_for_date as impl

    return impl(*args, **kwargs)


def work_events(*args: Any, **kwargs: Any) -> Any:
    from ..sources.polylogue import work_events as impl

    return impl(*args, **kwargs)


def add_polylogue(
    nodes: list[EvidenceNode], *, start: date, end: date, selected: set[str]
) -> None:
    for session in session_profiles_for_date(start=start, end=end + timedelta(days=1)):
        session_date = session.canonical_session_date
        if session_date is None:
            stamp = session.first_message_at or session.last_message_at
            if stamp is None:
                continue
            session_date = logical_date(stamp)
        projects = tuple(
            project
            for project in (normalize_project(p) for p in session.work_event_projects)
            if project
        )
        if not projects:
            projects = _projects_from_text(session.title)
        for project in projects or (None,):
            if project is not None and not include_project(project, selected):
                continue
            if project is None and selected:
                continue
            nodes.append(
                EvidenceNode(
                    id=f"polylogue:{session.conversation_id}:{project or 'unattributed'}",
                    kind="ai_session",
                    source="polylogue",
                    date=session_date,
                    project=project,
                    start=session.first_message_at,
                    end=session.last_message_at,
                    summary=session.title or f"{session.provider} session",
                    payload={
                        "conversation_id": session.conversation_id,
                        "provider": session.provider,
                        "message_count": session.message_count,
                        "word_count": session.word_count,
                        "engaged_duration_ms": session.engaged_duration_ms,
                        "tool_use_count": session.tool_use_count,
                        "work_event_kind": session.work_event_kind,
                    },
                    provenance=EvidenceProvenance("polylogue", "materialized"),
                    caveats=(),
                )
            )


def add_polylogue_work_events(
    nodes: list[EvidenceNode],
    *,
    start: date,
    end: date,
    selected: set[str],
    mode: CostClass,
) -> None:
    """Promote Polylogue's ``session_work_events`` rows into per-event nodes."""
    from ..core.classify import resolve_project
    from .work_event_kind import overlay_label

    session_projects: dict[str, tuple[str, ...]] = {}
    for session in session_profiles_for_date(start=start, end=end + timedelta(days=1)):
        projects = tuple(
            project
            for project in (normalize_project(p) for p in session.work_event_projects)
            if project
        )
        if not projects:
            projects = _projects_from_text(session.title)
        session_projects[session.conversation_id] = projects

    for event in work_events(start=start, end=end + timedelta(days=1)):
        if event.start is None:
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
        label = overlay_label(
            polylogue_kind=event.kind,
            polylogue_confidence=float(event.confidence or 0.0),
            file_paths=event.file_paths,
            tools_used=event.tools_used,
            duration_ms=int(event.duration_ms or 0),
        )
        target_projects: list[str | None] = (
            list(event_projects) if event_projects else [None]
        )
        for project in target_projects:
            if project is not None and not include_project(project, selected):
                continue
            if project is None and selected:
                continue
            parent_session_id = (
                f"polylogue:{event.conversation_id}:{project or 'unattributed'}"
            )
            summary = (event.summary or f"{event.kind} ({event.provider})")[:240]
            event_caveats: list[EvidenceCaveat] = [
                EvidenceCaveat(
                    "polylogue",
                    "partial",
                    "Work-event boundaries and kind labels are heuristic; see Lynchpin re-classifier overlay (Arc K).",
                )
            ]
            if label.source == "disagreement":
                event_caveats.append(
                    EvidenceCaveat(
                        "lynchpin_overlay",
                        "partial",
                        f"Polylogue says '{label.polylogue_kind}', overlay says '{label.overlay_kind}' - using overlay (stronger features).",
                    )
                )
            nodes.append(
                EvidenceNode(
                    id=f"polylogue:we:{event.event_id}:{project or 'unattributed'}",
                    kind="ai_work_event",
                    source="polylogue",
                    date=event_date,
                    project=project,
                    start=event.start,
                    end=event.end,
                    summary=summary,
                    payload={
                        "event_id": event.event_id,
                        "conversation_id": event.conversation_id,
                        "provider": event.provider,
                        "kind": label.kind,
                        "kind_confidence": label.confidence,
                        "kind_source": label.source,
                        "kind_tier": label.tier,
                        "polylogue_kind": label.polylogue_kind,
                        "polylogue_confidence": label.polylogue_confidence,
                        "overlay_kind": label.overlay_kind,
                        "overlay_confidence": label.overlay_confidence,
                        "duration_ms": event.duration_ms,
                        "file_paths": list(event.file_paths),
                        "tools_used": list(event.tools_used),
                        "parent_session_id": parent_session_id,
                    },
                    provenance=EvidenceProvenance("polylogue", mode),
                    caveats=tuple(event_caveats),
                )
            )


def _projects_from_text(text: str) -> tuple[str, ...]:
    return projects_mentioned_in_text(text)
