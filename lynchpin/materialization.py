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
import inspect
import json
import os
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Literal

from .core.cache import files_signature
from .core.config import LynchpinConfig, get_config
from .core.errors import MaterializationError
from .core.parse import iter_dates
from .core.source_contracts import (
    DatasetStatus,
    GITHUB_CONTEXT_DEFAULT_MAX_AGE_SECONDS,
    SOURCE_CONTRACT_NAMES,
    dataset_status_to_substrate_status,
    source_contract,
)
from .ingest.webhistory import (
    WEBHISTORY_FULL_HISTORY_SCHEMA_VERSION,
    build_full_history,
    full_history_manifest_path,
)
from .ingest.exports_materialize import (
    MESSENGER_CANONICAL_SCHEMA_VERSION,
    RAINDROP_BOOKMARKS_SCHEMA_VERSION,
    REDDIT_CANONICAL_SCHEMA_VERSION,
    SPOTIFY_STREAMS_SCHEMA_VERSION,
    _export_roots,
    _messenger_thread_files,
    _spotify_roots,
    materialize_messenger,
    materialize_raindrop,
    materialize_reddit,
    materialize_spotify,
    messenger_canonical_dir,
    raindrop_bookmarks_path,
    reddit_canonical_dir,
    spotify_streams_path,
)
from .ingest.activitywatch_materialize import (
    ACTIVITYWATCH_EVENTS_SCHEMA_VERSION,
    activitywatch_input_files,
    materialize_activitywatch_events,
)
from .ingest.activitywatch_event_index_materialize import (
    activitywatch_event_index_input_files,
    materialize_activitywatch_event_index,
)
from .ingest.activity_content_materialize import (
    ACTIVITY_CONTENT_SCHEMA_VERSION,
    activity_content_input_files,
    materialize_activity_content,
)
from .ingest.activitywatch_derived_materialize import (
    ACTIVITYWATCH_DERIVED_SCHEMA_VERSION,
    activitywatch_derived_input_files,
    materialize_activitywatch_derived,
)
from .ingest.terminal_materialize import ATUIN_HISTORY_SCHEMA_VERSION, atuin_input_files, materialize_atuin_history
from .ingest.title_metadata_materialize import TITLE_METADATA_SCHEMA_VERSION, materialize_title_metadata
from .ingest.machine_materialize import MACHINE_TELEMETRY_SCHEMA_VERSION, machine_input_files, materialize_machine_telemetry
from .ingest.personal_signals_materialize import (
    PERSONAL_DAILY_SIGNALS_SCHEMA_VERSION,
    SPOTIFY_DAILY_SCHEMA_VERSION,
    materialize_personal_daily_signals,
    materialize_spotify_daily,
    spotify_daily_input_files,
)
from .ingest.temporal_signals_materialize import (
    TEMPORAL_SIGNALS_SCHEMA_VERSION,
    materialize_temporal_signals,
)
from .ingest.sleep_productivity_materialize import (
    SLEEP_PRODUCTIVITY_SCHEMA_VERSION,
    materialize_sleep_productivity,
)
from .ingest.google_takeout_materialize import (
    GOOGLE_TAKEOUT_INVENTORY_SCHEMA_VERSION,
    google_takeout_inventory_dir,
    materialize_google_takeout_inventory,
)
from .ingest.google_takeout_products import (
    GOOGLE_TAKEOUT_PRODUCTS_SCHEMA_VERSION,
    google_takeout_products_dir,
    materialize_google_takeout_products,
)
from .ingest.gmail_takeout_materialize import GMAIL_EVENTS_SCHEMA_VERSION, materialize_gmail_events
from .ingest.github_context_materialize import materialize_github_context
from .analysis.keylog import write_keylog_analysis
from .ingest.bookmarks_materialize import (
    BOOKMARK_EVENTS_SCHEMA_VERSION,
    _bookmark_roots,
    _discover_bookmark_files,
    materialize_bookmarks,
)
from .ingest.communications_materialize import (
    COMMUNICATION_EVENTS_SCHEMA_VERSION,
    communication_input_files,
    materialize_communication_events,
)
from .ingest.arbtt_materialize import ARBTT_EVENTS_SCHEMA_VERSION, _capture_logs, materialize_arbtt_events
from .ingest.irc_materialize import IRC_EVENTS_SCHEMA_VERSION, irc_input_files, materialize_irc_events
from .sources.activitywatch_raw import canonical_activitywatch_events_path
from .sources.activitywatch_event_index import (
    ACTIVITYWATCH_EVENT_INDEX_SCHEMA_VERSION,
    activitywatch_event_index_manifest_path,
    activitywatch_event_index_path,
)
from .sources.activity_content import activity_content_daily_path, activity_content_manifest_path, activity_title_usage_path
from .sources.activitywatch_derived import (
    PRODUCT_KINDS as ACTIVITYWATCH_DERIVED_PRODUCT_KINDS,
    activitywatch_derived_manifest_path,
    activitywatch_derived_path,
)
from .sources.machine import canonical_machine_table_path
from .sources.terminal import canonical_atuin_history_path
from .sources.title_metadata import title_metadata_manifest_path, title_metadata_path
from .sources.google_takeout import discover_takeout_archives
from .sources.gmail_takeout import gmail_events_path, gmail_manifest_path
from .sources.polylogue import archive_readiness, iter_session_profiles
from .sources.exports_raindrop import list_raindrop_exports
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
from .sources.temporal_signals import temporal_signals_manifest_path, temporal_signals_path
from .sources.sleep_productivity import sleep_productivity_manifest_path, sleep_productivity_path
from .sources.github_context import GITHUB_CONTEXT_SCHEMA_VERSION, github_context_manifest_path, github_context_path


Status = DatasetStatus
MaterializationStatus = Literal["ready", "updated", "blocked", "failed", "coverage_bound", "manual"]
MaterializationBudget = Literal["inline", "background", "manual"]


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
    materialization_hint: str
    reason: str
    covered_dates: tuple[date, ...] = ()

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
            "covered_dates": [day.isoformat() for day in self.covered_dates],
            "coverage": coverage,
            "materialization_hint": self.materialization_hint,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class MaterializationPlanStep:
    name: str
    before: MaterializedDataset
    action: str
    materialization_hint: str
    reason: str

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.before.status,
            "action": self.action,
            "materialization_hint": self.materialization_hint,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class MaterializationResult:
    """Result of ensuring one product/source is ready for a read path.

    This is the materialization-first replacement boundary for read-side
    readiness decisions. It deliberately reports product status without writing
    diagnostic ledger history.
    """

    name: str
    status: MaterializationStatus
    changed: bool
    reason: str
    elapsed_ms: int
    product_paths: tuple[Path, ...]
    source_high_water: dict[str, str | int | float | None]
    coverage: dict[str, Any]
    diagnostics: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "changed": self.changed,
            "reason": self.reason,
            "elapsed_ms": self.elapsed_ms,
            "product_paths": [str(path) for path in self.product_paths],
            "source_high_water": dict(self.source_high_water),
            "coverage": self.coverage,
            "diagnostics": list(self.diagnostics),
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
        "asciinema": _asciinema_dataset,
        "webhistory": _webhistory_dataset,
        "google_takeout": _google_takeout_dataset,
        "polylogue": _polylogue_dataset,
        "codex": _codex_dataset,
        "polylogue_devtools": _polylogue_devtools_dataset,
        "activitywatch": _activitywatch_dataset,
        "activitywatch_event_index": _activitywatch_event_index_dataset,
        "activitywatch_derived": _activitywatch_derived_dataset,
        "clipboard": _clipboard_dataset,
        "title_metadata": _title_metadata_dataset,
        "activity_content": _activity_content_dataset,
        "atuin": _atuin_dataset,
        "dendron": _dendron_dataset,
        "evidence_graph_substrate": _git_substrate_dataset,
        "github_context": _github_context_dataset,
        "analysis_artifacts": _analysis_artifacts_dataset,
        "health": _health_dataset,
        "goodreads": _goodreads_dataset,
        "keylog": _keylog_dataset,
        "keylog_analysis": _keylog_analysis_dataset,
        "raw_log": _raw_log_dataset,
        "sleep": _sleep_dataset,
        "substance": _substance_dataset,
        "spotify": _spotify_dataset,
        "reddit": _reddit_dataset,
        "samsung_gdpr_cloud": _samsung_gdpr_cloud_dataset,
        "sinnix_runtime_inventory": _sinnix_runtime_inventory_dataset,
        "facebook_messenger": _messenger_dataset,
        "communications": _communications_dataset,
        "raindrop": _raindrop_dataset,
        "browser_bookmarks": _bookmarks_dataset,
        "arbtt": _arbtt_dataset,
        "machine": _machine_dataset,
        "xtask_history": _xtask_history_dataset,
        "spotify_daily": _spotify_daily_dataset,
        "personal_daily_signals": _personal_daily_signals_dataset,
        "temporal_signals": _temporal_signals_dataset,
        "sleep_productivity": _sleep_productivity_dataset,
        "irc": _irc_dataset,
        "wykop": _wykop_dataset,
    }


def _materializers() -> dict[str, Callable[..., Any]]:
    return {
        "webhistory": _materialize_webhistory,
        "google_takeout": _materialize_google_takeout,
        "activitywatch": materialize_activitywatch_events,
        "activitywatch_event_index": materialize_activitywatch_event_index,
        "activitywatch_derived": materialize_activitywatch_derived,
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
        "github_context": materialize_github_context,
        "keylog_analysis": _materialize_keylog_analysis,
        "spotify_daily": materialize_spotify_daily,
        "personal_daily_signals": materialize_personal_daily_signals,
        "temporal_signals": materialize_temporal_signals,
        "sleep_productivity": materialize_sleep_productivity,
        "irc": materialize_irc_events,
    }


def _materialize_webhistory(*, start: date | None = None, end: date | None = None) -> None:
    cfg = get_config()
    if cfg.webhistory_ndjson is None:
        raise MaterializationError(
            "webhistory",
            reason="canonical webhistory output path is not configured",
        )
    build_full_history(data_dir=cfg.webhistory_dir, output=cfg.webhistory_ndjson, start=start, end=end)


