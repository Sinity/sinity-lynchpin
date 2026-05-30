from datetime import date, timedelta


def test_genre_deep_work_correlation_planted_signal(monkeypatch):
    import lynchpin.analysis.lifestyle_correlations as lc
    from lynchpin.analysis.operator_daily import OperatorDay

    base = date(2026, 1, 1)
    days = [base + timedelta(days=i) for i in range(30)]

    # "focus" genre minutes track deep-work exactly; "noise" is unrelated.
    genre_days = {
        d: {"focus": float((i % 5) * 10), "noise": float((i * 7) % 31)}
        for i, d in enumerate(days)
    }
    monkeypatch.setattr(
        "lynchpin.sources.spotify.daily_genre_minutes",
        lambda start, end, cache_path=None: genre_days,
    )
    rows = [
        OperatorDay(
            date=d,
            aw_deep_work_min=float((i % 5) * 30),
            sources_present=frozenset({"activitywatch"}),
        )
        for i, d in enumerate(days)
    ]
    monkeypatch.setattr(lc, "operator_daily_matrix", lambda start, end: rows)

    out = lc.genre_deep_work_correlation(base, base + timedelta(days=29), min_listen_days=5)

    assert out["covered_days"] == 30
    focus = next(f for f in out["findings"] if f["genre"] == "focus")
    assert focus["r"] > 0.9  # focus minutes perfectly track deep-work
    assert focus["significant"] is True


def test_genre_deep_work_correlation_excludes_uncovered_days(monkeypatch):
    """Days without ActivityWatch presence are not counted (missing != zero)."""
    import lynchpin.analysis.lifestyle_correlations as lc
    from lynchpin.analysis.operator_daily import OperatorDay

    base = date(2026, 1, 1)
    days = [base + timedelta(days=i) for i in range(10)]
    monkeypatch.setattr(
        "lynchpin.sources.spotify.daily_genre_minutes",
        lambda start, end, cache_path=None: {d: {"focus": 10.0} for d in days},
    )
    # aw_deep_work_min present but activitywatch NOT in sources_present → excluded
    rows = [
        OperatorDay(date=d, aw_deep_work_min=30.0, sources_present=frozenset())
        for d in days
    ]
    monkeypatch.setattr(lc, "operator_daily_matrix", lambda start, end: rows)

    out = lc.genre_deep_work_correlation(base, base + timedelta(days=9), min_listen_days=3)
    assert out["covered_days"] == 0
    assert out["findings"] == []
