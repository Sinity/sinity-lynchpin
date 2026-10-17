from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import pytest

from lynchpin.cli import health_io


def test_health_processor_and_readers_share_custom_roots(tmp_path):
    root = tmp_path / "health"
    cloud = tmp_path / "cloud"
    env = os.environ.copy()
    env.update(LYNCHPIN_HEALTH_ROOT=str(root), LYNCHPIN_SAMSUNG_GDPR_CLOUD=str(cloud))
    env.pop("LYNCHPIN_SLEEP_JSONL", None)
    code = "from lynchpin.cli import health_io; from lynchpin.core.config import get_config; import json; print(json.dumps([str(health_io.HEALTH_RAW),str(health_io.SAA_RAW),str(health_io.PROCESSED),str(health_io.GDPR_CLOUD_DIR),str(get_config().sleep_jsonl)]))"
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == [str(root / "raw/samsung-health"), str(root / "raw/sleep-as-android"), str(root / "processed"), str(cloud), str(root / "processed/sleep_merged.jsonl")]
    assert not root.exists()


def test_process_health_dry_run_does_not_create_output(tmp_path, monkeypatch):
    from lynchpin.cli import process_health
    output = tmp_path / "uncreated"
    calls = []
    monkeypatch.setattr(process_health, "PROCESSED", output)
    monkeypatch.setattr(process_health, "MERGED_PROCESSORS", (lambda dry: calls.append(dry),))
    monkeypatch.setattr(process_health, "GDPR_ONLY_PROCESSORS", ())
    monkeypatch.setattr(process_health, "process_sleep", lambda dry: calls.append(dry))
    monkeypatch.setattr(sys, "argv", ["process_health", "--dry-run"])
    process_health.main()
    assert calls == [True, True]
    assert not output.exists()


