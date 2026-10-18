"""Resolve the collector's SQLite source set without copying database payloads."""

from __future__ import annotations

import json
import hashlib
import heapq
import stat
import sys
from contextlib import ExitStack, closing, contextmanager
from collections.abc import Callable, Generator, Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from itertools import islice
from pathlib import Path
from typing import Any, Protocol, TypeVar

from ..core.errors import SourceUnavailableError
from ..core.input_snapshot import (
    current_input_snapshot,
    pinned_version,
    read_input_path,
)

from .machine_sqlite import (
    expect_sqlite_inode,
    acquire_machine_read_lock,
    machine_lock_path,
)
from .machine_models import (
    MachineBlockDeviceSample,
    MachineServiceCgroupIOSample,
    MachineServiceCgroupPressureSample,
    MachineProcessIODeltaSample,
    MachineProcessMemorySample,
    MachineCgroupMemorySample,
    MachineServiceState,
)

SCHEMA = "sinnix-machine-sqlite-segments-v1"


def manifest_path(current: Path) -> Path:
    return current.with_name(current.name + ".segments.json")


def _unavailable(path: Path, reason: str) -> SourceUnavailableError:
    return SourceUnavailableError("machine", path=str(path), reason=reason)


def _stat(path: Path) -> tuple[int, int, int, int]:
    value = path.lstat()
    if not stat.S_ISREG(value.st_mode):
        raise _unavailable(path, "SQLite source is not a regular file")
    # Device numbers can change across mounts. They are not durable identity.
    return value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


@dataclass(frozen=True)
class MachineSegment:
    identity: str
    path: Path
    sealed: bool
    version: tuple[int, int, int, int]
    coordination: Path | None = None

    def validate(self) -> None:
        try:
            actual = _stat(self.path)
            if actual[0] != self.version[0] or (self.sealed and actual != self.version):
                raise _unavailable(self.path, "SQLite segment identity changed")
            if self.sealed:
                wal = self.path.with_name(self.path.name + "-wal")
                try:
                    if _stat(wal)[1]:
                        raise _unavailable(
                            wal, "sealed SQLite segment has an uncheckpointed WAL"
                        )
                except FileNotFoundError:
                    pass
        except OSError as exc:
            raise _unavailable(self.path, str(exc)) from exc


@dataclass(frozen=True)
class MachineSourceSet:
    current: Path
    manifest: Path | None
    generation: str | None
    segments: tuple[MachineSegment, ...]
    origin: tuple[str, int, int] | None = None

    def lineage_identity(self) -> str:
        """Preserve the pre-cutover append lineage without rescanning its rows."""
        if self.origin is not None:
            origin = self.origin
        elif self.segments:
            path = self.current.resolve()
            value = path.stat()
            origin = (str(path), value.st_dev, value.st_ino)
        else:
            raise _unavailable(self.current, "no SQLite source for lineage")
        observation = json.dumps(list(origin), separators=(",", ":"))
        return "sqlite-location-v1:" + hashlib.sha256(observation.encode()).hexdigest()

    def __bool__(self) -> bool:
        return bool(self.segments)

    def input_files(self) -> tuple[Path, ...]:
        """Include control and WAL identities in materialization fingerprints."""
        files: list[Path] = [self.manifest] if self.manifest is not None else []
        for segment in self.segments:
            segment.validate()
            files.append(segment.path)
            if not segment.sealed:
                wal = segment.path.with_name(segment.path.name + "-wal")
                try:
                    _stat(wal)
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    raise _unavailable(wal, str(exc)) from exc
                else:
                    files.append(wal)
        return tuple(files)


