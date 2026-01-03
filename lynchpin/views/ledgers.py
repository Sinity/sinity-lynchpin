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
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import typer

from ..core.io import write_text_if_changed

app = typer.Typer(help="Ledger builders (session + artefact)", pretty_exceptions_show_locals=False)


# ---------------------------------------------------------------------------
# Session ledger
# ---------------------------------------------------------------------------


@dataclass
class SessionRecord:
    date: str
    provider: str
    label: str
    doc_path: Path
    source_files: List[str]
    highlights: List[str]
    next_actions: List[str]
    last_modified: datetime

    def to_row(self) -> Dict[str, str]:
        return {
            "date": self.date,
            "provider": self.provider,
            "label": self.label,
            "doc_path": str(self.doc_path),
            "source_files": " | ".join(self.source_files),
            "highlights": " || ".join(self.highlights),
            "next_actions": " || ".join(self.next_actions),
            "source_count": str(len(self.source_files)),
            "highlight_count": str(len(self.highlights)),
            "next_action_count": str(len(self.next_actions)),
            "last_modified": self.last_modified.isoformat(),
        }


def parse_markdown_sections(text: str) -> Dict[str, List[str]]:
    sections: Dict[str, List[str]] = {}
    current: Optional[str] = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if line.startswith("## "):
            current = line[3:].strip()
            sections[current] = []
            continue
        if current is not None:
            sections[current].append(line)
    return sections


def extract_bullets(lines: List[str]) -> List[str]:
    bullets: List[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("#", "```")):
            break
        if stripped.startswith(("- ", "* ")):
            bullets.append(_clean_inline(stripped[2:].strip()))
        elif stripped[:2].isdigit() and stripped[2:].lstrip().startswith("."):
            content = stripped.split(".", 1)[-1].strip()
            bullets.append(_clean_inline(content))
        elif stripped:
            if bullets:
                bullets[-1] = f"{bullets[-1]} {stripped}"
            else:
                bullets.append(_clean_inline(stripped))
    return [b for b in (bullet.strip() for bullet in bullets) if b]


def _clean_inline(value: str) -> str:
    cleaned = value.replace("`", "").replace("*", "")
    return " ".join(cleaned.split())


def parse_session_file(path: Path) -> SessionRecord:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    title = next((ln.lstrip("# ").strip() for ln in lines if ln.startswith("#")), path.stem)

    parts = path.stem.split("-")
    date_str = "-".join(parts[:3]) if len(parts) >= 3 else ""
    provider = parts[-1] if len(parts) >= 1 else "unknown"

    sections = parse_markdown_sections(text)
    source_files = extract_bullets(sections.get("Source Files", []))
    highlights = extract_bullets(sections.get("Highlights", []))
    next_actions = extract_bullets(sections.get("Next Actions", []))

    stat = path.stat()
    last_modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)

    return SessionRecord(
        date=date_str,
        provider=provider,
        label=title,
        doc_path=path,
        source_files=source_files,
        highlights=highlights,
        next_actions=next_actions,
        last_modified=last_modified,
    )


def _write_session_csv(records: List[SessionRecord], output: Path) -> bool:
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
        writer.writerow(record.to_row())
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

    records: List[SessionRecord] = []
    for path in sorted(sessions_dir.glob("*.md")):
        if path.name.startswith("."):
            continue
        records.append(parse_session_file(path))

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