def _materialize_google_takeout() -> None:
    materialize_google_takeout_inventory()
    materialize_google_takeout_products()
    materialize_gmail_events()


def _materialize_keylog_analysis(
    *,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    from .analysis.keylog import DEFAULT_HYPRLAND_BINDINGS
    from .core.io import resolve_analysis_path

    if start is None or end is None:
        end = date.today()
        start = end - timedelta(days=13)
    inclusive_end = end - timedelta(days=1)
    if inclusive_end < start:
        inclusive_end = start
    analysis = write_keylog_analysis(
        Path(resolve_analysis_path("keylog_analysis.json")),
        start=start,
        end=inclusive_end,
        bindings_path=DEFAULT_HYPRLAND_BINDINGS,
    )
    return {"row_count": analysis.source_event_count}


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
                    materialization_hint=contract.materialization_hint,
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


def run_materializer_by_name(name: str) -> dict[str, Any]:
    """Run one registered local materializer by source/product name."""

    materializers = _materializers()
    if name not in materializers:
        raise MaterializationError(
            name,
            reason="no local materializer is defined for this contract",
        )
    report = _run_materializer(materializers[name], window=None)
    return report if isinstance(report, dict) else {"result": report}


def _run_materializer(
    materializer: Callable[..., Any],
    *,
    window: tuple[date, date] | None,
) -> Any:
    if window is None:
        return materializer()
    signature = inspect.signature(materializer)
    if "start" not in signature.parameters or "end" not in signature.parameters:
        return materializer()
    return materializer(start=window[0], end=window[1])


def ensure_materialized(
    name: str,
    *,
    window: tuple[date, date] | None = None,
    budget: MaterializationBudget = "inline",
    force: bool = False,
    cfg: LynchpinConfig | None = None,
) -> MaterializationResult:
    """Ensure one source/product is materialized enough for a read path.

    This function is intentionally not a queueing API. It either proves the
    current product is usable, runs a local materializer directly when the
    source contract says that is valid, or reports why Lynchpin cannot advance
    the product locally.
    """

    started = datetime.now(timezone.utc)
    cfg = cfg or get_config()
    contract = source_contract(name)
    materializers = _materializers()
    before = _audit_one(name, cfg=cfg)

    if not force and before.status == "ready" and _materialized_enough_for_window(before, window):
        return _materialization_result(
            before,
            status="ready",
            changed=False,
            reason=before.reason,
            started=started,
            window=window,
        )

    if contract.materialization_mode == "coverage_bound":
        return _materialization_result(
            before,
            status="coverage_bound",
            changed=False,
            reason="source coverage is bounded by external exports; Lynchpin cannot extend it locally",
            started=started,
            window=window,
        )

    if contract.materialization_mode == "manual":
        return _materialization_result(
            before,
            status="manual",
            changed=False,
            reason=contract.materialization_hint,
            started=started,
            window=window,
        )

    if contract.materialization_mode == "live" and name not in materializers:
        status: MaterializationStatus = "ready" if before.status == "ready" else "blocked"
        return _materialization_result(
            before,
            status=status,
            changed=False,
            reason=before.reason,
            started=started,
            window=window,
        )

    if name not in materializers:
        return _materialization_result(
            before,
            status="blocked",
            changed=False,
            reason="no local materializer is defined for this contract",
            started=started,
            window=window,
        )

    if budget == "manual":
        return _materialization_result(
            before,
            status="blocked",
            changed=False,
            reason="materialization requires local work but budget is manual",
            started=started,
            window=window,
        )

    try:
        _run_materializer(materializers[name], window=window)
    except Exception as exc:
        if _can_read_stale_github_context(before, window):
            return _materialization_result(
                before,
                status="blocked",
                changed=False,
                reason=f"GitHub network refresh failed; existing canonical context product is stale: {exc}",
                started=started,
                window=window,
                diagnostics=(type(exc).__name__, "stale_github_context"),
            )
        return _materialization_result(
            before,
            status="failed",
            changed=False,
            reason=str(exc),
            started=started,
            window=window,
            diagnostics=(type(exc).__name__,),
        )

    after = _audit_one(name, cfg=cfg)
    enough_for_window = _materialized_enough_for_window(after, window)
    status = "updated" if after.status == "ready" and enough_for_window else "failed"
    if after.status != "ready":
        reason = f"materializer ran but product is still {after.status}: {after.reason}"
    elif not enough_for_window:
        reason = "materializer ran but continuous product still does not cover the requested window"
    else:
        reason = after.reason
    return _materialization_result(
        after,
        status=status,
        changed=status == "updated",
        reason=reason,
        started=started,
        window=window,
    )


def substrate_materialization_snapshot(
    path: Path,
    *,
    latest_materialized_refresh_id: str | None = None,
    latest_recorded_at: Any | None = None,
) -> MaterializationResult:
    """Cheap materialization status for the derived DuckDB substrate."""

    status: MaterializationStatus = "ready" if path.exists() and latest_materialized_refresh_id else "blocked"
    reason = (
        "substrate has a recorded promotion snapshot"
        if status == "ready"
        else "substrate has no recorded promotion snapshot"
    )
    return MaterializationResult(
        name="evidence_graph_substrate",
        status=status,
        changed=False,
        reason=reason,
        elapsed_ms=0,
        product_paths=(path,),
        source_high_water={
            "latest_materialized_refresh_id": latest_materialized_refresh_id,
            "latest_recorded_at": str(latest_recorded_at) if latest_recorded_at is not None else None,
        },
        coverage={
            "relation": "dated" if status == "ready" else "unavailable",
            "interpretation": reason,
        },
    )


def _audit_one(name: str, *, cfg: LynchpinConfig) -> MaterializedDataset:
    builders = _dataset_builders()
    canonical = source_contract(name).name
    if canonical not in builders:
        raise MaterializationError(
            canonical,
            reason="no materialization audit builder is defined for this contract",
        )
    return builders[canonical](cfg)


def _materialization_result(
    row: MaterializedDataset,
    *,
    status: MaterializationStatus,
    changed: bool,
    reason: str,
    started: datetime,
    window: tuple[date, date] | None,
    diagnostics: tuple[str, ...] = (),
) -> MaterializationResult:
    return MaterializationResult(
        name=row.name,
        status=status,
        changed=changed,
        reason=reason,
        elapsed_ms=_elapsed_ms(started),
        product_paths=row.materialized_paths,
        source_high_water=_source_high_water(row),
        coverage=materialized_dataset_coverage(
            row,
            start=window[0] if window else None,
            end=window[1] if window else None,
        ),
        diagnostics=diagnostics,
    )


def _materialized_enough_for_window(
    row: MaterializedDataset,
    window: tuple[date, date] | None,
) -> bool:
    if window is None:
        return True
    contract = source_contract(row.name)
    if contract.collection_model == "derived":
        if not row.covered_dates and (row.first_date is None or row.last_date is None):
            return True
    elif contract.collection_model != "continuous":
        return True
    coverage = materialized_dataset_coverage(row, start=window[0], end=window[1])
    return coverage["fully_covers_requested_window"] is True


def _can_read_stale_github_context(row: MaterializedDataset, window: tuple[date, date] | None) -> bool:
    if row.name != "github_context":
        return False
    manifest = _first_manifest(row.materialized_paths)
    if manifest.get("schema_version") != GITHUB_CONTEXT_SCHEMA_VERSION:
        return False
    product_paths = [path for path in row.materialized_paths if path.suffix != ".json"]
    if not product_paths or not all(path.exists() for path in product_paths):
        return False
    if window is None:
        return True
    start, end = window
    if row.covered_dates:
        requested = _requested_dates(start, end)
        covered = set(row.covered_dates)
        return all(day in covered for day in requested)
    if row.first_date is None or row.last_date is None:
        return True
    return _covered_day_count(row.first_date, row.last_date, start=start, end=end) >= (_requested_day_count(start, end) or 0)


def _source_high_water(row: MaterializedDataset) -> dict[str, str | int | float | None]:
    latest_raw = max((_path_mtime_float(path) for path in row.raw_roots if path.exists()), default=None)
    latest_product = max((_path_mtime_float(path) for path in row.materialized_paths if path.exists()), default=None)
    high_water: dict[str, str | int | float | None] = {
        "row_count": row.row_count,
        "first_date": row.first_date.isoformat() if row.first_date else None,
        "last_date": row.last_date.isoformat() if row.last_date else None,
        "covered_date_count": len(row.covered_dates) if row.covered_dates else None,
        "latest_raw_mtime": latest_raw,
        "latest_product_mtime": latest_product,
    }
    manifest = _first_manifest(row.materialized_paths)
    if manifest:
        high_water["input_file_count"] = _int_or_none(manifest.get("input_file_count"))
        input_latest = manifest.get("input_latest_mtime")
        high_water["input_latest_mtime"] = str(input_latest) if input_latest else None
    return high_water


def _first_manifest(paths: Iterable[Path]) -> dict[str, Any]:
    for path in paths:
        if path.suffix == ".json" and path.exists():
            payload = _load_json(path)
            if payload:
                return payload
    return {}


def _spotify_input_files(cfg: LynchpinConfig) -> tuple[Path, ...]:
    root = cfg.exports_root / "spotify/processed"
    return tuple(
        path
        for export_root in _spotify_roots(root)
        for path in sorted(export_root.rglob("Streaming*.json"))
        if path.is_file()
    )


def _reddit_input_files(cfg: LynchpinConfig) -> tuple[Path, ...]:
    root = cfg.exports_root / "reddit/processed"
    return tuple(
        path
        for export_root in _export_roots(root)
        for path in sorted(export_root.rglob("*.csv"))
        if path.is_file()
    )


def _raindrop_input_files() -> tuple[Path, ...]:
    return tuple(export.path for export in list_raindrop_exports())


def _manifest_inputs_current(manifest: dict[str, Any], paths: Iterable[Path]) -> bool:
    expected_count = _int_or_none(manifest.get("input_file_count"))
    expected_latest = manifest.get("input_latest_mtime")
    if expected_count is None and not expected_latest:
        return True
    current_count, current_latest = _path_high_water(paths)
    if expected_count is not None and expected_count != current_count:
        return False
    if expected_latest and str(expected_latest) != current_latest:
        return False
    return True


def _source_db_mtime_current(manifest: dict[str, Any], path: Path) -> bool:
    expected = manifest.get("source_db_mtime")
    if not expected:
        return True
    if not path.exists():
        return False
    current = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).astimezone().isoformat()
    return str(expected) == current


