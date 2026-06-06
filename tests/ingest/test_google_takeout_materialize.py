from __future__ import annotations

import zipfile

from lynchpin.ingest import google_takeout_materialize
from lynchpin.ingest.google_takeout_materialize import GOOGLE_TAKEOUT_INVENTORY_SCHEMA_VERSION


def test_materialize_google_takeout_inventory_writes_schema_version(monkeypatch, tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    archive = raw / "takeout.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("Takeout/Tasks/Tasks.json", "{}")

    cfg = type("Cfg", (), {"exports_root": tmp_path / "exports"})()
    monkeypatch.setattr(google_takeout_materialize, "get_config", lambda: cfg)

    manifest = google_takeout_materialize.materialize_google_takeout_inventory(root=raw)

    assert manifest["schema_version"] == GOOGLE_TAKEOUT_INVENTORY_SCHEMA_VERSION
    assert manifest["archive_count"] == 1
    assert manifest["member_count"] == 1
