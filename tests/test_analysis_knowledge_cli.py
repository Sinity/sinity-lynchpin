from __future__ import annotations

from pathlib import Path

from lynchpin.analysis.knowledge import cli as knowledge_cli


def test_session_index_cli_dispatches_to_writer(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    class Result:
        output = Path("/tmp/session_index.csv")
        row_count = 7
        wrote = True

    def fake_write_session_ledger(**kwargs):
        captured.update(kwargs)
        return Result()

    monkeypatch.setattr(knowledge_cli, "write_session_ledger", fake_write_session_ledger)

    exit_code = knowledge_cli.main(
        [
            "session-index",
            "--sessions-dir",
            "docs/reference/sessions",
            "--output",
            "/tmp/session_index.csv",
        ]
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert captured["sessions_dir"] == Path("docs/reference/sessions")
    assert captured["output"] == Path("/tmp/session_index.csv")
    assert "Wrote 7 session rows to /tmp/session_index.csv" in out


def test_artefact_index_cli_reports_missing_artifacts(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    class Result:
        output = Path("/tmp/artefact_index.csv")
        artefact_count = 2
        wrote = False
        missing_artifacts = ("missing-a", "missing-b")

    def fake_write_artefact_ledger(**kwargs):
        captured.update(kwargs)
        return Result()

    monkeypatch.setattr(knowledge_cli, "write_artefact_ledger", fake_write_artefact_ledger)

    exit_code = knowledge_cli.main(
        [
            "artefact-index",
            "--catalog",
            "docs/reference/ledgers/artefact_catalog.json",
            "--output",
            "/tmp/artefact_index.csv",
            "--base-dir",
            "/realm/project/sinity-lynchpin",
        ]
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert captured["catalog"] == Path("docs/reference/ledgers/artefact_catalog.json")
    assert captured["output"] == Path("/tmp/artefact_index.csv")
    assert captured["base_dir"] == Path("/realm/project/sinity-lynchpin")
    assert "Reused 2 artefacts -> /tmp/artefact_index.csv (missing paths: missing-a, missing-b)" in out


def test_summarise_session_cli_passes_backend_and_force(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    class Result:
        output_path = Path("/tmp/summary.json")
        skipped = False

    def fake_summarise_session_transcript(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return Result()

    monkeypatch.setattr(knowledge_cli, "summarise_session_transcript", fake_summarise_session_transcript)

    exit_code = knowledge_cli.main(
        [
            "summarise-session",
            "--input",
            "/tmp/conversation.md",
            "--output",
            "/tmp/summary.json",
            "--model",
            "gpt-5-mini",
            "--backend",
            "claude-agent-sdk",
            "--max-chars",
            "12345",
            "--force",
            "true",
        ]
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert captured["args"] == (Path("/tmp/conversation.md"),)
    assert captured["kwargs"]["output"] == Path("/tmp/summary.json")
    assert captured["kwargs"]["model"] == "gpt-5-mini"
    assert captured["kwargs"]["backend"] == "claude-agent-sdk"
    assert captured["kwargs"]["max_chars"] == 12345
    assert captured["kwargs"]["force"] is True
    assert captured["kwargs"]["log"] is print
    assert "Summary written to /tmp/summary.json" in out
