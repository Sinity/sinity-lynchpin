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


def test_merge_manifest_covered_dates_purges_phantom_dates_outside_verified_bounds(tmp_path) -> None:
    """Regression test for lynchpin-jzb.

    A manifest previously accumulated placeholder/bad-merge dates (2010 and
    2017-2018) that have zero backing events in the real source data. Those
    claims must not be silently re-affirmed forever just because they sit
    outside the window a given run happens to touch -- they should be
    dropped once the caller can prove (via ``verified_bounds``) that no
    currently-known data supports them.
    """

    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "first_date": "2010-01-01",
                "last_date": "2026-07-13",
                "covered_dates": [
                    "2010-01-01",
                    "2010-01-02",
                    "2017-01-30",
                    "2024-02-15",
                    "2026-07-12",
                ],
            }
        ),
        encoding="utf-8",
    )

    result = merge_manifest_covered_dates(
        manifest=manifest,
        start=date(2026, 7, 12),
        end=date(2026, 7, 13),
        observed_dates=(date(2026, 7, 12),),
        verified_bounds=(date(2024, 2, 15), date(2026, 7, 12)),
    )

    assert date(2010, 1, 1) not in result
    assert date(2010, 1, 2) not in result
    assert date(2017, 1, 30) not in result
    assert result == (date(2024, 2, 15), date(2026, 7, 12))


def test_merge_manifest_covered_dates_skips_verification_without_bounds(tmp_path) -> None:
    """Without verified_bounds, historical merge behaviour is unchanged.

    Callers that cannot cheaply establish true data bounds this run (no
    rows at all, e.g. an empty first run) must not have their manifest
    silently wiped -- verification is an additive guard, not a mandatory
    purge.
    """

    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"covered_dates": ["2010-01-01", "2026-07-12"]}),
        encoding="utf-8",
    )

    result = merge_manifest_covered_dates(
        manifest=manifest,
        start=date(2026, 7, 13),
        end=date(2026, 7, 14),
    )

    assert date(2010, 1, 1) in result
    assert date(2026, 7, 12) in result
