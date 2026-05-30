"""Spotify Web API artist-genre enrichment (client-credentials flow).

Genres are NOT present in the Spotify GDPR streaming export — only Spotify's
catalog API carries them. This module fetches per-artist genres via the
**client-credentials** flow (public catalog data; no user OAuth, no redirect
URI), caching results on disk since artist genres change rarely.

Credentials come from the environment (``SPOTIFY_CLIENT_ID`` /
``SPOTIFY_CLIENT_SECRET``), provisioned via sinnix agenix. If they are absent a
``SourceUnavailableError`` is raised rather than failing opaquely.

Graduated API:
    artist_genres(ids, *, cache_path=None) -> dict[str, list[str]]
        Batched id -> genres, disk-cached. The building block for any
        genre-aware analysis (e.g. per-day genre mix x work mode).
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.parse
import urllib.request
from collections.abc import Iterable
from pathlib import Path

from ..core.errors import SourceUnavailableError

_TOKEN_URL = "https://accounts.spotify.com/api/token"
_API_BASE = "https://api.spotify.com/v1"
_ARTISTS_BATCH = 50  # Spotify /artists accepts up to 50 ids per call

# Module-level token memo: {"token": str, "expires_at": monotonic_seconds}
_token_cache: dict[str, object] = {}


def _credentials() -> tuple[str, str]:
    cid = os.environ.get("SPOTIFY_CLIENT_ID")
    secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    if not cid or not secret:
        raise SourceUnavailableError(
            "spotify_api",
            reason=(
                "SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET not set — provision "
                "via sinnix agenix (spotify-client-{id,secret}.age) and `switch`"
            ),
        )
    return cid, secret


def _request_token() -> tuple[str, float]:
    """Client-credentials token grant. Returns (token, monotonic_expiry)."""
    cid, secret = _credentials()
    auth = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    data = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
    req = urllib.request.Request(
        _TOKEN_URL,
        data=data,
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 (fixed https host)
        payload = json.load(resp)
    # Refresh a minute early to avoid edge-of-expiry 401s.
    return payload["access_token"], time.monotonic() + float(payload.get("expires_in", 3600)) - 60.0


def _token() -> str:
    tok = _token_cache.get("token")
    raw_exp = _token_cache.get("expires_at", 0.0)
    expires_at = float(raw_exp) if isinstance(raw_exp, (int, float)) else 0.0
    if isinstance(tok, str) and time.monotonic() < expires_at:
        return tok
    token, expires_at = _request_token()
    _token_cache["token"] = token
    _token_cache["expires_at"] = expires_at
    return token


def _api_get(path: str) -> dict[str, object]:
    req = urllib.request.Request(
        f"{_API_BASE}{path}", headers={"Authorization": f"Bearer {_token()}"}
    )
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 (fixed https host)
        data = json.load(resp)
    return data if isinstance(data, dict) else {}


def _default_cache_path() -> Path:
    return Path(".lynchpin/cache/spotify_artist_genres.json")


def _load_cache(path: Path) -> dict[str, list[str]]:
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k): list(v) for k, v in data.items()}
        except (json.JSONDecodeError, OSError, ValueError):
            return {}
    return {}


def _save_cache(path: Path, cache: dict[str, list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, sort_keys=True), encoding="utf-8")


def _strip_artist_id(value: str) -> str:
    """Accept a bare id or a ``spotify:artist:<id>`` / open.spotify URL form."""
    value = value.strip()
    if value.startswith("spotify:artist:"):
        return value.rsplit(":", 1)[-1]
    if "open.spotify.com/artist/" in value:
        return value.rsplit("/", 1)[-1].split("?", 1)[0]
    return value


def artist_genres(
    ids: Iterable[str], *, cache_path: Path | None = None
) -> dict[str, list[str]]:
    """Return ``{artist_id: [genres]}`` for the given Spotify artist ids.

    Accepts bare ids, ``spotify:artist:<id>`` uris, or open.spotify urls. Results
    are disk-cached (genres rarely change); only uncached ids hit the API, batched
    50 per request. Raises ``SourceUnavailableError`` if credentials are absent.
    """
    path = cache_path or _default_cache_path()
    norm = []
    seen: set[str] = set()
    for raw in ids:
        aid = _strip_artist_id(raw)
        if aid and aid not in seen:
            seen.add(aid)
            norm.append(aid)

    cache = _load_cache(path)
    missing = [aid for aid in norm if aid not in cache]

    if missing:
        for start in range(0, len(missing), _ARTISTS_BATCH):
            batch = missing[start : start + _ARTISTS_BATCH]
            payload = _api_get("/artists?ids=" + ",".join(batch))
            artists = payload.get("artists")
            for artist in artists if isinstance(artists, list) else []:
                if isinstance(artist, dict) and artist.get("id"):
                    genres = artist.get("genres", [])
                    cache[str(artist["id"])] = list(genres) if isinstance(genres, list) else []
            # Any id the API returned null for (unknown artist) -> empty, so we
            # don't re-request it every run.
            for aid in batch:
                cache.setdefault(aid, [])
        _save_cache(path, cache)

    return {aid: cache.get(aid, []) for aid in norm}


def _default_name_cache_path() -> Path:
    return Path(".lynchpin/cache/spotify_genres_by_name.json")


def _search_artist(name: str) -> dict[str, object] | None:
    query = urllib.parse.urlencode({"q": name, "type": "artist", "limit": "1"})
    payload = _api_get(f"/search?{query}")
    artists = payload.get("artists")
    items = artists.get("items") if isinstance(artists, dict) else None
    if isinstance(items, list) and items and isinstance(items[0], dict):
        return items[0]
    return None


def artist_genres_by_name(
    names: Iterable[str], *, cache_path: Path | None = None
) -> dict[str, list[str]]:
    """Return ``{artist_name: [genres]}``, resolving each name via catalog search.

    The streaming export carries artist *names*, not ids, so genres are resolved
    with one ``/search?type=artist`` call per uncached name (artist objects carry
    genres inline). Results are disk-cached by name; names with no match cache as
    empty so they aren't re-searched. Raises ``SourceUnavailableError`` if
    credentials are absent.
    """
    path = cache_path or _default_name_cache_path()
    cache = _load_cache(path)
    result: dict[str, list[str]] = {}
    dirty = False
    for raw in names:
        name = raw.strip()
        if not name:
            continue
        if name not in cache:
            artist = _search_artist(name)
            genres = artist.get("genres") if artist else None
            cache[name] = list(genres) if isinstance(genres, list) else []
            dirty = True
        result[name] = cache[name]
    if dirty:
        _save_cache(path, cache)
    return result
