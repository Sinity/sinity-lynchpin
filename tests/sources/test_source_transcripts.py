"""Tests for the transcripts source."""

import json
from datetime import date

from lynchpin.sources import transcripts as tr


def _write_day(root, day, records):
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{day}.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")
    return path


def _write_ledger(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")


def test_readiness_missing_dir(tmp_path):
    r = tr.readiness(tmp_path / "nope")
    assert r.status == "missing"


def test_readiness_counts_across_day_files_only(tmp_path):
    _write_day(tmp_path, "2026-08-13", [
        {"schema": "sinnix.transcript/1", "file": "ambient-20260813T000236Z.m4a",
         "lane": "phone", "speech_seconds": 0, "audio_seconds": 300.0,
         "segments": [], "text": "", "transcribed_at": "2026-08-14T12:00:00Z"},
    ])
    _write_ledger(tmp_path / "transcribed.jsonl", [
        {"file": "ambient-20260813T000236Z.m4a", "bytes": 100, "lane": "phone",
         "speech_seconds": 0, "at": "2026-08-14T12:00:00Z"},
    ])
    r = tr.readiness(tmp_path)
    # transcribed.jsonl is NOT a YYYY-MM-DD day file and must not be double-counted.
    assert r.status == "ok"
    assert r.row_count == 1


def test_transcript_dedupes_repeated_file_keeping_last(tmp_path):
    _write_day(tmp_path, "2026-08-13", [
        {"schema": "sinnix.transcript/1", "file": "ambient-20260813T130526Z.m4a",
         "lane": "phone", "engine": "old-engine", "audio_seconds": 300.0,
         "speech_seconds": 0, "segments": [], "text": "",
         "transcribed_at": "2026-08-13T12:00:00Z"},
        {"schema": "sinnix.transcript/1", "file": "ambient-20260813T130526Z.m4a",
         "lane": "phone", "engine": "parakeet-tdt-0.6b-v3-int8", "audio_seconds": 300.16,
         "speech_seconds": 5.38, "rtf": 0.0119,
         "segments": [{"start": 1.0, "end": 2.0, "text": "hi", "words": []}],
         "text": "hi", "transcribed_at": "2026-08-14T12:03:38Z"},
    ])
    records = list(tr.transcripts(root=tmp_path))
    assert len(records) == 1
    record = records[0]
    assert record.engine == "parakeet-tdt-0.6b-v3-int8"
    assert record.speech_seconds == 5.38
    assert len(record.segments) == 1
    assert record.segments[0].text == "hi"


def test_transcript_date_is_recording_date_not_transcription_date(tmp_path):
    # File recorded 2026-08-12 but only transcribed on 2026-08-14 (matches the
    # live data: 2026-08-12 chunks were all transcribed on 2026-08-14).
    _write_day(tmp_path, "2026-08-14", [
        {"schema": "sinnix.transcript/1", "file": "ambient-20260812T120000Z.m4a",
         "lane": "phone", "audio_seconds": 300.0, "speech_seconds": 0,
         "segments": [], "text": "", "transcribed_at": "2026-08-14T12:00:00Z"},
    ])
    records = list(tr.transcripts(root=tmp_path))
    assert records[0].date == date(2026, 8, 12)
    assert records[0].transcribed_at.date() == date(2026, 8, 14)


def test_undecodable_transcript_has_no_speech_and_error(tmp_path):
    _write_day(tmp_path, "2026-08-13", [
        {"schema": "sinnix.transcript/1", "file": "ambient-20260813T000236Z.m4a",
         "undecodable": True, "error": "ffmpeg failed", "audio_seconds": None,
         "speech_seconds": 0, "segments": [], "text": "",
         "transcribed_at": "2026-08-14T12:02:57Z", "lane": "phone", "bytes": 43360},
    ])
    records = list(tr.transcripts(root=tmp_path))
    assert records[0].undecodable is True
    assert records[0].error == "ffmpeg failed"
    assert records[0].audio_seconds is None


def test_daily_speech_aggregates_and_skips_absent_days(tmp_path):
    _write_day(tmp_path, "2026-08-13", [
        {"schema": "sinnix.transcript/1", "file": "ambient-20260813T060000Z.m4a",
         "lane": "phone", "audio_seconds": 300.0, "speech_seconds": 5.0,
         "segments": [], "text": "hi", "transcribed_at": "2026-08-13T07:00:00Z"},
        {"schema": "sinnix.transcript/1", "file": "ambient-20260813T120000Z.m4a",
         "undecodable": True, "audio_seconds": None, "speech_seconds": 0,
         "segments": [], "text": "", "transcribed_at": "2026-08-13T13:00:00Z", "lane": "phone"},
    ])
    days = tr.daily_speech(root=tmp_path, start=date(2026, 8, 12), end=date(2026, 8, 14))
    assert len(days) == 1
    row = days[0]
    assert row.date == date(2026, 8, 13)
    assert row.chunk_count == 2
    assert row.undecodable_count == 1
    assert row.speech_seconds_sum == 5.0


def test_transcription_ledger_dedupes_by_file(tmp_path):
    path = tmp_path / "transcribed.jsonl"
    _write_ledger(path, [
        {"file": "ambient-20260813T130526Z.m4a", "bytes": 100, "lane": "phone",
         "speech_seconds": 0, "at": "2026-08-13T12:00:00Z"},
        {"file": "ambient-20260813T130526Z.m4a", "bytes": 200, "lane": "phone",
         "speech_seconds": 5.38, "at": "2026-08-14T12:03:38Z"},
    ])
    entries = list(tr.transcription_ledger(path=path))
    assert len(entries) == 1
    assert entries[0].bytes == 200
    assert entries[0].speech_seconds == 5.38