def _manifest_declared_input_files(manifest: dict[str, Any]) -> tuple[Path, ...]:
    paths = manifest.get("input_files")
    if not isinstance(paths, list):
        return ()
    return tuple(Path(str(path)) for path in paths)


def _manifest_covered_dates(manifest: dict[str, Any]) -> tuple[date, ...]:
    raw_dates = manifest.get("covered_dates")
    if not isinstance(raw_dates, list):
        return ()
    dates: list[date] = []
    for raw in raw_dates:
        try:
            dates.append(date.fromisoformat(str(raw)))
        except ValueError:
            continue
    return tuple(sorted(set(dates)))


def _path_high_water(paths: Iterable[Path]) -> tuple[int, str | None]:
    existing = tuple(path for path in paths if path.exists())
    latest = max((path.stat().st_mtime for path in existing), default=None)
    if latest is None:
        return len(existing), None
    return len(existing), datetime.fromtimestamp(latest, timezone.utc).astimezone().isoformat()


def _path_mtime_float(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _elapsed_ms(started: datetime) -> int:
    return int((datetime.now(timezone.utc) - started).total_seconds() * 1000)


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
    precise_dates = tuple(
        day
        for day in row.covered_dates
        if row.first_date is None or (row.first_date <= day <= (row.last_date or day))
    )
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
        covered_days = len(precise_dates) if precise_dates else None
        overlaps = None
        fully_covers = None
    else:
        if precise_dates:
            requested = _requested_dates(start, end)
            covered_set = set(precise_dates)
            covered_days = sum(1 for day in requested if day in covered_set)
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
        "precise_covered_dates": bool(precise_dates),
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
    if start is None or end is None:
        return None
    if end <= start:
        return 0
    return (end - start).days


def _requested_dates(start: date, end: date) -> tuple[date, ...]:
    if end <= start:
        return ()
    return tuple(start + timedelta(days=offset) for offset in range((end - start).days))


def _covered_day_count(first: date, last: date, *, start: date, end: date) -> int:
    # Dataset bounds are inclusive by day; requested windows are [start, end).
    requested_last = end - timedelta(days=1)
    left = max(first, start)
    right = min(last, requested_last)
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
            contract.materialization_hint,
            "canonical full_history.ndjson is not configured",
            raw_roots=(cfg.webhistory_raw_dir, cfg.webhistory_dir),
        )
    if not output.exists():
        return _missing_dataset(
            "webhistory",
            contract.authority,
            contract.query_surface,
            contract.materialization_hint,
            f"canonical NDJSON is missing: {output}",
            materialized_paths=(output,),
            raw_roots=(cfg.webhistory_raw_dir, cfg.webhistory_dir),
        )

    manifest_valid = bool(manifest and _manifest_valid(manifest))
    meta = _load_json(manifest) if manifest_valid else {}
    row_count = _int_or_none(meta.get("row_count"))
    first = _date_from_iso(meta.get("first_date"))
    last = _date_from_iso(meta.get("last_date"))
    covered_dates = _manifest_covered_dates(meta)
    cheap_bounds_complete = row_count is not None and first is not None and last is not None
    input_files = _manifest_declared_input_files(meta)
    inputs_current = _manifest_inputs_current(meta, input_files)
    schema_current = meta.get("schema_version") == WEBHISTORY_FULL_HISTORY_SCHEMA_VERSION
    if manifest_valid and not schema_current:
        status: Status = "partial"
        reason = "canonical merged webhistory schema is older than the current reader contract"
    elif manifest_valid and not cheap_bounds_complete:
        status = "partial"
        reason = "canonical merged webhistory manifest is missing cheap row or coverage bounds"
    elif manifest_valid and not inputs_current:
        status: Status = "partial"
        reason = "canonical merged NDJSON was built from older webhistory segment files"
    elif manifest_valid:
        status = "ready"
        reason = "canonical merged NDJSON and manifest are present"
    else:
        status = "degraded"
        reason = "canonical merged NDJSON exists but manifest is missing or malformed"
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
        covered_dates=covered_dates,
        materialization_hint=contract.materialization_hint,
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
    gmail_path = gmail_events_path()
    gmail_manifest = gmail_manifest_path()
    gmail_meta = _load_json(gmail_manifest)
    gmail_manifest_valid = _manifest_valid(gmail_manifest)
    inventory_schema_current = meta.get("schema_version") == GOOGLE_TAKEOUT_INVENTORY_SCHEMA_VERSION
    products_schema_current = product_meta.get("schema_version") == GOOGLE_TAKEOUT_PRODUCTS_SCHEMA_VERSION
    gmail_schema_current = gmail_meta.get("schema_version") == GMAIL_EVENTS_SCHEMA_VERSION
    inventory_inputs_current = _manifest_inputs_current(meta, _manifest_declared_input_files(meta))
    products_inputs_current = _manifest_inputs_current(product_meta, _manifest_declared_input_files(product_meta))
    gmail_inputs_current = _manifest_inputs_current(gmail_meta, _manifest_declared_input_files(gmail_meta))
    raw_product_counts = product_meta.get("products")
    product_counts = raw_product_counts if isinstance(raw_product_counts, dict) else {}
    typed_rows = sum(
        _int_or_none(row.get("row_count")) or 0
        for row in product_counts.values()
        if isinstance(row, dict)
    )
    first = _date_from_iso(product_meta.get("first_date"))
    last = _date_from_iso(product_meta.get("last_date"))
    gmail_first = _date_from_iso(gmail_meta.get("first_date"))
    gmail_last = _date_from_iso(gmail_meta.get("last_date"))
    first = _min_date(first, gmail_first)
    last = _max_date(last, gmail_last)
    if first is None and last is None:
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
    elif products_manifest_valid and not inventory_schema_current:
        status = "partial"
        reason = "Google Takeout inventory manifest schema is older than the current reader contract"
    elif products_manifest_valid and not products_schema_current:
        status = "partial"
        reason = "Google Takeout typed product manifest schema is older than the current reader contract"
    elif products_manifest_valid and not gmail_manifest_valid:
        status = "partial"
        reason = "Google Takeout Gmail event product is missing"
    elif products_manifest_valid and not gmail_schema_current:
        status = "partial"
        reason = "Google Takeout Gmail event manifest schema is older than the current reader contract"
    elif products_manifest_valid and not (inventory_inputs_current and products_inputs_current and gmail_inputs_current):
        status = "partial"
        reason = f"{len(archives)} raw Takeout archives changed since typed product materialization"
    elif products_manifest_valid:
        status = "ready"
        reason = (
            f"{len(archives)} raw Takeout archives inventoried; Chrome history plus "
            f"{typed_rows} non-Chrome typed product rows and "
            f"{_manifest_row_count(gmail_meta, gmail_path) or 0} Gmail rows are materialized"
        )
    else:
        status = "partial"
        reason = f"{len(archives)} raw Takeout archives inventoried but typed non-Chrome product rows are missing"
    return MaterializedDataset(
        name="google_takeout",
        status=status,
        authority="raw Google Takeout archives",
        query_surface="lynchpin.sources.google_takeout plus lynchpin.sources.google_takeout_products",
        materialized_paths=(archive_rows, members, manifest, products_manifest, gmail_path, gmail_manifest),
        raw_roots=(raw_root,),
        row_count=(_int_or_none(meta.get("member_count")) or len(archives)) + typed_rows + (_manifest_row_count(gmail_meta, gmail_path) or 0),
        first_date=first,
        last_date=last,
        materialization_hint="python -m lynchpin.ingest.google_takeout_materialize && python -m lynchpin.ingest.google_takeout_products",
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
        materialization_hint="polylogue doctor --repair --target session_insights",
        reason=readiness.reason,
    )


def _activitywatch_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    path = canonical_activitywatch_events_path()
    manifest = path.with_suffix(".manifest.json")
    meta = _load_json(manifest)
    input_files = activitywatch_input_files(cfg)
    archives = _count_files(cfg.activitywatch_archive_db_dir, suffixes=(".sqlite", ".db"))
    product_ready = _product_with_manifest_exists(path, manifest)
    inputs_current = _manifest_inputs_current(meta, input_files)
    schema_current = meta.get("schema_version") == ACTIVITYWATCH_EVENTS_SCHEMA_VERSION
    if product_ready and not schema_current:
        status = "partial"
        reason = "canonical ActivityWatch event schema is older than the current reader contract"
    elif product_ready and not inputs_current:
        status = "partial"
        reason = "canonical ActivityWatch event NDJSON was built from older local input databases"
    elif product_ready:
        status = "ready"
        reason = "canonical ActivityWatch event NDJSON is present"
    elif input_files:
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
        covered_dates=_manifest_covered_dates(meta),
        materialization_hint="python -m lynchpin.ingest.activitywatch_materialize",
        reason=reason,
    )


