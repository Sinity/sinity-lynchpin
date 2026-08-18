"""Sinnix phone app event stream.

Reads the append-only JSONL event log the phone app pushes to prime
(`Events.append`), one file per UTC day at
``machine/phone/events/events-YYYYMMDD.jsonl``. Every record shares
two common fields, ``kind`` and ``ts``, plus kind-specific payload fields.
See ``docs/phone.md`` in the sinnix repo for the full event catalogue
(instrument runs, marks, EMA answers, ambient light/motion samples, chunk
lifecycle, boot/toggle/attestation events, ...).

Timestamp quirk: records written before 2026-08-14 stamp ``ts`` (and
``started_at`` on instrument runs) in the COMPACT form the app uses for audio
chunk filenames (``20260814T002141Z``) rather than extended ISO-8601
(``2026-08-14T00:21:41Z``). ``core.parse.parse_datetime`` only accepts the
extended form (it round-trips through ``datetime.fromisoformat``), so a naive
read silently drops every pre-2026-08-14 record. ``_parse_phone_ts`` below
tries the extended form first and falls back to the compact one.

This module exposes two levels of typed access:

* ``phone_events`` — every record, generically, as ``PhoneEvent`` (kind + ts
  + date + the full raw payload). Any consumer that needs a kind not given a
  dedicated extractor here can filter this stream itself.
* ``instrument_runs`` / ``daily_instrument_metrics`` — the ``instrument_run``
  kind specifically, since it is the substrate for the psychometric
  correlation analysis. ``daily_instrument_metrics`` derives one row per
  (date, instrument) with a run count and a "primary metric" value.

Primary-metric caveat: the persisted ``instrument_run`` payload does NOT
carry a canonical "this is the headline number" field — that framing
(``Outcome.primaryLabel`` in the Kotlin source, ``ui/instrument/*Engine.kt``)
exists only in the on-screen result and is never written to the event.
``_primary_metric`` reconstructs a reader-side approximation. This is a
FALLBACK, not the mechanism of record: if the app is ever changed to persist
``primary_metric``/``primary_value`` directly on the event, this function
should defer to that field first and keep the table below only for old
records. As of this writing (2026-08-14), traced against
``ui/instrument/*Engine.kt`` and ``instruments/Catalogue.kt``:

======================  ========  ==================  ============================  ===============================
engine (canon)          instrument  Kotlin primaryLabel  field this reader picks       agreement
======================  ========  ==================  ============================  ===============================
reaction                pvt         median_rt_ms          median_rt_ms                  exact match
reaction                go_no_go    median_rt_ms          median_rt_ms                  exact match (same code path as pvt)
reaction                tapping     interval_sd_ms         interval_sd_ms                exact match (falls through to 2nd priority)
staircase               pitch_jnd   threshold              threshold                     exact match
staircase               gap_detection threshold            threshold                     exact match
staircase               (torch-feasibility preflight, not a catalogued instrument) achievable_hz  NOT PICKED — no "achievable_hz" in the priority list; falls through to None
forced_choice           stroop      median_correct_rt_ms OR interference_ms (dynamic)  accuracy   DELIBERATE DIVERGENCE — this reader reports accuracy (0-1), not the app's reaction-time-shaped headline, because the correlation this feeds wants an accuracy-style outcome; the app's actual primary is a different, also-persisted field
counting                breath_counting  cycles_correct (label only; value = 100 * cycles_correct/(cycles_correct+unaware_miscounts))  accuracy, derived as cycles_correct/(cycles_correct+unaware_miscounts)  DELIBERATE DIVERGENCE — same ratio, this reader keeps it on a 0-1 scale and calls it "accuracy" rather than the app's 0-100 "cycles_correct"-labelled percentage
counting                (prime-scored breath variant, "scored_by":"prime")  none (empty)  none (falls through to None; no accuracy fields on-device)  exact match (both absent)
hold_still              ppg, tremor  none (empty; scored via prime trace_file)  none    exact match (both absent)
======================  ========  ==================  ============================  ===============================

A run whose engine/fields don't match any of the above falls through with
``primary_metric_value=None``; the run still counts towards ``run_count``
(missing a metric is not the same as missing a run).
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
    "PhoneEvent",
    "InstrumentRun",
    "InstrumentDay",
    "readiness",
    "phone_events",
    "instrument_runs",
    "daily_instrument_metrics",
]

_DAY_FILE_RE = re.compile(r"events-(\d{8})\.jsonl\Z")
_COMPACT_TS_RE = re.compile(r"\A\d{8}T\d{6}Z\Z")

# Fields common to every instrument_run envelope (covariates, not metrics).
_INSTRUMENT_RUN_COMMON_FIELDS = frozenset({
    "kind", "instrument", "engine", "epoch", "started_at", "seconds",
    "hour_of_day", "energy_state", "lux_mean", "motion_rms", "headphones",
    "scored_on_device", "ts", "touch_offset_ms", "screen_interactive",
    "preflight_unmet",
})

# instrument.engine.name.lowercase() values. "count" is not a bug in the
# current app: the pre-rewrite Java BreathActivity hard-coded
# o.put("engine", "count"), and the only live "count" record predates the
# Kotlin rewrite (which writes "counting", matching Engine.COUNTING.name.
# lowercase()) by hours. The events plane is append-only and spans that
# rewrite, so both spellings are permanently present in the historical
# record — this alias is not a stopgap for a defect to later "fix away".
_ENGINE_ALIASES: dict[str, str] = {
    "count": "counting",
    "counting": "counting",
    "reaction": "reaction",
    "staircase": "staircase",
    "forced_choice": "forced_choice",
    "hold_still": "hold_still",
}

# Per-engine-family metric field priority: first present field wins.
_METRIC_PRIORITY: dict[str, tuple[str, ...]] = {
    "reaction": ("median_rt_ms", "interval_sd_ms", "interval_mean_ms"),
    "staircase": ("threshold",),
    "forced_choice": ("accuracy",),
}


def _parse_phone_ts(value: object) -> Optional[datetime]:
    """Parse a phone-event timestamp, extended ISO-8601 or compact stamp."""
    dt = _parse_dt(value)
    if dt is not None:
        return dt
    if value is None:
        return None
    text = str(value).strip()
    if not _COMPACT_TS_RE.match(text):
        return None
    try:
        naive_utc = datetime.strptime(text, "%Y%m%dT%H%M%SZ")
    except ValueError:
        return None
    return as_local(naive_utc.replace(tzinfo=timezone.utc))


@dataclass(frozen=True)
class PhoneEvent:
    """One record from the phone event stream, generically.

    ``payload`` is the full raw JSON object (including ``kind``/``ts``) so
    callers can pull kind-specific fields without a dedicated extractor.
    """

    kind: str
    ts: datetime
    date: date
    payload: dict[str, Any]


@dataclass(frozen=True)
class InstrumentRun:
    """One ``instrument_run`` event: covariates typed, metrics left raw.

    ``metrics`` holds every payload field that isn't a covariate (e.g.
    ``median_rt_ms``, ``threshold``, ``accuracy``, ``rt_ms``, ``lapses``,
    ``cycles_correct``, ``trace_file``, ...) — the instrument catalogue
    determines which fields a given engine writes, and hard-coding all of
    them here would drift the moment a new instrument or engine ships.
    """

    date: date
    instrument: str
    engine: str
    epoch: Optional[str]
    started_at: datetime
    seconds: int
    hour_of_day: Optional[int]
    energy_state: Optional[str]
    lux_mean: Optional[float]
    motion_rms: Optional[float]
    headphones: Optional[bool]
    scored_on_device: Optional[bool]
    metrics: dict[str, Any]


@dataclass(frozen=True)
class InstrumentDay:
    """Daily rollup for one instrument: run count + a primary-metric mean.

    Missing != zero: a (date, instrument) pair with no runs simply does not
    appear in ``daily_instrument_metrics``'s output — it is never synthesized
    as a zero-run or zero-metric row.
    """

    date: date
    instrument: str
    engine: str
    run_count: int
    primary_metric_name: Optional[str]
    primary_metric_value: Optional[float]  # mean across the day's runs


def _day_files(root: Path, start: Optional[date], end: Optional[date]) -> list[Path]:
    if not root.exists():
        return []
    dated: list[tuple[date, Path]] = []
    for path in root.glob("events-*.jsonl"):
        match = _DAY_FILE_RE.search(path.name)
        if match is None:
            continue
        try:
            file_date = datetime.strptime(match.group(1), "%Y%m%d").date()
        except ValueError:
            continue
        if start is not None and file_date < start:
            continue
        if end is not None and file_date > end:
            continue
        dated.append((file_date, path))
    return [path for _day, path in sorted(dated)]


def readiness(root: Path | None = None) -> SourceReadiness:
    """Aggregate readiness across every ``events-YYYYMMDD.jsonl`` day file."""
    base = root or LynchpinConfig.from_env().phone_events_dir
    if not base.exists():
        return SourceReadiness(
            status="missing", reason=f"{base} does not exist", path=base, row_count=0,
        )
    files = sorted(base.glob("events-*.jsonl"))
    if not files:
        return SourceReadiness(
            status="empty", reason="directory present but no day files yet",
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
            status="empty", reason="day files present but no rows yet",
            path=base, row_count=0,
        )
    return SourceReadiness(status="ok", reason="", path=base, row_count=total)


def _hydrate_event(payload: dict[str, Any]) -> Optional[PhoneEvent]:
    ts = _parse_phone_ts(payload.get("ts"))
    if ts is None:
        return None
    kind = str(payload.get("kind") or "")
    if not kind:
        return None
    return PhoneEvent(kind=kind, ts=ts, date=ts.date(), payload=payload)


def phone_events(
    *,
    start: Optional[date] = None,
    end: Optional[date] = None,
    kind: Optional[str] = None,
    root: Optional[Path] = None,
) -> Iterator[PhoneEvent]:
    """Yield every phone event, optionally filtered by kind and date.

    ``start``/``end`` bound the day-file name AND the parsed ``ts`` date, so
    a record whose ``ts`` falls just outside its filename's UTC day (rare,
    near midnight) is still filtered correctly on the parsed value.
    """
    base = root or LynchpinConfig.from_env().phone_events_dir
    for path in _day_files(base, start, end):
        # Health Connect re-exports carry a stable record identity; a forced
        # backfill used to re-emit the same record per sync (sinnix-jvt8), so
        # reads deduplicate on it defensively even though the writer now
        # gates on the same key. Scope is per day file, matching the writer.
        seen_records: set[tuple[Any, ...]] = set()
        for event in read_jsonl_with(path, _hydrate_event, source_name=path.name):
            record_id = event.payload.get("record_id")
            if record_id is not None:
                key = (
                    event.kind,
                    record_id,
                    event.payload.get("modified"),
                    event.payload.get("client_record_version"),
                )
                if key in seen_records:
                    continue
                seen_records.add(key)
            if kind is not None and event.kind != kind:
                continue
            if start is not None and event.date < start:
                continue
            if end is not None and event.date > end:
                continue
            yield event


def instrument_runs(
    *,
    start: Optional[date] = None,
    end: Optional[date] = None,
    root: Optional[Path] = None,
) -> Iterator[InstrumentRun]:
    """Yield typed ``instrument_run`` events."""
    for event in phone_events(start=start, end=end, kind="instrument_run", root=root):
        payload = event.payload
        started_at = _parse_phone_ts(payload.get("started_at")) or event.ts
        metrics = {
            k: v for k, v in payload.items() if k not in _INSTRUMENT_RUN_COMMON_FIELDS
        }
        yield InstrumentRun(
            date=started_at.date(),
            instrument=str(payload.get("instrument") or ""),
            engine=str(payload.get("engine") or ""),
            epoch=payload.get("epoch"),
            started_at=started_at,
            seconds=safe_int(payload.get("seconds")) or 0,
            hour_of_day=safe_int(payload.get("hour_of_day")),
            energy_state=payload.get("energy_state"),
            lux_mean=safe_float(payload.get("lux_mean")),
            motion_rms=safe_float(payload.get("motion_rms")),
            headphones=payload.get("headphones"),
            scored_on_device=payload.get("scored_on_device"),
            metrics=metrics,
        )


def _primary_metric(engine: str, metrics: dict[str, Any]) -> Optional[tuple[str, float]]:
    canon = _ENGINE_ALIASES.get(engine, engine)
    for field in _METRIC_PRIORITY.get(canon, ()):
        value = safe_float(metrics.get(field))
        if value is not None:
            return field, value
    if canon == "counting":
        correct = safe_float(metrics.get("cycles_correct"))
        miss = safe_float(metrics.get("unaware_miscounts"))
        if correct is not None and miss is not None and (correct + miss) > 0:
            return "accuracy", correct / (correct + miss)
    return None


def daily_instrument_metrics(
    *,
    start: Optional[date] = None,
    end: Optional[date] = None,
    root: Optional[Path] = None,
) -> list[InstrumentDay]:
    """Per (date, instrument) run count and mean primary-metric value.

    A date/instrument with zero runs is absent from the result, never
    synthesized as a zero row. When some of a day's runs carry a primary
    metric and others don't (e.g. a feasibility-check run mixed with real
    runs), the mean is taken over the runs that DO carry one.
    """
    buckets: dict[tuple[date, str], list[InstrumentRun]] = defaultdict(list)
    for run in instrument_runs(start=start, end=end, root=root):
        buckets[(run.date, run.instrument)].append(run)

    out: list[InstrumentDay] = []
    for (day, instrument), runs in sorted(buckets.items()):
        engine = runs[0].engine
        metric_name: Optional[str] = None
        values: list[float] = []
        for run in runs:
            found = _primary_metric(run.engine, run.metrics)
            if found is not None:
                metric_name, value = found
                values.append(value)
        primary_value = round(sum(values) / len(values), 4) if values else None
        out.append(InstrumentDay(
            date=day, instrument=instrument, engine=engine, run_count=len(runs),
            primary_metric_name=metric_name, primary_metric_value=primary_value,
        ))
    return out
