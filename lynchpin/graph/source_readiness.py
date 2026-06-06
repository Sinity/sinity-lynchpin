"""Source readiness inventory for current-state and narrative analysis."""
from __future__ import annotations
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from ..core.config import get_config
from ..core.evidence import CostClass, ReadinessStatus, SourceReadiness, SourceReadinessReport
from ..sources.analysis_artifacts import artifact_inventory
from ..sources.source_observations import SourceObservation, source_observations
from .coverage import coverage_report

def archive_readiness(*args: Any, **kwargs: Any) -> Any:
    from ..sources.polylogue import archive_readiness as impl
    if 'include_polylogue_product_counts' in kwargs:
        kwargs['include_heavy_counts'] = kwargs.pop('include_polylogue_product_counts')
    return impl(*args, **kwargs)

def source_readiness(*, start: date, end: date, include_polylogue_product_counts: bool=True, include_github_frontier: bool=False, include_analysis_inventory: bool=True, repair_materializations: bool=True) -> SourceReadinessReport:
    """Return a compact readiness report for analysis-relevant sources."""
    cfg = get_config()
    materialization_end = end + timedelta(days=1)
    available = cfg.available_sources()
    observations = {item.source: item for item in source_observations()} if repair_materializations else {}
    coverage = coverage_report(start=start, end=materialization_end, repair_materializations=repair_materializations).by_source()
    exports_root = getattr(cfg, 'exports_root', None)
    comms_root = exports_root / 'comms' if exports_root is not None else None
    items: list[SourceReadiness] = [_path_source('activitywatch', available['activitywatch'], cfg.activitywatch_db, 'ActivityWatch focus database is present', 'ActivityWatch focus database is missing', coverage=coverage.get('activitywatch')), _path_source('terminal', available['atuin'], cfg.atuin_db, 'Atuin shell history database is present', 'Atuin shell history database is missing', coverage=coverage.get('terminal')), _path_source('git', cfg.sinnix_root.parent.exists(), cfg.sinnix_root.parent, 'local project git repositories are read live', 'project checkout root is missing'), _polylogue_source(include_product_counts=include_polylogue_product_counts), _path_source('raw_log', available['raw_log'], cfg.raw_log_file, 'knowledgebase raw-log is present', 'knowledgebase raw-log is missing', count_fn=_line_count), _path_source('browser', available['webhistory'], cfg.webhistory_ndjson, 'canonical browser history NDJSON is present', 'canonical browser history NDJSON is missing', observation=observations.get('webhistory'), coverage=coverage.get('webhistory')), _optional_product_source('browser_bookmarks', getattr(cfg, 'browser_bookmarks_root', None), 'processed/bookmarks.ndjson', 'canonical browser bookmark product is present', 'canonical browser bookmark product is missing'), _optional_product_source('communications', comms_root, 'processed/communication_events.ndjson', 'canonical communication event product is present', 'canonical communication event product is missing'), _optional_product_source('arbtt', getattr(cfg, 'arbtt_root', None), 'processed/events.ndjson', 'canonical ARBTT focus product is present', 'canonical ARBTT focus product is missing'), _materialized_contract_source('title_metadata', 'canonical title metadata product is present', 'canonical title metadata product is missing', start=start, end=materialization_end, repair=repair_materializations), _materialized_contract_source('activity_content', 'canonical ActivityWatch content metadata product is present', 'canonical ActivityWatch content metadata product is missing', start=start, end=materialization_end, repair=repair_materializations), _path_source('sleep', available['sleep'], cfg.sleep_jsonl, 'sleep processed export is present', 'sleep processed export is missing', observation=observations.get('sleep'), coverage=coverage.get('sleep')), _path_source('health', cfg.samsung_gdpr_cloud_dir.exists(), cfg.samsung_gdpr_cloud_dir, 'Samsung health export root is present', 'Samsung health export root is missing', coverage=coverage.get('health')), _path_source('spotify', available['spotify'], cfg.spotify_root, 'Spotify processed export is present', 'Spotify processed export is missing', observation=observations.get('spotify'), coverage=coverage.get('spotify')), _path_source('reddit', available['reddit'], cfg.reddit_export_dir, 'Reddit processed export is present', 'Reddit processed export is missing', observation=observations.get('reddit'), coverage=coverage.get('reddit')), _path_source('messenger', available['fbmessenger'], cfg.fbmessenger_gdpr_root if cfg.fbmessenger_gdpr_root.exists() else cfg.fbmessenger_db, 'Facebook Messenger export/database is present', 'Facebook Messenger export/database is missing', observation=observations.get('fbmessenger'), coverage=coverage.get('messenger')), _path_source('raindrop', available['raindrop'], cfg.raindrop_csv, 'Raindrop CSV export is present', 'Raindrop CSV export is missing', observation=observations.get('raindrop'), coverage=coverage.get('raindrop')), _path_source('substance', (cfg.exports_root / 'health/processed/substance_log_unified.csv').exists(), cfg.exports_root / 'health/processed/substance_log_unified.csv', 'substance log is present', 'substance log is missing', coverage=coverage.get('substance')), _machine_source(), _xtask_history_source(), _polylogue_devtools_source(), _sinnix_runtime_inventory_source(), _analysis_source(include_inventory=include_analysis_inventory), _github_source(include_frontier=include_github_frontier, start=start, end=materialization_end, repair=repair_materializations)]
    return SourceReadinessReport(start=start, end=end, generated_at=datetime.now(timezone.utc), sources=tuple(sorted(items, key=lambda item: item.source)))

