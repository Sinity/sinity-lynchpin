from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, List, Optional

from ..core.config import get_config
from ..sources.indices.repos import GitRepository

_ENABLE_RE = re.compile(r"sinnix\.([A-Za-z0-9_.-]+)\.enable\s*=\s*(true|false)", re.MULTILINE)
_INSTRUMENTATION_KEYWORDS = {
    "asciinema",
    "audio",
    "screenshot",
    "screen",
    "instrument",
    "capture",
    "telemetry",
    "recorder",
}


@dataclass
class FeatureToggle:
    """Represents a `sinnix.*.enable = true|false` entry inside a host definition."""

    key: str
    enabled: bool


@dataclass
class SinnixHost:
    """High-level view of Sinnix host definitions (`hosts/<name>/default.nix`)."""

    name: str
    path: Path
    last_modified: datetime
    toggles: List[FeatureToggle]
    preview: str


@dataclass
class SinnixModule:
    """Metadata for feature/service modules under `modules/`."""

    name: str
    category: str
    path: Path
    last_modified: datetime
    description: Optional[str]
    is_instrumentation: bool


def iter_hosts(root: Optional[Path] = None) -> Iterator[SinnixHost]:
    """Iterate over Sinnix hosts and return the options they enable."""

    cfg = get_config()
    host_root = Path(root) if root else cfg.sinnix_root / "hosts"
    if not host_root.exists():
        return iter(())

    def generator() -> Iterator[SinnixHost]:
        for node in sorted(host_root.iterdir()):
            candidate = node / "default.nix" if node.is_dir() else node
            if not candidate.exists() or not candidate.is_file():
                continue
            try:
                text = candidate.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            toggles = [
                FeatureToggle(key=match.group(1), enabled=(match.group(2) == "true"))
                for match in _ENABLE_RE.finditer(text)
            ]
            stat = candidate.stat()
            preview = "\n".join(text.splitlines()[:40])
            yield SinnixHost(
                name=node.name if node.is_dir() else node.stem,
                path=candidate,
                last_modified=datetime.fromtimestamp(stat.st_mtime),
                toggles=toggles,
                preview=preview,
            )

    return generator()


def iter_modules(root: Optional[Path] = None) -> Iterator[SinnixModule]:
    """List every module under `/modules` with light metadata."""

    cfg = get_config()
    modules_root = Path(root) if root else cfg.sinnix_root / "modules"
    if not modules_root.exists():
        return iter(())

    def generator() -> Iterator[SinnixModule]:
        for path in sorted(modules_root.rglob("*.nix")):
            if not path.is_file():
                continue
            rel = path.relative_to(modules_root)
            category = rel.parts[0] if len(rel.parts) > 1 else rel.stem
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            description = _leading_comment(text)
            stat = path.stat()
            lowered = text.lower()
            is_instrumentation = any(keyword in lowered for keyword in _INSTRUMENTATION_KEYWORDS)
            yield SinnixModule(
                name=rel.stem,
                category=category,
                path=path,
                last_modified=datetime.fromtimestamp(stat.st_mtime),
                description=description,
                is_instrumentation=is_instrumentation,
            )

    return generator()


def instrumentation_modules() -> List[SinnixModule]:
    """Convenience helper returning only modules that mention capture/instrumentation keywords."""

    return [module for module in iter_modules() if module.is_instrumentation]


@dataclass
class SinnixDocument:
    """Structured record for notable Sinnix documents."""

    path: Path
    title: str
    body: str
    updated_at: datetime


def load_doc(relative_path: str | Path) -> Optional[SinnixDocument]:
    """Load any Markdown document relative to the Sinnix repo root."""

    cfg = get_config()
    repo = GitRepository(cfg.sinnix_root)
    doc_path = cfg.sinnix_root / Path(relative_path)
    if not doc_path.exists():
        return None
    try:
        content = repo.read_file(str(Path(relative_path)))
    except Exception:
        content = None
    if not content:
        content = doc_path.read_text(encoding="utf-8")
    title = _first_heading(content) or doc_path.name
    updated = datetime.fromtimestamp(doc_path.stat().st_mtime)
    return SinnixDocument(path=doc_path, title=title, body=content, updated_at=updated)


def _leading_comment(text: str) -> Optional[str]:
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if lines:
                lines.append("")
            continue
        if stripped.startswith("#"):
            lines.append(stripped.lstrip("# ").rstrip())
            continue
        if stripped.startswith("//"):
            lines.append(stripped.lstrip("/ ").rstrip())
            continue
        if stripped.startswith("/*"):
            cleaned = stripped.lstrip("/* ").rstrip(" */")
            lines.append(cleaned)
            continue
        break
    summary = " ".join(part for part in lines if part).strip()
    return summary or None


def _first_heading(text: str) -> Optional[str]:
    for line in text.splitlines():
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return None
