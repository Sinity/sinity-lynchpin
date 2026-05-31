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

    def __post_init__(self) -> None:
        if self.size_bytes < 0:
            raise ValueError(
                f"AnalysisArtifact.size_bytes ({self.size_bytes}) must be >= 0"
            )

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

    def __post_init__(self) -> None:
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"AnalysisClaim.confidence ({self.confidence}) must be in [0.0, 1.0]"
            )


ClaimExtractor = Callable[..., tuple[AnalysisClaim, ...]]
