"""Tests for lynchpin.sources.acousticbrainz.

All tests use monkeypatched HTTP helpers — no live network calls, no rate-limit
waits.  The ``sleep_fn=lambda _: None`` parameter bypasses the production
``time.sleep`` call so tests are instant.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import pytest


# ── Fixtures / helpers ────────────────────────────────────────────────────────

_MBID_FRIPSIDE = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_MBID_MILI = "ffffffff-0000-1111-2222-333333333333"

_NO_SLEEP = lambda _: None  # noqa: E731


def _mb_response_found(mbid: str) -> dict:
    return {"recordings": [{"id": mbid, "title": "Song", "score": 100}]}


def _mb_response_empty() -> dict:
    return {"recordings": []}


def _hl_payload(mbid: str, **overrides: object) -> dict:
    """Minimal /high-level payload with all major models populated."""
    return {
        "metadata": {"audio_properties": {"length": 240.0}, "tags": {"musicbrainz_recordid": [mbid]}},
        "highlevel": {
            "danceability": {"all": {"danceable": 0.72, "not_danceable": 0.28}, "value": "danceable"},
            "mood_happy": {"all": {"happy": 0.85, "not_happy": 0.15}, "value": "happy"},
            "mood_sad": {"all": {"sad": 0.10, "not_sad": 0.90}, "value": "not_sad"},
            "mood_aggressive": {"all": {"aggressive": 0.20, "not_aggressive": 0.80}, "value": "not_aggressive"},
            "mood_relaxed": {"all": {"relaxed": 0.60, "not_relaxed": 0.40}, "value": "relaxed"},
            "mood_party": {"all": {"party": 0.45, "not_party": 0.55}, "value": "not_party"},
            "mood_acoustic": {"all": {"acoustic": 0.30, "not_acoustic": 0.70}, "value": "not_acoustic"},
            "voice_instrumental": {"all": {"voice": 0.90, "instrumental": 0.10}, "value": "voice"},
            **overrides,
        },
        "rhythm": {"bpm": 138.5},
        "tonal": {"chords_key": "C", "chords_scale": "major"},
    }


def _ll_payload(bpm: float = 128.0) -> dict:
    return {
        "rhythm": {"bpm": bpm},
        "tonal": {"chords_key": "A", "chords_scale": "minor"},
    }


# ── resolve_mbid tests ────────────────────────────────────────────────────────


class TestResolveMbid:
    def test_found_returns_mbid(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import lynchpin.sources.acousticbrainz as ab

        calls: list[str] = []

        def fake_mb_get(url: str) -> dict:
            calls.append(url)
            return _mb_response_found(_MBID_FRIPSIDE)

        monkeypatch.setattr(ab, "_mb_get", fake_mb_get)
        cache = tmp_path / "mbid.json"

        result = ab.resolve_mbid("fripSide", "only my railgun", cache_path=cache, sleep_fn=_NO_SLEEP)
        assert result == _MBID_FRIPSIDE
        assert len(calls) == 1
        assert "fripside" in calls[0].lower() or "railgun" in calls[0].lower()

    def test_not_found_returns_none_and_caches_null(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import lynchpin.sources.acousticbrainz as ab

        monkeypatch.setattr(ab, "_mb_get", lambda url: _mb_response_empty())
        cache = tmp_path / "mbid.json"

        result = ab.resolve_mbid("NoArtist", "NoTrack", cache_path=cache, sleep_fn=_NO_SLEEP)
        assert result is None

        # Cache should contain the null entry.
        data = json.loads(cache.read_text())
        assert any(v is None for v in data.values())

    def test_caching_avoids_second_network_call(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import lynchpin.sources.acousticbrainz as ab

        call_count = 0

        def counting_mb_get(url: str) -> dict:
            nonlocal call_count
            call_count += 1
            return _mb_response_found(_MBID_FRIPSIDE)

        monkeypatch.setattr(ab, "_mb_get", counting_mb_get)
        cache = tmp_path / "mbid.json"

        ab.resolve_mbid("fripSide", "only my railgun", cache_path=cache, sleep_fn=_NO_SLEEP)
        ab.resolve_mbid("fripSide", "only my railgun", cache_path=cache, sleep_fn=_NO_SLEEP)
        assert call_count == 1  # second call served from cache

    def test_normalization_same_key_for_variants(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Parentheticals / edition suffixes are stripped before cache key lookup."""
        import lynchpin.sources.acousticbrainz as ab

        call_count = 0

        def counting_mb_get(url: str) -> dict:
            nonlocal call_count
            call_count += 1
            return _mb_response_found(_MBID_FRIPSIDE)

        monkeypatch.setattr(ab, "_mb_get", counting_mb_get)
        cache = tmp_path / "mbid.json"

        ab.resolve_mbid("fripSide", "only my railgun", cache_path=cache, sleep_fn=_NO_SLEEP)
        # Parenthetical variant should map to same normalised key.
        ab.resolve_mbid("fripSide", "only my railgun (TV Size)", cache_path=cache, sleep_fn=_NO_SLEEP)
        assert call_count == 1

    def test_network_error_returns_none_without_caching(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import urllib.error

        import lynchpin.sources.acousticbrainz as ab

        def failing_mb_get(url: str) -> dict:
            raise urllib.error.URLError("timeout")

        monkeypatch.setattr(ab, "_mb_get", failing_mb_get)
        cache = tmp_path / "mbid.json"

        result = ab.resolve_mbid("artist", "title", cache_path=cache, sleep_fn=_NO_SLEEP)
        assert result is None
        # Should NOT have cached the failure (so retries are possible).
        assert not cache.exists() or json.loads(cache.read_text()) == {}


# ── fetch_ab_features tests ───────────────────────────────────────────────────


class TestFetchAbFeatures:
    def test_parses_high_level_fields(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import lynchpin.sources.acousticbrainz as ab

        payload = _hl_payload(_MBID_FRIPSIDE)
        monkeypatch.setattr(ab, "_ab_get", lambda url: payload)
        cache = tmp_path / "feats.json"

        feats = ab.fetch_ab_features(_MBID_FRIPSIDE, cache_path=cache, sleep_fn=_NO_SLEEP)
        assert feats is not None
        assert feats.mbid == _MBID_FRIPSIDE
        assert abs(feats.bpm - 138.5) < 0.01
        assert feats.key == "C major"
        assert abs(feats.danceability - 0.72) < 0.001
        assert abs(feats.mood_happy - 0.85) < 0.001
        assert abs(feats.mood_relaxed - 0.60) < 0.001
        assert abs(feats.instrumentalness - 0.10) < 0.001
        assert feats.voice_instrumental == "voice"

    def test_404_returns_none_and_caches_null(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import urllib.error

        import lynchpin.sources.acousticbrainz as ab

        def raise_404(url: str) -> dict:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)  # type: ignore[arg-type]

        monkeypatch.setattr(ab, "_ab_get", raise_404)
        cache = tmp_path / "feats.json"

        result = ab.fetch_ab_features("unknown-mbid", cache_path=cache, sleep_fn=_NO_SLEEP)
        assert result is None

        # Null cached.
        data = json.loads(cache.read_text())
        assert data.get("unknown-mbid") is None

    def test_caching_avoids_second_network_call(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import lynchpin.sources.acousticbrainz as ab

        call_count = 0

        def counting_ab_get(url: str) -> dict:
            nonlocal call_count
            call_count += 1
            return _hl_payload(_MBID_FRIPSIDE)

        monkeypatch.setattr(ab, "_ab_get", counting_ab_get)
        cache = tmp_path / "feats.json"

        ab.fetch_ab_features(_MBID_FRIPSIDE, cache_path=cache, sleep_fn=_NO_SLEEP)
        ab.fetch_ab_features(_MBID_FRIPSIDE, cache_path=cache, sleep_fn=_NO_SLEEP)
        # First call fetches /high-level (no bpm fallback needed here since
        # payload includes rhythm.bpm), so exactly 1 call.
        assert call_count == 1

    def test_bpm_falls_back_to_low_level(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When /high-level has no rhythm section, BPM is fetched from /low-level."""
        import lynchpin.sources.acousticbrainz as ab

        hl_no_bpm = _hl_payload(_MBID_MILI)
        hl_no_bpm = {k: v for k, v in hl_no_bpm.items() if k != "rhythm"}  # strip rhythm

        calls: list[str] = []

        def ab_get(url: str) -> dict:
            calls.append(url)
            if "low-level" in url:
                return _ll_payload(bpm=130.0)
            return hl_no_bpm

        monkeypatch.setattr(ab, "_ab_get", ab_get)
        cache = tmp_path / "feats.json"

        feats = ab.fetch_ab_features(_MBID_MILI, cache_path=cache, sleep_fn=_NO_SLEEP)
        assert feats is not None
        assert abs(feats.bpm - 130.0) < 0.01
        assert any("low-level" in c for c in calls)

    def test_cache_round_trip_preserves_fields(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import lynchpin.sources.acousticbrainz as ab

        monkeypatch.setattr(ab, "_ab_get", lambda url: _hl_payload(_MBID_FRIPSIDE))
        cache = tmp_path / "feats.json"

        original = ab.fetch_ab_features(_MBID_FRIPSIDE, cache_path=cache, sleep_fn=_NO_SLEEP)
        assert original is not None

        # Reload from disk cache — no network call.
        def no_network(url: str) -> dict:
            raise AssertionError(f"unexpected network call: {url}")

        monkeypatch.setattr(ab, "_ab_get", no_network)
        cached = ab.fetch_ab_features(_MBID_FRIPSIDE, cache_path=cache, sleep_fn=_NO_SLEEP)
        assert cached is not None
        assert cached.bpm == original.bpm
        assert cached.mood_happy == original.mood_happy
        assert cached.key == original.key
        assert cached.voice_instrumental == original.voice_instrumental


# ── daily_audio_features_ab tests ─────────────────────────────────────────────


class TestDailyAudioFeaturesAb:
    def _make_stream(self, day: int, hour: int, artist: str, track: str, ms: int):
        from lynchpin.sources.spotify import SpotifyStream

        return SpotifyStream(
            end_time=datetime(2026, 5, day, hour, tzinfo=timezone.utc),
            artist=artist,
            track=track,
            ms_played=ms,
            platform=None,
            context=None,
            source_file="fake",
        )

    def test_weighted_mean_and_missing_not_zero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import lynchpin.sources.acousticbrainz as ab

        mbid_a = "aaaa"
        mbid_b = "bbbb"

        # Stream A: 120s, mood_happy=0.9; Stream B: 60s, mood_happy=0.3
        # Weighted mean = (0.9*120 + 0.3*60) / 180 = (108 + 18) / 180 = 0.7
        # Stream C: unmatched → excluded (missing != zero)

        mbid_map = {"fripside\x00only my railgun": mbid_a, "mili\x00world execute": mbid_b}
        feats_map = {
            mbid_a: AudioFeaturesAB_from_happy(mbid_a, 0.9),
            mbid_b: AudioFeaturesAB_from_happy(mbid_b, 0.3),
        }

        monkeypatch.setattr(ab, "resolve_mbid", lambda artist, title, **kw: mbid_map.get(
            f"{ab._normalize(artist)}\x00{ab._normalize(title)}"
        ))
        monkeypatch.setattr(ab, "fetch_ab_features", lambda mbid, **kw: feats_map.get(mbid))

        streams = [
            self._make_stream(1, 10, "fripSide", "only my railgun", 120_000),
            self._make_stream(1, 11, "Mili", "world.execute(me)", 60_000),
            self._make_stream(1, 12, "Unknown Artist", "Unknown Track", 300_000),
        ]
        monkeypatch.setattr("lynchpin.sources.spotify.iter_streams", lambda root=None: iter(streams))

        days = ab.daily_audio_features_ab(
            date(2026, 5, 1), date(2026, 5, 1),
            mbid_cache_path=tmp_path / "m.json",
            features_cache_path=tmp_path / "f.json",
            sleep_fn=_NO_SLEEP,
        )
        assert len(days) == 1
        d = days[0]
        assert d.date == date(2026, 5, 1)
        assert d.matched_streams == 2
        assert d.total_streams == 3
        assert abs(d.match_rate - (180_000 / 480_000)) < 0.001
        assert abs(d.means["mood_happy"] - 0.7) < 0.001
        # Unmatched stream's ms not in matched_minutes.
        assert abs(d.matched_minutes - 3.0) < 0.01
        assert abs(d.total_minutes - 8.0) < 0.01

    def test_missing_feature_excluded_from_mean(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If one stream has bpm=None, it does not pollute the weighted mean."""
        import lynchpin.sources.acousticbrainz as ab

        mbid_a = "aaaa"
        mbid_b = "bbbb"

        # feats_a has bpm; feats_b has bpm=None
        feats_a = AudioFeaturesAB_from_bpm(mbid_a, bpm=120.0)
        feats_b = AudioFeaturesAB_from_bpm(mbid_b, bpm=None)

        monkeypatch.setattr(ab, "resolve_mbid", lambda artist, title, **kw: {
            "artist a\x00track a": mbid_a,
            "artist b\x00track b": mbid_b,
        }.get(f"{ab._normalize(artist)}\x00{ab._normalize(title)}"))
        monkeypatch.setattr(ab, "fetch_ab_features", lambda mbid, **kw: {
            mbid_a: feats_a, mbid_b: feats_b
        }.get(mbid))

        streams = [
            self._make_stream(1, 10, "Artist A", "Track A", 60_000),  # bpm=120
            self._make_stream(1, 11, "Artist B", "Track B", 60_000),  # bpm=None
        ]
        monkeypatch.setattr("lynchpin.sources.spotify.iter_streams", lambda root=None: iter(streams))

        days = ab.daily_audio_features_ab(
            date(2026, 5, 1), date(2026, 5, 1),
            mbid_cache_path=tmp_path / "m.json",
            features_cache_path=tmp_path / "f.json",
            sleep_fn=_NO_SLEEP,
        )
        assert len(days) == 1
        d = days[0]
        # bpm mean uses only the stream with bpm available → 120.0
        assert "bpm" in d.means
        assert abs(d.means["bpm"] - 120.0) < 0.01
        # matched_streams includes both (both had AB features, even if bpm was None)
        assert d.matched_streams == 2

    def test_no_match_day_absent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import lynchpin.sources.acousticbrainz as ab

        monkeypatch.setattr(ab, "resolve_mbid", lambda artist, title, **kw: None)
        monkeypatch.setattr(ab, "fetch_ab_features", lambda mbid, **kw: None)

        streams = [self._make_stream(1, 10, "Unknown", "Unknown", 60_000)]
        monkeypatch.setattr("lynchpin.sources.spotify.iter_streams", lambda root=None: iter(streams))

        days = ab.daily_audio_features_ab(
            date(2026, 5, 1), date(2026, 5, 1),
            mbid_cache_path=tmp_path / "m.json",
            features_cache_path=tmp_path / "f.json",
            sleep_fn=_NO_SLEEP,
        )
        assert days == []  # missing != zero → absent day

    def test_out_of_range_streams_excluded(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import lynchpin.sources.acousticbrainz as ab

        mbid = "aaaa"
        monkeypatch.setattr(ab, "resolve_mbid", lambda artist, title, **kw: mbid)
        monkeypatch.setattr(
            ab, "fetch_ab_features",
            lambda m, **kw: AudioFeaturesAB_from_happy(m, 0.5)
        )

        streams = [
            self._make_stream(1, 10, "X", "Y", 60_000),  # May 1 — in range
            self._make_stream(5, 10, "X", "Y", 60_000),  # May 5 — out of range
        ]
        monkeypatch.setattr("lynchpin.sources.spotify.iter_streams", lambda root=None: iter(streams))

        days = ab.daily_audio_features_ab(
            date(2026, 5, 1), date(2026, 5, 2),
            mbid_cache_path=tmp_path / "m.json",
            features_cache_path=tmp_path / "f.json",
            sleep_fn=_NO_SLEEP,
        )
        assert len(days) == 1
        assert days[0].date == date(2026, 5, 1)


# ── features_for_ab convenience ───────────────────────────────────────────────


class TestFeaturesForAb:
    def test_chained_resolution(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import lynchpin.sources.acousticbrainz as ab

        monkeypatch.setattr(ab, "_mb_get", lambda url: _mb_response_found(_MBID_FRIPSIDE))
        monkeypatch.setattr(ab, "_ab_get", lambda url: _hl_payload(_MBID_FRIPSIDE))

        feats = ab.features_for_ab(
            "fripSide", "only my railgun",
            mbid_cache_path=tmp_path / "m.json",
            features_cache_path=tmp_path / "f.json",
            sleep_fn=_NO_SLEEP,
        )
        assert feats is not None
        assert feats.mbid == _MBID_FRIPSIDE

    def test_none_when_mbid_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import lynchpin.sources.acousticbrainz as ab

        monkeypatch.setattr(ab, "_mb_get", lambda url: _mb_response_empty())

        feats = ab.features_for_ab(
            "NoArtist", "NoTrack",
            mbid_cache_path=tmp_path / "m.json",
            features_cache_path=tmp_path / "f.json",
            sleep_fn=_NO_SLEEP,
        )
        assert feats is None


# ── Test helpers ──────────────────────────────────────────────────────────────


def AudioFeaturesAB_from_happy(mbid: str, happy: float) -> "ab_module.AudioFeaturesAB":
    from lynchpin.sources.acousticbrainz import AudioFeaturesAB

    return AudioFeaturesAB(
        mbid=mbid, bpm=120.0, key="C major",
        danceability=0.5, mood_happy=happy, mood_sad=0.1,
        mood_aggressive=0.2, mood_relaxed=0.4, mood_party=0.3,
        mood_acoustic=0.2, instrumentalness=0.05, voice_instrumental="voice",
    )


def AudioFeaturesAB_from_bpm(mbid: str, bpm: Optional[float]) -> "ab_module.AudioFeaturesAB":
    from lynchpin.sources.acousticbrainz import AudioFeaturesAB

    return AudioFeaturesAB(
        mbid=mbid, bpm=bpm, key="A minor",
        danceability=0.6, mood_happy=0.5, mood_sad=0.2,
        mood_aggressive=0.1, mood_relaxed=0.5, mood_party=0.3,
        mood_acoustic=0.4, instrumentalness=0.2, voice_instrumental="voice",
    )


# Type alias used in type annotations above — forward ref trick.
import lynchpin.sources.acousticbrainz as ab_module  # noqa: E402
