"""Tests for knowledgebase bundle discovery helpers."""

from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

from lynchpin.analysis.evidence_bundles import (
    build_bundle_catalog,
    discover_bundle_records,
    ensure_unpacked_bundle,
    preferred_bundle,
    unpack_preferred_bundle,
)


def _make_zip(path: Path, members: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)


class TestEvidenceBundles:
    def test_discover_normalizes_duplicate_names(self, tmp_path: Path) -> None:
        _make_zip(
            tmp_path / "alpha_bundle.zip",
            {
                "alpha/report.md": "# report",
                "alpha/summary.json": '{"ok": true}',
            },
        )
        _make_zip(
            tmp_path / "alpha_bundle (1).zip",
            {
                "alpha/report.md": "# newer report",
                "alpha/summary.json": '{"ok": false}',
            },
        )

        records = discover_bundle_records(tmp_path)
        assert len(records) == 2
        assert {record.canonical_name for record in records} == {"alpha_bundle"}
        assert {record.duplicate_index for record in records} == {0, 1}
        assert all(record.report_member == "alpha/report.md" for record in records)

    def test_preferred_bundle_uses_latest_mtime(self, tmp_path: Path) -> None:
        older = tmp_path / "alpha_bundle.zip"
        newer = tmp_path / "alpha_bundle (1).zip"
        _make_zip(older, {"alpha/summary.json": '{"version": 1}'})
        _make_zip(newer, {"alpha/summary.json": '{"version": 2}'})
        os.utime(older, (10, 10))
        os.utime(newer, (20, 20))

        chosen = preferred_bundle(discover_bundle_records(tmp_path), "alpha_bundle")
        assert chosen is not None
        assert chosen.filename == "alpha_bundle (1).zip"

    def test_catalog_groups_duplicates(self, tmp_path: Path) -> None:
        _make_zip(tmp_path / "alpha_bundle.zip", {"alpha/summary.json": '{"ok": 1}'})
        _make_zip(tmp_path / "alpha_bundle (1).zip", {"alpha/summary.json": '{"ok": 2}'})
        _make_zip(tmp_path / "beta_bundle.zip", {"beta/report.md": "# beta"})

        out = tmp_path / "catalog.json"
        payload = build_bundle_catalog(out, root=tmp_path)

        assert payload["bundle_count"] == 3
        assert payload["canonical_bundle_count"] == 2
        assert "alpha_bundle" in payload["canonical_groups"]
        assert payload["canonical_groups"]["alpha_bundle"]["preferred"]["filename"] == "alpha_bundle (1).zip"
        assert out.exists()

    def test_ensure_unpacked_bundle_materializes_contents(self, tmp_path: Path) -> None:
        archive = tmp_path / "alpha_bundle.zip"
        _make_zip(
            archive,
            {
                "alpha/summary.json": '{"ok": true}',
                "alpha/report.md": "# report",
            },
        )

        record = discover_bundle_records(tmp_path)[0]
        unpacked = ensure_unpacked_bundle(record, root=tmp_path / "out")

        assert unpacked.exists()
        assert (unpacked / "alpha" / "summary.json").exists()
        assert (unpacked / ".bundle-source.json").exists()

    def test_unpack_preferred_bundle_selects_latest_duplicate(self, tmp_path: Path) -> None:
        older = tmp_path / "alpha_bundle.zip"
        newer = tmp_path / "alpha_bundle (1).zip"
        _make_zip(older, {"alpha/summary.json": '{"version": 1}'})
        _make_zip(newer, {"alpha/summary.json": '{"version": 2}'})
        os.utime(older, (10, 10))
        os.utime(newer, (20, 20))

        unpacked = unpack_preferred_bundle("alpha_bundle", bundle_root=tmp_path, root=tmp_path / "out")
        summary = (unpacked / "alpha" / "summary.json").read_text()

        assert json.loads(summary) == {"version": 2}
