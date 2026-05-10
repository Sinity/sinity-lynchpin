from __future__ import annotations

import json
from datetime import date

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
    days = daily_listening(start=date(2026, 5, 5), end=date(2026, 5, 5), root=root)

    assert [stream.track for stream in streams] == ["One", "Two"]
    assert len(sessions) == 1
    assert sessions[0].stream_count == 2
    assert sessions[0].top_artist == "Artist"
    assert days[0].stream_count == 2
    assert days[0].unique_tracks == 2