def _activitywatch_event_index_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    contract = source_contract("activitywatch_event_index")
    manifest = activitywatch_event_index_manifest_path()
    meta = _load_json(manifest)
    input_files = activitywatch_event_index_input_files()
    covered_dates = _manifest_covered_dates(meta)
    product_paths = tuple(activitywatch_event_index_path(day) for day in covered_dates)
    products_ready = (
        _manifest_valid(manifest)
        and bool(covered_dates)
        and all(path.exists() for path in product_paths)
    )
    inputs_current = _manifest_inputs_current(meta, input_files)
    schema_current = meta.get("schema_version") == ACTIVITYWATCH_EVENT_INDEX_SCHEMA_VERSION
    if products_ready and not schema_current:
        status: Status = "partial"
        reason = "ActivityWatch event index schema is older than the current reader contract"
    elif products_ready and not inputs_current:
        status = "partial"
        reason = "ActivityWatch event index was built from an older canonical event product"
    elif products_ready:
        status = "ready"
        reason = "ActivityWatch logical-day event index is present"
    elif input_files:
        status = "partial"
        reason = "ActivityWatch logical-day event index is missing"
    else:
        status = "missing"
        reason = "canonical ActivityWatch event product is missing"
    return MaterializedDataset(
        name="activitywatch_event_index",
        status=status,
        authority=contract.authority,
        query_surface=contract.query_surface,
        materialized_paths=(*product_paths, manifest),
        raw_roots=(cfg.captures_root / "activitywatch",),
        row_count=_int_or_none(meta.get("row_count")),
        first_date=_date_from_iso(meta.get("first_date")),
        last_date=_date_from_iso(meta.get("last_date")),
        materialization_hint=contract.materialization_hint,
        reason=reason,
        covered_dates=covered_dates,
    )


def _activitywatch_derived_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    contract = source_contract("activitywatch_derived")
    manifest = activitywatch_derived_manifest_path()
    meta = _load_json(manifest)
    paths = tuple(activitywatch_derived_path(kind) for kind in ACTIVITYWATCH_DERIVED_PRODUCT_KINDS)
    input_files = activitywatch_derived_input_files()
    products_ready = manifest.exists() and all(path.exists() for path in paths)
    inputs_current = _manifest_inputs_current(meta, input_files)
    schema_current = meta.get("schema_version") == ACTIVITYWATCH_DERIVED_SCHEMA_VERSION
    if products_ready and not schema_current:
        status: Status = "partial"
        reason = "ActivityWatch derived graph product schema is older than the current reader contract"
    elif products_ready and not inputs_current:
        status: Status = "partial"
        reason = "ActivityWatch derived graph products were built from an older canonical event product"
    elif products_ready:
        status = "ready"
        reason = "ActivityWatch derived graph products are present"
    elif input_files:
        status = "partial"
        reason = "ActivityWatch derived graph products are missing"
    else:
        status = "missing"
        reason = "canonical ActivityWatch event product is missing"
    return MaterializedDataset(
        name="activitywatch_derived",
        status=status,
        authority=contract.authority,
        query_surface=contract.query_surface,
        materialized_paths=(*paths, manifest),
        raw_roots=(cfg.captures_root / "activitywatch",),
        row_count=_int_or_none(meta.get("row_count")),
        first_date=_date_from_iso(meta.get("first_date")),
        last_date=_date_from_iso(meta.get("last_date")),
        materialization_hint=contract.materialization_hint,
        reason=reason,
        covered_dates=_manifest_covered_dates(meta),
    )


def _title_metadata_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    contract = source_contract("title_metadata")
    path = title_metadata_path()
    manifest = title_metadata_manifest_path()
    meta = _load_json(manifest)
    source_db = Path(str(meta.get("source_db"))) if meta.get("source_db") else cfg.local_root / "enrich/semantic_classifications.duckdb"
    ready = _product_with_manifest_exists(path, manifest)
    row_count = _manifest_row_count(meta, path)
    source_files = (source_db,) if source_db.exists() else ()
    raw_roots = tuple(root for root in (source_db, cfg.local_root / "enrich") if root.exists())
    inputs_current = _manifest_inputs_current(meta, source_files) and _source_db_mtime_current(meta, source_db)
    schema_current = meta.get("schema_version") == TITLE_METADATA_SCHEMA_VERSION
    if ready and not schema_current:
        status: Status = "partial"
        reason = "canonical title metadata product schema is older than the current reader contract"
    elif ready and not inputs_current:
        status: Status = "partial"
        reason = "canonical title metadata NDJSON was built from an older source database"
    elif ready:
        status = "ready"
        reason = "canonical title metadata NDJSON is present"
    else:
        status = "partial" if raw_roots else "missing"
        reason = "canonical title metadata product is missing"
    return MaterializedDataset(
        name="title_metadata",
        status=status,
        authority=contract.authority,
        query_surface=contract.query_surface,
        materialized_paths=(path, manifest),
        raw_roots=raw_roots or (cfg.local_root / "enrich",),
        row_count=row_count,
        first_date=None,
        last_date=None,
        materialization_hint=contract.materialization_hint,
        reason=reason,
    )


def _activity_content_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    contract = source_contract("activity_content")
    path = activity_content_daily_path()
    usage = activity_title_usage_path()
    manifest = activity_content_manifest_path()
    meta = _load_json(manifest)
    input_files = activity_content_input_files()
    ready = _product_with_manifest_exists(path, manifest) and usage.exists()
    inputs_current = _manifest_inputs_current(meta, input_files)
    schema_current = meta.get("schema_version") == ACTIVITY_CONTENT_SCHEMA_VERSION
    has_inputs = len(input_files) == 2
    if ready and not schema_current:
        status: Status = "partial"
        reason = "canonical ActivityWatch content daily product schema is older than the current reader contract"
    elif ready and not inputs_current:
        status: Status = "partial"
        reason = "canonical ActivityWatch content daily product was built from older upstream products"
    elif ready:
        status = "ready"
        reason = "canonical ActivityWatch content daily product is present"
    else:
        status = "partial" if has_inputs else "missing"
        reason = "canonical ActivityWatch content daily product is missing"
    return MaterializedDataset(
        name="activity_content",
        status=status,
        authority=contract.authority,
        query_surface=contract.query_surface,
        materialized_paths=(path, usage, manifest),
        raw_roots=(cfg.derived_root / "title_metadata", cfg.captures_root / "activitywatch"),
        row_count=_manifest_row_count(meta, path),
        first_date=_date_from_iso(meta.get("first_date")),
        last_date=_date_from_iso(meta.get("last_date")),
        materialization_hint=contract.materialization_hint,
        reason=reason,
    )


def _atuin_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    path = canonical_atuin_history_path()
    manifest = path.with_suffix(".manifest.json")
    meta = _load_json(manifest)
    input_files = atuin_input_files(cfg)
    ready = _product_with_manifest_exists(path, manifest)
    inputs_current = _manifest_inputs_current(meta, input_files)
    schema_current = meta.get("schema_version") == ATUIN_HISTORY_SCHEMA_VERSION
    if ready and not schema_current:
        status: Status = "partial"
        reason = "canonical Atuin command history schema is older than the current reader contract"
    elif ready and not inputs_current:
        status: Status = "partial"
        reason = "canonical Atuin command history was built from an older local database"
    elif ready:
        status = "ready"
        reason = "canonical Atuin command history NDJSON is present"
    else:
        status = "partial" if input_files else "missing"
        reason = "canonical Atuin command history product or manifest is missing/malformed"
    return MaterializedDataset(
        name="atuin",
        status=status,
        authority="Atuin live SQLite",
        query_surface="lynchpin.sources.terminal",
        materialized_paths=(path, manifest),
        raw_roots=(cfg.atuin_db,),
        row_count=_manifest_row_count(meta, path),
        first_date=_date_from_iso(meta.get("first_date")),
        last_date=_date_from_iso(meta.get("last_date")),
        covered_dates=_manifest_covered_dates(meta),
        materialization_hint="python -m lynchpin.ingest.terminal_materialize",
        reason=reason,
    )


def _raw_source_dataset(
    cfg: LynchpinConfig,
    *,
    name: str,
    raw_roots: tuple[Path, ...],
    authority: str,
    query_surface: str,
    materialization_hint: str,
    row_count: int | None = None,
    materialized_paths: tuple[Path, ...] = (),
) -> MaterializedDataset:
    existing = tuple(path for path in raw_roots if path.exists())
    status: Status = "ready" if existing else "missing"
    observed = max((_path_mtime_date(path) for path in existing), default=None)
    return MaterializedDataset(
        name=name,
        status=status,
        authority=authority,
        query_surface=query_surface,
        materialized_paths=materialized_paths,
        raw_roots=raw_roots,
        row_count=row_count,
        first_date=None,
        last_date=observed,
        materialization_hint=materialization_hint,
        reason=(
            "raw source authority is present"
            if existing
            else "configured raw source authority is missing"
        ),
    )


def _asciinema_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    return _raw_source_dataset(
        cfg,
        name="asciinema",
        raw_roots=(cfg.asciinema_root,),
        authority="asciinema terminal recording captures",
        query_surface="lynchpin.sources.terminal.recordings",
        materialization_hint="asciinema recording capture writes under /realm/data/captures/asciinema",
        row_count=_count_files(cfg.asciinema_root, suffixes=(".cast",)),
    )


def _codex_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    return _raw_source_dataset(
        cfg,
        name="codex",
        raw_roots=(cfg.codex_sessions_root,),
        authority="Codex session JSONL archive",
        query_surface="lynchpin.sources.polylogue once Polylogue has archived Codex sessions",
        materialization_hint="polylogued tails Codex session logs into the Polylogue archive",
        row_count=_count_files(cfg.codex_sessions_root, suffixes=(".jsonl",)),
    )


def _clipboard_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    raw_roots = (cfg.clipboard_live_file, *cfg.clipboard_export_files)
    return _raw_source_dataset(
        cfg,
        name="clipboard",
        raw_roots=raw_roots,
        authority="Clipse live clipboard history plus exported clipboard snapshots",
        query_surface="lynchpin.sources.clipboard",
        materialization_hint="clipse records live; export snapshots are read from configured raw files",
        row_count=sum(1 for path in raw_roots if path.exists()),
    )


def _dendron_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    return _raw_source_dataset(
        cfg,
        name="dendron",
        raw_roots=(cfg.dendron_root,),
        authority="knowledgebase/Dendron markdown notes",
        query_surface="lynchpin.sources.exports_dendron",
        materialization_hint="edit knowledgebase notes; Lynchpin reads the note tree directly",
        row_count=_count_files(cfg.dendron_root, suffixes=(".md",)),
    )


