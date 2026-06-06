from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

from lynchpin.graph.coverage import coverage_report
from lynchpin.materialization import MaterializationResult, MaterializedDataset


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
        materialization_hint="refresh",
        reason="fixture",
    )


def _materialization_result(name: str, *, changed: bool = False) -> MaterializationResult:
    return MaterializationResult(
        name=name,
        status="updated" if changed else "ready",
        changed=changed,
        reason="fixture",
        elapsed_ms=0,
        product_paths=(),
        source_high_water={"row_count": 10, "first_date": "2026-05-01", "last_date": "2026-05-23"},
        coverage={"relation": "covers_window"},
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
    ensure_calls = []

    monkeypatch.setattr("lynchpin.materialization.audit_materialization", lambda cfg=None: rows)
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, window=None, budget="inline", cfg=None, force=False: (
            ensure_calls.append((name, window, budget)) or _materialization_result(name)
        ),
    )
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
    assert ("webhistory", (date(2026, 5, 20), date(2026, 5, 24)), "inline") in ensure_calls
    assert ("activitywatch", (date(2026, 5, 20), date(2026, 5, 24)), "inline") in ensure_calls
    assert ("spotify", (date(2026, 5, 20), date(2026, 5, 24)), "inline") in ensure_calls
    assert ("reddit", (date(2026, 5, 20), date(2026, 5, 24)), "inline") in ensure_calls


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
    ensure_calls = []

    monkeypatch.setattr("lynchpin.materialization.audit_materialization", lambda cfg=None: rows)
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, window=None, budget="inline", cfg=None, force=False: (
            ensure_calls.append((name, window, budget)) or _materialization_result(name)
        ),
    )
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
    assert ("spotify", (date(2026, 5, 20), date(2026, 5, 23)), "inline") in ensure_calls


def test_coverage_report_reaudits_after_inline_materialization(monkeypatch, tmp_path) -> None:
    first_rows = [_dataset("webhistory", status="missing", first=None, last=None, rows=None)]
    second_rows = [_dataset("webhistory", first=date(2026, 5, 20), last=date(2026, 5, 23), rows=4)]
    audits = {"count": 0}

    def fake_audit(cfg=None):
        audits["count"] += 1
        return first_rows if audits["count"] == 1 else second_rows

    monkeypatch.setattr("lynchpin.materialization.audit_materialization", fake_audit)
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, window=None, budget="inline", cfg=None, force=False: _materialization_result(name, changed=True),
    )
    monkeypatch.setattr(
        "lynchpin.graph.coverage.get_config",
        lambda: SimpleNamespace(exports_root=tmp_path / "exports"),
    )

    report = coverage_report(start=date(2026, 5, 20), end=date(2026, 5, 24))

    assert audits["count"] == 2
    assert report.by_source()["webhistory"].status == "available"


def test_coverage_report_can_skip_inline_materialization_repair(monkeypatch, tmp_path) -> None:
    rows = [_dataset("webhistory", status="missing", first=None, last=None, rows=None)]
    ensure_calls = []

    monkeypatch.setattr("lynchpin.materialization.audit_materialization", lambda cfg=None: rows)
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, window=None, budget="inline", cfg=None, force=False: (
            ensure_calls.append((name, window, budget)) or _materialization_result(name)
        ),
    )
    monkeypatch.setattr(
        "lynchpin.graph.coverage.get_config",
        lambda: SimpleNamespace(exports_root=tmp_path / "exports"),
    )

    report = coverage_report(
        start=date(2026, 5, 20),
        end=date(2026, 5, 24),
        repair_materializations=False,
    )

    assert ensure_calls == []
    assert report.by_source()["webhistory"].status == "missing"
