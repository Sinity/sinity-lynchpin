"""Google Takeout raw archive inventory and typed member access."""

from __future__ import annotations

import tarfile
import tempfile
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from ..core.config import get_config
from .takeout_chrome import iter_takeout_chrome_visits
from .web import WebHistoryVisit

__all__ = [
    "TakeoutArchive",
    "TakeoutChromeHistoryBatch",
    "TakeoutMember",
    "archive_inventory",
    "discover_takeout_archives",
    "iter_archive_members",
    "iter_member_bytes",
    "iter_chrome_history_batches",
    "product_counts",
]


@dataclass(frozen=True)
class TakeoutArchive:
    path: Path
    size_bytes: int
    member_count: int
    total_member_bytes: int
    product_counts: tuple[tuple[str, int], ...]
    chrome_history_members: int


@dataclass(frozen=True)
class TakeoutMember:
    archive: Path
    path: str
    product: str
    size_bytes: int


@dataclass(frozen=True)
class TakeoutChromeHistoryBatch:
    archive: Path
    member: str
    visits: tuple[WebHistoryVisit, ...]


def discover_takeout_archives(root: Path | None = None) -> tuple[Path, ...]:
    cfg = get_config()
    root = root or cfg.exports_root / "google/raw/takeout"
    if not root.exists():
        return ()
    return tuple(
        sorted(path for path in root.iterdir() if path.is_file() and _is_supported_archive(path))
    )


def archive_inventory(root: Path | None = None) -> tuple[TakeoutArchive, ...]:
    rows: list[TakeoutArchive] = []
    for archive in discover_takeout_archives(root):
        members = tuple(iter_archive_members(archive))
        counts = product_counts(members)
        rows.append(
            TakeoutArchive(
                path=archive,
                size_bytes=archive.stat().st_size,
                member_count=len(members),
                total_member_bytes=sum(member.size_bytes for member in members),
                product_counts=tuple(sorted(counts.items())),
                chrome_history_members=sum(1 for member in members if is_chrome_history_member(member.path)),
            )
        )
    return tuple(rows)


def product_counts(members: tuple[TakeoutMember, ...]) -> Counter[str]:
    return Counter(member.product for member in members)


def iter_archive_members(path: Path) -> Iterator[TakeoutMember]:
    if path.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(path) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    member_path = _normalize_member(info.filename)
                    yield TakeoutMember(
                        archive=path,
                        path=member_path,
                        product=_takeout_product(member_path),
                        size_bytes=info.file_size,
                    )
        except (OSError, zipfile.BadZipFile):
            return
        return

    try:
        with tarfile.open(path, mode="r:*") as tf:
            for member in tf.getmembers():
                if not member.isfile():
                    continue
                member_path = _normalize_member(member.name)
                yield TakeoutMember(
                    archive=path,
                    path=member_path,
                    product=_takeout_product(member_path),
                    size_bytes=member.size,
                )
    except (OSError, tarfile.TarError):
        return


def iter_member_bytes(
    *,
    root: Path | None = None,
    products: set[str] | None = None,
    suffixes: set[str] | None = None,
) -> Iterator[tuple[TakeoutMember, bytes]]:
    """Yield selected Takeout member payloads from raw archives.

    This is for canonical materializers that need source bytes while keeping raw
    archives authoritative. Callers should keep selections narrow; large media
    products are better represented through inventory rows.
    """
    normalized_suffixes = {suffix.lower() for suffix in suffixes or ()}
    for archive in discover_takeout_archives(root):
        if archive.suffix.lower() == ".zip":
            yield from _zip_member_bytes(archive, products=products, suffixes=normalized_suffixes)
        else:
            yield from _tar_member_bytes(archive, products=products, suffixes=normalized_suffixes)


def _selected_member(member: TakeoutMember, *, products: set[str] | None, suffixes: set[str]) -> bool:
    if products is not None and member.product not in products:
        return False
    if suffixes and Path(member.path).suffix.lower() not in suffixes:
        return False
    return True


