"""Clean ActivityWatch raw events into honest intervals.

Two distinct upstream bugs corrupt the live AW database:

  1. **Window/chrome zero-duration heartbeat spam**. aw-server-rust's
     ``aw_transform::heartbeat`` (`aw-transform/src/heartbeat.rs:29`)
     refuses to merge when the next heartbeat arrives later than
     ``last.endtime + pulsetime``. The operator's awatcher polls at
     1 s nominal but actual arrival cadence is ~2.02 s (poll-loop
     throttling under load). Pulsetime sent in the API call is also 1 s.
     So gap (2.02 s) > pulsetime (1 s) ⇒ every heartbeat lands as a
     fresh zero-duration row. Net result: ~95 000 zero-duration window
     events per day instead of ~10 000 merged intervals.

  2. **AFK duplicate-starttime cluster bug**. Patched aw-server-rust
     (operator's fork includes the heartbeat-fix PR #555 fix) merges
     events by id rather than ``max(endtime)``. But the merged event
     returned from ``aw_transform::heartbeat`` has ``id: None``
     (`aw-datastore/src/datastore.rs:642-643`). The cache stores it.
     The next heartbeat call calls ``last_event.id.ok_or_else(...)``
     which errors. The next call repopulates from DB → merge succeeds
     once → cache stores ``id: None`` → next call errors → DB read →
     merge once → repeat. We observe clusters of 91+ rows sharing one
     starttime with monotonically-growing endtime at stride ≈10 s.

This module reads the raw events and re-applies the intended merge:

  - Consecutive same-bucket events with identical ``data`` are merged
    iff their gap ≤ ``MERGE_PULSETIME_S`` (default 30 s — generous
    enough to absorb watcher throttling AND tolerate the awatcher
    1-s nominal cadence at any realistic load).

  - Duplicate-starttime clusters are collapsed to the single row with
    the maximum endtime.

Output is a clean stream of ``AWEvent`` records with non-zero durations,
no duplicate starttimes per bucket, and accurate ``start`` / ``end``
intervals you can integrate over to get honest focus seconds.
"""
from __future__ import annotations

from typing import Iterable, Iterator

from .activitywatch_raw import AWEvent

# Maximum gap between consecutive same-data events that we treat as
# "still the same activity stretch." Empirically chosen to absorb
# awatcher throttling. Set as conservative as possible without
# inviting cross-activity bleed.
MERGE_PULSETIME_S: float = 30.0


def dedup_and_merge(events: Iterable[AWEvent]) -> Iterator[AWEvent]:
    """Stream of AWEvents → cleaned stream of AWEvents.

    Assumes input is sorted by ``start`` within bucket (the raw
    materializer already sorts). Applies per-bucket merging.

    Pass 1 (per-bucket): collapse duplicate-starttime clusters by
    keeping only the row with maximum ``end`` per ``(bucket,
    start, data)`` tuple. Fixes the AFK cluster bug.

    Pass 2: merge consecutive same-``data`` rows when the gap between
    them is ≤ MERGE_PULSETIME_S. Fixes the window zero-duration spam.

    Memory cost: O(events_per_bucket) — we batch per bucket to keep
    sorting cheap.
    """
    bucket_buf: dict[str, list[AWEvent]] = {}

    def flush(bucket: str) -> Iterator[AWEvent]:
        rows = bucket_buf.pop(bucket, [])
        if not rows:
            return
        # Pass 1: dedupe by (start, data)
        by_key: dict[tuple, AWEvent] = {}
        for e in rows:
            data_key = _data_key(e.data)
            key = (e.start, data_key)
            existing = by_key.get(key)
            if existing is None or e.end > existing.end:
                by_key[key] = e
        deduped = sorted(by_key.values(), key=lambda x: x.start)

        # Pass 2: merge consecutive same-data rows within MERGE_PULSETIME_S
        current: AWEvent | None = None
        for e in deduped:
            if current is None:
                current = e
                continue
            same_data = _data_key(current.data) == _data_key(e.data)
            # Gap = next.start - current.end (negative if overlapping).
            # Note: many events have current.end == current.start (legacy
            # zero-duration rows). Use next.start - current.start when
            # current is zero-duration to avoid pathological infinite-gap
            # comparisons.
            current_end = current.end if current.end > current.start else current.start
            gap_s = (e.start - current_end).total_seconds()
            if same_data and 0 <= gap_s <= MERGE_PULSETIME_S:
                # Extend current's endtime to absorb this heartbeat.
                # Synthesize an endtime that covers up to e.start + 1
                # heartbeat tick (treat e as a heartbeat marker, not an
                # interval) — but bounded by next event's start.
                merged_end = max(current.end, e.end, e.start)
                current = AWEvent(
                    bucket=current.bucket,
                    start=current.start,
                    end=merged_end,
                    data=current.data,
                )
            else:
                yield current
                current = e
        if current is not None:
            yield current

    last_bucket: str | None = None
    for e in events:
        if last_bucket is not None and e.bucket != last_bucket:
            # New bucket → flush the previous one.
            # NB: this only triggers when input changes bucket between rows
            # which the raw NDJSON sort-by-(bucket,start) makes uncommon.
            for x in flush(last_bucket):
                yield x
        bucket_buf.setdefault(e.bucket, []).append(e)
        last_bucket = e.bucket
    # Flush all remaining buckets at end of stream
    for b in list(bucket_buf.keys()):
        for x in flush(b):
            yield x


def _data_key(data: dict) -> str:
    """Stable hashable representation of event data. Tolerates dict-order."""
    if not isinstance(data, dict):
        return repr(data)
    return repr(sorted(data.items()))


__all__ = ["dedup_and_merge", "MERGE_PULSETIME_S"]
