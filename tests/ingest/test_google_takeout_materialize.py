from __future__ import annotations

import json
import os
import zipfile

import pytest

from lynchpin.ingest import google_takeout_materialize
from lynchpin.ingest.google_takeout_materialize import GOOGLE_TAKEOUT_INVENTORY_SCHEMA_VERSION
from lynchpin.sources import google_takeout


def test_materialize_google_takeout_inventory_writes_schema_version(monkeypatch, tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    archive = raw / "takeout.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("Takeout/Tasks/Tasks.json", "{}")

    cfg = type("Cfg", (), {"accounts_root": tmp_path / "exports"})()
    monkeypatch.setattr(google_takeout_materialize, "get_config", lambda: cfg)

    manifest = google_takeout_materialize.materialize_google_takeout_inventory(root=raw)

    assert manifest["schema_version"] == GOOGLE_TAKEOUT_INVENTORY_SCHEMA_VERSION
    assert manifest["archive_count"] == 1
    assert manifest["member_count"] == 1


def test_inventory_reads_each_archive_once_and_preserves_summary(monkeypatch, tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    for index in range(2):
        with zipfile.ZipFile(raw / f"takeout-{index}.zip", "w") as zf:
            zf.writestr("Takeout/Chrome/History.json", "{}")
            zf.writestr("Takeout/Tasks/Tasks.json", "{}")

    cfg = type("Cfg", (), {"accounts_root": tmp_path / "exports"})()
    monkeypatch.setattr(google_takeout_materialize, "get_config", lambda: cfg)
    original = google_takeout.iter_archive_members
    reads = []

    def counted(path, **kwargs):
        reads.append(path)
        yield from original(path, **kwargs)

    monkeypatch.setattr(google_takeout, "iter_archive_members", counted)
    monkeypatch.setattr(google_takeout_materialize, "iter_archive_members", counted)

    manifest = google_takeout_materialize.materialize_google_takeout_inventory(root=raw)

    assert reads == sorted(raw.glob("*.zip"))
    output = cfg.accounts_root / "google/processed/takeout-inventory"
    archives = [json.loads(line) for line in (output / "archives.ndjson").read_text().splitlines()]
    members = [json.loads(line) for line in (output / "members.ndjson").read_text().splitlines()]
    assert len(archives) == 2
    assert all(row["member_count"] == 2 and row["chrome_history_members"] == 1 for row in archives)
    assert all(row["product_counts"] == {"Chrome": 1, "Tasks": 1} for row in archives)
    assert len(members) == manifest["member_count"] == 4
    assert manifest["product_counts"] == {"Chrome": 2, "Tasks": 2}


def test_inventory_reuses_unchanged_archives_and_matches_cold_scan(monkeypatch, tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    archives = [raw / f"takeout-{index}.zip" for index in range(3)]

    def write_archive(path, product):
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr(f"Takeout/{product}/one.json", "{}")
            zf.writestr(f"Takeout/{product}/two.json", "{}")

    for archive in archives:
        write_archive(archive, "Tasks")
    cfg = type("Cfg", (), {"accounts_root": tmp_path / "exports"})()
    monkeypatch.setattr(google_takeout_materialize, "get_config", lambda: cfg)
    original = google_takeout.iter_archive_members
    reads = []

    def counted(path, **kwargs):
        reads.append(path)
        yield from original(path, **kwargs)

    monkeypatch.setattr(google_takeout_materialize, "iter_archive_members", counted)
    first = google_takeout_materialize.materialize_google_takeout_inventory(root=raw)
    output = cfg.accounts_root / "google/processed/takeout-inventory"
    reads.clear()
    warm = google_takeout_materialize.materialize_google_takeout_inventory(root=raw)
    assert reads == []
    assert warm["reused_archive_count"] == 3
    assert warm["archive_bytes_scanned"] == 0
    assert warm["member_count"] == first["member_count"] == 6

    write_archive(archives[1], "Chrome")
    reads.clear()
    changed = google_takeout_materialize.materialize_google_takeout_inventory(root=raw)
    assert reads == [archives[1]]
    assert changed["reused_archive_count"] == 2
    assert changed["scanned_archive_count"] == 1
    assert changed["archive_bytes_scanned"] == archives[1].stat().st_size
    reused_archives = (output / "archives.ndjson").read_bytes()
    reused_members = (output / "members.ndjson").read_bytes()

    (output / "manifest.json").unlink()
    reads.clear()
    cold = google_takeout_materialize.materialize_google_takeout_inventory(root=raw)
    assert reads == archives
    assert (output / "archives.ndjson").read_bytes() == reused_archives
    assert (output / "members.ndjson").read_bytes() == reused_members
    assert cold["product_counts"] == changed["product_counts"] == {"Chrome": 2, "Tasks": 4}


def test_inventory_reports_malformed_archive_and_preserves_previous_on_failure(monkeypatch, tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    broken = raw / "broken.zip"
    broken.write_bytes(b"not a zip")
    good = raw / "good.zip"
    with zipfile.ZipFile(good, "w") as zf:
        zf.writestr("Takeout/Tasks/Tasks.json", "{}")
    cfg = type("Cfg", (), {"accounts_root": tmp_path / "exports"})()
    monkeypatch.setattr(google_takeout_materialize, "get_config", lambda: cfg)
    first = google_takeout_materialize.materialize_google_takeout_inventory(root=raw)
    assert first["unreadable_archives"] == [str(broken)]
    output = cfg.accounts_root / "google/processed/takeout-inventory"
    before = {name: (output / name).read_bytes() for name in ("members.ndjson", "archives.ndjson", "manifest.json")}
    original = google_takeout_materialize.iter_archive_members

    def interrupted(path, **kwargs):
        if path == good:
            raise KeyboardInterrupt
        yield from original(path, **kwargs)

    monkeypatch.setattr(google_takeout_materialize, "iter_archive_members", interrupted)
    with zipfile.ZipFile(good, "a") as zf:
        zf.writestr("Takeout/Tasks/other.json", "{}")

    with pytest.raises(KeyboardInterrupt):
        google_takeout_materialize.materialize_google_takeout_inventory(root=raw)
    assert (output / "members.ndjson").read_bytes() == before["members.ndjson"]
    assert (output / "archives.ndjson").read_bytes() == before["archives.ndjson"]
    assert (output / "manifest.json").read_bytes() == before["manifest.json"]


def test_inventory_rejects_tampered_cache_and_restored_mtime(monkeypatch, tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    archive = raw / "takeout.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("Takeout/Tasks/Tasks.json", "{}")
    cfg = type("Cfg", (), {"accounts_root": tmp_path / "exports"})()
    monkeypatch.setattr(google_takeout_materialize, "get_config", lambda: cfg)
    first = google_takeout_materialize.materialize_google_takeout_inventory(root=raw)
    output = cfg.accounts_root / "google/processed/takeout-inventory"
    (output / "members.ndjson").write_text("", encoding="utf-8")
    after_tamper = google_takeout_materialize.materialize_google_takeout_inventory(root=raw)
    assert after_tamper["scanned_archive_count"] == 1
    assert after_tamper["member_count"] == 1

    mtime = archive.stat().st_mtime_ns
    size = archive.stat().st_size
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("Takeout/Drive/Tasks.json", "{}")
    os.utime(archive, ns=(mtime, mtime))
    assert archive.stat().st_size == size
    after_change = google_takeout_materialize.materialize_google_takeout_inventory(root=raw)
    assert after_change["scanned_archive_count"] == 1
    assert after_change["product_counts"] == {"Drive": 1}
    assert after_change["input_versions"] != first["input_versions"]
