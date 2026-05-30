from datetime import date, time as dtime, timedelta


def test_daily_active_levels_decays_and_skips_unknown(monkeypatch):
    import lynchpin.analysis.substance_kinetics as sk
    from lynchpin.sources.substance import SubstanceEntry

    def _e(substance, amount_mg, t=dtime(12, 0)):
        return SubstanceEntry(
            date=date(2026, 5, 1), time=t, substance=substance,
            amount_mg=amount_mg, source="test", note="",
        )

    entries = [
        _e("CAFFEINE", 100.0),
        _e("UnknownDrug", 50.0),   # not in half-life table → skipped
        _e("CAFFEINE", None, t=None),  # no amount → skipped
    ]
    monkeypatch.setattr("lynchpin.sources.substance.entries_in_range", lambda *, start, end: entries)

    days = sk.daily_active_levels(date(2026, 5, 1), date(2026, 5, 3))
    assert days and all(d.substance == "caffeine" for d in days)  # unknown drug skipped

    aucs = {d.date: d.auc_mg_h for d in days}
    d1 = next(d for d in days if d.date == date(2026, 5, 1))
    assert 50 < d1.peak_mg <= 100.0001            # peak ~ dose amount at/after noon
    assert d1.auc_mg_h > 0
    # exposure decays across subsequent dose-free days
    assert aucs[date(2026, 5, 1)] > aucs.get(date(2026, 5, 2), 0) > aucs.get(date(2026, 5, 3), 0)


def test_active_level_health_correlation_planted_signal(monkeypatch):
    import lynchpin.analysis.substance_kinetics as sk
    from lynchpin.analysis.operator_daily import OperatorDay

    base = date(2026, 1, 1)
    days = [base + timedelta(days=i) for i in range(30)]
    # caffeine exposure auc[day i] = i
    sub_days = [
        sk.SubstanceDay(date=d, substance="caffeine", peak_mg=0.0, mean_mg=0.0, auc_mg_h=float(i), dosed_mg=0.0)
        for i, d in enumerate(days)
    ]
    monkeypatch.setattr(sk, "daily_active_levels", lambda start, end, half_lives=None: sub_days)
    # hrv on day j = 100 - 2j  → pair (auc[i]=i, hrv[i+1]=98-2i): perfect negative r
    rows = [
        OperatorDay(date=d, hrv_rmssd=float(100 - 2 * i), sources_present=frozenset({"health"}))
        for i, d in enumerate(days)
    ]
    monkeypatch.setattr(sk, "operator_daily_matrix", lambda start, end: rows, raising=False)
    # operator_daily_matrix is imported inside the function from .operator_daily; patch there too
    monkeypatch.setattr("lynchpin.analysis.operator_daily.operator_daily_matrix", lambda start, end: rows)

    out = sk.active_level_health_correlation(base, base + timedelta(days=29), min_days=10)
    hit = next(f for f in out["findings"] if f["substance"] == "caffeine" and f["signal"] == "hrv_rmssd")
    assert abs(hit["r"]) > 0.9
    assert hit["significant"] is True
