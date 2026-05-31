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

import csv
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .core.config import LynchpinConfig, get_config
from .core.errors import MaterializationError
from .core.source_contracts import (
    DatasetStatus,
    SOURCE_CONTRACT_NAMES,
    dataset_status_to_substrate_status,
    source_contract,
)
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
from .ingest.activity_content_materialize import materialize_activity_content
from .ingest.terminal_materialize import materialize_atuin_history
from .ingest.title_metadata_materialize import materialize_title_metadata
from .ingest.machine_materialize import materialize_machine_telemetry
from .ingest.personal_signals_materialize import (
    materialize_personal_daily_signals,
    materialize_spotify_daily,
)
from .ingest.google_takeout_materialize import (
    google_takeout_inventory_dir,
    materialize_google_takeout_inventory,
)
from .ingest.google_takeout_products import (
    google_takeout_products_dir,
    materialize_google_takeout_products,
)
from .ingest.bookmarks_materialize import materialize_bookmarks
from .ingest.communications_materialize import materialize_communication_events
from .ingest.arbtt_materialize import materialize_arbtt_events
from .ingest.irc_materialize import materialize_irc_events
from .sources.activitywatch_raw import canonical_activitywatch_events_path
from .sources.activity_content import activity_content_daily_path, activity_content_manifest_path, activity_title_usage_path
from .sources.machine import canonical_machine_table_path
from .sources.terminal import canonical_atuin_history_path
from .sources.title_metadata import title_metadata_manifest_path, title_metadata_path
from .sources.google_takeout import discover_takeout_archives
from .sources.polylogue import archive_readiness, iter_session_profiles
from .sources.bookmarks import bookmarks_manifest_path, bookmarks_path
from .sources.communications import communication_events_path, communication_manifest_path
from .sources.arbtt import arbtt_events_path, arbtt_manifest_path
from .sources.irc_raw import irc_events_path, irc_manifest_path, irc_raw_root
from .sources.personal_signals import (
    personal_daily_signals_manifest_path,
    personal_daily_signals_path,
    spotify_daily_manifest_path,
    spotify_daily_path,
)


