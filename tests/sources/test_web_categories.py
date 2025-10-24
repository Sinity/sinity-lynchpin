"""Tests for web-history domain categorization (seed + LLM + cache + NSFW).

The LLM classifier (``_llm_classify_batch``) is monkeypatched in every test so
no real model call is made.
"""

from __future__ import annotations

import json

import pytest

from lynchpin.sources import web_categories as wc
from lynchpin.sources.web_categories import (
    CATEGORY_VOCABULARY,
    DomainCategory,
    classify_domains,
)


def test_seed_covers_obvious_domains_without_llm(monkeypatch, tmp_path):
    def _boom(domains):
        raise AssertionError("LLM should not be called for seed domains")

    monkeypatch.setattr(wc, "_llm_classify_batch", _boom)
    cache = tmp_path / "web_categories.json"

    out = classify_domains(
        ["github.com", "reddit.com", "youtube.com"],
        cache_path=cache,
    )

    assert out["github.com"].category == "dev"
    assert out["reddit.com"].category == "social"
    assert out["youtube.com"].category == "media"
    # No cache file written because nothing hit the LLM path.
    assert not cache.exists()


def test_adult_domains_are_not_seeded_and_go_through_llm(monkeypatch, tmp_path):
    # Adult domains are deliberately excluded from the tracked SEED table so no
    # real domain list ever lives in source; they must route through the LLM
    # fallback (and land only in the gitignored on-disk cache) like any other
    # unseeded domain.
    calls = []

    def _fake_llm(domains):
        calls.extend(domains)
        return {d: {"category": "adult", "nsfw": True, "content_type": "video"} for d in domains}

    monkeypatch.setattr(wc, "_llm_classify_batch", _fake_llm)
    cache = tmp_path / "web_categories.json"

    out = classify_domains(["unseeded-site.example"], cache_path=cache)

    assert calls == ["unseeded-site.example"]
    assert out["unseeded-site.example"].category == "adult"
    assert out["unseeded-site.example"].nsfw is True


def test_normalization_www_and_case(monkeypatch, tmp_path):
    monkeypatch.setattr(wc, "_llm_classify_batch", lambda d: {})
    out = classify_domains(
        ["WWW.GitHub.com", "github.com:443"], cache_path=tmp_path / "c.json"
    )
    # Both normalize to github.com -> single seed entry.
    assert set(out) == {"github.com"}
    assert out["github.com"].category == "dev"


def test_llm_used_for_unknown_domains_and_cached(monkeypatch, tmp_path):
    calls: list[list[str]] = []

    def fake_llm(domains):
        calls.append(list(domains))
        return {
            "example-news.tld": {
                "category": "news",
                "nsfw": False,
                "content_type": "general",
            }
        }

    monkeypatch.setattr(wc, "_llm_classify_batch", fake_llm)
    cache = tmp_path / "web_categories.json"

    out = classify_domains(["example-news.tld"], cache_path=cache)
    assert out["example-news.tld"].category == "news"
    assert out["example-news.tld"].nsfw is False
    assert calls == [["example-news.tld"]]

    # Cache file written with the result.
    saved = json.loads(cache.read_text())
    assert saved["example-news.tld"]["category"] == "news"

    # Second call: LLM must NOT be invoked again (served from cache on disk).
    calls.clear()
    out2 = classify_domains(["example-news.tld"], cache_path=cache)
    assert out2["example-news.tld"].category == "news"
    assert calls == []


def test_seed_beats_cache_and_llm(monkeypatch, tmp_path):
    # Even if a stale cache disagrees, seed domains resolve from seed.
    cache = tmp_path / "web_categories.json"
    cache.write_text(
        json.dumps(
            {"github.com": {"category": "other", "nsfw": False, "content_type": "x"}}
        )
    )
    monkeypatch.setattr(wc, "_llm_classify_batch", lambda d: {})
    out = classify_domains(["github.com"], cache_path=cache)
    assert out["github.com"].category == "dev"


