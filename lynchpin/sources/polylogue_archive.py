"""Thin, read-only adapter for Polylogue archive SQLite databases."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from ..core.config import get_config
from ..core.errors import SchemaVersionError, SourceUnavailableError

_DATABASES = {"source": "source.db", "index": "index.db", "embeddings": "embeddings.db"}
_TABLES = {
    "raw_sessions": ("raw_sessions",),
    "blob_references": ("blob_refs", "blob_references"),
    "sessions": ("sessions", "conversations"),
    "messages": ("messages", "conversation_messages"),
    "blocks": ("blocks", "message_blocks", "content_blocks"),
    "provider_identities": ("provider_identities", "providers", "identities"),
    "lineage": ("session_links", "conversation_lineage", "session_lineage", "lineage"),
    "embedding_status": ("embedding_status",),
    "embedding_metadata": ("embedding_derivation_state", "message_embedding_refs", "message_embeddings"),
}
_CAPABILITY_DATABASES = {
    "raw_sessions": ("source",),
    "blob_references": ("source",),
    "sessions": ("index", "source"),
    "messages": ("index", "source"),
    "blocks": ("index", "source"),
    "provider_identities": ("index", "source"),
    "lineage": ("index", "source"),
    "fts": ("index", "source"),
    "embedding_status": ("embeddings",),
    "embedding_metadata": ("embeddings",),
}


class PolylogueArchiveUnavailableError(SourceUnavailableError):
    """A configured archive database is absent or unreadable."""

    def __init__(self, *, path: Path | None = None, reason: str = "") -> None:
        super().__init__("polylogue_archive", path=str(path) if path else None, reason=reason)


class PolylogueArchiveSchemaError(SchemaVersionError):
    """The archive does not expose the requested source capability."""

    def __init__(self, *, found: object, expected: object) -> None:
        super().__init__(found=found, expected=expected, source="polylogue_archive")


@dataclass(frozen=True)
class ArchiveDatabase:
    name: str
    path: Path


@dataclass(frozen=True)
class ArchiveCapabilities:
    raw_sessions: bool
    blob_references: bool
    sessions: bool
    messages: bool
    blocks: bool
    provider_identities: bool
    lineage: bool
    fts: bool
    embedding_status: bool
    embedding_metadata: bool


@dataclass(frozen=True)
class TableIntrospectionCaveat:
    """A table was discovered but one metadata operation could not inspect it."""

    table: str
    operation: str
    reason: str


@dataclass(frozen=True)
class DatabaseSchema:
    database: ArchiveDatabase
    database_list: tuple[str, ...]
    schema_version: int
    user_version: int
    data_version: int
    journal_mode: str
    tables: tuple[str, ...]
    table_columns: tuple[tuple[str, tuple[str, ...] | None], ...]
    table_introspection_caveats: tuple[TableIntrospectionCaveat, ...]
    indexes: tuple[str, ...]
    triggers: tuple[str, ...]
    fts_tables: tuple[str, ...]
    capabilities: ArchiveCapabilities


@dataclass(frozen=True)
class ArchiveCoverage:
    status: str
    reason: str
    collection_model: str = "historical"


@dataclass(frozen=True)
class ArchiveReadiness:
    status: str
    reason: str
    root: Path
    schemas: tuple[DatabaseSchema, ...]
    coverage: ArchiveCoverage


@dataclass(frozen=True)
class ArchiveSnapshot:
    database: ArchiveDatabase
    path: Path
    sha256: str
    created_at: datetime
    source_data_version: int
    source_size: int
    source_mtime_ns: int


@dataclass(frozen=True)
class ArchiveSession:
    locator: str | None
    provider_identity_locator: str | None
    origin: str | None
    started_at: str | None
    updated_at: str | None
    parent_locator: str | None


@dataclass(frozen=True)
class ArchiveMessage:
    locator: str | None
    session_locator: str | None
    role: str | None
    author_kind: str | None
    occurred_at: str | None
    content_hash: str | None


@dataclass(frozen=True)
class ArchiveBlock:
    locator: str | None
    message_locator: str | None
    kind: str | None
    content_hash: str | None


@dataclass(frozen=True)
class ArchiveProviderIdentity:
    locator: str | None
    provider: str | None
    external_locator: str | None


@dataclass(frozen=True)
class ArchiveLineage:
    child_locator: str | None
    parent_locator: str | None
    relation: str | None


def resolve_archive_root(root: Path | None = None) -> Path:
    """Resolve an explicit root, a Polylogue override, or Lynchpin config."""
    if root is not None:
        return root.expanduser()
    for name in ("POLYLOGUE_ARCHIVE_ROOT", "LYNCHPIN_POLYLOGUE_ARCHIVE_ROOT"):
        if value := os.environ.get(name, "").strip():
            return Path(value).expanduser()
    return get_config().polylogue_archive_root


def archive_databases(root: Path | None = None) -> tuple[ArchiveDatabase, ...]:
    """Return expected archive paths without opening or creating any database."""
    archive_root = resolve_archive_root(root)
    return tuple(ArchiveDatabase(name, archive_root / filename) for name, filename in _DATABASES.items())


def open_readonly(path: Path) -> sqlite3.Connection:
    """Open an existing SQLite database without write permission."""
    if not path.is_file():
        raise PolylogueArchiveUnavailableError(path=path, reason="database does not exist")
    try:
        conn = sqlite3.connect(f"file:{quote(path.as_posix())}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise PolylogueArchiveUnavailableError(path=path, reason=str(exc)) from exc
    conn.row_factory = sqlite3.Row
    return conn


def introspect_database(database: ArchiveDatabase) -> DatabaseSchema:
    """Inspect SQLite metadata only. It never scans archive rows."""
    try:
        with open_readonly(database.path) as conn:
            rows = conn.execute("SELECT name, sql FROM sqlite_master WHERE type IN ('table', 'view') ORDER BY name").fetchall()
            database_list = tuple(str(row[1]) for row in conn.execute("PRAGMA database_list"))
            schema_version = _pragma_int(conn, "schema_version")
            user_version = _pragma_int(conn, "user_version")
            data_version = _pragma_int(conn, "data_version")
            journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0])
    except sqlite3.Error as exc:
        raise PolylogueArchiveUnavailableError(path=database.path, reason=str(exc)) from exc
    tables = tuple(str(row["name"]) for row in rows)
    with open_readonly(database.path) as conn:
        table_columns: list[tuple[str, tuple[str, ...] | None]] = []
        table_introspection_caveats: list[TableIntrospectionCaveat] = []
        for table in tables:
            try:
                columns = tuple(
                    str(row["name"])
                    for row in conn.execute(f"PRAGMA table_xinfo({_quote(table)})")
                )
            except sqlite3.Error as exc:
                table_columns.append((table, None))
                table_introspection_caveats.append(
                    TableIntrospectionCaveat(table, "table_xinfo", str(exc))
                )
            else:
                table_columns.append((table, columns))
        indexes = tuple(
            sorted(
                {
                    str(row["name"])
                    for table in tables
                    for row in conn.execute(f"PRAGMA index_list({_quote(table)})")
                }
            )
        )
        triggers = tuple(str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger' ORDER BY name"))
    fts_tables = tuple(str(row["name"]) for row in rows if "virtual table" in str(row["sql"] or "").lower() and "fts" in str(row["sql"] or "").lower())
    available = set(tables)
    return DatabaseSchema(
        database=database,
        database_list=database_list,
        schema_version=schema_version,
        user_version=user_version,
        data_version=data_version,
        journal_mode=journal_mode,
        tables=tables,
        table_columns=tuple(table_columns),
        table_introspection_caveats=tuple(table_introspection_caveats),
        indexes=indexes,
        triggers=triggers,
        fts_tables=fts_tables,
        capabilities=ArchiveCapabilities(
            raw_sessions=_matching_table(available, "raw_sessions") is not None,
            blob_references=_matching_table(available, "blob_references") is not None,
            sessions=_matching_table(available, "sessions") is not None,
            messages=_matching_table(available, "messages") is not None,
            blocks=_matching_table(available, "blocks") is not None,
            provider_identities=_matching_table(available, "provider_identities") is not None,
            lineage=_matching_table(available, "lineage") is not None,
            fts=bool(fts_tables),
            embedding_status=_matching_table(available, "embedding_status") is not None,
            embedding_metadata=_matching_table(available, "embedding_metadata") is not None,
        ),
    )


def readiness(root: Path | None = None) -> ArchiveReadiness:
    """Report schema readiness and explicit unknown coverage without row scans."""
    archive_root = resolve_archive_root(root)
    coverage = ArchiveCoverage("unknown", "timestamp coverage requires a coherent snapshot scan")
    if not archive_root.is_dir():
        return ArchiveReadiness("missing", "archive root does not exist", archive_root, (), coverage)
    schemas: list[DatabaseSchema] = []
    failures = False
    for database in archive_databases(archive_root):
        if not database.path.is_file():
            continue
        try:
            schemas.append(introspect_database(database))
        except PolylogueArchiveUnavailableError:
            failures = True
    by_name = {schema.database.name: schema for schema in schemas}
    source = by_name.get("source")
    index = by_name.get("index")
    if source is None:
        status, reason = "missing", "source database is unavailable"
    elif not source.capabilities.raw_sessions:
        status, reason = "degraded", "source database lacks raw session authority"
    elif index is None:
        status, reason = "degraded", "index database is unavailable"
    elif not (index.capabilities.sessions and index.capabilities.messages):
        status, reason = "degraded", "index database lacks normalized session or message tables"
    elif failures:
        status, reason = "degraded", "one or more archive databases could not be inspected"
    else:
        status, reason = "ready", "source authority and normalized index capabilities are available"
    return ArchiveReadiness(status, reason, archive_root, tuple(schemas), coverage)


def database_for_capability(capability: str, root: Path | None = None) -> ArchiveDatabase:
    """Resolve a capability to its owning database, with neutral fallbacks."""
    if capability not in _CAPABILITY_DATABASES:
        raise ValueError(f"unknown Polylogue archive capability: {capability}")
    archive_root = resolve_archive_root(root)
    found: dict[str, tuple[str, ...]] = {}
    databases = {database.name: database for database in archive_databases(archive_root)}
    for name in _CAPABILITY_DATABASES[capability]:
        database = databases[name]
        if not database.path.is_file():
            continue
        schema = introspect_database(database)
        found[name] = schema.tables
        if _has_capability(schema, capability):
            return database
    raise PolylogueArchiveSchemaError(found=found, expected=f"a {capability} capability")


def snapshot_database(database: ArchiveDatabase, destination: Path) -> ArchiveSnapshot:
    """Create a coherent backup in a caller-provided private destination."""
    if not database.path.is_file():
        raise PolylogueArchiveUnavailableError(path=database.path, reason="database does not exist")
    destination.mkdir(parents=True, exist_ok=True)
    snapshot_path = destination / f"{database.name}.snapshot.db"
    if snapshot_path.exists():
        raise FileExistsError(f"snapshot destination already exists: {snapshot_path}")
    try:
        source_stat = database.path.stat()
    except OSError as exc:
        raise PolylogueArchiveUnavailableError(path=database.path, reason=str(exc)) from exc
    try:
        with open_readonly(database.path) as source, sqlite3.connect(snapshot_path) as target:
            data_version = _pragma_int(source, "data_version")
            source.backup(target)
    except sqlite3.Error as exc:
        raise PolylogueArchiveUnavailableError(path=database.path, reason=str(exc)) from exc
    return ArchiveSnapshot(database, snapshot_path, _sha256(snapshot_path), datetime.now(UTC), data_version, source_stat.st_size, source_stat.st_mtime_ns)


def snapshot_capability(capability: str, destination: Path, root: Path | None = None) -> ArchiveSnapshot:
    """Snapshot the database that owns a requested archive capability."""
    return snapshot_database(database_for_capability(capability, root), destination)


def iter_sessions(snapshot: ArchiveSnapshot):
    """Yield session metadata from a coherent snapshot."""
    for row in _records(snapshot, "sessions", {
        "locator": ("session_id", "conversation_id", "id"),
        "provider_identity_locator": ("provider_identity_id", "provider_id", "source_name"),
        "origin": ("origin", "source_origin"),
        "started_at": ("started_at", "created_at", "created_at_ms", "first_message_at"),
        "updated_at": ("updated_at", "updated_at_ms", "last_message_at"),
        "parent_locator": ("parent_session_id", "parent_conversation_id", "parent_id"),
    }, composite_locators=("origin", "native_id")):
        yield ArchiveSession(**row)


def iter_messages(snapshot: ArchiveSnapshot):
    """Yield message metadata, deliberately excluding message text."""
    for row in _records(snapshot, "messages", {
        "locator": ("message_id", "native_id", "id"),
        "session_locator": ("session_id", "conversation_id"),
        "role": ("role", "author_role"),
        "author_kind": ("author_kind", "author_type", "role"),
        "occurred_at": ("occurred_at", "occurred_at_ms", "created_at", "timestamp"),
        "content_hash": ("content_hash", "text_hash", "sha256"),
    }, composite_locators=("session_id", "native_id")):
        yield ArchiveMessage(**row)


def iter_blocks(snapshot: ArchiveSnapshot):
    """Yield content-block metadata, deliberately excluding block text."""
    for row in _records(snapshot, "blocks", {
        "locator": ("block_id", "id"),
        "message_locator": ("message_id",),
        "kind": ("kind", "type", "block_type"),
        "content_hash": ("content_hash", "text_hash", "sha256"),
    }):
        yield ArchiveBlock(**row)


def iter_provider_identities(snapshot: ArchiveSnapshot):
    """Yield provider identity locators without expanding private metadata."""
    for row in _records(snapshot, "provider_identities", {
        "locator": ("provider_identity_id", "provider_id", "id"),
        "provider": ("provider", "source_name", "name"),
        "external_locator": ("external_id", "account_id", "source_id"),
    }):
        yield ArchiveProviderIdentity(**row)


def iter_lineages(snapshot: ArchiveSnapshot):
    """Yield explicit session lineage edges when present in the schema."""
    for row in _records(snapshot, "lineage", {
        "child_locator": ("src_session_id", "child_session_id", "conversation_id", "session_id"),
        "parent_locator": ("resolved_dst_session_id", "parent_session_id", "parent_conversation_id", "parent_id"),
        "relation": ("link_type", "relation", "relation_type", "kind"),
    }, lineage_destination_columns=("dst_origin", "dst_native_id")):
        yield ArchiveLineage(**row)


def _records(
    snapshot: ArchiveSnapshot,
    capability: str,
    fields: dict[str, tuple[str, ...]],
    *,
    composite_locators: tuple[str, str] | None = None,
    lineage_destination_columns: tuple[str, str] | None = None,
):
    schema = introspect_database(ArchiveDatabase(snapshot.database.name, snapshot.path))
    table = _matching_table(set(schema.tables), capability)
    if table is None:
        raise PolylogueArchiveSchemaError(found=schema.tables, expected=f"a {capability} table")
    with open_readonly(snapshot.path) as conn:
        columns = {str(row["name"]) for row in conn.execute(f"PRAGMA table_xinfo({_quote(table)})")}
        expressions = []
        milliseconds: set[str] = set()
        for field, candidates in fields.items():
            if field == "locator" and composite_locators and all(item in columns for item in composite_locators):
                expressions.append(f"{_quote(composite_locators[0])} || ':' || {_quote(composite_locators[1])} AS {_quote(field)}")
                continue
            if field == "parent_locator" and lineage_destination_columns and all(item in columns for item in lineage_destination_columns):
                direct = next((item for item in candidates if item in columns), None)
                destination = f"{_quote(lineage_destination_columns[0])} || ':' || {_quote(lineage_destination_columns[1])}"
                expression = f"COALESCE({_quote(direct)}, {destination})" if direct else destination
                expressions.append(f"{expression} AS {_quote(field)}")
                continue
            column = next((item for item in candidates if item in columns), None)
            if column and column.endswith("_ms"):
                milliseconds.add(field)
            expressions.append(f"{_quote(column)} AS {_quote(field)}" if column else f"NULL AS {_quote(field)}")
        for row in conn.execute(f"SELECT {', '.join(expressions)} FROM {_quote(table)}"):
            yield {
                field: _serialized_value(row[field], milliseconds=field in milliseconds)
                for field in fields
            }


def _matching_table(tables: set[str], capability: str) -> str | None:
    return next((table for table in _TABLES[capability] if table in tables), None)


def _has_capability(schema: DatabaseSchema, capability: str) -> bool:
    if capability == "fts":
        return schema.capabilities.fts
    return bool(getattr(schema.capabilities, capability))


def _serialized_value(value: object, *, milliseconds: bool) -> str | None:
    if value is None:
        return None
    if milliseconds and isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


def _pragma_int(conn: sqlite3.Connection, name: str) -> int:
    return int(conn.execute(f"PRAGMA {name}").fetchone()[0])


def _quote(identifier: str | None) -> str:
    if identifier is None:
        raise ValueError("SQLite identifier is required")
    return '"' + identifier.replace('"', '""') + '"'


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
