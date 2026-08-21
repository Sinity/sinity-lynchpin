"""Tests for core/analytics.py: trend, changepoints, periodicity, correlation, clustering, anomalies."""

import math
import random
from lynchpin.core.analytics import (
    detect_trend, detect_changepoints, detect_periodicity,
    cross_correlate, cluster_days, anomaly_score,
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

    def test_long_daily_series_keeps_detecting_shift(self):
        """Exercise the all-history path used by temporal signal materialization.

        A full substrate refresh supplies thousands of daily observations here.
        The production detector must retain the same shift semantics without
        repeatedly rescanning each candidate segment.
        """
        values = [2.0] * 2_048 + [8.0] * 2_048

        cps = detect_changepoints(values)

        assert any(2_040 <= cp.index <= 2_056 for cp in cps)

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


class TestAutocorrCorrectedPearson:
    """Autocorrelation-aware correlation helpers (validity audit 2026-08-03).

    Anti-vacuity: these exercise the production ``autocorr_corrected_pearson``
    route that lifestyle/mood/substance/weather/daytime correlation modules
    now feed into their FDR passes. Removing the effective-n correction (e.g.
    returning ``n`` from ``effective_sample_size``) fails
    ``test_autocorrelated_noise_gets_larger_p``.
    """

    def test_lag1_autocorrelation_of_persistent_series(self):
        from lynchpin.core.analytics import lag1_autocorrelation

        # AR(1)-style persistent series: high positive lag-1 autocorrelation.
        random.seed(7)
        x = [0.0]
        for _ in range(299):
            x.append(0.9 * x[-1] + random.gauss(0, 1))
        r1 = lag1_autocorrelation(x)
        assert r1 is not None and r1 > 0.7

    def test_lag1_autocorrelation_short_or_constant(self):
        from lynchpin.core.analytics import lag1_autocorrelation

        assert lag1_autocorrelation([1.0, 2.0, 3.0]) is None
        assert lag1_autocorrelation([5.0] * 50) is None

    def test_effective_sample_size_shrinks_only_when_persistent(self):
        from lynchpin.core.analytics import effective_sample_size

        assert effective_sample_size(100, 0.5, 0.5) == 100 * 0.75 / 1.25
        # Unknown or anti-persistent autocorrelation: no correction applied.
        assert effective_sample_size(100, None, 0.5) == 100.0
        assert effective_sample_size(100, -0.4, 0.5) == 100.0
        # Floor keeps df positive.
        assert effective_sample_size(6, 0.99, 0.99) == 5.0

    def test_autocorrelated_noise_gets_larger_p(self):
        from lynchpin.core.analytics import autocorr_corrected_pearson

        random.seed(11)
        # Two independent AR(1) series: any sample correlation is spurious.
        def ar1(n, phi):
            out = [0.0]
            for _ in range(n - 1):
                out.append(phi * out[-1] + random.gauss(0, 1))
            return out

        x = ar1(120, 0.85)
        y = ar1(120, 0.85)
        stat = autocorr_corrected_pearson(x, y)
        assert stat is not None
        assert stat.n == 120
        assert stat.n_eff < 60  # heavy persistence roughly halves the sample
        assert stat.p_value >= stat.p_naive  # correction is conservative

    def test_iid_series_unchanged(self):
        from lynchpin.core.analytics import autocorr_corrected_pearson

        random.seed(3)
        x = [random.gauss(0, 1) for _ in range(150)]
        y = [xi * 0.8 + random.gauss(0, 0.5) for xi in x]
        stat = autocorr_corrected_pearson(x, y)
        assert stat is not None
        # IID noise: measured autocorrelation is near zero, so n_eff ~ n and
        # a genuinely strong relationship stays strongly significant.
        assert stat.n_eff > 100
        assert stat.r > 0.6
        assert stat.p_value < 0.001

    def test_constant_series_returns_none(self):
        from lynchpin.core.analytics import autocorr_corrected_pearson

        assert autocorr_corrected_pearson([1.0] * 30, [2.0] * 30) is None
