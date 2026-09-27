from __future__ import annotations

from pathlib import Path

import pytest

from lynchpin.sources.exports_dendron import read_note, search_notes


def test_note_search_pages_full_tree_and_reads_provenance(tmp_path: Path) -> None:
    for number in range(205):
        (tmp_path / f"note-{number:03}.md").write_text(
            f"---\nid: note-{number}\ntitle: Match {number}\n---\nBody {number}\n"
        )
    page = search_notes("match", root=tmp_path, offset=200, limit=20)
    assert page["status"] == "complete"
    assert page["total"] == 205
    assert len(page["notes"]) == 5
    assert page["next_offset"] is None
    last = read_note("note-204.md", root=tmp_path)
    assert last["id"] == "note-204"
    assert last["body"] == "Body 204"
    assert last["source_mtime_ns"] > 0
    assert search_notes("absent", root=tmp_path)["total"] == 0


def test_note_read_rejects_escape_and_search_reports_unreadable(tmp_path: Path) -> None:
    (tmp_path / "good.md").write_text("# Good\n")
    (tmp_path / "bad.md").write_bytes(b"\xff")
    (tmp_path / "link.md").symlink_to(tmp_path / "good.md")
    result = search_notes("", root=tmp_path)
    assert result["status"] == "partial"
    assert result["omissions"] == [{"path": "bad.md", "reason": "UnicodeDecodeError"}]
    assert [row["path"] for row in result["notes"]] == ["good.md"]
    with pytest.raises(ValueError):
        read_note("../outside.md", root=tmp_path)
    with pytest.raises(FileNotFoundError):
        read_note("link.md", root=tmp_path)
