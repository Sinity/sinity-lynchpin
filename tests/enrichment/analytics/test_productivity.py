from lynchpin.enrichment.analytics.productivity import productivity_model, optimal_work_windows

def test_productivity_model_no_data():
    result = productivity_model(["a", "b"], [[None, None]], [0.0])
    assert len(result.feature_importance) == 2
    assert result.feature_importance[0][1] == 0.0  # zero correlation

def test_optimal_work_windows_empty():
    result = optimal_work_windows([0.0] * 24)
    assert result.amplitude >= 0
    assert result.r_squared >= 0

def test_optimal_work_windows_clear_peak():
    # Peak at hour 10 (10 AM) — cosinor shape
    import math
    profile = [max(0, math.cos(2 * math.pi * (h - 10) / 24)) for h in range(24)]
    result = optimal_work_windows(profile)
    assert 8 <= result.acrophase_hour <= 12  # peak in morning
    assert result.r_squared > 0.5
