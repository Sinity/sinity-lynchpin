"""Source readiness inventory for current-state and narrative analysis."""
from __future__ import annotations
from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from ..core.config import get_config
from ..core.evidence import CostClass, ReadinessStatus, SourceReadiness, SourceReadinessReport
from ..sources.analysis_artifacts import artifact_inventory
from ..sources.freshness import SourceFreshness, source_freshness
from .coverage import coverage_report
_STALE_AFTER_DAYS = 45

def archive_readiness(*args: Any, **kwargs: Any) -> Any:
    from ..sources.polylogue import archive_readiness as impl
    return impl(*args, **kwargs)

def source_readiness(*, start: date, end: date, include_heavy_counts: bool=False, include_github_frontier: bool=False, include_analysis_inventory: bool=True) -> SourceReadinessReport:
    """Return a compact readiness report for analysis-relevant sources."""
    cfg = get_config()
    available = cfg.available_sources()
    freshness = {item.source: item for item in source_freshness()}
    coverage = coverage_report(start=start, end=end).by_source()
    items: list[SourceReadiness] = [_path_source('activitywatch', available['activitywatch'], cfg.activitywatch_db, 'ActivityWatch focus database is present', 'ActivityWatch focus database is missing', coverage=coverage.get('activitywatch')), _path_source('terminal', available['atuin'], cfg.atuin_db, 'Atuin shell history database is present', 'Atuin shell history database is missing', coverage=coverage.get('terminal')), _path_source('git', cfg.sinnix_root.parent.exists(), cfg.sinnix_root.parent, 'local project git repositories are read live', 'project checkout root is missing'), _polylogue_source(include_heavy_counts=include_heavy_counts), _path_source('raw_log', available['raw_log'], cfg.raw_log_file, 'knowledgebase raw-log is present', 'knowledgebase raw-log is missing', count_fn=_line_count), _path_source('browser', available['webhistory'], cfg.webhistory_ndjson, 'canonical browser history NDJSON is present', 'canonical browser history NDJSON is missing', freshness=freshness.get('webhistory'), coverage=coverage.get('webhistory')), _path_source('sleep', available['sleep'], cfg.sleep_jsonl, 'sleep processed export is present', 'sleep processed export is missing', freshness=freshness.get('sleep'), coverage=coverage.get('sleep')), _path_source('health', cfg.samsung_gdpr_cloud_dir.exists(), cfg.samsung_gdpr_cloud_dir, 'Samsung health export root is present', 'Samsung health export root is missing', coverage=coverage.get('health')), _path_source('spotify', available['spotify'], cfg.spotify_root, 'Spotify processed export is present', 'Spotify processed export is missing', freshness=freshness.get('spotify'), coverage=coverage.get('spotify')), _path_source('reddit', available['reddit'], cfg.reddit_export_dir, 'Reddit processed export is present', 'Reddit processed export is missing', freshness=freshness.get('reddit'), coverage=coverage.get('reddit')), _path_source('messenger', available['fbmessenger'], cfg.fbmessenger_gdpr_root if cfg.fbmessenger_gdpr_root.exists() else cfg.fbmessenger_db, 'Facebook Messenger export/database is present', 'Facebook Messenger export/database is missing', freshness=freshness.get('fbmessenger'), coverage=coverage.get('messenger')), _path_source('raindrop', available['raindrop'], cfg.raindrop_csv, 'Raindrop CSV export is present', 'Raindrop CSV export is missing', freshness=freshness.get('raindrop'), coverage=coverage.get('raindrop')), _path_source('substance', (cfg.exports_root / 'health/processed/substance_log_unified.csv').exists(), cfg.exports_root / 'health/processed/substance_log_unified.csv', 'substance log is present', 'substance log is missing', coverage=coverage.get('substance')), _machine_source(), *_historical_dataset_sources(), _analysis_source(include_inventory=include_analysis_inventory), SourceReadiness(source='github', status='available' if include_github_frontier else 'partial', reason='GitHub frontier fetch is enabled for this analysis' if include_github_frontier else 'GitHub is optional network evidence; use --github-frontier to fetch/cache it', cost='network', path=None, caveats=() if include_github_frontier else ('disabled outside network mode',))]
    return SourceReadinessReport(start=start, end=end, generated_at=datetime.now(timezone.utc), sources=tuple(sorted(items, key=lambda item: item.source)))

def render_source_readiness(report: SourceReadinessReport) -> str:
    """Render source readiness in a compact Markdown table."""
    lines = ['| Source | Status | Cost | Count | Freshness | Reason |', '|---|---:|---:|---:|---|---|']
    for item in report.sources:
        freshness = _freshness(item)
        count = '' if item.count is None else str(item.count)
        reason = item.reason.replace('|', '\\|')
        if item.caveats:
            reason = reason + '<br>' + '<br>'.join((c.replace('|', '\\|') for c in item.caveats))
        lines.append(f'| {item.source} | {item.status} | {item.cost} | {count} | {freshness} | {reason} |')
    return '\n'.join(lines)

