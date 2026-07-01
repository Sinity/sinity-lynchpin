from __future__ import annotations

import json
import inspect
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from lynchpin.ingest.webhistory import (
    WEBHISTORY_FULL_HISTORY_SCHEMA_VERSION,
    _write_raw_batch,
    build_full_history,
    dedup_raw_files,
    full_history_manifest_path,
)
from lynchpin.materialization import MaterializedDataset, ensure_materialized, render_materialization_audit
from lynchpin.sources.web_models import WebHistoryVisit

MACHINE_TABLE_NAMES = (
    "metric_sample",
    "gpu_sample",
    "network_sample",
    "service_state",
    "block_device_sample",
    "service_cgroup_io_sample",
    "service_cgroup_pressure_sample",
    "process_io_delta_sample",
    "process_memory_sample",
    "cgroup_memory_sample",
)


@dataclass(frozen=True)
class _MachineFixtureSample:
    observed_at: datetime
    value: str


def test_build_full_history_writes_manifest(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    segment = data_dir / "segment_unique_2026-01-01_to_2026-01-01.ndjson"
    segment.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "iso_time": "2026-01-01T10:00:00+00:00",
                        "url": "https://example.com/a",
                        "title": "A",
                        "source": "fixture",
                    }
                ),
                json.dumps(
                    {
                        "iso_time": "2026-01-01T10:01:00+00:00",
                        "url": "https://example.com/b",
                        "title": "B",
                        "source": "fixture",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    output = tmp_path / "derived" / "full_history.ndjson"
    report = build_full_history(data_dir=data_dir, output=output)

    manifest_path = full_history_manifest_path(output)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert report["row_count"] == 2
    assert output.exists()
    assert manifest["dataset"] == "webhistory.full_history"
    assert manifest["schema_version"] == WEBHISTORY_FULL_HISTORY_SCHEMA_VERSION
    assert manifest["row_count"] == 2
    assert manifest["first_visit_at"].startswith("2026-01-01T10:00:00")
    assert manifest["last_visit_at"].startswith("2026-01-01T10:01:00")
    assert manifest["first_date"] == "2026-01-01"
    assert manifest["last_date"] == "2026-01-01"
    assert manifest["dedup_tolerance_seconds"] == 30
    assert manifest["input_file_count"] == 1
    assert manifest["input_latest_mtime"] is not None
    assert manifest["segments"] == [
        {
            "path": str(segment),
            "input_visit_count": 2,
            "first_visit_at": "2026-01-01T10:00:00+00:00",
            "last_visit_at": "2026-01-01T10:01:00+00:00",
        }
    ]
    assert manifest["source_counts"] == {str(segment): 2}


def test_build_full_history_merges_requested_window(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    segment = data_dir / "segment_unique_2026-01-02_to_2026-01-02.ndjson"
    segment.write_text(
        json.dumps(
            {
                "iso_time": "2026-01-02T10:00:00+00:00",
                "url": "https://example.com/new",
                "title": "New",
                "source": "fixture",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "derived" / "full_history.ndjson"
    output.parent.mkdir()
    output.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "iso_time": "2026-01-01T10:00:00+00:00",
                        "url": "https://example.com/before",
                        "title": "Before",
                        "source": "old",
                    }
                ),
                json.dumps(
                    {
                        "iso_time": "2026-01-02T10:00:00+00:00",
                        "url": "https://example.com/old",
                        "title": "Old Window",
                        "source": "old",
                    }
                ),
                json.dumps(
                    {
                        "iso_time": "2026-01-03T10:00:00+00:00",
                        "url": "https://example.com/after",
                        "title": "After",
                        "source": "old",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    full_history_manifest_path(output).write_text(
        json.dumps(
            {
                "covered_dates": ["2026-01-01", "2026-01-02", "2026-01-03"],
                "first_date": "2026-01-01",
                "last_date": "2026-01-03",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = build_full_history(
        data_dir=data_dir,
        output=output,
        start=date(2026, 1, 2),
        end=date(2026, 1, 3),
    )

    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert [row["url"] for row in rows] == [
        "https://example.com/before",
        "https://example.com/new",
        "https://example.com/after",
    ]
    assert [row["source"] for row in rows] == ["old", str(segment), "old"]
    assert report["covered_dates"] == ["2026-01-01", "2026-01-02", "2026-01-03"]
    assert report["window_start"] == "2026-01-02"
    assert report["window_end"] == "2026-01-03"


def test_build_full_history_reports_logical_date_bounds(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    segment = data_dir / "segment_unique_2026-01-02_to_2026-01-02.ndjson"
    segment.write_text(
        json.dumps(
            {
                "iso_time": "2026-01-02T01:00:00+00:00",
                "url": "https://example.com/a",
                "title": "A",
                "source": "fixture",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    output = tmp_path / "derived" / "full_history.ndjson"
    report = build_full_history(data_dir=data_dir, output=output)
    manifest = json.loads(full_history_manifest_path(output).read_text(encoding="utf-8"))

    assert report["first_date"] == "2026-01-01"
    assert report["last_date"] == "2026-01-01"
    assert report["first_timestamp_date"] == "2026-01-02"
    assert report["last_timestamp_date"] == "2026-01-02"
    assert report["date_boundary"] == "logical_06:00_local"
    assert manifest["first_date"] == "2026-01-01"
    assert manifest["first_timestamp_date"] == "2026-01-02"


def test_webhistory_raw_and_dedup_reports_use_logical_date_bounds(tmp_path):
    raw_dir = tmp_path / "raw"
    data_dir = tmp_path / "data"
    raw_dir.mkdir()
    data_dir.mkdir()
    visit = WebHistoryVisit(
        timestamp=datetime(2026, 1, 2, 1, tzinfo=timezone.utc),
        url="https://example.com/a",
        title="A",
        source="fixture",
    )

    raw_report = _write_raw_batch(raw_dir, "manual_history.json", [visit])

    assert raw_report["path"].endswith("manual_history_2026-01-01_to_2026-01-01.ndjson")
    assert raw_report["first_date"] == "2026-01-01"
    assert raw_report["first_timestamp_date"] == "2026-01-02"

    dedup_report = dedup_raw_files(raw_dir=raw_dir, data_dir=data_dir)[0]

    assert dedup_report["kept_path"].endswith(
        "manual_history_2026-01-01_to_2026-01-01_unique_2026-01-01_to_2026-01-01.ndjson"
    )
    assert dedup_report["first_date"] == "2026-01-01"
    assert dedup_report["first_timestamp_date"] == "2026-01-02"
    assert dedup_report["date_boundary"] == "logical_06:00_local"


def test_webhistory_audit_marks_manifest_with_changed_segment_files_partial(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization

    segment = tmp_path / "segment_unique_2026-01-01.ndjson"
    segment.write_text("{}\n", encoding="utf-8")
    product = tmp_path / "full_history.ndjson"
    manifest = tmp_path / "full_history.manifest.json"
    product.write_text(
        json.dumps(
            {
                "iso_time": "2026-01-01T10:00:00+00:00",
                "url": "https://example.com",
                "title": "Example",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    manifest.write_text(
        json.dumps(
            {
                "row_count": 1,
                "first_date": "2026-01-01",
                "last_date": "2026-01-01",
                "first_visit_at": "2026-01-01T10:00:00+00:00",
                "last_visit_at": "2026-01-01T10:00:00+00:00",
                "input_files": [str(segment)],
                "input_file_count": 1,
                "input_latest_mtime": "2000-01-01T00:00:00+00:00",
                "schema_version": WEBHISTORY_FULL_HISTORY_SCHEMA_VERSION,
            }
        ),
        encoding="utf-8",
    )
    cfg = SimpleNamespace(
        webhistory_ndjson=product,
        webhistory_raw_dir=tmp_path / "raw",
        webhistory_dir=tmp_path,
        exports_root=tmp_path / "exports",
    )

    monkeypatch.setattr(materialization, "full_history_manifest_path", lambda _output: manifest)

    row = materialization._webhistory_dataset(cfg)

    assert row.status == "partial"
    assert "older webhistory segment files" in row.reason


def test_webhistory_audit_uses_manifest_logical_bounds_without_scan(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization

    segment = tmp_path / "segment_unique_2026-01-02.ndjson"
    segment.write_text("{}\n", encoding="utf-8")
    product = tmp_path / "full_history.ndjson"
    manifest = tmp_path / "full_history.manifest.json"
    product.write_text("not json\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "row_count": 1,
                "first_date": "2026-01-01",
                "last_date": "2026-01-01",
                "first_visit_at": "2026-01-02T01:00:00+00:00",
                "last_visit_at": "2026-01-02T01:00:00+00:00",
                "input_files": [str(segment)],
                "input_file_count": 1,
                "input_latest_mtime": datetime.fromtimestamp(segment.stat().st_mtime, timezone.utc).astimezone().isoformat(),
                "schema_version": WEBHISTORY_FULL_HISTORY_SCHEMA_VERSION,
            }
        ),
        encoding="utf-8",
    )
    cfg = SimpleNamespace(
        webhistory_ndjson=product,
        webhistory_raw_dir=tmp_path / "raw",
        webhistory_dir=tmp_path,
        exports_root=tmp_path / "exports",
    )

    monkeypatch.setattr(materialization, "full_history_manifest_path", lambda _output: manifest)
    monkeypatch.setattr(
        materialization,
        "_scan_webhistory_ndjson",
        lambda _path: (_ for _ in ()).throw(AssertionError("webhistory audit should trust manifest bounds")),
    )

    row = materialization._webhistory_dataset(cfg)

    assert row.status == "ready"
    assert row.first_date == date(2026, 1, 1)
    assert row.last_date == date(2026, 1, 1)


def test_webhistory_audit_reads_precise_covered_dates(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization

    segment = tmp_path / "segment_unique_2026-01-01.ndjson"
    segment.write_text("{}\n", encoding="utf-8")
    product = tmp_path / "full_history.ndjson"
    manifest = tmp_path / "full_history.manifest.json"
    product.write_text("{}\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "row_count": 2,
                "first_date": "2026-01-01",
                "last_date": "2026-01-03",
                "covered_dates": ["2026-01-01", "2026-01-03"],
                "first_visit_at": "2026-01-01T10:00:00+00:00",
                "last_visit_at": "2026-01-03T10:00:00+00:00",
                "input_files": [str(segment)],
                "input_file_count": 1,
                "input_latest_mtime": datetime.fromtimestamp(segment.stat().st_mtime, timezone.utc).astimezone().isoformat(),
                "schema_version": WEBHISTORY_FULL_HISTORY_SCHEMA_VERSION,
            }
        ),
        encoding="utf-8",
    )
    cfg = SimpleNamespace(
        webhistory_ndjson=product,
        webhistory_raw_dir=tmp_path / "raw",
        webhistory_dir=tmp_path,
        exports_root=tmp_path / "exports",
    )

    monkeypatch.setattr(materialization, "full_history_manifest_path", lambda _output: manifest)

    row = materialization._webhistory_dataset(cfg)

    assert row.status == "ready"
    assert row.covered_dates == (date(2026, 1, 1), date(2026, 1, 3))


def test_webhistory_audit_missing_manifest_bounds_does_not_scan(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization

    segment = tmp_path / "segment_unique_2026-01-02.ndjson"
    segment.write_text("{}\n", encoding="utf-8")
    product = tmp_path / "full_history.ndjson"
    manifest = tmp_path / "full_history.manifest.json"
    product.write_text(
        json.dumps(
            {
                "iso_time": "2026-01-02T01:00:00+00:00",
                "url": "https://example.com",
                "title": "Example",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    manifest.write_text(
        json.dumps(
            {
                "row_count": 1,
                "input_files": [str(segment)],
                "input_file_count": 1,
                "input_latest_mtime": datetime.fromtimestamp(segment.stat().st_mtime, timezone.utc).astimezone().isoformat(),
                "schema_version": WEBHISTORY_FULL_HISTORY_SCHEMA_VERSION,
            }
        ),
        encoding="utf-8",
    )
    cfg = SimpleNamespace(
        webhistory_ndjson=product,
        webhistory_raw_dir=tmp_path / "raw",
        webhistory_dir=tmp_path,
        exports_root=tmp_path / "exports",
    )

    monkeypatch.setattr(materialization, "full_history_manifest_path", lambda _output: manifest)
    monkeypatch.setattr(
        materialization,
        "_scan_webhistory_ndjson",
        lambda _path: (_ for _ in ()).throw(AssertionError("webhistory audit should not scan missing bounds")),
    )

    row = materialization._webhistory_dataset(cfg)

    assert row.status == "partial"
    assert row.row_count == 1
    assert row.first_date is None
    assert row.last_date is None
    assert "missing cheap row or coverage bounds" in row.reason


def test_webhistory_audit_marks_old_schema_partial(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization

    segment = tmp_path / "segment_unique_2026-01-01.ndjson"
    segment.write_text("{}\n", encoding="utf-8")
    product = tmp_path / "full_history.ndjson"
    manifest = tmp_path / "full_history.manifest.json"
    product.write_text(
        json.dumps(
            {
                "iso_time": "2026-01-01T10:00:00+00:00",
                "url": "https://example.com",
                "title": "Example",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    manifest.write_text(
        json.dumps(
            {
                "row_count": 1,
                "first_date": "2026-01-01",
                "last_date": "2026-01-01",
                "first_visit_at": "2026-01-01T10:00:00+00:00",
                "last_visit_at": "2026-01-01T10:00:00+00:00",
                "input_files": [str(segment)],
                "input_file_count": 1,
                "input_latest_mtime": datetime.fromtimestamp(segment.stat().st_mtime, timezone.utc).astimezone().isoformat(),
                "schema_version": 0,
            }
        ),
        encoding="utf-8",
    )
    cfg = SimpleNamespace(
        webhistory_ndjson=product,
        webhistory_raw_dir=tmp_path / "raw",
        webhistory_dir=tmp_path,
        exports_root=tmp_path / "exports",
    )

    monkeypatch.setattr(materialization, "full_history_manifest_path", lambda _output: manifest)

    row = materialization._webhistory_dataset(cfg)

    assert row.status == "partial"
    assert "schema is older" in row.reason


def test_local_continuous_materializers_accept_windows() -> None:
    from lynchpin import materialization
    from lynchpin.core.source_contracts import source_contract

    offenders = []
    for name, fn in materialization._materializers().items():
        contract = source_contract(name)
        if contract.materialization_mode != "local" or contract.collection_model != "continuous":
            continue
        params = inspect.signature(fn).parameters
        if "start" not in params or "end" not in params:
            offenders.append(name)

    assert offenders == []


def test_google_takeout_audit_marks_manifest_with_changed_archives_partial(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization
    from lynchpin.ingest.google_takeout_materialize import GOOGLE_TAKEOUT_INVENTORY_SCHEMA_VERSION
    from lynchpin.ingest.google_takeout_products import GOOGLE_TAKEOUT_PRODUCTS_SCHEMA_VERSION
    from lynchpin.ingest.gmail_takeout_materialize import GMAIL_EVENTS_SCHEMA_VERSION

    raw_root = tmp_path / "google/raw/takeout"
    raw_root.mkdir(parents=True)
    archive = raw_root / "takeout.zip"
    archive.write_text("fixture", encoding="utf-8")
    inventory_dir = tmp_path / "inventory"
    products_dir = tmp_path / "products"
    gmail_path = tmp_path / "gmail/events.ndjson"
    gmail_manifest = gmail_path.with_suffix(".manifest.json")
    inventory_dir.mkdir()
    products_dir.mkdir()
    gmail_path.parent.mkdir()
    for path in (inventory_dir / "archives.ndjson", inventory_dir / "members.ndjson"):
        path.write_text("{}\n", encoding="utf-8")
    gmail_path.write_text("", encoding="utf-8")
    (inventory_dir / "manifest.json").write_text(
        json.dumps(
            {
                "archive_count": 1,
                "member_count": 1,
                "schema_version": GOOGLE_TAKEOUT_INVENTORY_SCHEMA_VERSION,
                "input_files": [str(archive)],
                "input_file_count": 1,
                "input_latest_mtime": "2000-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    (products_dir / "manifest.json").write_text(
        json.dumps(
            {
                "archive_count": 1,
                "schema_version": GOOGLE_TAKEOUT_PRODUCTS_SCHEMA_VERSION,
                "products": {"tasks": {"row_count": 1}},
                "input_files": [str(archive)],
                "input_file_count": 1,
                "input_latest_mtime": "2000-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    gmail_manifest.write_text(
        json.dumps(
            {
                "schema_version": GMAIL_EVENTS_SCHEMA_VERSION,
                "row_count": 0,
                "input_files": [str(archive)],
                "input_file_count": 1,
                "input_latest_mtime": "2000-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    cfg = SimpleNamespace(exports_root=tmp_path)

    monkeypatch.setattr(materialization, "google_takeout_inventory_dir", lambda: inventory_dir)
    monkeypatch.setattr(materialization, "google_takeout_products_dir", lambda: products_dir)
    monkeypatch.setattr(materialization, "gmail_events_path", lambda: gmail_path)
    monkeypatch.setattr(materialization, "gmail_manifest_path", lambda: gmail_manifest)
    monkeypatch.setattr(materialization, "discover_takeout_archives", lambda _root: (archive,))

    row = materialization._google_takeout_dataset(cfg)

    assert row.status == "partial"
    assert "archives changed" in row.reason


def test_google_takeout_audit_uses_product_manifest_bounds(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization
    from lynchpin.core.io import latest_mtime_iso
    from lynchpin.ingest.google_takeout_materialize import GOOGLE_TAKEOUT_INVENTORY_SCHEMA_VERSION
    from lynchpin.ingest.google_takeout_products import GOOGLE_TAKEOUT_PRODUCTS_SCHEMA_VERSION
    from lynchpin.ingest.gmail_takeout_materialize import GMAIL_EVENTS_SCHEMA_VERSION

    raw_root = tmp_path / "google/raw/takeout"
    raw_root.mkdir(parents=True)
    archive = raw_root / "takeout.zip"
    archive.write_text("fixture", encoding="utf-8")
    inventory_dir = tmp_path / "inventory"
    products_dir = tmp_path / "products"
    gmail_path = tmp_path / "gmail/events.ndjson"
    gmail_manifest = gmail_path.with_suffix(".manifest.json")
    inventory_dir.mkdir()
    products_dir.mkdir()
    gmail_path.parent.mkdir()
    for path in (inventory_dir / "archives.ndjson", inventory_dir / "members.ndjson"):
        path.write_text("{}\n", encoding="utf-8")
    gmail_path.write_text("", encoding="utf-8")
    latest = latest_mtime_iso((archive,))
    (inventory_dir / "manifest.json").write_text(
        json.dumps(
            {
                "archive_count": 1,
                "member_count": 1,
                "schema_version": GOOGLE_TAKEOUT_INVENTORY_SCHEMA_VERSION,
                "input_files": [str(archive)],
                "input_file_count": 1,
                "input_latest_mtime": latest,
            }
        ),
        encoding="utf-8",
    )
    (products_dir / "manifest.json").write_text(
        json.dumps(
            {
                "archive_count": 1,
                "schema_version": GOOGLE_TAKEOUT_PRODUCTS_SCHEMA_VERSION,
                "first_date": "2026-01-01",
                "last_date": "2026-01-02",
                "products": {"tasks": {"row_count": 1}},
                "input_files": [str(archive)],
                "input_file_count": 1,
                "input_latest_mtime": latest,
            }
        ),
        encoding="utf-8",
    )
    gmail_manifest.write_text(
        json.dumps(
            {
                "schema_version": GMAIL_EVENTS_SCHEMA_VERSION,
                "row_count": 0,
                "input_files": [str(archive)],
                "input_file_count": 1,
                "input_latest_mtime": latest,
            }
        ),
        encoding="utf-8",
    )
    cfg = SimpleNamespace(exports_root=tmp_path)

    monkeypatch.setattr(materialization, "google_takeout_inventory_dir", lambda: inventory_dir)
    monkeypatch.setattr(materialization, "google_takeout_products_dir", lambda: products_dir)
    monkeypatch.setattr(materialization, "gmail_events_path", lambda: gmail_path)
    monkeypatch.setattr(materialization, "gmail_manifest_path", lambda: gmail_manifest)
    monkeypatch.setattr(materialization, "discover_takeout_archives", lambda _root: (archive,))

    row = materialization._google_takeout_dataset(cfg)

    assert row.status == "ready"
    assert row.row_count == 2
    assert row.first_date == date(2026, 1, 1)
    assert row.last_date == date(2026, 1, 2)


def test_google_takeout_audit_marks_old_inventory_schema_partial(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization
    from lynchpin.core.io import latest_mtime_iso
    from lynchpin.ingest.google_takeout_products import GOOGLE_TAKEOUT_PRODUCTS_SCHEMA_VERSION
    from lynchpin.ingest.gmail_takeout_materialize import GMAIL_EVENTS_SCHEMA_VERSION

    raw_root = tmp_path / "google/raw/takeout"
    raw_root.mkdir(parents=True)
    archive = raw_root / "takeout.zip"
    archive.write_text("fixture", encoding="utf-8")
    inventory_dir = tmp_path / "inventory"
    products_dir = tmp_path / "products"
    gmail_path = tmp_path / "gmail/events.ndjson"
    gmail_manifest = gmail_path.with_suffix(".manifest.json")
    inventory_dir.mkdir()
    products_dir.mkdir()
    gmail_path.parent.mkdir()
    for path in (inventory_dir / "archives.ndjson", inventory_dir / "members.ndjson"):
        path.write_text("{}\n", encoding="utf-8")
    gmail_path.write_text("", encoding="utf-8")
    latest = latest_mtime_iso((archive,))
    (inventory_dir / "manifest.json").write_text(
        json.dumps(
            {
                "archive_count": 1,
                "member_count": 1,
                "schema_version": 0,
                "input_files": [str(archive)],
                "input_file_count": 1,
                "input_latest_mtime": latest,
            }
        ),
        encoding="utf-8",
    )
    (products_dir / "manifest.json").write_text(
        json.dumps(
            {
                "archive_count": 1,
                "schema_version": GOOGLE_TAKEOUT_PRODUCTS_SCHEMA_VERSION,
                "products": {"tasks": {"row_count": 1}},
                "input_files": [str(archive)],
                "input_file_count": 1,
                "input_latest_mtime": latest,
            }
        ),
        encoding="utf-8",
    )
    gmail_manifest.write_text(
        json.dumps(
            {
                "schema_version": GMAIL_EVENTS_SCHEMA_VERSION,
                "row_count": 0,
                "input_files": [str(archive)],
                "input_file_count": 1,
                "input_latest_mtime": latest,
            }
        ),
        encoding="utf-8",
    )
    cfg = SimpleNamespace(exports_root=tmp_path)

    monkeypatch.setattr(materialization, "google_takeout_inventory_dir", lambda: inventory_dir)
    monkeypatch.setattr(materialization, "google_takeout_products_dir", lambda: products_dir)
    monkeypatch.setattr(materialization, "gmail_events_path", lambda: gmail_path)
    monkeypatch.setattr(materialization, "gmail_manifest_path", lambda: gmail_manifest)
    monkeypatch.setattr(materialization, "discover_takeout_archives", lambda _root: (archive,))

    row = materialization._google_takeout_dataset(cfg)

    assert row.status == "partial"
    assert "inventory manifest schema" in row.reason


def test_google_takeout_audit_marks_old_product_schema_partial(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization
    from lynchpin.core.io import latest_mtime_iso
    from lynchpin.ingest.google_takeout_materialize import GOOGLE_TAKEOUT_INVENTORY_SCHEMA_VERSION
    from lynchpin.ingest.gmail_takeout_materialize import GMAIL_EVENTS_SCHEMA_VERSION

    raw_root = tmp_path / "google/raw/takeout"
    raw_root.mkdir(parents=True)
    archive = raw_root / "takeout.zip"
    archive.write_text("fixture", encoding="utf-8")
    inventory_dir = tmp_path / "inventory"
    products_dir = tmp_path / "products"
    gmail_path = tmp_path / "gmail/events.ndjson"
    gmail_manifest = gmail_path.with_suffix(".manifest.json")
    inventory_dir.mkdir()
    products_dir.mkdir()
    gmail_path.parent.mkdir()
    for path in (inventory_dir / "archives.ndjson", inventory_dir / "members.ndjson"):
        path.write_text("{}\n", encoding="utf-8")
    gmail_path.write_text("", encoding="utf-8")
    latest = latest_mtime_iso((archive,))
    (inventory_dir / "manifest.json").write_text(
        json.dumps(
            {
                "archive_count": 1,
                "member_count": 1,
                "schema_version": GOOGLE_TAKEOUT_INVENTORY_SCHEMA_VERSION,
                "input_files": [str(archive)],
                "input_file_count": 1,
                "input_latest_mtime": latest,
            }
        ),
        encoding="utf-8",
    )
    (products_dir / "manifest.json").write_text(
        json.dumps(
            {
                "archive_count": 1,
                "schema_version": 0,
                "products": {"tasks": {"row_count": 1}},
                "input_files": [str(archive)],
                "input_file_count": 1,
                "input_latest_mtime": latest,
            }
        ),
        encoding="utf-8",
    )
    gmail_manifest.write_text(
        json.dumps(
            {
                "schema_version": GMAIL_EVENTS_SCHEMA_VERSION,
                "row_count": 0,
                "input_files": [str(archive)],
                "input_file_count": 1,
                "input_latest_mtime": latest,
            }
        ),
        encoding="utf-8",
    )
    cfg = SimpleNamespace(exports_root=tmp_path)

    monkeypatch.setattr(materialization, "google_takeout_inventory_dir", lambda: inventory_dir)
    monkeypatch.setattr(materialization, "google_takeout_products_dir", lambda: products_dir)
    monkeypatch.setattr(materialization, "gmail_events_path", lambda: gmail_path)
    monkeypatch.setattr(materialization, "gmail_manifest_path", lambda: gmail_manifest)
    monkeypatch.setattr(materialization, "discover_takeout_archives", lambda _root: (archive,))

    row = materialization._google_takeout_dataset(cfg)

    assert row.status == "partial"
    assert "typed product manifest schema" in row.reason


def test_google_takeout_audit_marks_missing_gmail_product_partial(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization
    from lynchpin.core.io import latest_mtime_iso
    from lynchpin.ingest.google_takeout_materialize import GOOGLE_TAKEOUT_INVENTORY_SCHEMA_VERSION
    from lynchpin.ingest.google_takeout_products import GOOGLE_TAKEOUT_PRODUCTS_SCHEMA_VERSION

    raw_root = tmp_path / "google/raw/takeout"
    raw_root.mkdir(parents=True)
    archive = raw_root / "takeout.zip"
    archive.write_text("fixture", encoding="utf-8")
    inventory_dir = tmp_path / "inventory"
    products_dir = tmp_path / "products"
    gmail_path = tmp_path / "gmail/events.ndjson"
    gmail_manifest = gmail_path.with_suffix(".manifest.json")
    inventory_dir.mkdir()
    products_dir.mkdir()
    for path in (inventory_dir / "archives.ndjson", inventory_dir / "members.ndjson"):
        path.write_text("{}\n", encoding="utf-8")
    latest = latest_mtime_iso((archive,))
    (inventory_dir / "manifest.json").write_text(
        json.dumps(
            {
                "archive_count": 1,
                "member_count": 1,
                "schema_version": GOOGLE_TAKEOUT_INVENTORY_SCHEMA_VERSION,
                "input_files": [str(archive)],
                "input_file_count": 1,
                "input_latest_mtime": latest,
            }
        ),
        encoding="utf-8",
    )
    (products_dir / "manifest.json").write_text(
        json.dumps(
            {
                "archive_count": 1,
                "schema_version": GOOGLE_TAKEOUT_PRODUCTS_SCHEMA_VERSION,
                "products": {"tasks": {"row_count": 1}},
                "input_files": [str(archive)],
                "input_file_count": 1,
                "input_latest_mtime": latest,
            }
        ),
        encoding="utf-8",
    )
    cfg = SimpleNamespace(exports_root=tmp_path)

    monkeypatch.setattr(materialization, "google_takeout_inventory_dir", lambda: inventory_dir)
    monkeypatch.setattr(materialization, "google_takeout_products_dir", lambda: products_dir)
    monkeypatch.setattr(materialization, "gmail_events_path", lambda: gmail_path)
    monkeypatch.setattr(materialization, "gmail_manifest_path", lambda: gmail_manifest)
    monkeypatch.setattr(materialization, "discover_takeout_archives", lambda _root: (archive,))

    row = materialization._google_takeout_dataset(cfg)

    assert row.status == "partial"
    assert "Gmail event product is missing" in row.reason


def test_google_takeout_audit_marks_old_gmail_schema_partial(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization
    from lynchpin.core.io import latest_mtime_iso
    from lynchpin.ingest.google_takeout_materialize import GOOGLE_TAKEOUT_INVENTORY_SCHEMA_VERSION
    from lynchpin.ingest.google_takeout_products import GOOGLE_TAKEOUT_PRODUCTS_SCHEMA_VERSION

    raw_root = tmp_path / "google/raw/takeout"
    raw_root.mkdir(parents=True)
    archive = raw_root / "takeout.zip"
    archive.write_text("fixture", encoding="utf-8")
    inventory_dir = tmp_path / "inventory"
    products_dir = tmp_path / "products"
    gmail_path = tmp_path / "gmail/events.ndjson"
    gmail_manifest = gmail_path.with_suffix(".manifest.json")
    inventory_dir.mkdir()
    products_dir.mkdir()
    gmail_path.parent.mkdir()
    for path in (inventory_dir / "archives.ndjson", inventory_dir / "members.ndjson"):
        path.write_text("{}\n", encoding="utf-8")
    gmail_path.write_text("", encoding="utf-8")
    latest = latest_mtime_iso((archive,))
    (inventory_dir / "manifest.json").write_text(
        json.dumps(
            {
                "archive_count": 1,
                "member_count": 1,
                "schema_version": GOOGLE_TAKEOUT_INVENTORY_SCHEMA_VERSION,
                "input_files": [str(archive)],
                "input_file_count": 1,
                "input_latest_mtime": latest,
            }
        ),
        encoding="utf-8",
    )
    (products_dir / "manifest.json").write_text(
        json.dumps(
            {
                "archive_count": 1,
                "schema_version": GOOGLE_TAKEOUT_PRODUCTS_SCHEMA_VERSION,
                "products": {"tasks": {"row_count": 1}},
                "input_files": [str(archive)],
                "input_file_count": 1,
                "input_latest_mtime": latest,
            }
        ),
        encoding="utf-8",
    )
    gmail_manifest.write_text(
        json.dumps(
            {
                "schema_version": 0,
                "row_count": 0,
                "input_files": [str(archive)],
                "input_file_count": 1,
                "input_latest_mtime": latest,
            }
        ),
        encoding="utf-8",
    )
    cfg = SimpleNamespace(exports_root=tmp_path)

    monkeypatch.setattr(materialization, "google_takeout_inventory_dir", lambda: inventory_dir)
    monkeypatch.setattr(materialization, "google_takeout_products_dir", lambda: products_dir)
    monkeypatch.setattr(materialization, "gmail_events_path", lambda: gmail_path)
    monkeypatch.setattr(materialization, "gmail_manifest_path", lambda: gmail_manifest)
    monkeypatch.setattr(materialization, "discover_takeout_archives", lambda _root: (archive,))

    row = materialization._google_takeout_dataset(cfg)

    assert row.status == "partial"
    assert "Gmail event manifest schema" in row.reason


def test_render_materialization_audit_marks_non_ready_reasons(tmp_path):
    row = MaterializedDataset(
        name="example",
        status="partial",
        authority="raw fixture",
        query_surface="lynchpin.sources.example",
        materialized_paths=(tmp_path / "example.ndjson",),
        raw_roots=(tmp_path / "raw",),
        row_count=10,
        first_date=date(2020, 1, 1),
        last_date=date(2020, 1, 2),
        materialization_hint="example refresh",
        reason="canonical product missing",
    )

    rendered = render_materialization_audit([row])

    assert "| example | partial | 10 | 2020-01-01 -> 2020-01-02 |" in rendered
    assert "canonical product missing" in rendered


def test_materialization_contract_names_have_builders() -> None:
    from lynchpin.core.source_contracts import SOURCE_CONTRACT_NAMES
    from lynchpin.materialization import _dataset_builders

    assert tuple(_dataset_builders()) == SOURCE_CONTRACT_NAMES


def test_analysis_artifacts_dataset_reports_generated_products(tmp_path) -> None:
    from lynchpin.materialization import _analysis_artifacts_dataset

    root = tmp_path / "analysis"
    root.mkdir()
    (root / "workflow_mechanics.json").write_text(
        json.dumps({"generated_at_utc": "2026-06-02T00:00:00+00:00"}),
        encoding="utf-8",
    )

    row = _analysis_artifacts_dataset(SimpleNamespace(analysis_output_dir=root))

    assert row.name == "analysis_artifacts"
    assert row.status == "ready"
    assert row.row_count == 1
    assert row.first_date is None
    assert row.last_date is None
    assert row.to_json()["mcp_tools"] == [
        "analysis_artifact_status",
        "analysis_artifact_inventory",
        "read_analysis_artifact",
    ]


def test_keylog_analysis_dataset_reports_artifact_coverage(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization

    artifact = tmp_path / "analysis/keylog_analysis.json"
    artifact.parent.mkdir(parents=True)
    log = tmp_path / "keylog/logs/2026-06-05.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text("{}\n", encoding="utf-8")
    artifact.write_text(
        json.dumps(
            {
                "start": "2026-06-05",
                "end": "2026-06-06",
                "source_event_count": 7,
                "input_files": [str(log)],
                "input_file_count": 1,
                "input_latest_mtime": datetime.fromtimestamp(
                    log.stat().st_mtime,
                    timezone.utc,
                ).astimezone().isoformat(),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("lynchpin.core.io.resolve_analysis_path", lambda _name: str(artifact))

    row = materialization._keylog_analysis_dataset(
        SimpleNamespace(keylog_root=tmp_path / "keylog")
    )

    assert row.name == "keylog_analysis"
    assert row.status == "ready"
    assert row.row_count == 7
    assert row.first_date == date(2026, 6, 5)
    assert row.last_date == date(2026, 6, 6)
    assert row.covered_dates == (date(2026, 6, 5), date(2026, 6, 6))


def test_github_context_audit_marks_recent_product_ready(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization
    from lynchpin.sources.github_context import GITHUB_CONTEXT_SCHEMA_VERSION

    product = tmp_path / "github" / "context.ndjson"
    product.parent.mkdir()
    manifest = product.with_suffix(".manifest.json")
    product.write_text("{}\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "row_count": 1,
                "first_date": "2026-06-01",
                "last_date": "2026-06-02",
                "covered_dates": ["2026-06-01", "2026-06-02"],
                "input_file_count": 0,
                "materialized_at": datetime.now(timezone.utc).isoformat(),
                "ttl_seconds": 172800,
                "schema_version": GITHUB_CONTEXT_SCHEMA_VERSION,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(materialization, "github_context_path", lambda: product)
    monkeypatch.setattr(materialization, "github_context_manifest_path", lambda: manifest)

    row = materialization._github_context_dataset(SimpleNamespace(derived_root=tmp_path))

    assert row.status == "ready"
    assert row.covered_dates == (date(2026, 6, 1), date(2026, 6, 2))
    assert "48h" in row.reason


def test_github_context_audit_uses_network_ttl_not_local_git_mtime(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization
    from lynchpin.sources.github_context import GITHUB_CONTEXT_SCHEMA_VERSION

    product = tmp_path / "github" / "context.ndjson"
    product.parent.mkdir()
    manifest = product.with_suffix(".manifest.json")
    git_input = tmp_path / "repo/.git/logs/HEAD"
    git_input.parent.mkdir(parents=True)
    git_input.write_text("new local commit\n", encoding="utf-8")
    product.write_text("{}\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "row_count": 1,
                "first_date": "2026-06-01",
                "last_date": "2026-06-02",
                "input_files": [str(git_input)],
                "input_file_count": 1,
                "input_latest_mtime": "2000-01-01T00:00:00+00:00",
                "materialized_at": datetime.now(timezone.utc).isoformat(),
                "ttl_seconds": 172800,
                "schema_version": GITHUB_CONTEXT_SCHEMA_VERSION,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(materialization, "github_context_path", lambda: product)
    monkeypatch.setattr(materialization, "github_context_manifest_path", lambda: manifest)

    row = materialization._github_context_dataset(SimpleNamespace(derived_root=tmp_path))

    assert row.status == "ready"
    assert "48h" in row.reason


def test_github_context_audit_marks_old_product_partial(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization
    from lynchpin.sources.github_context import GITHUB_CONTEXT_SCHEMA_VERSION

    product = tmp_path / "github" / "context.ndjson"
    product.parent.mkdir()
    manifest = product.with_suffix(".manifest.json")
    product.write_text("{}\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "row_count": 1,
                "first_date": "2026-06-01",
                "last_date": "2026-06-02",
                "input_file_count": 0,
                "materialized_at": "2000-01-01T00:00:00+00:00",
                "ttl_seconds": 172800,
                "schema_version": GITHUB_CONTEXT_SCHEMA_VERSION,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(materialization, "github_context_path", lambda: product)
    monkeypatch.setattr(materialization, "github_context_manifest_path", lambda: manifest)

    row = materialization._github_context_dataset(SimpleNamespace(derived_root=tmp_path))

    assert row.status == "partial"
    assert "older than the 48h" in row.reason


def test_github_context_audit_honors_manifest_ttl(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization
    from lynchpin.sources.github_context import GITHUB_CONTEXT_SCHEMA_VERSION

    product = tmp_path / "github" / "context.ndjson"
    product.parent.mkdir()
    manifest = product.with_suffix(".manifest.json")
    product.write_text("{}\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "row_count": 1,
                "first_date": "2026-06-01",
                "last_date": "2026-06-02",
                "input_file_count": 0,
                "materialized_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
                "ttl_seconds": 3600,
                "schema_version": GITHUB_CONTEXT_SCHEMA_VERSION,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(materialization, "github_context_path", lambda: product)
    monkeypatch.setattr(materialization, "github_context_manifest_path", lambda: manifest)

    row = materialization._github_context_dataset(SimpleNamespace(derived_root=tmp_path))

    assert row.status == "partial"
    assert "older than the 1h" in row.reason


def test_github_context_audit_marks_old_schema_partial(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization

    product = tmp_path / "github" / "context.ndjson"
    product.parent.mkdir()
    manifest = product.with_suffix(".manifest.json")
    product.write_text("{}\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "row_count": 1,
                "first_date": "2026-06-01",
                "last_date": "2026-06-02",
                "input_file_count": 0,
                "materialized_at": datetime.now(timezone.utc).isoformat(),
                "ttl_seconds": 172800,
                "schema_version": 0,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(materialization, "github_context_path", lambda: product)
    monkeypatch.setattr(materialization, "github_context_manifest_path", lambda: manifest)

    row = materialization._github_context_dataset(SimpleNamespace(derived_root=tmp_path))

    assert row.status == "partial"
    assert "schema is older" in row.reason


def test_materialization_contract_names_have_explicit_local_materializer_policy() -> None:
    from lynchpin.core.source_contracts import SOURCE_CONTRACT_NAMES
    from lynchpin.core.source_contracts import source_contract
    from lynchpin.materialization import _dataset_builders, _materializers

    builder_names = set(_dataset_builders())
    materializer_names = set(_materializers())
    executable_contract_names = {
        name
        for name in materializer_names
        if source_contract(name).materialization_mode in {"local", "derived"}
    }

    assert builder_names == set(SOURCE_CONTRACT_NAMES)
    assert materializer_names <= builder_names
    assert materializer_names == executable_contract_names
    assert "polylogue" not in materializer_names
    assert "evidence_graph_substrate" not in materializer_names
    assert "title_metadata" in materializer_names
    assert "activitywatch_derived" in materializer_names
    assert "activity_content" in materializer_names


def test_materialized_dataset_json_carries_contract_status_semantics(tmp_path) -> None:
    row = MaterializedDataset(
        name="webhistory",
        status="partial",
        authority="raw fixture",
        query_surface="lynchpin.sources.web",
        materialized_paths=(tmp_path / "history.ndjson",),
        raw_roots=(tmp_path / "raw",),
        row_count=None,
        first_date=None,
        last_date=None,
        materialization_hint="refresh",
        reason="fixture missing",
    )

    payload = row.to_json()

    assert payload["kind"] == "dataset"
    assert payload["status"] == "partial"
    assert payload["substrate_status"] == "unavailable"
    assert payload["required"] is True
    assert payload["collection_model"] == "continuous"
    assert payload["coverage"]["relation"] == "unavailable"


def test_materialized_window_overlaps_uses_known_bounds(monkeypatch) -> None:
    from lynchpin import materialization

    rows = [
        MaterializedDataset(
            name="example",
            status="ready",
            authority="raw fixture",
            query_surface="fixture",
            materialized_paths=(),
            raw_roots=(),
            row_count=1,
            first_date=date(2020, 1, 1),
            last_date=date(2020, 1, 31),
            materialization_hint="materialize",
            reason="ready",
        ),
        MaterializedDataset(
            name="unknown_bounds",
            status="ready",
            authority="raw fixture",
            query_surface="fixture",
            materialized_paths=(),
            raw_roots=(),
            row_count=1,
            first_date=None,
            last_date=None,
            materialization_hint="refresh",
            reason="ready",
        ),
    ]
    monkeypatch.setattr(materialization, "audit_materialization", lambda cfg=None: rows)

    assert materialization.materialized_window_overlaps(
        "example", start=date(2020, 1, 15), end=date(2020, 1, 16)
    )
    assert not materialization.materialized_window_overlaps(
        "example", start=date(2020, 2, 1), end=date(2020, 2, 2)
    )
    assert not materialization.materialized_window_overlaps(
        "unknown_bounds", start=date(2020, 1, 1), end=date(2020, 1, 2)
    )


def test_materialized_dataset_coverage_describes_window_without_age_scoring() -> None:
    from lynchpin.materialization import materialized_dataset_coverage

    row = MaterializedDataset(
        name="reddit",
        status="ready",
        authority="raw fixture",
        query_surface="fixture",
        materialized_paths=(),
        raw_roots=(),
        row_count=1,
        first_date=date(2025, 1, 1),
        last_date=date(2025, 1, 31),
        materialization_hint="refresh",
        reason="ready",
    )

    coverage = materialized_dataset_coverage(
        row,
        start=date(2026, 1, 1),
        end=date(2026, 1, 2),
    )

    assert coverage["relation"] == "no_overlap"
    assert coverage["collection_model"] == "event_export"
    assert "not proof of zero activity" in coverage["interpretation"]


def test_product_with_manifest_requires_valid_manifest(tmp_path) -> None:
    from lynchpin.materialization import _product_with_manifest_exists

    product = tmp_path / "product.ndjson"
    manifest = tmp_path / "product.manifest.json"
    product.write_text("{}\n", encoding="utf-8")
    manifest.write_text("{", encoding="utf-8")

    assert not _product_with_manifest_exists(product, manifest)

    manifest.write_text('{"row_count": 1}', encoding="utf-8")

    assert _product_with_manifest_exists(product, manifest)


def test_dataset_status_mapping_is_shared() -> None:
    from lynchpin.core.source_contracts import dataset_status_to_substrate_status

    assert dataset_status_to_substrate_status("ready") == "ok"
    assert dataset_status_to_substrate_status("empty") == "empty"
    assert dataset_status_to_substrate_status("missing") == "unavailable"
    assert dataset_status_to_substrate_status("partial") == "unavailable"
    # "stale" was removed from DatasetStatus — unknown values now route to error
    assert dataset_status_to_substrate_status("stale") == "error"
    assert dataset_status_to_substrate_status("degraded") == "error"
    assert dataset_status_to_substrate_status("error") == "error"


def test_ensure_materialized_runs_local_materializer_without_queue(monkeypatch) -> None:
    from lynchpin import materialization

    calls = {"audit": 0, "materialize": 0}

    def builder(_cfg):
        calls["audit"] += 1
        status = "ready" if calls["audit"] > 1 else "missing"
        return MaterializedDataset(
            name="webhistory",
            status=status,
            authority="raw fixture",
            query_surface="fixture",
            materialized_paths=(),
            raw_roots=(),
            row_count=1 if status == "ready" else None,
            first_date=date(2026, 1, 1) if status == "ready" else None,
            last_date=date(2026, 1, 1) if status == "ready" else None,
            materialization_hint="refresh",
            reason="ready" if status == "ready" else "missing",
        )

    def materializer():
        calls["materialize"] += 1
        return {"row_count": 1}

    monkeypatch.setattr(materialization, "_dataset_builders", lambda: {"webhistory": builder})
    monkeypatch.setattr(materialization, "_materializers", lambda: {"webhistory": materializer})

    result = ensure_materialized("webhistory", cfg=SimpleNamespace())

    assert result.status == "updated"
    assert result.changed is True
    assert calls == {"audit": 2, "materialize": 1}


def test_ensure_materialized_ready_product_is_noop(monkeypatch) -> None:
    from lynchpin import materialization

    def builder(_cfg):
        return MaterializedDataset(
            name="webhistory",
            status="ready",
            authority="raw fixture",
            query_surface="fixture",
            materialized_paths=(),
            raw_roots=(),
            row_count=1,
            first_date=date(2026, 1, 1),
            last_date=date(2026, 1, 1),
            materialization_hint="refresh",
            reason="already ready",
        )

    monkeypatch.setattr(materialization, "_dataset_builders", lambda: {"webhistory": builder})
    monkeypatch.setattr(
        materialization,
        "_materializers",
        lambda: {"webhistory": lambda: (_ for _ in ()).throw(AssertionError("should not run"))},
    )

    result = ensure_materialized("webhistory", cfg=SimpleNamespace())

    assert result.status == "ready"
    assert result.changed is False
    assert result.reason == "already ready"


def test_ensure_materialized_refreshes_expired_github_context(monkeypatch) -> None:
    from lynchpin import materialization

    calls = {"audit": 0, "materialize": 0}

    def builder(_cfg):
        calls["audit"] += 1
        ready = calls["audit"] > 1
        return MaterializedDataset(
            name="github_context",
            status="ready" if ready else "partial",
            authority="GitHub fixture",
            query_surface="fixture",
            materialized_paths=(),
            raw_roots=(),
            row_count=1,
            first_date=date(2026, 6, 1),
            last_date=date(2026, 6, 2),
            materialization_hint="refresh",
            reason="fresh" if ready else "older than the 48h network refresh contract",
            covered_dates=(date(2026, 6, 1), date(2026, 6, 2)),
        )

    def materializer():
        calls["materialize"] += 1
        return {"row_count": 1}

    monkeypatch.setattr(materialization, "_dataset_builders", lambda: {"github_context": builder})
    monkeypatch.setattr(materialization, "_materializers", lambda: {"github_context": materializer})

    result = ensure_materialized("github_context", cfg=SimpleNamespace())

    assert result.status == "updated"
    assert result.changed is True
    assert calls == {"audit": 2, "materialize": 1}


def test_ensure_materialized_blocks_stale_github_context_after_network_failure(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization
    from lynchpin.sources.github_context import GITHUB_CONTEXT_SCHEMA_VERSION

    product = tmp_path / "github" / "context.ndjson"
    product.parent.mkdir()
    manifest = product.with_suffix(".manifest.json")
    product.write_text('{"project": "lynchpin", "kind": "issue", "number": 1}\n', encoding="utf-8")
    manifest.write_text(json.dumps({"schema_version": GITHUB_CONTEXT_SCHEMA_VERSION, "row_count": 1}), encoding="utf-8")
    calls = {"materialize": 0}

    def builder(_cfg):
        return MaterializedDataset(
            name="github_context",
            status="partial",
            authority="GitHub fixture",
            query_surface="fixture",
            materialized_paths=(product, manifest),
            raw_roots=(),
            row_count=1,
            first_date=date(2026, 6, 1),
            last_date=date(2026, 6, 2),
            materialization_hint="refresh",
            reason="older than the 48h network refresh contract",
            covered_dates=(date(2026, 6, 1), date(2026, 6, 2)),
        )

    def materializer():
        calls["materialize"] += 1
        raise RuntimeError("gh api unavailable")

    monkeypatch.setattr(materialization, "_dataset_builders", lambda: {"github_context": builder})
    monkeypatch.setattr(materialization, "_materializers", lambda: {"github_context": materializer})

    result = ensure_materialized("github_context", cfg=SimpleNamespace(), window=(date(2026, 6, 1), date(2026, 6, 3)))

    assert result.status == "blocked"
    assert result.changed is False
    assert "existing canonical context product is stale" in result.reason
    assert result.diagnostics == ("RuntimeError", "stale_github_context")
    assert calls == {"materialize": 1}


def test_ensure_materialized_exposes_manifest_input_high_water(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization

    product = tmp_path / "product.ndjson"
    manifest = tmp_path / "product.manifest.json"
    product.write_text("{}\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "row_count": 1,
                "input_file_count": 2,
                "input_latest_mtime": "2026-01-02T03:04:05+00:00",
            }
        ),
        encoding="utf-8",
    )

    def builder(_cfg):
        return MaterializedDataset(
            name="webhistory",
            status="ready",
            authority="raw fixture",
            query_surface="fixture",
            materialized_paths=(product, manifest),
            raw_roots=(),
            row_count=1,
            first_date=date(2026, 1, 1),
            last_date=date(2026, 1, 1),
            materialization_hint="materialize",
            reason="already ready",
        )

    monkeypatch.setattr(materialization, "_dataset_builders", lambda: {"webhistory": builder})
    monkeypatch.setattr(
        materialization,
        "_materializers",
        lambda: {"webhistory": lambda: (_ for _ in ()).throw(AssertionError("should not run"))},
    )

    result = ensure_materialized("webhistory", cfg=SimpleNamespace())

    assert result.source_high_water["input_file_count"] == 2
    assert result.source_high_water["input_latest_mtime"] == "2026-01-02T03:04:05+00:00"


def test_spotify_audit_marks_manifest_with_changed_inputs_partial(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization
    from lynchpin.ingest.exports_materialize import SPOTIFY_STREAMS_SCHEMA_VERSION

    product = tmp_path / "streaming_history.ndjson"
    manifest = product.with_suffix(".manifest.json")
    source = tmp_path / "Spotify Extended Streaming History" / "Streaming_History_Audio_0.json"
    source.parent.mkdir(parents=True)
    source.write_text("[]", encoding="utf-8")
    product.write_text("{}\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "row_count": 1,
                "first_date": "2026-01-01",
                "last_date": "2026-01-01",
                "input_file_count": 1,
                "input_latest_mtime": "2000-01-01T00:00:00+00:00",
                "schema_version": SPOTIFY_STREAMS_SCHEMA_VERSION,
            }
        ),
        encoding="utf-8",
    )
    cfg = SimpleNamespace(exports_root=tmp_path, spotify_root=tmp_path)

    monkeypatch.setattr(materialization, "spotify_streams_path", lambda: product)
    monkeypatch.setattr(materialization, "_spotify_input_files", lambda _cfg: (source,))

    row = materialization._spotify_dataset(cfg)

    assert row.status == "partial"
    assert "older local export inputs" in row.reason


def test_spotify_audit_marks_old_schema_partial(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization

    product = tmp_path / "streaming_history.ndjson"
    manifest = product.with_suffix(".manifest.json")
    source = tmp_path / "Spotify Extended Streaming History" / "Streaming_History_Audio_0.json"
    source.parent.mkdir(parents=True)
    source.write_text("[]", encoding="utf-8")
    product.write_text("{}\n", encoding="utf-8")
    manifest.write_text(
        json.dumps({"row_count": 1, "first_date": "2026-01-01", "last_date": "2026-01-01"}),
        encoding="utf-8",
    )
    cfg = SimpleNamespace(exports_root=tmp_path, spotify_root=tmp_path)

    monkeypatch.setattr(materialization, "spotify_streams_path", lambda: product)
    monkeypatch.setattr(materialization, "_spotify_input_files", lambda _cfg: (source,))

    row = materialization._spotify_dataset(cfg)

    assert row.status == "partial"
    assert "schema is older" in row.reason


def test_reddit_audit_uses_manifest_bounds(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization
    from lynchpin.core.io import latest_mtime_iso
    from lynchpin.ingest.exports_materialize import REDDIT_CANONICAL_SCHEMA_VERSION

    canonical = tmp_path / "canonical"
    canonical.mkdir()
    product = canonical / "comments.csv"
    product.write_text("date\n2000-01-01\n", encoding="utf-8")
    source = tmp_path / "reddit/processed/2026-01-01/comments.csv"
    source.parent.mkdir(parents=True)
    source.write_text("date\n2026-01-01\n", encoding="utf-8")
    latest = latest_mtime_iso((source,))
    (canonical / "manifest.json").write_text(
        json.dumps(
            {
                "row_count": 9,
                "first_date": "2026-01-01",
                "last_date": "2026-01-03",
                "input_file_count": 1,
                "input_latest_mtime": latest,
                "schema_version": REDDIT_CANONICAL_SCHEMA_VERSION,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(materialization, "reddit_canonical_dir", lambda: canonical)

    row = materialization._reddit_dataset(SimpleNamespace(exports_root=tmp_path, reddit_export_dir=tmp_path))

    assert row.status == "ready"
    assert row.row_count == 9
    assert row.first_date == date(2026, 1, 1)
    assert row.last_date == date(2026, 1, 3)


def test_reddit_audit_preserves_zero_manifest_row_count(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization
    from lynchpin.core.io import latest_mtime_iso
    from lynchpin.ingest.exports_materialize import REDDIT_CANONICAL_SCHEMA_VERSION

    canonical = tmp_path / "canonical"
    canonical.mkdir()
    product = canonical / "comments.csv"
    product.write_text("date\n2026-01-01\n", encoding="utf-8")
    source = tmp_path / "reddit/processed/2026-01-01/comments.csv"
    source.parent.mkdir(parents=True)
    source.write_text("date\n2026-01-01\n", encoding="utf-8")
    (canonical / "manifest.json").write_text(
        json.dumps(
            {
                "row_count": 0,
                "first_date": "2026-01-01",
                "last_date": "2026-01-01",
                "input_file_count": 1,
                "input_latest_mtime": latest_mtime_iso((source,)),
                "schema_version": REDDIT_CANONICAL_SCHEMA_VERSION,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(materialization, "reddit_canonical_dir", lambda: canonical)

    row = materialization._reddit_dataset(SimpleNamespace(exports_root=tmp_path, reddit_export_dir=tmp_path))

    assert row.status == "ready"
    assert row.row_count == 0


def test_reddit_audit_marks_old_schema_partial(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization
    from lynchpin.core.io import latest_mtime_iso

    canonical = tmp_path / "canonical"
    canonical.mkdir()
    product = canonical / "comments.csv"
    product.write_text("date\n2026-01-01\n", encoding="utf-8")
    source = tmp_path / "reddit/processed/2026-01-01/comments.csv"
    source.parent.mkdir(parents=True)
    source.write_text("date\n2026-01-01\n", encoding="utf-8")
    (canonical / "manifest.json").write_text(
        json.dumps(
            {
                "row_count": 1,
                "first_date": "2026-01-01",
                "last_date": "2026-01-01",
                "input_file_count": 1,
                "input_latest_mtime": latest_mtime_iso((source,)),
                "schema_version": 0,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(materialization, "reddit_canonical_dir", lambda: canonical)

    row = materialization._reddit_dataset(SimpleNamespace(exports_root=tmp_path, reddit_export_dir=tmp_path))

    assert row.status == "partial"
    assert "schema is older" in row.reason


def test_messenger_audit_marks_old_schema_partial(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization

    canonical = tmp_path / "canonical"
    canonical.mkdir()
    messages = canonical / "messages.ndjson"
    threads = canonical / "threads.ndjson"
    manifest = canonical / "manifest.json"
    messages.write_text("{}\n", encoding="utf-8")
    threads.write_text("{}\n", encoding="utf-8")
    source = tmp_path / "messenger/export/messages/inbox.json"
    source.parent.mkdir(parents=True)
    source.write_text("{}", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "row_count": 1,
                "first_date": "2026-01-01",
                "last_date": "2026-01-01",
                "input_file_count": 1,
                "input_latest_mtime": datetime.fromtimestamp(source.stat().st_mtime, timezone.utc).astimezone().isoformat(),
                "schema_version": 0,
            }
        ),
        encoding="utf-8",
    )
    cfg = SimpleNamespace(
        exports_root=tmp_path,
        fbmessenger_gdpr_root=tmp_path / "messenger/export",
        fbmessenger_db=tmp_path / "messenger.sqlite",
    )

    monkeypatch.setattr(materialization, "messenger_canonical_dir", lambda: canonical)
    monkeypatch.setattr(materialization, "_messenger_thread_files", lambda _root: [source])

    row = materialization._messenger_dataset(cfg)

    assert row.status == "partial"
    assert "schema is older" in row.reason


def test_raindrop_audit_marks_old_schema_partial(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization

    product = tmp_path / "bookmarks.csv"
    manifest = tmp_path / "bookmarks.manifest.json"
    product.write_text("created\n2026-01-01\n", encoding="utf-8")
    source = tmp_path / "raindrop.csv"
    source.write_text("fixture", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "row_count": 1,
                "first_date": "2026-01-01",
                "last_date": "2026-01-01",
                "input_file_count": 1,
                "input_latest_mtime": datetime.fromtimestamp(source.stat().st_mtime, timezone.utc).astimezone().isoformat(),
                "schema_version": 0,
            }
        ),
        encoding="utf-8",
    )
    cfg = SimpleNamespace(raindrop_csv=source, raindrop_dir=tmp_path)

    monkeypatch.setattr(materialization, "raindrop_bookmarks_path", lambda: product)
    monkeypatch.setattr(materialization, "_raindrop_input_files", lambda: (source,))

    row = materialization._raindrop_dataset(cfg)

    assert row.status == "partial"
    assert "schema is older" in row.reason


def test_arbtt_audit_marks_manifest_with_changed_inputs_partial(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization
    from lynchpin.ingest.arbtt_materialize import ARBTT_EVENTS_SCHEMA_VERSION

    root = tmp_path / "arbtt"
    capture = root / "machine" / "capture.log"
    capture.parent.mkdir(parents=True)
    capture.write_text("fixture", encoding="utf-8")
    product = tmp_path / "events.ndjson"
    manifest = tmp_path / "events.manifest.json"
    product.write_text("{}\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "row_count": 1,
                "first_date": "2026-01-01",
                "last_date": "2026-01-01",
                "input_file_count": 1,
                "input_latest_mtime": "2000-01-01T00:00:00+00:00",
                "schema_version": ARBTT_EVENTS_SCHEMA_VERSION,
            }
        ),
        encoding="utf-8",
    )
    cfg = SimpleNamespace(arbtt_root=root)

    monkeypatch.setattr(materialization, "arbtt_events_path", lambda: product)
    monkeypatch.setattr(materialization, "arbtt_manifest_path", lambda: manifest)
    monkeypatch.setattr(materialization, "_capture_logs", lambda _root: [capture])

    row = materialization._arbtt_dataset(cfg)

    assert row.status == "partial"
    assert "older local input files" in row.reason


def test_arbtt_audit_marks_old_schema_partial(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization

    root = tmp_path / "arbtt"
    capture = root / "machine" / "capture.log"
    capture.parent.mkdir(parents=True)
    capture.write_text("fixture", encoding="utf-8")
    product = tmp_path / "events.ndjson"
    manifest = tmp_path / "events.manifest.json"
    product.write_text("{}\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "row_count": 1,
                "first_date": "2026-01-01",
                "last_date": "2026-01-01",
                "input_file_count": 1,
                "input_latest_mtime": datetime.fromtimestamp(capture.stat().st_mtime, timezone.utc).astimezone().isoformat(),
                "schema_version": 0,
            }
        ),
        encoding="utf-8",
    )
    cfg = SimpleNamespace(arbtt_root=root)

    monkeypatch.setattr(materialization, "arbtt_events_path", lambda: product)
    monkeypatch.setattr(materialization, "arbtt_manifest_path", lambda: manifest)
    monkeypatch.setattr(materialization, "_capture_logs", lambda _root: [capture])

    row = materialization._arbtt_dataset(cfg)

    assert row.status == "partial"
    assert "schema is older" in row.reason


def test_communications_audit_marks_manifest_with_changed_inputs_partial(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization
    from lynchpin.ingest.communications_materialize import COMMUNICATION_EVENTS_SCHEMA_VERSION

    exports = tmp_path / "exports"
    source = exports / "comms" / "outlook" / "raw" / "sent.CSV"
    source.parent.mkdir(parents=True)
    source.write_text("fixture", encoding="utf-8")
    product = tmp_path / "communication_events.ndjson"
    manifest = tmp_path / "communication_events.manifest.json"
    product.write_text("{}\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "row_count": 1,
                "first_date": "2026-01-01",
                "last_date": "2026-01-01",
                "input_file_count": 1,
                "input_latest_mtime": "2000-01-01T00:00:00+00:00",
                "schema_version": COMMUNICATION_EVENTS_SCHEMA_VERSION,
            }
        ),
        encoding="utf-8",
    )
    cfg = SimpleNamespace(exports_root=exports, teams_root=tmp_path / "teams")

    monkeypatch.setattr(materialization, "communication_events_path", lambda: product)
    monkeypatch.setattr(materialization, "communication_manifest_path", lambda: manifest)
    monkeypatch.setattr(materialization, "communication_input_files", lambda _cfg: (source,))

    row = materialization._communications_dataset(cfg)

    assert row.status == "partial"
    assert "older local input files" in row.reason


def test_communications_audit_marks_old_schema_partial(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization

    exports = tmp_path / "exports"
    source = exports / "comms" / "outlook" / "raw" / "sent.CSV"
    source.parent.mkdir(parents=True)
    source.write_text("fixture", encoding="utf-8")
    product = tmp_path / "communication_events.ndjson"
    manifest = tmp_path / "communication_events.manifest.json"
    product.write_text("{}\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "row_count": 1,
                "first_date": "2026-01-01",
                "last_date": "2026-01-01",
                "input_file_count": 1,
                "input_latest_mtime": datetime.fromtimestamp(source.stat().st_mtime, timezone.utc).astimezone().isoformat(),
                "schema_version": 0,
            }
        ),
        encoding="utf-8",
    )
    cfg = SimpleNamespace(exports_root=exports, teams_root=tmp_path / "teams")

    monkeypatch.setattr(materialization, "communication_events_path", lambda: product)
    monkeypatch.setattr(materialization, "communication_manifest_path", lambda: manifest)
    monkeypatch.setattr(materialization, "communication_input_files", lambda _cfg: (source,))

    row = materialization._communications_dataset(cfg)

    assert row.status == "partial"
    assert "schema is older" in row.reason


def test_bookmarks_audit_marks_old_schema_partial(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization

    root = tmp_path / "bookmarks"
    source = root / "profile" / "Bookmarks"
    source.parent.mkdir(parents=True)
    source.write_text("fixture", encoding="utf-8")
    product = tmp_path / "bookmarks.ndjson"
    manifest = tmp_path / "bookmarks.manifest.json"
    product.write_text("{}\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "row_count": 1,
                "first_date": "2026-01-01",
                "last_date": "2026-01-01",
                "input_file_count": 1,
                "input_latest_mtime": datetime.fromtimestamp(source.stat().st_mtime, timezone.utc).astimezone().isoformat(),
                "schema_version": 0,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(materialization, "bookmarks_path", lambda: product)
    monkeypatch.setattr(materialization, "bookmarks_manifest_path", lambda: manifest)
    monkeypatch.setattr(materialization, "_bookmark_roots", lambda _root: (root,))
    monkeypatch.setattr(materialization, "_discover_bookmark_files", lambda _roots: [source])

    row = materialization._bookmarks_dataset(SimpleNamespace(browser_bookmarks_root=root))

    assert row.status == "partial"
    assert "schema is older" in row.reason


def test_irc_audit_marks_manifest_with_changed_inputs_partial(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization
    from lynchpin.ingest.irc_materialize import IRC_EVENTS_SCHEMA_VERSION

    raw_root = tmp_path / "_raw"
    source = raw_root / "#chan" / "2026-01-01.log"
    source.parent.mkdir(parents=True)
    source.write_text("2026-01-01 10:00:00\talice\thello\n", encoding="utf-8")
    product = tmp_path / "events.ndjson"
    manifest = tmp_path / "events.manifest.json"
    product.write_text("{}\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "row_count": 1,
                "first_date": "2026-01-01",
                "last_date": "2026-01-01",
                "input_file_count": 1,
                "input_latest_mtime": "2000-01-01T00:00:00+00:00",
                "schema_version": IRC_EVENTS_SCHEMA_VERSION,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(materialization, "irc_events_path", lambda: product)
    monkeypatch.setattr(materialization, "irc_manifest_path", lambda: manifest)
    monkeypatch.setattr(materialization, "irc_raw_root", lambda: raw_root)
    monkeypatch.setattr(materialization, "irc_input_files", lambda _root: (source,))

    row = materialization._irc_dataset(SimpleNamespace())

    assert row.status == "partial"
    assert "older local input files" in row.reason


def test_irc_audit_marks_old_schema_partial(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization

    raw_root = tmp_path / "_raw"
    source = raw_root / "#chan" / "2026-01-01.log"
    source.parent.mkdir(parents=True)
    source.write_text("2026-01-01 10:00:00\talice\thello\n", encoding="utf-8")
    product = tmp_path / "events.ndjson"
    manifest = tmp_path / "events.manifest.json"
    product.write_text("{}\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "row_count": 1,
                "first_date": "2026-01-01",
                "last_date": "2026-01-01",
                "input_file_count": 1,
                "input_latest_mtime": datetime.fromtimestamp(source.stat().st_mtime, timezone.utc).astimezone().isoformat(),
                "schema_version": 0,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(materialization, "irc_events_path", lambda: product)
    monkeypatch.setattr(materialization, "irc_manifest_path", lambda: manifest)
    monkeypatch.setattr(materialization, "irc_raw_root", lambda: raw_root)
    monkeypatch.setattr(materialization, "irc_input_files", lambda _root: (source,))

    row = materialization._irc_dataset(SimpleNamespace())

    assert row.status == "partial"
    assert "schema is older" in row.reason


def test_irc_audit_reads_precise_covered_dates(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization
    from lynchpin.ingest.irc_materialize import IRC_EVENTS_SCHEMA_VERSION

    raw_root = tmp_path / "irc"
    source = raw_root / "#chan" / "2026-06-05.log"
    source.parent.mkdir(parents=True)
    source.write_text("fixture", encoding="utf-8")
    product = tmp_path / "irc-events.ndjson"
    manifest = tmp_path / "irc-events.manifest.json"
    product.write_text("{}\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "row_count": 2,
                "first_date": "2026-06-05",
                "last_date": "2026-06-07",
                "covered_dates": ["2026-06-05", "2026-06-07"],
                "input_file_count": 1,
                "input_latest_mtime": datetime.fromtimestamp(source.stat().st_mtime, timezone.utc).astimezone().isoformat(),
                "schema_version": IRC_EVENTS_SCHEMA_VERSION,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(materialization, "irc_events_path", lambda: product)
    monkeypatch.setattr(materialization, "irc_manifest_path", lambda: manifest)
    monkeypatch.setattr(materialization, "irc_raw_root", lambda: raw_root)
    monkeypatch.setattr(materialization, "irc_input_files", lambda _root: (source,))

    row = materialization._irc_dataset(SimpleNamespace())

    assert row.status == "ready"
    assert row.covered_dates == (date(2026, 6, 5), date(2026, 6, 7))


def test_atuin_audit_marks_manifest_with_changed_input_db_partial(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization
    from lynchpin.ingest.terminal_materialize import ATUIN_HISTORY_SCHEMA_VERSION

    db = tmp_path / "history.db"
    db.write_text("fixture", encoding="utf-8")
    product = tmp_path / "history.ndjson"
    manifest = tmp_path / "history.manifest.json"
    product.write_text("{}\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "row_count": 1,
                "first_date": "2026-01-01",
                "last_date": "2026-01-01",
                "input_file_count": 1,
                "input_latest_mtime": "2000-01-01T00:00:00+00:00",
                "schema_version": ATUIN_HISTORY_SCHEMA_VERSION,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(materialization, "canonical_atuin_history_path", lambda: product)
    monkeypatch.setattr(materialization, "atuin_input_files", lambda _cfg: (db,))

    row = materialization._atuin_dataset(SimpleNamespace(atuin_db=db))

    assert row.status == "partial"
    assert "older local database" in row.reason


def test_atuin_audit_marks_old_schema_partial(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization

    db = tmp_path / "history.db"
    db.write_text("fixture", encoding="utf-8")
    product = tmp_path / "history.ndjson"
    manifest = tmp_path / "history.manifest.json"
    product.write_text("{}\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "row_count": 1,
                "first_date": "2026-01-01",
                "last_date": "2026-01-01",
                "input_file_count": 1,
                "input_latest_mtime": datetime.fromtimestamp(db.stat().st_mtime, timezone.utc).astimezone().isoformat(),
                "schema_version": 0,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(materialization, "canonical_atuin_history_path", lambda: product)
    monkeypatch.setattr(materialization, "atuin_input_files", lambda _cfg: (db,))

    row = materialization._atuin_dataset(SimpleNamespace(atuin_db=db))

    assert row.status == "partial"
    assert "schema is older" in row.reason


def test_atuin_audit_reads_precise_covered_dates(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization
    from lynchpin.ingest.terminal_materialize import ATUIN_HISTORY_SCHEMA_VERSION

    db = tmp_path / "history.db"
    db.write_text("fixture", encoding="utf-8")
    product = tmp_path / "history.ndjson"
    manifest = tmp_path / "history.manifest.json"
    product.write_text("{}\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "row_count": 2,
                "first_date": "2026-06-05",
                "last_date": "2026-06-07",
                "covered_dates": ["2026-06-05", "2026-06-07"],
                "input_file_count": 1,
                "input_latest_mtime": datetime.fromtimestamp(db.stat().st_mtime, timezone.utc).astimezone().isoformat(),
                "schema_version": ATUIN_HISTORY_SCHEMA_VERSION,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(materialization, "canonical_atuin_history_path", lambda: product)
    monkeypatch.setattr(materialization, "atuin_input_files", lambda _cfg: (db,))

    row = materialization._atuin_dataset(SimpleNamespace(atuin_db=db))

    assert row.status == "ready"
    assert row.covered_dates == (date(2026, 6, 5), date(2026, 6, 7))


def test_activitywatch_audit_marks_manifest_with_changed_input_db_partial(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization
    from lynchpin.ingest.activitywatch_materialize import ACTIVITYWATCH_EVENTS_SCHEMA_VERSION

    db = tmp_path / "aw.db"
    db.write_text("fixture", encoding="utf-8")
    product = tmp_path / "events.ndjson"
    manifest = tmp_path / "events.manifest.json"
    product.write_text("{}\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "row_count": 1,
                "first_date": "2026-01-01",
                "last_date": "2026-01-01",
                "input_file_count": 1,
                "input_latest_mtime": "2000-01-01T00:00:00+00:00",
                "schema_version": ACTIVITYWATCH_EVENTS_SCHEMA_VERSION,
            }
        ),
        encoding="utf-8",
    )
    cfg = SimpleNamespace(
        activitywatch_db=db,
        activitywatch_archive_db_dir=tmp_path / "archive",
        exports_root=tmp_path / "exports",
    )

    monkeypatch.setattr(materialization, "canonical_activitywatch_events_path", lambda: product)
    monkeypatch.setattr(materialization, "activitywatch_input_files", lambda _cfg: (db,))

    row = materialization._activitywatch_dataset(cfg)

    assert row.status == "partial"
    assert "older local input databases" in row.reason


def test_activitywatch_audit_marks_old_schema_partial(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization

    db = tmp_path / "aw.db"
    db.write_text("fixture", encoding="utf-8")
    product = tmp_path / "events.ndjson"
    manifest = tmp_path / "events.manifest.json"
    product.write_text("{}\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "row_count": 1,
                "first_date": "2026-01-01",
                "last_date": "2026-01-01",
                "input_file_count": 1,
                "input_latest_mtime": datetime.fromtimestamp(db.stat().st_mtime, timezone.utc).astimezone().isoformat(),
                "schema_version": 0,
            }
        ),
        encoding="utf-8",
    )
    cfg = SimpleNamespace(
        activitywatch_db=db,
        activitywatch_archive_db_dir=tmp_path / "archive",
        exports_root=tmp_path / "exports",
    )

    monkeypatch.setattr(materialization, "canonical_activitywatch_events_path", lambda: product)
    monkeypatch.setattr(materialization, "activitywatch_input_files", lambda _cfg: (db,))

    row = materialization._activitywatch_dataset(cfg)

    assert row.status == "partial"
    assert "schema is older" in row.reason


def test_activitywatch_audit_reads_precise_covered_dates(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization
    from lynchpin.ingest.activitywatch_materialize import ACTIVITYWATCH_EVENTS_SCHEMA_VERSION

    db = tmp_path / "aw.db"
    db.write_text("fixture", encoding="utf-8")
    product = tmp_path / "events.ndjson"
    manifest = tmp_path / "events.manifest.json"
    product.write_text("{}\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "row_count": 1,
                "first_date": "2026-06-05",
                "last_date": "2026-06-07",
                "covered_dates": ["2026-06-05", "2026-06-07"],
                "input_file_count": 1,
                "input_latest_mtime": datetime.fromtimestamp(db.stat().st_mtime, timezone.utc).astimezone().isoformat(),
                "schema_version": ACTIVITYWATCH_EVENTS_SCHEMA_VERSION,
            }
        ),
        encoding="utf-8",
    )
    cfg = SimpleNamespace(
        activitywatch_db=db,
        activitywatch_archive_db_dir=tmp_path / "archive",
        exports_root=tmp_path / "exports",
    )

    monkeypatch.setattr(materialization, "canonical_activitywatch_events_path", lambda: product)
    monkeypatch.setattr(materialization, "activitywatch_input_files", lambda _cfg: (db,))

    row = materialization._activitywatch_dataset(cfg)

    assert row.status == "ready"
    assert row.covered_dates == (date(2026, 6, 5), date(2026, 6, 7))


def test_activitywatch_derived_audit_reads_precise_covered_dates(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization
    from lynchpin.ingest.activitywatch_derived_materialize import ACTIVITYWATCH_DERIVED_SCHEMA_VERSION
    from lynchpin.sources.activitywatch_derived import PRODUCT_KINDS

    root = tmp_path / "derived/activitywatch/graph"
    root.mkdir(parents=True)
    paths = {kind: root / f"{kind}.ndjson" for kind in PRODUCT_KINDS}
    for path in paths.values():
        path.write_text("", encoding="utf-8")
    manifest = root / "manifest.json"
    canonical = tmp_path / "captures/activitywatch/events.ndjson"
    canonical.parent.mkdir(parents=True)
    canonical.write_text("{}\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "row_count": 2,
                "first_date": "2026-06-05",
                "last_date": "2026-06-07",
                "covered_dates": ["2026-06-05", "2026-06-07"],
                "input_file_count": 1,
                "input_latest_mtime": datetime.fromtimestamp(
                    canonical.stat().st_mtime, timezone.utc
                ).astimezone().isoformat(),
                "schema_version": ACTIVITYWATCH_DERIVED_SCHEMA_VERSION,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(materialization, "activitywatch_derived_manifest_path", lambda: manifest)
    monkeypatch.setattr(materialization, "activitywatch_derived_path", lambda kind: paths[kind])
    monkeypatch.setattr(materialization, "activitywatch_derived_input_files", lambda: (canonical,))

    row = materialization._activitywatch_derived_dataset(
        SimpleNamespace(captures_root=tmp_path / "captures")
    )

    assert row.status == "ready"
    assert row.covered_dates == (date(2026, 6, 5), date(2026, 6, 7))


def test_activitywatch_event_index_audit_reads_precise_covered_dates(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization
    from lynchpin.sources.activitywatch_event_index import ACTIVITYWATCH_EVENT_INDEX_SCHEMA_VERSION

    root = tmp_path / "captures/activitywatch/events_by_day"
    root.mkdir(parents=True)
    day_path = root / "2026-06-05.ndjson"
    day_path.write_text("{}\n", encoding="utf-8")
    canonical = tmp_path / "captures/activitywatch/events.ndjson"
    canonical.write_text("{}\n", encoding="utf-8")
    canonical_manifest = canonical.with_suffix(".manifest.json")
    canonical_manifest.write_text('{"row_count": 1}\n', encoding="utf-8")
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "row_count": 1,
                "first_date": "2026-06-05",
                "last_date": "2026-06-05",
                "covered_dates": ["2026-06-05"],
                "input_file_count": 2,
                "input_latest_mtime": datetime.fromtimestamp(
                    canonical_manifest.stat().st_mtime, timezone.utc
                ).astimezone().isoformat(),
                "schema_version": ACTIVITYWATCH_EVENT_INDEX_SCHEMA_VERSION,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(materialization, "activitywatch_event_index_manifest_path", lambda: manifest)
    monkeypatch.setattr(materialization, "activitywatch_event_index_path", lambda day: root / f"{day.isoformat()}.ndjson")
    monkeypatch.setattr(materialization, "activitywatch_event_index_input_files", lambda: (canonical, canonical_manifest))

    row = materialization._activitywatch_event_index_dataset(
        SimpleNamespace(captures_root=tmp_path / "captures")
    )

    assert row.status == "ready"
    assert row.covered_dates == (date(2026, 6, 5),)


def test_activitywatch_derived_audit_marks_old_schema_partial(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization
    from lynchpin.sources.activitywatch_derived import PRODUCT_KINDS

    root = tmp_path / "derived/activitywatch/graph"
    root.mkdir(parents=True)
    paths = {kind: root / f"{kind}.ndjson" for kind in PRODUCT_KINDS}
    for path in paths.values():
        path.write_text("", encoding="utf-8")
    manifest = root / "manifest.json"
    canonical = tmp_path / "captures/activitywatch/events.ndjson"
    canonical.parent.mkdir(parents=True)
    canonical.write_text("{}\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "row_count": 2,
                "first_date": "2026-06-05",
                "last_date": "2026-06-07",
                "covered_dates": ["2026-06-05", "2026-06-07"],
                "input_file_count": 1,
                "input_latest_mtime": datetime.fromtimestamp(
                    canonical.stat().st_mtime, timezone.utc
                ).astimezone().isoformat(),
                "schema_version": 0,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(materialization, "activitywatch_derived_manifest_path", lambda: manifest)
    monkeypatch.setattr(materialization, "activitywatch_derived_path", lambda kind: paths[kind])
    monkeypatch.setattr(materialization, "activitywatch_derived_input_files", lambda: (canonical,))

    row = materialization._activitywatch_derived_dataset(
        SimpleNamespace(captures_root=tmp_path / "captures")
    )

    assert row.status == "partial"
    assert "schema is older" in row.reason


def test_health_audit_uses_jsonl_manifest_counts(tmp_path) -> None:
    from lynchpin import materialization

    processed = tmp_path / "health/processed"
    processed.mkdir(parents=True)
    product = processed / "health_fixture.jsonl"
    product.write_text("{}\n{}\n", encoding="utf-8")
    product.with_suffix(".manifest.json").write_text(
        json.dumps(
            {
                "row_count": 9,
                "first_date": "2026-01-01",
                "last_date": "2026-01-03",
            }
        ),
        encoding="utf-8",
    )

    row = materialization._health_dataset(SimpleNamespace(exports_root=tmp_path))

    assert row.status == "ready"
    assert row.row_count == 9
    assert row.first_date == date(2026, 1, 1)
    assert row.last_date == date(2026, 1, 3)


def test_count_files_counts_nested_suffixes_case_insensitively(tmp_path) -> None:
    from lynchpin import materialization

    nested = tmp_path / "a/b"
    nested.mkdir(parents=True)
    (tmp_path / "root.CAST").write_text("fixture", encoding="utf-8")
    (nested / "session.cast").write_text("fixture", encoding="utf-8")
    (nested / "ignore.txt").write_text("fixture", encoding="utf-8")

    assert materialization._count_files(tmp_path) == 3
    assert materialization._count_files(tmp_path, suffixes=(".cast",)) == 2


def test_activity_content_audit_marks_manifest_with_changed_upstream_products_partial(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization
    from lynchpin.ingest.activity_content_materialize import ACTIVITY_CONTENT_SCHEMA_VERSION

    aw = tmp_path / "events.ndjson"
    titles = tmp_path / "title_metadata.ndjson"
    aw.write_text("{}\n", encoding="utf-8")
    titles.write_text("{}\n", encoding="utf-8")
    product = tmp_path / "activity_content_daily.ndjson"
    usage = tmp_path / "title_usage.ndjson"
    manifest = tmp_path / "activity_content_daily.manifest.json"
    product.write_text("{}\n", encoding="utf-8")
    usage.write_text("{}\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "row_count": 1,
                "first_date": "2026-01-01",
                "last_date": "2026-01-01",
                "input_file_count": 2,
                "input_latest_mtime": "2000-01-01T00:00:00+00:00",
                "schema_version": ACTIVITY_CONTENT_SCHEMA_VERSION,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(materialization, "activity_content_daily_path", lambda: product)
    monkeypatch.setattr(materialization, "activity_title_usage_path", lambda: usage)
    monkeypatch.setattr(materialization, "activity_content_manifest_path", lambda: manifest)
    monkeypatch.setattr(materialization, "activity_content_input_files", lambda: (aw, titles))

    row = materialization._activity_content_dataset(SimpleNamespace(derived_root=tmp_path, captures_root=tmp_path))

    assert row.status == "partial"
    assert "older upstream products" in row.reason


def test_activity_content_audit_marks_old_schema_partial(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization

    aw = tmp_path / "events.ndjson"
    titles = tmp_path / "title_metadata.ndjson"
    aw.write_text("{}\n", encoding="utf-8")
    titles.write_text("{}\n", encoding="utf-8")
    product = tmp_path / "activity_content_daily.ndjson"
    usage = tmp_path / "title_usage.ndjson"
    manifest = tmp_path / "activity_content_daily.manifest.json"
    product.write_text("{}\n", encoding="utf-8")
    usage.write_text("{}\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "row_count": 1,
                "first_date": "2026-01-01",
                "last_date": "2026-01-01",
                "input_file_count": 2,
                "schema_version": 0,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(materialization, "activity_content_daily_path", lambda: product)
    monkeypatch.setattr(materialization, "activity_title_usage_path", lambda: usage)
    monkeypatch.setattr(materialization, "activity_content_manifest_path", lambda: manifest)
    monkeypatch.setattr(materialization, "activity_content_input_files", lambda: (aw, titles))

    row = materialization._activity_content_dataset(SimpleNamespace(derived_root=tmp_path, captures_root=tmp_path))

    assert row.status == "partial"
    assert "schema is older" in row.reason


def test_title_metadata_audit_marks_manifest_with_changed_source_db_partial(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization
    from lynchpin.ingest.title_metadata_materialize import TITLE_METADATA_SCHEMA_VERSION

    source_db = tmp_path / "semantic_classifications.duckdb"
    source_db.write_text("fixture", encoding="utf-8")
    product = tmp_path / "title_metadata.ndjson"
    manifest = tmp_path / "title_metadata.manifest.json"
    product.write_text("{}\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "row_count": 1,
                "source_db": str(source_db),
                "source_db_mtime": "2000-01-01T00:00:00+00:00",
                "schema_version": TITLE_METADATA_SCHEMA_VERSION,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(materialization, "title_metadata_path", lambda: product)
    monkeypatch.setattr(materialization, "title_metadata_manifest_path", lambda: manifest)

    row = materialization._title_metadata_dataset(SimpleNamespace(local_root=tmp_path))

    assert row.status == "partial"
    assert "older source database" in row.reason


def test_title_metadata_audit_marks_old_schema_partial(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization

    source_db = tmp_path / "semantic_classifications.duckdb"
    source_db.write_text("fixture", encoding="utf-8")
    product = tmp_path / "title_metadata.ndjson"
    manifest = tmp_path / "title_metadata.manifest.json"
    product.write_text("{}\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "row_count": 1,
                "source_db": str(source_db),
                "source_db_mtime": datetime.fromtimestamp(source_db.stat().st_mtime, timezone.utc).isoformat(),
                "schema_version": 0,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(materialization, "title_metadata_path", lambda: product)
    monkeypatch.setattr(materialization, "title_metadata_manifest_path", lambda: manifest)

    row = materialization._title_metadata_dataset(SimpleNamespace(local_root=tmp_path))

    assert row.status == "partial"
    assert "schema is older" in row.reason


def test_spotify_daily_audit_marks_manifest_with_changed_stream_product_partial(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization
    from lynchpin.ingest.personal_signals_materialize import SPOTIFY_DAILY_SCHEMA_VERSION

    streams = tmp_path / "streaming_history.ndjson"
    streams.write_text("{}\n", encoding="utf-8")
    product = tmp_path / "spotify_daily.ndjson"
    manifest = tmp_path / "spotify_daily.manifest.json"
    product.write_text("{}\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "row_count": 1,
                "first_date": "2026-01-01",
                "last_date": "2026-01-01",
                "input_file_count": 1,
                "input_latest_mtime": "2000-01-01T00:00:00+00:00",
                "schema_version": SPOTIFY_DAILY_SCHEMA_VERSION,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(materialization, "spotify_daily_path", lambda: product)
    monkeypatch.setattr(materialization, "spotify_daily_manifest_path", lambda: manifest)
    monkeypatch.setattr(materialization, "spotify_daily_input_files", lambda: (streams,))

    row = materialization._spotify_daily_dataset(SimpleNamespace(exports_root=tmp_path))

    assert row.status == "partial"
    assert "older stream product" in row.reason


def test_spotify_daily_audit_marks_old_schema_partial(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization

    product = tmp_path / "spotify_daily.ndjson"
    manifest = tmp_path / "spotify_daily.manifest.json"
    product.write_text("{}\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "row_count": 1,
                "first_date": "2026-01-01",
                "last_date": "2026-01-01",
                "input_file_count": 0,
                "schema_version": 0,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(materialization, "spotify_daily_path", lambda: product)
    monkeypatch.setattr(materialization, "spotify_daily_manifest_path", lambda: manifest)
    monkeypatch.setattr(materialization, "spotify_daily_input_files", lambda: ())

    row = materialization._spotify_daily_dataset(SimpleNamespace(exports_root=tmp_path))

    assert row.status == "partial"
    assert "schema is older" in row.reason


def test_spotify_daily_audit_reads_precise_covered_dates(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization
    from lynchpin.ingest.personal_signals_materialize import SPOTIFY_DAILY_SCHEMA_VERSION

    product = tmp_path / "spotify_daily.ndjson"
    manifest = tmp_path / "spotify_daily.manifest.json"
    product.write_text("{}\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "row_count": 2,
                "first_date": "2026-05-01",
                "last_date": "2026-05-03",
                "covered_dates": ["2026-05-01", "2026-05-03"],
                "input_file_count": 0,
                "schema_version": SPOTIFY_DAILY_SCHEMA_VERSION,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(materialization, "spotify_daily_path", lambda: product)
    monkeypatch.setattr(materialization, "spotify_daily_manifest_path", lambda: manifest)
    monkeypatch.setattr(materialization, "spotify_daily_input_files", lambda: ())

    row = materialization._spotify_daily_dataset(SimpleNamespace(exports_root=tmp_path))

    assert row.status == "ready"
    assert row.covered_dates == (date(2026, 5, 1), date(2026, 5, 3))


def test_personal_daily_signals_audit_marks_manifest_with_changed_upstream_product_partial(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization
    from lynchpin.ingest.personal_signals_materialize import PERSONAL_DAILY_SIGNALS_SCHEMA_VERSION

    upstream = tmp_path / "activity_content_daily.ndjson"
    upstream.write_text("{}\n", encoding="utf-8")
    product = tmp_path / "personal_daily_signals.ndjson"
    manifest = tmp_path / "personal_daily_signals.manifest.json"
    product.write_text("{}\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "row_count": 1,
                "first_date": "2026-01-01",
                "last_date": "2026-01-01",
                "input_files": [str(upstream)],
                "input_file_count": 1,
                "input_latest_mtime": "2000-01-01T00:00:00+00:00",
                "schema_version": PERSONAL_DAILY_SIGNALS_SCHEMA_VERSION,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(materialization, "personal_daily_signals_path", lambda: product)
    monkeypatch.setattr(materialization, "personal_daily_signals_manifest_path", lambda: manifest)

    row = materialization._personal_daily_signals_dataset(SimpleNamespace(derived_root=tmp_path))

    assert row.status == "partial"
    assert "older upstream products" in row.reason


def test_personal_daily_signals_audit_marks_old_schema_partial(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization

    product = tmp_path / "personal_daily_signals.ndjson"
    manifest = tmp_path / "personal_daily_signals.manifest.json"
    product.write_text("{}\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "row_count": 1,
                "first_date": "2026-01-01",
                "last_date": "2026-01-01",
                "input_file_count": 0,
                "schema_version": 1,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(materialization, "personal_daily_signals_path", lambda: product)
    monkeypatch.setattr(materialization, "personal_daily_signals_manifest_path", lambda: manifest)

    row = materialization._personal_daily_signals_dataset(SimpleNamespace(derived_root=tmp_path))

    assert row.status == "partial"
    assert "schema is older" in row.reason


def test_personal_daily_signals_audit_reads_precise_covered_dates(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization
    from lynchpin.ingest.personal_signals_materialize import PERSONAL_DAILY_SIGNALS_SCHEMA_VERSION

    product = tmp_path / "personal_daily_signals.ndjson"
    manifest = tmp_path / "personal_daily_signals.manifest.json"
    product.write_text("{}\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "row_count": 1,
                "first_date": "2026-05-01",
                "last_date": "2026-05-03",
                "covered_dates": ["2026-05-01", "2026-05-03"],
                "input_file_count": 0,
                "schema_version": PERSONAL_DAILY_SIGNALS_SCHEMA_VERSION,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(materialization, "personal_daily_signals_path", lambda: product)
    monkeypatch.setattr(materialization, "personal_daily_signals_manifest_path", lambda: manifest)

    row = materialization._personal_daily_signals_dataset(SimpleNamespace(derived_root=tmp_path))

    assert row.covered_dates == (date(2026, 5, 1), date(2026, 5, 3))


def test_temporal_signals_audit_reads_precise_covered_dates(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization
    from lynchpin.ingest.temporal_signals_materialize import TEMPORAL_SIGNALS_SCHEMA_VERSION

    product = tmp_path / "temporal_signals.ndjson"
    manifest = tmp_path / "temporal_signals.manifest.json"
    product.write_text("{}\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "row_count": 1,
                "first_date": "2026-05-01",
                "last_date": "2026-05-03",
                "covered_dates": ["2026-05-01", "2026-05-03"],
                "input_file_count": 0,
                "schema_version": TEMPORAL_SIGNALS_SCHEMA_VERSION,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(materialization, "temporal_signals_path", lambda: product)
    monkeypatch.setattr(materialization, "temporal_signals_manifest_path", lambda: manifest)

    row = materialization._temporal_signals_dataset(SimpleNamespace(derived_root=tmp_path))

    assert row.covered_dates == (date(2026, 5, 1), date(2026, 5, 3))


def test_temporal_signals_audit_marks_old_schema_partial(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization

    product = tmp_path / "temporal_signals.ndjson"
    manifest = tmp_path / "temporal_signals.manifest.json"
    product.write_text("{}\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "row_count": 1,
                "first_date": "2026-05-01",
                "last_date": "2026-05-03",
                "input_file_count": 0,
                "schema_version": 0,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(materialization, "temporal_signals_path", lambda: product)
    monkeypatch.setattr(materialization, "temporal_signals_manifest_path", lambda: manifest)

    row = materialization._temporal_signals_dataset(SimpleNamespace(derived_root=tmp_path))

    assert row.status == "partial"
    assert "schema is older" in row.reason


def test_sleep_productivity_audit_reads_precise_covered_dates(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization
    from lynchpin.ingest.sleep_productivity_materialize import SLEEP_PRODUCTIVITY_SCHEMA_VERSION

    product = tmp_path / "sleep_productivity.ndjson"
    manifest = tmp_path / "sleep_productivity.manifest.json"
    product.write_text("{}\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "row_count": 1,
                "first_date": "2026-05-01",
                "last_date": "2026-05-03",
                "covered_dates": ["2026-05-01", "2026-05-03"],
                "input_file_count": 0,
                "schema_version": SLEEP_PRODUCTIVITY_SCHEMA_VERSION,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(materialization, "sleep_productivity_path", lambda: product)
    monkeypatch.setattr(materialization, "sleep_productivity_manifest_path", lambda: manifest)

    row = materialization._sleep_productivity_dataset(SimpleNamespace(derived_root=tmp_path))

    assert row.covered_dates == (date(2026, 5, 1), date(2026, 5, 3))


def test_sleep_productivity_audit_marks_old_schema_partial(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization

    product = tmp_path / "sleep_productivity.ndjson"
    manifest = tmp_path / "sleep_productivity.manifest.json"
    product.write_text("{}\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "row_count": 1,
                "first_date": "2026-05-01",
                "last_date": "2026-05-03",
                "input_file_count": 0,
                "schema_version": 0,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(materialization, "sleep_productivity_path", lambda: product)
    monkeypatch.setattr(materialization, "sleep_productivity_manifest_path", lambda: manifest)

    row = materialization._sleep_productivity_dataset(SimpleNamespace(derived_root=tmp_path))

    assert row.status == "partial"
    assert "schema is older" in row.reason


def test_keylog_audit_uses_log_file_dates_for_coverage(tmp_path) -> None:
    from lynchpin import materialization

    logs = tmp_path / "keylog" / "logs"
    logs.mkdir(parents=True)
    (logs / "2026-05-01.jsonl").write_text("{}\n", encoding="utf-8")
    (logs / "2026-05-03.jsonl").write_text("{}\n", encoding="utf-8")

    row = materialization._keylog_dataset(SimpleNamespace(keylog_root=tmp_path / "keylog"))

    assert row.status == "ready"
    assert row.first_date == date(2026, 5, 1)
    assert row.last_date == date(2026, 5, 3)


def test_wykop_audit_uses_comment_dates_for_coverage(tmp_path) -> None:
    from lynchpin import materialization

    root = tmp_path / "wykop"
    operator_root = root / "Sinity"
    operator_root.mkdir(parents=True)
    (operator_root / "wykop_links_commented.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "comment_id": 1,
                        "comment_created_at": "2026-05-03 10:00:00",
                        "comment_content": "later",
                    }
                ),
                json.dumps(
                    {
                        "comment_id": 2,
                        "comment_created_at": "2026-05-01 10:00:00",
                        "comment_content": "earlier",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    row = materialization._wykop_dataset(SimpleNamespace(wykop_root=root))

    assert row.status == "ready"
    assert row.raw_roots == (operator_root,)
    assert row.first_date == date(2026, 5, 1)
    assert row.last_date == date(2026, 5, 3)


def test_themotte_audit_uses_synced_date_bounds(tmp_path) -> None:
    from lynchpin import materialization

    root = tmp_path / "themotte" / "Sinity"
    root.mkdir(parents=True)
    (root / "themotte_messages.jsonl").write_text(
        json.dumps(
            {
                "id": "1",
                "created_at": "2026-02-01T10:00:00Z",
                "author": "Sinity",
                "recipient": "self_made_human",
                "peer": "self_made_human",
                "body": "hello",
                "url": "https://www.themotte.org/comment/1",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    cfg = SimpleNamespace(themotte_root=tmp_path / "themotte", themotte_username="Sinity")

    row = materialization._themotte_dataset(cfg)

    assert row.status == "ready"
    assert row.first_date == date(2026, 2, 1)
    assert row.last_date == date(2026, 2, 1)


def test_machine_audit_marks_manifest_with_changed_input_db_partial(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization
    from lynchpin.ingest.machine_materialize import MACHINE_TELEMETRY_SCHEMA_VERSION

    db = tmp_path / "telemetry.sqlite"
    db.write_text("fixture", encoding="utf-8")
    paths = {
        name: tmp_path / f"{name}.ndjson"
        for name in MACHINE_TABLE_NAMES
    }
    for path in paths.values():
        path.write_text("{}\n", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "row_count": 4,
                "tables": {
                    name: {"row_count": 1, "first_date": "2026-01-01", "last_date": "2026-01-01"}
                    for name in paths
                },
                "input_file_count": 1,
                "input_latest_mtime": "2000-01-01T00:00:00+00:00",
                "schema_version": MACHINE_TELEMETRY_SCHEMA_VERSION,
            }
        ),
        encoding="utf-8",
    )

    def table_path(name: str):
        if name == "manifest":
            return tmp_path / "manifest.ndjson"
        return paths[name]

    monkeypatch.setattr(materialization, "canonical_machine_table_path", table_path)
    monkeypatch.setattr(materialization, "machine_input_files", lambda _cfg: (db,))

    row = materialization._machine_dataset(
        SimpleNamespace(machine_telemetry_db=db, machine_capture_root=tmp_path)
    )

    assert row.status == "partial"
    assert "older local database" in row.reason


def test_machine_audit_marks_old_schema_partial(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization

    db = tmp_path / "telemetry.sqlite"
    db.write_text("fixture", encoding="utf-8")
    paths = {
        name: tmp_path / f"{name}.ndjson"
        for name in MACHINE_TABLE_NAMES
    }
    for path in paths.values():
        path.write_text("{}\n", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "row_count": 4,
                "tables": {
                    name: {"row_count": 1, "first_date": "2026-01-01", "last_date": "2026-01-01"}
                    for name in paths
                },
                "input_file_count": 1,
                "input_latest_mtime": datetime.fromtimestamp(db.stat().st_mtime, timezone.utc).astimezone().isoformat(),
                "schema_version": 0,
            }
        ),
        encoding="utf-8",
    )

    def table_path(name: str):
        if name == "manifest":
            return tmp_path / "manifest.ndjson"
        return paths[name]

    monkeypatch.setattr(materialization, "canonical_machine_table_path", table_path)
    monkeypatch.setattr(materialization, "machine_input_files", lambda _cfg: (db,))

    row = materialization._machine_dataset(
        SimpleNamespace(machine_telemetry_db=db, machine_capture_root=tmp_path)
    )

    assert row.status == "partial"
    assert "schema is older" in row.reason


def test_machine_table_materialization_merges_requested_window(monkeypatch, tmp_path) -> None:
    from lynchpin.ingest import machine_materialize

    table = tmp_path / "metric_sample.ndjson"
    manifest = tmp_path / "manifest.json"
    table.write_text(
        "\n".join(
            [
                json.dumps({"observed_at": "2026-01-01T12:00:00+00:00", "value": "before"}),
                json.dumps({"observed_at": "2026-01-02T12:00:00+00:00", "value": "old"}),
                json.dumps({"observed_at": "2026-01-03T12:00:00+00:00", "value": "after"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    manifest.write_text(
        json.dumps(
            {
                "tables": {
                    "metric_sample": {
                        "covered_dates": ["2026-01-01", "2026-01-02", "2026-01-03"],
                    }
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )

    def table_path(name: str):
        if name == "manifest":
            return tmp_path / "manifest.ndjson"
        return table

    monkeypatch.setattr(machine_materialize, "canonical_machine_table_path", table_path)

    report = machine_materialize._materialize_table(
        "metric_sample",
        lambda: [
            _MachineFixtureSample(
                observed_at=datetime(2026, 1, 2, 13, tzinfo=timezone.utc),
                value="new",
            )
        ],
        start=date(2026, 1, 2),
        end=date(2026, 1, 3),
    )

    rows = [json.loads(line) for line in table.read_text(encoding="utf-8").splitlines()]
    assert [row["value"] for row in rows] == ["before", "new", "after"]
    assert report["covered_dates"] == ["2026-01-01", "2026-01-02", "2026-01-03"]
    assert report["first_date"] == "2026-01-01"
    assert report["last_date"] == "2026-01-03"


def test_machine_audit_reads_precise_covered_dates(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization
    from lynchpin.ingest.machine_materialize import MACHINE_TELEMETRY_SCHEMA_VERSION

    db = tmp_path / "telemetry.sqlite"
    db.write_text("fixture", encoding="utf-8")
    paths = {name: tmp_path / f"{name}.ndjson" for name in MACHINE_TABLE_NAMES}
    for path in paths.values():
        path.write_text("{}\n", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "row_count": 2,
                "first_date": "2026-01-01",
                "last_date": "2026-01-03",
                "covered_dates": ["2026-01-01", "2026-01-03"],
                "tables": {
                    name: {"row_count": 1, "first_date": "2026-01-01", "last_date": "2026-01-03"}
                    for name in paths
                },
                "input_file_count": 1,
                "input_latest_mtime": datetime.fromtimestamp(db.stat().st_mtime, timezone.utc).astimezone().isoformat(),
                "schema_version": MACHINE_TELEMETRY_SCHEMA_VERSION,
            }
        ),
        encoding="utf-8",
    )

    def table_path(name: str):
        if name == "manifest":
            return tmp_path / "manifest.ndjson"
        return paths[name]

    monkeypatch.setattr(materialization, "canonical_machine_table_path", table_path)
    monkeypatch.setattr(materialization, "machine_input_files", lambda _cfg: (db,))

    row = materialization._machine_dataset(
        SimpleNamespace(machine_telemetry_db=db, machine_capture_root=tmp_path)
    )

    assert row.status == "ready"
    assert row.covered_dates == (date(2026, 1, 1), date(2026, 1, 3))


def test_ensure_materialized_continuous_window_gap_runs_local_materializer(monkeypatch) -> None:
    from lynchpin import materialization

    calls = {"audit": 0, "materialize": 0}

    def builder(_cfg):
        calls["audit"] += 1
        return MaterializedDataset(
            name="webhistory",
            status="ready",
            authority="raw fixture",
            query_surface="fixture",
            materialized_paths=(),
            raw_roots=(),
            row_count=1,
            first_date=date(2026, 2, 1) if calls["audit"] > 1 else date(2026, 1, 1),
            last_date=date(2026, 2, 1) if calls["audit"] > 1 else date(2026, 1, 1),
            materialization_hint="materialize",
            reason="ready",
        )

    def materializer():
        calls["materialize"] += 1
        return {"row_count": 1}

    monkeypatch.setattr(materialization, "_dataset_builders", lambda: {"webhistory": builder})
    monkeypatch.setattr(materialization, "_materializers", lambda: {"webhistory": materializer})

    result = ensure_materialized(
        "webhistory",
        window=(date(2026, 2, 1), date(2026, 2, 2)),
        cfg=SimpleNamespace(),
    )

    assert result.status == "updated"
    assert result.changed is True
    assert calls == {"audit": 2, "materialize": 1}
    assert result.coverage["fully_covers_requested_window"] is True


def test_ensure_materialized_substrate_blocks_out_of_window_snapshot(monkeypatch) -> None:
    from lynchpin import materialization

    def builder(_cfg):
        return MaterializedDataset(
            name="evidence_graph_substrate",
            status="ready",
            authority="DuckDB fixture",
            query_surface="fixture",
            materialized_paths=(),
            raw_roots=(),
            row_count=1,
            first_date=date(2026, 5, 1),
            last_date=date(2026, 5, 31),
            covered_dates=tuple(date(2026, 5, day) for day in range(1, 32)),
            materialization_hint="build snapshot",
            reason="snapshot ready",
        )

    monkeypatch.setattr(materialization, "_dataset_builders", lambda: {"evidence_graph_substrate": builder})
    monkeypatch.setattr(materialization, "_materializers", lambda: {})

    result = ensure_materialized(
        "evidence_graph_substrate",
        window=(date(2026, 6, 1), date(2026, 6, 2)),
        cfg=SimpleNamespace(),
    )

    assert result.status == "blocked"
    assert result.changed is False
    assert result.coverage["fully_covers_requested_window"] is False
    assert "no local materializer" in result.reason


def test_ensure_materialized_derived_without_bounds_runs_materializer(monkeypatch) -> None:
    from lynchpin import materialization

    calls = {"audit": 0, "materialize": 0}

    def builder(_cfg):
        calls["audit"] += 1
        if calls["audit"] == 1:
            return MaterializedDataset(
                name="personal_daily_signals",
                status="ready",
                authority="fixture",
                query_surface="fixture",
                materialized_paths=(),
                raw_roots=(),
                row_count=1,
                first_date=None,
                last_date=None,
                materialization_hint="materialize",
                reason="old manifest has no bounds",
            )
        return MaterializedDataset(
            name="personal_daily_signals",
            status="ready",
            authority="fixture",
            query_surface="fixture",
            materialized_paths=(),
            raw_roots=(),
            row_count=1,
            first_date=date(2026, 6, 1),
            last_date=date(2026, 6, 1),
            covered_dates=(date(2026, 6, 1),),
            materialization_hint="materialize",
            reason="ready",
        )

    def materializer(*, start: date, end: date):
        calls["materialize"] += 1
        assert (start, end) == (date(2026, 6, 1), date(2026, 6, 2))
        return {"row_count": 1}

    monkeypatch.setattr(materialization, "_dataset_builders", lambda: {"personal_daily_signals": builder})
    monkeypatch.setattr(materialization, "_materializers", lambda: {"personal_daily_signals": materializer})

    result = ensure_materialized(
        "personal_daily_signals",
        window=(date(2026, 6, 1), date(2026, 6, 2)),
        cfg=SimpleNamespace(),
    )

    assert result.status == "updated"
    assert result.coverage["fully_covers_requested_window"] is True
    assert calls == {"audit": 2, "materialize": 1}


def test_ensure_materialized_substrate_without_bounds_blocks_windowed_read(monkeypatch) -> None:
    from lynchpin import materialization

    def builder(_cfg):
        return MaterializedDataset(
            name="evidence_graph_substrate",
            status="ready",
            authority="DuckDB fixture",
            query_surface="fixture",
            materialized_paths=(),
            raw_roots=(),
            row_count=1,
            first_date=None,
            last_date=None,
            materialization_hint="build snapshot",
            reason="snapshot ready but unbounded",
        )

    monkeypatch.setattr(materialization, "_dataset_builders", lambda: {"evidence_graph_substrate": builder})
    monkeypatch.setattr(materialization, "_materializers", lambda: {})

    result = ensure_materialized(
        "evidence_graph_substrate",
        window=(date(2026, 6, 1), date(2026, 6, 2)),
        cfg=SimpleNamespace(),
    )

    assert result.status == "blocked"
    assert result.coverage["relation"] == "undated"
    assert "no local materializer" in result.reason


def test_ensure_materialized_forwards_window_to_window_aware_materializer(monkeypatch) -> None:
    from lynchpin import materialization

    calls = {"audit": 0, "window": None}

    def builder(_cfg):
        calls["audit"] += 1
        return MaterializedDataset(
            name="webhistory",
            status="ready",
            authority="raw fixture",
            query_surface="fixture",
            materialized_paths=(),
            raw_roots=(),
            row_count=1,
            first_date=date(2026, 2, 1) if calls["audit"] > 1 else date(2026, 1, 1),
            last_date=date(2026, 2, 1) if calls["audit"] > 1 else date(2026, 1, 1),
            materialization_hint="materialize",
            reason="ready",
        )

    def materializer(*, start: date, end: date):
        calls["window"] = (start, end)
        return {"row_count": 1}

    monkeypatch.setattr(materialization, "_dataset_builders", lambda: {"webhistory": builder})
    monkeypatch.setattr(materialization, "_materializers", lambda: {"webhistory": materializer})

    result = ensure_materialized(
        "webhistory",
        window=(date(2026, 2, 1), date(2026, 2, 2)),
        cfg=SimpleNamespace(),
    )

    assert result.status == "updated"
    assert calls["window"] == (date(2026, 2, 1), date(2026, 2, 2))


def test_ensure_materialized_empty_continuous_window_is_ready_noop(monkeypatch) -> None:
    from lynchpin import materialization

    calls = {"audit": 0, "materialize": 0}

    def builder(_cfg):
        calls["audit"] += 1
        return MaterializedDataset(
            name="webhistory",
            status="ready",
            authority="raw fixture",
            query_surface="fixture",
            materialized_paths=(),
            raw_roots=(),
            row_count=1,
            first_date=date(2026, 1, 1),
            last_date=date(2026, 1, 1),
            materialization_hint="materialize",
            reason="ready",
        )

    def materializer():
        calls["materialize"] += 1
        return {"row_count": 1}

    monkeypatch.setattr(materialization, "_dataset_builders", lambda: {"webhistory": builder})
    monkeypatch.setattr(materialization, "_materializers", lambda: {"webhistory": materializer})

    result = ensure_materialized(
        "webhistory",
        window=(date(2026, 2, 1), date(2026, 2, 1)),
        cfg=SimpleNamespace(),
    )

    assert result.status == "ready"
    assert result.changed is False
    assert calls == {"audit": 1, "materialize": 0}
    assert result.coverage["requested_days"] == 0
    assert result.coverage["fully_covers_requested_window"] is True


def test_materialized_dataset_coverage_treats_end_as_exclusive() -> None:
    from lynchpin.materialization import materialized_dataset_coverage

    row = MaterializedDataset(
        name="webhistory",
        status="ready",
        authority="fixture",
        query_surface="fixture",
        materialized_paths=(),
        raw_roots=(),
        row_count=1,
        first_date=date(2026, 5, 3),
        last_date=date(2026, 5, 3),
        materialization_hint="fixture",
        reason="fixture",
    )

    coverage = materialized_dataset_coverage(
        row,
        start=date(2026, 5, 1),
        end=date(2026, 5, 3),
    )

    assert coverage["covered_days"] == 0
    assert coverage["overlaps_requested_window"] is False
    assert coverage["fully_covers_requested_window"] is False


def test_materialized_dataset_coverage_empty_window_is_vacuously_covered() -> None:
    from lynchpin.materialization import materialized_dataset_coverage

    row = MaterializedDataset(
        name="webhistory",
        status="ready",
        authority="fixture",
        query_surface="fixture",
        materialized_paths=(),
        raw_roots=(),
        row_count=1,
        first_date=date(2026, 5, 3),
        last_date=date(2026, 5, 3),
        materialization_hint="fixture",
        reason="fixture",
    )

    coverage = materialized_dataset_coverage(
        row,
        start=date(2026, 5, 1),
        end=date(2026, 5, 1),
    )

    assert coverage["requested_days"] == 0
    assert coverage["covered_days"] == 0
    assert coverage["overlaps_requested_window"] is False
    assert coverage["fully_covers_requested_window"] is True
    assert coverage["relation"] == "covers_window"


def test_materialized_dataset_coverage_caps_days_to_requested_window() -> None:
    from lynchpin.materialization import materialized_dataset_coverage

    row = MaterializedDataset(
        name="webhistory",
        status="ready",
        authority="fixture",
        query_surface="fixture",
        materialized_paths=(),
        raw_roots=(),
        row_count=10,
        first_date=date(2026, 5, 1),
        last_date=date(2026, 5, 10),
        materialization_hint="fixture",
        reason="fixture",
    )

    coverage = materialized_dataset_coverage(
        row,
        start=date(2026, 5, 1),
        end=date(2026, 5, 3),
    )

    assert coverage["requested_days"] == 2
    assert coverage["covered_days"] == 2
    assert coverage["coverage_ratio"] == 1.0


def test_materialized_dataset_coverage_uses_precise_covered_dates() -> None:
    from lynchpin.materialization import materialized_dataset_coverage

    row = MaterializedDataset(
        name="activitywatch_derived",
        status="ready",
        authority="fixture",
        query_surface="fixture",
        materialized_paths=(),
        raw_roots=(),
        row_count=2,
        first_date=date(2026, 6, 5),
        last_date=date(2026, 6, 7),
        covered_dates=(date(2026, 6, 5), date(2026, 6, 7)),
        materialization_hint="materialize",
        reason="ready",
    )

    coverage = materialized_dataset_coverage(
        row,
        start=date(2026, 6, 5),
        end=date(2026, 6, 8),
    )

    assert coverage["precise_covered_dates"] is True
    assert coverage["covered_days"] == 2
    assert coverage["coverage_ratio"] == 0.666667
    assert coverage["fully_covers_requested_window"] is False
    assert coverage["relation"] == "partial_overlap"


def test_ensure_materialized_derived_precise_gap_runs_materializer(monkeypatch) -> None:
    from lynchpin import materialization

    calls = {"audit": 0, "materialize": 0}

    def builder(_cfg):
        calls["audit"] += 1
        covered = (
            (date(2026, 6, 5), date(2026, 6, 7))
            if calls["audit"] == 1
            else (date(2026, 6, 5), date(2026, 6, 6), date(2026, 6, 7))
        )
        return MaterializedDataset(
            name="activitywatch_derived",
            status="ready",
            authority="fixture",
            query_surface="fixture",
            materialized_paths=(),
            raw_roots=(),
            row_count=len(covered),
            first_date=date(2026, 6, 5),
            last_date=date(2026, 6, 7),
            covered_dates=covered,
            materialization_hint="materialize",
            reason="ready",
        )

    def materializer(*, start: date, end: date):
        calls["materialize"] += 1
        assert (start, end) == (date(2026, 6, 6), date(2026, 6, 7))
        return {"row_count": 1}

    monkeypatch.setattr(materialization, "_dataset_builders", lambda: {"activitywatch_derived": builder})
    monkeypatch.setattr(materialization, "_materializers", lambda: {"activitywatch_derived": materializer})

    result = ensure_materialized(
        "activitywatch_derived",
        window=(date(2026, 6, 6), date(2026, 6, 7)),
        cfg=SimpleNamespace(),
    )

    assert result.status == "updated"
    assert result.coverage["fully_covers_requested_window"] is True
    assert calls == {"audit": 2, "materialize": 1}


def test_ensure_materialized_event_export_window_gap_is_not_zero_or_rebuild(monkeypatch) -> None:
    from lynchpin import materialization

    def builder(_cfg):
        return MaterializedDataset(
            name="reddit",
            status="ready",
            authority="raw fixture",
            query_surface="fixture",
            materialized_paths=(),
            raw_roots=(),
            row_count=1,
            first_date=date(2025, 1, 1),
            last_date=date(2025, 1, 1),
            materialization_hint="replace export",
            reason="ready export",
        )

    monkeypatch.setattr(materialization, "_dataset_builders", lambda: {"reddit": builder})
    monkeypatch.setattr(
        materialization,
        "_materializers",
        lambda: {"reddit": lambda: (_ for _ in ()).throw(AssertionError("should not run"))},
    )

    result = ensure_materialized(
        "reddit",
        window=(date(2026, 1, 1), date(2026, 1, 2)),
        cfg=SimpleNamespace(),
    )

    assert result.status == "ready"
    assert result.changed is False
    assert result.coverage["relation"] == "no_overlap"
    assert "not proof of zero activity" in result.coverage["interpretation"]


def test_ensure_materialized_coverage_bound_source_does_not_run_materializer(monkeypatch) -> None:
    from lynchpin import materialization

    def builder(_cfg):
        return MaterializedDataset(
            name="health",
            status="missing",
            authority="raw fixture",
            query_surface="fixture",
            materialized_paths=(),
            raw_roots=(),
            row_count=None,
            first_date=None,
            last_date=None,
            materialization_hint="replace export",
            reason="no export",
        )

    monkeypatch.setattr(materialization, "_dataset_builders", lambda: {"health": builder})
    monkeypatch.setattr(
        materialization,
        "_materializers",
        lambda: {"health": lambda: (_ for _ in ()).throw(AssertionError("should not run"))},
    )

    result = ensure_materialized("health", cfg=SimpleNamespace())

    assert result.status == "coverage_bound"
    assert result.changed is False
    assert "cannot extend" in result.reason


def test_ensure_materialized_manual_budget_blocks_local_work(monkeypatch) -> None:
    from lynchpin import materialization

    def builder(_cfg):
        return MaterializedDataset(
            name="machine",
            status="missing",
            authority="raw fixture",
            query_surface="fixture",
            materialized_paths=(),
            raw_roots=(),
            row_count=None,
            first_date=None,
            last_date=None,
            materialization_hint="refresh",
            reason="missing",
        )

    monkeypatch.setattr(materialization, "_dataset_builders", lambda: {"machine": builder})
    monkeypatch.setattr(
        materialization,
        "_materializers",
        lambda: {"machine": lambda: (_ for _ in ()).throw(AssertionError("should not run"))},
    )

    result = ensure_materialized("machine", budget="manual", cfg=SimpleNamespace())

    assert result.status == "blocked"
    assert result.changed is False
    assert "budget is manual" in result.reason


def test_substrate_materialization_snapshot_is_cheap_status(tmp_path) -> None:
    from lynchpin.materialization import substrate_materialization_snapshot

    substrate = tmp_path / "substrate.duckdb"
    missing = substrate_materialization_snapshot(substrate)

    assert missing.status == "blocked"
    assert missing.name == "evidence_graph_substrate"
    assert missing.changed is False

    substrate.write_bytes(b"duckdb fixture")
    ready = substrate_materialization_snapshot(
        substrate,
        latest_materialized_refresh_id="rid-1",
        latest_recorded_at="2026-06-05T00:00:00+00:00",
    )

    assert ready.status == "ready"
    assert ready.source_high_water["latest_materialized_refresh_id"] == "rid-1"
    assert ready.product_paths == (substrate,)


def test_duck_evidence_graph_status_reads_build_and_source_status_once(tmp_path) -> None:
    import duckdb

    from lynchpin.materialization import _duck_evidence_graph_status

    path = tmp_path / "substrate.duckdb"
    conn = duckdb.connect(str(path))
    try:
        conn.execute(
            """
            CREATE TABLE evidence_graph_build (
                refresh_id VARCHAR,
                node_count INTEGER,
                edge_count INTEGER,
                materialized_at TIMESTAMP,
                generated_at TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE substrate_source_status (
                refresh_id VARCHAR,
                source VARCHAR,
                status VARCHAR,
                reason VARCHAR,
                recorded_at TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            INSERT INTO evidence_graph_build VALUES
              ('old', 0, 0, TIMESTAMP '2026-06-04 00:00:00', TIMESTAMP '2026-06-04 00:00:00'),
              ('new', 12, 14, TIMESTAMP '2026-06-05 00:00:00', TIMESTAMP '2026-06-05 00:00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO substrate_source_status VALUES
              ('new', 'evidence_graph', 'ok', NULL, TIMESTAMP '2026-06-05 00:00:00')
            """
        )
    finally:
        conn.close()

    builds, latest_build_counts, latest_status = _duck_evidence_graph_status(path)

    assert builds == 2
    assert latest_build_counts == (12, 14)
    assert latest_status == ("ok", None)


def test_duck_evidence_graph_status_tolerates_missing_build_table(tmp_path) -> None:
    import duckdb

    from lynchpin.materialization import _duck_evidence_graph_status

    path = tmp_path / "substrate.duckdb"
    conn = duckdb.connect(str(path))
    try:
        conn.execute(
            """
            CREATE TABLE substrate_source_status (
                refresh_id VARCHAR,
                source VARCHAR,
                status VARCHAR,
                reason VARCHAR,
                recorded_at TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            INSERT INTO substrate_source_status VALUES
              ('rid-empty', 'evidence_graph', 'empty', 'no nodes', TIMESTAMP '2026-06-05 00:00:00')
            """
        )
    finally:
        conn.close()

    builds, latest_build_counts, latest_status = _duck_evidence_graph_status(path)

    assert builds is None
    assert latest_build_counts is None
    assert latest_status == ("empty", "no nodes")


def test_substrate_dataset_ready_from_successful_promotion_run(
    tmp_path,
    monkeypatch,
) -> None:
    import duckdb

    from lynchpin import materialization

    path = tmp_path / "substrate.duckdb"
    conn = duckdb.connect(str(path))
    try:
        conn.execute(
            """
            CREATE TABLE substrate_promotion_run (
                refresh_id VARCHAR,
                status VARCHAR,
                finished_at TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            INSERT INTO substrate_promotion_run VALUES
              ('rid-substrate', 'ok', TIMESTAMP '2026-06-05 00:00:00')
            """
        )
    finally:
        conn.close()

    monkeypatch.setattr(
        "lynchpin.substrate.connection.substrate_path",
        lambda: path,
    )

    row = materialization._git_substrate_dataset(
        SimpleNamespace(baseline_dir=tmp_path / "baseline", repo_root=tmp_path / "repo")
    )

    assert row.status == "ready"
    assert row.row_count == 1
    assert "promotion runs are present" in row.reason


def test_substrate_dataset_uses_current_status_manifest(tmp_path, monkeypatch) -> None:
    from lynchpin import materialization
    from lynchpin.substrate.status_manifest import substrate_status_manifest_path

    path = tmp_path / "substrate.duckdb"
    path.write_bytes(b"duckdb fixture")
    stat = path.stat()
    manifest_path = substrate_status_manifest_path(path)
    manifest_path.write_text(
        json.dumps(
            {
                "dataset": "evidence_graph_substrate",
                "substrate_size_bytes": stat.st_size,
                "substrate_mtime_ns": stat.st_mtime_ns,
                "status": "ready",
                "reason": "manifest proves current substrate",
                "row_count": 42,
                "first_date": "2026-05-01",
                "last_date": "2026-05-03",
                "covered_dates": ["2026-05-01", "2026-05-02", "2026-05-03"],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: path)
    monkeypatch.setattr(
        materialization,
        "_duck_substrate_status",
        lambda _: (_ for _ in ()).throw(AssertionError("status should come from manifest")),
    )

    row = materialization._git_substrate_dataset(
        SimpleNamespace(baseline_dir=tmp_path / "baseline", repo_root=tmp_path / "repo")
    )

    assert row.status == "ready"
    assert row.row_count == 42
    assert row.first_date == date(2026, 5, 1)
    assert row.last_date == date(2026, 5, 3)
    assert row.covered_dates == (
        date(2026, 5, 1),
        date(2026, 5, 2),
        date(2026, 5, 3),
    )
    assert row.reason == "manifest proves current substrate"
    assert row.materialized_paths == (path, manifest_path)


def test_substrate_status_manifest_records_latest_graph_bounds(tmp_path, monkeypatch) -> None:
    import duckdb

    from lynchpin.substrate.status_manifest import write_substrate_status_manifest

    path = tmp_path / "substrate.duckdb"
    conn = duckdb.connect(str(path))
    try:
        conn.execute(
            """
            CREATE TABLE evidence_graph_build (
                refresh_id VARCHAR,
                start_date DATE,
                end_date DATE,
                node_count INTEGER,
                edge_count INTEGER,
                generated_at TIMESTAMP,
                materialized_at TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            INSERT INTO evidence_graph_build VALUES
              (
                'rid-older',
                DATE '2026-04-01',
                DATE '2026-04-03',
                1,
                1,
                TIMESTAMP '2026-04-03 00:00:00',
                TIMESTAMP '2026-04-03 00:00:00'
              ),
              (
                'rid-latest',
                DATE '2026-05-01',
                DATE '2026-05-04',
                7,
                2,
                TIMESTAMP '2026-05-04 00:00:00',
                TIMESTAMP '2026-05-04 00:00:00'
              )
            """
        )
    finally:
        conn.close()

    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: path)

    manifest = write_substrate_status_manifest(path)

    assert manifest is not None
    assert manifest["status"] == "ready"
    assert manifest["latest_node_count"] == 7
    assert manifest["latest_edge_count"] == 2
    assert manifest["first_date"] == "2026-05-01"
    assert manifest["last_date"] == "2026-05-03"
    assert manifest["covered_dates"] == ["2026-05-01", "2026-05-02", "2026-05-03"]


def test_substrate_dataset_falls_back_when_status_manifest_is_stale(tmp_path, monkeypatch) -> None:
    from lynchpin import materialization
    from lynchpin.substrate.status_manifest import substrate_status_manifest_path

    path = tmp_path / "substrate.duckdb"
    path.write_bytes(b"duckdb fixture")
    manifest_path = substrate_status_manifest_path(path)
    manifest_path.write_text(
        json.dumps(
            {
                "dataset": "evidence_graph_substrate",
                "substrate_size_bytes": path.stat().st_size,
                "substrate_mtime_ns": path.stat().st_mtime_ns - 1,
                "status": "ready",
                "reason": "stale manifest",
                "row_count": 42,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: path)
    monkeypatch.setattr(materialization, "_duck_substrate_status", lambda _: (None, None, None, 3))

    row = materialization._git_substrate_dataset(
        SimpleNamespace(baseline_dir=tmp_path / "baseline", repo_root=tmp_path / "repo")
    )

    assert row.status == "ready"
    assert row.row_count == 3
    assert "promotion runs are present" in row.reason


def test_polylogue_date_bounds_reads_direct_sqlite_product(tmp_path) -> None:
    import sqlite3

    from lynchpin.materialization import _polylogue_date_bounds_from_sqlite

    path = tmp_path / "polylogue.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE session_profiles (
                conversation_id TEXT PRIMARY KEY,
                canonical_session_date TEXT
            )
            """
        )
        conn.execute("INSERT INTO session_profiles VALUES ('c1', '2026-06-05')")
        conn.execute("INSERT INTO session_profiles VALUES ('c2', '2026-06-01')")

    assert _polylogue_date_bounds_from_sqlite(path) == (
        date(2026, 6, 1),
        date(2026, 6, 5),
    )


def test_polylogue_date_bounds_direct_read_tolerates_missing_table(tmp_path) -> None:
    import sqlite3

    from lynchpin.materialization import _polylogue_date_bounds_from_sqlite

    path = tmp_path / "polylogue.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE unrelated (id TEXT)")

    assert _polylogue_date_bounds_from_sqlite(path) == (None, None)


def test_jsonl_date_bounds_use_current_manifests(tmp_path) -> None:
    from lynchpin.materialization import _jsonl_date_bounds

    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    first.write_text('{"date":"2000-01-01"}\n', encoding="utf-8")
    second.write_text('{"date":"2000-01-02"}\n', encoding="utf-8")
    for path, row_count, first_date, last_date in (
        (first, 2, "2026-06-01", "2026-06-02"),
        (second, 3, "2026-06-03", "2026-06-05"),
    ):
        path.with_suffix(".manifest.json").write_text(
            json.dumps(
                {
                    "row_count": row_count,
                    "first_date": first_date,
                    "last_date": last_date,
                }
            ),
            encoding="utf-8",
        )

    assert _jsonl_date_bounds((first, second)) == (
        5,
        date(2026, 6, 1),
        date(2026, 6, 5),
    )


def test_jsonl_date_bounds_ignore_stale_manifest(tmp_path) -> None:
    from lynchpin.materialization import _jsonl_date_bounds

    path = tmp_path / "product.jsonl"
    path.write_text('{"date":"2026-06-07"}\n', encoding="utf-8")
    manifest = path.with_suffix(".manifest.json")
    manifest.write_text(
        json.dumps(
            {
                "row_count": 5,
                "first_date": "2026-01-01",
                "last_date": "2026-01-05",
            }
        ),
        encoding="utf-8",
    )
    path.write_text('{"date":"2026-06-07"}\n{"date":"2026-06-08"}\n', encoding="utf-8")

    assert _jsonl_date_bounds((path,)) == (
        2,
        date(2026, 6, 7),
        date(2026, 6, 8),
    )


def test_jsonl_date_bounds_uses_valid_manifests_when_sibling_scans(tmp_path) -> None:
    from lynchpin.materialization import _jsonl_date_bounds

    manifested = tmp_path / "manifested.jsonl"
    scanned = tmp_path / "scanned.jsonl"
    manifested.write_text('{"date":"2000-01-01"}\n', encoding="utf-8")
    scanned.write_text('{"date":"2026-06-09"}\n{"date":"2026-06-10"}\n', encoding="utf-8")
    manifested.with_suffix(".manifest.json").write_text(
        json.dumps(
            {
                "row_count": 9,
                "first_date": "2026-06-01",
                "last_date": "2026-06-03",
            }
        ),
        encoding="utf-8",
    )

    assert _jsonl_date_bounds((manifested, scanned)) == (
        11,
        date(2026, 6, 1),
        date(2026, 6, 10),
    )


def test_manifest_row_count_does_not_scan_product_without_manifest_count(monkeypatch, tmp_path) -> None:
    from lynchpin import materialization

    product = tmp_path / "product.ndjson"
    product.write_text("{}\n{}\n", encoding="utf-8")
    monkeypatch.setattr(
        materialization,
        "_line_count",
        lambda _path: (_ for _ in ()).throw(AssertionError("manifest row counts must be cheap metadata")),
    )

    assert materialization._manifest_row_count({}, product) is None


def test_csv_date_bounds_are_signature_cached_and_invalidated(tmp_path) -> None:
    from lynchpin.materialization import _csv_date_bounds

    path = tmp_path / "product.csv"
    path.write_text("date,value\n2026-06-01,1\n2026-06-03,2\n", encoding="utf-8")

    assert _csv_date_bounds((path,)) == (
        2,
        date(2026, 6, 1),
        date(2026, 6, 3),
    )

    path.write_text("date,value\n2026-06-05,1\n", encoding="utf-8")

    assert _csv_date_bounds((path,)) == (
        1,
        date(2026, 6, 5),
        date(2026, 6, 5),
    )
