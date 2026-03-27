"""Narrative data types and canonical file I/O for retrospective artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import logging

from ..periods import hierarchical_relpath

log = logging.getLogger(__name__)

_NARRATIVE_DIR = Path("artefacts/retrospective/narratives")


class NarrativeKind(str, Enum):
    day = "day"
    week = "week"
    range = "range"
    month = "month"
    episode = "episode"
    quarter = "quarter"
    half = "half"
    year = "year"
    contrast = "contrast"


SCALE_HIERARCHY: tuple[NarrativeKind, ...] = (
    NarrativeKind.day,
    NarrativeKind.week,
    NarrativeKind.month,
    NarrativeKind.quarter,
    NarrativeKind.half,
    NarrativeKind.year,
)


@dataclass(frozen=True)
class Narrative:
    kind: str
    key: str
    text: str
    generated_at: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    backend: str = "unknown"
    session_id: str | None = None


def _write_narrative_file(
    narrative: Narrative,
    session_id: str | None = None,
    pass_num: int = 1,
    enrichment_sources: list[str] | None = None,
    evidence_bundle: str | None = None,
) -> None:
    """Write a narrative as canonical Markdown with YAML frontmatter."""
    path = _narrative_path(narrative.kind, narrative.key)

    prior_versions = []
    prior_fm = _read_frontmatter(path) if path.exists() else None
    if prior_fm:
        existing_prior = prior_fm.get("prior_versions")
        if isinstance(existing_prior, list):
            prior_versions = existing_prior
        prior_versions.append(
            {
                "generated_at": prior_fm.get("generated_at"),
                "pass": prior_fm.get("pass", 1),
                "session_id": prior_fm.get("session_id"),
            },
        )

    frontmatter = {
        "kind": narrative.kind,
        "key": narrative.key,
        "generated_at": narrative.generated_at,
        "model": narrative.model,
        "backend": narrative.backend,
        "session_id": session_id or narrative.session_id,
        "input_tokens": narrative.input_tokens,
        "output_tokens": narrative.output_tokens,
        "cost_usd": narrative.cost_usd,
        "pass": pass_num,
    }
    if enrichment_sources:
        frontmatter["enrichment_sources"] = enrichment_sources
    if evidence_bundle:
        frontmatter["evidence_bundle"] = evidence_bundle
    if prior_versions:
        frontmatter["prior_versions"] = prior_versions

    try:
        import yaml

        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            handle.write("---\n")
            yaml.dump(frontmatter, handle, default_flow_style=False, sort_keys=False, allow_unicode=True)
            handle.write("---\n\n")
            handle.write(narrative.text)
            if not narrative.text.endswith("\n"):
                handle.write("\n")
    except Exception as exc:
        log.warning("Failed to write narrative file %s: %s", path, exc)


def _read_frontmatter(file_path: Path) -> dict[str, object]:
    """Extract YAML frontmatter from a narrative file."""
    try:
        with file_path.open("r", encoding="utf-8") as handle:
            content = handle.read()
    except OSError as exc:
        log.warning("Could not read narrative frontmatter from %s: %s", file_path, exc)
        return {}
    if not content.startswith("---"):
        return {}
    end_idx = content.find("\n---\n", 4)
    if end_idx <= 0:
        return {}
    try:
        import yaml

        parsed = yaml.safe_load(content[4:end_idx])
    except Exception as exc:
        log.warning("Could not parse narrative frontmatter from %s: %s", file_path, exc)
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _read_narrative_body(file_path: Path) -> str | None:
    try:
        with file_path.open("r", encoding="utf-8") as handle:
            content = handle.read()
    except OSError as exc:
        log.warning("Failed to read narrative file %s: %s", file_path, exc)
        return None
    if not content.startswith("---"):
        return None
    end_idx = content.find("\n---\n", 4)
    if end_idx <= 0:
        return None
    return content[end_idx + 5:]


def _narrative_path(kind: str, key: str) -> Path:
    if kind.startswith("enhancement:"):
        pass_type = kind.split(":", 1)[1]
        return _NARRATIVE_DIR / "enhancements" / pass_type / f"{key}.md"
    path = _narrative_hierarchical_path(kind, key)
    if path is None:
        raise ValueError(f"No canonical narrative path for kind={kind!r} key={key!r}")
    return path


def _narrative_hierarchical_path(kind: str, key: str) -> Path | None:
    rel = hierarchical_relpath(kind, key)
    return (_NARRATIVE_DIR / rel) if rel is not None else None


def load_narratives(kind: str, keys: list[str]) -> dict[str, str]:
    """Load narrative text from the canonical hierarchical file tree."""
    results: dict[str, str] = {}
    for key in keys:
        try:
            path = _narrative_path(kind, key)
        except ValueError:
            continue
        body = _read_narrative_body(path)
        if body is not None:
            results[key] = body.lstrip("\n")
    return results
