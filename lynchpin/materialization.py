"""Materialization audit for canonical Lynchpin datasets.

This module distinguishes three things that older source readiness checks often
blurred together:

- raw authority: immutable exports, app databases, or repo state;
- materialized product: canonical file/DB/table that query surfaces should read;
- runtime cache: cachew/lru speedups, never authoritative.

The audit is intentionally stricter than source availability. A source can be
available while still not being fully materialized for near-instant analysis.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from .core.config import LynchpinConfig, get_config
from .ingest.webhistory import (
    build_full_history,
    full_history_manifest_path,
)
from .ingest.exports_materialize import (
    materialize_messenger,
    materialize_raindrop,
    materialize_reddit,
    materialize_spotify,
    messenger_canonical_dir,
    raindrop_bookmarks_path,
    reddit_canonical_dir,
    spotify_streams_path,
)
from .ingest.activitywatch_materialize import materialize_activitywatch_events
from .ingest.terminal_materialize import materialize_atuin_history
from .ingest.machine_materialize import materialize_machine_telemetry
from .ingest.google_takeout_materialize import (
    google_takeout_inventory_dir,
    materialize_google_takeout_inventory,
)
from .ingest.google_takeout_products import (
    google_takeout_products_dir,
    materialize_google_takeout_products,
)
from .sources.activitywatch_raw import canonical_activitywatch_events_path
from .sources.machine import canonical_machine_table_path
from .sources.terminal import canonical_atuin_history_path
from .sources.google_takeout import discover_takeout_archives
from .sources.polylogue import archive_readiness


Status = str


@dataclass(frozen=True)
class MaterializedDataset:
    name: str
    status: Status
    authority: str
    query_surface: str
    materialized_paths: tuple[Path, ...]
    raw_roots: tuple[Path, ...]
    row_count: int | None
    first_date: date | None
    last_date: date | None
    refresh_command: str
    reason: str

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "authority": self.authority,
            "query_surface": self.query_surface,
            "materialized_paths": [str(path) for path in self.materialized_paths],
            "raw_roots": [str(path) for path in self.raw_roots],
            "row_count": self.row_count,
            "first_date": self.first_date.isoformat() if self.first_date else None,
            "last_date": self.last_date.isoformat() if self.last_date else None,
            "refresh_command": self.refresh_command,
            "reason": self.reason,
        }


def audit_materialization(
    *,
    cfg: LynchpinConfig | None = None,
    ensure_supported: bool = False,
) -> list[MaterializedDataset]:
    """Return strict materialization status for known Lynchpin datasets."""
    cfg = cfg or get_config()
    if ensure_supported:
        ensure_supported_materializations(cfg=cfg)
    return [
        _webhistory_dataset(cfg),
        _google_takeout_dataset(cfg),
        _polylogue_dataset(cfg),
        _activitywatch_dataset(cfg),
        _atuin_dataset(cfg),
        _git_substrate_dataset(cfg),
        _health_dataset(cfg),
        _sleep_dataset(cfg),
        _substance_dataset(cfg),
        _spotify_dataset(cfg),
        _reddit_dataset(cfg),
        _messenger_dataset(cfg),
        _raindrop_dataset(cfg),
        _historical_browser_db_dataset(cfg),
        _machine_dataset(cfg),
        _recovery_ontology_dataset(cfg),
    ]


def ensure_supported_materializations(*, cfg: LynchpinConfig | None = None) -> None:
    """Materialize products this module can rebuild without extra credentials."""
    cfg = cfg or get_config()
    webhistory = cfg.webhistory_ndjson
    if webhistory is None or not webhistory.exists():
        build_full_history(data_dir=cfg.webhistory_dir, output=webhistory)
    elif not full_history_manifest_path(webhistory).exists():
        build_full_history(data_dir=cfg.webhistory_dir, output=webhistory)
    if not spotify_streams_path().exists():
        materialize_spotify()
    if not (reddit_canonical_dir() / "manifest.json").exists():
        materialize_reddit()
    if not raindrop_bookmarks_path().exists():
        materialize_raindrop()
    if not (messenger_canonical_dir() / "manifest.json").exists():
        materialize_messenger()
    if not canonical_atuin_history_path().exists():
        materialize_atuin_history()
    if not canonical_activitywatch_events_path().exists():
        materialize_activitywatch_events()
    if not canonical_machine_table_path("manifest").with_suffix(".json").exists():
        materialize_machine_telemetry()
    if not (google_takeout_inventory_dir() / "manifest.json").exists():
        materialize_google_takeout_inventory()
    if not (google_takeout_products_dir() / "manifest.json").exists():
        materialize_google_takeout_products()


def render_materialization_audit(rows: Iterable[MaterializedDataset]) -> str:
    rows = list(rows)
    lines = [
        "# Lynchpin Materialization Audit",
        "",
        "| Dataset | Status | Rows | Coverage | Product | Reason |",
        "|---|---:|---:|---|---|---|",
    ]
    for row in rows:
        coverage = _coverage_label(row.first_date, row.last_date)
        product = ", ".join(str(path) for path in row.materialized_paths) or "-"
        lines.append(
            "| "
            + " | ".join(
                [
                    row.name,
                    row.status,
                    str(row.row_count) if row.row_count is not None else "-",
                    coverage,
                    product,
                    row.reason.replace("|", "\\|"),
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def _webhistory_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    output = cfg.webhistory_ndjson
    manifest = full_history_manifest_path(output) if output is not None else None
    if output is None:
        return _missing_dataset(
            "webhistory",
            "browser DBs, browser exports, Google Takeout Chrome history",
            "lynchpin.sources.web",
            "python -m lynchpin.ingest.webhistory",
            "canonical full_history.ndjson is not configured",
            raw_roots=(cfg.webhistory_raw_dir, cfg.webhistory_dir),
        )
    if not output.exists():
        return _missing_dataset(
            "webhistory",
            "browser DBs, browser exports, Google Takeout Chrome history",
            "lynchpin.sources.web",
            "python -m lynchpin.ingest.webhistory",
            f"canonical NDJSON is missing: {output}",
            materialized_paths=(output,),
            raw_roots=(cfg.webhistory_raw_dir, cfg.webhistory_dir),
        )

    meta = _load_json(manifest) if manifest and manifest.exists() else {}
    row_count = _int_or_none(meta.get("row_count"))
    first = _date_from_iso(meta.get("first_visit_at"))
    last = _date_from_iso(meta.get("last_visit_at"))
    if row_count is None or first is None or last is None:
        row_count, first, last = _scan_webhistory_ndjson(output)
    status = "ready" if manifest and manifest.exists() else "degraded"
    reason = "canonical merged NDJSON and manifest are present" if status == "ready" else "canonical merged NDJSON exists but manifest is missing"
    return MaterializedDataset(
        name="webhistory",
        status=status,
        authority="all canonical webhistory segment files plus raw Takeout archives",
        query_surface="lynchpin.sources.web",
        materialized_paths=(output, manifest) if manifest else (output,),
        raw_roots=(cfg.webhistory_raw_dir, cfg.webhistory_dir, cfg.exports_root / "google/raw/takeout"),
        row_count=row_count,
        first_date=first,
        last_date=last,
        refresh_command="python -m lynchpin.ingest.webhistory",
        reason=reason,
    )


def _google_takeout_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    raw_root = cfg.exports_root / "google/raw/takeout"
    archives = tuple(discover_takeout_archives(raw_root))
    inventory_dir = google_takeout_inventory_dir()
    manifest = inventory_dir / "manifest.json"
    meta = _load_json(manifest)
    members = inventory_dir / "members.ndjson"
    archive_rows = inventory_dir / "archives.ndjson"
    products_dir = google_takeout_products_dir()
    products_manifest = products_dir / "manifest.json"
    product_meta = _load_json(products_manifest)
    raw_product_counts = product_meta.get("products")
    product_counts = raw_product_counts if isinstance(raw_product_counts, dict) else {}
    typed_rows = sum(
        _int_or_none(row.get("row_count")) or 0
        for row in product_counts.values()
        if isinstance(row, dict)
    )
    if not archives:
        status = "missing"
        reason = "no raw Takeout archives found"
    elif products_manifest.exists():
        status = "ready"
        reason = (
            f"{len(archives)} raw Takeout archives inventoried; Chrome history plus "
            f"{typed_rows} non-Chrome typed product rows are materialized"
        )
    else:
        status = "partial"
        reason = f"{len(archives)} raw Takeout archives inventoried but typed non-Chrome product rows are missing"
    return MaterializedDataset(
        name="google_takeout",
        status=status,
        authority="raw Google Takeout archives",
        query_surface="lynchpin.sources.google_takeout plus lynchpin.sources.google_takeout_products",
        materialized_paths=(archive_rows, members, manifest, products_manifest),
        raw_roots=(raw_root,),
        row_count=(_int_or_none(meta.get("member_count")) or len(archives)) + typed_rows,
        first_date=None,
        last_date=None,
        refresh_command="python -m lynchpin.ingest.google_takeout_materialize && python -m lynchpin.ingest.google_takeout_products",
        reason=reason,
    )


def _polylogue_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    readiness = archive_readiness()
    return MaterializedDataset(
        name="polylogue",
        status="ready" if readiness.status == "ready" else readiness.status,
        authority="Polylogue archive database",
        query_surface="lynchpin.sources.polylogue",
        materialized_paths=(readiness.db_path,),
        raw_roots=(cfg.polylogue_archive_root, cfg.polylogue_root),
        row_count=readiness.session_profile_count,
        first_date=None,
        last_date=None,
        refresh_command="polylogue doctor --repair --target session_insights",
        reason=readiness.reason,
    )


def _activitywatch_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    path = canonical_activitywatch_events_path()
    manifest = path.with_suffix(".manifest.json")
    meta = _load_json(manifest)
    archives = _count_files(cfg.activitywatch_archive_db_dir, suffixes=(".sqlite", ".db"))
    if path.exists() and manifest.exists():
        status = "ready"
        reason = "canonical ActivityWatch event NDJSON is present"
    elif cfg.activitywatch_db.exists():
        status = "partial"
        reason = "canonical ActivityWatch event product is missing"
    else:
        status = "missing"
        reason = "live ActivityWatch DB is missing"
    return MaterializedDataset(
        name="activitywatch",
        status=status,
        authority="ActivityWatch live SQLite plus exported backup DBs",
        query_surface="lynchpin.sources.activitywatch",
        materialized_paths=(path, manifest),
        raw_roots=(cfg.activitywatch_db, cfg.exports_root / "activitywatch/raw"),
        row_count=_int_or_none(meta.get("row_count")) or archives,
        first_date=_date_from_iso(meta.get("first_date")),
        last_date=_date_from_iso(meta.get("last_date")),
        refresh_command="python -m lynchpin.ingest.activitywatch_materialize",
        reason=reason,
    )


def _atuin_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    path = canonical_atuin_history_path()
    manifest = path.with_suffix(".manifest.json")
    meta = _load_json(manifest)
    return MaterializedDataset(
        name="atuin",
        status="ready" if path.exists() and manifest.exists() else "partial" if cfg.atuin_db.exists() else "missing",
        authority="Atuin live SQLite",
        query_surface="lynchpin.sources.terminal",
        materialized_paths=(path, manifest),
        raw_roots=(cfg.atuin_db,),
        row_count=_int_or_none(meta.get("row_count")) or _line_count(path) if path.exists() else None,
        first_date=_date_from_iso(meta.get("first_date")),
        last_date=_date_from_iso(meta.get("last_date")),
        refresh_command="python -m lynchpin.ingest.terminal_materialize",
        reason="canonical Atuin command history NDJSON is present" if path.exists() and manifest.exists() else "canonical Atuin command history product is missing",
    )


def _git_substrate_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    from .substrate.connection import substrate_path

    path = substrate_path()
    builds = _sqlite_duck_count(path, "evidence_graph_build")
    status = "ready" if builds and builds > 0 else "partial"
    return MaterializedDataset(
        name="evidence_graph_substrate",
        status=status,
        authority="source modules promoted into DuckDB",
        query_surface="lynchpin.graph.context_pack",
        materialized_paths=(path,),
        raw_roots=(cfg.baseline_dir, cfg.repo_root.parent),
        row_count=builds,
        first_date=None,
        last_date=None,
        refresh_command="python -m lynchpin.cli.current_state --refresh-substrate --start 2013-01-01 --end $(date +%F)",
        reason="DuckDB evidence graph builds are present" if status == "ready" else "no materialized evidence graph build recorded",
    )


def _health_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    root = cfg.exports_root / "health/processed"
    files = tuple(root.glob("health_*.jsonl")) if root.exists() else ()
    status = "ready" if files else "missing"
    row_count = sum(_line_count(path) for path in files)
    _, first, last = _jsonl_date_bounds(files)
    return MaterializedDataset(
        name="health",
        status=status,
        authority="Samsung Health raw exports",
        query_surface="lynchpin.sources.health",
        materialized_paths=files,
        raw_roots=(cfg.exports_root / "health/raw",),
        row_count=row_count if files else None,
        first_date=first,
        last_date=last,
        refresh_command="python -m lynchpin.cli.process_health",
        reason="processed health JSONL products are present" if files else "processed health JSONL products are missing",
    )


def _sleep_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    row_count, first, last = _jsonl_file_bounds(cfg.sleep_jsonl)
    return MaterializedDataset(
        name="sleep",
        status="ready" if cfg.sleep_jsonl.exists() else "missing",
        authority="Samsung Health/Sleep-as-Android exports",
        query_surface="lynchpin.sources.sleep",
        materialized_paths=(cfg.sleep_jsonl,),
        raw_roots=(cfg.exports_root / "health/raw",),
        row_count=row_count,
        first_date=first,
        last_date=last,
        refresh_command="python -m lynchpin.cli.process_health",
        reason="processed sleep JSONL is present" if cfg.sleep_jsonl.exists() else "processed sleep JSONL is missing",
    )


def _substance_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    path = cfg.exports_root / "health/processed/substance_log_unified.csv"
    return MaterializedDataset(
        name="substance",
        status="ready" if path.exists() else "missing",
        authority="processed substance log CSV",
        query_surface="lynchpin.sources.substance",
        materialized_paths=(path,),
        raw_roots=(cfg.exports_root / "health/processed",),
        row_count=max(_line_count(path) - 1, 0) if path.exists() else None,
        first_date=None,
        last_date=None,
        refresh_command="edit /realm/data/exports/health/processed/substance_log_unified.csv",
        reason="processed substance CSV is present" if path.exists() else "processed substance CSV is missing",
    )


def _spotify_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    path = spotify_streams_path()
    manifest = path.with_suffix(".manifest.json")
    meta = _load_json(manifest)
    raw_files = tuple(cfg.spotify_root.rglob("Streaming*.json")) if cfg.spotify_root.exists() else ()
    row_count = _int_or_none(meta.get("row_count"))
    return MaterializedDataset(
        name="spotify",
        status="ready" if path.exists() and manifest.exists() else "partial" if raw_files else "missing",
        authority="Spotify GDPR export directories",
        query_surface="lynchpin.sources.spotify",
        materialized_paths=(path, manifest),
        raw_roots=(cfg.spotify_root,),
        row_count=row_count if row_count is not None else _line_count(path) if path.exists() else None,
        first_date=_date_from_iso(meta.get("first_date")),
        last_date=_date_from_iso(meta.get("last_date")),
        refresh_command="python -m lynchpin.ingest.exports_materialize spotify",
        reason="canonical all-export Spotify stream NDJSON is present" if path.exists() and manifest.exists() else "canonical Spotify stream NDJSON is missing",
    )


def _reddit_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    root = reddit_canonical_dir()
    manifest = root / "manifest.json"
    files = tuple(root.glob("*.csv")) if root.exists() else ()
    meta = _load_json(manifest)
    return MaterializedDataset(
        name="reddit",
        status="ready" if files and manifest.exists() else "partial" if cfg.reddit_export_dir else "missing",
        authority="Reddit GDPR export directories",
        query_surface="lynchpin.sources.reddit",
        materialized_paths=(root, manifest),
        raw_roots=(cfg.exports_root / "reddit/processed", cfg.exports_root / "reddit/raw"),
        row_count=_int_or_none(meta.get("row_count")) or len(files) if files else None,
        first_date=None,
        last_date=None,
        refresh_command="python -m lynchpin.ingest.exports_materialize reddit",
        reason="canonical coalesced Reddit CSV products are present" if files and manifest.exists() else "canonical Reddit CSV products are missing",
    )


def _messenger_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    root = messenger_canonical_dir()
    manifest = root / "manifest.json"
    messages = root / "messages.ndjson"
    threads = root / "threads.ndjson"
    meta = _load_json(manifest)
    raw_paths = tuple(path for path in (cfg.fbmessenger_gdpr_root, cfg.fbmessenger_db) if path.exists())
    return MaterializedDataset(
        name="facebook_messenger",
        status="ready" if messages.exists() and threads.exists() and manifest.exists() else "partial" if raw_paths else "missing",
        authority="Facebook Messenger GDPR export",
        query_surface="lynchpin.sources.exports",
        materialized_paths=(messages, threads, manifest),
        raw_roots=(cfg.exports_root / "comms/facebook-messenger/raw",),
        row_count=_int_or_none(meta.get("row_count")),
        first_date=_date_from_iso(meta.get("first_date")),
        last_date=_date_from_iso(meta.get("last_date")),
        refresh_command="python -m lynchpin.ingest.exports_materialize facebook-messenger",
        reason="canonical Messenger message/thread NDJSON products are present" if messages.exists() and threads.exists() and manifest.exists() else "canonical Messenger products are missing",
    )


def _raindrop_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    path = raindrop_bookmarks_path()
    manifest = path.with_suffix(".manifest.json")
    meta = _load_json(manifest)
    return MaterializedDataset(
        name="raindrop",
        status="ready" if path.exists() and manifest.exists() else "partial" if cfg.raindrop_csv and cfg.raindrop_csv.exists() else "missing",
        authority="Raindrop export CSVs",
        query_surface="lynchpin.sources.exports",
        materialized_paths=(path, manifest),
        raw_roots=(cfg.raindrop_dir,),
        row_count=_int_or_none(meta.get("row_count")) or max(_line_count(path) - 1, 0) if path.exists() else None,
        first_date=_date_from_iso(meta.get("first_date")),
        last_date=_date_from_iso(meta.get("last_date")),
        refresh_command="python -m lynchpin.ingest.exports_materialize raindrop",
        reason="canonical coalesced Raindrop bookmark CSV is present" if path.exists() and manifest.exists() else "canonical Raindrop bookmark product is missing",
    )


def _historical_browser_db_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    root = cfg.captures_root / "webhistory/browser-dbs/historical"
    files = tuple(root.rglob("*.db")) if root.exists() else ()
    return MaterializedDataset(
        name="historical_browser_dbs",
        status="ready" if files and cfg.webhistory_ndjson and cfg.webhistory_ndjson.exists() else "partial",
        authority="copied browser DBs from old disk images",
        query_surface="lynchpin.ingest.webhistory -> lynchpin.sources.web",
        materialized_paths=(cfg.webhistory_ndjson,) if cfg.webhistory_ndjson else (),
        raw_roots=(root,),
        row_count=len(files) if files else None,
        first_date=None,
        last_date=None,
        refresh_command="python -m lynchpin.ingest.webhistory",
        reason="historical browser DBs are represented through canonical webhistory materialization",
    )


def _machine_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    manifest = canonical_machine_table_path("manifest").with_suffix(".json")
    meta = _load_json(manifest)
    tables = meta.get("tables") if isinstance(meta.get("tables"), dict) else {}
    paths = tuple(
        canonical_machine_table_path(name)
        for name in ("metric_sample", "gpu_sample", "network_sample", "service_state")
    )
    ready = manifest.exists() and all(path.exists() for path in paths)
    return MaterializedDataset(
        name="machine",
        status="ready" if ready else "partial" if cfg.machine_telemetry_db.exists() else "missing",
        authority="machine telemetry SQLite/JSONL captures",
        query_surface="lynchpin.sources.machine plus analysis machine artifacts",
        materialized_paths=(*paths, manifest),
        raw_roots=(cfg.machine_capture_root,),
        row_count=_int_or_none(meta.get("row_count")),
        first_date=_date_from_iso(_first_table_date(tables)),
        last_date=_date_from_iso(_last_table_date(tables)),
        refresh_command="python -m lynchpin.ingest.machine_materialize",
        reason="canonical machine telemetry NDJSON tables are present" if ready else "canonical machine telemetry products are missing",
    )


def _first_table_date(tables: object) -> str | None:
    if not isinstance(tables, dict):
        return None
    dates = [
        str(table.get("first_date"))
        for table in tables.values()
        if isinstance(table, dict) and table.get("first_date")
    ]
    return min(dates) if dates else None


def _last_table_date(tables: object) -> str | None:
    if not isinstance(tables, dict):
        return None
    dates = [
        str(table.get("last_date"))
        for table in tables.values()
        if isinstance(table, dict) and table.get("last_date")
    ]
    return max(dates) if dates else None


def _recovery_ontology_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    recovery = cfg.libraries_root / "machine-recovery"
    has_remaining = recovery.exists() and any(recovery.iterdir())
    status = "partial" if has_remaining else "ready"
    return MaterializedDataset(
        name="ontology_migration_backlog",
        status=status,
        authority="files not yet moved into canonical /realm/data ontology roots",
        query_surface="/realm/data ontology roots",
        materialized_paths=(),
        raw_roots=(recovery,),
        row_count=None,
        first_date=None,
        last_date=None,
        refresh_command="move remaining files into canonical /realm/data homes, then remove /realm/data/libraries/machine-recovery",
        reason="files remain outside canonical ontology roots under /realm/data/libraries/machine-recovery" if has_remaining else "machine-recovery has been dismantled",
    )


def _missing_dataset(
    name: str,
    authority: str,
    query_surface: str,
    refresh_command: str,
    reason: str,
    *,
    materialized_paths: tuple[Path, ...] = (),
    raw_roots: tuple[Path, ...] = (),
) -> MaterializedDataset:
    return MaterializedDataset(
        name=name,
        status="missing",
        authority=authority,
        query_surface=query_surface,
        materialized_paths=materialized_paths,
        raw_roots=raw_roots,
        row_count=None,
        first_date=None,
        last_date=None,
        refresh_command=refresh_command,
        reason=reason,
    )


def _scan_webhistory_ndjson(path: Path) -> tuple[int, date | None, date | None]:
    row_count = 0
    first: date | None = None
    last: date | None = None
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row_count += 1
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            stamp = _date_from_iso(payload.get("iso_time") or payload.get("time") or payload.get("date"))
            if stamp is None:
                continue
            first = stamp if first is None or stamp < first else first
            last = stamp if last is None or stamp > last else last
    return row_count, first, last


def _jsonl_file_bounds(path: Path) -> tuple[int | None, date | None, date | None]:
    if not path.exists():
        return None, None, None
    count, first, last = _jsonl_date_bounds((path,))
    return count, first, last


def _jsonl_date_bounds(paths: Iterable[Path]) -> tuple[int, date | None, date | None]:
    count = 0
    first: date | None = None
    last: date | None = None
    for path in paths:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                count += 1
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                stamp = _date_from_payload(payload)
                if stamp is None:
                    continue
                first = stamp if first is None or stamp < first else first
                last = stamp if last is None or stamp > last else last
    return count, first, last


def _date_from_payload(payload: dict[str, Any]) -> date | None:
    for key in ("date", "day", "start_time", "timestamp", "created_at", "time"):
        stamp = _date_from_iso(payload.get(key))
        if stamp is not None:
            return stamp
    return None


def _date_from_iso(value: object) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    raw = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(raw).date()
    except ValueError:
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            return None


def _coverage_label(first: date | None, last: date | None) -> str:
    if first and last:
        return f"{first.isoformat()} -> {last.isoformat()}"
    if first:
        return first.isoformat()
    if last:
        return last.isoformat()
    return "-"


def _load_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _int_or_none(value: object) -> int | None:
    if not isinstance(value, (str, int, float)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _line_count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("rb") as handle:
        return sum(1 for _ in handle)


def _count_files(root: Path, *, suffixes: tuple[str, ...] | None = None) -> int:
    if not root.exists():
        return 0
    return sum(
        1
        for path in root.rglob("*")
        if path.is_file() and (suffixes is None or path.suffix.lower() in suffixes)
    )


def _sqlite_count(path: Path, table: str) -> int | None:
    try:
        with sqlite3.connect(path) as conn:
            row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    except sqlite3.Error:
        return None
    return int(row[0]) if row else None


def _sqlite_duck_count(path: Path, table: str) -> int | None:
    if not path.exists():
        return None
    try:
        import duckdb

        conn = duckdb.connect(str(path), read_only=True)
        try:
            row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        finally:
            conn.close()
    except Exception:
        return None
    return int(row[0]) if row else None
