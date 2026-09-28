from __future__ import annotations

import json
from pathlib import Path

from lynchpin.sources.xiaomi_cloud import readiness


def write(root: Path, *rows: dict[str, object]) -> None:
    path = root / "xiaomi-cloud-20260825.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_readiness_reports_latest_failed_capture(tmp_path: Path) -> None:
    write(
        tmp_path,
        {"kind": "vendor_sleep", "day": "2026-08-17", "fetched_at": "2026-08-17T01:00:00Z", "data": {}},
        {"kind": "vendor_fetch_failed", "day": None, "fetched_at": "2026-08-25T01:00:00Z", "reason": "expired token"},
    )

    report = readiness(tmp_path)

    assert report.status == "error"
    assert "expired token" in report.reason


def test_readiness_accepts_quiet_successful_sync_receipt(tmp_path: Path) -> None:
    write(
        tmp_path,
        {"kind": "vendor_fetch_failed", "day": None, "fetched_at": "2026-08-25T01:00:00Z", "reason": "expired token"},
        {"kind": "vendor_sync_pass", "day": None, "fetched_at": "2026-08-25T02:00:00Z", "failures": 0, "appended": 0, "unchanged": 10},
    )

    report = readiness(tmp_path)

    assert report.status == "ok"


def test_equal_timestamp_failed_pass_receipt_wins_after_success_rows(tmp_path: Path) -> None:
    stamp = "2026-08-25T02:00:00Z"
    write(
        tmp_path,
        {"kind": "vendor_sleep", "day": "2026-08-24", "fetched_at": stamp, "data": {}},
        {"kind": "vendor_sync_pass", "day": None, "fetched_at": stamp, "failures": 1},
    )
    report = readiness(tmp_path)
    assert report.status == "error"
    assert "1 failed lanes" in report.reason


def test_trailing_data_without_receipt_is_incomplete(tmp_path: Path) -> None:
    write(
        tmp_path,
        {"kind": "vendor_sync_pass", "day": None, "fetched_at": "2026-08-25T02:00:00Z", "failures": 0},
        {"kind": "vendor_sleep", "day": "2026-08-24", "fetched_at": "2026-08-25T02:00:00Z", "data": {}},
    )
    report = readiness(tmp_path)
    assert report.status == "partial"
    assert "no completion receipt" in report.reason


def test_later_successful_pass_supersedes_earlier_failed_pass_at_same_timestamp(
    tmp_path: Path,
) -> None:
    stamp = "2026-08-25T02:00:00Z"
    write(
        tmp_path,
        {"kind": "vendor_sync_pass", "day": None, "fetched_at": stamp, "failures": 1},
        {"kind": "vendor_sync_pass", "day": None, "fetched_at": stamp, "failures": 0},
    )
    report = readiness(tmp_path)
    assert report.status == "ok"
