"""Tests for the Samsung Health + Sleep as Android sleep fusion."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from lynchpin.cli import health_io, health_sleep_processor as proc


# ── Fixture builders ─────────────────────────────────────────────────────────

TZ2 = timezone(timedelta(hours=2))


def _saa_csv(sessions: list[dict]) -> str:
    """Render SAA's alternating header/data line format."""
    lines: list[str] = []
    for s in sessions:
        events = s.get("events", [])
        header = [
            "Id", "Tz", "From", "To", "Sched", "Hours", "Rating", "Comment",
            "Framerate", "Snore", "Noise", "Cycles", "DeepSleep", "LenAdjust",
            "Geo", *(["Event"] * len(events)),
        ]
        start: datetime = s["start"]
        end: datetime = s["end"]
        epoch_ms = int(start.timestamp() * 1000)
        naive = start.astimezone(TZ2).replace(tzinfo=None)
        naive_end = end.astimezone(TZ2).replace(tzinfo=None)
        row = [
            str(epoch_ms), "Europe/Warsaw",
            naive.strftime("%d. %m. %Y %H:%M"),
            naive_end.strftime("%d. %m. %Y %H:%M"),
            naive_end.strftime("%d. %m. %Y %H:%M"),
            str(s.get("hours", round((end - start).total_seconds() / 3600, 3))),
            str(s.get("rating", 0.0)), s.get("comment", ""),
            "10008", str(s.get("snore", -1)), "-1.0",
            str(s.get("cycles", 3)), "0.5", "0", "", *events,
        ]
        for record in (header, row):
            buf = io.StringIO()
            csv.writer(buf, lineterminator="").writerow(record)
            lines.append(buf.getvalue())
    return "\n".join(lines) + "\n"


