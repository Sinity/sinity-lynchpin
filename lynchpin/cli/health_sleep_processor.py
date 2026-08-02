"""Samsung Health + Sleep as Android sleep fusion.

Rebuilds the canonical fused sleep dataset (``sleep_all_nights.jsonl`` and its
compatibility twin ``sleep_merged.jsonl``) entirely from raw exports. The
previous incarnation of this module only *preserved* fusion rows produced by a
lost one-off script; this version re-derives every tier so the product is
reproducible from ``raw/`` alone.

Inputs (all read-only):

- ``raw/sleep-as-android/sleep-as-android.csv`` — SAA sessions, alternating
  header/data lines, first 15 fixed columns then per-session event columns.
- ``raw/samsung-health/*/com.samsung.shealth.sleep_combined.*.csv`` — scored
  in-app session summaries.
- ``raw/samsung-gdpr-cloud/Sleep Combined`` — same summaries via GDPR.
- ``raw/samsung-gdpr-cloud/Sleep`` — the master session table (basic rows).
- ``raw/samsung-gdpr-cloud/Sleep Stage`` — per-stage events keyed by
  ``sleep_id`` == basic-session ``datauuid``.
- ``raw/samsung-gdpr-cloud/Shealth Vitality Nap Data`` — explicit nap windows.
- ``processed/health_{heart_rate,hrv,respiratory,spo2,skin_temperature,
  snoring,movement}.jsonl`` — normalized signal products used for per-night
  sensor summaries (``process_sleep`` therefore runs after the signal
  processors in ``process_health``).

Output tiers (``source`` field):

- ``merged``        — Samsung session paired with an SAA session (any Samsung
                      kind; the Samsung sub-kind is kept in ``samsung_kind``).
- ``combined_only`` — scored ``sleep_combined`` summary, no SAA pair.
- ``stage_derived`` — basic session with stage events but no scored summary.
- ``samsung_only``  — basic session without stage events.
- ``saa_only``      — SAA session with no Samsung counterpart.

Additional derived fields: ``nap_evidence`` (explicit nap-record overlap or a
short-daytime heuristic), ``signals`` (per-night HR/HRV/respiratory/SpO2/skin
temperature/snoring/movement summary), and ``sleep_metrics.proxy_score`` (a
documented heuristic score for records Samsung never scored).
"""

from __future__ import annotations

import csv
import json
from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from lynchpin.cli import health_io
from lynchpin.cli.health_io import (
    parse_dt,
    parse_gdpr_dt,
    parse_gdpr_offset,
    parse_offset,
    read_gdpr_cloud_csvs,
    read_samsung_csv,
    try_float,
    try_int,
)

SLEEP_STAGE_MAP = {
    40001: "awake",
    40002: "light",
    40003: "deep",
    40004: "rem",
}

SAA_DATE_FORMATS = ("%d. %m. %Y %H:%M", "%d. %m. %Y %H:%M:%S")
SAA_CAP_MINUTES = 480.0
PAIR_MIN_OVERLAP_MINUTES = 15.0
NAP_MAX_MINUTES = 120.0
NAP_DAYTIME_HOURS = range(9, 21)

Record = dict[str, Any]


# ── Generic helpers ──────────────────────────────────────────────────────────


def _iso_to_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _overlap_minutes(
    a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime
) -> float:
    start = max(a_start, b_start)
    end = min(a_end, b_end)
    return max((end - start).total_seconds() / 60.0, 0.0)


# ── Sleep as Android parsing ─────────────────────────────────────────────────


