"""Knowledgebase raw-log source.

The raw-log is a prompt-facing evidence stream: short timestamped thoughts
captured by the user's hotkey workflow. Preserve the text verbatim; callers can
decide later whether to summarize or quote.

Includes structured extractors for substance entries (dose + substance) and
subjective entries (intent, reflection, observation, decision).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterator, Literal, Optional

from ..core.config import get_config
from ..core.parse import as_local

__all__ = [
    "RawLogEntry",
    "SubstanceEntry",
    "SubjectiveEntry",
    "entries",
    "entries_in_range",
    "substance_entries",
    "subjective_entries",
]


_ENTRY_RE = re.compile(r"^-\s+\*\*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\*\*\s*(.*)$")


@dataclass(frozen=True)
class RawLogEntry:
    timestamp: datetime
    text: str
    source_path: str
    line_no: int

    @property
    def date(self) -> date:
        return self.timestamp.date()


@dataclass(frozen=True)
class SubstanceEntry:
    """Extracted substance dose from raw-log entry."""

    ts: datetime
    substance: str  # normalized identifier, e.g. "caffeine" -- see _KNOWN_SUBSTANCES
    dose_mg: Optional[float]
    raw_text: str
    source_line_no: int

    @property
    def date(self) -> date:
        return self.ts.date()


@dataclass(frozen=True)
class SubjectiveEntry:
    """Classified subjective entry from raw-log."""

    ts: datetime
    body: str
    kind: Literal["intent", "reflection", "observation", "decision", "other"]
    raw_text: str
    source_line_no: int

    @property
    def date(self) -> date:
        return self.ts.date()


def _path(path: Optional[Path] = None) -> Path:
    return path or get_config().raw_log_file


def entries(*, path: Optional[Path] = None) -> Iterator[RawLogEntry]:
    source = _path(path)
    if not source.exists():
        return
    with source.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            match = _ENTRY_RE.match(line.rstrip("\n"))
            if not match:
                continue
            try:
                ts = as_local(datetime.fromisoformat(match.group(1)))
            except ValueError:
                continue
            yield RawLogEntry(
                timestamp=ts,
                text=match.group(2),
                source_path=str(source),
                line_no=line_no,
            )


def entries_in_range(*, start: date, end: date, path: Optional[Path] = None) -> list[RawLogEntry]:
    return [entry for entry in entries(path=path) if start <= entry.date <= end]


# Known substance identifiers (lowercase). Only the near-universal, legal,
# minimal-personal-signal ones are seeded here; which other substances
# someone logs doses for is personal, so that vocabulary is loaded from an
# optional external override -- see _load_known_substances, same pattern as
# life_phase.py's KNOWN_EVENTS and substance_kinetics.py's HALF_LIVES_HOURS.
_GENERIC_SUBSTANCES = {"caffeine", "coffee", "tea"}


def _load_known_substances() -> set[str]:
    merged = set(_GENERIC_SUBSTANCES)
    path = get_config().derived_root / "local-config" / "substance_vocabulary.json"
    try:
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                merged.update(str(name).lower() for name in raw)
    except (OSError, json.JSONDecodeError):
        pass
    return merged


_KNOWN_SUBSTANCES = _load_known_substances()


def _extract_substance_from_entry(entry: RawLogEntry) -> Optional[SubstanceEntry]:
    """Extract substance dose from a raw-log entry if present.

    Regex patterns:
    - "123 mg substance" or "123mg substance"
    - "substance 123 mg"
    - "123.5 µg substance"

    Returns SubstanceEntry if a recognized substance + dose are found, else None.
    """
    text_lower = entry.text.lower()

    # Pattern 1: <dose> <unit> <substance>
    match1 = re.search(r"(\d+(?:\.\d+)?)\s*(mg|µg|ug)\s+(\w[\w\d\-]*)", text_lower)
    if match1:
        dose_str, unit, substance = match1.groups()
        # Normalize unit to mg (rough: 1µg ≈ 0.001mg, but keep as-is for now)
        if unit in ("µg", "ug"):
            dose_mg = float(dose_str) / 1000.0
        else:
            dose_mg = float(dose_str)

        if substance in _KNOWN_SUBSTANCES or any(
            substance.startswith(known) for known in _KNOWN_SUBSTANCES
        ):
            return SubstanceEntry(
                ts=entry.timestamp,
                substance=substance,
                dose_mg=dose_mg,
                raw_text=entry.text,
                source_line_no=entry.line_no,
            )

    # Pattern 2: <substance> <dose> <unit>
    match2 = re.search(r"(\w[\w\d\-]*)\s+(\d+(?:\.\d+)?)\s*(mg|µg|ug)", text_lower)
    if match2:
        substance, dose_str, unit = match2.groups()
        if substance in _KNOWN_SUBSTANCES:
            if unit in ("µg", "ug"):
                dose_mg = float(dose_str) / 1000.0
            else:
                dose_mg = float(dose_str)
            return SubstanceEntry(
                ts=entry.timestamp,
                substance=substance,
                dose_mg=dose_mg,
                raw_text=entry.text,
                source_line_no=entry.line_no,
            )

    return None


def substance_entries(
    start: Optional[date] = None, end: Optional[date] = None, *, path: Optional[Path] = None
) -> Iterator[SubstanceEntry]:
    """Extract substance entries (dose + substance) from raw-log.

    Yields SubstanceEntry for each entry that matches known substances + dose patterns.
    """
    for entry in entries(path=path):
        if start is not None and entry.date < start:
            continue
        if end is not None and entry.date > end:
            continue

        substance_entry = _extract_substance_from_entry(entry)
        if substance_entry is not None:
            yield substance_entry


def _classify_subjective_entry(entry: RawLogEntry) -> Literal[
    "intent", "reflection", "observation", "decision", "other"
]:
    """Classify a raw-log entry by lexical patterns.

    - intent: "want to", "should", "need to", "will", "going to", "have to"
    - reflection: "I realized", "I think", "noticed that", "feel like", "seems"
    - observation: starts with "on the screen", "actually", "turns out", "looks like"
    - decision: "decided to", "going with", "chose to", "will use"
    - other: default fallback
    """
    text_lower = entry.text.lower()

    # Check in order of specificity
    if re.search(r"\b(decided to|going with|chose to|will use|will go with)\b", text_lower):
        return "decision"

    if re.search(r"\b(want to|should|need to|will|going to|have to|must)\b", text_lower):
        return "intent"

    if re.search(
        r"\b(i realized|i think|noticed that|feel like|seems|appears|looks like)\b",
        text_lower,
    ):
        return "reflection"

    if re.search(
        r"^(on the screen|actually|turns out|looks like|it turns out|the|a |note:)",
        text_lower,
    ):
        return "observation"

    return "other"


def subjective_entries(
    start: Optional[date] = None, end: Optional[date] = None, *, path: Optional[Path] = None
) -> Iterator[SubjectiveEntry]:
    """Classify and yield subjective entries from raw-log.

    Yields SubjectiveEntry for each entry that is not a substance entry,
    classified by lexical patterns into: intent, reflection, observation, decision, other.
    """
    for entry in entries(path=path):
        if start is not None and entry.date < start:
            continue
        if end is not None and entry.date > end:
            continue

        # Skip if it's a substance entry
        if _extract_substance_from_entry(entry) is not None:
            continue

        kind = _classify_subjective_entry(entry)
        yield SubjectiveEntry(
            ts=entry.timestamp,
            body=entry.text,
            kind=kind,
            raw_text=entry.text,
            source_line_no=entry.line_no,
        )