def _goodreads_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    return _raw_source_dataset(
        cfg,
        name="goodreads",
        raw_roots=(cfg.goodreads_library,),
        authority="Goodreads library export CSV",
        query_surface="lynchpin.sources.exports_goodreads",
        materialization_hint="replace /realm/data/exports/goodreads/raw/library_export.csv",
        row_count=_csv_count(cfg.goodreads_library),
    )


def _keylog_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    logs_root = cfg.keylog_root / "logs"
    row = _raw_source_dataset(
        cfg,
        name="keylog",
        raw_roots=(logs_root,),
        authority="scribe-tap keylog captures",
        query_surface="lynchpin.sources.keylog",
        materialization_hint="scribe-tap records live keylog captures",
        row_count=_count_files(logs_root),
    )
    log_dates = sorted(
        date.fromisoformat(path.stem)
        for path in logs_root.glob("*.jsonl")
        if _date_from_iso(path.stem) is not None
    ) if logs_root.exists() else []
    if not log_dates:
        return row
    return replace(
        row,
        first_date=log_dates[0],
        last_date=log_dates[-1],
        reason="scribe-tap keylog log files are present",
    )


def _keylog_analysis_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    from .core.io import resolve_analysis_path

    contract = source_contract("keylog_analysis")
    path = Path(resolve_analysis_path("keylog_analysis.json"))
    payload = _load_json(path)
    input_files = _manifest_declared_input_files(payload)
    ready = path.exists() and bool(payload)
    inputs_current = _manifest_inputs_current(payload, input_files)
    first = _date_from_iso(payload.get("start"))
    last = _date_from_iso(payload.get("end"))
    if ready and not inputs_current:
        status: Status = "partial"
        reason = "keylog analysis artifact was built from older keylog or binding inputs"
    elif ready:
        status = "ready"
        reason = "keylog analysis artifact is present"
    elif cfg.keylog_root.exists():
        status = "partial"
        reason = "keylog analysis artifact is missing"
    else:
        status = "missing"
        reason = "scribe-tap keylog root is missing"
    covered_dates = tuple(iter_dates(first, last)) if first is not None and last is not None else ()
    return MaterializedDataset(
        name="keylog_analysis",
        status=status,
        authority=contract.authority,
        query_surface=contract.query_surface,
        materialized_paths=(path,),
        raw_roots=(cfg.keylog_root, Path("/realm/project/sinnix/modules/features/desktop/hyprland")),
        row_count=_int_or_none(payload.get("source_event_count")),
        first_date=first,
        last_date=last,
        materialization_hint=contract.materialization_hint,
        reason=reason,
        covered_dates=covered_dates,
    )


def _raw_log_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    return _raw_source_dataset(
        cfg,
        name="raw_log",
        raw_roots=(cfg.raw_log_file,),
        authority="operator raw-log file",
        query_surface="lynchpin.sources.raw_log",
        materialization_hint="append operator raw-log entries",
        row_count=_line_count(cfg.raw_log_file) if cfg.raw_log_file.exists() else None,
    )


def _samsung_gdpr_cloud_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    return _raw_source_dataset(
        cfg,
        name="samsung_gdpr_cloud",
        raw_roots=(cfg.samsung_gdpr_cloud_dir,),
        authority="Samsung GDPR cloud export",
        query_surface="lynchpin.sources.samsung_gdpr_cloud",
        materialization_hint="replace Samsung GDPR cloud export under /realm/data/exports/samsung",
        row_count=_count_files(cfg.samsung_gdpr_cloud_dir),
    )


def _sinnix_runtime_inventory_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    return _raw_source_dataset(
        cfg,
        name="sinnix_runtime_inventory",
        raw_roots=(cfg.sinnix_runtime_inventory_json,),
        authority="Sinnix runtime inventory JSON",
        query_surface="lynchpin.sources.sinnix_runtime_inventory",
        materialization_hint="update Sinnix runtime inventory from the Sinnix capture pipeline",
        row_count=1 if cfg.sinnix_runtime_inventory_json.exists() else None,
    )


def _wykop_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    root = _wykop_operator_root(cfg.wykop_root)
    row = _raw_source_dataset(
        cfg,
        name="wykop",
        raw_roots=(root,),
        authority="Wykop GDPR export",
        query_surface="lynchpin.sources.wykop",
        materialization_hint="replace Wykop GDPR export under /realm/data/exports/wykop/raw",
        row_count=_count_files(root, suffixes=(".csv", ".json", ".jsonl")),
    )
    try:
        from lynchpin.sources.wykop import date_range

        first, last = date_range(root=root)
    except Exception:
        return row
    return replace(
        row,
        first_date=first.date(),
        last_date=last.date(),
        reason="Wykop export is present with dated operator comments",
    )


def _wykop_operator_root(root: Path) -> Path:
    account_root = root / "Sinity"
    return account_root if account_root.exists() else root


def _git_substrate_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    from .substrate.connection import substrate_path
    from .substrate.status_manifest import load_current_substrate_status_manifest, substrate_status_manifest_path

    path = Path(substrate_path())
    manifest = load_current_substrate_status_manifest(path)
    if manifest is not None:
        row = _git_substrate_dataset_from_manifest(cfg, path, manifest)
        if row is not None:
            return row

    builds, latest_build_counts, latest_status, promotion_count = _duck_substrate_status(path)
    latest_node_count = latest_build_counts[0] if latest_build_counts else None
    if builds and builds > 0 and latest_node_count and latest_node_count > 0:
        status: Status = "ready"
        reason = "DuckDB evidence graph builds are present"
    elif builds and builds > 0 and latest_node_count == 0:
        status = "empty"
        reason = "latest evidence graph build contains no nodes"
    elif latest_status and latest_status[0] == "empty":
        status = "empty"
        reason = latest_status[1] or "latest evidence graph promotion produced no nodes"
    elif latest_status and latest_status[0] == "error":
        status = "degraded"
        reason = latest_status[1] or "latest evidence graph promotion errored"
    elif promotion_count and promotion_count > 0:
        status = "ready"
        reason = "DuckDB substrate promotion runs are present"
    else:
        status = "partial"
        reason = "no materialized evidence graph build recorded"
    return MaterializedDataset(
        name="evidence_graph_substrate",
        status=status,
        authority="source modules promoted into DuckDB",
        query_surface="lynchpin.graph.context_pack",
        materialized_paths=tuple(p for p in (path, substrate_status_manifest_path(path)) if p.exists()),
        raw_roots=(cfg.baseline_dir, cfg.repo_root.parent),
        row_count=latest_node_count or builds or promotion_count,
        first_date=None,
        last_date=None,
        materialization_hint="python -m lynchpin.cli.substrate_snapshot --start 2013-01-01 --end $(date +%F)",
        reason=reason,
    )


def _git_substrate_dataset_from_manifest(
    cfg: LynchpinConfig,
    path: Path,
    manifest: dict[str, Any],
) -> MaterializedDataset | None:
    from .substrate.status_manifest import substrate_status_manifest_path

    status = manifest.get("status")
    if status not in {"ready", "empty", "missing", "partial", "degraded", "error"}:
        return None
    reason = manifest.get("reason")
    row_count = manifest.get("row_count")
    return MaterializedDataset(
        name="evidence_graph_substrate",
        status=status,
        authority="source modules promoted into DuckDB",
        query_surface="lynchpin.graph.context_pack",
        materialized_paths=(path, substrate_status_manifest_path(path)),
        raw_roots=(cfg.baseline_dir, cfg.repo_root.parent),
        row_count=int(row_count) if isinstance(row_count, int) else None,
        first_date=None,
        last_date=None,
        materialization_hint="python -m lynchpin.cli.substrate_snapshot --start 2013-01-01 --end $(date +%F)",
        reason=str(reason) if reason else "substrate status manifest is current",
    )


def _analysis_artifacts_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    from .sources.analysis_artifacts import artifact_inventory

    root = cfg.analysis_output_dir
    artifacts = artifact_inventory(root)
    count = len(artifacts)
    return MaterializedDataset(
        name="analysis_artifacts",
        status="ready" if count else "empty" if root.exists() else "missing",
        authority="generated Lynchpin analysis products under the configured analysis output directory",
        query_surface="lynchpin.sources.analysis_artifacts",
        materialized_paths=tuple(item.path for item in artifacts),
        raw_roots=(root,),
        row_count=count if root.exists() else None,
        first_date=None,
        last_date=None,
        materialization_hint="python -m lynchpin.analysis materialize",
        reason=(
            f"{count} generated analysis artifacts are visible"
            if count
            else "analysis output directory exists but contains no readable artifacts"
            if root.exists()
            else "analysis output directory is missing"
        ),
    )


def _github_context_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    contract = source_contract("github_context")
    path = github_context_path()
    manifest = github_context_manifest_path()
    meta = _load_json(manifest)
    ready = _product_with_manifest_exists(path, manifest)
    schema_current = meta.get("schema_version") == GITHUB_CONTEXT_SCHEMA_VERSION
    max_age = _int_or_none(meta.get("ttl_seconds")) or contract.default_max_age_seconds or GITHUB_CONTEXT_DEFAULT_MAX_AGE_SECONDS
    max_age_hours = max_age // 3600 if max_age % 3600 == 0 else round(max_age / 3600, 1)
    materialized_at = _datetime_from_iso(meta.get("materialized_at"))
    age_current = (
        materialized_at is not None
        and (datetime.now(timezone.utc) - materialized_at.astimezone(timezone.utc)).total_seconds() <= max_age
    )
    if ready and not schema_current:
        status: Status = "partial"
        reason = "canonical GitHub context product schema is older than the current reader contract"
    elif ready and not age_current:
        status: Status = "partial"
        reason = f"canonical GitHub context product is older than the {max_age_hours}h network refresh contract"
    elif ready:
        status = "ready"
        reason = f"canonical GitHub context product is present and within the {max_age_hours}h network refresh contract"
    else:
        status = "partial"
        reason = "canonical GitHub context product or manifest is missing/malformed"
    return MaterializedDataset(
        name="github_context",
        status=status,
        authority=contract.authority,
        query_surface=contract.query_surface,
        materialized_paths=(path, manifest),
        raw_roots=(cfg.derived_root,),
        row_count=_manifest_row_count(meta, path),
        first_date=_date_from_iso(meta.get("first_date")),
        last_date=_date_from_iso(meta.get("last_date")),
        covered_dates=_manifest_covered_dates(meta),
        materialization_hint=contract.materialization_hint,
        reason=reason,
    )


