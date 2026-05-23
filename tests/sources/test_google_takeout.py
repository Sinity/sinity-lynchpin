from __future__ import annotations

import json
import tarfile
import zipfile

from lynchpin.sources.google_takeout import (
    archive_inventory,
    discover_takeout_archives,
    iter_chrome_history_batches,
    iter_member_bytes,
)


def test_archive_inventory_counts_products_and_chrome_members(tmp_path):
    archive = tmp_path / "takeout.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            "Takeout/Chrome/History.json",
            json.dumps({
                "Session": [
                    {
                        "tab": {
                            "navigation": [
                                {
                                    "timestamp_msec": "1773717025000",
                                    "virtual_url": "https://example.com/",
                                }
                            ]
                        }
                    }
                ]
            }),
        )
        zf.writestr("Takeout/My Activity/Search/MyActivity.json", "[]")

    rows = archive_inventory(tmp_path)

    assert len(rows) == 1
    assert rows[0].member_count == 2
    assert rows[0].chrome_history_members == 1
    assert dict(rows[0].product_counts) == {"Chrome": 1, "My Activity": 1}


def test_chrome_history_batches_read_zip_and_tgz(tmp_path):
    payload = json.dumps({
        "Browser History": [
            {
                "time_usec": "1773717025000000",
                "url": "https://legacy.example/",
                "title": "Legacy",
            }
        ]
    })
    zip_path = tmp_path / "takeout.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("Takeout/Chrome/BrowserHistory.json", payload)
    source = tmp_path / "History.json"
    source.write_text(payload, encoding="utf-8")
    with tarfile.open(tmp_path / "takeout.tgz", "w:gz") as tf:
        tf.add(source, arcname="Takeout/Chrome/History.json")

    batches = list(iter_chrome_history_batches(tmp_path))

    assert len(batches) == 2
    assert sum(len(batch.visits) for batch in batches) == 2
    assert {batch.archive.name for batch in batches} == {"takeout.zip", "takeout.tgz"}


def test_discover_takeout_archives_ignores_nested_extracted_caches(tmp_path):
    raw = tmp_path / "takeout-raw.zip"
    nested_dir = tmp_path / "takeout-extracted" / "historical" / "x"
    nested_dir.mkdir(parents=True)
    nested = nested_dir / "nested.zip"
    for path in (raw, nested):
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("Takeout/Chrome/History.json", "{}")

    assert discover_takeout_archives(tmp_path) == (raw,)


def test_iter_member_bytes_filters_product_and_suffix(tmp_path):
    archive = tmp_path / "takeout.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("Takeout/Tasks/Tasks.json", "{}")
        zf.writestr("Takeout/Calendar/example.ics", "BEGIN:VCALENDAR")

    rows = list(iter_member_bytes(root=tmp_path, products={"Tasks"}, suffixes={".json"}))

    assert len(rows) == 1
    assert rows[0][0].path == "Takeout/Tasks/Tasks.json"
    assert rows[0][1] == b"{}"
