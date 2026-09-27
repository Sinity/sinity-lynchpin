"""Bounded reads of owner-native notes and AgentCTL job details."""

from __future__ import annotations

from typing import Any

from lynchpin.sources.agentctl import AgentctlObservationError, read_job_detail
from lynchpin.sources.exports_dendron import read_note, search_notes


def notes(*, view: str = "search", query: str = "", path: str | None = None,
          offset: int = 0, limit: int = 100) -> dict[str, Any]:
    try:
        if view == "search":
            return search_notes(query, offset=offset, limit=limit)
        if view == "read" and path:
            return read_note(path)
    except (ValueError, FileNotFoundError, OSError, UnicodeError) as error:
        return {"error": type(error).__name__, "message": str(error), "source": "dendron"}
    return {"error": "invalid_request", "message": "view must be search or read with path", "source": "dendron"}


def agentctl_job(*, job_id: str) -> dict[str, Any]:
    try:
        return read_job_detail(job_id)
    except (AgentctlObservationError, LookupError) as error:
        return {"error": type(error).__name__, "message": str(error), "source": "agentctl"}
