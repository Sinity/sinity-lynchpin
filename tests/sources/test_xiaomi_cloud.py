from __future__ import annotations

from pathlib import Path

import pytest

from lynchpin.core.errors import SourceUnavailableError
from lynchpin.sources.xiaomi_cloud import xiaomi_envelopes


def test_missing_and_empty_lane_are_distinct(tmp_path: Path) -> None:
    with pytest.raises(SourceUnavailableError):
        list(xiaomi_envelopes(root=tmp_path / "absent"))
    assert list(xiaomi_envelopes(root=tmp_path)) == []


def test_absent_default_witness_remains_optional(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("lynchpin.sources.xiaomi_cloud._lane_root", lambda root=None: tmp_path / "absent")
    assert list(xiaomi_envelopes()) == []


def test_unreadable_lane_is_not_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = Path.iterdir

    def denied(path: Path):
        if path == tmp_path:
            raise PermissionError("synthetic listing denied")
        return original(path)

    monkeypatch.setattr(Path, "iterdir", denied)
    with pytest.raises(SourceUnavailableError, match="synthetic listing denied"):
        list(xiaomi_envelopes(root=tmp_path))


def test_wrong_type_lane_is_not_empty(tmp_path: Path) -> None:
    root = tmp_path / "regular-file"
    root.write_text("", encoding="utf-8")
    with pytest.raises(SourceUnavailableError):
        list(xiaomi_envelopes(root=root))


def test_unreadable_day_file_is_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    denied = tmp_path / "xiaomi-cloud-20260826.jsonl"
    denied.write_text("{}\n", encoding="utf-8")
    original = Path.open

    def guarded_open(path: Path, *args, **kwargs):
        if path == denied:
            raise PermissionError("synthetic read denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    with pytest.raises(SourceUnavailableError, match="synthetic read denied"):
        list(xiaomi_envelopes(root=tmp_path))


def test_non_utf8_day_file_is_unavailable(tmp_path: Path) -> None:
    (tmp_path / "xiaomi-cloud-20260826.jsonl").write_bytes(b"\xff\n")
    with pytest.raises(SourceUnavailableError):
        list(xiaomi_envelopes(root=tmp_path))
