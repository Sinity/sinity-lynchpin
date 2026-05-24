from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

from lynchpin.graph.coverage import coverage_report
from lynchpin.materialization import MaterializedDataset


def _dataset(
    name: str,
    *,
    status: str = "ready",
    first: date | None = date(2026, 5, 1),
    last: date | None = date(2026, 5, 23),
    rows: int | None = 10,
) -> MaterializedDataset:
    return MaterializedDataset(
        name=name,
        status=status,
        authority="fixture",
        query_surface="fixture",
        materialized_paths=(),
        raw_roots=(),
        row_count=rows,
        first_date=first,
        last_date=last,
        refresh_command="refresh",
        reason="fixture",
    )


def test_coverage_report_uses_materialized_datasets(monkeypatch, tmp_path) -> None:
    names = {
        "activitywatch",
        "atuin",
        "webhistory",
        "sleep",
        "health",
        "spotify",
        "reddit",
        "facebook_messenger",
        "raindrop",
        "substance",
    }
    rows = [_dataset(name) for name in names]

    monkeypatch.setattr("lynchpin.materialization.audit_materialization", lambda cfg=None: rows)
    monkeypatch.setattr(
        "lynchpin.graph.coverage.get_config",
        lambda: SimpleNamespace(
            activitywatch_db=tmp_path / "aw.db",
            atuin_db=tmp_path / "atuin.db",
            webhistory_ndjson=tmp_path / "web.ndjson",
            sleep_jsonl=tmp_path / "sleep.ndjson",
            samsung_gdpr_cloud_dir=tmp_path / "health",
            spotify_root=tmp_path / "spotify",
            reddit_export_dir=tmp_path / "reddit",
            fbmessenger_gdpr_root=tmp_path / "messenger",
            raindrop_csv=tmp_path / "raindrop.csv",
            exports_root=tmp_path / "exports",
        ),
    )

    report = coverage_report(start=date(2026, 5, 20), end=date(2026, 5, 24))
    by_source = report.by_source()

    assert by_source["spotify"].status == "available"
    assert by_source["spotify"].basis == "canonical-ndjson"
    assert by_source["messenger"].row_count == 10


def test_coverage_report_treats_end_as_exclusive(monkeypatch, tmp_path) -> None:
    names = {
        "activitywatch",
        "atuin",
        "webhistory",
        "sleep",
        "health",
        "spotify",
        "reddit",
        "facebook_messenger",
        "raindrop",
        "substance",
    }
    rows = [
        _dataset(
            name,
            first=date(2026, 5, 1),
            last=date(2026, 5, 22),
        )
        for name in names
    ]

    monkeypatch.setattr("lynchpin.materialization.audit_materialization", lambda cfg=None: rows)
    monkeypatch.setattr(
        "lynchpin.graph.coverage.get_config",
        lambda: SimpleNamespace(
            activitywatch_db=Path("/aw.db"),
            atuin_db=Path("/atuin.db"),
            webhistory_ndjson=Path("/web.ndjson"),
            sleep_jsonl=Path("/sleep.ndjson"),
            samsung_gdpr_cloud_dir=Path("/health"),
            spotify_root=Path("/spotify"),
            reddit_export_dir=Path("/reddit"),
            fbmessenger_gdpr_root=Path("/messenger"),
            raindrop_csv=Path("/raindrop.csv"),
            exports_root=tmp_path / "exports",
        ),
    )

    report = coverage_report(start=date(2026, 5, 20), end=date(2026, 5, 23))

    assert report.by_source()["spotify"].status == "available"
