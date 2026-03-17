from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, List, Optional

from ...core.cache import files_signature, persistent_cache
from ...core.config import get_config

if TYPE_CHECKING:
    from polylogue.lib.coverage import ArchiveCoverage
    from polylogue.lib.session_profile import SessionProfile

logger = logging.getLogger(__name__)


@dataclass
class PolylogueDocument:
    provider: str
    path: Path
    modified_at: datetime
    size_bytes: int

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "path": str(self.path),
            "modified_at": self.modified_at.isoformat(),
            "size_bytes": self.size_bytes,
        }


@dataclass
class PolylogueRun:
    run_id: str
    timestamp: datetime
    path: Path
    counts: dict
    drift: dict
    indexed: Optional[bool]
    index_error: Optional[str]
    duration_ms: Optional[int]

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "timestamp": self.timestamp.isoformat(),
            "path": str(self.path),
            "counts": self.counts,
            "drift": self.drift,
            "indexed": self.indexed,
            "index_error": self.index_error,
            "duration_ms": self.duration_ms,
        }


def _document_paths(provider: Optional[str]) -> List[Path]:
    cfg = get_config()
    root = cfg.polylogue_root
    if not root.exists():
        return []
    if provider:
        candidates = [root / provider]
    else:
        candidates = [child for child in root.iterdir() if child.is_dir()]
    files: List[Path] = []
    for provider_dir in candidates:
        if provider_dir.exists() and provider_dir.is_dir():
            files.extend(sorted(provider_dir.rglob("*.md")))
    return files


def _run_paths() -> List[Path]:
    cfg = get_config()
    root = cfg.polylogue_archive_root / "runs"
    if not root.exists():
        return []
    return sorted(root.rglob("run-*.json"))


def _provider_key(provider: Optional[str]) -> str:
    return provider or "__all__"


@dataclass
class _PolylogueCacheRow:
    provider: str
    path: str
    modified_at: datetime
    size_bytes: int


@dataclass
class _PolylogueRunRow:
    run_id: str
    timestamp: datetime
    path: str
    counts_json: str
    drift_json: str
    indexed: Optional[bool]
    index_error: Optional[str]
    duration_ms: Optional[int]


@persistent_cache(
    "polylogue_documents",
    depends_on=lambda provider=None: (_provider_key(provider), files_signature(_document_paths(provider))),
)
def _load_documents(provider: Optional[str]) -> List[_PolylogueCacheRow]:
    cfg = get_config()
    root = cfg.polylogue_root
    rows: List[_PolylogueCacheRow] = []
    for path in _document_paths(provider):
        if not path.is_file():
            continue
        try:
            provider_name = path.relative_to(root).parts[0]
        except ValueError:
            provider_name = path.parent.name
        stat = path.stat()
        rows.append(
            _PolylogueCacheRow(
                provider=provider_name,
                path=str(path),
                modified_at=datetime.fromtimestamp(stat.st_mtime),
                size_bytes=stat.st_size,
            )
        )
    return rows


@persistent_cache(
    "polylogue_runs",
    depends_on=lambda: files_signature(_run_paths()),
)
def _load_runs() -> List[_PolylogueRunRow]:
    rows: List[_PolylogueRunRow] = []
    for path in _run_paths():
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        run_id = str(payload.get("run_id") or path.stem)
        ts_raw = payload.get("timestamp")
        ts_value: float
        if isinstance(ts_raw, (int, float)):
            ts_value = float(ts_raw)
        else:
            ts_value = path.stat().st_mtime
        timestamp = datetime.fromtimestamp(ts_value, tz=timezone.utc)
        counts = payload.get("counts") if isinstance(payload.get("counts"), dict) else {}
        drift = payload.get("drift") if isinstance(payload.get("drift"), dict) else {}
        rows.append(
            _PolylogueRunRow(
                run_id=run_id,
                timestamp=timestamp,
                path=str(path),
                counts_json=json.dumps(counts, sort_keys=True),
                drift_json=json.dumps(drift, sort_keys=True),
                indexed=payload.get("indexed") if isinstance(payload.get("indexed"), bool) else None,
                index_error=payload.get("index_error") if isinstance(payload.get("index_error"), str) else None,
                duration_ms=payload.get("duration_ms") if isinstance(payload.get("duration_ms"), int) else None,
            )
        )
    rows.sort(key=lambda row: row.timestamp)
    return rows


def iter_documents(provider: Optional[str] = None) -> Iterator[PolylogueDocument]:
    for row in _load_documents(provider):
        yield PolylogueDocument(
            provider=row.provider,
            path=Path(row.path),
            modified_at=row.modified_at,
            size_bytes=row.size_bytes,
        )


def iter_runs() -> Iterator[PolylogueRun]:
    for row in _load_runs():
        yield PolylogueRun(
            run_id=row.run_id,
            timestamp=row.timestamp,
            path=Path(row.path),
            counts=json.loads(row.counts_json),
            drift=json.loads(row.drift_json),
            indexed=row.indexed,
            index_error=row.index_error,
            duration_ms=row.duration_ms,
        )


def iter_session_profiles(
    *,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    provider: Optional[str] = None,
) -> Iterator[SessionProfile]:
    """Yield rich SessionProfile objects from the Polylogue archive.

    Uses SyncPolylogue to query the archive database, then builds
    a SessionProfile for each conversation in the time window.
    """
    from polylogue.lib.session_profile import build_session_profile
    from polylogue.sync import SyncPolylogue

    try:
        poly = SyncPolylogue()
    except Exception:
        logger.warning("Failed to initialize SyncPolylogue — no session profiles", exc_info=True)
        return

    try:
        filt = poly.filter()
        if provider:
            filt = filt.provider(provider)
        if start:
            filt = filt.since(start.isoformat())
        if end:
            filt = filt.until(end.isoformat())

        from polylogue.sync import _run

        summaries = _run(filt.list_summaries())

        # Fetch full conversations in batches for session profiling
        batch_size = 20
        ids = [str(s.id) for s in summaries]
        for i in range(0, len(ids), batch_size):
            batch_ids = ids[i : i + batch_size]
            conversations = poly.get_conversations(batch_ids)
            for conv in conversations:
                try:
                    yield build_session_profile(conv)
                except Exception:
                    logger.debug("Failed to build session profile for %s", conv.id, exc_info=True)
    finally:
        poly.close()


def get_coverage() -> ArchiveCoverage:
    """Get archive coverage diagnostics from Polylogue."""
    from polylogue.lib.coverage import analyze_coverage
    from polylogue.sync import SyncPolylogue

    poly = SyncPolylogue()
    try:
        summaries = poly.list_summaries()
        return analyze_coverage(summaries)
    finally:
        poly.close()