def _resolve_machine_sources_unlocked(current: Path) -> MachineSourceSet:
    """A missing manifest means the existing single source, never missing history.

    Once the owner publishes a manifest, every selected member is required.
    Transition, malformed, missing or changed members refuse the whole set.
    Explicit single-file diagnostic readers do not call this resolver.
    """
    current = Path(current)
    manifest = manifest_path(current)
    snapshot = current_input_snapshot()
    try:
        if manifest.is_symlink():
            raise _unavailable(manifest, "segment manifest must be a regular file")
        if snapshot is not None:
            snapshot.capture((manifest,))
            captured, control_version = pinned_version(manifest)
            if captured and control_version is None and manifest.exists():
                raise _unavailable(
                    manifest, "source set appeared during an unsegmented operation"
                )
        selected = read_input_path(manifest)
        try:
            before = _stat(selected)
        except FileNotFoundError:
            # A dangling configured manifest is an unavailable contract.
            if manifest.is_symlink():
                raise _unavailable(manifest, "segment manifest is a dangling link")
            try:
                unsegmented_version = _stat(current)
            except FileNotFoundError:
                if current.is_symlink():
                    raise _unavailable(current, "SQLite source is a dangling link")
                entrance = current.parent
                while True:
                    try:
                        entrance.lstat()
                    except FileNotFoundError:
                        if entrance == entrance.parent:
                            break
                        entrance = entrance.parent
                    else:
                        entrance.stat()  # broken intermediate links are unavailable
                        break
                return MachineSourceSet(current, None, None, ())
            return MachineSourceSet(
                current,
                None,
                None,
                (MachineSegment("unsegmented", current, False, unsegmented_version),),
            )
        payload = json.loads(selected.read_text(encoding="utf-8"))
        if _stat(selected) != before:
            raise _unavailable(manifest, "segment manifest changed while reading")
        if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
            raise _unavailable(manifest, "unsupported segment manifest schema")
        if payload.get("state") != "ready":
            raise _unavailable(manifest, "SQLite source set is transitioning")
        generation = payload.get("generation")
        sealed = payload.get("sealed")
        if (
            not isinstance(generation, str)
            or not generation
            or not isinstance(sealed, list)
        ):
            raise _unavailable(manifest, "invalid segment generation or member list")
        rows = [*((entry, True) for entry in sealed), (payload.get("current"), False)]
        segments: list[MachineSegment] = []
        identities: set[str] = set()
        paths: set[Path] = set()
        for entry, is_sealed in rows:
            if not isinstance(entry, dict):
                raise _unavailable(manifest, "invalid segment entry")
            identity, filename, raw_version = (
                entry.get("id"),
                entry.get("file"),
                entry.get("identity"),
            )
            if (
                not isinstance(identity, str)
                or not identity
                or identity in identities
                or not isinstance(filename, str)
                or filename in {"", ".", ".."}
                or Path(filename).name != filename
                or "\\" in filename
                or not isinstance(raw_version, dict)
            ):
                raise _unavailable(
                    manifest, "invalid or duplicate SQLite segment identity/path"
                )
            path = current.parent / filename
            if path in paths or (not is_sealed and path != current):
                raise _unavailable(
                    manifest, "duplicate segment path or wrong current basename"
                )
            values = tuple(
                raw_version.get(key)
                for key in ("inode", "size", "mtime_ns", "ctime_ns")
            )
            if any(type(value) is not int or value < 0 for value in values):
                raise _unavailable(manifest, "invalid physical segment identity")
            # Checked above before converting: bool and partial identities refuse.
            entry_version = tuple(int(value) for value in values)
            segment = MachineSegment(
                identity,
                path,
                is_sealed,
                (
                    entry_version[0],
                    entry_version[1],
                    entry_version[2],
                    entry_version[3],
                ),
                current,
            )
            segment.validate()
            segments.append(segment)
            identities.add(identity)
            paths.add(path)
        _stat(
            machine_lock_path(current)
        )  # Required owner coordination, not a payload copy.
        origin = payload.get("origin")
        if (
            not isinstance(origin, dict)
            or not isinstance(origin.get("path"), str)
            or not Path(origin["path"]).is_absolute()
            or type(origin.get("device")) is not int
            or origin["device"] < 0
            or type(origin.get("inode")) is not int
            or origin["inode"] not in {segment.version[0] for segment in segments}
        ):
            raise _unavailable(manifest, "invalid original SQLite lineage identity")
        return MachineSourceSet(
            current,
            manifest,
            generation,
            tuple(segments),
            (origin["path"], origin["device"], origin["inode"]),
        )
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise _unavailable(manifest, str(exc)) from exc


def resolve_machine_sources(current: Path) -> MachineSourceSet:
    """Resolve a stable membership selection without entering a cutover."""
    current = Path(current)
    held = acquire_machine_read_lock(current)
    try:
        return _resolve_machine_sources_unlocked(current)
    finally:
        if held is not None:
            held.close()


def machine_source_input_files(cfg: Any) -> tuple[Path, ...]:
    return resolve_machine_sources(Path(cfg.machine_telemetry_db)).input_files()


class _ObservedRow(Protocol):
    @property
    def observed_at(self) -> datetime: ...


_Row = TypeVar("_Row", bound=_ObservedRow)
_Result = TypeVar("_Result")


def read_machine_segment(
    segment: MachineSegment, reader: Callable[..., _Result], **kwargs: Any
) -> _Result:
    """Use the existing scalar reader with an identity-guarded connection."""
    segment.validate()
    with ExitStack() as stack:
        held = acquire_machine_read_lock(
            segment.coordination or segment.path,
            required=segment.coordination is not None,
        )
        if held is not None:
            stack.enter_context(closing(held))
        with expect_sqlite_inode(segment.path, segment.version[0]):
            result = reader(path=segment.path, **kwargs)
    segment.validate()
    return result


def _segment_rows(
    segment: MachineSegment,
    reader: Callable[..., Iterable[_Row]],
    kwargs: dict[str, Any],
) -> Generator[_Row, None, None]:
    segment.validate()
    iterator = iter(reader(path=segment.path, **kwargs))
    try:
        # Priming opens the reader's single connection. Reset the context before
        # yielding: heap-merged generators resume in a non-stack order.
        with expect_sqlite_inode(segment.path, segment.version[0]):
            try:
                first = next(iterator)
            except StopIteration:
                return
        yield first
        yield from iterator
    finally:
        close = getattr(iterator, "close", None)
        if callable(close):
            close()
        segment.validate()


