from pathlib import Path

from lynchpin.ingest.exports_materialize import (
    REDDIT_CANONICAL_SCHEMA_VERSION,
    SPOTIFY_STREAMS_SCHEMA_VERSION,
    _export_roots,
    _row_date_bounds,
    _spotify_roots,
    _write_manifest,
    _write_reddit_manifest,
)


def test_export_roots_only_accept_dated_directories(tmp_path: Path) -> None:
    dated = tmp_path / "2026-01-02"
    dated.mkdir()
    for name in ("legacy", "raw", "canonical", "not-an-export"):
        (tmp_path / name).mkdir()

    assert _export_roots(tmp_path) == [dated]


def test_spotify_roots_do_not_descend_into_legacy_staging(tmp_path: Path) -> None:
    dated = tmp_path / "2026-01-02"
    (dated / "Spotify Account Data").mkdir(parents=True)
    legacy = tmp_path / "legacy"
    (legacy / "Spotify Account Data").mkdir(parents=True)

    assert _spotify_roots(tmp_path) == [dated]


def test_export_manifest_records_input_high_water(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_text("[]", encoding="utf-8")
    product = tmp_path / "product.ndjson"
    product.write_text("{}\n", encoding="utf-8")
    manifest_path = tmp_path / "product.manifest.json"

    manifest = _write_manifest(
        manifest_path,
        "fixture.dataset",
        [{"created": "2026-01-02T03:04:05+00:00", "source_file": str(source)}],
        product_path=product,
        source_files=(source,),
        schema_version=SPOTIFY_STREAMS_SCHEMA_VERSION,
        extra={"thread_count": 3},
    )

    assert manifest["schema_version"] == SPOTIFY_STREAMS_SCHEMA_VERSION
    assert manifest["input_file_count"] == 1
    assert manifest["input_files"] == [str(source)]
    assert manifest["input_latest_mtime"] is not None
    assert manifest["first_date"] == "2026-01-02"
    assert manifest["thread_count"] == 3
    assert '"thread_count": 3' in manifest_path.read_text(encoding="utf-8")
    assert manifest_path.exists()


def test_reddit_manifest_records_aggregate_bounds(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    source.write_text("date\n2026-01-01\n", encoding="utf-8")
    manifest_path = tmp_path / "reddit" / "manifest.json"
    manifest_path.parent.mkdir()

    manifest = _write_reddit_manifest(
        manifest_path,
        {
            "comments.csv": {
                "row_count": 2,
                "first_date": "2026-01-03",
                "last_date": "2026-01-04",
            },
            "posts.csv": {
                "row_count": 1,
                "first_date": "2026-01-01",
                "last_date": "2026-01-02",
            },
        },
        product_path=manifest_path.parent,
        source_files=(source,),
    )

    assert manifest["schema_version"] == REDDIT_CANONICAL_SCHEMA_VERSION
    assert manifest["row_count"] == 3
    assert manifest["first_date"] == "2026-01-01"
    assert manifest["last_date"] == "2026-01-04"
    assert manifest["input_files"] == [str(source)]
    assert manifest["input_file_count"] == 1


def test_reddit_style_utc_dates_contribute_to_bounds() -> None:
    assert _row_date_bounds(
        (
            {"date": "2013-10-19 00:03:30 UTC"},
            {"date": "2014-02-13 15:06:36 UTC"},
        )
    ) == ("2013-10-19", "2014-02-13")
