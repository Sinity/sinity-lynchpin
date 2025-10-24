"""Statistical-integrity tests for substance × health correlation analysis.

These pin the three correctness contracts that keep ``analyze`` from feeding
false claims into LLM narratives:

1. Multiple-comparisons (Benjamini-Hochberg FDR) correction across the full
   substance × signal × lag test family; q-value + n surfaced per correlation.
2. Per-dose-bucket n with sub-``MIN_BUCKET_N`` buckets flagged unreliable.
3. Coverage clamping — days past the substance log's coverage are excluded
   (missing ≠ zero), never coerced to a fabricated 0 mg "abstinence".
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from types import SimpleNamespace

import lynchpin.analysis.substance_health as sh
from lynchpin.analysis.operator_daily import OperatorDay
from lynchpin.analysis.substance_health import MIN_BUCKET_N, analyze


def _day(d: date, *, mg: float = 0.0, stress: float | None = None) -> OperatorDay:
    row = OperatorDay(date=d)
    row.substance_mg_by_name = {"test_substance": mg} if mg else {}
    row.substance_doses = 1 if mg > 0 else 0
    row.stress_mean = stress
    return row


def _patch(monkeypatch, rows, *, substance_cov, health_cov):
    """Patch the matrix builder + materialization audit used by ``analyze``."""
    monkeypatch.setattr(sh, "operator_daily_matrix", lambda *a, **k: rows)

    audit_rows = [
        SimpleNamespace(name="substance", first_date=substance_cov[0], last_date=substance_cov[1]),
        SimpleNamespace(name="health", first_date=health_cov[0], last_date=health_cov[1]),
        SimpleNamespace(name="sleep", first_date=health_cov[0], last_date=health_cov[1]),
    ]
    import lynchpin.materialization as materialization

    monkeypatch.setattr(materialization, "audit_materialization", lambda: audit_rows)


def test_fdr_correction_suppresses_pure_noise(monkeypatch):
    """Random substance/stress data must yield NO FDR-significant associations.

    With ~|substances|×|signals|×(lag+1) tests, raw |r|>0.2 will flag false
    positives; BH FDR must reject them. Each correlation carries q + n.
    """
    rng = random.Random(1234)
    start = date(2024, 1, 1)
    rows = []
    for i in range(120):
        d = start + timedelta(days=i)
        rows.append(
            _day(
                d,
                mg=rng.choice([0.0, 50.0, 100.0, 150.0]),
                stress=rng.uniform(20.0, 80.0),
            )
        )
    end = rows[-1].date
    _patch(monkeypatch, rows, substance_cov=(start, end), health_cov=(start, end))

    report = analyze(
        start, end,
        substances=("test_substance",),
        health_signals=("stress_mean",),
        max_lag=7,
    )

    assert report.lag_correlations, "expected correlations to be computed"
    # Every correlation carries the significance machinery.
    for c in report.lag_correlations:
        assert 0.0 <= c.p_value <= 1.0
        assert 0.0 <= c.q_value <= 1.0
        assert c.n >= sh.MIN_PAIRS
        assert c.significant == (c.q_value < sh.FDR_TARGET)
    # Pure noise: nothing should survive FDR.
    assert not any(c.significant for c in report.lag_correlations)
    assert report.n_tests == len(report.lag_correlations)
    assert "Benjamini-Hochberg" in report.summary


def test_real_signal_survives_fdr(monkeypatch):
    """A genuine lag-0 dose→stress signal must survive FDR and be flagged."""
    start = date(2024, 1, 1)
    rng = random.Random(7)
    rows = []
    for i in range(120):
        d = start + timedelta(days=i)
        mg = float((i % 4) * 50)  # 0,50,100,150 cycling
        stress = 30.0 + 0.3 * mg + rng.uniform(-3.0, 3.0)  # strong same-day link
        rows.append(_day(d, mg=mg, stress=stress))
    end = rows[-1].date
    _patch(monkeypatch, rows, substance_cov=(start, end), health_cov=(start, end))

    report = analyze(
        start, end,
        substances=("test_substance",),
        health_signals=("stress_mean",),
        max_lag=7,
    )
    sig = [c for c in report.lag_correlations if c.significant]
    assert sig, "a strong same-day dose→stress link should survive FDR"
    assert any(c.lag_days == 0 for c in sig)
    assert all(c.q_value < sh.FDR_TARGET for c in sig)


def test_dose_buckets_carry_n_and_flag_small(monkeypatch):
    """Dose buckets report n; buckets below MIN_BUCKET_N are flagged unreliable."""
    start = date(2024, 1, 1)
    rows = []
    # 10 days at 50mg (reliable), 1 day at 200mg (unreliable), filler zero days.
    for i in range(10):
        rows.append(_day(start + timedelta(days=i), mg=50.0, stress=40.0))
    rows.append(_day(start + timedelta(days=10), mg=200.0, stress=70.0))
    for i in range(11, 30):
        rows.append(_day(start + timedelta(days=i), mg=0.0, stress=35.0))
    end = rows[-1].date
    _patch(monkeypatch, rows, substance_cov=(start, end), health_cov=(start, end))

    report = analyze(start, end, substances=("test_substance",), health_signals=("stress_mean",))
    buckets = report.dose_response["test_substance"]
    by_dose = {b.dose_mg: b for b in buckets}
    assert by_dose[50.0].n == 10
    assert by_dose[50.0].reliable is True
    assert by_dose[200.0].n == 1
    assert by_dose[200.0].reliable is False
    assert f"below n={MIN_BUCKET_N}" in report.summary


def test_coverage_clamp_excludes_fabricated_abstinence(monkeypatch):
    """Days past substance coverage are excluded, not treated as 0 mg.

    Substance log ends mid-window; the trailing zero-dose days are absent, NOT
    abstinence. They must not appear as an abstinence period nor enter the
    correlation pairs, and the covered window must reflect the clamp.
    """
    start = date(2024, 1, 1)
    rng = random.Random(99)
    rows = []
    # First 40 days: real substance + health coverage.
    for i in range(40):
        d = start + timedelta(days=i)
        rows.append(_day(d, mg=rng.choice([0.0, 100.0]), stress=rng.uniform(30, 60)))
    # Next 60 days: PAST substance coverage — matrix fabricates 0.0 mg.
    for i in range(40, 100):
        d = start + timedelta(days=i)
        rows.append(_day(d, mg=0.0, stress=rng.uniform(30, 60)))
    end = rows[-1].date
    substance_end = start + timedelta(days=39)
    _patch(
        monkeypatch, rows,
        substance_cov=(start, substance_end),
        health_cov=(start, end),
    )

    report = analyze(start, end, substances=("test_substance",), health_signals=("stress_mean",))

    # Covered window clamped to substance coverage.
    assert report.covered_start == start
    assert report.covered_end == substance_end
    # The 60-day trailing block of absent days must NOT become an abstinence run.
    for a_start, a_end, _days in report.abstinence_periods:
        assert a_end <= substance_end, (a_start, a_end)
    # Correlations only use in-coverage pairs (≤ 40 substance days).
    for c in report.lag_correlations:
        assert c.n <= 40
    assert "coverage" in report.summary.lower()
    assert any("substance: covers" in line for line in report.coverage_provenance)


def test_no_coverage_overlap_yields_no_correlations(monkeypatch):
    """Disjoint substance/health coverage → empty correlations, explicit note."""
    start = date(2024, 1, 1)
    rows = [_day(start + timedelta(days=i), mg=50.0, stress=40.0) for i in range(30)]
    end = rows[-1].date
    # Health coverage entirely after substance coverage: no overlap.
    _patch(
        monkeypatch, rows,
        substance_cov=(start, start + timedelta(days=10)),
        health_cov=(start + timedelta(days=20), end),
    )
    report = analyze(start, end, substances=("test_substance",), health_signals=("stress_mean",))
    assert report.covered_start is None
    assert report.lag_correlations == []
    assert "No substance" in report.summary


def test_summary_frames_association_not_causation(monkeypatch):
    """The summary must carry the association-not-causation caveat inline."""
    start = date(2024, 1, 1)
    rng = random.Random(3)
    rows = [
        _day(start + timedelta(days=i), mg=rng.choice([0.0, 50.0]), stress=rng.uniform(30, 60))
        for i in range(60)
    ]
    end = rows[-1].date
    _patch(monkeypatch, rows, substance_cov=(start, end), health_cov=(start, end))
    report = analyze(start, end, substances=("test_substance",), health_signals=("stress_mean",))
    assert "not causation" in report.summary.lower()
    assert "association" in report.summary.lower()