def _github_source(*, include_frontier: bool, start: date, end: date, repair: bool) -> SourceReadiness:
    base = _materialized_contract_source('github_context', 'GitHub lifecycle context product is materialized within the 48h network refresh contract', 'GitHub lifecycle context product is missing', start=start, end=end, repair=repair)
    reason = base.reason
    caveats = base.caveats
    if include_frontier:
        reason = f'{reason}; frontier rows are enabled for this analysis'
    else:
        caveats = (*caveats, 'frontier rows are available in github_context but not rendered by this read')
    return SourceReadiness(source='github', status=base.status, reason=reason, cost=base.cost, path=base.path, count=base.count, first_date=base.first_date, last_date=base.last_date, caveats=caveats)

def render_source_readiness(report: SourceReadinessReport) -> str:
    """Render source readiness in a compact Markdown table."""
    lines = ['| Source | Status | Cost | Count | Date coverage | Reason |', '|---|---:|---:|---:|---|---|']
    for item in report.sources:
        coverage = _coverage_window(item)
        count = '' if item.count is None else str(item.count)
        reason = item.reason.replace('|', '\\|')
        if item.caveats:
            reason = reason + '<br>' + '<br>'.join((c.replace('|', '\\|') for c in item.caveats))
        lines.append(f'| {item.source} | {item.status} | {item.cost} | {count} | {coverage} | {reason} |')
    return '\n'.join(lines)

def _path_source(source: str, exists: bool, path: Path | str | None, ok_reason: str, missing_reason: str, *, cost: CostClass='materialized', count_fn: Callable[[Path], int | None] | None=None, observation: SourceObservation | None=None, coverage: object | None=None) -> SourceReadiness:
    resolved = Path(path) if path is not None else None
    if not exists or resolved is None:
        return SourceReadiness(source=source, status='missing', reason=missing_reason, cost=cost, path=str(resolved) if resolved else None)
    count = count_fn(resolved) if count_fn is not None else None
    last_date = observation.last_observed if observation is not None else _mtime_date(resolved)
    first_date = None
    status: ReadinessStatus = 'available'
    caveats: tuple[str, ...] = ()
    if coverage is not None:
        first_date = getattr(coverage, 'first_date', None)
        last_date = getattr(coverage, 'last_date', last_date)
        count = count if count is not None else getattr(coverage, 'row_count', None)
        coverage_status = getattr(coverage, 'status', None)
        if coverage_status in {'partial', 'out_of_range', 'missing'}:
            status = 'partial' if coverage_status == 'partial' else 'out_of_range' if coverage_status == 'out_of_range' else 'missing'
            hint = getattr(coverage, 'repair_hint', None)
            reason = getattr(coverage, 'reason', '')
            caveats = tuple((item for item in (reason, hint) if item))
        elif coverage_status == 'available':
            status = 'available'
    return SourceReadiness(source=source, status=status, reason=ok_reason, cost=cost, path=str(resolved), count=count, first_date=first_date, last_date=last_date, caveats=caveats)

def _optional_product_source(source: str, root: Path | None, relpath: str, ok_reason: str, missing_reason: str) -> SourceReadiness:
    path = root / relpath if root is not None else None
    return _path_source(source, path.exists() if path is not None else False, path, ok_reason, missing_reason, count_fn=_line_count)

