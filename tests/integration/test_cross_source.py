"""Integration tests for cross-source analysis modules."""

from datetime import date

import pytest


@pytest.mark.slow
def test_operator_daily_matrix_basic():
    """Smoke test: operator_daily_matrix builds on a narrow window."""
    from lynchpin.analysis.operator_daily import operator_daily_matrix

    rows = operator_daily_matrix(date(2026, 5, 20), date(2026, 5, 28), skip_slow=True)
    assert len(rows) == 9
    assert all(r.date is not None for r in rows)

    active = [r for r in rows if r.aw_active_hours is not None and r.aw_active_hours > 0]
    assert len(active) >= 5

    git_days = [r for r in rows if r.git_commits > 0]
    assert len(git_days) >= 3


def test_operator_daily_empty_window():
    """Pre-source-era windows return rows with all signal defaults."""
    from lynchpin.analysis.operator_daily import operator_daily_matrix

    rows = operator_daily_matrix(date(2010, 1, 1), date(2010, 1, 3), skip_slow=True)
    assert len(rows) == 3
    for r in rows:
        assert r.git_commits == 0
        assert r.substance_doses == 0
        assert r.total_known_source_count <= 1


@pytest.mark.slow
def test_health_modeling_report():
    """Health modeling report runs without crashing."""
    from lynchpin.sources.samsung_binning import iter_stress_bins, iter_hrv_bins, iter_hr_bins
    from lynchpin.analysis.health_modeling import align_signals, build_report

    stress = list(iter_stress_bins())[:50000]
    hrv = list(iter_hrv_bins())
    hr = list(iter_hr_bins())[:50000]
    rows = align_signals(iter(stress), iter(hrv), iter(hr))
    report = build_report(rows)
    assert report.n_aligned_total > 0
    assert report.hr_only.r2 > 0


def test_daily_activity_consistency():
    """All sources with daily_activity should have consistent API shape."""
    sources = [
        ("lynchpin.sources.activitywatch", "AWDayActivity"),
        ("lynchpin.sources.git", "GitDayActivity"),
        ("lynchpin.sources.web", "WebDayActivity"),
        ("lynchpin.sources.terminal", "DailyTerminalActivity"),
        ("lynchpin.sources.spotify", "DailyListening"),
        ("lynchpin.sources.keylog", "KeylogDayActivity"),
        ("lynchpin.sources.sleep", "SleepDayActivity"),
        ("lynchpin.sources.wykop", "WykopDayActivity"),
        ("lynchpin.sources.sms", "SMSDayActivity"),
        ("lynchpin.sources.outlook", "OutlookDayActivity"),
    ]
    import importlib
    for mod_name, type_name in sources:
        mod = importlib.import_module(mod_name)
        assert hasattr(mod, "daily_activity"), f"{mod_name} missing daily_activity"
        cls = getattr(mod, type_name, None)
        assert cls is not None, f"{mod_name} missing {type_name}"
        # date is an instance field, verify via annotations or __init__
        assert "date" in (getattr(cls, "__annotations__", {}) or {}), f"{type_name} missing date field"


def test_all_source_modules_importable():
    """Every source module should import without crashing."""
    import importlib
    from pathlib import Path

    sources_dir = Path("lynchpin/sources")
    for f in sorted(sources_dir.glob("*.py")):
        name = f.stem
        if name.startswith("_") or name == "__init__":
            continue
        mod = importlib.import_module(f"lynchpin.sources.{name}")
        assert mod is not None, f"Failed to import lynchpin.sources.{name}"
