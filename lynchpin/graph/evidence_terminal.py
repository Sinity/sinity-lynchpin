"""Terminal source-node builders for the evidence graph."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ..core.evidence import EvidenceProvenance
from ..core.evidence_graph import EvidenceNode
from ..core.primitives import date_to_dt_range, logical_date
from .evidence_projects import include_project, normalize_project


def shell_sessions(*args: Any, **kwargs: Any) -> Any:
    from ..sources.terminal import shell_sessions as impl

    return impl(*args, **kwargs)


def add_terminal(
    nodes: list[EvidenceNode],
    *,
    start: date,
    end: date,
    selected: set[str],
) -> None:
    from ..materialization import ensure_materialized

    ensure_materialized(
        "atuin",
        window=(start, end + timedelta(days=1)),
        budget="manual",
    )

    start_dt, end_dt = date_to_dt_range(start, end)
    sessions = tuple(shell_sessions(start=start_dt, end=end_dt, ensure=False))
    for idx, session in enumerate(sessions):
        project = normalize_project(session.project)
        if not include_project(project, selected):
            continue
        nodes.append(
            EvidenceNode(
                id=f"terminal:{session.start.isoformat()}:{idx}:{project}",
                kind="terminal_session",
                source="terminal",
                date=logical_date(session.start),
                project=project,
                start=session.start,
                end=session.end,
                summary=f"{session.command_count} commands in {session.cwd}",
                payload={
                    "cwd": session.cwd,
                    "duration_s": session.duration_s,
                    "command_count": session.command_count,
                    "error_count": session.error_count,
                    "category": session.category,
                    "commands_summary": list(session.commands_summary),
                },
                provenance=EvidenceProvenance("terminal", "materialized"),
            )
        )

    from .terminal_patterns import detect_patterns

    for idx, pattern in enumerate(
        detect_patterns(
            start=start,
            end=end,
            projects=tuple(selected) if selected else None,
            sessions=sessions,
        )
    ):
        nodes.append(
            EvidenceNode(
                id=f"terminal-pattern:{pattern.date.isoformat()}:{idx}:{pattern.kind}",
                kind="terminal_pattern",
                source="terminal",
                date=pattern.date,
                project=normalize_project(pattern.project),
                summary=pattern.summary,
                payload={
                    "kind": pattern.kind,
                    "cwd": pattern.cwd,
                    "command_count": pattern.command_count,
                    "error_count": pattern.error_count,
                    "duration_s": pattern.duration_s,
                    "top_commands": pattern.top_commands,
                    "confidence": pattern.confidence,
                },
                provenance=EvidenceProvenance("terminal", "materialized"),
            )
        )
