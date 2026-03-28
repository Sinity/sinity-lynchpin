"""Knowledge-ledger builders for session and artefact registries.

Tracked registry inputs live under `config/knowledge/`:

- `config/knowledge/sessions/*.md` for curated session notes
- `config/knowledge/artefact_catalog.json` for the artefact catalog

The generated CSVs belong under `artefacts/knowledge/ledgers/`.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from ...core.cache import write_text_if_changed


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


@dataclass(frozen=True)
class SessionLedgerResult:
    output: Path
    row_count: int
    wrote: bool


@dataclass(frozen=True)
class SessionDocument:
    date: date
    provider: str
    label: str
    doc_path: Path
    source_files: tuple[str, ...]
    highlights: tuple[str, ...]
    next_actions: tuple[str, ...]
    last_modified: datetime


@dataclass(frozen=True)
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

    def to_row(self) -> list[str]:
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


@dataclass(frozen=True)
class ArtefactLedgerResult:
    output: Path
    artefact_count: int
    wrote: bool
    missing_artifacts: tuple[str, ...]


def build_session_records(
    sessions_dir: Path,
) -> list[SessionDocument]:
    if not sessions_dir.exists():
        raise ValueError(f"Sessions directory {sessions_dir} does not exist")
    return list(iter_session_documents_from(sessions_dir))


def iter_session_documents_from(sessions_dir: Path) -> Iterator[SessionDocument]:
    for path in sorted(sessions_dir.glob("*.md")):
        if path.name == "README.md":
            continue
        yield _parse_session_document(path)


_SESSION_DOC_RE = re.compile(r"(?P<date>\d{4}-\d{2}-\d{2})-(?P<provider>[a-z0-9-]+)$")
_BULLET_PREFIXES = ("- ", "* ")


def _parse_session_document(path: Path) -> SessionDocument:
    match = _SESSION_DOC_RE.match(path.stem)
    if not match:
        raise ValueError(f"Session doc filename must start with YYYY-MM-DD-provider: {path}")

    doc_date = date.fromisoformat(match.group("date"))
    provider = match.group("provider")
    text = path.read_text(encoding="utf-8")
    title = next((line[2:].strip() for line in text.splitlines() if line.startswith("# ")), path.stem)
    sections = _markdown_sections(text)
    source_files = tuple(_list_items(sections.get("Source Files", [])))
    highlights = tuple(_list_items(sections.get("Key Instructions Captured", []) or sections.get("Highlights", [])))
    next_actions = tuple(_list_items(sections.get("Next Actions", [])))
    last_modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return SessionDocument(
        date=doc_date,
        provider=provider,
        label=title,
        doc_path=path,
        source_files=source_files,
        highlights=highlights,
        next_actions=next_actions,
        last_modified=last_modified,
    )


def _markdown_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(line)
    return sections


def _list_items(lines: list[str]) -> list[str]:
    items: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith(_BULLET_PREFIXES):
            items.append(stripped[2:].strip())
            continue
        numbered = re.match(r"\d+\.\s+(.*)", stripped)
        if numbered:
            items.append(numbered.group(1).strip())
    return items


def write_session_ledger(
    *,
    sessions_dir: Path = Path("config/knowledge/sessions"),
    output: Path = Path("artefacts/knowledge/ledgers/session_index.csv"),
) -> SessionLedgerResult:
    records = build_session_records(sessions_dir)
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
    wrote = write_text_if_changed(output, buffer.getvalue())
    return SessionLedgerResult(output=output, row_count=len(records), wrote=wrote)


def load_catalog(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Catalog must be a list of artefact definitions")
    return payload


def validate_entry(entry: dict) -> None:
    missing = [field for field in REQUIRED_FIELDS if field not in entry]
    if missing:
        raise ValueError(f"Entry {entry.get('artifact_id')} missing {missing}")


def build_artefacts(entries: Iterable[dict], base_dir: Path) -> list[Artefact]:
    artefacts: list[Artefact] = []
    for entry in entries:
        validate_entry(entry)
        source_path = Path(entry["source_path"]).expanduser()
        exists = (
            (base_dir / source_path).exists()
            if not source_path.is_absolute()
            else source_path.exists()
        )
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


def write_artefact_ledger(
    *,
    catalog: Path = Path("config/knowledge/artefact_catalog.json"),
    output: Path = Path("artefacts/knowledge/ledgers/artefact_index.csv"),
    base_dir: Path | None = None,
) -> ArtefactLedgerResult:
    if not catalog.exists():
        raise ValueError(f"Catalog not found: {catalog}")

    entries = load_catalog(catalog)
    artefacts = build_artefacts(entries, base_dir=(base_dir or Path(".").resolve()))
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
    wrote = write_text_if_changed(output, buffer.getvalue())
    missing = tuple(sorted(a.artifact_id for a in artefacts if not a.exists))
    return ArtefactLedgerResult(
        output=output,
        artefact_count=len(artefacts),
        wrote=wrote,
        missing_artifacts=missing,
    )
