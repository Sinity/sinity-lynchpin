"""Ledger builders for sessions and artefacts.

These helpers keep lightweight CSV indexes in sync with the Markdown session
notes and the curated artefact catalog. They replace the previous
`pipelines/knowledge/ledgers` scripts so other tooling can simply run
`python -m lynchpin.views.ledgers <command>`.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

import typer

from ..core.io import write_text_if_changed
from ..sources.indices import sessions

app = typer.Typer(help="Ledger builders (session + artefact)", pretty_exceptions_show_locals=False)


def _write_session_csv(records: List[sessions.SessionDocument], output: Path) -> bool:
    fieldnames = [
        "date",
        "provider",
        "label",
        "doc_path",
        "source_files",
        "highlights",
        "next_actions",
        "source_count",
        "highlight_count",
        "next_action_count",
        "last_modified",
    ]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for record in records:
        writer.writerow(
            {
                "date": record.date.isoformat(),
                "provider": record.provider,
                "label": record.label,
                "doc_path": str(record.doc_path),
                "source_files": " | ".join(record.source_files),
                "highlights": " || ".join(record.highlights),
                "next_actions": " || ".join(record.next_actions),
                "source_count": str(len(record.source_files)),
                "highlight_count": str(len(record.highlights)),
                "next_action_count": str(len(record.next_actions)),
                "last_modified": record.last_modified.isoformat(),
            }
        )
    return write_text_if_changed(output, buffer.getvalue())


# ---------------------------------------------------------------------------
# Artefact ledger
# ---------------------------------------------------------------------------


REQUIRED_FIELDS = [
    "artifact_id",
    "repo",
    "category",
    "description",
    "source_path",
    "owner",
    "refresh_method",
    "status",
]


@dataclass
class Artefact:
    artifact_id: str
    repo: str
    category: str
    description: str
    source_path: str
    owner: str
    refresh_method: str
    status: str
    exists: bool

    def to_row(self) -> List[str]:
        return [
            self.artifact_id,
            self.repo,
            self.category,
            self.description,
            self.source_path,
            self.owner,
            self.refresh_method,
            self.status,
        ]


def load_catalog(path: Path) -> List[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise typer.BadParameter("Catalog must be a list of artefact definitions")
    return payload


def validate_entry(entry: dict) -> None:
    missing = [field for field in REQUIRED_FIELDS if field not in entry]
    if missing:
        raise typer.BadParameter(f"Entry {entry.get('artifact_id')} missing {missing}")


def build_artefacts(entries: Iterable[dict], base_dir: Path) -> List[Artefact]:
    artefacts: List[Artefact] = []
    for entry in entries:
        validate_entry(entry)
        source_path = Path(entry["source_path"]).expanduser()
        exists = (base_dir / source_path).exists() if not source_path.is_absolute() else source_path.exists()
        artefacts.append(
            Artefact(
                artifact_id=entry["artifact_id"],
                repo=entry["repo"],
                category=entry["category"],
                description=entry["description"],
                source_path=entry["source_path"],
                owner=entry["owner"],
                refresh_method=entry["refresh_method"],
                status=entry["status"],
                exists=exists,
            )
        )
    return artefacts


def _write_artefact_csv(artefacts: List[Artefact], output: Path) -> bool:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        [
            "artifact_id",
            "repo",
            "category",
            "description",
            "source_path",
            "owner",
            "refresh_method",
            "status",
        ]
    )
    for artefact in sorted(artefacts, key=lambda a: a.artifact_id):
        writer.writerow(artefact.to_row())
    return write_text_if_changed(output, buffer.getvalue())


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


@app.command()
def session(
    sessions_dir: Path = typer.Option(Path("docs/reference/sessions"), "--sessions-dir", help="Directory containing session Markdown files"),
    output: Path = typer.Option(Path("artefacts/knowledge/ledgers/session_index.csv"), "--output", help="Destination CSV path"),
) -> None:
    """Materialise the session ledger CSV."""
    if not sessions_dir.exists():
        raise typer.BadParameter(f"Sessions directory {sessions_dir} does not exist")

    records = list(sessions.iter_session_documents_from(sessions_dir))

    wrote = _write_session_csv(records, output)
    if wrote:
        typer.secho(f"Wrote {len(records)} session rows to {output}", fg=typer.colors.GREEN)
    else:
        typer.secho(f"Session ledger unchanged at {output}", fg=typer.colors.YELLOW)


@app.command()
def artefact(
    catalog: Path = typer.Option(Path("docs/reference/ledgers/artefact_catalog.json"), "--catalog", help="Artefact catalog JSON"),
    output: Path = typer.Option(Path("artefacts/knowledge/ledgers/artefact_index.csv"), "--output", help="Destination CSV path"),
) -> None:
    """Materialise the artefact ledger CSV."""
    if not catalog.exists():
        raise typer.BadParameter(f"Catalog not found: {catalog}")

    entries = load_catalog(catalog)
    artefacts = build_artefacts(entries, base_dir=Path(".").resolve())
    wrote = _write_artefact_csv(artefacts, output)
    missing = [a.artifact_id for a in artefacts if not a.exists]
    if missing:
        typer.secho(
            f"{'Wrote' if wrote else 'Reused'} {len(artefacts)} artefacts → {output} (missing paths: {', '.join(missing)})",
            fg=typer.colors.YELLOW,
        )
    else:
        if wrote:
            typer.secho(f"Wrote {len(artefacts)} artefacts → {output}", fg=typer.colors.GREEN)
        else:
            typer.secho(f"Artefact ledger unchanged at {output}", fg=typer.colors.YELLOW)


if __name__ == "__main__":
    app()
