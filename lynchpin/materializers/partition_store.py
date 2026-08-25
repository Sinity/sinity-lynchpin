"""Immutable, content-addressed artifacts for partitioned materialization.

The store deliberately knows nothing about DuckDB schemas or serving
connections.  It publishes bytes first and lets a manifest select those bytes
for a generation.  This makes NDJSON and Parquet clients share the same
interruption-safe publication boundary.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO, Callable, Iterable, Literal, Mapping, cast

PartitionScheme = Literal["day", "month", "entity", "singleton"]
_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._=-]*$")


def _safe(value: str, label: str) -> str:
    if not value or value in {".", ".."} or not _SAFE.fullmatch(value):
        raise ValueError(f"unsafe {label}: {value!r}")
    return value


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


@dataclass(frozen=True, slots=True)
class ProductPartitionKey:
    """Stable logical identity of one product partition."""

    product: str
    scheme: PartitionScheme
    value: str

    def __post_init__(self) -> None:
        _safe(self.product, "product")
        if self.scheme not in {"day", "month", "entity", "singleton"}:
            raise ValueError(f"unknown partition scheme: {self.scheme!r}")
        _safe(self.value, "partition value")

    @classmethod
    def day(cls, product: str, value: date | str) -> ProductPartitionKey:
        return cls(product, "day", value.isoformat() if isinstance(value, date) else value)

    @classmethod
    def month(cls, product: str, value: date | str) -> ProductPartitionKey:
        raw = value.strftime("%Y-%m") if isinstance(value, date) else value
        return cls(product, "month", raw)

    @classmethod
    def entity(cls, product: str, entity: str) -> ProductPartitionKey:
        return cls(product, "entity", entity)

    @classmethod
    def singleton(cls, product: str) -> ProductPartitionKey:
        return cls(product, "singleton", "singleton")

    @property
    def path(self) -> str:
        return f"{self.scheme}/{self.value}"


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    key: ProductPartitionKey
    digest: str
    path: str
    format: str
    byte_count: int
    row_count: int | None = None
    first_date: date | None = None
    last_date: date | None = None
    input_digest: str | None = None
    generations: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[0-9a-f]{64}", self.digest):
            raise ValueError("artifact digest must be a SHA-256 hex digest")
        if self.byte_count < 0 or (self.row_count is not None and self.row_count < 0):
            raise ValueError("artifact counts cannot be negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": {"product": self.key.product, "scheme": self.key.scheme, "value": self.key.value},
            "digest": self.digest,
            "path": self.path,
            "format": self.format,
            "byte_count": self.byte_count,
            "row_count": self.row_count,
            "first_date": _iso(self.first_date),
            "last_date": _iso(self.last_date),
            "input_digest": self.input_digest,
            "generations": list(self.generations),
            "created_at": self.created_at.astimezone(timezone.utc).isoformat(),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ArtifactRef:
        key_raw = raw["key"]
        return cls(
            key=ProductPartitionKey(
                str(key_raw["product"]),
                cast(PartitionScheme, str(key_raw["scheme"])),
                str(key_raw["value"]),
            ),
            digest=str(raw["digest"]), path=str(raw["path"]), format=str(raw["format"]),
            byte_count=int(raw["byte_count"]), row_count=None if raw.get("row_count") is None else int(raw["row_count"]),
            first_date=None if raw.get("first_date") is None else date.fromisoformat(str(raw["first_date"])),
            last_date=None if raw.get("last_date") is None else date.fromisoformat(str(raw["last_date"])),
            input_digest=None if raw.get("input_digest") is None else str(raw["input_digest"]),
            generations=tuple(str(item) for item in raw.get("generations", [])),
            created_at=datetime.fromisoformat(str(raw["created_at"])),
        )


def deterministic_input_digest(inputs: Iterable[Any]) -> str:
    """Hash typed, length-framed input values deterministically."""
    digest = hashlib.sha256()
    for item in inputs:
        if isinstance(item, Path):
            payload = b"path\0" + str(item).encode() + b"\0" + item.read_bytes()
        elif isinstance(item, bytes):
            payload = b"bytes\0" + item
        else:
            payload = b"json\0" + json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


class ArtifactStore:
    """Publish immutable partition bytes and atomically update their manifest."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.artifacts_root = root / "artifacts"
        self.manifest_path = root / "manifest.json"

    def _artifact_path(self, key: ProductPartitionKey, digest: str, format: str) -> Path:
        _safe(format, "format")
        return self.artifacts_root / key.product / key.scheme / key.value / f"{digest}.{format}"

    def put(
        self, key: ProductPartitionKey, data: bytes | bytearray | str | Path | Iterable[bytes] | Callable[[BinaryIO], None] | Any,
        *, format: str | None = None, input_digest: str | None = None, row_count: int | None = None,
        first_date: date | None = None, last_date: date | None = None, generations: Iterable[str] = (),
    ) -> ArtifactRef:
        """Stage, fsync, and publish bytes; reuse an existing digest exactly."""
        chosen_format = format or ("parquet" if hasattr(data, "write_parquet") else "bin")
        _safe(chosen_format, "format")
        staging_dir = self.root / ".staging"
        staging_dir.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix="artifact-", dir=staging_dir)
        temp_path = Path(temp_name)
        try:
            if hasattr(data, "write_parquet") and chosen_format == "parquet":
                os.close(fd)
                data.write_parquet(str(temp_path))
                _fsync_file(temp_path)
            else:
                with os.fdopen(fd, "wb") as output:
                    if isinstance(data, (bytes, bytearray)):
                        output.write(data)
                    elif isinstance(data, str):
                        output.write(data.encode())
                    elif isinstance(data, Path):
                        output.write(data.read_bytes())
                    elif callable(data):
                        data(output)
                    else:
                        for chunk in data:
                            output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
            digest = hashlib.sha256(temp_path.read_bytes()).hexdigest()
            target = self._artifact_path(key, digest, chosen_format)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                temp_path.unlink()
            else:
                os.replace(temp_path, target)
                _fsync_dir(target.parent)
            ref = ArtifactRef(key, digest, str(target.relative_to(self.root)), chosen_format, target.stat().st_size,
                             row_count, first_date, last_date, input_digest, tuple(sorted(set(generations))))
            self.update_manifest(ref)
            return ref
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise

    def load_manifest(self) -> tuple[ArtifactRef, ...]:
        if not self.manifest_path.exists():
            return ()
        raw = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        return tuple(ArtifactRef.from_dict(item) for item in raw.get("artifacts", []))

    def update_manifest(self, ref: ArtifactRef) -> None:
        refs = {item.path: item for item in self.load_manifest()}
        refs[ref.path] = ref
        payload = {"schema_version": 1, "artifacts": [item.to_dict() for item in sorted(refs.values(), key=lambda item: item.path)]}
        self.root.mkdir(parents=True, exist_ok=True)
        _atomic_json(self.manifest_path, payload)

    def plan_gc(self, referenced_generations: Iterable[str], *, now: datetime | None = None, grace_period: timedelta = timedelta(days=7)) -> tuple[ArtifactRef, ...]:
        """Return only old artifacts absent from the explicitly referenced generations."""
        referenced = set(referenced_generations)
        cutoff = (now or datetime.now(timezone.utc)) - grace_period
        return tuple(item for item in self.load_manifest() if not referenced.intersection(item.generations) and item.created_at < cutoff)


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_file(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_path, path)
        _fsync_dir(path.parent)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


__all__ = ["ArtifactRef", "ArtifactStore", "ProductPartitionKey", "deterministic_input_digest"]
