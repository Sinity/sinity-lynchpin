"""Tests for sleep-architecture × next-day productivity correlation analysis.

Pins four correctness contracts:

1. Day-offset join: sleep night d → productivity day d+1 (correct offset).
2. None-exclusion: AW days with None outcomes never enter the correlation.
3. Correlation sign: synthetic data with a known positive deep_pct → deep-work
   relationship must yield r > 0.
4. Coverage clamping: days outside sleep or AW coverage produce no correlations.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

import lynchpin.analysis.sleep_architecture_productivity as sap
from lynchpin.analysis.sleep_architecture_productivity import (
    MIN_PAIRS,
    analyze,
)
from lynchpin.sources.sleep import SleepArchitecture


# ---------------------------------------------------------------------------
# Synthetic data factories
# ---------------------------------------------------------------------------


def _arch(d: date, *, deep_pct: float = 20.0, rem_pct: float = 20.0,
          total_min: float = 420.0, first_rem_min: Optional[float] = 90.0) -> SleepArchitecture:
    """Build a minimal SleepArchitecture for date *d*."""
    awake_pct = 5.0
    light_pct = 100.0 - awake_pct - deep_pct - rem_pct
    awake_min = total_min * awake_pct / 100
    deep_min = total_min * deep_pct / 100
    rem_min = total_min * rem_pct / 100
    light_min = total_min * light_pct / 100
    return SleepArchitecture(
        date=d,
        sleep_id=f"sleep-{d.isoformat()}",
        total_min=total_min,
        awake_min=awake_min,
        light_min=light_min,
        deep_min=deep_min,
        rem_min=rem_min,
        awake_pct=awake_pct,
        light_pct=light_pct,
        deep_pct=deep_pct,
        rem_pct=rem_pct,
        stage_transitions=10,
        first_rem_min=first_rem_min,
    )


class _FakeOperatorDay:
    """Minimal OperatorDay stand-in."""
    def __init__(
        self,
        d: date,
        *,
        aw_deep_work_min: Optional[float] = None,
        aw_fragmentation: Optional[float] = None,
        aw_active_hours: Optional[float] = None,
    ) -> None:
        self.date = d
        self.aw_deep_work_min = aw_deep_work_min
        self.aw_fragmentation = aw_fragmentation
        self.aw_active_hours = aw_active_hours


def _patch(
    monkeypatch,
    arch_rows: list[SleepArchitecture],
    prod_rows: list[_FakeOperatorDay],
) -> None:
    """Monkeypatch sleep_architecture and operator_daily_matrix."""
    monkeypatch.setattr(sap, "sleep_architecture", lambda **_kw: arch_rows)

    # operator_daily_matrix is imported inside analyze() from .operator_daily.
    # Patch it on the operator_daily module so the late import resolves correctly.
    import lynchpin.analysis.operator_daily as op_mod
    monkeypatch.setattr(op_mod, "operator_daily_matrix", lambda *a, **kw: prod_rows)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_day_offset_join_is_correct(monkeypatch):
    """Sleep night d must correlate with productivity day d+1, not same day.

    We put a strong deep_pct signal on nights and a matching high deep-work
    signal on the NEXT day only.  A wrong offset (lag 0) would produce noise.
    """
    start = date(2024, 1, 1)
    n = 50
    arch_rows = []
    prod_rows = []

    for i in range(n):
        d = start + timedelta(days=i)
        # Alternate deep_pct: even nights = high (30%), odd = low (10%)
        deep = 30.0 if i % 2 == 0 else 10.0
        arch_rows.append(_arch(d, deep_pct=deep))

        # Next day: match the night's deep_pct (high deep → high deep-work)
        next_d = d + timedelta(days=1)
        dw = 180.0 if deep == 30.0 else 30.0
        prod_rows.append(_FakeOperatorDay(next_d, aw_deep_work_min=dw, aw_active_hours=8.0))

    _patch(monkeypatch, arch_rows, prod_rows)
    report = analyze(start=start, end=start + timedelta(days=n - 1))

    assert report.covered_start is not None, "Should have coverage"
    deep_dw = [c for c in report.correlations if c.predictor == "deep_pct" and c.outcome == "aw_deep_work_min"]
    assert deep_dw, "Should compute deep_pct → aw_deep_work_min"
    assert deep_dw[0].r > 0.8, f"Expected strong positive r, got {deep_dw[0].r}"
    assert deep_dw[0].n >= MIN_PAIRS, f"Expected ≥{MIN_PAIRS} pairs, got {deep_dw[0].n}"


def test_none_aw_days_excluded_from_correlation(monkeypatch):
    """AW days with None outcome must be excluded; n must reflect only paired rows.

    We build 60 sleep nights, of which only 20 have non-None aw_deep_work_min.
    The correlation n must be ≤ 20, not 60.
    """
    start = date(2024, 1, 1)
    arch_rows = []
    prod_rows = []
    REAL = 30  # nights where next-day AW is non-None
    TOTAL = 60

    for i in range(TOTAL):
        d = start + timedelta(days=i)
        arch_rows.append(_arch(d, deep_pct=20.0 + (i % 10)))

        next_d = d + timedelta(days=1)
        if i < REAL:
            prod_rows.append(_FakeOperatorDay(next_d, aw_deep_work_min=float(60 + i), aw_active_hours=6.0))
        else:
            # None outcome — must NOT enter the correlation
            prod_rows.append(_FakeOperatorDay(next_d, aw_deep_work_min=None, aw_active_hours=None))

    _patch(monkeypatch, arch_rows, prod_rows)
    report = analyze(start=start, end=start + timedelta(days=TOTAL - 1))

    for c in report.correlations:
        if c.outcome == "aw_deep_work_min":
            assert c.n <= REAL, (
                f"n={c.n} exceeds the {REAL} non-None AW days; "
                "None days were wrongly coerced to 0"
            )


def test_correlation_sign_with_known_positive_relationship(monkeypatch):
    """deep_pct → next-day deep-work must yield positive r when relationship is direct."""
    import random
    rng = random.Random(42)
    start = date(2024, 1, 1)
    n = 80
    arch_rows = []
    prod_rows = []

    for i in range(n):
        d = start + timedelta(days=i)
        deep = 10.0 + rng.uniform(0, 20)
        arch_rows.append(_arch(d, deep_pct=deep))
        # Next day deep-work linearly tracks deep_pct + noise
        dw = 5.0 * deep + rng.uniform(-10, 10)
        prod_rows.append(_FakeOperatorDay(d + timedelta(days=1), aw_deep_work_min=max(0.0, dw), aw_active_hours=7.0))

    _patch(monkeypatch, arch_rows, prod_rows)
    report = analyze(start=start, end=start + timedelta(days=n - 1))

    deep_dw = [c for c in report.correlations if c.predictor == "deep_pct" and c.outcome == "aw_deep_work_min"]
    assert deep_dw, "Expected deep_pct → aw_deep_work_min correlation"
    assert deep_dw[0].r > 0.0, f"Expected positive r, got {deep_dw[0].r}"


def test_rem_pct_negative_with_fragmentation(monkeypatch):
    """rem_pct → next-day fragmentation should be negative when more REM means less fragmentation."""
    import random
    rng = random.Random(99)
    start = date(2024, 3, 1)
    n = 80
    arch_rows = []
    prod_rows = []

    for i in range(n):
        d = start + timedelta(days=i)
        rem = 15.0 + rng.uniform(0, 20)
        arch_rows.append(_arch(d, rem_pct=rem))
        # More REM → less fragmentation (negative relationship)
        frag = 1.5 - 0.04 * rem + rng.uniform(-0.1, 0.1)
        prod_rows.append(_FakeOperatorDay(d + timedelta(days=1), aw_fragmentation=max(0.0, frag), aw_active_hours=7.0))

    _patch(monkeypatch, arch_rows, prod_rows)
    report = analyze(start=start, end=start + timedelta(days=n - 1))

    rem_frag = [c for c in report.correlations if c.predictor == "rem_pct" and c.outcome == "aw_fragmentation"]
    assert rem_frag, "Expected rem_pct → aw_fragmentation correlation"
    assert rem_frag[0].r < 0.0, f"Expected negative r for rem→fragmentation, got {rem_frag[0].r}"


def test_no_sleep_coverage_yields_empty_correlations(monkeypatch):
    """Empty sleep architecture → no correlations, covered_start is None."""
    start = date(2024, 1, 1)
    end = date(2024, 3, 31)

    _patch(monkeypatch, [], [])
    report = analyze(start=start, end=end)

    assert report.covered_start is None
    assert report.correlations == []
    assert report.n_tests == 0


def test_no_aw_coverage_yields_empty_correlations(monkeypatch):
    """Sleep architecture present but zero AW data → no correlations."""
    start = date(2024, 1, 1)
    n = 40
    arch_rows = [_arch(start + timedelta(days=i)) for i in range(n)]
    # All AW days are None
    prod_rows = [
        _FakeOperatorDay(start + timedelta(days=i + 1), aw_deep_work_min=None, aw_active_hours=None)
        for i in range(n)
    ]

    _patch(monkeypatch, arch_rows, prod_rows)
    report = analyze(start=start, end=start + timedelta(days=n - 1))

    # No AW data → no coverage → no correlations
    assert report.covered_start is None or report.correlations == []


def test_coverage_clamping_excludes_out_of_range(monkeypatch):
    """Sleep nights past AW coverage must not contribute correlation pairs.

    We have 60 sleep nights but AW only covers the first 30.  The correlation
    n must not exceed 30.
    """
    start = date(2024, 1, 1)
    TOTAL = 60
    AW_COVERED = 30

    import random
    rng = random.Random(7)

    arch_rows = []
    prod_rows = []

    for i in range(TOTAL):
        d = start + timedelta(days=i)
        deep = 15.0 + rng.uniform(0, 15)
        arch_rows.append(_arch(d, deep_pct=deep))

        next_d = d + timedelta(days=1)
        if i < AW_COVERED:
            dw = 3.0 * deep + rng.uniform(-5, 5)
            prod_rows.append(_FakeOperatorDay(next_d, aw_deep_work_min=max(0.0, dw), aw_active_hours=7.0))
        else:
            # Out-of-AW-coverage; these should be excluded by coverage gating
            prod_rows.append(_FakeOperatorDay(next_d, aw_deep_work_min=None, aw_active_hours=None))

    _patch(monkeypatch, arch_rows, prod_rows)
    report = analyze(start=start, end=start + timedelta(days=TOTAL - 1))

    for c in report.correlations:
        assert c.n <= AW_COVERED, (
            f"{c.label}: n={c.n} exceeds AW-covered days {AW_COVERED}; "
            "out-of-coverage days leaked into correlations"
        )


def test_n_carried_per_correlation(monkeypatch):
    """Every correlation must carry n ≥ MIN_PAIRS (per-correlation, not global)."""
    start = date(2024, 1, 1)
    n = 60
    arch_rows = [_arch(start + timedelta(days=i), deep_pct=15.0 + i % 10) for i in range(n)]
    prod_rows = [
        _FakeOperatorDay(
            start + timedelta(days=i + 1),
            aw_deep_work_min=float(60 + i),
            aw_fragmentation=0.5 + 0.01 * i,
            aw_active_hours=6.0 + 0.05 * i,
        )
        for i in range(n)
    ]

    _patch(monkeypatch, arch_rows, prod_rows)
    report = analyze(start=start, end=start + timedelta(days=n - 1))

    for c in report.correlations:
        assert c.n >= MIN_PAIRS, f"{c.label} has n={c.n} < MIN_PAIRS={MIN_PAIRS}"
        assert 0.0 <= c.p_value <= 1.0
        assert 0.0 <= c.q_value <= 1.0
        assert c.significant == (c.q_value < sap.FDR_TARGET)


def test_summary_contains_association_caveat(monkeypatch):
    """The summary must frame results as association, not causation."""
    start = date(2024, 1, 1)
    n = 60
    arch_rows = [_arch(start + timedelta(days=i)) for i in range(n)]
    prod_rows = [
        _FakeOperatorDay(start + timedelta(days=i + 1), aw_deep_work_min=float(60 + i), aw_active_hours=7.0)
        for i in range(n)
    ]

    _patch(monkeypatch, arch_rows, prod_rows)
    report = analyze(start=start, end=start + timedelta(days=n - 1))

    assert "association" in report.summary.lower()
    assert "not causal" in report.summary.lower() or "not causation" in report.summary.lower() or "causal" not in report.summary.lower()
    # More specifically — our summary uses "not causal effects"
    assert "causal" in report.summary.lower()


def test_efficiency_derivation(monkeypatch):
    """Derived sleep efficiency should be (total - awake) / total × 100."""
    from lynchpin.analysis.sleep_architecture_productivity import _efficiency

    arch = _arch(date(2024, 1, 1), deep_pct=20.0, rem_pct=20.0, total_min=480.0)
    eff = _efficiency(arch)
    assert eff is not None
    expected = (arch.total_min - arch.awake_min) / arch.total_min * 100
    assert abs(eff - expected) < 0.01, f"Expected {expected:.2f}, got {eff}"
    assert 0.0 <= eff <= 100.0
