"""Source selection and readiness status helpers for substrate promotion."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from lynchpin.core.source_contracts import PROMOTION_STAGE_NAMES

# Source identifiers used in substrate_source_status.source. Keep these as a
# stable vocabulary for MCP/readiness consumers.
SOURCE_COMMITS = "commits"
SOURCE_FILE_CHANGES = "file_changes"
SOURCE_SYMBOLS = "symbols"
SOURCE_AI_WORK_EVENTS = "ai_work_events"
SOURCE_POLYLOGUE_TIMELINE = "polylogue_timeline"
SOURCE_EVIDENCE_GRAPH = "evidence_graph"
SOURCE_PR_REVIEW = "pr_review"
SOURCE_SPOTIFY_DAILY = "spotify_daily"
SOURCE_PERSONAL_DAILY_SIGNAL = "personal_daily_signal"
SOURCE_TITLE_CLASSIFICATION = "title_classification"
SOURCE_ACTIVITY_CONTENT = "activity_content"
SOURCE_MACHINE = "machine"
SOURCE_MACHINE_GPU = "machine_gpu_sample"
SOURCE_MACHINE_NETWORK = "machine_network_sample"
SOURCE_MACHINE_SERVICE_STATE = "machine_service_state"
SOURCE_MACHINE_EXPERIMENTS = "machine_experiments"
SOURCE_SINNIX_GENERATION = "sinnix_generation"
SOURCE_BORG_DRILL = "borg_drill_run"
SOURCE_WORK_OBSERVATIONS = "work_observations"

ALL_SOURCE_IDS = frozenset(PROMOTION_STAGE_NAMES)

MACHINE_SOURCE_IDS = frozenset(
    {
        SOURCE_MACHINE,
        SOURCE_MACHINE_GPU,
        SOURCE_MACHINE_NETWORK,
        SOURCE_MACHINE_SERVICE_STATE,
        SOURCE_MACHINE_EXPERIMENTS,
    }
)


@dataclass(frozen=True)
class SourceSelection:
    """Promoter source selection with no skipped-status side effects."""

    sources: frozenset[str] | None = None

    @classmethod
    def from_collection(cls, sources: Collection[str] | None) -> SourceSelection:
        if sources is None:
            return cls()
        selected = frozenset(sources)
        unknown = selected - ALL_SOURCE_IDS
        if unknown:
            raise ValueError(
                "unknown substrate promote source(s): "
                + ", ".join(sorted(unknown))
            )
        return cls(selected)

    def includes(self, *names: str) -> bool:
        return self.sources is None or any(name in self.sources for name in names)


def record_source_status(
    conn: Any,
    *,
    refresh_id: str,
    source: str,
    status: str,
    reason: str | None,
    row_count: int,
    kind: str = "stage",
    window_start: date | None = None,
    window_end: date | None = None,
) -> None:
    """Upsert a per-source status row into ``substrate_source_status``."""
    conn.execute(
        "DELETE FROM substrate_source_status WHERE refresh_id = ? AND source = ? AND kind = ?",
        [refresh_id, source, kind],
    )
    conn.execute(
        """
        INSERT INTO substrate_source_status
        (refresh_id, source, kind, status, reason, row_count, window_start, window_end, recorded_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            refresh_id,
            source,
            kind,
            status,
            reason,
            int(row_count),
            window_start,
            window_end,
            datetime.now(timezone.utc),
        ],
    )