def _parse_saa_naive(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in SAA_DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _saa_event_stats(events: Iterable[str]) -> dict[str, Any]:
    counts = {"hr": 0, "light": 0, "deep": 0, "rem": 0}
    hr_values: list[float] = []
    for event in events:
        if not event:
            continue
        head, _, rest = event.partition("-")
        if head == "HR":
            counts["hr"] += 1
            parts = rest.split("-")
            if len(parts) >= 2:
                value = try_float(parts[-1])
                if value is not None and 20 <= value <= 250:
                    hr_values.append(value)
        elif head in ("LIGHT_START", "DEEP_START", "REM_START"):
            counts[head.split("_")[0].lower()] += 1
    stats: dict[str, Any] = {f"events_{k}": v for k, v in counts.items()}
    stats["event_hr_avg"] = (
        round(sum(hr_values) / len(hr_values), 1) if hr_values else None
    )
    return stats


def load_saa_sessions() -> tuple[list[Record], list[Record]]:
    """Parse the SAA export into session dicts plus a trim log.

    The export alternates per-session header and data lines. ``Id`` is the
    epoch-millisecond session start, which lets us derive the true UTC offset
    from the naive ``From`` wall-clock time instead of trusting the export's
    inconsistent ``Tz`` column (observed values range from IANA names to
    POSIX-inverted ``GMT-02:00`` strings).
    """
    path = health_io.SAA_RAW / "sleep-as-android.csv"
    if not path.exists():
        return [], []

    sessions: list[Record] = []
    trim_log: list[Record] = []
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    i = 0
    while i < len(lines) - 1:
        if not lines[i].startswith("Id,"):
            i += 1
            continue
        header = next(csv.reader([lines[i]]))
        row = next(csv.reader([lines[i + 1]]))
        i += 2
        fixed = dict(zip(header[:15], row[:15]))
        events = row[15:]

        raw_id = fixed.get("Id", "")
        if not raw_id.isdigit():
            continue
        start_utc = datetime.fromtimestamp(int(raw_id) / 1000, tz=timezone.utc)
        from_naive = _parse_saa_naive(fixed.get("From"))
        to_naive = _parse_saa_naive(fixed.get("To"))
        if from_naive is None or to_naive is None:
            continue
        offset_hours = (
            round(
                (from_naive - start_utc.replace(tzinfo=None)).total_seconds()
                / 900.0
            )
            / 4.0
        )
        tz = timezone(timedelta(hours=offset_hours))
        start_local = from_naive.replace(tzinfo=tz)
        end_local = to_naive.replace(tzinfo=tz)
        if end_local <= start_local:
            continue

        duration_min = (end_local - start_local).total_seconds() / 60.0
        trimmed_from: float | None = None
        if duration_min > SAA_CAP_MINUTES:
            trimmed_from = round(duration_min, 1)
            end_local = start_local + timedelta(minutes=SAA_CAP_MINUTES)
            duration_min = SAA_CAP_MINUTES
            trim_log.append(
                {
                    "raw_id": raw_id,
                    "original_duration_minutes": trimmed_from,
                    "trimmed_duration_minutes": SAA_CAP_MINUTES,
                    "reason": "cap",
                }
            )

        hours = try_float(fixed.get("Hours"))
        deep_sleep = try_float(fixed.get("DeepSleep"))
        snore = try_float(fixed.get("Snore"))
        noise = try_float(fixed.get("Noise"))
        metrics: Record = {
            "rating": try_float(fixed.get("Rating")),
            "snore": snore,
            "noise": noise if noise is not None and noise >= 0 else 0.0,
            "cycles": try_int(fixed.get("Cycles")),
            "duration_minutes": round(hours * 60.0, 1)
            if hours is not None and hours > 0
            else round(duration_min, 1),
            "deep_sleep_fraction": deep_sleep
            if deep_sleep is not None and deep_sleep >= 0
            else None,
        }
        metrics.update(_saa_event_stats(events))

        sessions.append(
            {
                "raw_id": raw_id,
                "start": start_local,
                "end": end_local,
                "offset_hours": offset_hours,
                "duration_minutes": round(duration_min, 1),
                "trimmed_from_minutes": trimmed_from,
                "comment": (fixed.get("Comment") or "").strip() or None,
                "metrics": metrics,
            }
        )

    sessions.sort(key=lambda s: s["start"])
    return sessions, trim_log


# ── Samsung parsing ──────────────────────────────────────────────────────────


def _combined_metrics(row: dict[str, str]) -> Record:
    return {
        "sleep_score": try_float(row.get("sleep_score")),
        "sleep_duration": try_float(row.get("sleep_duration")),
        "total_rem_duration": try_float(row.get("total_rem_duration")),
        "total_light_duration": try_float(row.get("total_light_duration")),
        "deep_score": try_float(row.get("deep_score")),
        "rem_score": try_float(row.get("rem_score")),
        "wake_score": try_float(row.get("wake_score")),
        "physical_recovery": try_float(row.get("physical_recovery")),
        "mental_recovery": try_float(row.get("mental_recovery")),
        "sleep_efficiency": try_float(row.get("efficiency")),
        "sleep_cycle": try_float(row.get("sleep_cycle")),
        "movement_awakening": try_float(row.get("movement_awakening")),
    }


def _merge_missing(target: Record, extra: Record) -> None:
    for key, value in extra.items():
        if value is not None and target.get(key) is None:
            target[key] = value


def load_samsung_sessions() -> dict[str, Record]:
    """Load Samsung sessions keyed by ``datauuid``.

    Combined (scored) summaries and basic GDPR ``Sleep`` rows are disjoint uuid
    populations in practice; stage events attach to basic sessions through
    ``sleep_id``. Stage groups without any session row become synthetic
    sessions bounded by their stage events.
    """
    sessions: dict[str, Record] = {}

    # Scored in-app summaries.
    if health_io.HEALTH_RAW.exists():
        for export_dir in sorted(health_io.HEALTH_RAW.iterdir()):
            if not export_dir.is_dir():
                continue
            for csv_path in export_dir.glob(
                "com.samsung.shealth.sleep_combined.*.csv"
            ):
                for row in read_samsung_csv(csv_path):
                    uuid = row.get("datauuid", "")
                    offset_str = row.get("time_offset", "UTC+0000")
                    start = parse_dt(row.get("start_time"), offset_str)
                    end = parse_dt(row.get("end_time"), offset_str)
                    if not uuid or not start or not end:
                        continue
                    if uuid in sessions:
                        _merge_missing(
                            sessions[uuid]["sleep_metrics"], _combined_metrics(row)
                        )
                        continue
                    sessions[uuid] = {
                        "canonical_id": uuid,
                        "samsung_kind": "combined",
                        "start_local": start,
                        "end_local": end,
                        "device_uuid": row.get("deviceuuid", ""),
                        "time_offset_hours": parse_offset(offset_str),
                        "sleep_metrics": _combined_metrics(row),
                    }

    # Scored GDPR summaries.
    for row in read_gdpr_cloud_csvs("Sleep Combined"):
        uuid = row.get("datauuid", "")
        offset_ms = row.get("time_offset", "0")
        start = parse_gdpr_dt(row.get("start_time"), offset_ms)
        end = parse_gdpr_dt(row.get("end_time"), offset_ms)
        if not uuid or not start or not end:
            continue
        if uuid in sessions:
            _merge_missing(sessions[uuid]["sleep_metrics"], _combined_metrics(row))
            continue
        sessions[uuid] = {
            "canonical_id": uuid,
            "samsung_kind": "combined",
            "start_local": start,
            "end_local": end,
            "device_uuid": row.get("deviceuuid", ""),
            "time_offset_hours": parse_gdpr_offset(offset_ms),
            "sleep_metrics": _combined_metrics(row),
        }

    # Basic GDPR session rows (the master session table).
    for row in read_gdpr_cloud_csvs("Sleep"):
        uuid = row.get("datauuid", "")
        if not uuid or uuid in sessions:
            continue
        offset_ms = row.get("time_offset", "0")
        start = parse_gdpr_dt(row.get("start_time"), offset_ms)
        end = parse_gdpr_dt(row.get("end_time"), offset_ms)
        if not start or not end:
            continue
        sessions[uuid] = {
            "canonical_id": uuid,
            "samsung_kind": "basic",
            "start_local": start,
            "end_local": end,
            "device_uuid": row.get("deviceuuid", ""),
            "time_offset_hours": parse_gdpr_offset(offset_ms),
            "sleep_metrics": {
                "sleep_score": try_float(row.get("sleep_score")),
                "sleep_duration": try_float(row.get("sleep_duration")),
                "physical_recovery": try_float(row.get("physical_recovery")),
                "mental_recovery": try_float(row.get("mental_recovery")),
                "sleep_efficiency": try_float(row.get("efficiency")),
                "sleep_cycle": try_float(row.get("sleep_cycle")),
                "movement_awakening": try_float(row.get("movement_awakening")),
            },
        }

    _attach_stage_groups(sessions)
    return sessions


def _attach_stage_groups(sessions: dict[str, Record]) -> None:
    groups: dict[str, list[Record]] = {}
    for row in read_gdpr_cloud_csvs("Sleep Stage"):
        sleep_id = row.get("sleep_id", "")
        if not sleep_id:
            continue
        offset_ms = row.get("time_offset", "0")
        start = _iso_to_dt(parse_gdpr_dt(row.get("start_time"), offset_ms))
        end = _iso_to_dt(parse_gdpr_dt(row.get("end_time"), offset_ms))
        stage_code = try_int(row.get("stage"))
        if start is None or end is None or stage_code is None:
            continue
        stage = SLEEP_STAGE_MAP.get(stage_code)
        if stage is None:
            continue
        groups.setdefault(sleep_id, []).append(
            {
                "start": start,
                "end": end,
                "stage": stage,
                "minutes": max((end - start).total_seconds() / 60.0, 0.0),
            }
        )

    for sleep_id, records in groups.items():
        records.sort(key=lambda r: r["start"])
        totals = {"awake": 0.0, "light": 0.0, "deep": 0.0, "rem": 0.0}
        for record in records:
            totals[record["stage"]] += record["minutes"]
        asleep = totals["light"] + totals["deep"] + totals["rem"]
        span = totals["awake"] + asleep
        if span <= 0:
            continue
        aggregate = {
            "stage_count": len(records),
            "total_awake_duration": round(totals["awake"], 1),
            "total_light_duration": round(totals["light"], 1),
            "total_deep_duration": round(totals["deep"], 1),
            "total_rem_duration": round(totals["rem"], 1),
            "asleep_minutes": round(asleep, 1),
            "awake_pct": round(totals["awake"] / span * 100, 1),
            "light_pct": round(totals["light"] / span * 100, 1),
            "deep_pct": round(totals["deep"] / span * 100, 1),
            "rem_pct": round(totals["rem"] / span * 100, 1),
        }
        session = sessions.get(sleep_id)
        if session is None:
            tz = records[0]["start"].tzinfo
            offset = tz.utcoffset(records[0]["start"]) if tz else None
            sessions[sleep_id] = {
                "canonical_id": sleep_id,
                "samsung_kind": "basic",
                "start_local": records[0]["start"].isoformat(),
                "end_local": records[-1]["end"].isoformat(),
                "device_uuid": "",
                "time_offset_hours": (
                    offset.total_seconds() / 3600 if offset else 0.0
                ),
                "sleep_metrics": {},
                "stage_aggregate": aggregate,
            }
        else:
            session["stage_aggregate"] = aggregate


# ── Pairing ──────────────────────────────────────────────────────────────────


def pair_sessions(
    saa: list[Record], samsung: dict[str, Record]
) -> tuple[dict[str, Record], list[Record]]:
    """Greedy best-overlap matching between SAA and Samsung sessions.

    Returns ``{samsung_uuid: saa_session}`` plus the unmatched SAA sessions.
    Pairing operates on absolute (UTC) intervals, so the two devices' clock
    representations do not have to agree.
    """
    samsung_items = []
    for uuid, session in samsung.items():
        start = _iso_to_dt(session["start_local"])
        end = _iso_to_dt(session["end_local"])
        if start is None or end is None or end <= start:
            continue
        samsung_items.append((uuid, start, end))

    candidates: list[tuple[float, int, str]] = []
    for idx, session in enumerate(saa):
        for uuid, start, end in samsung_items:
            overlap = _overlap_minutes(session["start"], session["end"], start, end)
            if overlap >= PAIR_MIN_OVERLAP_MINUTES:
                candidates.append((overlap, idx, uuid))
    candidates.sort(reverse=True)

    paired: dict[str, Record] = {}
    used_saa: set[int] = set()
    for _, idx, uuid in candidates:
        if idx in used_saa or uuid in paired:
            continue
        used_saa.add(idx)
        paired[uuid] = saa[idx]

    unmatched = [s for i, s in enumerate(saa) if i not in used_saa]
    return paired, unmatched


# ── Record assembly ──────────────────────────────────────────────────────────


def _samsung_tier(session: Record) -> str:
    if session["samsung_kind"] == "combined":
        return "combined_only"
    if "stage_aggregate" in session:
        return "stage_derived"
    return "samsung_only"


def _build_samsung_record(session: Record) -> Record:
    metrics = dict(session["sleep_metrics"])
    aggregate = session.get("stage_aggregate")
    stage_count: int | None = None
    if aggregate:
        stage_count = aggregate["stage_count"]
        _merge_missing(
            metrics,
            {
                key: aggregate[key]
                for key in (
                    "total_awake_duration",
                    "total_light_duration",
                    "total_deep_duration",
                    "total_rem_duration",
                    "awake_pct",
                    "light_pct",
                    "deep_pct",
                    "rem_pct",
                )
            },
        )
        if metrics.get("sleep_duration") is None:
            metrics["sleep_duration"] = aggregate["asleep_minutes"]

    start = _iso_to_dt(session["start_local"])
    end = _iso_to_dt(session["end_local"])
    duration = (
        (end - start).total_seconds() / 60.0 if start and end else 0.0
    )
    record: Record = {
        "canonical_id": session["canonical_id"],
        "source": _samsung_tier(session),
        "samsung_kind": session["samsung_kind"],
        "start_local": session["start_local"],
        "end_local": session["end_local"],
        "duration_minutes": round(duration, 1),
        "device_uuid": session.get("device_uuid", ""),
        "device_name": "",
        "time_offset_hours": session.get("time_offset_hours", 0.0),
        "sleep_metrics": metrics,
        "saa_metrics": None,
        "deltas": None,
        "comment": None,
    }
    if stage_count is not None:
        record["stage_count"] = stage_count
    return record


def _attach_saa(record: Record, saa: Record) -> None:
    record["source"] = "merged"
    record["saa_metrics"] = dict(saa["metrics"])
    record["comment"] = saa["comment"]
    sh_start = _iso_to_dt(record["start_local"])
    sh_end = _iso_to_dt(record["end_local"])
    if sh_start and sh_end:
        start_delta = (saa["start"] - sh_start).total_seconds() / 60.0
        end_delta = (saa["end"] - sh_end).total_seconds() / 60.0
        record["deltas"] = {
            "start_minutes": round(start_delta, 1),
            "end_minutes": round(end_delta, 1),
            "sa_duration_vs_sh_minutes": round(
                saa["duration_minutes"]
                - (sh_end - sh_start).total_seconds() / 60.0,
                1,
            ),
        }
        # SAA syncs sessions from the watch/Samsung Health; identical
        # boundaries mean the SAA row is a synced mirror of the Samsung
        # session, not an independent recording of the same night.
        record["saa_relation"] = (
            "mirror"
            if abs(start_delta) < 5 and abs(end_delta) < 5
            else "independent"
        )


def _build_saa_record(saa: Record) -> Record:
    return {
        "canonical_id": saa["raw_id"],
        "source": "saa_only",
        "start_local": saa["start"].isoformat(),
        "end_local": saa["end"].isoformat(),
        "duration_minutes": saa["duration_minutes"],
        "trimmed_from_minutes": saa["trimmed_from_minutes"],
        "device_uuid": "",
        "device_name": "",
        "time_offset_hours": saa["offset_hours"],
        "sleep_metrics": {},
        "saa_metrics": dict(saa["metrics"]),
        "deltas": None,
        "comment": saa["comment"],
    }


# ── Nap classification ───────────────────────────────────────────────────────


def load_nap_windows() -> list[tuple[datetime, datetime]]:
    windows: list[tuple[datetime, datetime]] = []
    for row in read_gdpr_cloud_csvs("Shealth Vitality Nap Data"):
        offset_ms = row.get("time_offset", "0")
        start = _iso_to_dt(parse_gdpr_dt(row.get("start_time"), offset_ms))
        end = _iso_to_dt(parse_gdpr_dt(row.get("end_time"), offset_ms))
        if start and end and end > start:
            windows.append((start, end))
    windows.sort()
    return windows


def classify_nap(record: Record, naps: list[tuple[datetime, datetime]]) -> None:
    start = _iso_to_dt(record["start_local"])
    end = _iso_to_dt(record["end_local"])
    if start is None or end is None or end <= start:
        record["nap_evidence"] = None
        return
    span = (end - start).total_seconds() / 60.0
    for nap_start, nap_end in naps:
        if nap_start > end:
            break
        overlap = _overlap_minutes(start, end, nap_start, nap_end)
        if overlap >= 0.5 * span:
            record["nap_evidence"] = "vitality_nap"
            return
    if span <= NAP_MAX_MINUTES and start.hour in NAP_DAYTIME_HOURS:
        record["nap_evidence"] = "short_daytime"
        return
    record["nap_evidence"] = None


# ── Proxy score ──────────────────────────────────────────────────────────────


# Least-squares fit against Samsung's own sleep_score on the 1,205 nights
# that carry both a real score and stage aggregates (fitted 2026-08-03;
# 5-fold cross-validated Pearson r = 0.823, MAE = 6.6 score points; refit
# procedure preserved at /realm/tmp/work/proxy_fit2.py lineage and described
# in the sleep-pipeline audit report). Feature order matters below.
_PROXY_COEF = {
    "asleep_sat": 24.831873,   # min(asleep/450, 1)
    "deep_sat": 9.816288,      # min(deep share of asleep / 20%, 1)
    "rem_sat": 15.510639,      # min(rem share of asleep / 22%, 1)
    "eff": 15.190455,          # asleep / time-in-bed
    "awake_min": 0.037172,     # scored awake minutes inside the session
    "frag": -0.775678,         # stage records per hour of span (fragmentation)
}
_PROXY_INTERCEPT = 18.956087


def compute_proxy_score(record: Record) -> float | None:
    """Estimated 0-100 quality score for records Samsung never scored.

    A linear model fitted to Samsung's own scores on the scored population
    (see ``_PROXY_COEF``): sleep amount saturating at 7.5 h, deep/REM shares
    saturating at typical-architecture levels, efficiency, awake minutes, and
    stage fragmentation per hour. Consumers see it in
    ``sleep_metrics.proxy_score``, never in ``sleep_score``.
    """
    metrics = record.get("sleep_metrics") or {}
    if metrics.get("sleep_score") is not None:
        return None
    light = metrics.get("total_light_duration")
    deep = metrics.get("total_deep_duration")
    rem = metrics.get("total_rem_duration")
    if light is None and deep is None and rem is None:
        return None
    asleep = sum(v for v in (light, deep, rem) if v is not None)
    if asleep <= 0:
        return None
    span = record.get("duration_minutes") or asleep
    awake = metrics.get("total_awake_duration")
    if awake is None:
        awake = max(span - asleep, 0.0)
    stage_count = record.get("stage_count") or 0
    features = {
        "asleep_sat": min(asleep / 450.0, 1.0),
        "deep_sat": min(((deep or 0.0) / asleep * 100) / 20.0, 1.0),
        "rem_sat": min(((rem or 0.0) / asleep * 100) / 22.0, 1.0),
        "eff": asleep / span if span > 0 else 0.0,
        "awake_min": float(awake),
        "frag": stage_count / max(span / 60.0, 0.5),
    }
    score = _PROXY_INTERCEPT + sum(
        _PROXY_COEF[name] * value for name, value in features.items()
    )
    return round(max(0.0, min(100.0, score)), 1)


# ── Signal fusion ────────────────────────────────────────────────────────────

_SIGNAL_FILES = {
    "heart_rate": "health_heart_rate.jsonl",
    "hrv": "health_hrv.jsonl",
    "respiratory": "health_respiratory.jsonl",
    "spo2": "health_spo2.jsonl",
    "skin_temperature": "health_skin_temperature.jsonl",
    "snoring": "health_snoring.jsonl",
    "movement": "health_movement.jsonl",
}

_MAX_NIGHT_HOURS = 24.0


class _NightIndex:
    """Interval index over night records for streaming signal attachment."""

    def __init__(self, records: list[Record]) -> None:
        self._items: list[tuple[datetime, datetime, Record]] = []
        for record in records:
            start = _iso_to_dt(record["start_local"])
            end = _iso_to_dt(record["end_local"])
            if start and end and end > start:
                self._items.append((start, end, record))
        self._items.sort(key=lambda item: item[0])
        self._starts = [item[0] for item in self._items]

    def overlapping(self, start: datetime, end: datetime) -> Iterable[Record]:
        hi = bisect_right(self._starts, end)
        horizon = start - timedelta(hours=_MAX_NIGHT_HOURS)
        for idx in range(hi - 1, -1, -1):
            night_start, night_end, record = self._items[idx]
            if night_start < horizon:
                break
            if night_end > start:
                yield record


def _iter_signal_rows(name: str) -> Iterable[Record]:
    path = health_io.PROCESSED / _SIGNAL_FILES[name]
    if not path.exists():
        return
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


def attach_signals(records: list[Record]) -> None:
    """Summarize adjacent-sensor evidence inside each record's sleep window."""
    index = _NightIndex(records)
    accumulators: dict[int, dict[str, Any]] = {}

    def acc(record: Record) -> dict[str, Any]:
        return accumulators.setdefault(
            id(record),
            {
                "hr_sum": 0.0,
                "hr_weight": 0.0,
                "hr_min": None,
                "hr_max": None,
                "hr_samples": 0,
                "rmssd_sum": 0.0,
                "rmssd_weight": 0.0,
                "sdnn_sum": 0.0,
                "resp_sum": 0.0,
                "resp_n": 0,
                "spo2_sum": 0.0,
                "spo2_n": 0,
                "spo2_min": None,
                "temp_sum": 0.0,
                "temp_n": 0,
                "snore_ms": 0.0,
                "snore_n": 0,
                "movement_ms": 0.0,
                "record": record,
            },
        )

    def window(row: Record) -> tuple[datetime, datetime] | None:
        start = _iso_to_dt(row.get("start_time"))
        end = _iso_to_dt(row.get("end_time")) or start
        if start is None or end is None:
            return None
        return start, max(end, start)

    for row in _iter_signal_rows("heart_rate"):
        bounds = window(row)
        rate = try_float(str(row.get("heart_rate")))
        if bounds is None or rate is None:
            continue
        weight = try_float(str(row.get("heart_beat_count"))) or 1.0
        low = try_float(str(row.get("min")))
        high = try_float(str(row.get("max")))
        for record in index.overlapping(*bounds):
            a = acc(record)
            a["hr_sum"] += rate * weight
            a["hr_weight"] += weight
            a["hr_samples"] += 1
            for key, value in (("hr_min", low or rate), ("hr_max", high or rate)):
                current = a[key]
                if value is None:
                    continue
                if key == "hr_min":
                    a[key] = value if current is None else min(current, value)
                else:
                    a[key] = value if current is None else max(current, value)

    for row in _iter_signal_rows("hrv"):
        bounds = window(row)
        rmssd = try_float(str(row.get("rmssd_avg")))
        if bounds is None or rmssd is None:
            continue
        weight = try_float(str(row.get("n_windows"))) or 1.0
        sdnn = try_float(str(row.get("sdnn_avg")))
        for record in index.overlapping(*bounds):
            a = acc(record)
            a["rmssd_sum"] += rmssd * weight
            a["rmssd_weight"] += weight
            if sdnn is not None:
                a["sdnn_sum"] += sdnn * weight

    for row in _iter_signal_rows("respiratory"):
        bounds = window(row)
        rate = try_float(str(row.get("avg_rate")))
        if bounds is None or rate is None:
            continue
        for record in index.overlapping(*bounds):
            a = acc(record)
            a["resp_sum"] += rate
            a["resp_n"] += 1

    for row in _iter_signal_rows("spo2"):
        bounds = window(row)
        spo2 = try_float(str(row.get("spo2")))
        if bounds is None or spo2 is None:
            continue
        low = try_float(str(row.get("min")))
        for record in index.overlapping(*bounds):
            a = acc(record)
            a["spo2_sum"] += spo2
            a["spo2_n"] += 1
            candidate = low if low is not None else spo2
            a["spo2_min"] = (
                candidate
                if a["spo2_min"] is None
                else min(a["spo2_min"], candidate)
            )

    for row in _iter_signal_rows("skin_temperature"):
        bounds = window(row)
        temp = try_float(str(row.get("temperature")))
        if bounds is None or temp is None:
            continue
        for record in index.overlapping(*bounds):
            a = acc(record)
            a["temp_sum"] += temp
            a["temp_n"] += 1

    for row in _iter_signal_rows("snoring"):
        bounds = window(row)
        if bounds is None:
            continue
        duration = try_float(str(row.get("duration"))) or 0.0
        for record in index.overlapping(*bounds):
            a = acc(record)
            a["snore_ms"] += duration
            a["snore_n"] += 1

    for row in _iter_signal_rows("movement"):
        bounds = window(row)
        duration = try_float(str(row.get("duration_ms")))
        if bounds is None or duration is None:
            continue
        for record in index.overlapping(*bounds):
            start, end = bounds
            night_start = _iso_to_dt(record["start_local"])
            night_end = _iso_to_dt(record["end_local"])
            if night_start is None or night_end is None:
                continue
            overlap = _overlap_minutes(start, end, night_start, night_end)
            span = max((end - start).total_seconds() / 60.0, 1e-9)
            acc(record)["movement_ms"] += duration * min(overlap / span, 1.0)

    for a in accumulators.values():
        signals: Record = {}
        if a["hr_weight"] > 0:
            signals["hr_avg"] = round(a["hr_sum"] / a["hr_weight"], 1)
            signals["hr_min"] = round(a["hr_min"], 1) if a["hr_min"] is not None else None
            signals["hr_max"] = round(a["hr_max"], 1) if a["hr_max"] is not None else None
            signals["hr_samples"] = a["hr_samples"]
        if a["rmssd_weight"] > 0:
            signals["hrv_rmssd"] = round(a["rmssd_sum"] / a["rmssd_weight"], 1)
            signals["hrv_sdnn"] = round(a["sdnn_sum"] / a["rmssd_weight"], 1)
        if a["resp_n"] > 0:
            signals["respiratory_rate"] = round(a["resp_sum"] / a["resp_n"], 1)
        if a["spo2_n"] > 0:
            signals["spo2_avg"] = round(a["spo2_sum"] / a["spo2_n"], 1)
            signals["spo2_min"] = round(a["spo2_min"], 1) if a["spo2_min"] is not None else None
        if a["temp_n"] > 0:
            signals["skin_temp_c"] = round(a["temp_sum"] / a["temp_n"], 2)
        if a["snore_n"] > 0:
            signals["snoring_seconds"] = round(a["snore_ms"] / 1000.0, 1)
        if a["movement_ms"] > 0:
            signals["movement_min"] = round(a["movement_ms"] / 60000.0, 1)
        if signals:
            a["record"]["signals"] = signals


# ── Entry point ──────────────────────────────────────────────────────────────


def process_sleep(dry_run: bool = False) -> int:
    """Rebuild the fused sleep dataset from raw exports."""
    saa_sessions, trim_log = load_saa_sessions()
    samsung_sessions = load_samsung_sessions()
    naps = load_nap_windows()

    paired, unmatched_saa = pair_sessions(saa_sessions, samsung_sessions)

    records: list[Record] = []
    for uuid, session in samsung_sessions.items():
        record = _build_samsung_record(session)
        if uuid in paired:
            _attach_saa(record, paired[uuid])
        records.append(record)
    for saa in unmatched_saa:
        records.append(_build_saa_record(saa))

    for record in records:
        classify_nap(record, naps)
    attach_signals(records)
    for record in records:
        proxy = compute_proxy_score(record)
        if proxy is not None:
            record["sleep_metrics"]["proxy_score"] = proxy

    records.sort(key=lambda r: r.get("start_local", ""))

    stats = {
        "saa_session_count": len(saa_sessions),
        "saa_trimmed_count": len(trim_log),
        "saa_paired_count": len(paired),
        "saa_unmatched_count": len(unmatched_saa),
        "samsung_session_count": len(samsung_sessions),
        "tier_counts": _tier_counts(records),
        "nap_counts": _nap_counts(records),
        "saa_relation_counts": _count_field(records, "saa_relation"),
        "signal_coverage": sum(1 for r in records if r.get("signals")),
        "proxy_scored": sum(
            1
            for r in records
            if (r.get("sleep_metrics") or {}).get("proxy_score") is not None
        ),
    }

    if dry_run:
        print(f"[dry-run] Would write {len(records)} sleep records")
        print(json.dumps(stats, indent=2, sort_keys=True))
        return len(records)

    all_nights = health_io.PROCESSED / "sleep_all_nights.jsonl"
    for path in (all_nights, health_io.PROCESSED / "sleep_merged.jsonl"):
        with open(path, "w") as handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")
    health_io._write_product_manifest(all_nights, records, "Sleep (fused)")

    (health_io.PROCESSED / "sleep_merge_stats.json").write_text(
        json.dumps(stats, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (health_io.PROCESSED / "sleep_as_android_trim_log.json").write_text(
        json.dumps(trim_log, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    with open(
        health_io.PROCESSED / "sleep_as_android_native_unmatched.jsonl", "w"
    ) as handle:
        for saa in unmatched_saa:
            handle.write(
                json.dumps(
                    {
                        "raw_id": saa["raw_id"],
                        "start_local": saa["start"].isoformat(),
                        "end_local": saa["end"].isoformat(),
                        "duration_minutes": saa["duration_minutes"],
                        "comment": saa["comment"],
                    }
                )
                + "\n"
            )

    print(
        f"Sleep: {len(records)} records "
        f"({stats['saa_paired_count']} SAA-paired, "
        f"{stats['proxy_scored']} proxy-scored, "
        f"{stats['signal_coverage']} with signals) -> {all_nights}"
    )
    return len(records)


def _tier_counts(records: list[Record]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        counts[record["source"]] = counts.get(record["source"], 0) + 1
    return counts


def _nap_counts(records: list[Record]) -> dict[str, int]:
    return _count_field(records, "nap_evidence")


def _count_field(records: list[Record], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        value = record.get(field)
        if value:
            counts[value] = counts.get(value, 0) + 1
    return counts


__all__ = [
    "process_sleep",
    "load_saa_sessions",
    "load_samsung_sessions",
    "load_nap_windows",
    "pair_sessions",
    "classify_nap",
    "compute_proxy_score",
    "attach_signals",
]
