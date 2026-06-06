"""Audio-feature enrichment for listening history (energy / valence / tempo …).

Spotify deprecated the live Audio Features endpoint for new apps (Nov 2024), so
these come from a frozen public dataset instead — a Kaggle/HuggingFace dump of
~114k tracks with the classic audio features. Treated like any other oneshot
reference library: a static CSV under ``libraries/music-audio-features/``.

Tracks are matched to the streaming history by normalized ``(artist, title)``
(the export carries names, not ids). Unmatched streams contribute nothing —
missing != zero — and the match rate is reported so coverage is honest.

Graduated API:
    load_audio_features(path?) -> dict[(norm_artist, norm_title), AudioFeatures]
    daily_audio_features(start, end) -> list[AudioFeatureDay]
        Per logical day, ms_played-weighted mean of each feature over matched
        streams, plus match coverage.
"""

from __future__ import annotations

import ast
import csv
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from ..core.errors import SourceUnavailableError

# Continuous audio features we aggregate/correlate (0..1 except tempo/loudness).
NUMERIC_FEATURES: tuple[str, ...] = (
    "danceability", "energy", "valence", "tempo",
    "acousticness", "instrumentalness", "speechiness", "liveness", "loudness",
)


@dataclass(frozen=True)
class AudioFeatures:
    track_id: str
    danceability: float
    energy: float
    valence: float
    tempo: float
    acousticness: float
    instrumentalness: float
    speechiness: float
    liveness: float
    loudness: float
    popularity: int


@dataclass(frozen=True)
class AudioFeatureDay:
    date: date
    means: dict[str, float]  # ms_played-weighted mean per NUMERIC_FEATURES key
    matched_minutes: float
    total_minutes: float
    matched_streams: int
    total_streams: int

    @property
    def match_rate(self) -> float:
        return self.matched_minutes / self.total_minutes if self.total_minutes else 0.0


_PAREN = re.compile(r"[\(\[].*?[\)\]]")
_DASH_SUFFIX = re.compile(r"\s+-\s+.*$")  # "Song - Radio Edit", "- Remastered 2011", "- Live"
_NONALNUM = re.compile(r"[^a-z0-9]+")


def _normalize(text: str) -> str:
    """Lowercase; drop parentheticals (feat./remaster) and ' - …' edition/mix
    suffixes; collapse to alphanumerics. Applied symmetrically to dataset titles
    and stream titles so e.g. 'Song - Extended Mix' matches 'Song'."""
    lowered = _PAREN.sub(" ", text.lower())
    lowered = _DASH_SUFFIX.sub(" ", lowered)
    return " ".join(_NONALNUM.sub(" ", lowered).split())


def _split_artists(raw: str) -> list[str]:
    # Dataset stores a single name, ';'-joined names, or a Python-list string
    # "['a', 'b']" (Kaggle 1.2M). literal_eval handles the list form cleanly.
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        try:
            parsed = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            parsed = None
        if isinstance(parsed, (list, tuple)):
            return [str(x).strip() for x in parsed if str(x).strip()]
    parts = re.split(r"[;,]", raw) if (";" in raw or "," in raw) else [raw]
    return [p.strip().strip("'\"") for p in parts if p.strip().strip("'\"")]


def default_dataset_path() -> Path:
    from ..core.config import get_config

    base = get_config().libraries_root / "music-audio-features"
    # Prefer larger / better-coverage dumps when present (1.2M Kaggle > 114k HF).
    for name in ("tracks_features.csv", "spotify-12m-songs.csv", "spotify-tracks-114k.csv"):
        candidate = base / name
        if candidate.exists():
            return candidate
    return base / "spotify-tracks-114k.csv"


def load_audio_features(
    path: Optional[Path] = None,
) -> dict[tuple[str, str], AudioFeatures]:
    """Index ``{(norm_artist, norm_title): AudioFeatures}`` from the dataset CSV.

    Each artist of a multi-artist row is indexed separately so a stream crediting
    any one of them matches. First occurrence wins (duplicate per-genre rows share
    the same track_id / features). Raises SourceUnavailableError if absent.
    """
    csv_path = path or default_dataset_path()
    if not csv_path.exists():
        raise SourceUnavailableError(
            "audio_features",
            path=str(csv_path),
            reason=(
                "audio-features dataset missing — download a Spotify audio-features "
                "dump (e.g. HF maharshipandya/spotify-tracks-dataset or the Kaggle "
                "1.2M-songs CSV) into libraries/music-audio-features/"
            ),
        )

    index: dict[tuple[str, str], AudioFeatures] = {}
    with csv_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            # track_name (HF 114k) or name (Kaggle 1.2M) — schema-flexible.
            title = _normalize(row.get("track_name") or row.get("name") or "")
            if not title:
                continue
            try:
                feats = AudioFeatures(
                    track_id=str(row.get("track_id") or row.get("id") or ""),
                    danceability=float(row["danceability"]),
                    energy=float(row["energy"]),
                    valence=float(row["valence"]),
                    tempo=float(row["tempo"]),
                    acousticness=float(row["acousticness"]),
                    instrumentalness=float(row["instrumentalness"]),
                    speechiness=float(row["speechiness"]),
                    liveness=float(row["liveness"]),
                    loudness=float(row["loudness"]),
                    popularity=int(float(row.get("popularity") or 0)),
                )
            except (KeyError, ValueError, TypeError):
                continue
            for artist in _split_artists(row.get("artists", "")):
                key = (_normalize(artist), title)
                index.setdefault(key, feats)
    return index


def features_for(artist: str, track: str, index: dict[tuple[str, str], AudioFeatures]) -> Optional[AudioFeatures]:
    return index.get((_normalize(artist), _normalize(track)))


def daily_audio_features(
    start: date, end: date, *, path: Optional[Path] = None
) -> list[AudioFeatureDay]:
    """Per logical day, ms_played-weighted mean of each audio feature over streams
    matched to the dataset. Days with no matched stream are absent (missing != zero);
    match coverage is reported per day."""
    from collections import defaultdict

    from ..core.primitives import logical_date
    from .spotify import iter_streams

    index = load_audio_features(path)

    # Per day: weighted sums per feature + matched/total minutes & stream counts.
    wsum: dict[date, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    matched_ms: dict[date, float] = defaultdict(float)
    total_ms: dict[date, float] = defaultdict(float)
    matched_n: dict[date, int] = defaultdict(int)
    total_n: dict[date, int] = defaultdict(int)

    for stream in iter_streams(start=start, end=end):
        if stream.end_time is None or not stream.artist:
            continue
        day = logical_date(stream.end_time)
        if day < start or day >= end:
            continue
        total_ms[day] += stream.ms_played
        total_n[day] += 1
        feats = features_for(stream.artist, stream.track, index)
        if feats is None:
            continue
        matched_ms[day] += stream.ms_played
        matched_n[day] += 1
        for feature in NUMERIC_FEATURES:
            wsum[day][feature] += getattr(feats, feature) * stream.ms_played

    result: list[AudioFeatureDay] = []
    for day in sorted(matched_ms):
        weight = matched_ms[day]
        if weight <= 0:
            continue
        means = {f: wsum[day][f] / weight for f in NUMERIC_FEATURES}
        result.append(
            AudioFeatureDay(
                date=day,
                means=means,
                matched_minutes=matched_ms[day] / 60_000.0,
                total_minutes=total_ms[day] / 60_000.0,
                matched_streams=matched_n[day],
                total_streams=total_n[day],
            )
        )
    return result