def _zip_member_bytes(
    archive: Path,
    *,
    products: set[str] | None,
    suffixes: set[str],
) -> Iterator[tuple[TakeoutMember, bytes]]:
    try:
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                member_path = _normalize_member(info.filename)
                member = TakeoutMember(
                    archive=archive,
                    path=member_path,
                    product=_takeout_product(member_path),
                    size_bytes=info.file_size,
                )
                if _selected_member(member, products=products, suffixes=suffixes):
                    yield member, zf.read(info)
    except (OSError, zipfile.BadZipFile):
        return


def _tar_member_bytes(
    archive: Path,
    *,
    products: set[str] | None,
    suffixes: set[str],
) -> Iterator[tuple[TakeoutMember, bytes]]:
    try:
        with tarfile.open(archive, mode="r:*") as tf:
            for item in tf.getmembers():
                if not item.isfile():
                    continue
                member_path = _normalize_member(item.name)
                member = TakeoutMember(
                    archive=archive,
                    path=member_path,
                    product=_takeout_product(member_path),
                    size_bytes=item.size,
                )
                if not _selected_member(member, products=products, suffixes=suffixes):
                    continue
                handle = tf.extractfile(item)
                if handle is not None:
                    yield member, handle.read()
    except (OSError, tarfile.TarError):
        return


def iter_chrome_history_batches(root: Path | None = None) -> Iterator[TakeoutChromeHistoryBatch]:
    for archive in discover_takeout_archives(root):
        if archive.suffix.lower() == ".zip":
            yield from _zip_chrome_history_batches(archive)
        else:
            yield from _tar_chrome_history_batches(archive)


def is_chrome_history_member(name: str) -> bool:
    normalized_name = name.replace("\\", "/")
    normalized = f"/{normalized_name}"
    return normalized.endswith(("/Takeout/Chrome/History.json", "/Takeout/Chrome/BrowserHistory.json"))


def _zip_chrome_history_batches(path: Path) -> Iterator[TakeoutChromeHistoryBatch]:
    try:
        with zipfile.ZipFile(path) as zf:
            for name in sorted(zf.namelist()):
                member_name = _normalize_member(name)
                if not is_chrome_history_member(member_name):
                    continue
                with tempfile.NamedTemporaryFile("wb", suffix=".json") as tmp:
                    tmp.write(zf.read(name))
                    tmp.flush()
                    visits = tuple(iter_takeout_chrome_visits(
                        Path(tmp.name),
                        source_label=f"takeout_chrome:{path.name}:{member_name}",
                    ))
                yield TakeoutChromeHistoryBatch(path, member_name, visits)
    except (OSError, zipfile.BadZipFile):
        return


def _tar_chrome_history_batches(path: Path) -> Iterator[TakeoutChromeHistoryBatch]:
    try:
        with tarfile.open(path, mode="r:*") as tf:
            for member in sorted(tf.getmembers(), key=lambda item: item.name):
                member_name = _normalize_member(member.name)
                if not member.isfile() or not is_chrome_history_member(member_name):
                    continue
                handle = tf.extractfile(member)
                if handle is None:
                    continue
                with tempfile.NamedTemporaryFile("wb", suffix=".json") as tmp:
                    tmp.write(handle.read())
                    tmp.flush()
                    visits = tuple(iter_takeout_chrome_visits(
                        Path(tmp.name),
                        source_label=f"takeout_chrome:{path.name}:{member_name}",
                    ))
                yield TakeoutChromeHistoryBatch(path, member_name, visits)
    except (OSError, tarfile.TarError):
        return


def _normalize_member(value: str) -> str:
    normalized = value.replace("\\", "/").lstrip("./")
    index = normalized.find("/Takeout/")
    if index >= 0:
        return normalized[index + 1 :]
    return normalized


def _takeout_product(value: str) -> str:
    parts = value.split("/")
    if len(parts) >= 2 and parts[0] == "Takeout":
        return parts[1]
    return "unknown"


def _is_supported_archive(path: Path) -> bool:
    if path.suffix.lower() == ".zip":
        return True
    suffixes = "".join(path.suffixes).lower()
    return suffixes.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz", ".tar.zst", ".tzst"))
