"""Code-owned handlers for canonical source products.

Plan data contains only identities.  This module is the closed mapping from
those identities to concrete implementation callables; it deliberately has no
import-by-name or module path resolution.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

from ..analysis.keylog import write_keylog_analysis
from ..analysis.ambient_intelligence import write_ambient_intelligence
from ..core.config import get_config
from ..core.errors import MaterializationError
from ..core.io import resolve_analysis_path
from ..ingest.activity_content_materialize import materialize_activity_content
from ..ingest.activitywatch_derived_materialize import materialize_activitywatch_derived
from ..ingest.activitywatch_event_index_materialize import materialize_activitywatch_event_index
from ..ingest.activitywatch_materialize import materialize_activitywatch_events
from ..ingest.arbtt_materialize import materialize_arbtt_events
from ..ingest.bookmarks_materialize import materialize_bookmarks
from ..ingest.code_snapshots_materialize import materialize_code_snapshots
from ..ingest.communications_materialize import materialize_communication_events
from ..ingest.exports_materialize import (
    materialize_messenger,
    materialize_raindrop,
    materialize_reddit,
    materialize_spotify,
)
from ..ingest.github_context_materialize import materialize_github_context
from ..ingest.google_takeout_materialize import materialize_google_takeout_inventory
from ..ingest.google_takeout_products import materialize_google_takeout_products
from ..ingest.gmail_takeout_materialize import materialize_gmail_events
from ..ingest.health_coverage_materialize import materialize_health_coverage
from ..ingest.irc_materialize import materialize_irc_events
from ..ingest.machine_materialize import materialize_machine_telemetry
from ..ingest.personal_signals_materialize import materialize_personal_daily_signals, materialize_spotify_daily
from ..ingest.polylogue_verify_materialize import materialize_polylogue_verify_runs
from ..ingest.sleep_productivity_materialize import materialize_sleep_productivity
from ..ingest.substack_materialize import materialize_substack
from ..ingest.temporal_signals_materialize import materialize_temporal_signals
from ..ingest.terminal_materialize import materialize_atuin_history
from ..ingest.title_metadata_materialize import materialize_title_metadata
from ..ingest.webhistory import run as run_webhistory_pipeline
from .executor import HandlerDefinition, StepContext


Materializer = Callable[..., Any]


def _materialize_webhistory(*, start: date | None = None, end: date | None = None) -> None:
    cfg = get_config()
    if cfg.webhistory_ndjson is None:
        raise MaterializationError("webhistory", reason="canonical webhistory output path is not configured")
    run_webhistory_pipeline(data_dir=cfg.webhistory_dir, output=cfg.webhistory_ndjson, start=start, end=end)


def _materialize_google_takeout() -> None:
    materialize_google_takeout_inventory()
    materialize_google_takeout_products()
    materialize_gmail_events()


def _materialize_keylog_analysis(*, start: date | None = None, end: date | None = None) -> dict[str, Any]:
    from ..analysis.keylog import DEFAULT_HYPRLAND_BINDINGS

    if start is None or end is None:
        end = date.today()
        start = end - timedelta(days=13)
    inclusive_end = max(start, end - timedelta(days=1))
    analysis = write_keylog_analysis(
        Path(resolve_analysis_path("keylog_analysis.json")),
        start=start,
        end=inclusive_end,
        bindings_path=DEFAULT_HYPRLAND_BINDINGS,
    )
    return {"row_count": analysis.source_event_count}


def _materialize_ambient_intelligence() -> dict[str, Any]:
    return write_ambient_intelligence()


_SOURCE_HANDLERS: dict[str, Materializer] = {
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
    "health_coverage": materialize_health_coverage,
    "irc": materialize_irc_events,
    "code_snapshots": materialize_code_snapshots,
    "substack": materialize_substack,
    "polylogue_verify_runs": materialize_polylogue_verify_runs,
    "ambient_intelligence": _materialize_ambient_intelligence,
}

_WINDOWED = frozenset(
    name
    for name, handler in _SOURCE_HANDLERS.items()
    if name not in {"google_takeout", "title_metadata", "spotify", "reddit", "facebook_messenger", "communications", "raindrop", "browser_bookmarks", "arbtt", "health_coverage", "code_snapshots", "polylogue_verify_runs", "ambient_intelligence"}
)
_REFRESH_ID = frozenset({"activitywatch", "personal_daily_signals", "temporal_signals"})


def run_source_handler(context: StepContext) -> Any:
    """Invoke the statically selected source handler for one typed step."""

    name = context.step.product
    try:
        handler = _SOURCE_HANDLERS[name]
    except KeyError as exc:
        raise KeyError(f"no closed source handler for {name}") from exc
    kwargs: dict[str, Any] = {}
    window = context.runtime.get("window", context.step.effective_window)
    if name in _WINDOWED and window is not None:
        kwargs.update(start=window[0], end=window[1])
    if name in _REFRESH_ID and context.runtime.get("refresh_id") is not None:
        kwargs["refresh_id"] = context.runtime["refresh_id"]
    return handler(**kwargs)


def source_handler_definitions() -> dict[str, HandlerDefinition]:
    """Return the immutable source-handler definitions used by production."""

    return {
        f"source:{name}": HandlerDefinition(
            identity=f"source:{name}",
            handler=run_source_handler,
            raw_read_permission="owner-native" if name not in {"activitywatch_event_index", "activitywatch_derived", "activity_content", "personal_daily_signals", "temporal_signals", "sleep_productivity"} else "none",
            window_policy="bounded" if name in _WINDOWED else "unbounded",
        )
        for name in sorted(_SOURCE_HANDLERS)
    }


__all__ = ["source_handler_definitions"]
