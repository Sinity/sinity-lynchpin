from pathlib import Path

from lynchpin.ingest.exports_materialize import _export_roots, _spotify_roots


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
