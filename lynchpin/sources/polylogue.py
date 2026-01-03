from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, List, Optional

from ..core.cache import files_signature, persistent_cache
from ..core.config import get_config


@dataclass
class PolylogueDocument:
    provider: str
    path: Path
    modified_at: datetime
    size_bytes: int

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "path": str(self.path),
            "modified_at": self.modified_at.isoformat(),
            "size_bytes": self.size_bytes,
        }


def _document_paths(provider: Optional[str]) -> List[Path]:
    cfg = get_config()
    root = cfg.polylogue_root
    if not root.exists():
        return []
    if provider:
        candidates = [root / provider]
    else:
        candidates = [child for child in root.iterdir() if child.is_dir()]
    files: List[Path] = []
    for provider_dir in candidates:
        if provider_dir.exists() and provider_dir.is_dir():
            files.extend(sorted(provider_dir.rglob("*.md")))
    return files


def _provider_key(provider: Optional[str]) -> str:
    return provider or "__all__"


@dataclass
class _PolylogueCacheRow:
    provider: str
    path: str
    modified_at: datetime
    size_bytes: int


@persistent_cache(
    "polylogue_documents",
    depends_on=lambda provider=None: (_provider_key(provider), files_signature(_document_paths(provider))),
)
def _load_documents(provider: Optional[str]) -> List[_PolylogueCacheRow]:
    cfg = get_config()
    root = cfg.polylogue_root
    rows: List[_PolylogueCacheRow] = []
    for path in _document_paths(provider):
        if not path.is_file():
            continue
        try:
            provider_name = path.relative_to(root).parts[0]
        except ValueError:
            provider_name = path.parent.name
        stat = path.stat()
        rows.append(
            _PolylogueCacheRow(
                provider=provider_name,
                path=str(path),
                modified_at=datetime.fromtimestamp(stat.st_mtime),
                size_bytes=stat.st_size,
            )
        )
    return rows


def iter_documents(provider: Optional[str] = None) -> Iterator[PolylogueDocument]:
    for row in _load_documents(provider):
        yield PolylogueDocument(
            provider=row.provider,
            path=Path(row.path),
            modified_at=row.modified_at,
            size_bytes=row.size_bytes,
        )
