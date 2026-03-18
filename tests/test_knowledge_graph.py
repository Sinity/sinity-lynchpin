"""Tests for lynchpin.views.knowledge_graph pure functions."""

from __future__ import annotations

from pathlib import Path

import pytest

from lynchpin.views.knowledge_graph import (
    Edge,
    Node,
    _date_ranges_overlap,
    _digest,
    _month_to_date_str,
    build_temporal_edges,
    edges_to_df,
    iter_markdown_files,
    nodes_to_df,
    parse_markdown,
)


# ---------------------------------------------------------------------------
# _month_to_date_str
# ---------------------------------------------------------------------------

class TestMonthToDateStr:
    def test_valid_month_returns_first_day(self) -> None:
        assert _month_to_date_str("2026-03") == "2026-03-01"

    def test_january(self) -> None:
        assert _month_to_date_str("2026-01") == "2026-01-01"

    def test_december(self) -> None:
        assert _month_to_date_str("2025-12") == "2025-12-01"

    def test_invalid_format_returns_none(self) -> None:
        assert _month_to_date_str("2026-W11") is None
        assert _month_to_date_str("not-a-date") is None
        assert _month_to_date_str("") is None


# ---------------------------------------------------------------------------
# _date_ranges_overlap
# ---------------------------------------------------------------------------

class TestDateRangesOverlap:
    def test_identical_ranges_overlap(self) -> None:
        assert _date_ranges_overlap("2026-01-01", "2026-01-31", "2026-01-01", "2026-01-31")

    def test_adjacent_ranges_overlap_at_boundary(self) -> None:
        # end of A == start of B: they touch, so overlap
        assert _date_ranges_overlap("2026-01-01", "2026-01-15", "2026-01-15", "2026-01-31")

    def test_disjoint_ranges_do_not_overlap(self) -> None:
        assert not _date_ranges_overlap("2026-01-01", "2026-01-14", "2026-01-15", "2026-01-31")

    def test_a_contains_b(self) -> None:
        assert _date_ranges_overlap("2026-01-01", "2026-03-31", "2026-02-01", "2026-02-28")

    def test_b_contains_a(self) -> None:
        assert _date_ranges_overlap("2026-02-01", "2026-02-28", "2026-01-01", "2026-03-31")

    def test_a_entirely_before_b(self) -> None:
        assert not _date_ranges_overlap("2025-01-01", "2025-12-31", "2026-01-01", "2026-12-31")


# ---------------------------------------------------------------------------
# _digest
# ---------------------------------------------------------------------------

class TestDigest:
    def test_returns_prefixed_sha1(self) -> None:
        result = _digest("doc", "/some/path.md")
        assert result.startswith("doc:")
        assert len(result) > 10

    def test_same_inputs_same_output(self) -> None:
        assert _digest("sec", "a", "b") == _digest("sec", "a", "b")

    def test_different_inputs_different_output(self) -> None:
        assert _digest("doc", "a") != _digest("doc", "b")


# ---------------------------------------------------------------------------
# iter_markdown_files
# ---------------------------------------------------------------------------

