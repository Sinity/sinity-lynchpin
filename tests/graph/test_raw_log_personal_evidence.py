"""Personal evidence must not depend on mentioning a software project."""

from dataclasses import replace
from datetime import date, datetime, timezone

from lynchpin.graph import evidence_raw_log as builder
from lynchpin.sources.raw_log import RawLogEntry


DAY = date(2026, 1, 15)


def _entry(text="A note about a walk."):
    return RawLogEntry(datetime(2026, 1, 15, 12, tzinfo=timezone.utc), text, "/fixture/journal.md", 7)


def _build(monkeypatch, entry, projects=(), selected=None):
    monkeypatch.setattr(builder, "entries_in_range", lambda **kwargs: [entry])
    monkeypatch.setattr(builder, "_projects_from_text", lambda text: projects)
    nodes = []
    builder.add_raw_log(nodes, start=DAY, end=DAY, selected=selected or set())
    return nodes


def test_unassociated_personal_entry_is_included_in_unscoped_graph(monkeypatch):
    nodes = _build(monkeypatch, _entry())
    assert len(nodes) == 1
    assert nodes[0].project is None
    assert nodes[0].payload["evidence_role"] == "journal_entry"
    assert not nodes[0].caveats


def test_explicit_project_filter_still_excludes_unassociated_entry(monkeypatch):
    assert _build(monkeypatch, _entry(), selected={"example"}) == []


def test_project_projections_share_source_identity(monkeypatch):
    nodes = _build(monkeypatch, _entry(), projects=("example", "other"))
    assert len(nodes) == 2
    assert nodes[0].id != nodes[1].id
    assert nodes[0].payload["source_entry_id"] == nodes[1].payload["source_entry_id"]
    selected = _build(monkeypatch, _entry(), projects=("example", "other"), selected={"example"})
    assert [node.project for node in selected] == ["example"]


def test_source_identity_survives_line_shift_and_source_move(monkeypatch):
    original = _entry()
    before = _build(monkeypatch, original)[0]
    after = _build(monkeypatch, replace(original, line_no=23, source_path="/fixture/moved.md"))[0]
    assert before.payload["source_entry_id"] == after.payload["source_entry_id"]
    assert before.id != after.id  # Existing locator-based node IDs remain compatible.
    changed = _build(monkeypatch, replace(original, text="A different note."))[0]
    assert before.payload["source_entry_id"] != changed.payload["source_entry_id"]


def test_quotation_is_not_attributed_to_journal_owner(monkeypatch):
    node = _build(monkeypatch, _entry("  > An unattributed assessment."))[0]
    assert node.payload["evidence_role"] == "quotation"
    assert "does not establish" in node.provenance.note
    assert node.caveats[0].source == "raw_log"
    assert "independently corroborate" in node.caveats[0].message


def test_quote_marker_inside_ordinary_entry_is_not_a_quotation(monkeypatch):
    node = _build(monkeypatch, _entry("The result was > 10."))[0]
    assert node.payload["evidence_role"] == "journal_entry"
    assert not node.caveats
