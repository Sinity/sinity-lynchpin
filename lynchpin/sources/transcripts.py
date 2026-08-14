"""Speech transcripts over phone/desktop audio captures.

Reads the per-day transcript log at ``captures/transcripts/YYYY-MM-DD.jsonl``
(schema ``sinnix.transcript/1``: ``file``, ``lane``, ``engine``,
``audio_seconds``, ``speech_seconds``, ``segments[]`` with ``start``/``end``/
``text``, ``rtf``, ``undecodable``, ``error``, ``transcribed_at``, ``bytes``)
plus the ``transcribed.jsonl`` completion ledger in the same directory (a
lighter per-file record used to track what has already been transcribed:
``file``, ``bytes``, ``lane``, ``speech_seconds``, ``at`` — no text/segments).

Real-data quirk (confirmed against the live files, 2026-08-14: 547 daily-log
rows across 2026-08-12..14 / 338 distinct source chunks; the ledger separately
carries 545 rows / 337 distinct chunks): BOTH the daily log and the ledger MAY
carry more than one row for the same ``file`` — a re-transcription re-appends
rather than replacing, the same append-only pattern as
``phone_ambient.ambient-levels.jsonl`` (209 of 547 daily-log rows are repeat
``file`` entries). The newest record per file wins.

Date semantics: ``file`` encodes the SOURCE CHUNK's own recording timestamp
(``ambient-20260813T130526Z.m4a``, same compact stamp as
``sources.phone_ambient``), which is what ``Transcript.date`` uses — it is
usually well before ``transcribed_at`` (the day the transcription pipeline
actually ran; every 2026-08-12 chunk in the live data was transcribed on
2026-08-14). A consumer wanting "when was this transcribed" should read
``transcribed_at`` directly rather than ``.date``.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from ..core.config import LynchpinConfig
from ..core.parse import as_local, parse_datetime as _parse_dt, safe_float, safe_int
from ..core.source import SourceReadiness, read_jsonl_with

__all__ = [
    "TranscriptSegment",
    "Transcript",
    "TranscriptionLedgerEntry",
    "readiness",
    "transcripts",
    "transcription_ledger",
    "daily_speech",
]

_DAY_FILE_RE = re.compile(r"\A\d{4}-\d{2}-\d{2}\.jsonl\Z")
_CHUNK_TS_RE = re.compile(r"-(\d{8}T\d{6})Z")


def _chunk_recorded_at(filename: str) -> Optional[datetime]:
    match = _CHUNK_TS_RE.search(filename)
    if match is None:
        return None
    try:
        naive_utc = datetime.strptime(match.group(1), "%Y%m%dT%H%M%S")
    except ValueError:
        return None
    return as_local(naive_utc.replace(tzinfo=timezone.utc))


@dataclass(frozen=True)
class TranscriptSegment:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Transcript:
    file: str
    lane: str
    engine: Optional[str]
    audio_seconds: Optional[float]
    speech_seconds: Optional[float]
    rtf: Optional[float]
    text: str
    segments: tuple[TranscriptSegment, ...]
    undecodable: bool
    error: Optional[str]
    transcribed_at: Optional[datetime]
    bytes: Optional[int]

    @property
    def recorded_at(self) -> Optional[datetime]:
        return _chunk_recorded_at(self.file)

    @property
    def date(self) -> Optional[date]:
        """The source chunk's own recording date, not the transcription date."""
        recorded = self.recorded_at
        if recorded is not None:
            return recorded.date()
        return self.transcribed_at.date() if self.transcribed_at is not None else None


@dataclass(frozen=True)
class TranscriptionLedgerEntry:
    file: str
    bytes: Optional[int]
    lane: str
    speech_seconds: Optional[float]
    at: Optional[datetime]

    @property
    def date(self) -> Optional[date]:
        recorded = _chunk_recorded_at(self.file)
        if recorded is not None:
            return recorded.date()
        return self.at.date() if self.at is not None else None


@dataclass(frozen=True)
class DailySpeech:
    """Per-day rollup, bucketed by the source chunks' own recording date."""

    date: date
    chunk_count: int
    undecodable_count: int
    speech_seconds_sum: float
    audio_seconds_sum: float


def _day_files(root: Path, start: Optional[date], end: Optional[date]) -> list[Path]:
    if not root.exists():
        return []
    dated: list[tuple[date, Path]] = []
    for path in root.glob("*.jsonl"):
        if not _DAY_FILE_RE.match(path.name):
            continue
        try:
            file_date = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if start is not None and file_date < start:
            continue
        if end is not None and file_date > end:
            continue
        dated.append((file_date, path))
    return [path for _day, path in sorted(dated)]


