"""Tests for the sinnix-capture-v1 event-lane reader."""

import json
from datetime import date, datetime, timezone

from lynchpin.sources import sinnix_capture_lanes as lanes


def _write_lane_day(root, lane, day, records):
    lane_dir = root / lane
    lane_dir.mkdir(parents=True, exist_ok=True)
    path = lane_dir / f"{lane}-{day}.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    return path


def _envelope(lane, seq, ts, payload, raw_ref=None):
    return {
        "host": "sinnix-prime",
        "lane": lane,
        "payload": payload,
        "raw_ref": raw_ref,
        "schema": "sinnix-capture-v1",
        "schema_version": 1,
        "seq": seq,
        "ts": ts,
    }


def test_iter_lane_events_reads_and_bounds_by_day_filename(tmp_path):
    _write_lane_day(
        tmp_path,
        "notifications",
        "20260812",
        [_envelope("notifications", 1, 1786529597.3, {"app_name": "kitty", "summary": "hi"})],
    )
    _write_lane_day(
        tmp_path,
        "notifications",
        "20260101",
        [_envelope("notifications", 1, 1767225600.0, {"app_name": "old", "summary": "old"})],
    )

    events = list(lanes.iter_lane_events("notifications", tmp_path, start=date(2026, 8, 1)))

    assert len(events) == 1
    assert events[0].host == "sinnix-prime"
    assert events[0].payload["app_name"] == "kitty"
    assert events[0].timestamp.tzinfo == timezone.utc


def test_notification_events_extracts_typed_fields(tmp_path):
    _write_lane_day(
        tmp_path,
        "notifications",
        "20260812",
        [
            _envelope(
                "notifications",
                1,
                1786529597.3,
                {"app_name": "kitty", "summary": "build failed", "body": "mypy", "urgency": 2, "sender": ":1.1"},
            )
        ],
    )

    events = list(lanes.notification_events(tmp_path))

    assert len(events) == 1
    assert events[0].app_name == "kitty"
    assert events[0].summary == "build failed"
    assert events[0].urgency == 2


def test_mpris_events_extracts_typed_fields(tmp_path):
    _write_lane_day(
        tmp_path,
        "mpris",
        "20260812",
        [
            _envelope(
                "mpris",
                1,
                1786528986.6,
                {
                    "player": "chromium",
                    "event": "initial",
                    "status": "Paused",
                    "title": "starly",
                    "artist": "LONOWN",
                    "album": None,
                    "position_seconds": 23.4,
                    "duration_seconds": 173.0,
                },
            )
        ],
    )

    events = list(lanes.mpris_events(tmp_path))

    assert len(events) == 1
    assert events[0].player == "chromium"
    assert events[0].status == "Paused"
    assert events[0].duration_seconds == 173.0


def test_audio_index_entries_reports_speech_presence(tmp_path):
    _write_lane_day(
        tmp_path,
        "audio-index",
        "20260812",
        [
            _envelope(
                "audio-index",
                1,
                1786536055.5,
                {
                    "channel": "mic",
                    "kind": "speech-index",
                    "segment": "audio-mic-20260812T110000Z.opus",
                    "segment_start": 1786532400.0,
                    "speech_spans": [],
                },
                raw_ref="/realm/data/captures/audio/mic/audio-mic-20260812T110000Z.opus",
            )
        ],
    )

    entries = list(lanes.audio_index_entries(tmp_path))

    assert len(entries) == 1
    assert entries[0].channel == "mic"
    assert entries[0].has_speech is False
    assert entries[0].raw_ref is not None
    assert entries[0].segment_start is not None


def test_audio_topology_events_extracts_typed_fields(tmp_path):
    _write_lane_day(
        tmp_path,
        "audio-topology",
        "20260812",
        [
            _envelope(
                "audio-topology",
                1,
                1786532607.7,
                {"action": "added", "id": 96, "kind": "link"},
            )
        ],
    )

    events = list(lanes.audio_topology_events(tmp_path))

    assert len(events) == 1
    assert events[0].action == "added"
    assert events[0].object_id == 96


def test_daily_lane_activity_counts_by_logical_day(tmp_path):
    _write_lane_day(
        tmp_path,
        "mpris",
        "20260812",
        [
            _envelope("mpris", 1, datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc).timestamp(), {"player": "a"}),
            _envelope("mpris", 2, datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc).timestamp(), {"player": "a"}),
        ],
    )

    activity = lanes.daily_lane_activity("mpris", tmp_path)

    assert len(activity) == 1
    day, count = activity[0]
    assert count == 2
    assert isinstance(day, date)


def test_iter_lane_events_skips_missing_root(tmp_path):
    assert list(lanes.iter_lane_events("notifications", tmp_path)) == []
