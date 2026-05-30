import pytest


def test_artist_genres_batches_caches_and_normalizes(tmp_path, monkeypatch):
    import lynchpin.sources.spotify_genres as sg

    calls = []

    def fake_api_get(path):
        calls.append(path)
        ids = path.split("ids=")[1].split(",")
        return {"artists": [{"id": i, "genres": [f"g-{i}"]} for i in ids]}

    monkeypatch.setattr(sg, "_api_get", fake_api_get)
    cache = tmp_path / "genres.json"

    # spotify:artist: uri normalized to bare id; duplicate deduped
    out = sg.artist_genres(["spotify:artist:a1", "a2", "a1"], cache_path=cache)
    assert out == {"a1": ["g-a1"], "a2": ["g-a2"]}
    assert len(calls) == 1  # single batched request

    # second call fully served from disk cache, no API hit
    calls.clear()
    out2 = sg.artist_genres(["a1", "a2"], cache_path=cache)
    assert out2 == {"a1": ["g-a1"], "a2": ["g-a2"]}
    assert calls == []


def test_artist_genres_unknown_artist_cached_empty(tmp_path, monkeypatch):
    import lynchpin.sources.spotify_genres as sg

    monkeypatch.setattr(sg, "_api_get", lambda path: {"artists": [None]})
    cache = tmp_path / "g.json"
    assert sg.artist_genres(["bogus"], cache_path=cache) == {"bogus": []}

    # cached as empty → never re-requested
    calls = []
    monkeypatch.setattr(sg, "_api_get", lambda path: calls.append(path) or {"artists": []})
    sg.artist_genres(["bogus"], cache_path=cache)
    assert calls == []


def test_artist_genres_by_name_searches_and_caches(tmp_path, monkeypatch):
    import lynchpin.sources.spotify_genres as sg

    calls = []

    def fake_api_get(path):
        calls.append(path)
        name = path.split("q=")[1].split("&")[0]
        return {"artists": {"items": [{"id": "x", "name": name, "genres": [f"{name}-genre"]}]}}

    monkeypatch.setattr(sg, "_api_get", fake_api_get)
    cache = tmp_path / "by_name.json"

    out = sg.artist_genres_by_name(["Halou", "Halou", " "], cache_path=cache)
    assert out == {"Halou": ["Halou-genre"]}  # dedup + blank skipped
    assert len(calls) == 1

    calls.clear()
    assert sg.artist_genres_by_name(["Halou"], cache_path=cache) == {"Halou": ["Halou-genre"]}
    assert calls == []  # served from disk cache


def test_artist_genres_by_name_no_match_caches_empty(tmp_path, monkeypatch):
    import lynchpin.sources.spotify_genres as sg

    monkeypatch.setattr(sg, "_api_get", lambda path: {"artists": {"items": []}})
    cache = tmp_path / "n.json"
    assert sg.artist_genres_by_name(["Nonexistent"], cache_path=cache) == {"Nonexistent": []}


def test_artist_genres_missing_credentials(tmp_path, monkeypatch):
    import lynchpin.sources.spotify_genres as sg
    from lynchpin.core.errors import SourceUnavailableError

    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
    monkeypatch.delenv("SPOTIFY_CLIENT_SECRET", raising=False)
    sg._token_cache.clear()
    with pytest.raises(SourceUnavailableError):
        sg.artist_genres(["needs_api_call"], cache_path=tmp_path / "x.json")