def _path_source(source: str, exists: bool, path: Path | str | None, ok_reason: str, missing_reason: str, *, cost: CostClass='local-fast', count_fn: Callable[[Path], int | None] | None=None, freshness: SourceFreshness | None=None, coverage: object | None=None) -> SourceReadiness:
    resolved = Path(path) if path is not None else None
    if not exists or resolved is None:
        return SourceReadiness(source=source, status='missing', reason=missing_reason, cost=cost, path=str(resolved) if resolved else None)
    count = count_fn(resolved) if count_fn is not None else None
    last_date = freshness.last_observed if freshness is not None else _mtime_date(resolved)
    first_date = None
    status: ReadinessStatus = 'available'
    caveats: tuple[str, ...] = ()
    if coverage is not None:
        first_date = getattr(coverage, 'first_date', None)
        last_date = getattr(coverage, 'last_date', last_date)
        count = count if count is not None else getattr(coverage, 'row_count', None)
        coverage_status = getattr(coverage, 'status', None)
        if coverage_status in {'partial', 'blocked', 'missing'}:
            status = 'partial' if coverage_status == 'partial' else 'blocked' if coverage_status == 'blocked' else 'missing'
            hint = getattr(coverage, 'repair_hint', None)
            reason = getattr(coverage, 'reason', '')
            caveats = tuple((item for item in (reason, hint) if item))
        elif coverage_status == 'available':
            status = 'available'
    if last_date is not None:
        age = (date.today() - last_date).days
        if status in {'blocked', 'missing', 'partial'}:
            pass
        elif freshness is not None and freshness.stale:
            status = 'stale'
            caveats = (f'latest observed data is {age} days old ({freshness.basis})',)
        elif freshness is None and age > _STALE_AFTER_DAYS:
            status = 'stale'
            caveats = (f'latest filesystem update is {age} days old',)
    return SourceReadiness(source=source, status=status, reason=ok_reason, cost=cost, path=str(resolved), count=count, first_date=first_date, last_date=last_date, caveats=caveats)

def _polylogue_source(*, include_heavy_counts: bool) -> SourceReadiness:
    readiness = archive_readiness(include_heavy_counts=include_heavy_counts)
    if readiness.status == 'ready':
        status: ReadinessStatus = 'available'
    elif readiness.status == 'unavailable':
        status = 'missing'
    else:
        status = 'partial'
    caveats = []
    if readiness.derives_profiles_from_base_tables:
        caveats.append('session profiles are derived from base archive tables')
    if readiness.derives_day_summaries_from_profiles:
        caveats.append('day summaries are derived rather than materialized')
    if readiness.work_event_count == 0:
        caveats.append('work-event products are unavailable; chat semantics are limited')
    return SourceReadiness(source='polylogue', status=status, reason=readiness.reason, cost='local-heavy' if include_heavy_counts else 'local-fast', path=str(readiness.db_path), count=readiness.conversation_count, caveats=tuple(caveats))

def _analysis_source(*, include_inventory: bool) -> SourceReadiness:
    cfg = get_config()
    if not cfg.analysis_output_dir.exists():
        return SourceReadiness(source='analysis', status='missing', reason='generated analysis artifact root is missing', cost='local-fast', path=str(cfg.analysis_output_dir))
    if not include_inventory:
        return SourceReadiness(source='analysis', status='partial', reason='generated analysis artifact root is present; inventory was not scanned', cost='local-fast', path=str(cfg.analysis_output_dir), last_date=_mtime_date(cfg.analysis_output_dir), caveats=('artifact inventory skipped in local-fast mode',))
    artifacts = artifact_inventory(cfg.analysis_output_dir)
    readable = tuple((item for item in artifacts if item.status == 'available'))
    partial = tuple((item for item in artifacts if item.status != 'available'))
    caveats = tuple((f'{item.name}: {item.reason}' for item in partial if item.reason))
    status: ReadinessStatus = 'available' if readable and (not partial) else 'partial' if readable else 'missing'
    reason = 'generated analysis artifacts are present' if readable else 'generated analysis artifact root contains no readable artifacts'
    latest = max((item.modified_at.date() for item in artifacts), default=None)
    return SourceReadiness(source='analysis', status=status, reason=reason, cost='local-fast', path=str(cfg.analysis_output_dir), count=len(readable), last_date=latest, caveats=caveats)

def _machine_source() -> SourceReadiness:
    from ..sources.machine import readiness as machine_readiness
    cfg = get_config()
    if not hasattr(cfg, 'machine_telemetry_db'):
        return SourceReadiness(source='machine', status='missing', reason='machine telemetry path is not configured', cost='local-fast', path=None)
    ready = machine_readiness()
    if ready.status == 'ready':
        status: ReadinessStatus = 'available'
        path = ready.live_db
        caveats: tuple[str, ...] = ()
    else:
        status = 'missing'
        path = ready.live_db
        caveats = ()
    return SourceReadiness(source='machine', status=status, reason=ready.reason, cost='local-fast', path=str(path), count=ready.live_rows if ready.live_rows else None, last_date=_mtime_date(path), caveats=caveats)

def _historical_dataset_sources() -> tuple[SourceReadiness, ...]:
    from ..sources.historical_datasets import source_summary
    cfg = get_config()
    required = ('browser_bookmarks_root', 'arbtt_root', 'teams_root', 'tortoisesvn_root', 'software_inventory_root', 'calibre_root', 'onedrive_inventory_root', 'singlefile_root')
    if not all((hasattr(cfg, attr) for attr in required)):
        return ()
    summaries = tuple((item for item in source_summary(cfg) if item.count > 0))
    return tuple((SourceReadiness(source=item.source, status='available', reason='historical dataset is present in its canonical /realm/data home', cost='local-fast', path=item.path, count=item.count, first_date=item.first_date, last_date=item.last_date) for item in summaries))

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

def _freshness(item: SourceReadiness) -> str:
    if item.first_date and item.last_date:
        return f'{item.first_date.isoformat()} → {item.last_date.isoformat()}'
    if item.last_date:
        return item.last_date.isoformat()
    return ''
__all__ = ['render_source_readiness', 'source_readiness']
