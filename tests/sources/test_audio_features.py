import csv
from datetime import date, datetime, timezone

import pytest

_COLS = [
    "", "track_id", "artists", "album_name", "track_name", "popularity", "duration_ms",
    "explicit", "danceability", "energy", "key", "loudness", "mode", "speechiness",
    "acousticness", "instrumentalness", "liveness", "valence", "tempo", "time_signature",
    "track_genre",
]


def _row(track_id, artists, track, **kw):
    v = dict(
        danceability=0.5, energy=0.8, valence=0.6, tempo=120.0, acousticness=0.1,
        instrumentalness=0.0, speechiness=0.05, liveness=0.1, loudness=-6.0,
        popularity=50, key=1, mode=1,
    )
    v.update(kw)
    return [
        "0", track_id, artists, "alb", track, v["popularity"], 200000, "False",
        v["danceability"], v["energy"], v["key"], v["loudness"], v["mode"],
        v["speechiness"], v["acousticness"], v["instrumentalness"], v["liveness"],
        v["valence"], v["tempo"], 4, "rock",
    ]


def _write_dataset(path, rows):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(_COLS)
        for r in rows:
            w.writerow(r)
    return path


def test_load_audio_features_normalizes_and_indexes_each_artist(tmp_path):
    from lynchpin.sources.audio_features import features_for, load_audio_features

    ds = _write_dataset(tmp_path / "ds.csv", [
        _row("t1", "Halou", "Honeythief", energy=0.4),
        _row("t2", "A;B", "Shared Song", energy=0.9),
    ])
    idx = load_audio_features(ds)
    # case- and parenthetical-insensitive match
    f = features_for("HALOU", "Honeythief (Remastered)", idx)
    assert f is not None and f.energy == 0.4
    # each artist of a multi-artist row is independently matchable
    assert features_for("A", "Shared Song", idx) is not None
    assert features_for("B", "Shared Song", idx) is not None


def test_load_audio_features_missing_dataset_raises(tmp_path):
    from lynchpin.core.errors import SourceUnavailableError
    from lynchpin.sources.audio_features import load_audio_features

    with pytest.raises(SourceUnavailableError):
        load_audio_features(tmp_path / "nope.csv")


def test_daily_audio_features_weighted_mean_and_missing_not_zero(tmp_path, monkeypatch):
    import lynchpin.sources.audio_features as af
    from lynchpin.sources.spotify import SpotifyStream

    ds = _write_dataset(tmp_path / "ds.csv", [
        _row("t1", "Halou", "Song A", energy=0.4),
        _row("t2", "Halou", "Song B", energy=0.8),
    ])

    def _s(day, hour, track, ms):
        return SpotifyStream(
            end_time=datetime(2026, 5, day, hour, tzinfo=timezone.utc),
            artist="Halou", track=track, ms_played=ms,
            platform=None, context=None, source_file="f",
        )

    streams = [
        _s(1, 15, "Song A", 120_000),       # 2 min, energy 0.4
        _s(1, 16, "Song B", 60_000),        # 1 min, energy 0.8 → wmean 0.533
        _s(1, 17, "Unknown Track", 300_000),  # unmatched → excluded (missing != zero)
        _s(2, 12, "Song A", 60_000),
    ]
    seen_bounds = []

    def iter_streams(root=None, *, start=None, end=None, ensure=True):
        seen_bounds.append((root, start, end, ensure))
        return iter(streams)

    monkeypatch.setattr("lynchpin.sources.spotify.iter_streams", iter_streams)

    by_day = {d.date: d for d in af.daily_audio_features(date(2026, 5, 1), date(2026, 5, 2), path=ds)}
    assert seen_bounds == [(None, date(2026, 5, 1), date(2026, 5, 2), True)]
    d1 = by_day[date(2026, 5, 1)]
    assert set(by_day) == {date(2026, 5, 1)}
    assert round(d1.means["energy"], 3) == 0.533
    assert round(d1.matched_minutes, 1) == 3.0   # unmatched 5 min excluded
    assert round(d1.total_minutes, 1) == 8.0
    assert d1.matched_streams == 2 and d1.total_streams == 3
    assert round(d1.match_rate, 3) == 0.375
