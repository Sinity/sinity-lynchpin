from __future__ import annotations

import json
from datetime import date

from lynchpin.ingest.manifest_windows import (
    half_open_dates,
    merge_manifest_covered_dates,
    read_manifest_covered_dates,
)


def test_half_open_dates_excludes_end() -> None:
    assert half_open_dates(date(2026, 6, 1), date(2026, 6, 3)) == (
        date(2026, 6, 1),
        date(2026, 6, 2),
    )


def test_read_manifest_covered_dates_tolerates_malformed_json(tmp_path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{", encoding="utf-8")

    assert read_manifest_covered_dates(manifest) == ()


def test_merge_manifest_covered_dates_prefers_precise_dates(tmp_path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "first_date": "2026-06-01",
                "last_date": "2026-06-09",
                "covered_dates": ["2026-06-01", "2026-06-05", "2026-06-09"],
            }
        ),
        encoding="utf-8",
    )

    assert merge_manifest_covered_dates(
        manifest=manifest,
        start=date(2026, 6, 5),
        end=date(2026, 6, 7),
    ) == (
        date(2026, 6, 1),
        date(2026, 6, 5),
        date(2026, 6, 6),
        date(2026, 6, 9),
    )


def test_merge_manifest_covered_dates_falls_back_to_bounds(tmp_path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"first_date": "2026-06-01", "last_date": "2026-06-03"}),
        encoding="utf-8",
    )

    assert merge_manifest_covered_dates(
        manifest=manifest,
        start=date(2026, 6, 2),
        end=date(2026, 6, 4),
    ) == (
        date(2026, 6, 1),
        date(2026, 6, 2),
        date(2026, 6, 3),
    )


def test_merge_manifest_covered_dates_preserves_observed_sparse_days(tmp_path) -> None:
    manifest = tmp_path / "missing.json"

    assert merge_manifest_covered_dates(
        manifest=manifest,
        observed_dates=(date(2026, 6, 1), date(2026, 6, 9)),
        start=date(2026, 6, 5),
        end=date(2026, 6, 6),
    ) == (
        date(2026, 6, 1),
        date(2026, 6, 5),
        date(2026, 6, 9),
    )