def _row_order(row: _ObservedRow) -> tuple[Any, ...]:
    """Match each reader's existing SQL ordering across file boundaries."""
    if isinstance(row, MachineProcessMemorySample):
        return row.observed_at, -row.pss_kb
    if isinstance(row, MachineProcessIODeltaSample):
        return row.observed_at, -row.total_bytes_delta, -row.total_syscalls_delta
    if isinstance(row, MachineBlockDeviceSample):
        return row.observed_at, row.device
    if isinstance(row, MachineServiceCgroupIOSample):
        return row.observed_at, row.scope or "", row.unit or "", row.major, row.minor
    if isinstance(row, (MachineServiceCgroupPressureSample, MachineServiceState)):
        return row.observed_at, row.scope or "", row.unit or ""
    if isinstance(row, MachineCgroupMemorySample):
        return row.observed_at, row.label
    return (row.observed_at,)


def iter_machine_segments(
    sources: MachineSourceSet,
    reader: Callable[..., Iterable[_Row]],
    *,
    limit: int | None = None,
    **kwargs: Any,
) -> Iterator[_Row]:
    """Merge indexed per-file windows; close every stream on any exit."""
    if limit is not None and limit <= 0:
        return
    if limit is not None:
        kwargs["limit"] = limit
    with ExitStack() as stack:
        held = acquire_machine_read_lock(
            sources.current, required=sources.manifest is not None
        )
        if held is not None:
            stack.enter_context(closing(held))
        streams = [
            stack.enter_context(closing(_segment_rows(segment, reader, kwargs)))
            for segment in sources.segments
        ]
        merged: Iterator[_Row] = (
            streams[0] if len(streams) == 1 else heapq.merge(*streams, key=_row_order)
        )
        yield from merged if limit is None else islice(merged, limit)


@contextmanager
def attach_machine_sources(
    conn: Any, sources: MachineSourceSet, *, alias: str, tables: Iterable[str]
) -> Iterator[None]:
    """Expose one native SQLite set through a temporary DuckDB read surface."""
    import re

    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", alias):
        raise ValueError("invalid machine source alias")
    if not sources:
        raise _unavailable(sources.current, "no telemetry SQLite source")
    attached: list[str] = []
    schema_created = False
    held = acquire_machine_read_lock(
        sources.current, required=sources.manifest is not None
    )

    def literal(value: Path) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    try:
        if sources.manifest is None:
            segment = sources.segments[0]
            segment.validate()
            conn.execute(
                f"ATTACH {literal(segment.path)} AS {alias} (TYPE SQLITE, READ_ONLY)"
            )
            attached.append(alias)
            segment.validate()
        else:
            members = []
            for i, segment in enumerate(sources.segments):
                segment.validate()
                member = f"{alias}_segment_{i}"
                conn.execute(
                    f"ATTACH {literal(segment.path)} AS {member} (TYPE SQLITE, READ_ONLY)"
                )
                attached.append(member)
                segment.validate()
                present = {
                    row[0]
                    for row in conn.execute(
                        "SELECT table_name FROM information_schema.tables WHERE table_catalog=?",
                        [member],
                    ).fetchall()
                }
                members.append((member, present))
            conn.execute(f"CREATE SCHEMA {alias}")
            schema_created = True
            for table in tables:
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
                    raise ValueError("invalid machine source table")
                selections = []
                for i, (member, present) in enumerate(members):
                    if table in present:
                        ordinal = (
                            f", {i} AS __segment_order"
                            if table == "source_status"
                            else ""
                        )
                        selections.append(f"SELECT *{ordinal} FROM {member}.{table}")
                if len(selections) != len(members):
                    raise _unavailable(
                        sources.current,
                        f"required SQLite table {table!r} is missing in a selected segment",
                    )
                query = " UNION ALL BY NAME ".join(selections)
                if table == "source_status":
                    query = (
                        "SELECT * EXCLUDE (__segment_order, __rank) FROM (SELECT *, "
                        "row_number() OVER (PARTITION BY source ORDER BY __segment_order DESC) AS __rank "
                        f"FROM ({query})) WHERE __rank=1"
                    )
                conn.execute(f"CREATE VIEW {alias}.{table} AS {query}")
        yield
        for segment in sources.segments:
            segment.validate()
    finally:
        original = sys.exc_info()[1]
        try:
            if schema_created:
                conn.execute(f"DROP SCHEMA {alias} CASCADE")
            for member in reversed(attached):
                conn.execute(f"DETACH {member}")
        except BaseException as cleanup:
            # A failed detach cannot leave an old-basename handle behind when
            # its source lock is released. The private candidate must abort.
            try:
                conn.close()
            except BaseException as close_error:
                cleanup.add_note(
                    f"closing the failed source connection also failed: {close_error}"
                )
            if original is None:
                raise
            original.add_note(f"machine source attachment cleanup failed: {cleanup}")
        finally:
            if held is not None:
                held.close()
