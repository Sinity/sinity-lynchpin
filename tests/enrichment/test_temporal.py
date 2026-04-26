from lynchpin.enrichment.temporal import (
    decompose_stl, bootstrap_ci, granger_causality,
    rolling_correlation, period_compare,
)

def test_bootstrap_ci_basic():
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    est, lo, hi = bootstrap_ci(values, lambda vs: sum(vs)/len(vs))
    assert 4.0 < est < 7.0
    assert lo < est < hi

def test_bootstrap_ci_reproducible():
    values = list(range(20))
    est1, lo1, hi1 = bootstrap_ci(values, lambda vs: sum(vs)/len(vs), random_state=42)
    est2, lo2, hi2 = bootstrap_ci(values, lambda vs: sum(vs)/len(vs), random_state=42)
    assert est1 == est2 and lo1 == lo2 and hi1 == hi2

def test_period_compare():
    before = [1.0, 2.0, 3.0, 4.0, 5.0]
    after = [6.0, 7.0, 8.0, 9.0, 10.0]
    result = period_compare(before, after)
    assert result.d_mean > 0  # after is higher
    assert result.p_value < 0.05
    assert result.cohens_d > 1.0

def test_decompose_stl_constant():
    values = [5.0] * 30
    result = decompose_stl(values, period=7)
    assert abs(result.seasonal_strength) < 0.2

def test_granger_no_causality():
    import random
    random.seed(42)
    a = [random.gauss(0, 1) for _ in range(100)]
    b = [random.gauss(0, 1) for _ in range(100)]
    result = granger_causality(a, b, max_lag=3)
    # Random noise should not show significant Granger causality
    assert not result.significant or result.p_value > 0.01

def test_rolling_correlation():
    a = list(range(50))
    b = [x * 2 + 5 for x in a]
    result = rolling_correlation(a, b, window=10)
    assert all(r > 0.99 for r in result.r_values)
