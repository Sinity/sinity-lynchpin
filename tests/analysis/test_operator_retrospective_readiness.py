from __future__ import annotations

from datetime import date
from pathlib import Path

from lynchpin.analysis.operator_retrospective_readiness import operator_retrospective_readiness
from lynchpin.materialization import MaterializedDataset


def _dataset(
    name: str,
    *,
    first: date | None,
    last: date | None,
    status: str = "ready",
) -> MaterializedDataset:
    return MaterializedDataset(
        name=name,
        status=status,  # type: ignore[arg-type]
        authority=f"{name} authority",
        query_surface=f"{name} surface",
        materialized_paths=(Path(f"/tmp/{name}.jsonl"),),
        raw_roots=(Path("/tmp/raw"),),
        row_count=10,
        first_date=first,
        last_date=last,
        materialization_hint=f"refresh {name}",
        reason="fixture",
    )


def test_retrospective_readiness_allows_behavioral_when_core_sources_cover_window() -> None:
    start = date(2026, 6, 1)
    end = date(2026, 6, 5)
    datasets = [
        _dataset("activitywatch", first=start, last=end),
        _dataset("atuin", first=start, last=end),
        _dataset("machine", first=start, last=end),
        _dataset("xtask_history", first=start, last=end),
        _dataset("polylogue_devtools", first=start, last=end),
        _dataset("webhistory", first=start, last=end),
        _dataset("irc", first=start, last=end),
        _dataset("substance", first=start, last=end),
        _dataset("polylogue", first=None, last=None, status="degraded"),
    ]

    report = operator_retrospective_readiness(start=start, end=end, datasets=datasets)

    assert report.behavioral_explanation_allowed is True
    assert report.mode == "behavioral"
    assert "polylogue" not in report.blocking_sources
    assert any("Polylogue chat semantics are caveated" in caveat for caveat in report.caveats)


def test_retrospective_readiness_blocks_behavioral_when_core_source_is_partial() -> None:
    start = date(2026, 6, 1)
    end = date(2026, 6, 5)
    datasets = [
        _dataset("activitywatch", first=start, last=date(2026, 6, 2)),
        _dataset("atuin", first=start, last=end),
        _dataset("machine", first=start, last=end),
        _dataset("xtask_history", first=start, last=end),
        _dataset("polylogue_devtools", first=start, last=end),
        _dataset("webhistory", first=start, last=date(2026, 6, 2)),
        _dataset("irc", first=start, last=end),
        _dataset("substance", first=start, last=end),
        _dataset("polylogue", first=None, last=None, status="degraded"),
    ]

    report = operator_retrospective_readiness(start=start, end=end, datasets=datasets)

    assert report.behavioral_explanation_allowed is False
    assert report.structural_explanation_allowed is True
    assert report.mode == "structural_only"
    assert report.blocking_sources == ("activitywatch",)


def test_retrospective_readiness_can_require_polylogue() -> None:
    start = date(2026, 6, 1)
    end = date(2026, 6, 5)
    datasets = [
        _dataset("activitywatch", first=start, last=end),
        _dataset("atuin", first=start, last=end),
        _dataset("machine", first=start, last=end),
        _dataset("xtask_history", first=start, last=end),
        _dataset("polylogue_devtools", first=start, last=end),
        _dataset("webhistory", first=start, last=end),
        _dataset("irc", first=start, last=end),
        _dataset("substance", first=start, last=end),
        _dataset("polylogue", first=None, last=None, status="degraded"),
    ]

    report = operator_retrospective_readiness(
        start=start,
        end=end,
        require_polylogue=True,
        datasets=datasets,
    )

    assert report.behavioral_explanation_allowed is False
    assert report.blocking_sources == ("polylogue",)
