"""Statistical analytics: trend detection, change points, periodicity, correlation, clustering, anomalies.

All functions use pure Python + math stdlib. No heavy dependencies.
Data volumes (100-5000 daily observations) are ideal for these methods.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import date
from itertools import combinations
from typing import Optional, Sequence


# ══════════════════════════════════════════════════════════════════════════════
# Result types
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class TrendResult:
    direction: str     # "rising" | "falling" | "stable"
    slope: float       # Sen's slope (units per time step)
    p_value: float     # Mann-Kendall p-value
    significant: bool  # p < 0.05
    n: int


@dataclass(frozen=True)
class ChangePoint:
    index: int
    before_mean: float
    after_mean: float
    magnitude: float   # (after - before) / before if before != 0
    cost_reduction: float


@dataclass(frozen=True)
class PeriodicComponent:
    period: float      # in samples (e.g., 7.0 = weekly if daily data)
    amplitude: float   # relative to mean
    power: float       # spectral power (higher = stronger signal)
    label: str         # human-readable: "weekly", "biweekly", etc.


@dataclass(frozen=True)
class CorrelationResult:
    lag: int
    r: float           # Pearson correlation coefficient
    p_value: float
    significant: bool  # p < 0.05
    n: int


@dataclass(frozen=True)
class AnomalyResult:
    value: float
    score: float       # how anomalous (0 = normal, higher = more anomalous)
    threshold: float   # the threshold used
    is_anomaly: bool
    direction: str     # "high" | "low" | "normal"


@dataclass(frozen=True)
class DayCluster:
    cluster_id: int
    label: str
    size: int
    centroid: dict[str, float]
    members: list[int] = field(default_factory=list)  # indices


# ══════════════════════════════════════════════════════════════════════════════
# Trend detection: Mann-Kendall test + Sen's slope
# ══════════════════════════════════════════════════════════════════════════════


def detect_trend(values: Sequence[float], *, min_samples: int = 7) -> TrendResult:
    """Mann-Kendall trend test with Sen's slope estimator.

    Robust, non-parametric. Works well with noisy, non-normal daily data.
    Returns significance via p-value from normal approximation of S statistic.
    """
    n = len(values)
    if n < min_samples:
        return TrendResult("stable", 0.0, 1.0, False, n)

    # Mann-Kendall S statistic
    s = 0
    for i in range(n - 1):
        for j in range(i + 1, n):
            diff = values[j] - values[i]
            if diff > 0: s += 1
            elif diff < 0: s -= 1

    # Variance of S (with tie correction)
    unique_vals = {}
    for v in values:
        unique_vals[v] = unique_vals.get(v, 0) + 1
    tie_correction = sum(t * (t - 1) * (2 * t + 5) for t in unique_vals.values() if t > 1)
    var_s = (n * (n - 1) * (2 * n + 5) - tie_correction) / 18

    # Normal approximation for p-value
    if var_s == 0:
        return TrendResult("stable", 0.0, 1.0, False, n)
    std_s = math.sqrt(var_s)
    if s > 0:
        z = (s - 1) / std_s
    elif s < 0:
        z = (s + 1) / std_s
    else:
        z = 0.0
    p_value = 2 * (1 - _norm_cdf(abs(z)))

    # Sen's slope: median of all pairwise slopes
    slopes = []
    for i in range(n):
        for j in range(i + 1, n):
            if j != i:
                slopes.append((values[j] - values[i]) / (j - i))
    slope = statistics.median(slopes) if slopes else 0.0

    direction = "rising" if s > 0 and p_value < 0.05 else "falling" if s < 0 and p_value < 0.05 else "stable"
    return TrendResult(direction=direction, slope=slope, p_value=p_value, significant=p_value < 0.05, n=n)


# ══════════════════════════════════════════════════════════════════════════════
# Change point detection: simplified PELT via binary segmentation
# ══════════════════════════════════════════════════════════════════════════════


def detect_changepoints(
    values: Sequence[float], *, min_segment: int = 5, max_changepoints: int = 5, penalty: float | None = None
) -> list[ChangePoint]:
    """Binary segmentation change point detection.

    Recursively splits the series at the point that maximally reduces
    total squared-error cost. Stops when penalty exceeds improvement.
    """
    n = len(values)
    if n < 2 * min_segment:
        return []

    if penalty is None:
        # BIC-inspired penalty: log(n) * variance
        var = statistics.variance(values) if n > 1 else 1.0
        penalty = math.log(n) * var if var > 0 else 1.0

    points: list[int] = []
    _binary_segment(list(values), 0, n, min_segment, penalty, max_changepoints, points)
    points.sort()

    result: list[ChangePoint] = []
    for idx in points:
        before = values[:idx]
        after = values[idx:]
        bm = statistics.mean(before) if before else 0
        am = statistics.mean(after) if after else 0
        mag = (am - bm) / abs(bm) if abs(bm) > 1e-9 else 0
        result.append(ChangePoint(index=idx, before_mean=round(bm, 3), after_mean=round(am, 3),
                                  magnitude=round(mag, 3), cost_reduction=0))
    return result


def _binary_segment(values: list[float], start: int, end: int, min_seg: int,
                    penalty: float, max_cp: int, points: list[int]) -> None:
    if end - start < 2 * min_seg or len(points) >= max_cp:
        return
    best_idx = -1
    best_gain = 0.0
    total_cost = _segment_cost(values, start, end)

    for i in range(start + min_seg, end - min_seg + 1):
        left_cost = _segment_cost(values, start, i)
        right_cost = _segment_cost(values, i, end)
        gain = total_cost - left_cost - right_cost
        if gain > best_gain:
            best_gain = gain
            best_idx = i

    if best_idx >= 0 and best_gain > penalty:
        points.append(best_idx)
        _binary_segment(values, start, best_idx, min_seg, penalty, max_cp, points)
        _binary_segment(values, best_idx, end, min_seg, penalty, max_cp, points)


def _segment_cost(values: list[float], start: int, end: int) -> float:
    if end <= start:
        return 0.0
    seg = values[start:end]
    if len(seg) < 2:
        return 0.0
    mean = statistics.mean(seg)
    return sum((v - mean) ** 2 for v in seg)


# ══════════════════════════════════════════════════════════════════════════════
# Periodicity detection: FFT-based
# ══════════════════════════════════════════════════════════════════════════════


def detect_periodicity(values: Sequence[float], *, min_period: float = 2, max_period: float | None = None) -> list[PeriodicComponent]:
    """Detect periodic components via discrete Fourier transform.

    Returns significant periodic components sorted by power.
    Uses pure Python DFT (no numpy required) — fast enough for <2000 samples.
    """
    n = len(values)
    if n < 8:
        return []
    if max_period is None:
        max_period = n / 2

    mean = statistics.mean(values)
    centered = [v - mean for v in values]

    # DFT magnitudes for frequencies of interest
    components: list[PeriodicComponent] = []
    for k in range(1, n // 2):
        period = n / k
        if period < min_period or period > max_period:
            continue
        # DFT at frequency k
        real = sum(centered[j] * math.cos(2 * math.pi * k * j / n) for j in range(n))
        imag = sum(centered[j] * math.sin(2 * math.pi * k * j / n) for j in range(n))
        power = (real ** 2 + imag ** 2) / n
        amplitude = 2 * math.sqrt(power) / n

        # Significance: power should be notably above noise floor
        if power > 0:
            components.append(PeriodicComponent(
                period=round(period, 2), amplitude=round(amplitude, 4),
                power=round(power, 4), label=_period_label(period),
            ))

    # Sort by power, take top components above noise
    components.sort(key=lambda c: -c.power)
    if not components:
        return []

    # Filter: keep only those with power > 2× median power (simple significance)
    median_power = statistics.median(c.power for c in components) if len(components) > 3 else 0
    threshold = max(median_power * 2, components[0].power * 0.1)
    return [c for c in components if c.power >= threshold][:5]


def _period_label(period: float) -> str:
    if 6.5 <= period <= 7.5: return "weekly"
    if 13 <= period <= 15: return "biweekly"
    if 28 <= period <= 32: return "monthly"
    if 88 <= period <= 95: return "quarterly"
    return f"~{period:.0f}-day"


# ══════════════════════════════════════════════════════════════════════════════
# Cross-correlation with significance
# ══════════════════════════════════════════════════════════════════════════════


def cross_correlate(
    a: Sequence[float], b: Sequence[float], *, max_lag: int = 3
) -> list[CorrelationResult]:
    """Lagged Pearson cross-correlation with t-test significance.

    Positive lag means b is shifted forward (a leads b).
    e.g., lag=1: does today's a predict tomorrow's b?
    """
    results: list[CorrelationResult] = []
    n = min(len(a), len(b))
    if n < 5:
        return results

    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            x = list(a[:n - lag])
            y = list(b[lag:n])
        else:
            x = list(a[-lag:n])
            y = list(b[:n + lag])
        m = len(x)
        if m < 5:
            continue
        r = _pearson_r(x, y)
        if r is None:
            continue
        # t-test for significance
        if abs(r) >= 1.0:
            p = 0.0
        else:
            t_stat = r * math.sqrt((m - 2) / (1 - r ** 2))
            p = _t_test_p(t_stat, m - 2)
        results.append(CorrelationResult(lag=lag, r=round(r, 4), p_value=round(p, 4),
                                         significant=p < 0.05, n=m))

    results.sort(key=lambda c: c.p_value)
    return results


def _pearson_r(x: list[float], y: list[float]) -> float | None:
    n = len(x)
    if n < 3:
        return None
    mx, my = statistics.mean(x), statistics.mean(y)
    sx = math.sqrt(sum((xi - mx) ** 2 for xi in x))
    sy = math.sqrt(sum((yi - my) ** 2 for yi in y))
    if sx == 0 or sy == 0:
        return None
    return sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / (sx * sy)


# ══════════════════════════════════════════════════════════════════════════════
# Day clustering: K-means
# ══════════════════════════════════════════════════════════════════════════════


def cluster_days(
    features: Sequence[dict[str, float]], *, k: int | None = None, max_k: int = 5
) -> list[DayCluster]:
    """K-means clustering of feature vectors.

    Auto-selects k via silhouette score if not specified.
    Features should be pre-normalized (or at similar scales).
    """
    n = len(features)
    if n < 4:
        return []

    # Extract consistent feature names
    all_keys = sorted({key for f in features for key in f})
    matrix = [[f.get(k, 0.0) for k in all_keys] for f in features]

    # Normalize each feature to [0, 1]
    for col in range(len(all_keys)):
        vals = [row[col] for row in matrix]
        lo, hi = min(vals), max(vals)
        rng = hi - lo
        if rng > 0:
            for row in matrix:
                row[col] = (row[col] - lo) / rng

    # Auto-select k
    if k is None:
        best_k, best_score = 2, -1.0
        for trial_k in range(2, min(max_k + 1, n // 2 + 1)):
            labels = _kmeans(matrix, trial_k)
            score = _silhouette(matrix, labels)
            if score > best_score:
                best_score = score
                best_k = trial_k
        k = best_k

    labels = _kmeans(matrix, k)

    # Build clusters
    clusters: dict[int, list[int]] = {}
    for i, label in enumerate(labels):
        clusters.setdefault(label, []).append(i)

    result: list[DayCluster] = []
    for cid, members in sorted(clusters.items()):
        centroid = {}
        for col, key in enumerate(all_keys):
            centroid[key] = round(statistics.mean(matrix[m][col] for m in members), 3)
        # Auto-label from top feature
        top_feature = max(centroid, key=centroid.get) if centroid else "unknown"
        result.append(DayCluster(
            cluster_id=cid, label=f"cluster_{cid}_{top_feature}",
            size=len(members), centroid=centroid, members=members,
        ))
    return result


def _kmeans(matrix: list[list[float]], k: int, max_iter: int = 50) -> list[int]:
    import random
    n = len(matrix)
    dim = len(matrix[0]) if matrix else 0
    # Initialize centroids via k-means++
    centroids = [matrix[random.randint(0, n - 1)][:]]
    for _ in range(1, k):
        dists = [min(_dist(row, c) for c in centroids) for row in matrix]
        total = sum(dists)
        if total == 0:
            centroids.append(matrix[random.randint(0, n - 1)][:])
            continue
        r = random.random() * total
        cumsum = 0
        for i, d in enumerate(dists):
            cumsum += d
            if cumsum >= r:
                centroids.append(matrix[i][:])
                break

    labels = [0] * n
    for _ in range(max_iter):
        # Assign
        changed = False
        for i, row in enumerate(matrix):
            best = min(range(k), key=lambda c: _dist(row, centroids[c]))
            if best != labels[i]:
                changed = True
                labels[i] = best
        if not changed:
            break
        # Update centroids
        for c in range(k):
            members = [matrix[i] for i in range(n) if labels[i] == c]
            if members:
                centroids[c] = [statistics.mean(members[j][d] for j in range(len(members))) for d in range(dim)]
    return labels


def _dist(a: list[float], b: list[float]) -> float:
    return sum((ai - bi) ** 2 for ai, bi in zip(a, b))


def _silhouette(matrix: list[list[float]], labels: list[int]) -> float:
    n = len(matrix)
    if n < 3:
        return 0.0
    clusters = set(labels)
    if len(clusters) < 2:
        return 0.0
    scores = []
    for i in range(n):
        same = [j for j in range(n) if labels[j] == labels[i] and j != i]
        if not same:
            scores.append(0.0)
            continue
        a_i = sum(_dist(matrix[i], matrix[j]) for j in same) / len(same)
        b_i = float('inf')
        for c in clusters:
            if c == labels[i]:
                continue
            others = [j for j in range(n) if labels[j] == c]
            if others:
                avg = sum(_dist(matrix[i], matrix[j]) for j in others) / len(others)
                b_i = min(b_i, avg)
        scores.append((b_i - a_i) / max(a_i, b_i) if max(a_i, b_i) > 0 else 0)
    return statistics.mean(scores)


# ══════════════════════════════════════════════════════════════════════════════
# Anomaly scoring: IQR and MAD methods
# ══════════════════════════════════════════════════════════════════════════════


def anomaly_score(value: float, history: Sequence[float], *, method: str = "iqr") -> AnomalyResult:
    """Robust anomaly detection. IQR is better than z-score for skewed data."""
    if len(history) < 5:
        return AnomalyResult(value=value, score=0, threshold=0, is_anomaly=False, direction="normal")

    if method == "mad":
        return _mad_anomaly(value, history)
    return _iqr_anomaly(value, history)


def _iqr_anomaly(value: float, history: Sequence[float]) -> AnomalyResult:
    sorted_h = sorted(history)
    n = len(sorted_h)
    q1 = sorted_h[n // 4]
    q3 = sorted_h[3 * n // 4]
    iqr = q3 - q1
    if iqr == 0:
        return AnomalyResult(value=value, score=0, threshold=0, is_anomaly=False, direction="normal")
    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr
    if value > upper:
        score = (value - upper) / iqr
        return AnomalyResult(value=value, score=round(score, 3), threshold=round(upper, 3),
                             is_anomaly=True, direction="high")
    if value < lower:
        score = (lower - value) / iqr
        return AnomalyResult(value=value, score=round(score, 3), threshold=round(lower, 3),
                             is_anomaly=True, direction="low")
    return AnomalyResult(value=value, score=0, threshold=0, is_anomaly=False, direction="normal")


def _mad_anomaly(value: float, history: Sequence[float]) -> AnomalyResult:
    med = statistics.median(history)
    mad = statistics.median(abs(v - med) for v in history)
    if mad == 0:
        return AnomalyResult(value=value, score=0, threshold=0, is_anomaly=False, direction="normal")
    # Modified z-score using MAD
    modified_z = 0.6745 * (value - med) / mad
    threshold = 3.5  # standard MAD threshold
    is_anomaly = abs(modified_z) > threshold
    direction = "high" if modified_z > threshold else "low" if modified_z < -threshold else "normal"
    return AnomalyResult(value=value, score=round(abs(modified_z), 3), threshold=threshold,
                         is_anomaly=is_anomaly, direction=direction)


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════


def _norm_cdf(x: float) -> float:
    """Standard normal CDF approximation (Abramowitz & Stegun)."""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _t_test_p(t_stat: float, df: int) -> float:
    """Approximate two-tailed p-value for t-distribution using normal approximation.

    Accurate for df > 30. For smaller df, slightly conservative.
    """
    if df <= 0:
        return 1.0
    # For large df, t → normal
    if df > 30:
        return 2 * (1 - _norm_cdf(abs(t_stat)))
    # Simple approximation for small df
    x = df / (df + t_stat ** 2)
    # Regularized incomplete beta approximation (crude but functional)
    p = 1 - (1 - x ** (df / 2)) ** 0.5
    return min(max(p, 0.0), 1.0)