def _health_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    root = cfg.exports_root / "health/processed"
    files = tuple(root.glob("health_*.jsonl")) if root.exists() else ()
    status = "ready" if files else "missing"
    row_count, first, last = _jsonl_date_bounds(files)
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
        materialization_hint="python -m lynchpin.cli.process_health",
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
        materialization_hint="python -m lynchpin.cli.process_health",
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
        materialization_hint="edit /realm/data/exports/health/processed/substance_log_unified.csv",
        reason="processed substance CSV is present" if path.exists() else "processed substance CSV is missing",
    )


def _spotify_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    path = spotify_streams_path()
    manifest = path.with_suffix(".manifest.json")
    meta = _load_json(manifest)
    raw_files = _spotify_input_files(cfg)
    product_ready = _product_with_manifest_exists(path, manifest)
    inputs_current = _manifest_inputs_current(meta, raw_files)
    schema_current = meta.get("schema_version") == SPOTIFY_STREAMS_SCHEMA_VERSION
    if product_ready and not schema_current:
        status: Status = "partial"
        reason = "canonical Spotify stream schema is older than the current reader contract"
    elif product_ready and inputs_current:
        status: Status = "ready"
        reason = "canonical all-export Spotify stream NDJSON is present"
    elif product_ready:
        status = "partial"
        reason = "canonical Spotify stream product was built from older local export inputs"
    elif raw_files:
        status = "partial"
        reason = "canonical Spotify stream product or manifest is missing/malformed"
    else:
        status = "missing"
        reason = "canonical Spotify stream product or manifest is missing/malformed"
    return MaterializedDataset(
        name="spotify",
        status=status,
        authority="Spotify GDPR export directories",
        query_surface="lynchpin.sources.spotify",
        materialized_paths=(path, manifest),
        raw_roots=(cfg.spotify_root,),
        row_count=_manifest_row_count(meta, path),
        first_date=_date_from_iso(meta.get("first_date")),
        last_date=_date_from_iso(meta.get("last_date")),
        materialization_hint="python -m lynchpin.ingest.exports_materialize spotify",
        reason=reason,
    )


def _reddit_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    root = reddit_canonical_dir()
    manifest = root / "manifest.json"
    files = tuple(root.glob("*.csv")) if root.exists() else ()
    meta = _load_json(manifest)
    raw_files = _reddit_input_files(cfg)
    csv_count: int | None = None
    first = _date_from_iso(meta.get("first_date"))
    last = _date_from_iso(meta.get("last_date"))
    if first is None and last is None:
        csv_count, first, last = _csv_date_bounds(files)
    row_count = _int_or_none(meta.get("row_count"))
    if row_count is None and files:
        row_count = csv_count
    product_ready = bool(files and _manifest_valid(manifest))
    inputs_current = _manifest_inputs_current(meta, raw_files)
    schema_current = meta.get("schema_version") == REDDIT_CANONICAL_SCHEMA_VERSION
    if product_ready and not schema_current:
        status: Status = "partial"
        reason = "canonical Reddit product schema is older than the current reader contract"
    elif product_ready and inputs_current:
        status: Status = "ready"
        reason = "canonical coalesced Reddit CSV products are present"
    elif product_ready:
        status = "partial"
        reason = "canonical Reddit products were built from older local export inputs"
    elif cfg.reddit_export_dir:
        status = "partial"
        reason = "canonical Reddit CSV products or manifest are missing/malformed"
    else:
        status = "missing"
        reason = "canonical Reddit CSV products or manifest are missing/malformed"
    return MaterializedDataset(
        name="reddit",
        status=status,
        authority="Reddit GDPR export directories",
        query_surface="lynchpin.sources.reddit",
        materialized_paths=(root, manifest),
        raw_roots=(cfg.exports_root / "reddit/processed", cfg.exports_root / "reddit/raw"),
        row_count=row_count,
        first_date=first,
        last_date=last,
        materialization_hint="python -m lynchpin.ingest.exports_materialize reddit",
        reason=reason,
    )


def _messenger_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    root = messenger_canonical_dir()
    manifest = root / "manifest.json"
    messages = root / "messages.ndjson"
    threads = root / "threads.ndjson"
    meta = _load_json(manifest)
    raw_paths = tuple(path for path in (cfg.fbmessenger_gdpr_root, cfg.fbmessenger_db) if path.exists())
    raw_files = tuple(_messenger_thread_files(cfg.fbmessenger_gdpr_root))
    product_ready = messages.exists() and threads.exists() and _manifest_valid(manifest)
    inputs_current = _manifest_inputs_current(meta, raw_files)
    schema_current = meta.get("schema_version") == MESSENGER_CANONICAL_SCHEMA_VERSION
    if product_ready and not schema_current:
        status: Status = "partial"
        reason = "canonical Messenger product schema is older than the current reader contract"
    elif product_ready and inputs_current:
        status: Status = "ready"
        reason = "canonical Messenger message/thread NDJSON products are present"
    elif product_ready:
        status = "partial"
        reason = "canonical Messenger products were built from older local export inputs"
    elif raw_paths:
        status = "partial"
        reason = "canonical Messenger products or manifest are missing/malformed"
    else:
        status = "missing"
        reason = "canonical Messenger products or manifest are missing/malformed"
    return MaterializedDataset(
        name="facebook_messenger",
        status=status,
        authority="Facebook Messenger GDPR export",
        query_surface="lynchpin.sources.exports",
        materialized_paths=(messages, threads, manifest),
        raw_roots=(cfg.exports_root / "comms/facebook-messenger/raw",),
        row_count=_int_or_none(meta.get("row_count")),
        first_date=_date_from_iso(meta.get("first_date")),
        last_date=_date_from_iso(meta.get("last_date")),
        materialization_hint="python -m lynchpin.ingest.exports_materialize facebook-messenger",
        reason=reason,
    )


def _raindrop_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    path = raindrop_bookmarks_path()
    manifest = path.with_suffix(".manifest.json")
    meta = _load_json(manifest)
    raw_files = _raindrop_input_files()
    product_ready = _product_with_manifest_exists(path, manifest)
    inputs_current = _manifest_inputs_current(meta, raw_files)
    schema_current = meta.get("schema_version") == RAINDROP_BOOKMARKS_SCHEMA_VERSION
    if product_ready and not schema_current:
        status: Status = "partial"
        reason = "canonical Raindrop bookmark schema is older than the current reader contract"
    elif product_ready and inputs_current:
        status: Status = "ready"
        reason = "canonical coalesced Raindrop bookmark CSV is present"
    elif product_ready:
        status = "partial"
        reason = "canonical Raindrop bookmark product was built from older local export inputs"
    elif cfg.raindrop_csv and cfg.raindrop_csv.exists():
        status = "partial"
        reason = "canonical Raindrop bookmark product or manifest is missing/malformed"
    else:
        status = "missing"
        reason = "canonical Raindrop bookmark product or manifest is missing/malformed"
    return MaterializedDataset(
        name="raindrop",
        status=status,
        authority="Raindrop export CSVs",
        query_surface="lynchpin.sources.exports",
        materialized_paths=(path, manifest),
        raw_roots=(cfg.raindrop_dir,),
        row_count=_manifest_row_count(meta, path, header_rows=1),
        first_date=_date_from_iso(meta.get("first_date")),
        last_date=_date_from_iso(meta.get("last_date")),
        materialization_hint="python -m lynchpin.ingest.exports_materialize raindrop",
        reason=reason,
    )


def _communications_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    path = communication_events_path()
    manifest = communication_manifest_path()
    meta = _load_json(manifest)
    input_files = communication_input_files(cfg)
    ready = _product_with_manifest_exists(path, manifest)
    inputs_current = _manifest_inputs_current(meta, input_files)
    row_count = _manifest_row_count(meta, path)
    schema_current = meta.get("schema_version") == COMMUNICATION_EVENTS_SCHEMA_VERSION
    if ready and not schema_current:
        status: Status = "partial"
        reason = "canonical unified communication event schema is older than the current reader contract"
    elif ready and not inputs_current:
        status: Status = "partial"
        reason = "canonical unified communication events were built from older local input files"
    elif ready:
        status = "ready"
        reason = "canonical unified communication events are present"
    else:
        status = "partial" if input_files else "missing"
        reason = "canonical communication event product is missing"
    return MaterializedDataset(
        name="communications",
        status=status,
        authority="canonical Messenger plus parseable Outlook communication exports",
        query_surface="lynchpin.sources.communications",
        materialized_paths=(path, manifest),
        raw_roots=(cfg.exports_root / "comms",),
        row_count=row_count,
        first_date=_date_from_iso(meta.get("first_date")),
        last_date=_date_from_iso(meta.get("last_date")),
        materialization_hint="python -m lynchpin.ingest.communications_materialize",
        reason=reason,
    )