def _materialized_contract_source(source: str, ok_reason: str, missing_reason: str, *, start: date, end: date, repair: bool) -> SourceReadiness:
    from ..materialization import ensure_materialized
    try:
        result = ensure_materialized(source, window=(start, end), budget='inline' if repair else 'manual', cfg=get_config())
    except Exception:
        return SourceReadiness(source=source, status='missing', reason=missing_reason, cost='materialized')
    if result.status in {'ready', 'updated'}:
        status: ReadinessStatus = 'available'
    elif result.status in {'failed', 'blocked'}:
        status = 'partial'
    else:
        status = 'missing'
    first_date = _date_from_high_water(result.source_high_water.get('first_date'))
    last_date = _date_from_high_water(result.source_high_water.get('last_date'))
    caveats = tuple(result.diagnostics)
    if status != 'available' and result.reason:
        caveats = (*caveats, result.reason)
    return SourceReadiness(source=source, status=status, reason=ok_reason if status == 'available' else result.reason, cost='materialized', path=str(result.product_paths[0]) if result.product_paths else None, count=_int_or_none(result.source_high_water.get('row_count')), first_date=first_date, last_date=last_date, caveats=caveats)

def _date_from_high_water(value: object) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None

def _int_or_none(value: object) -> int | None:
    if value is None:
        return None
    try:
        if isinstance(value, (str, bytes, bytearray)):
            text = value.decode() if isinstance(value, (bytes, bytearray)) else value
            return int(text)
        if isinstance(value, (int, float)):
            return int(value)
        return None
    except (TypeError, ValueError):
        return None

def _polylogue_source(*, include_product_counts: bool) -> SourceReadiness:
    readiness = archive_readiness(include_polylogue_product_counts=include_product_counts)
    if readiness.status == 'ready':
        status: ReadinessStatus = 'available'
    elif readiness.status == 'unavailable':
        status = 'missing'
    else:
        status = 'partial'
    caveats = []
    if readiness.derives_profiles_from_base_tables:
        caveats.append('session-profile products are stale or missing; repair Polylogue session insights')
    if readiness.derives_day_summaries_from_profiles:
        caveats.append('day-summary products are stale or missing; repair Polylogue session insights')
    if readiness.work_event_count == 0:
        caveats.append('work-event products are unavailable; chat semantics are limited')
    return SourceReadiness(source='polylogue', status=status, reason=readiness.reason, cost='materialized', path=str(readiness.db_path), count=readiness.conversation_count, caveats=tuple(caveats))

def _analysis_source(*, include_inventory: bool) -> SourceReadiness:
    cfg = get_config()
    if not cfg.analysis_output_dir.exists():
        return SourceReadiness(source='analysis', status='missing', reason='generated analysis artifact root is missing', cost='materialized', path=str(cfg.analysis_output_dir))
    if not include_inventory:
        return SourceReadiness(source='analysis', status='partial', reason='generated analysis artifact root is present; inventory was not scanned', cost='materialized', path=str(cfg.analysis_output_dir), last_date=_mtime_date(cfg.analysis_output_dir), caveats=('artifact inventory was not scanned',))
    artifacts = artifact_inventory(cfg.analysis_output_dir)
    readable = tuple((item for item in artifacts if item.status == 'available'))
    partial = tuple((item for item in artifacts if item.status != 'available'))
    caveats = tuple((f'{item.name}: {item.reason}' for item in partial if item.reason))
    status: ReadinessStatus = 'available' if readable and (not partial) else 'partial' if readable else 'missing'
    reason = 'generated analysis artifacts are present' if readable else 'generated analysis artifact root contains no readable artifacts'
    latest = max((item.modified_at.date() for item in artifacts), default=None)
    return SourceReadiness(source='analysis', status=status, reason=reason, cost='materialized', path=str(cfg.analysis_output_dir), count=len(readable), last_date=latest, caveats=caveats)

def _machine_source() -> SourceReadiness:
    from ..sources.machine import readiness as machine_readiness
    cfg = get_config()
    if not hasattr(cfg, 'machine_telemetry_db'):
        return SourceReadiness(source='machine', status='missing', reason='machine telemetry path is not configured', cost='materialized', path=None)
    ready = machine_readiness()
    if ready.status == 'ready':
        status: ReadinessStatus = 'available'
        path = ready.live_db
        caveats: tuple[str, ...] = ()
    else:
        status = 'missing'
        path = ready.live_db
        caveats = ()
    return SourceReadiness(source='machine', status=status, reason=ready.reason, cost='materialized', path=str(path), count=ready.live_rows if ready.live_rows else None, last_date=_mtime_date(path), caveats=caveats)

