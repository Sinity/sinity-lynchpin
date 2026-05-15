from datetime import date, time
from types import SimpleNamespace

from lynchpin.sources import substance


def test_substance_entries_and_summaries(monkeypatch, tmp_path):
    processed = tmp_path / "health" / "processed"
    processed.mkdir(parents=True)
    csv_path = processed / "substance_log_unified.csv"
    csv_path.write_text(
        "\n".join(
            [
                "date,time,substance,amount_mg,source,note",
                "2026-05-01,10:05,CAFFEINE,100,fixture,morning",
                "2026-05-01,14:00,CAFFEINE,50,fixture,afternoon",
                "2026-05-02,,MODAFINIL,200,fixture,",
                "bad-date,09:00,CAFFEINE,100,fixture,ignored",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(substance, "get_config", lambda: SimpleNamespace(exports_root=tmp_path))

    rows = list(substance.entries())
    assert len(rows) == 3
    assert rows[0].date == date(2026, 5, 1)
    assert rows[0].time == time(10, 5)
    assert rows[0].amount_mg == 100

    day = substance.entries_for_date(date(2026, 5, 1))
    assert len(day) == 2

    daily = substance.daily_summary(start=date(2026, 5, 1), end=date(2026, 5, 2))
    assert daily[0].dose_count == 2
    assert daily[0].substances == ("CAFFEINE",)
    assert daily[0].total_mg == 150
    assert daily[1].by_substance_mg == {"MODAFINIL": 200.0}

    monthly = substance.monthly_summary(start=date(2026, 5, 1), end=date(2026, 5, 31))
    assert monthly[0].month == "2026-05"
    assert monthly[0].dose_count == 3
    assert monthly[0].dose_days == 2
