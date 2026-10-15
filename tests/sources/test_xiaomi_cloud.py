from __future__ import annotations

import json
from pathlib import Path

import pytest

from lynchpin.core.errors import SourceUnavailableError
from lynchpin.sources.xiaomi_cloud import readiness, xiaomi_envelopes


def write(root: Path, *rows: dict[str, object]) -> None:
    path = root / "xiaomi-cloud-20260825.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


@pytest.mark.parametrize("contents", ["broken\n", "{}\n", "null\n", "[]\n"])
def test_nonempty_invalid_lane_is_not_healthy(tmp_path: Path, contents: str) -> None:
    (tmp_path / "xiaomi-cloud-20300101.jsonl").write_text(contents, encoding="utf-8")

    report = readiness(tmp_path)

    assert report.status == "error"
    assert report.row_count is None
    assert "no valid envelopes" in report.reason


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


def test_missing_and_empty_lane_are_distinct(tmp_path: Path) -> None:
    missing = tmp_path / "absent"
    report = readiness(missing)
    assert report.status == "missing"
    assert report.row_count is None
    with pytest.raises(SourceUnavailableError):
        list(xiaomi_envelopes(root=missing))
    assert readiness(tmp_path).status == "empty"
    assert readiness(tmp_path).row_count == 0
    assert list(xiaomi_envelopes(root=tmp_path)) == []


def test_absent_default_witness_remains_optional(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("lynchpin.sources.xiaomi_cloud._lane_root", lambda root=None: tmp_path / "absent")
    assert readiness().status == "missing"
    assert list(xiaomi_envelopes()) == []


def test_unreadable_lane_is_not_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = Path.iterdir

    def denied(path: Path):
        if path == tmp_path:
            raise PermissionError("synthetic listing denied")
        return original(path)

    monkeypatch.setattr(Path, "iterdir", denied)
    report = readiness(tmp_path)
    assert report.status == "error"
    assert report.row_count is None
    assert "synthetic listing denied" in report.reason
    with pytest.raises(SourceUnavailableError, match="synthetic listing denied"):
        list(xiaomi_envelopes(root=tmp_path))


def test_wrong_type_lane_is_not_empty(tmp_path: Path) -> None:
    root = tmp_path / "regular-file"
    root.write_text("", encoding="utf-8")
    assert readiness(root).status == "error"
    assert readiness(root).row_count is None
    with pytest.raises(SourceUnavailableError):
        list(xiaomi_envelopes(root=root))


@pytest.mark.parametrize("partial", [False, True])
def test_unreadable_day_files_have_explicit_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, partial: bool,
) -> None:
    write(tmp_path, {"kind": "vendor_sync_pass", "failures": 0})
    denied = tmp_path / "xiaomi-cloud-20260826.jsonl"
    denied.write_text("{}\n", encoding="utf-8")
    original = Path.open

    def guarded_open(path: Path, *args, **kwargs):
        if path == denied or (not partial and path.parent == tmp_path):
            raise PermissionError("synthetic read denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    report = readiness(tmp_path)
    assert report.status == ("partial" if partial else "error")
    assert report.row_count == (1 if partial else None)
    assert denied.name in report.reason
    if partial:
        assert "readable files only" in report.reason
    with pytest.raises(SourceUnavailableError, match="synthetic read denied"):
        list(xiaomi_envelopes(root=tmp_path))


def test_non_utf8_day_file_is_unavailable(tmp_path: Path) -> None:
    (tmp_path / "xiaomi-cloud-20260826.jsonl").write_bytes(b"\xff\n")
    report = readiness(tmp_path)
    assert report.status == "error"
    assert report.row_count is None
    with pytest.raises(SourceUnavailableError):
        list(xiaomi_envelopes(root=tmp_path))
