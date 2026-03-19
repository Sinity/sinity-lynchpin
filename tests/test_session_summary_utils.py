from __future__ import annotations

import json
from pathlib import Path

from lynchpin.analysis.knowledge import session_summaries


def test_summarise_session_transcript_writes_json(tmp_path: Path, monkeypatch) -> None:
    transcript = tmp_path / "conversation.md"
    transcript.write_text("# Session\n\nWorked on baseline cleanup.\n", encoding="utf-8")

    def fake_codex(prompt: str, model: str | None) -> tuple[str, str]:
        assert "Worked on baseline cleanup." in prompt
        assert model == "gpt-5-mini"
        return (
            "gpt-5-mini",
            json.dumps(
                {
                    "source_path": str(transcript),
                    "summary": "Condensed summary.",
                    "highlights": ["moved baseline helpers"],
                    "raw_references": [],
                }
            ),
        )

    monkeypatch.setattr(session_summaries, "_run_codex_exec", fake_codex)
    output = tmp_path / "summary.json"

    result = session_summaries.summarise_session_transcript(
        transcript,
        output=output,
        model="gpt-5-mini",
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert result.wrote is True
    assert result.skipped is False
    assert result.backend == "codex-exec"
    assert payload["summary"] == "Condensed summary."
    assert str(transcript) in payload["raw_references"]


def test_summarise_session_transcript_skips_existing_output(tmp_path: Path) -> None:
    transcript = tmp_path / "conversation.md"
    transcript.write_text("hello\n", encoding="utf-8")
    output = tmp_path / "summary.json"
    output.write_text("{}", encoding="utf-8")

    result = session_summaries.summarise_session_transcript(
        transcript,
        output=output,
        force=False,
    )

    assert result.wrote is False
    assert result.skipped is True
    assert result.output_path == output
