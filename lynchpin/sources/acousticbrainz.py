"""AcousticBrainz audio-feature enrichment for listening history.

AcousticBrainz is a community-driven open database of acoustic and high-level
music features, keyed by MusicBrainz Recording IDs (MBIDs). This module covers
the "long tail" of eclectic libraries (anime, J-rock, EDM-niche) that the
Spotify static-dump source (``audio_features.py``) misses.

Resolution pipeline:
    1. ``(artist, title)`` → MusicBrainz MBID  (via MB search API, disk-cached)
    2. MBID → AcousticBrainz high-level + low-level features  (disk-cached)
    3. ``daily_audio_features_ab(start, end)`` aggregates ms-played-weighted
       daily means over matched streams, with explicit match coverage.

HTTP contract:
    - MusicBrainz API: ``https://musicbrainz.org/ws/2/recording?...``
      Requires a descriptive ``User-Agent``.  Rate limit: ≤1 req/sec.
    - AcousticBrainz API: ``https://acousticbrainz.org/api/v1/<mbid>/high-level``
      and ``/low-level``.  Less strict but we throttle to 1 req/sec by default.

Both HTTP helpers (``_mb_get`` and ``_ab_get``) are module-level names so tests
can monkeypatch them without touching urllib internals.

Graduated API:
    resolve_mbid(artist, title, *, cache_path?, sleep_fn?) -> str | None
        (artist, title) -> MBID string, or None if not found.  Disk-cached.

    fetch_ab_features(mbid, *, cache_path?, sleep_fn?) -> AudioFeaturesAB | None
        MBID -> parsed AudioFeaturesAB, or None on miss / HTTP error.  Disk-cached.

    features_for_ab(artist, title, ...) -> AudioFeaturesAB | None
        Convenience: resolve_mbid then fetch_ab_features in one call.

    daily_audio_features_ab(start, end) -> list[AudioFeatureDayAB]
        Per logical day, ms_played-weighted mean of each numeric feature over
        matched streams.  Days with no matched stream are absent (missing ≠ zero).
        Match coverage is reported per day.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable, Optional


# ── Public constants ───────────────────────────────────────────────────────────

_MB_BASE = "https://musicbrainz.org/ws/2"
_AB_BASE = "https://acousticbrainz.org/api/v1"

# User-Agent as required by MusicBrainz API policy.
# https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting
_USER_AGENT = (
    "sinity-lynchpin/1.0 (personal-data-hub; "
    "contact: ezo.dev@gmail.com)"
)

# Numeric feature names aggregated in daily means.
NUMERIC_FEATURES_AB: tuple[str, ...] = (
    "bpm",
    "danceability",
    "mood_happy",
    "mood_sad",
    "mood_aggressive",
    "mood_relaxed",
    "mood_party",
    "mood_acoustic",
    "instrumentalness",
)

# Default inter-request sleep in seconds — respects MB's 1 req/sec limit.
_DEFAULT_SLEEP_S: float = 1.1

# ── Data classes ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AudioFeaturesAB:
    """Acoustic + high-level features resolved from AcousticBrainz for one recording.

    All probability fields are in [0, 1]; bpm is floating-point beats-per-minute.
    Fields absent from the AB response are None — callers must treat None as
    "missing", not as zero, so aggregation remains honest.
    """

    mbid: str

    # Low-level
    bpm: Optional[float]
    key: Optional[str]  # e.g. "C major"

    # High-level mood / descriptor probabilities
    danceability: Optional[float]
    mood_happy: Optional[float]
    mood_sad: Optional[float]
    mood_aggressive: Optional[float]
    mood_relaxed: Optional[float]
    mood_party: Optional[float]
    mood_acoustic: Optional[float]
    instrumentalness: Optional[float]  # "not_instrumental" inverted → P(instrumental)
    voice_instrumental: Optional[str]  # raw AB label: "voice" | "instrumental"


@dataclass(frozen=True)
class AudioFeatureDayAB:
    """Daily aggregate of AcousticBrainz features over matched Spotify streams."""

    date: date
    means: dict[str, float]       # ms_played-weighted mean per NUMERIC_FEATURES_AB key (only present features)
    matched_minutes: float
    total_minutes: float
    matched_streams: int
    total_streams: int

    @property
    def match_rate(self) -> float:
        return self.matched_minutes / self.total_minutes if self.total_minutes else 0.0


# ── Normalisation (mirrors audio_features.py) ────────────────────────────────

_PAREN = re.compile(r"[\(\[].*?[\)\]]")
_DASH_SUFFIX = re.compile(r"\s+-\s+.*$")
_NONALNUM = re.compile(r"[^a-z0-9]+")


def _normalize(text: str) -> str:
    """Lowercase; drop parentheticals and ' - …' edition suffixes; collapse to alnum."""
    lowered = _PAREN.sub(" ", text.lower())
    lowered = _DASH_SUFFIX.sub(" ", lowered)
    return " ".join(_NONALNUM.sub(" ", lowered).split())


# ── HTTP helpers (mockable at module level) ────────────────────────────────────


def _mb_get(url: str) -> dict[str, object]:
    """GET a MusicBrainz JSON URL.  Raises urllib.error.HTTPError on failure."""
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
        data = json.load(resp)
    return data if isinstance(data, dict) else {}


def _ab_get(url: str) -> dict[str, object]:
    """GET an AcousticBrainz JSON URL.  Raises urllib.error.HTTPError on failure."""
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
        data = json.load(resp)
    return data if isinstance(data, dict) else {}


# ── Disk-cache helpers ─────────────────────────────────────────────────────────


def _default_mbid_cache_path() -> Path:
    return Path(".lynchpin/cache/acousticbrainz_mbid.json")


def _default_features_cache_path() -> Path:
    return Path(".lynchpin/cache/acousticbrainz_features.json")


def _load_json_cache(path: Path) -> dict[str, object]:
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_json_cache(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True), encoding="utf-8")


# ── MBID resolution ───────────────────────────────────────────────────────────


def _build_mb_query_url(artist: str, title: str) -> str:
    # Lucene query syntax understood by the MB search API.
    query = f'artist:"{artist}" AND recording:"{title}"'
    params = urllib.parse.urlencode({"query": query, "fmt": "json", "limit": "1"})
    return f"{_MB_BASE}/recording?{params}"


def resolve_mbid(
    artist: str,
    title: str,
    *,
    cache_path: Optional[Path] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Optional[str]:
    """Resolve ``(artist, title)`` to a MusicBrainz Recording MBID.

    Results (including misses, stored as ``null``) are disk-cached so subsequent
    calls for the same pair never hit the network. Returns ``None`` if the
    recording cannot be found or the API returns an error.

    The ``sleep_fn`` parameter is injectable for tests (pass ``lambda _: None``).
    In production it defaults to ``time.sleep`` at ``_DEFAULT_SLEEP_S`` seconds
    between calls, honouring MusicBrainz's 1 req/sec policy.
    """
    path = cache_path or _default_mbid_cache_path()
    cache: dict[str, object] = _load_json_cache(path)

    key = f"{_normalize(artist)}\x00{_normalize(title)}"
    if key in cache:
        raw = cache[key]
        return str(raw) if isinstance(raw, str) else None

    # Network call — honour rate limit.
    sleep_fn(_DEFAULT_SLEEP_S)
    try:
        url = _build_mb_query_url(artist, title)
        data = _mb_get(url)
        recordings = data.get("recordings")
        mbid: Optional[str] = None
        if isinstance(recordings, list) and recordings:
            first = recordings[0]
            mbid = str(first.get("id")) if isinstance(first, dict) and first.get("id") else None
    except (urllib.error.URLError, OSError, ValueError):
        # Network or parse error — don't cache so we can retry later.
        return None

    # Cache hit or miss (null = confirmed not found).
    cache[key] = mbid if mbid is not None else None
    _save_json_cache(path, cache)
    return mbid


# ── AcousticBrainz feature fetch ─────────────────────────────────────────────


def _parse_key(hl: dict[str, object]) -> Optional[str]:
    """Extract key+scale label from high-level payload, e.g. 'C major'."""
    tonal = hl.get("tonal")
    if not isinstance(tonal, dict):
        return None
    key_key = tonal.get("chords_key")
    key_scale = tonal.get("chords_scale")
    if isinstance(key_key, str) and isinstance(key_scale, str):
        return f"{key_key} {key_scale}"
    return None


def _prob(model: object, label: str) -> Optional[float]:
    """Extract probability from a high-level model dict."""
    if not isinstance(model, dict):
        return None
    probs = model.get("all")
    if isinstance(probs, dict):
        v = probs.get(label)
        if isinstance(v, (int, float)):
            return float(v)
    return None


def _parse_ab_high_level(mbid: str, hl_payload: dict[str, object]) -> AudioFeaturesAB:
    """Parse the AcousticBrainz /high-level JSON response into AudioFeaturesAB."""
    hl = hl_payload.get("highlevel")
    if not isinstance(hl, dict):
        hl = {}
    ll = hl_payload.get("rhythm") or {}  # /high-level may include limited rhythm info
    # bpm is in /low-level; in /high-level it may appear under "rhythm".
    bpm_raw = ll.get("bpm") if isinstance(ll, dict) else None
    bpm = float(bpm_raw) if isinstance(bpm_raw, (int, float)) else None

    # Key from tonal sub-tree (present in /high-level merged response).
    key = _parse_key(hl_payload)

    # Danceability: AB uses label "danceable" / "not_danceable".
    danceability_model = hl.get("danceability")
    danceability = _prob(danceability_model, "danceable")

    # Moods
    mood_happy = _prob(hl.get("mood_happy"), "happy")
    mood_sad = _prob(hl.get("mood_sad"), "sad")
    mood_aggressive = _prob(hl.get("mood_aggressive"), "aggressive")
    mood_relaxed = _prob(hl.get("mood_relaxed"), "relaxed")
    mood_party = _prob(hl.get("mood_party"), "party")
    mood_acoustic = _prob(hl.get("mood_acoustic"), "acoustic")

    # Voice/Instrumental: label "voice" or "instrumental".
    voice_model = hl.get("voice_instrumental")
    vi_label: Optional[str] = None
    if isinstance(voice_model, dict):
        vi_label = voice_model.get("value")
        if not isinstance(vi_label, str):
            vi_label = None
    instrumentalness: Optional[float] = None
    if vi_label is not None:
        # Convert to a probability: P(instrumental) = prob of "instrumental" label.
        instrumentalness = _prob(voice_model, "instrumental")

    return AudioFeaturesAB(
        mbid=mbid,
        bpm=bpm,
        key=key,
        danceability=danceability,
        mood_happy=mood_happy,
        mood_sad=mood_sad,
        mood_aggressive=mood_aggressive,
        mood_relaxed=mood_relaxed,
        mood_party=mood_party,
        mood_acoustic=mood_acoustic,
        instrumentalness=instrumentalness,
        voice_instrumental=vi_label,
    )


def _merge_ll_into_features(features: AudioFeaturesAB, ll_payload: dict[str, object]) -> AudioFeaturesAB:
    """Merge BPM (and key if not yet set) from a separate /low-level response."""
    rhythm = ll_payload.get("rhythm")
    bpm = features.bpm
    if bpm is None and isinstance(rhythm, dict):
        bpm_raw = rhythm.get("bpm")
        bpm = float(bpm_raw) if isinstance(bpm_raw, (int, float)) else None

    key = features.key
    if key is None:
        key = _parse_key(ll_payload)

    if bpm == features.bpm and key == features.key:
        return features  # nothing changed
    return AudioFeaturesAB(
        mbid=features.mbid,
        bpm=bpm,
        key=key,
        danceability=features.danceability,
        mood_happy=features.mood_happy,
        mood_sad=features.mood_sad,
        mood_aggressive=features.mood_aggressive,
        mood_relaxed=features.mood_relaxed,
        mood_party=features.mood_party,
        mood_acoustic=features.mood_acoustic,
        instrumentalness=features.instrumentalness,
        voice_instrumental=features.voice_instrumental,
    )


def fetch_ab_features(
    mbid: str,
    *,
    cache_path: Optional[Path] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Optional[AudioFeaturesAB]:
    """Fetch AcousticBrainz features for a MBID.

    Tries ``/high-level`` first (mood models + optional rhythm), then enriches
    BPM from ``/low-level`` if not already present.  Results are disk-cached by
    MBID; ``null`` is stored for MBIDs not found in AB so they are not re-fetched.

    Returns ``None`` on miss or unrecoverable HTTP error.
    """
    path = cache_path or _default_features_cache_path()
    cache: dict[str, object] = _load_json_cache(path)

    if mbid in cache:
        raw = cache[mbid]
        if raw is None:
            return None
        if isinstance(raw, dict):
            return _features_from_cache_dict(mbid, raw)
        return None

    # Fetch high-level.
    sleep_fn(_DEFAULT_SLEEP_S)
    try:
        hl_payload = _ab_get(f"{_AB_BASE}/{mbid}/high-level")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            cache[mbid] = None
            _save_json_cache(path, cache)
        return None
    except (urllib.error.URLError, OSError, ValueError):
        return None

    features = _parse_ab_high_level(mbid, hl_payload)

    # Enrich BPM from low-level if missing.
    if features.bpm is None:
        sleep_fn(_DEFAULT_SLEEP_S)
        try:
            ll_payload = _ab_get(f"{_AB_BASE}/{mbid}/low-level")
            features = _merge_ll_into_features(features, ll_payload)
        except (urllib.error.URLError, OSError, ValueError):
            pass  # BPM stays None — not a fatal failure

    # Persist to cache.
    cache[mbid] = _features_to_cache_dict(features)
    _save_json_cache(path, cache)
    return features


def _features_to_cache_dict(f: AudioFeaturesAB) -> dict[str, object]:
    return {
        "mbid": f.mbid,
        "bpm": f.bpm,
        "key": f.key,
        "danceability": f.danceability,
        "mood_happy": f.mood_happy,
        "mood_sad": f.mood_sad,
        "mood_aggressive": f.mood_aggressive,
        "mood_relaxed": f.mood_relaxed,
        "mood_party": f.mood_party,
        "mood_acoustic": f.mood_acoustic,
        "instrumentalness": f.instrumentalness,
        "voice_instrumental": f.voice_instrumental,
    }


def _features_from_cache_dict(mbid: str, d: dict[str, object]) -> AudioFeaturesAB:
    def _f(k: str) -> Optional[float]:
        v = d.get(k)
        return float(v) if isinstance(v, (int, float)) else None

    def _s(k: str) -> Optional[str]:
        v = d.get(k)
        return str(v) if isinstance(v, str) else None

    return AudioFeaturesAB(
        mbid=mbid,
        bpm=_f("bpm"),
        key=_s("key"),
        danceability=_f("danceability"),
        mood_happy=_f("mood_happy"),
        mood_sad=_f("mood_sad"),
        mood_aggressive=_f("mood_aggressive"),
        mood_relaxed=_f("mood_relaxed"),
        mood_party=_f("mood_party"),
        mood_acoustic=_f("mood_acoustic"),
        instrumentalness=_f("instrumentalness"),
        voice_instrumental=_s("voice_instrumental"),
    )


# ── Convenience resolver ──────────────────────────────────────────────────────


def features_for_ab(
    artist: str,
    title: str,
    *,
    mbid_cache_path: Optional[Path] = None,
    features_cache_path: Optional[Path] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Optional[AudioFeaturesAB]:
    """Resolve MBID then fetch AB features in one call.

    Returns ``None`` if either step fails.
    """
    mbid = resolve_mbid(artist, title, cache_path=mbid_cache_path, sleep_fn=sleep_fn)
    if mbid is None:
        return None
    return fetch_ab_features(mbid, cache_path=features_cache_path, sleep_fn=sleep_fn)


# ── Daily aggregation ─────────────────────────────────────────────────────────


def daily_audio_features_ab(
    start: date,
    end: date,
    *,
    mbid_cache_path: Optional[Path] = None,
    features_cache_path: Optional[Path] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> list[AudioFeatureDayAB]:
    """Per logical day, ms_played-weighted mean of each AB feature over matched streams.

    Mirrors ``audio_features.daily_audio_features``:
      - Only streams with matched AB features contribute to means.
      - Missing features (None) are excluded from that feature's weighted sum
        — they do not default to zero.
      - Days with no matched stream are absent from the result.
      - Match coverage (matched_minutes / total_minutes) is reported per day.

    Rate-limiting note: The first call will make network requests for any
    (artist, title) pairs not yet in the MBID cache.  Subsequent calls for the
    same library are served entirely from disk cache.
    """
    from ..core.primitives import logical_date
    from .spotify import iter_streams

    # Per-day accumulators.
    wsum: dict[date, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    wcount: dict[date, dict[str, float]] = defaultdict(lambda: defaultdict(float))  # total weight per feature
    matched_ms: dict[date, float] = defaultdict(float)
    total_ms: dict[date, float] = defaultdict(float)
    matched_n: dict[date, int] = defaultdict(int)
    total_n: dict[date, int] = defaultdict(int)

    for stream in iter_streams():
        if stream.end_time is None or not stream.artist:
            continue
        day = logical_date(stream.end_time)
        if day < start or day >= end:
            continue
        total_ms[day] += stream.ms_played
        total_n[day] += 1

        feats = features_for_ab(
            stream.artist,
            stream.track,
            mbid_cache_path=mbid_cache_path,
            features_cache_path=features_cache_path,
            sleep_fn=sleep_fn,
        )
        if feats is None:
            continue

        matched_ms[day] += stream.ms_played
        matched_n[day] += 1
        for feature in NUMERIC_FEATURES_AB:
            val = getattr(feats, feature)
            if val is not None:
                wsum[day][feature] += val * stream.ms_played
                wcount[day][feature] += stream.ms_played

    result: list[AudioFeatureDayAB] = []
    for day in sorted(matched_ms):
        weight = matched_ms[day]
        if weight <= 0:
            continue
        # Only include features with at least some weight (missing != zero).
        means = {
            f: wsum[day][f] / wcount[day][f]
            for f in NUMERIC_FEATURES_AB
            if wcount[day][f] > 0
        }
        result.append(
            AudioFeatureDayAB(
                date=day,
                means=means,
                matched_minutes=matched_ms[day] / 60_000.0,
                total_minutes=total_ms[day] / 60_000.0,
                matched_streams=matched_n[day],
                total_streams=total_n[day],
            )
        )
    return result


__all__ = [
    "AudioFeaturesAB",
    "AudioFeatureDayAB",
    "NUMERIC_FEATURES_AB",
    "resolve_mbid",
    "fetch_ab_features",
    "features_for_ab",
    "daily_audio_features_ab",
]