def readiness(root: Path | None = None) -> SourceReadiness:
    """Aggregate readiness across every ``YYYY-MM-DD.jsonl`` transcript log."""
    base = root or LynchpinConfig.from_env().transcripts_dir
    if not base.exists():
        return SourceReadiness(
            status="missing", reason=f"{base} does not exist", path=base, row_count=0,
        )
    files = _day_files(base, None, None)
    if not files:
        return SourceReadiness(
            status="empty", reason="directory present but no day-log files yet",
            path=base, row_count=0,
        )
    total = 0
    for f in files:
        try:
            with f.open(encoding="utf-8") as fh:
                total += sum(1 for line in fh if line.strip())
        except OSError:
            continue
    if total == 0:
        return SourceReadiness(
            status="empty", reason="day-log files present but no rows yet",
            path=base, row_count=0,
        )
    return SourceReadiness(status="ok", reason="", path=base, row_count=total)


def _hydrate_transcript(payload: dict[str, Any]) -> Optional[Transcript]:
    file_name = payload.get("file")
    if not file_name:
        return None
    segments = tuple(
        TranscriptSegment(
            start=safe_float(seg.get("start")) or 0.0,
            end=safe_float(seg.get("end")) or 0.0,
            text=str(seg.get("text") or ""),
        )
        for seg in (payload.get("segments") or [])
        if isinstance(seg, dict)
    )
    return Transcript(
        file=str(file_name),
        lane=str(payload.get("lane") or ""),
        engine=payload.get("engine"),
        audio_seconds=safe_float(payload.get("audio_seconds")),
        speech_seconds=safe_float(payload.get("speech_seconds")),
        rtf=safe_float(payload.get("rtf")),
        text=str(payload.get("text") or ""),
        segments=segments,
        undecodable=bool(payload.get("undecodable")),
        error=payload.get("error"),
        transcribed_at=_parse_dt(payload.get("transcribed_at")),
        bytes=safe_int(payload.get("bytes")),
    )


def transcripts(
    *,
    start: Optional[date] = None,
    end: Optional[date] = None,
    root: Optional[Path] = None,
) -> Iterator[Transcript]:
    """Yield one deduped ``Transcript`` per source chunk, oldest first.

    Dedup keeps the LAST record seen per ``file`` across all day-log files in
    range (a re-transcription re-appends rather than replacing — see module
    docstring). ``start``/``end`` bound the chunk's own recording date, NOT
    the day-log filename it happens to live in.
    """
    base = root or LynchpinConfig.from_env().transcripts_dir
    latest: dict[str, Transcript] = {}
    for path in _day_files(base, None, None):
        for record in read_jsonl_with(path, _hydrate_transcript, source_name=path.name):
            latest[record.file] = record
    for record in sorted(latest.values(), key=lambda t: t.file):
        day = record.date
        if start is not None and (day is None or day < start):
            continue
        if end is not None and (day is None or day > end):
            continue
        yield record


def _hydrate_ledger_entry(payload: dict[str, Any]) -> Optional[TranscriptionLedgerEntry]:
    file_name = payload.get("file")
    if not file_name:
        return None
    return TranscriptionLedgerEntry(
        file=str(file_name),
        bytes=safe_int(payload.get("bytes")),
        lane=str(payload.get("lane") or ""),
        speech_seconds=safe_float(payload.get("speech_seconds")),
        at=_parse_dt(payload.get("at")),
    )


def transcription_ledger(
    *,
    start: Optional[date] = None,
    end: Optional[date] = None,
    path: Optional[Path] = None,
) -> Iterator[TranscriptionLedgerEntry]:
    """Yield one deduped ``TranscriptionLedgerEntry`` per chunk, oldest first."""
    jsonl = path or LynchpinConfig.from_env().transcribed_ledger_jsonl
    latest: dict[str, TranscriptionLedgerEntry] = {}
    for entry in read_jsonl_with(jsonl, _hydrate_ledger_entry, source_name="transcribed"):
        latest[entry.file] = entry
    for entry in sorted(latest.values(), key=lambda e: e.file):
        day = entry.date
        if start is not None and (day is None or day < start):
            continue
        if end is not None and (day is None or day > end):
            continue
        yield entry


def daily_speech(
    *,
    start: Optional[date] = None,
    end: Optional[date] = None,
    root: Optional[Path] = None,
) -> list[DailySpeech]:
    """Per-day chunk/speech-time rollup. A day with no transcribed chunk is absent."""
    buckets: dict[date, list[Transcript]] = defaultdict(list)
    for record in transcripts(start=start, end=end, root=root):
        if record.date is None:
            continue
        buckets[record.date].append(record)

    out: list[DailySpeech] = []
    for day, records in sorted(buckets.items()):
        out.append(DailySpeech(
            date=day,
            chunk_count=len(records),
            undecodable_count=sum(1 for r in records if r.undecodable),
            speech_seconds_sum=round(sum(r.speech_seconds or 0.0 for r in records), 2),
            audio_seconds_sum=round(sum(r.audio_seconds or 0.0 for r in records), 2),
        ))
    return out
