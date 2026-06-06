from __future__ import annotations

import json
from datetime import date

from lynchpin.sources.personal_signals import iter_personal_daily_signals, iter_spotify_daily_signals


def test_personal_daily_signals_filter_half_open_window(tmp_path) -> None:
    path = tmp_path / "daily_signals.ndjson"
    rows = [
        {"source": "keylog", "date": "2026-05-23", "metric": "keypress_count", "value": 1},
        {"source": "keylog", "date": "2026-05-24", "metric": "keypress_count", "value": 2},
        {"source": "keylog", "date": "2026-05-25", "metric": "keypress_count", "value": 3},
    ]
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    filtered = list(
        iter_personal_daily_signals(
            path,
            start=date(2026, 5, 24),
            end=date(2026, 5, 25),
        )
    )

    assert [(row.date, row.value) for row in filtered] == [(date(2026, 5, 24), 2.0)]


def test_personal_daily_signals_converges_default_materialization(tmp_path, monkeypatch) -> None:
    from lynchpin.sources import personal_signals

    path = tmp_path / "daily_signals.ndjson"
    path.write_text(
        json.dumps({"source": "keylog", "date": "2026-05-24", "metric": "keypress_count", "value": 2}) + "\n",
        encoding="utf-8",
    )
    calls = []

    def fake_ensure_materialized(name, *, window=None):
        calls.append((name, window))

    monkeypatch.setattr(personal_signals, "personal_daily_signals_path", lambda root=None: path)
    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)

    rows = list(personal_signals.iter_personal_daily_signals(start=date(2026, 5, 24), end=date(2026, 5, 25)))

    assert [(row.date, row.value) for row in rows] == [(date(2026, 5, 24), 2.0)]
    assert calls == [("personal_daily_signals", (date(2026, 5, 24), date(2026, 5, 25)))]

    calls.clear()
    assert [row.value for row in personal_signals.iter_personal_daily_signals(path)] == [2.0]
    assert calls == []


def test_spotify_daily_signals_filter_half_open_window(tmp_path) -> None:
    path = tmp_path / "spotify_daily.ndjson"
    rows = [
        {"date": "2026-05-23", "track_count": 1},
        {"date": "2026-05-24", "track_count": 2},
        {"date": "2026-05-25", "track_count": 3},
    ]
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    filtered = list(
        iter_spotify_daily_signals(
            path,
            start=date(2026, 5, 24),
            end=date(2026, 5, 25),
        )
    )

    assert [(row.date, row.track_count) for row in filtered] == [(date(2026, 5, 24), 2)]


def test_spotify_daily_signals_converges_default_materialization(tmp_path, monkeypatch) -> None:
    from lynchpin.sources import personal_signals

    path = tmp_path / "spotify_daily.ndjson"
    path.write_text(json.dumps({"date": "2026-05-24", "track_count": 2}) + "\n", encoding="utf-8")
    calls = []

    def fake_ensure_materialized(name, *, window=None):
        calls.append((name, window))

    monkeypatch.setattr(personal_signals, "spotify_daily_path", lambda root=None: path)
    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)

    rows = list(personal_signals.iter_spotify_daily_signals(start=date(2026, 5, 24), end=date(2026, 5, 25)))

    assert [(row.date, row.track_count) for row in rows] == [(date(2026, 5, 24), 2)]
    assert calls == [("spotify_daily", (date(2026, 5, 24), date(2026, 5, 25)))]

    calls.clear()
    assert [row.track_count for row in personal_signals.iter_spotify_daily_signals(path)] == [2]
    assert calls == []
