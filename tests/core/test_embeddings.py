"""Tests for the pluggable embedding backend (lynchpin.core.embeddings).

No network or model is touched: the Voyage HTTP POST is monkeypatched and the
local backend's availability probe is stubbed. The "auto raises when nothing
available" path is exercised by clearing the env and forcing local-unavailable.
"""
from __future__ import annotations

import pytest

from lynchpin.core import embeddings
from lynchpin.core.errors import SourceUnavailableError


def _fake_voyage_post(payload, *, api_key):
    """Return one deterministic 4-dim vector per input, with Voyage's shape."""
    inputs = payload["input"]
    return {
        "data": [
            {"index": i, "embedding": [float(len(text)), 1.0, 2.0, 3.0]}
            for i, text in enumerate(inputs)
        ]
    }


class TestResolveBackend:
    def test_explicit_voyage(self):
        assert embeddings.resolve_backend("voyage") == "voyage"

    def test_explicit_local(self):
        assert embeddings.resolve_backend("local") == "local"

    def test_auto_prefers_voyage_when_key_present(self, monkeypatch):
        monkeypatch.setenv("VOYAGE_API_KEY", "sk-test")
        assert embeddings.resolve_backend("auto") == "voyage"

    def test_auto_falls_to_local_when_no_key(self, monkeypatch):
        monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
        monkeypatch.setattr(embeddings, "_local_available", lambda: True)
        assert embeddings.resolve_backend("auto") == "local"

    def test_auto_raises_when_nothing_available(self, monkeypatch):
        monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
        monkeypatch.setattr(embeddings, "_local_available", lambda: False)
        with pytest.raises(SourceUnavailableError) as exc:
            embeddings.resolve_backend("auto")
        assert "VOYAGE_API_KEY" in str(exc.value)

    def test_unknown_backend(self):
        with pytest.raises(ValueError):
            embeddings.resolve_backend("nonsense")  # type: ignore[arg-type]


class TestVoyageBackend:
    def test_embed_voyage_batched(self, monkeypatch):
        monkeypatch.setenv("VOYAGE_API_KEY", "sk-test")
        monkeypatch.setattr(embeddings, "_voyage_post", _fake_voyage_post)
        vectors = embeddings.embed(["aa", "bbbb"], backend="voyage")
        assert len(vectors) == 2
        assert vectors[0] == [2.0, 1.0, 2.0, 3.0]
        assert vectors[1] == [4.0, 1.0, 2.0, 3.0]

    def test_voyage_preserves_input_order(self, monkeypatch):
        monkeypatch.setenv("VOYAGE_API_KEY", "sk-test")

        def shuffled_post(payload, *, api_key):
            inputs = payload["input"]
            data = [
                {"index": i, "embedding": [float(i)]} for i, _ in enumerate(inputs)
            ]
            return {"data": list(reversed(data))}

        monkeypatch.setattr(embeddings, "_voyage_post", shuffled_post)
        vectors = embeddings.embed(["x", "y", "z"], backend="voyage")
        assert vectors == [[0.0], [1.0], [2.0]]

    def test_voyage_requires_key(self, monkeypatch):
        monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
        with pytest.raises(SourceUnavailableError):
            embeddings.embed(["hello"], backend="voyage")

    def test_voyage_bad_response_count(self, monkeypatch):
        monkeypatch.setenv("VOYAGE_API_KEY", "sk-test")
        monkeypatch.setattr(
            embeddings, "_voyage_post", lambda payload, *, api_key: {"data": []}
        )
        with pytest.raises(SourceUnavailableError):
            embeddings.embed(["hello"], backend="voyage")


class TestLocalBackend:
    def test_local_unavailable_raises(self, monkeypatch):
        def boom():
            raise SourceUnavailableError(
                "embeddings", reason="sentence-transformers not installed"
            )

        monkeypatch.setattr(embeddings, "_load_local_model", boom)
        with pytest.raises(SourceUnavailableError):
            embeddings.embed(["hello"], backend="local")


def test_embed_empty_returns_empty():
    assert embeddings.embed([], backend="auto") == []
