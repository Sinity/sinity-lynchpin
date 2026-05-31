from datetime import date, timedelta


def test_mood_health_correlation_planted_signal(monkeypatch):
    import lynchpin.analysis.mood_correlations as mc
    import lynchpin.analysis.text_sentiment as ts
    from lynchpin.analysis.operator_daily import OperatorDay
    from lynchpin.analysis.text_sentiment import MoodDay

    base = date(2026, 1, 1)
    days = [base + timedelta(days=i) for i in range(30)]

    # mean_sentiment rises linearly -1 → +1; hrv falls linearly → perfect lag-0 negative r
    mood = [
        MoodDay(
            date=d, mean_sentiment=round(-1 + 2 * i / 29.0, 4), dominant_emotion="joy",
            message_count=3, total_words=30, emotion_means={}, sources=frozenset({"test"}),
        )
        for i, d in enumerate(days)
    ]
    monkeypatch.setattr(ts, "daily_mood", lambda start, end, **kw: mood)

    rows = [
        OperatorDay(date=d, hrv_rmssd=float(100 - 2 * i), sources_present=frozenset({"health"}))
        for i, d in enumerate(days)
    ]
    monkeypatch.setattr("lynchpin.analysis.operator_daily.operator_daily_matrix", lambda start, end: rows)

    rep = mc.mood_health_correlation(base, base + timedelta(days=29), min_pairs=10)
    assert rep.mood_days_in_window == 30
    hrv = [c for c in rep.lag_correlations if c.outcome == "hrv_rmssd"]
    assert hrv and any(abs(c.r) > 0.9 and c.significant for c in hrv)
