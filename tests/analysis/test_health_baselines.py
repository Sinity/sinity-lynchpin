"""Tests for stress baselines and vitality cross-validation."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from lynchpin.analysis import health_baselines as hb


def _stress_rows(days, per_day, score_fn):
    rows = []
    for i in range(days):
        day = date(2026, 1, 1) + timedelta(days=i)
        for j in range(per_day):
            rows.append({
                "start_time": datetime.combine(day, datetime.min.time()).replace(
                    hour=10 + j
                ).isoformat(),
                "score": score_fn(i, j),
            })
    return rows


def test_stress_baseline_flags_only_real_departures(monkeypatch):
    # 30 calm days then one spike
    rows = _stress_rows(30, 4, lambda i, j: 10.0 + (j % 2))
    spike_day = date(2026, 1, 31)
    for j in range(4):
        rows.append({
            "start_time": datetime.combine(spike_day, datetime.min.time()).replace(
                hour=10 + j
            ).isoformat(),
            "score": 80.0,
        })
    monkeypatch.setattr(hb, "_load_jsonl", lambda name: iter(rows), raising=False)
    monkeypatch.setattr("lynchpin.sources.sleep._load_jsonl", lambda name: iter(rows))

    out = hb.stress_baselines(start=date(2026, 1, 1), end=spike_day)
    flagged = [r for r in out if r.flagged]
    assert len(flagged) == 1
    assert flagged[0].date == spike_day
    assert flagged[0].deviation is not None and flagged[0].deviation > 2
    # early days lack history, so they carry no baseline and cannot be flagged
    assert out[0].baseline is None and out[0].flagged is False


def test_stress_baseline_is_robust_to_a_single_outlier(monkeypatch):
    rows = _stress_rows(30, 4, lambda i, j: 100.0 if (i == 5 and j == 0) else 12.0)
    monkeypatch.setattr("lynchpin.sources.sleep._load_jsonl", lambda name: iter(rows))
    out = hb.stress_baselines(start=date(2026, 1, 1), end=date(2026, 1, 30))
    tail = [r for r in out if r.baseline is not None][-1]
    # median baseline ignores the lone spike
    assert 11.0 <= tail.baseline <= 13.0


class _Composite:
    def __init__(self, d, score=None, estimated=False, minutes=420.0,
                 deep=20.0, hr=58.0):
        self.date = d
        self.score = score
        self.score_estimated = estimated
        self.main_asleep_minutes = minutes
        self.deep_pct = deep
        self.hr_avg = hr


def test_vitality_validation_separates_proxy_and_scored_subsets(monkeypatch):
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(40)]
    vit_rows = [
        {"date": d.isoformat(), "sleep_score": 40.0 + i, "total_score": 50.0,
         "shr_score": 60.0, "shrv_score": 55.0}
        for i, d in enumerate(days)
    ]
    comps = [
        _Composite(d, score=40.0 + i, estimated=(i % 2 == 0), minutes=300.0 + 5 * i)
        for i, d in enumerate(days)
    ]
    monkeypatch.setattr("lynchpin.sources.sleep._load_jsonl", lambda name: iter(vit_rows))
    monkeypatch.setattr(
        "lynchpin.sources.sleep_composite.night_composites", lambda **kw: comps
    )

    res = hb.vitality_validation(start=days[0], end=days[-1], min_days=10)
    assert res["aligned_nights"] == 40
    subsets = {c.scored_subset for c in res["checks"]}
    assert {"all", "proxy_only", "samsung_scored"} <= subsets
    strong = next(
        c for c in res["checks"]
        if c.metric == "effective_score" and c.vitality_field == "sleep_score"
        and c.scored_subset == "all"
    )
    assert strong.r > 0.95
    assert any("not validation against independent" in c for c in res["caveats"])


def test_vitality_validation_needs_enough_days(monkeypatch):
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(5)]
    monkeypatch.setattr(
        "lynchpin.sources.sleep._load_jsonl",
        lambda name: iter([{"date": d.isoformat(), "sleep_score": 50.0} for d in days]),
    )
    monkeypatch.setattr(
        "lynchpin.sources.sleep_composite.night_composites",
        lambda **kw: [_Composite(d, score=50.0) for d in days],
    )
    res = hb.vitality_validation(start=days[0], end=days[-1], min_days=15)
    assert res["checks"] == []
