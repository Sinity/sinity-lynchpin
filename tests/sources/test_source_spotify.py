from __future__ import annotations

import json
from datetime import date, datetime, timezone
from types import SimpleNamespace

from lynchpin.sources import spotify
from lynchpin.sources.spotify import daily_listening, iter_streams, listening_sessions


def test_spotify_reads_account_history_and_groups_sessions(tmp_path):
    root = tmp_path / "spotify"
    account = root / "Spotify Account Data"
    account.mkdir(parents=True)
    (account / "StreamingHistory_music_0.json").write_text(
        json.dumps(
            [
                {
                    "endTime": "2026-05-05 12:03",
                    "artistName": "Artist",
                    "trackName": "One",
                    "msPlayed": 180000,
                    "platform": "linux",
                },
                {
                    "endTime": "2026-05-05 12:06",
                    "artistName": "Artist",
                    "trackName": "Two",
                    "msPlayed": 180000,
                    "platform": "linux",
                },
            ]
        ),
        encoding="utf-8",
    )

    streams = list(iter_streams(root=root))
    sessions = listening_sessions(root=root)
    days = daily_listening(start=date(2026, 5, 5), end=date(2026, 5, 6), root=root)

    assert [stream.track for stream in streams] == ["One", "Two"]
    assert len(sessions) == 1
    assert sessions[0].stream_count == 2
    assert sessions[0].top_artist == "Artist"
    assert days[0].stream_count == 2
    assert days[0].unique_tracks == 2


def test_spotify_daily_listening_treats_end_as_exclusive(tmp_path):
    root = tmp_path / "spotify"
    account = root / "Spotify Account Data"
    account.mkdir(parents=True)
    (account / "StreamingHistory_music_0.json").write_text(
        json.dumps(
            [
                {
                    "endTime": "2026-05-05 12:03",
                    "artistName": "Artist",
                    "trackName": "Inside",
                    "msPlayed": 180000,
                },
                {
                    "endTime": "2026-05-06 12:03",
                    "artistName": "Artist",
                    "trackName": "Exclusive end",
                    "msPlayed": 180000,
                },
            ]
        ),
        encoding="utf-8",
    )

    days = daily_listening(start=date(2026, 5, 5), end=date(2026, 5, 6), root=root)

    assert [day.date for day in days] == [date(2026, 5, 5)]
    assert days[0].top_track == "Inside"


def test_spotify_default_reader_materializes(monkeypatch, tmp_path):
    calls = []
    product = tmp_path / "spotify/processed/streaming_history.ndjson"
    product.parent.mkdir(parents=True)
    product.write_text(
        json.dumps(
            {
                "end_time": "2026-05-05T12:03:00+00:00",
                "artist": "Artist",
                "track": "One",
                "ms_played": 180000,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(spotify, "get_config", lambda: SimpleNamespace(exports_root=tmp_path))
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda name, *, window=None: calls.append((name, window)),
    )

    rows = list(iter_streams())

    assert calls == [("spotify", None)]
    assert [row.track for row in rows] == ["One"]


def test_spotify_daily_uses_single_windowed_materialization(monkeypatch):
    calls = []
    stream = spotify.SpotifyStream(
        end_time=datetime(2026, 5, 5, 12, tzinfo=timezone.utc),
        artist="Artist",
        track="One",
        ms_played=180_000,
        platform=None,
        context=None,
        source_file="fixture",
    )

    def fake_ensure(name, *, window=None):
        calls.append((name, window))

    def fake_streams(root=None, *, start=None, end=None, ensure=True):
        assert root is None
        assert start == date(2026, 5, 5)
        assert end == date(2026, 5, 6)
        assert ensure is False
        yield stream

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure)
    monkeypatch.setattr(spotify, "iter_streams", fake_streams)

    rows = daily_listening(start=date(2026, 5, 5), end=date(2026, 5, 6))

    assert calls == [("spotify", (date(2026, 5, 5), date(2026, 5, 6)))]
    assert rows[0].stream_count == 1


def test_spotify_daily_can_skip_ensure(monkeypatch):
    stream = spotify.SpotifyStream(
        end_time=datetime(2026, 5, 5, 12, tzinfo=timezone.utc),
        artist="Artist",
        track="One",
        ms_played=180_000,
        platform=None,
        context=None,
        source_file="fixture",
    )

    def fail_ensure(*_args, **_kwargs):
        raise AssertionError("pre-audited reads must not materialize again")

    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fail_ensure)
    monkeypatch.setattr(
        spotify,
        "iter_streams",
        lambda root=None, *, start=None, end=None, ensure=True: iter([stream]),
    )

    rows = daily_listening(
        start=date(2026, 5, 5),
        end=date(2026, 5, 6),
        ensure=False,
    )

    assert rows[0].stream_count == 1