Status = DatasetStatus


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
        contract = source_contract(self.name)
        coverage = materialized_dataset_coverage(self)
        return {
            "name": self.name,
            "status": self.status,
            "substrate_status": dataset_status_to_substrate_status(self.status),
            "kind": contract.kind,
            "required": contract.required,
            "empty_policy": contract.empty,
            "query_mode": contract.query_mode,
            "collection_model": contract.collection_model,
            "authority": self.authority,
            "query_surface": self.query_surface,
            "substrate_tables": list(contract.substrate_tables),
            "graph_node_kinds": list(contract.graph_node_kinds),
            "mcp_tools": list(contract.mcp_tools),
            "caveats": list(contract.caveats),
            "materialized_paths": [str(path) for path in self.materialized_paths],
            "raw_roots": [str(path) for path in self.raw_roots],
            "row_count": self.row_count,
            "first_date": self.first_date.isoformat() if self.first_date else None,
            "last_date": self.last_date.isoformat() if self.last_date else None,
            "coverage": coverage,
            "refresh_command": self.refresh_command,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class MaterializationPlanStep:
    name: str
    before: MaterializedDataset
    action: str
    refresh_command: str
    reason: str

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.before.status,
            "action": self.action,
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
    builders = _dataset_builders()
    return [builders[name](cfg) for name in SOURCE_CONTRACT_NAMES]


def _dataset_builders() -> dict[str, Any]:
    return {
        "webhistory": _webhistory_dataset,
        "google_takeout": _google_takeout_dataset,
        "polylogue": _polylogue_dataset,
        "activitywatch": _activitywatch_dataset,
        "title_metadata": _title_metadata_dataset,
        "activity_content": _activity_content_dataset,
        "atuin": _atuin_dataset,
        "evidence_graph_substrate": _git_substrate_dataset,
        "health": _health_dataset,
        "sleep": _sleep_dataset,
        "substance": _substance_dataset,
        "spotify": _spotify_dataset,
        "reddit": _reddit_dataset,
        "facebook_messenger": _messenger_dataset,
        "communications": _communications_dataset,
        "raindrop": _raindrop_dataset,
        "browser_bookmarks": _bookmarks_dataset,
        "arbtt": _arbtt_dataset,
        "machine": _machine_dataset,
        "xtask_history": _xtask_history_dataset,
        "spotify_daily": _spotify_daily_dataset,
        "personal_daily_signals": _personal_daily_signals_dataset,
        "irc": _irc_dataset,
    }


def _materializers() -> dict[str, Callable[[], Any]]:
    return {
        "webhistory": _materialize_webhistory,
        "google_takeout": _materialize_google_takeout,
        "activitywatch": materialize_activitywatch_events,
        "title_metadata": materialize_title_metadata,
        "activity_content": materialize_activity_content,
        "atuin": materialize_atuin_history,
        "spotify": materialize_spotify,
        "reddit": materialize_reddit,
        "facebook_messenger": materialize_messenger,
        "communications": materialize_communication_events,
        "raindrop": materialize_raindrop,
        "browser_bookmarks": materialize_bookmarks,
        "arbtt": materialize_arbtt_events,
        "machine": materialize_machine_telemetry,
        "spotify_daily": materialize_spotify_daily,
        "personal_daily_signals": materialize_personal_daily_signals,
        "irc": materialize_irc_events,
    }


def _materialize_webhistory() -> None:
    cfg = get_config()
    if cfg.webhistory_ndjson is None:
        raise MaterializationError(
            "webhistory",
            reason="canonical webhistory output path is not configured",
        )
    build_full_history(data_dir=cfg.webhistory_dir, output=cfg.webhistory_ndjson)


def _materialize_google_takeout() -> None:
    materialize_google_takeout_inventory()
    materialize_google_takeout_products()


def plan_materializations(
    *,
    cfg: LynchpinConfig | None = None,
    force: bool = False,
) -> list[MaterializationPlanStep]:
    """Return the deterministic local materialization plan."""
    cfg = cfg or get_config()
    materializers = _materializers()
    steps: list[MaterializationPlanStep] = []
    for row in audit_materialization(cfg=cfg):
        contract = source_contract(row.name)
        if row.name not in materializers:
            action = "check-only"
            reason = "no local materializer is defined for this contract"
        elif force or row.status != "ready":
            action = "materialize"
            reason = row.reason
        else:
            action = "skip"
            reason = "canonical product is ready"
        if action != "skip":
            steps.append(
                MaterializationPlanStep(
                    name=row.name,
                    before=row,
                    action=action,
                    refresh_command=contract.refresh_command,
                    reason=reason,
                )
            )
    return steps


def run_materialization_plan(
    steps: Iterable[MaterializationPlanStep],
    *,
    refresh_id: str | None = None,
) -> list[MaterializationPlanStep]:
    """Execute materialization steps and return the steps that actually ran."""
    materializers = _materializers()
    ran: list[MaterializationPlanStep] = []
    refresh_id = refresh_id or f"materialize:{datetime.now(timezone.utc).isoformat()}"
    for step in steps:
        if step.action != "materialize":
            continue
        started = datetime.now(timezone.utc)
        _record_materialization_step(
            refresh_id,
            step.name,
            "started",
            step.reason,
            started_at=started,
        )
        try:
            report = materializers[step.name]()
        except Exception as exc:
            _record_materialization_step(
                refresh_id,
                step.name,
                "error",
                str(exc),
                started_at=started,
                finished_at=datetime.now(timezone.utc),
            )
            raise
        row_count = report.get("row_count") if isinstance(report, dict) else None
        _record_materialization_step(
            refresh_id,
            step.name,
            "ok",
            "materialized",
            row_count=_int_or_none(row_count),
            started_at=started,
            finished_at=datetime.now(timezone.utc),
        )
        ran.append(step)
    return ran


def _record_materialization_step(
    refresh_id: str,
    step: str,
    status: str,
    message: str | None,
    *,
    row_count: int | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> None:
    try:
        from .substrate.connection import apply_schema, connect, substrate_path
        from .substrate.run_steps import record_run_step

        with connect(substrate_path()) as conn:
            apply_schema(conn)
            record_run_step(
                conn,
                refresh_id=refresh_id,
                step=f"materialize:{step}",
                status=status,
                message=message,
                row_count=row_count,
                started_at=started_at,
                finished_at=finished_at,
            )
    except Exception:
        # Observability must never make canonical product repair impossible.
        return


def ensure_supported_materializations(*, cfg: LynchpinConfig | None = None) -> None:
    """Materialize products this module can rebuild without extra credentials."""
    cfg = cfg or get_config()
    run_materialization_plan(plan_materializations(cfg=cfg))


def _product_with_manifest_exists(product: Path, manifest: Path) -> bool:
    return product.exists() and _manifest_valid(manifest)


def materialized_window_overlaps(
    source: str,
    *,
    start: date,
    end: date,
    cfg: LynchpinConfig | None = None,
) -> bool:
    """Return whether a materialized source has known coverage overlapping a window.

    Unknown source names and products without known date bounds fail closed. That
    keeps query-time graph/promotion code from treating an absent manifest field
    as permission to hydrate an entire historical source.
    """
    rows = {row.name: row for row in audit_materialization(cfg=cfg)}
    row = rows[source]
    return materialized_dataset_overlaps(row, start=start, end=end)


def materialized_dataset_overlaps(
    row: MaterializedDataset,
    *,
    start: date,
    end: date,
) -> bool:
    return bool(materialized_dataset_coverage(row, start=start, end=end)["overlaps_requested_window"])


def materialized_dataset_coverage(
    row: MaterializedDataset,
    *,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    """Describe product/window coverage without recency heuristics.

    Continuous sources are expected to cover the queried window. Export/event
    sources only report whether their materialized rows overlap the window:
    a gap may mean "no use" or "no export coverage" and must be interpreted by
    the caller, not collapsed into a rigid stale-days rule.
    """
    try:
        contract = source_contract(row.name)
        collection_model = contract.collection_model
    except KeyError:
        collection_model = "event_export"
    product_dated = row.first_date is not None and row.last_date is not None
    requested_days = _requested_day_count(start, end)
    if row.status != "ready":
        relation = "unavailable"
        covered_days = 0
        overlaps = False
        fully_covers = False
    elif not product_dated:
        relation = "undated"
        covered_days = None
        overlaps = None
        fully_covers = None
    elif start is None or end is None:
        relation = "dated"
        covered_days = None
        overlaps = None
        fully_covers = None
    else:
        covered_days = _covered_day_count(row.first_date, row.last_date, start=start, end=end)
        overlaps = covered_days > 0
        fully_covers = covered_days >= (requested_days or 0) if requested_days is not None else False
        if fully_covers:
            relation = "covers_window"
        elif overlaps:
            relation = "partial_overlap"
        else:
            relation = "no_overlap"
    ratio = (
        round(covered_days / requested_days, 6)
        if isinstance(covered_days, int) and requested_days
        else None
    )
    return {
        "collection_model": collection_model,
        "product_has_date_bounds": product_dated,
        "requested_days": requested_days,
        "covered_days": covered_days,
        "coverage_ratio": ratio,
        "overlaps_requested_window": overlaps,
        "fully_covers_requested_window": fully_covers,
        "relation": relation,
        "interpretation": _coverage_interpretation(collection_model, relation),
    }


def _date_bounds_overlap(first: date, last: date, *, start: date, end: date) -> bool:
    return first < end and last >= start


def _requested_day_count(start: date | None, end: date | None) -> int | None:
    if start is None or end is None or end <= start:
        return None
    return (end - start).days


def _covered_day_count(first: date, last: date, *, start: date, end: date) -> int:
    # Dataset bounds are inclusive by day; requested windows are [start, end).
    left = max(first, start)
    right = min(last, end)
    if right < left:
        return 0
    return (right - left).days + 1


def _coverage_interpretation(collection_model: str, relation: str) -> str:
    if relation == "unavailable":
        return "canonical product is not ready"
    if relation == "undated":
        return "product is valid metadata but has no temporal coverage bounds"
    if relation == "no_overlap" and collection_model == "continuous":
        return "requested window is outside known continuous-capture coverage"
    if relation == "no_overlap":
        return "no materialized rows overlap the requested window; for event/export sources this is not proof of zero activity"
    if relation == "partial_overlap" and collection_model == "continuous":
        return "continuous source only partially covers the requested window"
    if relation == "partial_overlap":
        return "some materialized event/export rows overlap the requested window"
    if relation == "covers_window":
        return "known materialized date bounds cover the requested window"
    return "known materialized date bounds are available"


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
    contract = source_contract("webhistory")
    output = cfg.webhistory_ndjson
    manifest = full_history_manifest_path(output) if output is not None else None
    if output is None:
        return _missing_dataset(
            "webhistory",
            contract.authority,
            contract.query_surface,
            contract.refresh_command,
            "canonical full_history.ndjson is not configured",
            raw_roots=(cfg.webhistory_raw_dir, cfg.webhistory_dir),
        )
    if not output.exists():
        return _missing_dataset(
            "webhistory",
            contract.authority,
            contract.query_surface,
            contract.refresh_command,
            f"canonical NDJSON is missing: {output}",
            materialized_paths=(output,),
            raw_roots=(cfg.webhistory_raw_dir, cfg.webhistory_dir),
        )

    manifest_valid = bool(manifest and _manifest_valid(manifest))
    meta = _load_json(manifest) if manifest_valid else {}
    row_count = _int_or_none(meta.get("row_count"))
    first = _date_from_iso(meta.get("first_visit_at"))
    last = _date_from_iso(meta.get("last_visit_at"))
    if row_count is None or first is None or last is None:
        row_count, first, last = _scan_webhistory_ndjson(output)
    status = "ready" if manifest_valid else "degraded"
    reason = "canonical merged NDJSON and manifest are present" if status == "ready" else "canonical merged NDJSON exists but manifest is missing or malformed"
    return MaterializedDataset(
        name="webhistory",
        status=status,
        authority=contract.authority,
        query_surface=contract.query_surface,
        materialized_paths=(output, manifest) if manifest else (output,),
        raw_roots=(cfg.webhistory_raw_dir, cfg.webhistory_dir, cfg.exports_root / "google/raw/takeout"),
        row_count=row_count,
        first_date=first,
        last_date=last,
        refresh_command=contract.refresh_command,
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
    products_manifest_valid = _manifest_valid(products_manifest)
    raw_product_counts = product_meta.get("products")
    product_counts = raw_product_counts if isinstance(raw_product_counts, dict) else {}
    typed_rows = sum(
        _int_or_none(row.get("row_count")) or 0
        for row in product_counts.values()
        if isinstance(row, dict)
    )
    _, first, last = _jsonl_date_bounds(
        (
            products_dir / "my_activity.ndjson",
            products_dir / "youtube.ndjson",
            products_dir / "purchases.ndjson",
            products_dir / "tasks.ndjson",
            products_dir / "play_store.ndjson",
        )
    )
    if not archives:
        status = "missing"
        reason = "no raw Takeout archives found"
    elif products_manifest_valid:
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
        first_date=first,
        last_date=last,
        refresh_command="python -m lynchpin.ingest.google_takeout_materialize && python -m lynchpin.ingest.google_takeout_products",
        reason=reason,
    )


def _polylogue_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    readiness = archive_readiness()
    first, last = _polylogue_date_bounds() if readiness.status == "ready" else (None, None)
    return MaterializedDataset(
        name="polylogue",
        status="ready" if readiness.status == "ready" else readiness.status,
        authority="Polylogue archive database",
        query_surface="lynchpin.sources.polylogue",
        materialized_paths=(readiness.db_path,),
        raw_roots=(cfg.polylogue_archive_root, cfg.polylogue_root),
        row_count=readiness.session_profile_count,
        first_date=first,
        last_date=last,
        refresh_command="polylogue doctor --repair --target session_insights",
        reason=readiness.reason,
    )


def _activitywatch_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    path = canonical_activitywatch_events_path()
    manifest = path.with_suffix(".manifest.json")
    meta = _load_json(manifest)
    archives = _count_files(cfg.activitywatch_archive_db_dir, suffixes=(".sqlite", ".db"))
    if _product_with_manifest_exists(path, manifest):
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


def _title_metadata_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    contract = source_contract("title_metadata")
    path = title_metadata_path()
    manifest = title_metadata_manifest_path()
    meta = _load_json(manifest)
    source_db = Path(str(meta.get("source_db"))) if meta.get("source_db") else cfg.local_root / "enrich/semantic_classifications.duckdb"
    ready = _product_with_manifest_exists(path, manifest)
    row_count = _int_or_none(meta.get("row_count")) or (_line_count(path) if path.exists() else None)
    raw_roots = tuple(root for root in (source_db, cfg.local_root / "enrich") if root.exists())
    return MaterializedDataset(
        name="title_metadata",
        status="ready" if ready else "partial" if raw_roots else "missing",
        authority=contract.authority,
        query_surface=contract.query_surface,
        materialized_paths=(path, manifest),
        raw_roots=raw_roots or (cfg.local_root / "enrich",),
        row_count=row_count,
        first_date=None,
        last_date=None,
        refresh_command=contract.refresh_command,
        reason="canonical title metadata NDJSON is present" if ready else "canonical title metadata product is missing",
    )


def _activity_content_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    contract = source_contract("activity_content")
    path = activity_content_daily_path()
    usage = activity_title_usage_path()
    manifest = activity_content_manifest_path()
    meta = _load_json(manifest)
    ready = _product_with_manifest_exists(path, manifest) and usage.exists()
    has_inputs = canonical_activitywatch_events_path().exists() and title_metadata_path().exists()
    return MaterializedDataset(
        name="activity_content",
        status="ready" if ready else "partial" if has_inputs else "missing",
        authority=contract.authority,
        query_surface=contract.query_surface,
        materialized_paths=(path, usage, manifest),
        raw_roots=(cfg.derived_root / "title_metadata", cfg.captures_root / "activitywatch"),
        row_count=_int_or_none(meta.get("row_count")) or (_line_count(path) if path.exists() else None),
        first_date=_date_from_iso(meta.get("first_date")),
        last_date=_date_from_iso(meta.get("last_date")),
        refresh_command=contract.refresh_command,
        reason="canonical ActivityWatch content daily product is present" if ready else "canonical ActivityWatch content daily product is missing",
    )


def _atuin_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    path = canonical_atuin_history_path()
    manifest = path.with_suffix(".manifest.json")
    meta = _load_json(manifest)
    return MaterializedDataset(
        name="atuin",
        status="ready" if _product_with_manifest_exists(path, manifest) else "partial" if cfg.atuin_db.exists() else "missing",
        authority="Atuin live SQLite",
        query_surface="lynchpin.sources.terminal",
        materialized_paths=(path, manifest),
        raw_roots=(cfg.atuin_db,),
        row_count=_int_or_none(meta.get("row_count")) or _line_count(path) if path.exists() else None,
        first_date=_date_from_iso(meta.get("first_date")),
        last_date=_date_from_iso(meta.get("last_date")),
        refresh_command="python -m lynchpin.ingest.terminal_materialize",
        reason="canonical Atuin command history NDJSON is present" if _product_with_manifest_exists(path, manifest) else "canonical Atuin command history product or manifest is missing/malformed",
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
    row_count, first, last = _csv_date_bounds((path,))
    return MaterializedDataset(
        name="substance",
        status="ready" if path.exists() else "missing",
        authority="processed substance log CSV",
        query_surface="lynchpin.sources.substance",
        materialized_paths=(path,),
        raw_roots=(cfg.exports_root / "health/processed",),
        row_count=row_count,
        first_date=first,
        last_date=last,
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
        status="ready" if _product_with_manifest_exists(path, manifest) else "partial" if raw_files else "missing",
        authority="Spotify GDPR export directories",
        query_surface="lynchpin.sources.spotify",
        materialized_paths=(path, manifest),
        raw_roots=(cfg.spotify_root,),
        row_count=row_count if row_count is not None else _line_count(path) if path.exists() else None,
        first_date=_date_from_iso(meta.get("first_date")),
        last_date=_date_from_iso(meta.get("last_date")),
        refresh_command="python -m lynchpin.ingest.exports_materialize spotify",
        reason="canonical all-export Spotify stream NDJSON is present" if _product_with_manifest_exists(path, manifest) else "canonical Spotify stream product or manifest is missing/malformed",
    )


def _reddit_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    root = reddit_canonical_dir()
    manifest = root / "manifest.json"
    files = tuple(root.glob("*.csv")) if root.exists() else ()
    meta = _load_json(manifest)
    csv_count, first, last = _csv_date_bounds(files)
    return MaterializedDataset(
        name="reddit",
        status="ready" if files and _manifest_valid(manifest) else "partial" if cfg.reddit_export_dir else "missing",
        authority="Reddit GDPR export directories",
        query_surface="lynchpin.sources.reddit",
        materialized_paths=(root, manifest),
        raw_roots=(cfg.exports_root / "reddit/processed", cfg.exports_root / "reddit/raw"),
        row_count=_int_or_none(meta.get("row_count")) or csv_count if files else None,
        first_date=first,
        last_date=last,
        refresh_command="python -m lynchpin.ingest.exports_materialize reddit",
        reason="canonical coalesced Reddit CSV products are present" if files and _manifest_valid(manifest) else "canonical Reddit CSV products or manifest are missing/malformed",
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
        status="ready" if messages.exists() and threads.exists() and _manifest_valid(manifest) else "partial" if raw_paths else "missing",
        authority="Facebook Messenger GDPR export",
        query_surface="lynchpin.sources.exports",
        materialized_paths=(messages, threads, manifest),
        raw_roots=(cfg.exports_root / "comms/facebook-messenger/raw",),
        row_count=_int_or_none(meta.get("row_count")),
        first_date=_date_from_iso(meta.get("first_date")),
        last_date=_date_from_iso(meta.get("last_date")),
        refresh_command="python -m lynchpin.ingest.exports_materialize facebook-messenger",
        reason="canonical Messenger message/thread NDJSON products are present" if messages.exists() and threads.exists() and _manifest_valid(manifest) else "canonical Messenger products or manifest are missing/malformed",
    )


def _raindrop_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    path = raindrop_bookmarks_path()
    manifest = path.with_suffix(".manifest.json")
    meta = _load_json(manifest)
    return MaterializedDataset(
        name="raindrop",
        status="ready" if _product_with_manifest_exists(path, manifest) else "partial" if cfg.raindrop_csv and cfg.raindrop_csv.exists() else "missing",
        authority="Raindrop export CSVs",
        query_surface="lynchpin.sources.exports",
        materialized_paths=(path, manifest),
        raw_roots=(cfg.raindrop_dir,),
        row_count=_int_or_none(meta.get("row_count")) or max(_line_count(path) - 1, 0) if path.exists() else None,
        first_date=_date_from_iso(meta.get("first_date")),
        last_date=_date_from_iso(meta.get("last_date")),
        refresh_command="python -m lynchpin.ingest.exports_materialize raindrop",
        reason="canonical coalesced Raindrop bookmark CSV is present" if _product_with_manifest_exists(path, manifest) else "canonical Raindrop bookmark product or manifest is missing/malformed",
    )


def _communications_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    path = communication_events_path()
    manifest = communication_manifest_path()
    meta = _load_json(manifest)
    raw_paths = tuple(path for path in (cfg.fbmessenger_gdpr_root, cfg.exports_root / "comms/outlook") if path.exists())
    ready = _product_with_manifest_exists(path, manifest)
    row_count = _int_or_none(meta.get("row_count")) or (_line_count(path) if path.exists() else None)
    return MaterializedDataset(
        name="communications",
        status="ready" if ready else "partial" if raw_paths else "missing",
        authority="canonical Messenger plus parseable Outlook communication exports",
        query_surface="lynchpin.sources.communications",
        materialized_paths=(path, manifest),
        raw_roots=(cfg.exports_root / "comms",),
        row_count=row_count,
        first_date=_date_from_iso(meta.get("first_date")),
        last_date=_date_from_iso(meta.get("last_date")),
        refresh_command="python -m lynchpin.ingest.communications_materialize",
        reason="canonical unified communication events are present" if ready else "canonical communication event product is missing",
    )


def _bookmarks_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    path = bookmarks_path()
    manifest = bookmarks_manifest_path()
    meta = _load_json(manifest)
    raw_files = tuple(cfg.browser_bookmarks_root.rglob("*")) if cfg.browser_bookmarks_root.exists() else ()
    ready = _product_with_manifest_exists(path, manifest)
    row_count = _int_or_none(meta.get("row_count")) or (_line_count(path) if path.exists() else None)
    return MaterializedDataset(
        name="browser_bookmarks",
        status="ready" if ready else "partial" if raw_files else "missing",
        authority="browser bookmark exports and Firefox/Vivaldi profile data",
        query_surface="lynchpin.sources.bookmarks",
        materialized_paths=(path, manifest),
        raw_roots=(cfg.browser_bookmarks_root,),
        row_count=row_count,
        first_date=_date_from_iso(meta.get("first_date")),
        last_date=_date_from_iso(meta.get("last_date")),
        refresh_command="python -m lynchpin.ingest.bookmarks_materialize",
        reason="canonical bookmark NDJSON is present" if ready else "canonical bookmark product is missing",
    )


def _arbtt_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    path = arbtt_events_path()
    manifest = arbtt_manifest_path()
    meta = _load_json(manifest)
    raw_files = tuple(cfg.arbtt_root.rglob("capture.log")) if cfg.arbtt_root.exists() else ()
    row_count = _int_or_none(meta.get("row_count")) or (_line_count(path) if path.exists() else None)
    ready = _product_with_manifest_exists(path, manifest) and (row_count or 0) > 0
    return MaterializedDataset(
        name="arbtt",
        status="ready" if ready else "partial" if raw_files else "missing",
        authority="ARBTT capture.log files",
        query_surface="lynchpin.sources.arbtt",
        materialized_paths=(path, manifest),
        raw_roots=(cfg.arbtt_root,),
        row_count=row_count,
        first_date=_date_from_iso(meta.get("first_date")),
        last_date=_date_from_iso(meta.get("last_date")),
        refresh_command="python -m lynchpin.ingest.arbtt_materialize",
        reason="canonical ARBTT focus events are present" if ready else "canonical ARBTT product is missing or empty; ensure arbtt-dump is available",
    )


def _irc_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    path = irc_events_path()
    manifest = irc_manifest_path()
    meta = _load_json(manifest)
    raw_root = irc_raw_root()
    raw_files = tuple(raw_root.rglob("*.log")) if raw_root.exists() else ()
    row_count = _int_or_none(meta.get("row_count")) or (_line_count(path) if path.exists() else None)
    ready = _product_with_manifest_exists(path, manifest) and (row_count or 0) > 0
    return MaterializedDataset(
        name="irc",
        status="ready" if ready else "partial" if raw_files else "missing",
        authority="raw WeeChat IRC log files",
        query_surface="lynchpin.sources.irc_raw",
        materialized_paths=(path, manifest),
        raw_roots=(raw_root,),
        row_count=row_count,
        first_date=_date_from_iso(meta.get("first_date")),
        last_date=_date_from_iso(meta.get("last_date")),
        refresh_command="python -m lynchpin.ingest.irc_materialize",
        reason="canonical IRC events ndjson is present" if ready else "raw WeeChat logs need materialization" if raw_files else "no raw IRC log files found",
    )


def _machine_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    manifest = canonical_machine_table_path("manifest").with_suffix(".json")
    meta = _load_json(manifest)
    tables = meta.get("tables") if isinstance(meta.get("tables"), dict) else {}
    paths = tuple(
        canonical_machine_table_path(name)
        for name in ("metric_sample", "gpu_sample", "network_sample", "service_state")
    )
    ready = _manifest_valid(manifest) and all(path.exists() for path in paths)
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


def _spotify_daily_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    contract = source_contract("spotify_daily")
    path = spotify_daily_path()
    manifest = spotify_daily_manifest_path()
    meta = _load_json(manifest)
    ready = _product_with_manifest_exists(path, manifest)
    return MaterializedDataset(
        name="spotify_daily",
        status="ready" if ready else "partial" if spotify_streams_path().exists() else "missing",
        authority=contract.authority,
        query_surface=contract.query_surface,
        materialized_paths=(path, manifest),
        raw_roots=(cfg.exports_root / "spotify/processed",),
        row_count=_int_or_none(meta.get("row_count")) or (_line_count(path) if path.exists() else None),
        first_date=_date_from_iso(meta.get("first_date")),
        last_date=_date_from_iso(meta.get("last_date")),
        refresh_command=contract.refresh_command,
        reason="canonical Spotify daily product is present" if ready else "canonical Spotify daily product or manifest is missing/malformed",
    )


def _xtask_history_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    contract = source_contract("xtask_history")
    path = cfg.xtask_history_db
    row_count: int | None = None
    first: date | None = None
    last: date | None = None
    if path.exists():
        try:
            import sqlite3

            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                row = conn.execute(
                    "SELECT COUNT(*), MIN(started_at), MAX(started_at) FROM invocations"
                ).fetchone()
            finally:
                conn.close()
            row_count = _int_or_none(row[0]) if row else None
            first = _date_from_iso(row[1]) if row and row[1] else None
            last = _date_from_iso(row[2]) if row and row[2] else None
        except Exception:
            row_count = None
    ready = path.exists() and row_count is not None
    return MaterializedDataset(
        name="xtask_history",
        status="ready" if ready else "missing",
        authority=contract.authority,
        query_surface=contract.query_surface,
        materialized_paths=(path,),
        raw_roots=(path.parent,),
        row_count=row_count,
        first_date=first,
        last_date=last,
        refresh_command=contract.refresh_command,
        reason="live xtask history SQLite is readable" if ready else f"xtask history SQLite is missing or unreadable at {path}",
    )


def _personal_daily_signals_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    contract = source_contract("personal_daily_signals")
    path = personal_daily_signals_path()
    manifest = personal_daily_signals_manifest_path()
    meta = _load_json(manifest)
    ready = _product_with_manifest_exists(path, manifest)
    return MaterializedDataset(
        name="personal_daily_signals",
        status="ready" if ready else "partial",
        authority=contract.authority,
        query_surface=contract.query_surface,
        materialized_paths=(path, manifest),
        raw_roots=(cfg.derived_root,),
        row_count=_int_or_none(meta.get("row_count")) or (_line_count(path) if path.exists() else None),
        first_date=_date_from_iso(meta.get("first_date")),
        last_date=_date_from_iso(meta.get("last_date")),
        refresh_command=contract.refresh_command,
        reason="canonical personal daily-signal product is present" if ready else "canonical personal daily-signal product or manifest is missing/malformed",
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
        if not path.exists():
            continue
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
    for key in (
        "date",
        "day",
        "start_time",
        "timestamp",
        "created_at",
        "time",
        "start_local",
        "end_local",
        "end_time",
    ):
        stamp = _date_from_iso(payload.get(key))
        if stamp is not None:
            return stamp
    return None


def _csv_date_bounds(paths: Iterable[Path]) -> tuple[int | None, date | None, date | None]:
    count = 0
    first: date | None = None
    last: date | None = None
    seen = False
    for path in paths:
        if not path.exists():
            continue
        seen = True
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                count += 1
                stamp = _date_from_payload(row)
                if stamp is None:
                    continue
                first = stamp if first is None or stamp < first else first
                last = stamp if last is None or stamp > last else last
    return (count if seen else None), first, last


def _polylogue_date_bounds() -> tuple[date | None, date | None]:
    """Best-effort scan over session_profile rows for first/last date.

    Tolerates ``PolylogueMaterializationError`` mid-iteration — when the
    archive is rematerializing or insight tables are incomplete the
    iterator may raise. The materialization snapshot is a status surface;
    it must NOT become an all-or-nothing fail-stop just because polylogue
    is briefly in transition. Caller will surface ``readiness`` separately.
    """
    from .sources.polylogue import PolylogueMaterializationError

    first: date | None = None
    last: date | None = None
    try:
        for profile in iter_session_profiles():
            stamp = profile.canonical_session_date
            if stamp is None and profile.first_message_at is not None:
                stamp = profile.first_message_at.date()
            if stamp is None:
                continue
            first = stamp if first is None or stamp < first else first
            last = stamp if last is None or stamp > last else last
    except PolylogueMaterializationError:
        # Bounds unavailable — return what we've collected so far (possibly
        # both None). Status surfaces the error via archive_readiness().
        pass
    return first, last


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


def _manifest_valid(path: Path | None) -> bool:
    return bool(_load_json(path))


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
