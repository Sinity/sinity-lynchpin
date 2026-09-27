from __future__ import annotations

import json
import zipfile

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

    def counted(path):
        reads.append(path)
        yield from original(path)

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
