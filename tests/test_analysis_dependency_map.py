"""Tests for lynchpin.analysis.maps.dependency_map pure graph functions."""

from __future__ import annotations

import pytest

from lynchpin.analysis.maps.dependency_map import (
    _compute_degrees,
    _transitive_reachability,
)


# ---------------------------------------------------------------------------
# _compute_degrees
# ---------------------------------------------------------------------------

class TestComputeDegrees:
    def test_empty_nodes_returns_empty_dict(self) -> None:
        assert _compute_degrees([], []) == {}

    def test_isolated_node_has_zero_degrees(self) -> None:
        result = _compute_degrees(["A"], [])
        assert result["A"]["in_degree"] == 0
        assert result["A"]["out_degree"] == 0
        assert result["A"]["total_degree"] == 0

    def test_isolated_node_has_empty_deps_and_dependents(self) -> None:
        result = _compute_degrees(["A"], [])
        assert result["A"]["dependencies"] == []
        assert result["A"]["dependents"] == []

    def test_single_edge_increments_out_for_source(self) -> None:
        result = _compute_degrees(["A", "B"], [("A", "B")])
        assert result["A"]["out_degree"] == 1
        assert result["A"]["in_degree"] == 0

    def test_single_edge_increments_in_for_target(self) -> None:
        result = _compute_degrees(["A", "B"], [("A", "B")])
        assert result["B"]["in_degree"] == 1
        assert result["B"]["out_degree"] == 0

    def test_total_degree_is_sum(self) -> None:
        # A has out=1, B has in=1 from A→B
        result = _compute_degrees(["A", "B"], [("A", "B")])
        assert result["A"]["total_degree"] == 1
        assert result["B"]["total_degree"] == 1

    def test_dependencies_list_contains_targets(self) -> None:
        # A depends on B and C
        result = _compute_degrees(["A", "B", "C"], [("A", "B"), ("A", "C")])
        assert result["A"]["dependencies"] == ["B", "C"]

    def test_dependents_list_contains_sources(self) -> None:
        # B and C both depend on D
        result = _compute_degrees(["B", "C", "D"], [("B", "D"), ("C", "D")])
        assert result["D"]["dependents"] == ["B", "C"]

    def test_dependencies_sorted_alphabetically(self) -> None:
        result = _compute_degrees(["A", "B", "C", "Z"], [("A", "Z"), ("A", "B"), ("A", "C")])
        assert result["A"]["dependencies"] == ["B", "C", "Z"]

    def test_dependents_sorted_alphabetically(self) -> None:
        result = _compute_degrees(["A", "B", "C", "D"], [("B", "A"), ("D", "A"), ("C", "A")])
        assert result["A"]["dependents"] == ["B", "C", "D"]

    def test_mutual_edges_cycle(self) -> None:
        # A→B and B→A
        result = _compute_degrees(["A", "B"], [("A", "B"), ("B", "A")])
        assert result["A"]["in_degree"] == 1
        assert result["A"]["out_degree"] == 1
        assert result["B"]["in_degree"] == 1
        assert result["B"]["out_degree"] == 1

    def test_hub_node_high_in_degree(self) -> None:
        # Three nodes all depend on hub
        edges = [("X", "hub"), ("Y", "hub"), ("Z", "hub")]
        result = _compute_degrees(["X", "Y", "Z", "hub"], edges)
        assert result["hub"]["in_degree"] == 3
        assert result["hub"]["out_degree"] == 0

    def test_fan_out_node_high_out_degree(self) -> None:
        edges = [("root", "A"), ("root", "B"), ("root", "C")]
        result = _compute_degrees(["root", "A", "B", "C"], edges)
        assert result["root"]["out_degree"] == 3
        assert result["root"]["in_degree"] == 0

    def test_node_not_in_edges_still_present_in_result(self) -> None:
        # 'orphan' appears in node_ids but not in any edge
        result = _compute_degrees(["A", "B", "orphan"], [("A", "B")])
        assert "orphan" in result
        assert result["orphan"]["total_degree"] == 0


# ---------------------------------------------------------------------------
# _transitive_reachability
# ---------------------------------------------------------------------------

class TestTransitiveReachability:
    def test_empty_nodes_returns_empty_dict(self) -> None:
        assert _transitive_reachability([], []) == {}

    def test_isolated_node_zero_transitive(self) -> None:
        result = _transitive_reachability(["A"], [])
        assert result["A"]["transitive_dependencies"] == 0
        assert result["A"]["transitive_dependents"] == 0

    def test_direct_dependency_counted(self) -> None:
        # A→B: A transitively depends on B (count=1)
        result = _transitive_reachability(["A", "B"], [("A", "B")])
        assert result["A"]["transitive_dependencies"] == 1
        assert result["B"]["transitive_dependencies"] == 0

    def test_direct_dependent_counted(self) -> None:
        # A→B: B has 1 transitive dependent (A)
        result = _transitive_reachability(["A", "B"], [("A", "B")])
        assert result["B"]["transitive_dependents"] == 1
        assert result["A"]["transitive_dependents"] == 0

    def test_chain_transitive_counts(self) -> None:
        # A→B→C: A deps 2 (B+C), B deps 1 (C), C deps 0
        result = _transitive_reachability(["A", "B", "C"], [("A", "B"), ("B", "C")])
        assert result["A"]["transitive_dependencies"] == 2
        assert result["B"]["transitive_dependencies"] == 1
        assert result["C"]["transitive_dependencies"] == 0

    def test_chain_reverse_transitive_dependents(self) -> None:
        # A→B→C: C dependents 2 (A+B), B dependents 1 (A), A dependents 0
        result = _transitive_reachability(["A", "B", "C"], [("A", "B"), ("B", "C")])
        assert result["C"]["transitive_dependents"] == 2
        assert result["B"]["transitive_dependents"] == 1
        assert result["A"]["transitive_dependents"] == 0

    def test_diamond_dependency_no_double_count(self) -> None:
        # A→B, A→C, B→D, C→D: A transitively depends on B, C, D = 3 unique nodes
        edges = [("A", "B"), ("A", "C"), ("B", "D"), ("C", "D")]
        result = _transitive_reachability(["A", "B", "C", "D"], edges)
        assert result["A"]["transitive_dependencies"] == 3

    def test_diamond_dependents_no_double_count(self) -> None:
        # D is transitively depended on by A, B, C = 3
        edges = [("A", "B"), ("A", "C"), ("B", "D"), ("C", "D")]
        result = _transitive_reachability(["A", "B", "C", "D"], edges)
        assert result["D"]["transitive_dependents"] == 3

    def test_cycle_does_not_include_self(self) -> None:
        # A→B→A cycle: A's transitive_dependencies should be 1 (B), not include A itself
        result = _transitive_reachability(["A", "B"], [("A", "B"), ("B", "A")])
        assert result["A"]["transitive_dependencies"] == 1
        assert result["B"]["transitive_dependencies"] == 1

    def test_parallel_edges_not_double_counted(self) -> None:
        # A→B, A→B (duplicate) — BFS sees=set so B counted once
        result = _transitive_reachability(["A", "B"], [("A", "B"), ("A", "B")])
        assert result["A"]["transitive_dependencies"] == 1

    def test_multiple_roots_independent(self) -> None:
        # Two independent chains: A→B and C→D
        edges = [("A", "B"), ("C", "D")]
        result = _transitive_reachability(["A", "B", "C", "D"], edges)
        assert result["A"]["transitive_dependencies"] == 1
        assert result["C"]["transitive_dependencies"] == 1
        assert result["B"]["transitive_dependencies"] == 0
        assert result["A"]["transitive_dependents"] == 0
