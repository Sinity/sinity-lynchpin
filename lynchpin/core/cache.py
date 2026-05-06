"""Cache helpers for lazy, read-only Lynchpin readers.

`persistent_cache` stores cachew sqlite files under the configured
knowledgebase-backed runtime cache. Use it only for deterministic readers:
source modules remain read-only, while explicit writes and network refreshes
belong under `lynchpin.analysis` or `lynchpin.scripts`.

Prefer `file_signature` / `files_signature` when path, mtime, and size are
enough to invalidate cached results. Use `file_digest` / `files_digest` when
content-based invalidation matters, for example append-only NDJSON or raw export
trees where mtimes alone are too weak.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any, Callable, Iterable, ParamSpec, Tuple, TypeVar, cast

from .config import get_config

_cachew: Any
try:
    from cachew import cachew as _cachew
except ImportError:  # pragma: no cover - exercised when running outside devshell
    _cachew = None

P = ParamSpec("P")
T = TypeVar("T")


def persistent_cache(
    name: str,
    *,
    depends_on: Callable[P, object] | None = None,
    chunk_by: int | None = None,
    logger: logging.Logger | None = None,
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Cachew adapter that stores sqlite caches under the configured runtime cache."""

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        if _cachew is None:
            return func

        def cache_path(*args: P.args, **kwargs: P.kwargs) -> Path:
            cfg = get_config()
            return cfg.cache_dir / f"{name}.sqlite"

        kwargs: dict[str, object] = {"cache_path": cache_path}
        if depends_on is not None:
            kwargs["depends_on"] = depends_on
        if chunk_by is not None:
            kwargs["chunk_by"] = chunk_by
        if logger is not None:
            kwargs["logger"] = logger

        return cast(Callable[P, T], _cachew(**kwargs)(func))

    return decorator


def file_signature(path: Path) -> Tuple[str, int | None, int | None]:
    """Return a tuple that changes whenever the file path/mtime/size changes."""
    if not path.exists():
        return (str(path), None, None)
    stat = path.stat()
    return (str(path), stat.st_mtime_ns, stat.st_size)


def files_signature(paths: Iterable[Path]) -> Tuple[Tuple[str, int | None, int | None], ...]:
    return tuple(file_signature(path) for path in paths)


def file_digest(path: Path, *, chunk_size: int = 1024 * 1024) -> Tuple[str, int | None, int | None, str | None]:
    """Return (path, mtime_ns, size, digest) for content-based invalidation."""
    if not path.exists():
        return (str(path), None, None, None)
    stat = path.stat()
    hasher = hashlib.blake2b()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            hasher.update(chunk)
    return (str(path), stat.st_mtime_ns, stat.st_size, hasher.hexdigest())


def files_digest(paths: Iterable[Path]) -> Tuple[Tuple[str, int | None, int | None, str | None], ...]:
    ordered = sorted((Path(path) for path in paths), key=lambda p: str(p))
    return tuple(file_digest(path) for path in ordered)


def write_text_if_changed(path: Path, text: str, *, encoding: str = "utf-8") -> bool:
    """Write text to file only if content differs from existing file.

    Returns True if file was written, False if content was unchanged.
    Creates parent directories as needed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: str | None = None
    if path.exists():
        try:
            existing = path.read_text(encoding=encoding)
        except OSError:
            existing = None
    if existing == text:
        return False
    path.write_text(text, encoding=encoding)
    return True