def test_spotify_listening_sessions_use_bounded_stream_reader(monkeypatch):
    calls = []
    streams = [
        spotify.SpotifyStream(
            end_time=datetime(2026, 5, 5, 12, tzinfo=timezone.utc),
            artist="Artist",
            track="One",
            ms_played=180_000,
            platform=None,
            context=None,
            source_file="fixture",
        ),
        spotify.SpotifyStream(
            end_time=datetime(2026, 5, 5, 12, 4, tzinfo=timezone.utc),
            artist="Artist",
            track="Two",
            ms_played=180_000,
            platform=None,
            context=None,
            source_file="fixture",
        ),
    ]

    def fake_streams(root=None, *, start=None, end=None, ensure=True):
        calls.append((root, start, end, ensure))
        yield from streams

    monkeypatch.setattr(spotify, "iter_streams", fake_streams)

    sessions = listening_sessions(
        start=date(2026, 5, 5),
        end=date(2026, 5, 6),
        ensure=False,
    )

    assert calls == [(None, date(2026, 5, 5), date(2026, 5, 6), False)]
    assert len(sessions) == 1
    assert sessions[0].stream_count == 2


def test_spotify_iter_streams_filters_half_open_logical_date_window(monkeypatch):
    streams = [
        spotify.SpotifyStream(
            end_time=datetime(2026, 5, 4, 12, tzinfo=timezone.utc),
            artist="Artist",
            track="Old",
            ms_played=180_000,
            platform=None,
            context=None,
            source_file="fixture",
        ),
        spotify.SpotifyStream(
            end_time=datetime(2026, 5, 5, 12, tzinfo=timezone.utc),
            artist="Artist",
            track="Kept",
            ms_played=180_000,
            platform=None,
            context=None,
            source_file="fixture",
        ),
        spotify.SpotifyStream(
            end_time=datetime(2026, 5, 6, 12, tzinfo=timezone.utc),
            artist="Artist",
            track="Future",
            ms_played=180_000,
            platform=None,
            context=None,
            source_file="fixture",
        ),
    ]
    monkeypatch.setattr(spotify, "_load_streams", lambda root=None: streams)

    rows = list(spotify.iter_streams(start=date(2026, 5, 5), end=date(2026, 5, 6), ensure=False))

    assert [row.track for row in rows] == ["Kept"]


def test_spotify_monthly_summary_uses_bounded_stream_reader(monkeypatch):
    calls = []
    stream = spotify.SpotifyStream(
        end_time=datetime(2026, 5, 5, 12, tzinfo=timezone.utc),
        artist="Artist",
        track="Track",
        ms_played=3_600_000,
        platform=None,
        context=None,
        source_file="fixture",
    )

    def fake_streams(root=None, *, start=None, end=None, ensure=True):
        calls.append((root, start, end, ensure))
        return iter([stream])

    monkeypatch.setattr(spotify, "iter_streams", fake_streams)

    summary = spotify.summarize_streaming("2026-05", "2026-05")

    assert calls == [(None, date(2026, 5, 1), date(2026, 6, 1), True)]
    assert summary.hours == {"2026-05": 1.0}
