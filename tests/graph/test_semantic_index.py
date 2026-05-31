"""Tests for the persistent semantic vector index (lynchpin.graph.semantic_index).

embed() is monkeypatched to a deterministic toy embedding so no API/model is
needed. Covers: build + persistence round-trip, cosine ranking, since-filter,
and empty-index behavior.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from lynchpin.core import config as config_mod
from lynchpin.graph import semantic_index as si


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """Point get_config().cache_dir at a temp dir so indexes never touch real cache."""
    monkeypatch.setenv("LYNCHPIN_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(config_mod, "_CONFIG", None)
    yield
    monkeypatch.setattr(config_mod, "_CONFIG", None)


def _toy_embed(texts, *, backend="auto"):
    """Deterministic 3-dim embedding keyed on lowercase keywords.

    Each text maps to a direction so similarity is predictable:
      - contains "cat"  -> close to [1,0,0]
      - contains "dog"  -> close to [0,1,0]
      - else            -> [0,0,1]
    """
    out = []
    for t in texts:
        low = t.lower()
        if "cat" in low:
            out.append([1.0, 0.0, 0.05])
        elif "dog" in low:
            out.append([0.0, 1.0, 0.05])
        else:
            out.append([0.0, 0.0, 1.0])
    return out


@pytest.fixture
def patched_embed(monkeypatch):
    monkeypatch.setattr(si, "embed", _toy_embed)


def _corpus():
    return [
        si.Document(text="the cat sat", metadata={"source": "t", "id": "1", "date": "2026-01-01", "text": "the cat sat"}),
        si.Document(text="a dog barked", metadata={"source": "t", "id": "2", "date": "2026-03-01", "text": "a dog barked"}),
        si.Document(text="random noise", metadata={"source": "t", "id": "3", "date": "2026-05-01", "text": "random noise"}),
        si.Document(text="   ", metadata={"source": "t", "id": "blank"}),  # skipped
    ]


class TestBuildAndSearch:
    def test_build_skips_blank_and_reports_stats(self, patched_embed):
        stats = si.build_index(_corpus(), name="unit")
        assert stats.documents == 3  # blank skipped
        assert stats.dimension == 3
        assert stats.path.exists()

    def test_ranking_returns_most_similar_first(self, patched_embed):
        si.build_index(_corpus(), name="unit")
        hits = si.semantic_search("a fluffy cat", name="unit", k=3)
        assert hits[0].metadata["id"] == "1"  # cat doc ranks first
        assert hits[0].score > hits[1].score

    def test_dog_query_ranks_dog_doc(self, patched_embed):
        si.build_index(_corpus(), name="unit")
        hits = si.semantic_search("puppy dog", name="unit", k=1)
        assert hits[0].metadata["id"] == "2"

    def test_k_limits_results(self, patched_embed):
        si.build_index(_corpus(), name="unit")
        assert len(si.semantic_search("cat", name="unit", k=1)) == 1


class TestSinceFilter:
    def test_since_excludes_older_docs(self, patched_embed):
        si.build_index(_corpus(), name="unit")
        hits = si.semantic_search("anything", name="unit", k=10, since=date(2026, 4, 1))
        ids = {h.metadata["id"] for h in hits}
        assert ids == {"3"}  # only 2026-05-01 survives

    def test_since_drops_dateless(self, patched_embed):
        docs = [
            si.Document(text="cat", metadata={"source": "t", "id": "nd"}),
            si.Document(text="cat two", metadata={"source": "t", "id": "d", "date": "2026-05-01"}),
        ]
        si.build_index(docs, name="unit2")
        hits = si.semantic_search("cat", name="unit2", k=10, since=date(2026, 1, 1))
        assert {h.metadata["id"] for h in hits} == {"d"}


class TestPersistence:
    def test_round_trip_reload(self, patched_embed):
        si.build_index(_corpus(), name="persist")
        # Reload directly from disk via the private loader to prove round-trip.
        matrix, metadata = si._load_index("persist")
        assert matrix.shape == (3, 3)
        assert matrix.dtype == np.float32
        assert [m["id"] for m in metadata] == ["1", "2", "3"]

    def test_search_after_reload_no_rebuild(self, patched_embed):
        si.build_index(_corpus(), name="persist")
        # New search call reads from disk (no in-memory state carried over).
        hits = si.semantic_search("cat", name="persist", k=1)
        assert hits[0].metadata["id"] == "1"

    def test_missing_index_raises(self):
        with pytest.raises(FileNotFoundError):
            si.semantic_search("x", name="does-not-exist")


class TestEmptyCorpus:
    def test_empty_build_and_search(self, patched_embed):
        stats = si.build_index([], name="empty")
        assert stats.documents == 0
        assert si.semantic_search("anything", name="empty", k=5) == []


class TestPluggableSources:
    def test_index_personal_text_uses_custom_sources(self, patched_embed):
        def fake_source(start, end):
            yield si.Document(
                text="cat fact", metadata={"source": "fake", "id": "f1", "date": "2026-02-02", "text": "cat fact"}
            )

        stats = si.index_personal_text(
            date(2026, 1, 1), date(2026, 12, 31), name="custom", sources=(fake_source,)
        )
        assert stats.documents == 1
        hits = si.semantic_search("cat", name="custom", k=1)
        assert hits[0].metadata["source"] == "fake"
