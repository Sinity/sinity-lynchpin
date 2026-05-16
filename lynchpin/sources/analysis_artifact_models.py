from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class AnalysisArtifact:
    name: str
    path: Path
    kind: str
    projects: tuple[str, ...]
    size_bytes: int
    modified_at: datetime
    generated_at: datetime | None
    top_level_keys: tuple[str, ...]
    brief: str | None
    references: tuple[str, ...]
    status: str
    reason: str | None = None

    @property
    def date(self) -> date:
        return self.modified_at.date()

    @property
    def project(self) -> str | None:
        return self.projects[0] if len(self.projects) == 1 else None


@dataclass(frozen=True)
class AnalysisClaim:
    id: str
    artifact_name: str
    claim_type: str
    project: str
    summary: str
    payload: dict[str, Any]
    confidence: float
    generated_at: datetime | None


ClaimExtractor = Callable[..., tuple[AnalysisClaim, ...]]
