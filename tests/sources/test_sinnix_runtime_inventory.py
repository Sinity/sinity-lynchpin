import json

from lynchpin.sources.sinnix_runtime_inventory import read_inventory, readiness


def test_sinnix_runtime_inventory_reads_inventory(tmp_path):
    path = tmp_path / "runtime-inventory.json"
    path.write_text(
        json.dumps(
            {
                "schema": "sinnix-runtime-inventory-v1",
                "hostname": "sinnix-prime",
                "classes": {"observability": {"serviceConfig": {"Slice": "system-critical.slice"}}},
                "commandClasses": {"build": {"resourceClass": "developer-build"}},
                "environmentAllowList": ["PATH"],
                "slices": {"system": {"system-critical": {}}, "user": {}},
                "surfaces": {"machine-telemetry": {"unit": "machine-telemetry.service"}},
                "observedServices": [{"name": "machine-telemetry", "unit": "machine-telemetry.service"}],
                "captures": [{"name": "machine-telemetry", "path": "/realm/data/captures/machine"}],
                "mounts": [{"path": "/realm", "warnPct": 80, "failPct": 90}],
                "backups": {"snapshotDirs": [], "backupTargets": []},
            }
        ),
        encoding="utf-8",
    )

    inventory = read_inventory(path)
    status = readiness(path)

    assert inventory.hostname == "sinnix-prime"
    assert inventory.observed_services[0]["unit"] == "machine-telemetry.service"
    assert inventory.command_classes["build"]["resourceClass"] == "developer-build"
    assert status.status == "ok"
    assert status.row_count == 2


def test_sinnix_runtime_inventory_rejects_wrong_schema(tmp_path):
    path = tmp_path / "runtime-inventory.json"
    path.write_text(
        json.dumps(
            {
                "schema": "old",
                "hostname": "sinnix-prime",
                "classes": {},
                "commandClasses": {},
                "environmentAllowList": [],
                "slices": {},
                "surfaces": {},
                "observedServices": [],
                "captures": [],
                "mounts": [],
                "backups": {},
            }
        ),
        encoding="utf-8",
    )

    status = readiness(path)

    assert status.status == "error"
    assert "sinnix-runtime-inventory-v1" in status.reason