def _bookmarks_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    path = bookmarks_path()
    manifest = bookmarks_manifest_path()
    meta = _load_json(manifest)
    raw_files = tuple(_discover_bookmark_files(_bookmark_roots(cfg.browser_bookmarks_root)))
    ready = _product_with_manifest_exists(path, manifest)
    inputs_current = _manifest_inputs_current(meta, raw_files)
    schema_current = meta.get("schema_version") == BOOKMARK_EVENTS_SCHEMA_VERSION
    if ready and not schema_current:
        status: Status = "partial"
        reason = "canonical bookmark schema is older than the current reader contract"
    elif ready and inputs_current:
        status: Status = "ready"
        reason = "canonical bookmark NDJSON is present"
    elif ready:
        status = "partial"
        reason = "canonical bookmark product was built from older local input files"
    elif raw_files:
        status = "partial"
        reason = "canonical bookmark product is missing"
    else:
        status = "missing"
        reason = "canonical bookmark product is missing"
    row_count = _manifest_row_count(meta, path)
    return MaterializedDataset(
        name="browser_bookmarks",
        status=status,
        authority="browser bookmark exports and Firefox/Vivaldi profile data",
        query_surface="lynchpin.sources.bookmarks",
        materialized_paths=(path, manifest),
        raw_roots=(cfg.browser_bookmarks_root,),
        row_count=row_count,
        first_date=_date_from_iso(meta.get("first_date")),
        last_date=_date_from_iso(meta.get("last_date")),
        materialization_hint="python -m lynchpin.ingest.bookmarks_materialize",
        reason=reason,
    )


def _arbtt_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    path = arbtt_events_path()
    manifest = arbtt_manifest_path()
    meta = _load_json(manifest)
    raw_files = tuple(_capture_logs(cfg.arbtt_root))
    row_count = _manifest_row_count(meta, path)
    ready = _product_with_manifest_exists(path, manifest) and (row_count or 0) > 0
    inputs_current = _manifest_inputs_current(meta, raw_files)
    schema_current = meta.get("schema_version") == ARBTT_EVENTS_SCHEMA_VERSION
    if ready and not schema_current:
        status: Status = "partial"
        reason = "canonical ARBTT focus event schema is older than the current reader contract"
    elif ready and inputs_current:
        status: Status = "ready"
        reason = "canonical ARBTT focus events are present"
    elif ready:
        status = "partial"
        reason = "canonical ARBTT focus events were built from older local input files"
    elif raw_files:
        status = "partial"
        reason = "canonical ARBTT product is missing or empty; ensure arbtt-dump is available"
    else:
        status = "missing"
        reason = "canonical ARBTT product is missing or empty; ensure arbtt-dump is available"
    return MaterializedDataset(
        name="arbtt",
        status=status,
        authority="ARBTT capture.log files",
        query_surface="lynchpin.sources.arbtt",
        materialized_paths=(path, manifest),
        raw_roots=(cfg.arbtt_root,),
        row_count=row_count,
        first_date=_date_from_iso(meta.get("first_date")),
        last_date=_date_from_iso(meta.get("last_date")),
        materialization_hint="python -m lynchpin.ingest.arbtt_materialize",
        reason=reason,
    )


def _irc_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    path = irc_events_path()
    manifest = irc_manifest_path()
    meta = _load_json(manifest)
    raw_root = irc_raw_root()
    raw_files = irc_input_files(raw_root)
    row_count = _manifest_row_count(meta, path)
    ready = _product_with_manifest_exists(path, manifest) and (row_count or 0) > 0
    inputs_current = _manifest_inputs_current(meta, raw_files)
    schema_current = meta.get("schema_version") == IRC_EVENTS_SCHEMA_VERSION
    if ready and not schema_current:
        status: Status = "partial"
        reason = "canonical IRC event schema is older than the current reader contract"
    elif ready and not inputs_current:
        status: Status = "partial"
        reason = "canonical IRC events were built from older local input files"
    elif ready:
        status = "ready"
        reason = "canonical IRC events ndjson is present"
    elif raw_files:
        status = "partial"
        reason = "raw WeeChat logs need materialization"
    else:
        status = "missing"
        reason = "no raw IRC log files found"
    return MaterializedDataset(
        name="irc",
        status=status,
        authority="raw WeeChat IRC log files",
        query_surface="lynchpin.sources.irc_raw",
        materialized_paths=(path, manifest),
        raw_roots=(raw_root,),
        row_count=row_count,
        first_date=_date_from_iso(meta.get("first_date")),
        last_date=_date_from_iso(meta.get("last_date")),
        covered_dates=_manifest_covered_dates(meta),
        materialization_hint="python -m lynchpin.ingest.irc_materialize",
        reason=reason,
    )


def _machine_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    manifest = canonical_machine_table_path("manifest").with_suffix(".json")
    meta = _load_json(manifest)
    input_files = machine_input_files(cfg)
    tables = meta.get("tables") if isinstance(meta.get("tables"), dict) else {}
    paths = tuple(
        canonical_machine_table_path(name)
        for name in (
            "metric_sample",
            "gpu_sample",
            "network_sample",
            "service_state",
            "block_device_sample",
            "service_cgroup_io_sample",
            "service_cgroup_pressure_sample",
            "process_io_delta_sample",
        )
    )
    ready = _manifest_valid(manifest) and all(path.exists() for path in paths)
    inputs_current = _manifest_inputs_current(meta, input_files)
    schema_current = meta.get("schema_version") == MACHINE_TELEMETRY_SCHEMA_VERSION
    if ready and not schema_current:
        status: Status = "partial"
        reason = "canonical machine telemetry schema is older than the current reader contract"
    elif ready and not inputs_current:
        status: Status = "partial"
        reason = "canonical machine telemetry products were built from an older local database"
    elif ready:
        status = "ready"
        reason = "canonical machine telemetry NDJSON tables are present"
    else:
        status = "partial" if input_files else "missing"
        reason = "canonical machine telemetry products are missing"
    return MaterializedDataset(
        name="machine",
        status=status,
        authority="machine telemetry SQLite/JSONL captures",
        query_surface="lynchpin.sources.machine plus analysis machine artifacts",
        materialized_paths=(*paths, manifest),
        raw_roots=(cfg.machine_capture_root,),
        row_count=_int_or_none(meta.get("row_count")),
        first_date=_date_from_iso(_first_table_date(tables)),
        last_date=_date_from_iso(_last_table_date(tables)),
        covered_dates=_manifest_covered_dates(meta),
        materialization_hint="python -m lynchpin.ingest.machine_materialize",
        reason=reason,
    )


def _spotify_daily_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    contract = source_contract("spotify_daily")
    path = spotify_daily_path()
    manifest = spotify_daily_manifest_path()
    meta = _load_json(manifest)
    input_files = spotify_daily_input_files()
    ready = _product_with_manifest_exists(path, manifest)
    inputs_current = _manifest_inputs_current(meta, input_files)
    schema_current = meta.get("schema_version") == SPOTIFY_DAILY_SCHEMA_VERSION
    if ready and not schema_current:
        status: Status = "partial"
        reason = "canonical Spotify daily product schema is older than the current reader contract"
    elif ready and not inputs_current:
        status: Status = "partial"
        reason = "canonical Spotify daily product was built from an older stream product"
    elif ready:
        status = "ready"
        reason = "canonical Spotify daily product is present"
    else:
        status = "partial" if input_files else "missing"
        reason = "canonical Spotify daily product or manifest is missing/malformed"
    return MaterializedDataset(
        name="spotify_daily",
        status=status,
        authority=contract.authority,
        query_surface=contract.query_surface,
        materialized_paths=(path, manifest),
        raw_roots=(cfg.exports_root / "spotify/processed",),
        row_count=_manifest_row_count(meta, path),
        first_date=_date_from_iso(meta.get("first_date")),
        last_date=_date_from_iso(meta.get("last_date")),
        covered_dates=_manifest_covered_dates(meta),
        materialization_hint=contract.materialization_hint,
        reason=reason,
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
        materialization_hint=contract.materialization_hint,
        reason="live xtask history SQLite is readable" if ready else f"xtask history SQLite is missing or unreadable at {path}",
    )


def _polylogue_devtools_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    from .sources.polylogue_devtools import source_readiness

    contract = source_contract("polylogue_devtools")
    ready = source_readiness(
        xtask_path=cfg.polylogue_devtools_xtask_jsonl,
        logs_dir=cfg.polylogue_devtools_logs_dir,
    )
    row_count = ready.xtask_rows + ready.meta_files
    present = ready.xtask_path.exists() or ready.logs_dir.exists()
    return MaterializedDataset(
        name="polylogue_devtools",
        status="ready" if present and row_count else "partial" if present else "missing",
        authority=contract.authority,
        query_surface=contract.query_surface,
        materialized_paths=(ready.xtask_path, ready.logs_dir),
        raw_roots=(cfg.polylogue_project_root,),
        row_count=row_count if present else None,
        first_date=ready.first_seen.date() if ready.first_seen else None,
        last_date=ready.last_seen.date() if ready.last_seen else None,
        materialization_hint=contract.materialization_hint,
        reason=(
            f"Polylogue devtools ledgers readable: {ready.xtask_rows} xtask rows, {ready.meta_files} meta files"
            if present
            else f"Polylogue devtools ledgers missing under {cfg.polylogue_project_root}"
        ),
    )


def _personal_daily_signals_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    contract = source_contract("personal_daily_signals")
    path = personal_daily_signals_path()
    manifest = personal_daily_signals_manifest_path()
    meta = _load_json(manifest)
    ready = _product_with_manifest_exists(path, manifest)
    input_files = _manifest_declared_input_files(meta)
    inputs_current = _manifest_inputs_current(meta, input_files)
    schema_current = meta.get("schema_version") == PERSONAL_DAILY_SIGNALS_SCHEMA_VERSION
    if ready and not schema_current:
        status: Status = "partial"
        reason = "canonical personal daily-signal product schema is older than the current reader contract"
    elif ready and not inputs_current:
        status: Status = "partial"
        reason = "canonical personal daily-signal product was built from older upstream products"
    elif ready:
        status = "ready"
        reason = "canonical personal daily-signal product is present"
    else:
        status = "partial"
        reason = "canonical personal daily-signal product or manifest is missing/malformed"
    return MaterializedDataset(
        name="personal_daily_signals",
        status=status,
        authority=contract.authority,
        query_surface=contract.query_surface,
        materialized_paths=(path, manifest),
        raw_roots=(cfg.derived_root,),
        row_count=_manifest_row_count(meta, path),
        first_date=_date_from_iso(meta.get("first_date")),
        last_date=_date_from_iso(meta.get("last_date")),
        covered_dates=_manifest_covered_dates(meta),
        materialization_hint=contract.materialization_hint,
        reason=reason,
    )


