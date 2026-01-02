from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, List, Optional

from .config import get_config


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


def iter_documents(provider: Optional[str] = None) -> Iterator[PolylogueDocument]:
    cfg = get_config()
    root = cfg.polylogue_root
    if not root.exists():
        return iter(())

    def generator() -> Iterator[PolylogueDocument]:
        providers: List[Path]
        if provider:
            providers = [root / provider]
        else:
            providers = [child for child in root.iterdir() if child.is_dir()]
        for provider_dir in providers:
            if not provider_dir.exists() or not provider_dir.is_dir():
                continue
            provider_name = provider_dir.name
            for path in provider_dir.rglob("*.md"):
                if not path.is_file():
                    continue
                stat = path.stat()
                yield PolylogueDocument(
                    provider=provider_name,
                    path=path,
                    modified_at=datetime.fromtimestamp(stat.st_mtime),
                    size_bytes=stat.st_size,
                )

    return generator()
