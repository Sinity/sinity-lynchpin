"""Xiaomi cloud vendor-witness lane (sinnix ``xiaomi-witness`` service).

Reads the append-only JSONL envelope lane at ``health/xiaomi-cloud/
xiaomi-cloud-YYYYMMDD.jsonl``, written every 30 minutes by the sinnix
``sinnix-xiaomi-witness`` timer: the Mi Band's data as Xiaomi's servers hold
it, fetched independently of the phone's Health Connect path. Envelope kinds:

* ``vendor_sleep`` / ``vendor_*_day`` — daily aggregates (sleep with
  sleep_score/REM/segments, HR/SpO2/steps/stress summaries);
* ``vendor_raw_*`` — the band's dense series (one envelope per key and UTC
  day: ~1/minute heart rate, continuous SpO2/stress, 5-minute
  steps/calories, and a per-night sleep object whose ``items[]`` is the
  complete minute-level stage transition list);
* ``vendor_fetch_failed`` — per-lane fetch failures, not data.

Sleep architecture is more complete here than in Health Connect, measured
2026-08-17: Mi Fitness stages the main night into HC (27–43 stage segments
of awake/deep/light/rem) but writes short daytime sessions as a single
``unknown`` block, while this lane stages every segment. An earlier
``vendor_sleep_details`` kind chased the same detail through Xiaomi's FDS
blob store and never landed one; it was removed once the raw plane was
found to carry the staging already.

Write-on-change semantics matter for readers: the writer re-fetches a
rolling window every pass and appends an envelope ONLY when the day's
content changed, so one (kind, day) appears once per revision, in append
order. ``latest_envelopes`` therefore keeps the LAST envelope per logical
key — the current state — while ``xiaomi_envelopes`` yields every revision
for consumers that treat the revision history itself as evidence.

The logical key is ``(kind, day)``; a day's several sleep segments — night
plus naps — ride inside one ``vendor_sleep`` envelope.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator, Optional

from ..core.config import LynchpinConfig
from ..core.parse import parse_datetime
from ..core.source import SourceReadiness, read_jsonl_with

__all__ = [
    "XiaomiEnvelope",
    "readiness",
    "xiaomi_envelopes",
    "latest_envelopes",
]

_DAY_FILE_RE = re.compile(r"xiaomi-cloud-(\d{8})\.jsonl\Z")


def _lane_root(root: Optional[Path] = None) -> Path:
    # ``health/`` since the 2026-08-17 subject recut moved this lane out of
    # ``captures/``. The presence map in core.config already pointed here
    # while this reader still looked at the old path, which is why the
    # coverage report's witness rows silently went to zero: an empty lane and
    # a moved lane are the same observation to a reader that only globs.
    return root or (LynchpinConfig.from_env().data_root / "health" / "xiaomi-cloud")


@dataclass(frozen=True)
class XiaomiEnvelope:
    """One witness envelope: a (kind, day) state at one fetch, or a failure."""

    kind: str
    day: Optional[date]
    fetched_at: Optional[datetime]
    parser_version: Optional[int]
    content_sha256: Optional[str]
    payload: dict[str, Any]

    @property
    def data(self) -> Any:
        return self.payload.get("data")

    @property
    def logical_key(self) -> tuple[str, Optional[date]]:
        """What ``latest_envelopes`` collapses revisions onto.

        (kind, day) is the whole key: the writer emits one envelope per kind
        per UTC day per revision, and a day's several sleep segments — night
        plus naps — ride inside one ``vendor_sleep`` envelope rather than
        appearing as separate rows.
        """
        return (self.kind, self.day)


def _parse_day(value: object) -> Optional[date]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _hydrate(payload: dict[str, Any]) -> Optional[XiaomiEnvelope]:
    kind = str(payload.get("kind") or "")
    if not kind:
        return None
    version = payload.get("parser_version")
    return XiaomiEnvelope(
        kind=kind,
        day=_parse_day(payload.get("day")),
        fetched_at=parse_datetime(payload.get("fetched_at")),
        parser_version=version if isinstance(version, int) else None,
        content_sha256=payload.get("content_sha256"),
        payload=payload,
    )


def _day_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    dated: list[tuple[str, Path]] = []
    for path in root.glob("xiaomi-cloud-*.jsonl"):
        match = _DAY_FILE_RE.search(path.name)
        if match is None:
            continue
        dated.append((match.group(1), path))
    return [path for _stamp, path in sorted(dated)]


def readiness(root: Optional[Path] = None) -> SourceReadiness:
    base = _lane_root(root)
    if not base.exists():
        return SourceReadiness(
            status="missing", reason=f"{base} does not exist", path=base, row_count=0,
        )
    files = _day_files(base)
    if not files:
        return SourceReadiness(
            status="empty", reason="lane present but no day files yet", path=base, row_count=0,
        )
    total = 0
    for path in files:
        try:
            with path.open(encoding="utf-8") as fh:
                total += sum(1 for line in fh if line.strip())
        except OSError:
            continue
    if total == 0:
        return SourceReadiness(
            status="empty", reason="day files present but no envelopes yet", path=base, row_count=0,
        )
    return SourceReadiness(status="ok", reason="", path=base, row_count=total)


def xiaomi_envelopes(*, root: Optional[Path] = None) -> Iterator[XiaomiEnvelope]:
    """Yield every envelope revision, in append order across day files."""
    for path in _day_files(_lane_root(root)):
        yield from read_jsonl_with(path, _hydrate, source_name=path.name)


def latest_envelopes(
    *,
    kinds: Optional[set[str]] = None,
    root: Optional[Path] = None,
) -> dict[tuple[str, Optional[date]], XiaomiEnvelope]:
    """Current state per logical key: the last-appended revision wins."""
    latest: dict[tuple[str, Optional[date]], XiaomiEnvelope] = {}
    for envelope in xiaomi_envelopes(root=root):
        if envelope.kind == "vendor_fetch_failed":
            continue
        if kinds is not None and envelope.kind not in kinds:
            continue
        latest[envelope.logical_key] = envelope
    return latest
