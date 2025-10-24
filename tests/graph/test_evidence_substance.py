"""Tests for evidence_substance.add_substance()."""

from __future__ import annotations

from datetime import date

import pytest


def test_add_substance_emits_one_node_per_day() -> None:
    from lynchpin.graph.evidence_substance import add_substance
    from lynchpin.sources.substance import SubstanceDaySummary

    rows = [
        SubstanceDaySummary(
            date=date(2026, 5, 1),
            dose_count=3,
            substances=("test_substance_a", "test_substance_b"),
            total_mg=60.0,
            by_substance_mg={"test_substance_a": 40.0, "test_substance_b": 20.0},
        ),
        SubstanceDaySummary(
            date=date(2026, 5, 2),
            dose_count=1,
            substances=("nicotine",),
            total_mg=2.0,
            by_substance_mg={"nicotine": 2.0},
        ),
    ]

    def _fake_daily_summary(*, start: date, end: date):
        return iter(rows)

    import lynchpin.graph.evidence_substance as _mod

    orig = _mod._daily_summary
    try:
        _mod._daily_summary = _fake_daily_summary
        nodes: list = []
        add_substance(nodes, start=date(2026, 5, 1), end=date(2026, 5, 2), selected=set())
    finally:
        _mod._daily_summary = orig

    assert len(nodes) == 2
    ids = {n.id for n in nodes}
    assert "substance:day:2026-05-01" in ids
    assert "substance:day:2026-05-02" in ids
    assert all(n.kind == "substance_day" for n in nodes)
    assert all(n.source == "substance" for n in nodes)
    assert nodes[0].payload["dose_count"] == 3
    assert nodes[0].payload["substances"] == ["test_substance_a", "test_substance_b"]


def test_add_substance_project_filter_yields_zero_nodes() -> None:
    from lynchpin.graph.evidence_substance import add_substance

    nodes: list = []
    add_substance(nodes, start=date(2026, 5, 1), end=date(2026, 5, 31), selected={"lynchpin"})
    assert nodes == []


def test_add_substance_source_error_yields_zero_nodes() -> None:
    from lynchpin.core.errors import SourceUnavailableError
    from lynchpin.graph.evidence_substance import add_substance

    def _fail(*, start: date, end: date):
        raise SourceUnavailableError("substance", reason="no data")

    import lynchpin.graph.evidence_substance as _mod

    orig = _mod._daily_summary
    try:
        _mod._daily_summary = _fail
        nodes: list = []
        add_substance(nodes, start=date(2026, 5, 1), end=date(2026, 5, 31), selected=set())
    finally:
        _mod._daily_summary = orig

    assert nodes == []