def _xtask_history_source() -> SourceReadiness:
    from sqlite3 import connect as sqlite_connect
    from ..sources.xtask_history import xtask_history_paths
    paths = tuple(((label, path) for label, path in xtask_history_paths() if path.exists()))
    if not paths:
        return SourceReadiness(source='xtask_history', status='missing', reason='xtask invocation ledgers are missing', cost='materialized')
    invocation_count = 0
    stage_count = 0
    test_count = 0
    first_seen: date | None = None
    last_seen: date | None = None
    caveats: list[str] = []
    for label, path in paths:
        try:
            with sqlite_connect(path) as conn:
                inv, first_raw, last_raw = conn.execute('SELECT COUNT(*), MIN(started_at), MAX(started_at) FROM invocations').fetchone()
                invocation_count += int(inv or 0)
                stage_count += _sqlite_count(conn, 'stage_timings')
                test_count += _sqlite_count(conn, 'test_results')
                first_seen = _min_date(first_seen, _iso_date(first_raw))
                last_seen = _max_date(last_seen, _iso_date(last_raw))
        except Exception as exc:
            caveats.append(f'{label}: {exc}')
    status: ReadinessStatus = 'available' if invocation_count else 'partial'
    reason = f'{len(paths)} xtask ledger(s): {invocation_count} invocations, {stage_count} stages, {test_count} test results'
    if invocation_count == 0:
        reason = 'xtask ledgers are present but contain no invocation rows'
    caveats.append('xtask history is observational operator telemetry, not a controlled benchmark')
    return SourceReadiness(source='xtask_history', status=status, reason=reason, cost='materialized', path=':'.join((str(path) for _, path in paths)), count=invocation_count, first_date=first_seen, last_date=last_seen, caveats=tuple(caveats))

def _polylogue_devtools_source() -> SourceReadiness:
    from ..sources.polylogue_devtools import source_readiness
    ready = source_readiness()
    present = ready.xtask_path.exists() or ready.logs_dir.exists()
    count = ready.xtask_rows + ready.meta_files
    if not present:
        return SourceReadiness(source='polylogue_devtools', status='missing', reason='Polylogue devtool JSONL/log ledgers are missing', cost='materialized')
    return SourceReadiness(source='polylogue_devtools', status='available' if count else 'partial', reason=f'{ready.xtask_rows} xtask rows, {ready.meta_files} log meta files', cost='materialized', path=f'{ready.xtask_path}:{ready.logs_dir}', count=count, first_date=ready.first_seen.date() if ready.first_seen else None, last_date=ready.last_seen.date() if ready.last_seen else None, caveats=('repo-local Polylogue development tooling history, not chat archive content', '.local/logs metrics can contribute resource windows for machine attribution'))

def _sinnix_runtime_inventory_source() -> SourceReadiness:
    from ..sources.sinnix_runtime_inventory import readiness as inventory_readiness
    cfg = get_config()
    if not hasattr(cfg, 'sinnix_runtime_inventory_json'):
        return SourceReadiness(source='sinnix_runtime_inventory', status='missing', reason='Sinnix runtime inventory path is not configured', cost='materialized', path=None)
    ready = inventory_readiness(cfg.sinnix_runtime_inventory_json)
    if ready.status == 'ok':
        status: ReadinessStatus = 'available'
    elif ready.status == 'empty':
        status = 'partial'
    elif ready.status == 'error':
        status = 'blocked'
    else:
        status = 'missing'
    return SourceReadiness(source='sinnix_runtime_inventory', status=status, reason=ready.reason or 'Sinnix runtime inventory is readable', cost='materialized', path=str(ready.path), count=ready.row_count or None, last_date=_mtime_date(ready.path))

def _mtime_date(path: Path) -> date | None:
    try:
        if path.exists():
            return datetime.fromtimestamp(path.stat().st_mtime).date()
    except OSError:
        return None
    return None

def _line_count(path: Path) -> int | None:
    try:
        with path.open('r', encoding='utf-8', errors='replace') as handle:
            return sum((1 for _ in handle))
    except OSError:
        return None

def _sqlite_count(conn: Any, table: str) -> int:
    try:
        row = conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()
    except Exception:
        return 0
    return int(row[0] or 0) if row else 0

def _iso_date(value: object) -> date | None:
    if value is None:
        return None
    text = str(value)
    try:
        return datetime.fromisoformat(text.replace('Z', '+00:00')).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None

def _min_date(left: date | None, right: date | None) -> date | None:
    if left is None:
        return right
    if right is None:
        return left
    return min(left, right)

def _max_date(left: date | None, right: date | None) -> date | None:
    if left is None:
        return right
    if right is None:
        return left
    return max(left, right)

def _coverage_window(item: SourceReadiness) -> str:
    if item.first_date and item.last_date:
        return f'{item.first_date.isoformat()} → {item.last_date.isoformat()}'
    if item.last_date:
        return item.last_date.isoformat()
    return ''
__all__ = ['render_source_readiness', 'source_readiness']
