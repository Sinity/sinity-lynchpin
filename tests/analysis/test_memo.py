"""Tests for content-keyed DAG step memoization."""

from __future__ import annotations

from dataclasses import dataclass
from lynchpin.analysis.core import memo


@dataclass
class _FakeStep:
    name: str
    fingerprint: str | None = None


def test_memoized_skips_only_unchanged_fingerprints():
    current = {"a": "h1", "b": "h2", "c": "h3"}
    stored = {"a": "h1", "b": "OLD"}  # b changed; c never recorded
    assert memo.memoized_skips(current, stored) == {"a"}


def test_compute_fingerprints_uses_only_declared_serializable_keys():
    steps = {
        "fixed": _FakeStep("fixed", fingerprint="sha-1"),
        "none_fn": _FakeStep("none_fn", fingerprint=None),
        "empty": _FakeStep("empty", fingerprint=""),
        "callable": _FakeStep("callable", fingerprint=lambda: "not serializable"),
    }
    assert memo.compute_fingerprints(steps) == {"fixed": "sha-1"}


def test_record_and_load_roundtrip_merges(tmp_path):
    store = tmp_path / "fp.json"
    memo.record_fingerprints({"a": "h1"}, store)
    memo.record_fingerprints({"b": "h2", "a": "h1b"}, store)
    assert memo.load_fingerprints(store) == {"a": "h1b", "b": "h2"}


def test_load_fingerprints_absent_or_corrupt(tmp_path):
    assert memo.load_fingerprints(tmp_path / "missing.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    assert memo.load_fingerprints(bad) == {}


def test_record_fingerprints_noop_on_empty(tmp_path):
    store = tmp_path / "fp.json"
    memo.record_fingerprints({}, store)
    assert not store.exists()
