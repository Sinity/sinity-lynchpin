from __future__ import annotations

import json
import os
from pathlib import Path

from lynchpin.ingest import machine_materialize


def _age(path: Path, *, now: float = 10_000.0) -> None:
    os.utime(path, (now - 5_000, now - 5_000), follow_symlinks=False)


def test_cleanup_machine_staging_is_previewed_allowlisted_and_activity_safe(
    monkeypatch, tmp_path: Path
) -> None:
    tables = ("legacy", "unique", "active", "linked")
    monkeypatch.setattr(machine_materialize, "MACHINE_TABLES", tables)
    monkeypatch.setattr(
        machine_materialize,
        "canonical_machine_table_path",
        lambda name: tmp_path / f"{name}.ndjson",
    )
    serving = tmp_path / "legacy.ndjson"
    serving.write_text("serving\n", encoding="utf-8")
    legacy = tmp_path / "legacy.ndjson.tmp"
    legacy.write_bytes(b"legacy")
    unique = tmp_path / f".unique.ndjson.{'a' * 32}.tmp"
    unique.write_bytes(b"unique")
    active = tmp_path / f".active.ndjson.{'b' * 32}.tmp"
    active.write_bytes(b"active")
    linked = tmp_path / "linked.ndjson.tmp"
    linked.symlink_to(serving)
    for path in (legacy, unique, active):
        _age(path)
    monkeypatch.setattr(
        machine_materialize,
        "_path_is_open",
        lambda path: path == active,
    )

    preview = machine_materialize.cleanup_machine_staging(
        grace_period_s=1,
        apply=False,
        now=10_000,
    )
    dispositions = {
        Path(entry["path"]).name: entry["disposition"] for entry in preview["entries"]
    }
    assert dispositions == {
        legacy.name: "stale",
        unique.name: "stale",
        active.name: "active",
        linked.name: "unsafe",
    }
    assert preview["reclaimable_bytes"] == len(b"legacy") + len(b"unique")
    assert (
        serving.exists()
        and legacy.exists()
        and unique.exists()
        and active.exists()
        and linked.is_symlink()
    )

    applied = machine_materialize.cleanup_machine_staging(
        grace_period_s=1,
        apply=True,
        now=10_000,
    )
    assert applied["deleted_bytes"] == len(b"legacy") + len(b"unique")
    assert serving.exists()
    assert not legacy.exists() and not unique.exists()
    assert active.exists() and linked.is_symlink()


def test_cleanup_machine_staging_cli_requires_explicit_mode_and_writes_receipt(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    monkeypatch.setattr(machine_materialize, "MACHINE_TABLES", ("metric_sample",))
    monkeypatch.setattr(
        machine_materialize,
        "canonical_machine_table_path",
        lambda name: tmp_path / f"{name}.ndjson",
    )
    monkeypatch.setattr(machine_materialize, "_path_is_open", lambda _path: False)
    stale = tmp_path / "metric_sample.ndjson.tmp"
    stale.write_bytes(b"stale")
    _age(stale)
    receipt = tmp_path / "receipt.json"

    assert (
        machine_materialize.main(
            [
                "--cleanup-staging",
                "--apply",
                "--grace-period-s",
                "1",
                "--receipt",
                str(receipt),
            ]
        )
        == 0
    )
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert json.loads(capsys.readouterr().out) == payload
    assert payload["deleted_bytes"] == len(b"stale")
    assert not stale.exists()
