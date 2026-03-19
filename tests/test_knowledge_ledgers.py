from __future__ import annotations

import json
from pathlib import Path

from lynchpin.analysis.knowledge.ledgers import (
    build_artefacts,
    write_artefact_ledger,
    write_session_ledger,
)


def test_write_session_ledger_exports_csv(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    (sessions_dir / "2026-03-19-codex-demo.md").write_text(
        "\n".join(
            [
                "---",
                "provider: codex",
                "label: Demo Session",
                "date: 2026-03-19",
                "---",
                "",
                "# Demo Session",
                "",
                "## Source Files",
                "- /tmp/source-a.md",
                "",
                "## Highlights",
                "- cleaned command surface",
                "",
                "## Next Actions",
                "- remove dead wrappers",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    output = tmp_path / "session_index.csv"
    result = write_session_ledger(sessions_dir=sessions_dir, output=output)

    text = output.read_text(encoding="utf-8")
    assert result.wrote is True
    assert result.row_count == 1
    assert "Demo Session" in text
    assert "cleaned command surface" in text


def test_write_artefact_ledger_reports_missing_paths(tmp_path: Path) -> None:
    existing = tmp_path / "artefacts" / "example.txt"
    existing.parent.mkdir(parents=True)
    existing.write_text("ok\n", encoding="utf-8")
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            [
                {
                    "artifact_id": "existing",
                    "repo": "sinity-lynchpin",
                    "category": "report",
                    "description": "Existing artefact",
                    "source_path": "artefacts/example.txt",
                    "owner": "lynchpin",
                    "refresh_method": "just velocity",
                    "status": "active",
                },
                {
                    "artifact_id": "missing",
                    "repo": "sinity-lynchpin",
                    "category": "report",
                    "description": "Missing artefact",
                    "source_path": "artefacts/missing.txt",
                    "owner": "lynchpin",
                    "refresh_method": "just project-bundles",
                    "status": "active",
                },
            ],
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    result = write_artefact_ledger(
        catalog=catalog,
        output=tmp_path / "artefact_index.csv",
        base_dir=tmp_path,
    )

    assert result.wrote is True
    assert result.artefact_count == 2
    assert result.missing_artifacts == ("missing",)
    csv_text = result.output.read_text(encoding="utf-8")
    assert "existing" in csv_text
    assert "missing" in csv_text


def test_build_artefacts_rejects_missing_required_fields(tmp_path: Path) -> None:
    try:
        build_artefacts([{"artifact_id": "broken"}], base_dir=tmp_path)
    except ValueError as exc:
        assert "missing" in str(exc)
    else:
        raise AssertionError("expected missing required fields to fail")
