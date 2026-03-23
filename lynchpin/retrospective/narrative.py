"""Narrative data types and I/O for trajectory retrospectives.

Narratives are stored in two formats:
1. **Files**: Markdown with YAML frontmatter under artefacts/retrospective/narratives/
   - days/YYYY-MM-DD.md, weeks/YYYY-WNN.md, months/YYYY-MM.md, quarters/YYYY-QN.md
   - Primary source; includes metadata (session_id, tokens, pass number, prior_versions)

2. **JSONL Logs**: Legacy format in artefacts/retrospective/narratives/logs/ (for warehouse)
   - Written alongside files by _log_narrative()

Use _write_narrative_file() to create/update narrative files.
Use load_narratives() to read narratives (checks files first, falls back to JSONL).
Use migrate_jsonl_to_files() to backfill files from existing JSONL logs.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

log = logging.getLogger(__name__)

_NARRATIVE_LOG_DIR = Path("artefacts/retrospective/narratives/logs")
_NARRATIVE_DIR = Path("artefacts/retrospective/narratives")


class NarrativeKind(str, Enum):
    day = "day"
    week = "week"
    range = "range"
    month = "month"
    episode = "episode"
    quarter = "quarter"
    contrast = "contrast"


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


# ---------------------------------------------------------------------------
# Narrative file I/O
# ---------------------------------------------------------------------------


def _write_narrative_file(
    narrative: Narrative,
    session_id: str | None = None,
    pass_num: int = 1,
    enrichment_sources: list[str] | None = None,
) -> None:
    """Write narrative as a Markdown file with YAML frontmatter.

    Determines subdirectory from narrative.kind: "day" -> "days/", "week" -> "weeks/",
    "month" -> "months/", "quarter" -> "quarters/", or "enhancement:X" -> "enhancements/X/".
    """
    try:
        # Determine subdirectory
        if narrative.kind.startswith("enhancement:"):
            pass_type = narrative.kind.split(":", 1)[1]
            subdir = _NARRATIVE_DIR / "enhancements" / pass_type
        else:
            kind_map = {"day": "days", "week": "weeks", "month": "months", "quarter": "quarters"}
            subdir = _NARRATIVE_DIR / kind_map.get(narrative.kind, narrative.kind)

        subdir.mkdir(parents=True, exist_ok=True)
        file_path = subdir / f"{narrative.key}.md"

        # Build frontmatter
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

        # Check for existing file to preserve prior versions
        prior_versions = []
        if file_path.exists():
            try:
                with file_path.open("r", encoding="utf-8") as f:
                    content = f.read()
                    # Extract existing frontmatter
                    if content.startswith("---"):
                        end_idx = content.find("\n---\n", 4)
                        if end_idx > 0:
                            import yaml
                            existing_fm = yaml.safe_load(content[4:end_idx])
                            if existing_fm and "prior_versions" in existing_fm:
                                prior_versions = existing_fm["prior_versions"]
                            # Add current version to prior_versions
                            prior_versions.append({
                                "generated_at": existing_fm.get("generated_at"),
                                "pass": existing_fm.get("pass", 1),
                                "session_id": existing_fm.get("session_id"),
                            })
            except Exception as exc:
                log.warning("Could not read prior versions from %s: %s", file_path, exc)

        if prior_versions:
            frontmatter["prior_versions"] = prior_versions

        # Write file
        import yaml
        with file_path.open("w", encoding="utf-8") as f:
            f.write("---\n")
            yaml.dump(frontmatter, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
            f.write("---\n\n")
            f.write(narrative.text)
            if not narrative.text.endswith("\n"):
                f.write("\n")
    except Exception as exc:
        log.warning("Failed to write narrative file: %s", exc)


def _log_narrative(narrative: Narrative) -> None:
    # Write file
    _write_narrative_file(narrative, pass_num=1)

    # Also write to JSONL log for warehouse compatibility
    log_dir = _NARRATIVE_LOG_DIR
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"narrative_{narrative.generated_at[:10]}.jsonl"
        entry = {
            "kind": narrative.kind,
            "key": narrative.key,
            "generated_at": narrative.generated_at,
            "backend": narrative.backend,
            "model": narrative.model,
            "input_tokens": narrative.input_tokens,
            "output_tokens": narrative.output_tokens,
            "cost_usd": narrative.cost_usd,
            "text": narrative.text,
            "session_id": narrative.session_id,
        }
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("Failed to write narrative log: %s", exc)


# ---------------------------------------------------------------------------
# Narrative loading / migration
# ---------------------------------------------------------------------------


def load_narratives(kind: str, keys: list[str]) -> dict[str, str]:
    """Load the most recent narrative text for each *(kind, key)* pair.

    First checks for narrative files in the file tree. Falls back to JSONL logs
    for keys not found as files. For duplicate JSONL entries the latest by
    ``generated_at`` wins.
    """
    if not keys:
        return {}
    target_keys = set(keys)
    results: dict[str, str] = {}

    # Map kind to subdirectory
    kind_map = {"day": "days", "week": "weeks", "month": "months", "quarter": "quarters"}
    subdir_name = kind_map.get(kind)

    # Try to load from files first
    if subdir_name:
        subdir = _NARRATIVE_DIR / subdir_name
        for key in list(target_keys):
            file_path = subdir / f"{key}.md"
            if file_path.exists():
                try:
                    with file_path.open("r", encoding="utf-8") as f:
                        content = f.read()
                        # Skip YAML frontmatter
                        if content.startswith("---"):
                            end_idx = content.find("\n---\n", 4)
                            if end_idx > 0:
                                body = content[end_idx + 5:]  # Skip second ---\n
                                results[key] = body.lstrip("\n")
                                target_keys.discard(key)
                except Exception as exc:
                    log.warning("Failed to read narrative file %s: %s", file_path, exc)

    # Fall back to JSONL log for remaining keys
    log_dir = _NARRATIVE_LOG_DIR
    if log_dir.exists() and target_keys:
        jsonl_results: dict[str, tuple[str, str]] = {}  # key -> (generated_at, text)
        for log_path in sorted(log_dir.glob("narrative_*.jsonl")):
            try:
                with log_path.open(encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if entry.get("kind") != kind:
                            continue
                        entry_key = entry.get("key", "")
                        if entry_key not in target_keys:
                            continue
                        gen_at = entry.get("generated_at", "")
                        text = entry.get("text", "")
                        if not text:
                            continue
                        prev = jsonl_results.get(entry_key)
                        if prev is None or gen_at > prev[0]:
                            jsonl_results[entry_key] = (gen_at, text)
            except OSError:
                continue
        results.update({k: v[1] for k, v in jsonl_results.items()})

    return results


def migrate_jsonl_to_files() -> None:
    """Migrate narratives from JSONL logs to Markdown files.

    Reads all JSONL log entries and writes them as files. Only writes a file
    if it doesn't already exist (preserves newer file versions).
    """
    log_dir = _NARRATIVE_LOG_DIR
    if not log_dir.exists():
        return

    migrated_count = 0
    skipped_count = 0

    for log_path in sorted(log_dir.glob("narrative_*.jsonl")):
        try:
            with log_path.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # Check if file already exists
                    kind = entry.get("kind", "")
                    key = entry.get("key", "")
                    if not kind or not key:
                        continue

                    kind_map = {"day": "days", "week": "weeks", "month": "months", "quarter": "quarters"}
                    subdir_name = kind_map.get(kind)
                    if not subdir_name:
                        continue

                    file_path = _NARRATIVE_DIR / subdir_name / f"{key}.md"
                    if file_path.exists():
                        skipped_count += 1
                        continue

                    # Create Narrative and write file
                    narrative = Narrative(
                        kind=kind,
                        key=key,
                        text=entry.get("text", ""),
                        generated_at=entry.get("generated_at", ""),
                        model=entry.get("model", ""),
                        input_tokens=entry.get("input_tokens", 0),
                        output_tokens=entry.get("output_tokens", 0),
                        cost_usd=entry.get("cost_usd", 0.0),
                        backend=entry.get("backend", "unknown"),
                        session_id=entry.get("session_id"),
                    )
                    _write_narrative_file(narrative, pass_num=1)
                    migrated_count += 1
        except OSError as exc:
            log.warning("Failed to read JSONL log %s: %s", log_path, exc)

    log.info("Migration complete: %d migrated, %d skipped (already exist)", migrated_count, skipped_count)
