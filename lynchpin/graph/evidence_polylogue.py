"""Polylogue source-node builders for the evidence graph."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ..core.evidence import EvidenceCaveat, EvidenceProvenance
from ..core.evidence_graph import EvidenceNode
from ..core.primitives import logical_date
from ..core.project_mentions import projects_mentioned_in_text
from ..core.projects import canonical_project_name
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
    # Degrade gracefully when Polylogue insight products aren't materialized
    # (daemon down, schema upgrade pending, etc.). Previously the
    # PolylogueMaterializationError propagated all the way up through
    # build_evidence_graph → context_pack → materialize, crashing every
    # downstream consumer including unrelated personal-source promotions.
    # Now we emit zero polylogue nodes and continue — exactly as we'd
    # handle any other unavailable source.
    from ..sources.polylogue import PolylogueMaterializationError
    try:
        sessions = list(
            session_profiles_for_date(start=start, end=end + timedelta(days=1))
        )
    except PolylogueMaterializationError as exc:
        import logging
        logging.getLogger(__name__).warning(
            "Polylogue insight products unavailable; emitting zero ai_session nodes: %s", exc
        )
        return
    for session in sessions:
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
                        "workflow_shape": session.workflow_shape,
                        "workflow_shape_confidence": session.workflow_shape_confidence,
                        "terminal_state": session.terminal_state,
                        "terminal_state_confidence": session.terminal_state_confidence,
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
) -> None:
    """Promote Polylogue's ``session_work_events`` rows into per-event nodes."""
    from ..core.classify import resolve_project
    from .work_event_kind import overlay_label

    # Same graceful-degrade as add_polylogue above.
    from ..sources.polylogue import PolylogueMaterializationError
    session_projects: dict[str, tuple[str, ...]] = {}
    try:
        sessions = list(
            session_profiles_for_date(start=start, end=end + timedelta(days=1))
        )
    except PolylogueMaterializationError as exc:
        import logging
        logging.getLogger(__name__).warning(
            "Polylogue insight products unavailable; emitting zero work_event nodes: %s", exc
        )
        return
    for session in sessions:
        projects = tuple(
            project
            for project in (normalize_project(p) for p in session.work_event_projects)
            if project
        )
        if not projects:
            projects = _projects_from_text(session.title)
        session_projects[session.conversation_id] = projects

    try:
        events = work_events(start=start, end=end + timedelta(days=1))
    except PolylogueMaterializationError as exc:
        import logging
        logging.getLogger(__name__).warning(
            "Polylogue work-event products unavailable; emitting zero work_event nodes: %s", exc
        )
        return

    for event in events:
        if event.start is None:
            continue
        event_projects = _event_projects(
            event.file_paths,
            session_projects.get(event.conversation_id, ()),
            resolve_project=resolve_project,
        )

        event_date = logical_date(event.start)
        label = overlay_label(
            source_kind=event.kind,
            source_confidence=float(event.confidence or 0.0),
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
                        f"Source label '{label.source_kind}' conflicts with overlay label '{label.overlay_kind}' - using overlay (stronger features).",
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
                        "source_kind": label.source_kind,
                        "source_confidence": label.source_confidence,
                        "overlay_kind": label.overlay_kind,
                        "overlay_confidence": label.overlay_confidence,
                        "workflow_shape": getattr(event, "workflow_shape", None),
                        "workflow_shape_confidence": getattr(
                            event, "workflow_shape_confidence", 0.0
                        ),
                        "terminal_state": getattr(event, "terminal_state", None),
                        "terminal_state_confidence": getattr(
                            event, "terminal_state_confidence", 0.0
                        ),
                        "duration_ms": event.duration_ms,
                        "file_paths": list(event.file_paths),
                        "tools_used": list(event.tools_used),
                        "parent_session_id": parent_session_id,
                    },
                    provenance=EvidenceProvenance("polylogue", "materialized"),
                    caveats=tuple(event_caveats),
                )
            )


def _projects_from_text(text: str) -> tuple[str, ...]:
    return projects_mentioned_in_text(text)


def _event_projects(
    file_paths: tuple[str, ...],
    session_projects: tuple[str, ...],
    *,
    resolve_project: Any,
) -> list[str]:
    projects: list[str] = []
    for project in session_projects:
        if project and project not in projects:
            projects.append(project)
    for path in file_paths:
        if "/realm/project/" not in str(path):
            continue
        project = canonical_project_name(str(path))
        if project and project not in projects:
            projects.append(project)
    if projects:
        return projects
    for path in file_paths:
        project = resolve_project(path)
        if project and project not in projects:
            projects.append(project)
    return projects
