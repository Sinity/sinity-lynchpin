from datetime import date, datetime, timezone


def test_daily_genre_minutes_attributes_minutes_to_each_genre(monkeypatch):
    import lynchpin.sources.spotify as sp
    from lynchpin.sources.spotify import SpotifyStream

    def _s(day, hour, artist, ms):
        return SpotifyStream(
            end_time=datetime(2026, 5, day, hour, tzinfo=timezone.utc),
            artist=artist, track="t", ms_played=ms,
            platform=None, context=None, source_file="f",
        )

    streams = [
        _s(1, 15, "Halou", 180_000),       # 3 min
        _s(1, 16, "Aphex Twin", 300_000),  # 5 min
        _s(2, 12, "Halou", 60_000),        # 1 min
    ]
    monkeypatch.setattr(sp, "iter_streams", lambda root=None: iter(streams))
    monkeypatch.setattr(
        "lynchpin.sources.spotify_genres.artist_genres_by_name",
        lambda names, cache_path=None: {"Halou": ["trip hop"], "Aphex Twin": ["idm", "ambient"]},
    )

    out = sp.daily_genre_minutes(date(2026, 5, 1), date(2026, 5, 2))

    assert round(out[date(2026, 5, 1)]["trip hop"], 1) == 3.0
    assert round(out[date(2026, 5, 1)]["idm"], 1) == 5.0      # 5 min to each Aphex genre
    assert round(out[date(2026, 5, 1)]["ambient"], 1) == 5.0
    assert round(out[date(2026, 5, 2)]["trip hop"], 1) == 1.0
    assert "idm" not in out[date(2026, 5, 2)]  # Aphex not played day 2 — absent, not 0