def _temporal_signals_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    contract = source_contract("temporal_signals")
    path = temporal_signals_path()
    manifest = temporal_signals_manifest_path()
    meta = _load_json(manifest)
    ready = _product_with_manifest_exists(path, manifest)
    input_files = _manifest_declared_input_files(meta)
    inputs_current = _manifest_inputs_current(meta, input_files)
    schema_current = meta.get("schema_version") == TEMPORAL_SIGNALS_SCHEMA_VERSION
    if ready and not schema_current:
        status: Status = "partial"
        reason = "canonical temporal signal product schema is older than the current reader contract"
    elif ready and not inputs_current:
        status: Status = "partial"
        reason = "canonical temporal signal product was built from older upstream products"
    elif ready:
        status = "ready"
        reason = "canonical temporal signal product is present"
    else:
        status = "partial"
        reason = "canonical temporal signal product or manifest is missing/malformed"
    return MaterializedDataset(
        name="temporal_signals",
        status=status,
        authority=contract.authority,
        query_surface=contract.query_surface,
        materialized_paths=(path, manifest),
        raw_roots=(cfg.derived_root,),
        row_count=_manifest_row_count(meta, path),
        first_date=_date_from_iso(meta.get("first_date")),
        last_date=_date_from_iso(meta.get("last_date")),
        covered_dates=_manifest_covered_dates(meta),
        materialization_hint=contract.materialization_hint,
        reason=reason,
    )


def _sleep_productivity_dataset(cfg: LynchpinConfig) -> MaterializedDataset:
    contract = source_contract("sleep_productivity")
    path = sleep_productivity_path()
    manifest = sleep_productivity_manifest_path()
    meta = _load_json(manifest)
    ready = _product_with_manifest_exists(path, manifest)
    input_files = _manifest_declared_input_files(meta)
    inputs_current = _manifest_inputs_current(meta, input_files)
    schema_current = meta.get("schema_version") == SLEEP_PRODUCTIVITY_SCHEMA_VERSION
    if ready and not schema_current:
        status: Status = "partial"
        reason = "canonical sleep-productivity product schema is older than the current reader contract"
    elif ready and not inputs_current:
        status: Status = "partial"
        reason = "canonical sleep-productivity product was built from older upstream products"
    elif ready:
        status = "ready"
        reason = "canonical sleep-productivity product is present"
    else:
        status = "partial"
        reason = "canonical sleep-productivity product or manifest is missing/malformed"
    return MaterializedDataset(
        name="sleep_productivity",
        status=status,
        authority=contract.authority,
        query_surface=contract.query_surface,
        materialized_paths=(path, manifest),
        raw_roots=(cfg.derived_root,),
        row_count=_manifest_row_count(meta, path),
        first_date=_date_from_iso(meta.get("first_date")),
        last_date=_date_from_iso(meta.get("last_date")),
        covered_dates=_manifest_covered_dates(meta),
        materialization_hint=contract.materialization_hint,
        reason=reason,
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
    materialization_hint: str,
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
        materialization_hint=materialization_hint,
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
        manifest_bounds = _jsonl_single_file_manifest_bounds(path)
        if manifest_bounds is None:
            manifest_bounds = _jsonl_date_bounds_cached(
                (str(path),),
                files_signature((path,)),
            )
        row_count, row_first, row_last = manifest_bounds
        count += row_count
        first = _min_date(first, row_first)
        last = _max_date(last, row_last)
    return count, first, last


def _jsonl_single_file_manifest_bounds(path: Path) -> tuple[int, date | None, date | None] | None:
    manifest = path.with_suffix(".manifest.json")
    if not _manifest_valid(manifest):
        return None
    try:
        if manifest.stat().st_mtime_ns < path.stat().st_mtime_ns:
            return None
    except OSError:
        return None
    meta = _load_json(manifest)
    row_count = _int_or_none(meta.get("row_count"))
    if row_count is None:
        return None
    return row_count, _date_from_iso(meta.get("first_date")), _date_from_iso(meta.get("last_date"))


def _min_date(left: date | None, right: date | None) -> date | None:
    if left is None:
        return right
    if right is None:
        return left
    return left if left <= right else right


def _max_date(left: date | None, right: date | None) -> date | None:
    if left is None:
        return right
    if right is None:
        return left
    return left if left >= right else right


@lru_cache(maxsize=128)
def _jsonl_date_bounds_cached(
    path_names: tuple[str, ...],
    _signature: tuple[tuple[str, int | None, int | None], ...],
) -> tuple[int, date | None, date | None]:
    paths = tuple(Path(path) for path in path_names)
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
    ordered = tuple(paths)
    return _csv_date_bounds_cached(
        tuple(str(path) for path in ordered),
        files_signature(ordered),
    )


@lru_cache(maxsize=128)
def _csv_date_bounds_cached(
    path_names: tuple[str, ...],
    _signature: tuple[tuple[str, int | None, int | None], ...],
) -> tuple[int | None, date | None, date | None]:
    paths = tuple(Path(path) for path in path_names)
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
    """Best-effort read of session-profile first/last dates.

    Prefer direct product-table bounds so status reads do not hydrate thousands
    of session profiles. Fall back to profile iteration for older Polylogue
    products that do not expose the current SQLite table.
    """
    from .sources.polylogue import PolylogueMaterializationError
    from .sources.polylogue_client import _default_polylogue_db_path

    direct = _polylogue_date_bounds_from_sqlite(_default_polylogue_db_path())
    if direct != (None, None):
        return direct

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


def _polylogue_date_bounds_from_sqlite(path: Path) -> tuple[date | None, date | None]:
    if not path.exists():
        return None, None
    try:
        with sqlite3.connect(path) as conn:
            row = conn.execute(
                """
                SELECT MIN(canonical_session_date), MAX(canonical_session_date)
                FROM session_profiles
                WHERE canonical_session_date IS NOT NULL
                  AND canonical_session_date != ''
                """
            ).fetchone()
    except sqlite3.Error:
        return None, None
    if not row:
        return None, None
    return _date_from_iso(row[0]), _date_from_iso(row[1])


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


def _datetime_from_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    raw = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


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


def _manifest_row_count(meta: dict[str, Any], path: Path, *, header_rows: int = 0) -> int | None:
    _ = (path, header_rows)
    row_count = _int_or_none(meta.get("row_count"))
    if row_count is not None:
        return row_count
    return None


def _line_count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("rb") as handle:
        return sum(1 for _ in handle)


def _count_files(root: Path, *, suffixes: tuple[str, ...] | None = None) -> int:
    if not root.exists():
        return 0
    wanted = tuple(suffix.lower() for suffix in suffixes) if suffixes is not None else None
    count = 0
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False) and (
                            wanted is None or Path(entry.name).suffix.lower() in wanted
                        ):
                            count += 1
                    except OSError:
                        continue
        except OSError:
            continue
    return count


def _path_mtime_date(path: Path) -> date | None:
    try:
        if not path.exists():
            return None
        if path.is_file():
            return datetime.fromtimestamp(path.stat().st_mtime).date()
        latest = path.stat().st_mtime
        for child in path.iterdir():
            try:
                latest = max(latest, child.stat().st_mtime)
            except OSError:
                continue
        return datetime.fromtimestamp(latest).date()
    except OSError:
        return None


def _csv_count(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        with path.open(newline="", encoding="utf-8", errors="replace") as handle:
            reader = csv.reader(handle)
            next(reader, None)
            return sum(1 for _ in reader)
    except OSError:
        return None


def _sqlite_count(path: Path, table: str) -> int | None:
    try:
        with sqlite3.connect(path) as conn:
            row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    except sqlite3.Error:
        return None
    return int(row[0]) if row else None


def _duck_substrate_status(
    path: Path,
) -> tuple[int | None, tuple[int, int] | None, tuple[str, str | None] | None, int | None]:
    if not path.exists():
        return None, None, None, None
    try:
        import duckdb

        conn = duckdb.connect(str(path), read_only=True)
        try:
            builds = _duck_scalar_count(conn, "evidence_graph_build")
            latest_build_counts = _duck_latest_graph_build_counts(conn)
            latest_status = _duck_latest_source_status(conn, "evidence_graph")
            promotion_count = _duck_successful_promotion_count(conn)
        finally:
            conn.close()
    except Exception:
        return None, None, None, None
    return builds, latest_build_counts, latest_status, promotion_count


def _duck_evidence_graph_status(
    path: Path,
) -> tuple[int | None, tuple[int, int] | None, tuple[str, str | None] | None]:
    builds, latest_build_counts, latest_status, _promotion_count = _duck_substrate_status(path)
    return builds, latest_build_counts, latest_status


def _duck_scalar_count(conn: Any, table: str) -> int | None:
    try:
        row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    except Exception:
        return None
    return int(row[0]) if row else None


def _duck_latest_source_status(conn: Any, source: str) -> tuple[str, str | None] | None:
    try:
        row = conn.execute(
            """
            SELECT status, reason
            FROM substrate_source_status
            WHERE source = ?
            ORDER BY recorded_at DESC
            LIMIT 1
            """,
            [source],
        ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    return str(row[0]), row[1]


def _duck_successful_promotion_count(conn: Any) -> int | None:
    try:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM substrate_promotion_run
            WHERE status = 'ok'
            """
        ).fetchone()
    except Exception:
        return None
    return int(row[0]) if row else None


def _duck_latest_graph_build_counts(conn: Any) -> tuple[int, int] | None:
    try:
        row = conn.execute(
            """
            SELECT node_count, edge_count
            FROM evidence_graph_build
            ORDER BY materialized_at DESC, generated_at DESC
            LIMIT 1
            """
        ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    return int(row[0]), int(row[1])
