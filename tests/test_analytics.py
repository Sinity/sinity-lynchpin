"""Tests for core/analytics.py: trend, changepoints, periodicity, correlation, clustering, anomalies."""

import math
import random
from lynchpin.core.analytics import (
    detect_trend, detect_changepoints, detect_periodicity,
    cross_correlate, cluster_days, anomaly_score,
    TrendResult, ChangePoint, PeriodicComponent, CorrelationResult, DayCluster, AnomalyResult,
)


class TestDetectTrend:
    def test_clear_rising(self):
        values = [float(i) for i in range(20)]
        r = detect_trend(values)
        assert r.direction == "rising"
        assert r.significant
        assert r.slope > 0
        assert r.p_value < 0.01

    def test_clear_falling(self):
        values = [20.0 - i for i in range(20)]
        r = detect_trend(values)
        assert r.direction == "falling"
        assert r.significant
        assert r.slope < 0

    def test_stable_noisy(self):
        random.seed(42)
        values = [5.0 + random.gauss(0, 1) for _ in range(30)]
        r = detect_trend(values)
        assert r.direction == "stable"
        assert not r.significant

    def test_too_few_samples(self):
        r = detect_trend([1.0, 2.0, 3.0])
        assert r.direction == "stable"
        assert not r.significant

    def test_constant(self):
        r = detect_trend([5.0] * 20)
        assert r.direction == "stable"

    def test_slope_magnitude(self):
        values = [i * 2.0 for i in range(15)]
        r = detect_trend(values)
        assert 1.5 < r.slope < 2.5  # Sen's slope ≈ 2.0


class TestDetectChangepoints:
    def test_single_shift(self):
        values = [5.0] * 20 + [10.0] * 20
        cps = detect_changepoints(values)
        assert len(cps) >= 1
        # The changepoint should be near index 20
        assert any(18 <= cp.index <= 22 for cp in cps)

    def test_no_change(self):
        values = [5.0] * 30
        cps = detect_changepoints(values)
        assert len(cps) == 0

    def test_multiple_shifts(self):
        values = [5.0] * 15 + [10.0] * 15 + [3.0] * 15
        cps = detect_changepoints(values)
        assert len(cps) >= 2

    def test_too_short(self):
        assert detect_changepoints([1, 2, 3]) == []


class TestDetectPeriodicity:
    def test_weekly_cycle(self):
        weekly = [10 + 5 * math.sin(2 * math.pi * i / 7) for i in range(56)]
        components = detect_periodicity(weekly)
        assert len(components) > 0
        # Should detect ~7-day period
        assert any(6.5 <= c.period <= 7.5 for c in components)

    def test_no_cycle(self):
        random.seed(42)
        noise = [random.gauss(0, 1) for _ in range(50)]
        components = detect_periodicity(noise)
        # Should find nothing significant (or very weak)
        strong = [c for c in components if c.power > 10]
        assert len(strong) == 0

    def test_too_short(self):
        assert detect_periodicity([1, 2, 3]) == []


class TestCrossCorrelate:
    def test_perfect_sync(self):
        a = list(range(20))
        b = list(range(20))
        corrs = cross_correlate(a, b, max_lag=2)
        lag0 = [c for c in corrs if c.lag == 0]
        assert len(lag0) == 1
        assert lag0[0].r > 0.99

    def test_lagged(self):
        a = list(range(20))
        b = [0, 0] + list(range(18))  # b lags a by 2
        corrs = cross_correlate(a, b, max_lag=3)
        best = max(corrs, key=lambda c: abs(c.r))
        assert best.lag == 2

    def test_too_short(self):
        assert cross_correlate([1, 2], [3, 4]) == []

    def test_uncorrelated(self):
        random.seed(42)
        a = [random.gauss(0, 1) for _ in range(30)]
        b = [random.gauss(0, 1) for _ in range(30)]
        corrs = cross_correlate(a, b, max_lag=2)
        significant = [c for c in corrs if c.significant]
        # Unlikely to find significant correlation in random data
        assert len(significant) <= 1


class TestClusterDays:
    def test_two_clusters(self):
        features = [
            {"focus": 8, "commits": 10},
            {"focus": 7, "commits": 12},
            {"focus": 9, "commits": 11},
            {"focus": 2, "commits": 1},
            {"focus": 3, "commits": 0},
            {"focus": 1, "commits": 2},
        ]
        clusters = cluster_days(features, k=2)
        assert len(clusters) == 2
        assert sum(c.size for c in clusters) == 6

    def test_auto_k(self):
        features = [
            {"a": float(i % 3), "b": float(i // 3)}
            for i in range(12)
        ]
        clusters = cluster_days(features)
        assert len(clusters) >= 2

    def test_too_few(self):
        assert cluster_days([{"a": 1}]) == []


class TestAnomalyScore:
    def test_normal_value(self):
        history = [5.0, 5.1, 4.9, 5.2, 5.0, 4.8, 5.1, 5.0]
        r = anomaly_score(5.0, history)
        assert not r.is_anomaly
        assert r.direction == "normal"

    def test_high_outlier(self):
        history = [5.0, 5.1, 4.9, 5.2, 5.0, 4.8, 5.1, 5.0]
        r = anomaly_score(15.0, history)
        assert r.is_anomaly
        assert r.direction == "high"

    def test_low_outlier(self):
        history = [5.0, 5.1, 4.9, 5.2, 5.0, 4.8, 5.1, 5.0]
        r = anomaly_score(-5.0, history)
        assert r.is_anomaly
        assert r.direction == "low"

    def test_mad_method(self):
        history = [5.0, 5.1, 4.9, 5.2, 5.0, 4.8, 5.1, 5.0]
        r = anomaly_score(15.0, history, method="mad")
        assert r.is_anomaly

    def test_too_few(self):
        r = anomaly_score(5.0, [1.0, 2.0])
        assert not r.is_anomaly