class TestIterMarkdownFiles:
    def test_scans_md_files_recursively(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text("# A")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "b.markdown").write_text("# B")
        (tmp_path / "skip.txt").write_text("not markdown")

        files = list(iter_markdown_files([tmp_path]))
        names = {f.name for f in files}
        assert "a.md" in names
        assert "b.markdown" in names
        assert "skip.txt" not in names

    def test_accepts_single_file(self, tmp_path: Path) -> None:
        f = tmp_path / "doc.md"
        f.write_text("# Doc")
        result = list(iter_markdown_files([f]))
        assert result == [f]

    def test_skips_git_directory(self, tmp_path: Path) -> None:
        git_dir = tmp_path / ".git" / "COMMIT_EDITMSG"
        git_dir.parent.mkdir(parents=True)
        git_dir.write_text("# not a doc")
        (tmp_path / "real.md").write_text("# Real")

        files = list(iter_markdown_files([tmp_path]))
        assert all(".git/" not in f.as_posix() for f in files)

    def test_missing_directory_yields_nothing(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist"
        assert list(iter_markdown_files([missing])) == []


# ---------------------------------------------------------------------------
# parse_markdown
# ---------------------------------------------------------------------------

class TestParseMarkdown:
    def test_document_node_created(self, tmp_path: Path) -> None:
        md = tmp_path / "test.md"
        md.write_text("# Title\n\nSome content.")
        nodes, _ = parse_markdown(md)
        doc_nodes = [n for n in nodes if n.kind == "document"]
        assert len(doc_nodes) == 1
        assert doc_nodes[0].title == "Title"

    def test_section_nodes_created(self, tmp_path: Path) -> None:
        md = tmp_path / "doc.md"
        md.write_text("# Title\n\n## Section A\n\nContent A.\n\n## Section B\n\nContent B.")
        nodes, _ = parse_markdown(md)
        section_nodes = [n for n in nodes if n.kind == "section"]
        titles = {n.title for n in section_nodes}
        assert "Section A" in titles
        assert "Section B" in titles

    def test_task_nodes_created(self, tmp_path: Path) -> None:
        md = tmp_path / "tasks.md"
        md.write_text("# Tasks\n\n- [ ] Do this\n- [x] Done that")
        nodes, _ = parse_markdown(md)
        tasks = [n for n in nodes if n.kind == "task"]
        assert len(tasks) == 2
        statuses = {n.metadata["status"] for n in tasks}
        assert "todo" in statuses
        assert "done" in statuses

    def test_contains_edges_from_headings(self, tmp_path: Path) -> None:
        md = tmp_path / "doc.md"
        md.write_text("# Doc\n\n## Child\n\nBody.")
        _, edges = parse_markdown(md)
        contains_edges = [e for e in edges if e.edge_type == "contains"]
        assert len(contains_edges) >= 1

    def test_link_edges_extracted(self, tmp_path: Path) -> None:
        # Link appears in both document node (full text) and section node (section content),
        # so at least 1 edge is produced and all edges have the correct label.
        md = tmp_path / "linked.md"
        md.write_text("# Page\n\n## Links\n\nSee [other page](other.md) for details.")
        _, edges = parse_markdown(md)
        link_edges = [e for e in edges if e.edge_type == "link"]
        assert len(link_edges) >= 1
        assert all(e.metadata["label"] == "other page" for e in link_edges)

    def test_empty_file_produces_document_node(self, tmp_path: Path) -> None:
        md = tmp_path / "empty.md"
        md.write_text("")
        nodes, edges = parse_markdown(md)
        assert len([n for n in nodes if n.kind == "document"]) == 1
        assert edges == []

    def test_title_falls_back_to_stem_when_no_heading(self, tmp_path: Path) -> None:
        md = tmp_path / "my_file.md"
        md.write_text("Just a paragraph, no heading.")
        nodes, _ = parse_markdown(md)
        doc = next(n for n in nodes if n.kind == "document")
        assert doc.title == "my file"


# ---------------------------------------------------------------------------
# build_temporal_edges
# ---------------------------------------------------------------------------

def _make_episode_node(ep_id: str, valid_from: str, valid_until: str, confidence: float = 0.8) -> dict:
    return {
        "id": f"episode:{ep_id}",
        "kind": "episode",
        "label": ep_id,
        "valid_from": valid_from,
        "valid_until": valid_until,
        "confidence": confidence,
    }


def _make_theme_node(name: str, valid_from: str, valid_until: str, confidence: float = 0.7) -> dict:
    return {
        "id": f"theme:project:{name}",
        "kind": "theme",
        "label": name,
        "valid_from": valid_from,
        "valid_until": valid_until,
        "confidence": confidence,
    }


class TestBuildTemporalEdges:
    def test_precedes_edges_for_consecutive_episodes(self) -> None:
        eps = [
            _make_episode_node("ep1", "2026-01-01", "2026-01-15"),
            _make_episode_node("ep2", "2026-01-16", "2026-01-31"),
        ]
        edges = build_temporal_edges(eps, [])
        precedes = [e for e in edges if e["kind"] == "precedes"]
        assert len(precedes) == 1
        assert precedes[0]["source"] == "episode:ep1"
        assert precedes[0]["target"] == "episode:ep2"

    def test_precedes_sorted_by_start_date(self) -> None:
        # Supply episodes out of order — edges should be sorted by valid_from
        eps = [
            _make_episode_node("ep2", "2026-02-01", "2026-02-28"),
            _make_episode_node("ep1", "2026-01-01", "2026-01-31"),
        ]
        edges = build_temporal_edges(eps, [])
        precedes = [e for e in edges if e["kind"] == "precedes"]
        assert precedes[0]["source"] == "episode:ep1"
        assert precedes[0]["target"] == "episode:ep2"

    def test_no_precedes_edge_for_single_episode(self) -> None:
        eps = [_make_episode_node("ep1", "2026-01-01", "2026-01-31")]
        edges = build_temporal_edges(eps, [])
        assert not any(e["kind"] == "precedes" for e in edges)

    def test_contains_edge_when_episode_spans_theme(self) -> None:
        eps = [_make_episode_node("ep1", "2026-01-01", "2026-03-31")]
        themes = [_make_theme_node("sinex", "2026-01-01", "2026-02-01")]
        edges = build_temporal_edges(eps, themes)
        ep_theme = [e for e in edges if e["kind"] in ("contains", "overlaps")]
        assert len(ep_theme) == 1
        assert ep_theme[0]["kind"] == "contains"

    def test_overlaps_edge_when_partial_intersection(self) -> None:
        eps = [_make_episode_node("ep1", "2026-01-01", "2026-02-15")]
        themes = [_make_theme_node("sinex", "2026-02-01", "2026-03-01")]
        edges = build_temporal_edges(eps, themes)
        ep_theme = [e for e in edges if e["kind"] in ("overlaps", "contains")]
        assert len(ep_theme) == 1
        assert ep_theme[0]["kind"] == "overlaps"

    def test_no_ep_theme_edge_when_disjoint(self) -> None:
        eps = [_make_episode_node("ep1", "2026-01-01", "2026-01-31")]
        themes = [_make_theme_node("sinex", "2026-03-01", "2026-03-31")]
        edges = build_temporal_edges(eps, themes)
        assert not any(e["kind"] in ("overlaps", "contains") for e in edges)

    def test_confidence_is_averaged_for_precedes(self) -> None:
        eps = [
            _make_episode_node("ep1", "2026-01-01", "2026-01-15", confidence=0.8),
            _make_episode_node("ep2", "2026-01-16", "2026-01-31", confidence=0.6),
        ]
        edges = build_temporal_edges(eps, [])
        precedes = [e for e in edges if e["kind"] == "precedes"]
        assert precedes[0]["confidence"] == pytest.approx(0.7, abs=0.001)

    def test_empty_inputs_yield_no_edges(self) -> None:
        assert build_temporal_edges([], []) == []


# ---------------------------------------------------------------------------
# nodes_to_df / edges_to_df
# ---------------------------------------------------------------------------

def _make_node(node_id: str, kind: str = "document") -> Node:
    return Node(
        node_id=node_id,
        kind=kind,
        title="Test Title",
        content="Some content",
        source_path="/test/doc.md",
        parent_id=None,
        metadata={"key": "value"},
    )


def _make_edge(edge_id: str, source_id: str, target_id: str) -> Edge:
    return Edge(
        edge_id=edge_id,
        edge_type="contains",
        source_id=source_id,
        target_id=target_id,
        metadata={"label": "test"},
    )


class TestNodesToDF:
    def test_row_count_matches_input(self) -> None:
        pytest.importorskip("pandas", exc_type=ImportError)
        nodes = [_make_node("n1"), _make_node("n2"), _make_node("n3")]
        df = nodes_to_df(nodes)
        assert len(df) == 3

    def test_expected_columns_present(self) -> None:
        pytest.importorskip("pandas", exc_type=ImportError)
        df = nodes_to_df([_make_node("n1")])
        for col in ("node_id", "kind", "title", "content", "source_path", "parent_id", "metadata"):
            assert col in df.columns

    def test_metadata_serialized_to_json_string(self) -> None:
        import json
        pytest.importorskip("pandas", exc_type=ImportError)
        node = _make_node("n1")
        df = nodes_to_df([node])
        meta_val = df.iloc[0]["metadata"]
        assert isinstance(meta_val, str)
        parsed = json.loads(meta_val)
        assert parsed == {"key": "value"}

    def test_empty_input_produces_empty_df(self) -> None:
        pytest.importorskip("pandas", exc_type=ImportError)
        df = nodes_to_df([])
        assert len(df) == 0

    def test_node_id_preserved(self) -> None:
        pytest.importorskip("pandas", exc_type=ImportError)
        df = nodes_to_df([_make_node("my-unique-id")])
        assert df.iloc[0]["node_id"] == "my-unique-id"


class TestEdgesToDF:
    def test_row_count_matches_input(self) -> None:
        pytest.importorskip("pandas", exc_type=ImportError)
        edges = [_make_edge("e1", "n1", "n2"), _make_edge("e2", "n2", "n3")]
        df = edges_to_df(edges)
        assert len(df) == 2

    def test_expected_columns_present(self) -> None:
        pytest.importorskip("pandas", exc_type=ImportError)
        df = edges_to_df([_make_edge("e1", "n1", "n2")])
        for col in ("edge_id", "edge_type", "source_id", "target_id", "metadata"):
            assert col in df.columns

    def test_metadata_serialized_to_json_string(self) -> None:
        import json
        pytest.importorskip("pandas", exc_type=ImportError)
        df = edges_to_df([_make_edge("e1", "n1", "n2")])
        meta_val = df.iloc[0]["metadata"]
        assert isinstance(meta_val, str)
        parsed = json.loads(meta_val)
        assert parsed == {"label": "test"}

    def test_empty_input_produces_empty_df(self) -> None:
        pytest.importorskip("pandas", exc_type=ImportError)
        df = edges_to_df([])
        assert len(df) == 0