def _gdpr_dir(root: Path, category: str, rows: list[dict]) -> None:
    directory = root / category
    directory.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    keys = sorted({k for row in rows for k in row})
    with open(directory / f"{category}.csv", "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture()
def health_env(tmp_path, monkeypatch):
    raw = tmp_path / "raw/samsung-health"
    saa = tmp_path / "raw/sleep-as-android"
    gdpr = tmp_path / "raw/samsung-gdpr-cloud"
    processed = tmp_path / "processed"
    for path in (raw, saa, gdpr, processed):
        path.mkdir(parents=True)
    monkeypatch.setattr(health_io, "HEALTH_RAW", raw)
    monkeypatch.setattr(health_io, "SAA_RAW", saa)
    monkeypatch.setattr(health_io, "GDPR_CLOUD_DIR", gdpr)
    monkeypatch.setattr(health_io, "PROCESSED", processed)
    return tmp_path


def _ms(dt: datetime) -> str:
    return str(int(dt.timestamp() * 1000))


NIGHT_START = datetime(2025, 6, 1, 23, 30, tzinfo=TZ2)
NIGHT_END = datetime(2025, 6, 2, 7, 30, tzinfo=TZ2)


def _seed_basic_night(root: Path, uuid: str = "sh-night-1") -> None:
    _gdpr_dir(
        root / "raw/samsung-gdpr-cloud",
        "Sleep",
        [
            {
                "datauuid": uuid,
                "start_time": _ms(NIGHT_START),
                "end_time": _ms(NIGHT_END),
                "time_offset": "7200000",
                "sleep_duration": "",
                "sleep_score": "",
                "efficiency": "",
            }
        ],
    )
    stage_rows = []
    cursor = NIGHT_START
    for stage_code, minutes in ((40002, 200), (40003, 90), (40004, 100), (40001, 90)):
        stage_end = cursor + timedelta(minutes=minutes)
        stage_rows.append(
            {
                "datauuid": f"{uuid}-stage-{stage_code}",
                "sleep_id": uuid,
                "stage": str(stage_code),
                "start_time": _ms(cursor),
                "end_time": _ms(stage_end),
                "time_offset": "7200000",
            }
        )
        cursor = stage_end
    _gdpr_dir(root / "raw/samsung-gdpr-cloud", "Sleep Stage", stage_rows)


# ── SAA parsing ──────────────────────────────────────────────────────────────


def test_saa_offset_derived_from_epoch_id(health_env):
    (health_env / "raw/sleep-as-android/sleep-as-android.csv").write_text(
        _saa_csv(
            [
                {
                    "start": NIGHT_START,
                    "end": NIGHT_END,
                    "events": ["HR-1-60.0", "HR-2-70.0", "LIGHT_START-3"],
                }
            ]
        ),
        encoding="utf-8",
    )
    sessions, trim = proc.load_saa_sessions()
    assert len(sessions) == 1
    assert trim == []
    session = sessions[0]
    assert session["offset_hours"] == 2.0
    assert session["start"] == NIGHT_START
    assert session["metrics"]["event_hr_avg"] == 65.0
    assert session["metrics"]["events_hr"] == 2
    assert session["metrics"]["events_light"] == 1


def test_saa_cap_trims_runaway_sessions(health_env):
    (health_env / "raw/sleep-as-android/sleep-as-android.csv").write_text(
        _saa_csv(
            [
                {
                    "start": NIGHT_START,
                    "end": NIGHT_START + timedelta(hours=11),
                }
            ]
        ),
        encoding="utf-8",
    )
    sessions, trim = proc.load_saa_sessions()
    assert sessions[0]["duration_minutes"] == proc.SAA_CAP_MINUTES
    assert sessions[0]["trimmed_from_minutes"] == 660.0
    assert trim[0]["reason"] == "cap"


# ── Samsung sessions + tiers ─────────────────────────────────────────────────


def test_stage_derived_tier_and_aggregates(health_env):
    _seed_basic_night(health_env)
    sessions = proc.load_samsung_sessions()
    assert set(sessions) == {"sh-night-1"}
    record = proc._build_samsung_record(sessions["sh-night-1"])
    assert record["source"] == "stage_derived"
    assert record["stage_count"] == 4
    metrics = record["sleep_metrics"]
    assert metrics["total_light_duration"] == 200.0
    assert metrics["total_deep_duration"] == 90.0
    assert metrics["total_rem_duration"] == 100.0
    assert metrics["total_awake_duration"] == 90.0
    # sleep_duration is asleep time (light+deep+rem), not the wall span
    assert metrics["sleep_duration"] == 390.0
    assert record["duration_minutes"] == 480.0


def test_combined_summary_tier(health_env):
    _gdpr_dir(
        health_env / "raw/samsung-gdpr-cloud",
        "Sleep Combined",
        [
            {
                "datauuid": "sh-combined-1",
                "start_time": _ms(NIGHT_START),
                "end_time": _ms(NIGHT_END),
                "time_offset": "7200000",
                "sleep_score": "82",
                "sleep_duration": "420",
                "efficiency": "91",
            }
        ],
    )
    sessions = proc.load_samsung_sessions()
    record = proc._build_samsung_record(sessions["sh-combined-1"])
    assert record["source"] == "combined_only"
    assert record["sleep_metrics"]["sleep_score"] == 82.0


# ── Pairing ──────────────────────────────────────────────────────────────────


def test_pairing_produces_merged_with_deltas(health_env):
    _seed_basic_night(health_env)
    saa_start = NIGHT_START - timedelta(minutes=45)
    saa_end = NIGHT_END - timedelta(minutes=60)
    (health_env / "raw/sleep-as-android/sleep-as-android.csv").write_text(
        _saa_csv([{"start": saa_start, "end": saa_end}]), encoding="utf-8"
    )
    total = proc.process_sleep(dry_run=False)
    assert total == 1
    rows = [
        json.loads(line)
        for line in (health_env / "processed/sleep_all_nights.jsonl")
        .read_text()
        .splitlines()
    ]
    record = rows[0]
    assert record["source"] == "merged"
    assert record["samsung_kind"] == "basic"
    assert record["deltas"]["start_minutes"] == -45.0
    assert record["deltas"]["end_minutes"] == -60.0
    assert record["saa_relation"] == "independent"
    # compatibility twin stays in sync
    twin = (health_env / "processed/sleep_merged.jsonl").read_text()
    assert len(twin.splitlines()) == 1
    stats = json.loads(
        (health_env / "processed/sleep_merge_stats.json").read_text()
    )
    assert stats["tier_counts"] == {"merged": 1}
    assert stats["saa_relation_counts"] == {"independent": 1}


def test_mirror_relation_for_synced_sessions(health_env):
    _seed_basic_night(health_env)
    (health_env / "raw/sleep-as-android/sleep-as-android.csv").write_text(
        _saa_csv([{"start": NIGHT_START, "end": NIGHT_END}]), encoding="utf-8"
    )
    saa, _ = proc.load_saa_sessions()
    samsung = proc.load_samsung_sessions()
    paired, unmatched = proc.pair_sessions(saa, samsung)
    assert unmatched == []
    record = proc._build_samsung_record(samsung["sh-night-1"])
    proc._attach_saa(record, paired["sh-night-1"])
    assert record["saa_relation"] == "mirror"


def test_unmatched_saa_becomes_saa_only(health_env):
    (health_env / "raw/sleep-as-android/sleep-as-android.csv").write_text(
        _saa_csv([{"start": NIGHT_START, "end": NIGHT_END}]), encoding="utf-8"
    )
    proc.process_sleep(dry_run=False)
    rows = [
        json.loads(line)
        for line in (health_env / "processed/sleep_all_nights.jsonl")
        .read_text()
        .splitlines()
    ]
    assert [r["source"] for r in rows] == ["saa_only"]
    unmatched = (
        health_env / "processed/sleep_as_android_native_unmatched.jsonl"
    ).read_text()
    assert len(unmatched.splitlines()) == 1


# ── Naps ─────────────────────────────────────────────────────────────────────


def test_nap_classification(health_env):
    nap_start = datetime(2025, 6, 2, 14, 0, tzinfo=TZ2)
    nap_end = nap_start + timedelta(minutes=40)
    _gdpr_dir(
        health_env / "raw/samsung-gdpr-cloud",
        "Shealth Vitality Nap Data",
        [
            {
                "datauuid": "nap-1",
                "start_time": _ms(nap_start),
                "end_time": _ms(nap_end),
                "time_offset": "7200000",
            }
        ],
    )
    naps = proc.load_nap_windows()
    assert len(naps) == 1

    explicit = {
        "start_local": nap_start.isoformat(),
        "end_local": nap_end.isoformat(),
    }
    proc.classify_nap(explicit, naps)
    assert explicit["nap_evidence"] == "vitality_nap"

    heuristic = {
        "start_local": datetime(2024, 3, 3, 15, 0, tzinfo=TZ2).isoformat(),
        "end_local": datetime(2024, 3, 3, 16, 0, tzinfo=TZ2).isoformat(),
    }
    proc.classify_nap(heuristic, naps)
    assert heuristic["nap_evidence"] == "short_daytime"

    night = {
        "start_local": NIGHT_START.isoformat(),
        "end_local": NIGHT_END.isoformat(),
    }
    proc.classify_nap(night, naps)
    assert night["nap_evidence"] is None


# ── Proxy score ──────────────────────────────────────────────────────────────


def test_proxy_score_only_for_unscored_records():
    scored = {
        "sleep_metrics": {"sleep_score": 80, "total_deep_duration": 90},
        "duration_minutes": 480,
    }
    assert proc.compute_proxy_score(scored) is None

    unscored = {
        "sleep_metrics": {
            "sleep_score": None,
            "total_light_duration": 200.0,
            "total_deep_duration": 90.0,
            "total_rem_duration": 100.0,
        },
        "duration_minutes": 480.0,
    }
    score = proc.compute_proxy_score(unscored)
    assert score is not None
    assert 0 <= score <= 100
    # a fragmented short sleep scores lower
    fragment = {
        "sleep_metrics": {
            "sleep_score": None,
            "total_light_duration": 40.0,
            "total_deep_duration": 0.0,
            "total_rem_duration": 0.0,
        },
        "duration_minutes": 90.0,
    }
    assert proc.compute_proxy_score(fragment) < score

    no_stages = {"sleep_metrics": {"sleep_score": None}, "duration_minutes": 480}
    assert proc.compute_proxy_score(no_stages) is None


# ── Signals ──────────────────────────────────────────────────────────────────


def test_attach_signals_summarizes_window(health_env):
    processed = health_env / "processed"
    hr_rows = [
        {
            "start_time": (NIGHT_START + timedelta(hours=1)).isoformat(),
            "end_time": (NIGHT_START + timedelta(hours=1)).isoformat(),
            "heart_rate": 52.0,
            "min": 48.0,
            "max": 60.0,
            "heart_beat_count": 100,
        },
        {
            "start_time": (NIGHT_START + timedelta(hours=3)).isoformat(),
            "end_time": (NIGHT_START + timedelta(hours=3)).isoformat(),
            "heart_rate": 58.0,
            "min": None,
            "max": None,
            "heart_beat_count": 100,
        },
        {  # outside the night window: ignored
            "start_time": (NIGHT_END + timedelta(hours=2)).isoformat(),
            "end_time": (NIGHT_END + timedelta(hours=2)).isoformat(),
            "heart_rate": 120.0,
            "heart_beat_count": 1,
        },
    ]
    with open(processed / "health_heart_rate.jsonl", "w") as handle:
        for row in hr_rows:
            handle.write(json.dumps(row) + "\n")
    with open(processed / "health_snoring.jsonl", "w") as handle:
        handle.write(
            json.dumps(
                {
                    "start_time": (NIGHT_START + timedelta(hours=2)).isoformat(),
                    "end_time": (NIGHT_START + timedelta(hours=2, minutes=5)).isoformat(),
                    "duration": 90000,
                }
            )
            + "\n"
        )

    record = {
        "start_local": NIGHT_START.isoformat(),
        "end_local": NIGHT_END.isoformat(),
    }
    proc.attach_signals([record])
    signals = record["signals"]
    assert signals["hr_avg"] == 55.0
    assert signals["hr_min"] == 48.0
    assert signals["hr_max"] == 60.0
    assert signals["hr_samples"] == 2
    assert signals["snoring_seconds"] == 90.0
