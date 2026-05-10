from __future__ import annotations

from pathlib import Path

from lynchpin.analysis.knowledge.ledgers import write_session_ledger


def test_write_session_ledger_parses_session_docs(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    (sessions_dir / "README.md").write_text("# Sessions\n", encoding="utf-8")
    (sessions_dir / "2026-03-27-codex.md").write_text(
        "\n".join(
            [
                "# Codex Session - 2026-03-27",
                "",
                "## Source Files",
                "- `~/.codex/sessions/example.jsonl`",
                "",
                "## Highlights",
                "- Reworked the docs tree",
                "",
                "## Next Actions",
                "1. Verify the updated commands",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    output = tmp_path / "session_index.csv"
    result = write_session_ledger(sessions_dir=sessions_dir, output=output)

    assert result.row_count == 1
    text = output.read_text(encoding="utf-8")
    assert "Codex Session - 2026-03-27" in text
    assert "~/.codex/sessions/example.jsonl" in text
    assert "Reworked the docs tree" in text
    assert "Verify the updated commands" in text
