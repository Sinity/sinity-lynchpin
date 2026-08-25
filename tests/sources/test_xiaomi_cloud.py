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
