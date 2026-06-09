"""Shared helpers for lynchpin source readers.

Two helpers consolidate patterns that were repeated across
``lynchpin/sources/*.py``:

1. :class:`SourceReadiness` — the (status, reason, path, row_count)
   tuple every source returned via a per-source dataclass. Four
   near-identical copies existed (machine, polylogue, sinnix_generations,
   borg_drill); now there is one.

2. :func:`read_jsonl_with` — open a JSONL file, parse line by line,
   skip malformed lines with a warning, hydrate each record via a
   caller-supplied function. Replaces the open-iterate-loads-warn
   block duplicated across 7 source files.

These are general enough that any future JSONL-backed source should
use them instead of rolling its own.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date as _date
from pathlib import Path
from typing import Callable, Iterator, Optional, Protocol, TypeVar, runtime_checkable

logger = logging.getLogger(__name__)

T = TypeVar("T")


@runtime_checkable
class DayActivity(Protocol):
    """Structural protocol satisfied by all per-day activity dataclasses.

    Every source module's *DayActivity dataclass implicitly satisfies this
    protocol — no changes to existing dataclasses are needed.

    Naming convention for daily_activity functions:
    - Signature: daily_activity(*, start: date, end: date, ensure: bool = True) -> list[DayActivity]
    - Variant names (daily_browsing, daily_listening, etc.) are acceptable when
      the source produces multiple per-day rows (e.g., git per-repo, polylogue per-provider).
    - ensure=True is the canonical default for sources with ingest products;
      sources reading live DBs directly may omit ensure.
    """

    date: _date


@dataclass(frozen=True)
class SourceReadiness:
    """Uniform readiness report across all lynchpin source readers.

    Status values agreed across the existing source layer:

    - ``"ok"``        — file present, at least one valid row.
    - ``"empty"``     — file present, zero valid rows.
    - ``"missing"``   — file does not exist (often valid if optional).
    - ``"error"``     — file exists but could not be opened/read.
    """
    status: str
    reason: str
    path: Path
    row_count: int


def file_readiness(path: Path) -> SourceReadiness:
    """Compute readiness by counting non-blank lines in a JSONL file.

    Common shape for append-only JSONL sources such as sinnix generations
    and borg drill runs. Counts non-blank lines as a proxy for row count;
    the caller can refine if hydration filters further.
    """
    if not path.exists():
        return SourceReadiness(
            status="missing",
            reason=f"{path} does not exist",
            path=path,
            row_count=0,
        )
    try:
        with path.open(encoding="utf-8") as fh:
            count = sum(1 for line in fh if line.strip())
    except OSError as exc:
        return SourceReadiness(
            status="error",
            reason=f"could not read {path}: {exc}",
            path=path,
            row_count=0,
        )
    if count == 0:
        return SourceReadiness(
            status="empty",
            reason="file present but no rows yet",
            path=path,
            row_count=0,
        )
    return SourceReadiness(
        status="ok",
        reason="",
        path=path,
        row_count=count,
    )


def read_jsonl_with(
    path: Path,
    hydrate: Callable[[dict], Optional[T]],
    *,
    source_name: str | None = None,
) -> Iterator[T]:
    """Stream typed records from a JSONL file.

    Each non-blank line is parsed as JSON; the result is passed to
    ``hydrate``. If the line is unparseable JSON, ``hydrate`` returns
    ``None``, or ``hydrate`` itself raises, the line is skipped with
    a logged warning rather than aborting the read. This matches the
    append-only contract for files that may carry a partial last line
    (interrupted reboot mid-write) without blocking consumption of
    valid lines around it.

    ``source_name`` is included in the warning messages; defaults to
    the file basename.
    """
    if not path.exists():
        return
    name = source_name or path.name
    with path.open(encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                logger.warning("%s: line %d unparseable JSON: %s", name, lineno, exc)
                continue
            if not isinstance(payload, dict):
                logger.warning("%s: line %d JSON is not an object", name, lineno)
                continue
            try:
                record = hydrate(payload)
            except Exception as exc:  # noqa: BLE001 — log all hydration errors
                logger.warning("%s: line %d hydrate failed: %s", name, lineno, exc)
                continue
            if record is None:
                continue
            yield record
