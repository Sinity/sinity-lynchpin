from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

import typer

app = typer.Typer(help="Generate manifest JSONs for raw snapshot folders.")


def _iter_files(root: Path, include_hidden: bool) -> Iterable[Path]:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if not include_hidden and any(part.startswith(".") for part in rel.parts):
            continue
        yield path


def _sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_for_root(
    root: Path,
    hash_max_bytes: int,
    include_hidden: bool,
) -> dict:
    entries: List[dict] = []
    total_bytes = 0
    for path in sorted(_iter_files(root, include_hidden), key=lambda p: str(p)):
        stat = path.stat()
        size = stat.st_size
        total_bytes += size
        entry = {
            "path": str(path.relative_to(root)),
            "size": size,
            "mtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        }
        if hash_max_bytes > 0 and size <= hash_max_bytes:
            entry["sha256"] = _sha256(path)
        entries.append(entry)
    return {
        "root": str(root),
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "file_count": len(entries),
        "total_bytes": total_bytes,
        "hash_max_bytes": hash_max_bytes,
        "entries": entries,
    }


@app.command()
def manifest(
    roots: List[Path] = typer.Argument(..., help="Root directories to scan."),
    output: Optional[Path] = typer.Option(None, "--output", help="Write a single manifest here."),
    hash_max_mb: int = typer.Option(50, "--hash-max-mb", help="Hash files up to this size (MB)."),
    include_hidden: bool = typer.Option(False, "--include-hidden", help="Include dotfiles."),
) -> None:
    """Write MANIFEST.json files describing raw snapshot contents."""
    if output and len(roots) != 1:
        raise typer.BadParameter("--output requires a single root.")
    hash_max_bytes = max(hash_max_mb, 0) * 1024 * 1024
    for root in roots:
        if not root.exists():
            typer.secho(f"Skipping missing root: {root}", fg=typer.colors.YELLOW)
            continue
        if not root.is_dir():
            raise typer.BadParameter(f"Root must be a directory: {root}")
        payload = _manifest_for_root(root, hash_max_bytes, include_hidden)
        out_path = output or (root / "MANIFEST.json")
        out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        typer.secho(f"✓ Wrote manifest: {out_path}", fg=typer.colors.GREEN)


if __name__ == "__main__":
    app()