def test_adult_label_forces_nsfw_true(monkeypatch, tmp_path):
    # Model emits category=adult but nsfw=false (or omits it): we force nsfw True.
    def fake_llm(domains):
        return {
            "weird-adult.tld": {
                "category": "adult",
                "nsfw": False,
                "content_type": "video",
            }
        }

    monkeypatch.setattr(wc, "_llm_classify_batch", fake_llm)
    out = classify_domains(["weird-adult.tld"], cache_path=tmp_path / "c.json")
    assert out["weird-adult.tld"].category == "adult"
    assert out["weird-adult.tld"].nsfw is True


def test_unknown_domain_defaults_to_other_and_is_cached(monkeypatch, tmp_path):
    # LLM returns nothing for the domain -> default 'other', cached so not re-asked.
    calls: list[list[str]] = []

    def fake_llm(domains):
        calls.append(list(domains))
        return {}

    monkeypatch.setattr(wc, "_llm_classify_batch", fake_llm)
    cache = tmp_path / "c.json"

    out = classify_domains(["mystery.tld"], cache_path=cache)
    assert out["mystery.tld"].category == "other"
    assert out["mystery.tld"].nsfw is False

    calls.clear()
    out2 = classify_domains(["mystery.tld"], cache_path=cache)
    assert out2["mystery.tld"].category == "other"
    assert calls == []  # served from cache


def test_out_of_vocab_category_coerced_to_other(monkeypatch, tmp_path):
    def fake_llm(domains):
        return {"x.tld": {"category": "banana", "nsfw": False, "content_type": "y"}}

    monkeypatch.setattr(wc, "_llm_classify_batch", fake_llm)
    out = classify_domains(["x.tld"], cache_path=tmp_path / "c.json")
    assert out["x.tld"].category == "other"


def test_batching_splits_large_input(monkeypatch, tmp_path):
    monkeypatch.setattr(wc, "_LLM_BATCH_SIZE", 2)
    batches: list[list[str]] = []

    def fake_llm(domains):
        batches.append(list(domains))
        return {d: {"category": "other", "nsfw": False, "content_type": "g"} for d in domains}

    monkeypatch.setattr(wc, "_llm_classify_batch", fake_llm)
    domains = [f"d{i}.tld" for i in range(5)]
    classify_domains(domains, cache_path=tmp_path / "c.json")
    assert [len(b) for b in batches] == [2, 2, 1]


def test_empty_and_blank_domains_skipped(monkeypatch, tmp_path):
    monkeypatch.setattr(
        wc, "_llm_classify_batch", lambda d: pytest.fail("should not call")
    )
    out = classify_domains(["", "   ", None], cache_path=tmp_path / "c.json")  # type: ignore[list-item]
    assert out == {}


def test_parse_llm_json_tolerates_fences_and_prose():
    text = 'Here you go:\n```json\n{"a.tld": {"category": "dev"}}\n```'
    parsed = wc._parse_llm_json(text)
    assert parsed == {"a.tld": {"category": "dev"}}


def test_parse_llm_json_bad_input_returns_empty():
    assert wc._parse_llm_json("not json at all") == {}
    assert wc._parse_llm_json("") == {}


def test_domain_category_shape():
    dc = DomainCategory(domain="x", category="dev", nsfw=False, content_type="docs")
    assert dc.domain == "x"
    assert dc.category in CATEGORY_VOCABULARY


def test_cache_corrupt_file_is_ignored(monkeypatch, tmp_path):
    cache = tmp_path / "c.json"
    cache.write_text("{ this is not valid json")
    monkeypatch.setattr(
        wc, "_llm_classify_batch", lambda d: {"q.tld": {"category": "news"}}
    )
    out = classify_domains(["q.tld"], cache_path=cache)
    assert out["q.tld"].category == "news"
