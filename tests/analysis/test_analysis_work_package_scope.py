from lynchpin.analysis.ecosystem.work_package_scope import (
    _cluster_commit_records,
    _scope_geom,
    _surface_for_path,
)


def _record(
    *,
    date: str,
    dominant_surface: str,
    surfaces: list[str],
    subject_signature: str = "feat(storage)",
    author: str = "Sinity",
) -> dict:
    return {
        "date": date,
        "dt": None,
        "dominant_surface": dominant_surface,
        "surfaces": surfaces,
        "subject_signature": subject_signature,
        "author": author,
    }


def test_scope_geom_uses_all_three_axes() -> None:
    assert _scope_geom(0.0, 0, 0) == 0.0
    assert _scope_geom(2.5, 9, 4) > _scope_geom(2.5, 3, 4)


def test_surface_for_path_uses_polylogue_second_level_area() -> None:
    assert _surface_for_path("polylogue", "polylogue/storage/repair.py") == "storage"
    assert _surface_for_path("polylogue", "tests/unit/test_repair.py") == "tests/unit"


def test_surface_for_path_uses_sinex_crate_name() -> None:
    assert _surface_for_path("sinex", "crate/lib/sinex-node-sdk/src/lib.rs") == "sinex-node-sdk"
    assert _surface_for_path("sinex", "tests/integration/foo.rs") == "tests/integration"


def test_cluster_commit_records_splits_when_surfaces_break() -> None:
    records = [
        _record(date="2026-04-10T10:00:00+00:00", dominant_surface="storage", surfaces=["storage"]),
        _record(date="2026-04-11T10:00:00+00:00", dominant_surface="storage", surfaces=["storage", "schemas"]),
        _record(date="2026-04-11T11:00:00+00:00", dominant_surface="ui", surfaces=["ui"], subject_signature="feat(ui)"),
    ]
    clusters = _cluster_commit_records(records)
    assert len(clusters) == 2
    assert len(clusters[0]) == 2
    assert len(clusters[1]) == 1