def test_write_jsonl_emits_product_manifest(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(health_io, "PROCESSED", tmp_path)

    count = health_io.write_jsonl(
        [
            {"date": "2026-06-02", "value": 1},
            {"start_time": "2026-06-01T23:00:00+02:00", "value": 2},
        ],
        "health_fixture.jsonl",
        "Fixture",
        dry_run=False,
    )

    product = tmp_path / "health_fixture.jsonl"
    manifest = tmp_path / "health_fixture.manifest.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert count == 2
    assert product.read_text(encoding="utf-8").count("\n") == 2
    assert payload["dataset"] == "health.health_fixture"
    assert payload["label"] == "Fixture"
    assert payload["row_count"] == 2
    assert payload["first_date"] == "2026-06-01"
    assert payload["last_date"] == "2026-06-02"
    assert payload["materialized_at"]
    assert payload["sha256"] == hashlib.sha256(product.read_bytes()).hexdigest()


def test_write_jsonl_dry_run_does_not_write_manifest(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(health_io, "PROCESSED", tmp_path)

    count = health_io.write_jsonl(
        [{"date": "2026-06-02"}],
        "health_fixture.jsonl",
        "Fixture",
        dry_run=True,
    )

    assert count == 1
    assert not (tmp_path / "health_fixture.jsonl").exists()
    assert not (tmp_path / "health_fixture.manifest.json").exists()


@pytest.mark.parametrize("failure", ["serialization", "manifest", "pointer"])
def test_failed_publication_keeps_complete_previous_generation(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(health_io, "PROCESSED", tmp_path)
    health_io.write_jsonl([{"date": "2026-06-01", "value": 1}], "health_fixture.jsonl", "Fixture", False)
    data = tmp_path / "health_fixture.jsonl"
    manifest = data.with_suffix(".manifest.json")
    before = data.read_bytes(), manifest.read_bytes(), data.resolve()
    rows = [{"date": "2026-06-02", "value": 2}]
    if failure == "serialization":
        rows.append({"bad": object()})
        error = TypeError
    elif failure == "manifest":
        def reject(*args):
            raise OSError("synthetic metadata write failure")
        monkeypatch.setattr(health_io, "_write_synced", reject)
        error = OSError
    else:
        original = health_io._replace_link
        def interrupt(path, target):
            if path.name == "current":
                raise KeyboardInterrupt()
            original(path, target)
        monkeypatch.setattr(health_io, "_replace_link", interrupt)
        error = KeyboardInterrupt
    with pytest.raises(error):
        health_io.write_jsonl(rows, data.name, "Fixture", False)
    assert (data.read_bytes(), manifest.read_bytes(), data.resolve()) == before


def test_legacy_preimages_and_sleep_companions_publish_together(tmp_path, monkeypatch):
    monkeypatch.setattr(health_io, "PROCESSED", tmp_path)
    primary = tmp_path / "sleep_all_nights.jsonl"
    alias = tmp_path / "sleep_merged.jsonl"
    primary.write_text('{"old":1}\n')
    alias.write_text('{"distinct_old":2}\n')
    health_io.write_jsonl([{"date": "2026-06-01"}], primary.name, "Fixture", False,
                         aliases=(alias.name,), sidecars={"stats.json": '{"count":1}\n'})
    assert primary.read_bytes() == alias.read_bytes()
    assert primary.resolve().parent == alias.resolve().parent == (tmp_path / "stats.json").resolve().parent
    generations = tmp_path / ".sleep_all_nights.generations"
    assert any((p / alias.name).exists() and (p / alias.name).read_text() == '{"distinct_old":2}\n'
               for p in generations.iterdir() if p.is_dir())


def test_open_reader_and_selected_manifest_keep_their_generation(tmp_path, monkeypatch):
    from lynchpin.sources import health_loaders
    monkeypatch.setattr(health_io, "PROCESSED", tmp_path)
    monkeypatch.setattr(health_loaders, "_PROCESSED", tmp_path)
    health_io.write_jsonl([{"value": 1}, {"value": 2}], "health_fixture.jsonl", "Fixture", False)
    manifest = json.loads((tmp_path / "health_fixture.manifest.json").read_text())
    reader = health_loaders.load_jsonl("health_fixture.jsonl")
    assert next(reader)["value"] == 1
    health_io.write_jsonl([{"value": 3}], "health_fixture.jsonl", "Fixture", False)
    assert [row["value"] for row in reader] == [2]
    assert len(open(manifest["path"]).readlines()) == manifest["row_count"] == 2


@pytest.mark.parametrize("failure", ["missing-root", "wrong-root", "wrong-category", "denied", "io"])
def test_gdpr_unavailable_inputs_cannot_replace_a_published_product(tmp_path, monkeypatch, failure):
    from lynchpin.cli import health_signal_processors
    from lynchpin.core.errors import SourceUnavailableError

    cloud = tmp_path / "cloud"
    raw = tmp_path / "raw"
    raw.mkdir()
    category = cloud / "Heart Rate"
    if failure == "wrong-root":
        cloud.write_text("retained wrong-type source")
    elif failure != "missing-root":
        cloud.mkdir()
        if failure == "wrong-category":
            category.write_text("retained wrong-type category")
        else:
            category.mkdir()
            (category / "Heart Rate.csv").write_text("datauuid,heart_rate\nfixture,62\n")
    monkeypatch.setattr(health_io, "GDPR_CLOUD_DIR", cloud)
    monkeypatch.setattr(health_signal_processors, "HEALTH_RAW", raw)
    monkeypatch.setattr(health_io, "HEALTH_RAW", raw)
    monkeypatch.setattr(health_io, "PROCESSED", tmp_path / "processed")
    health_io.write_jsonl([{"datauuid": "retained", "start_time": "2026-06-01T12:00:00Z"}],
                         "health_heart_rate.jsonl", "Heart Rate", False)
    product = health_io.PROCESSED / "health_heart_rate.jsonl"
    before = product.read_bytes()
    pointer = product.readlink()
    if failure in ("denied", "io"):
        original = os.scandir
        def unavailable(path):
            if os.fspath(path) == str(category):
                if failure == "denied":
                    raise PermissionError("synthetic health source denied")
                raise OSError("synthetic health source I/O error")
            return original(path)
        monkeypatch.setattr(os, "scandir", unavailable)
    with pytest.raises(SourceUnavailableError):
        health_signal_processors.process_heart_rate()
    assert product.readlink() == pointer
    assert product.read_bytes() == before


def test_gdpr_accessible_optional_category_and_empty_category_remain_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(health_io, "GDPR_CLOUD_DIR", tmp_path)
    assert health_io.read_gdpr_cloud_csvs("Optional") == []
    (tmp_path / "Optional").mkdir()
    assert health_io.read_gdpr_cloud_csvs("Optional") == []
