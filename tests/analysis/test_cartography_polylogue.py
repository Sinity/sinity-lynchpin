"""Cartography consumes complete rewrite pages and refuses broken continuations."""

import json
import runpy
from types import SimpleNamespace

import pytest


@pytest.fixture
def cartography():
    return runpy.run_path("scripts/cartography.py")


def test_query_and_message_reads_follow_every_owner_page(cartography, tmp_path):
    calls = []
    answers = iter([
        {"rows": [{"session": "fixture:s1", "id": "fixture:m1"}], "next": "q2"},
        {"rows": [{"session": "fixture:s1", "id": "fixture:m2"}], "next": None},
        {"text": "first prompt", "next": "r2"},
        {"text": "assistant context", "next": None},
    ])
    def run(argv, **kwargs):
        calls.append(argv)
        assert kwargs == {"timeout": 60}
        return SimpleNamespace(returncode=0, stdout=json.dumps(next(answers)), stderr="")
    cartography["_polylogue_pages"].__globals__["_run"] = run
    result = cartography["gather_corrections"](tmp_path)
    assert result["status"] == "available"
    assert len(result["matches"]) == 2
    assert [page["text"] for page in result["sessions"]["fixture:s1"]] == ["first prompt", "assistant context"]
    assert "written_by:human" in calls[0][2]
    assert calls[1][2:4] == ["--cursor", "q2"]
    assert calls[2][2:6] == ["read", "fixture:s1", "--view", "full"]
    assert calls[3][2:4] == ["--cursor", "r2"]


@pytest.mark.parametrize("failure", ["repeated", "nonzero", "invalid"])
def test_incomplete_queries_are_unavailable(cartography, tmp_path, failure):
    def run(argv, **kwargs):
        return SimpleNamespace(returncode=1 if failure == "nonzero" else 0,
            stdout="invalid" if failure == "invalid" else json.dumps({"rows": [], "next": "same"}), stderr="synthetic failure")
    cartography["_polylogue_pages"].__globals__["_run"] = run
    result = cartography["gather_corrections"](tmp_path)
    assert result["status"] == "unavailable"
    assert "matches" not in result
    assert "synthetic failure" in (tmp_path / "corrections.stderr").read_text()


def test_facets_use_group_queries_and_keep_their_scope(cartography, tmp_path):
    queries = []
    def run(argv, **kwargs):
        queries.append(argv[2])
        return SimpleNamespace(returncode=0, stdout=json.dumps({"rows": [], "next": None}), stderr="")
    cartography["_polylogue_pages"].__globals__["_run"] = run
    result = cartography["gather_facets"](tmp_path)
    assert result["status"] == "available"
    assert set(result["facets"]) == {"origins", "repos", "tags"}
    assert all("| group by" in query and "since:" in query for query in queries)
    assert "notes where kind:tag current:true withdrawn:false" in queries[2]
    assert queries[2].endswith("| group by key | count")


@pytest.mark.parametrize("page", [{"error": {"kind": "invalid_query"}}, {"rows": []}, {"next": None}])
def test_malformed_group_response_is_unavailable(cartography, tmp_path, page):
    cartography["_polylogue_pages"].__globals__["_run"] = lambda *a, **kw: SimpleNamespace(
        returncode=0, stdout=json.dumps(page), stderr="")
    result = cartography["gather_facets"](tmp_path)
    assert result["status"] == "unavailable"
    assert "facets" not in result
