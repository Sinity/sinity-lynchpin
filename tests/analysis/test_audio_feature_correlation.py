from datetime import date, timedelta
from types import SimpleNamespace


def test_audio_feature_deep_work_correlation_planted_signal(monkeypatch):
    import lynchpin.analysis.lifestyle_correlations as lc
    from lynchpin.analysis.operator_daily import OperatorDay

    base = date(2026, 1, 1)
    days = [base + timedelta(days=i) for i in range(30)]

    def means(i):
        return {
            "energy": float((i % 5) / 4.0),       # tracks deep-work exactly
            "valence": float((i * 3) % 5) / 4.0,  # unrelated
            "danceability": 0.5, "tempo": 120.0, "acousticness": 0.1,
            "instrumentalness": 0.0, "speechiness": 0.05, "liveness": 0.1, "loudness": -6.0,
        }

    feat_days = [SimpleNamespace(date=d, means=means(i)) for i, d in enumerate(days)]
    monkeypatch.setattr(
        "lynchpin.sources.audio_features.daily_audio_features",
        lambda start, end, path=None: feat_days,
    )
    rows = [
        OperatorDay(date=d, aw_deep_work_min=float((i % 5) * 30), sources_present=frozenset({"activitywatch"}))
        for i, d in enumerate(days)
    ]
    monkeypatch.setattr(lc, "operator_daily_matrix", lambda start, end: rows)

    out = lc.audio_feature_deep_work_correlation(base, base + timedelta(days=29), min_days=10)
    assert out["covered_days"] == 30
    energy = next(f for f in out["findings"] if f["feature"] == "energy")
    assert energy["r"] > 0.9
    assert energy["significant"] is True
