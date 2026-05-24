"""Source-node builders for the range-scoped evidence graph."""
from __future__ import annotations
import logging
from datetime import date
from ..core.evidence import CostClass
from ..core.evidence_graph import EvidenceEdge, EvidenceNode
from . import evidence_system_signals
from . import evidence_git
from . import evidence_polylogue
from . import evidence_activitywatch
from . import evidence_web_media
from . import evidence_terminal
from . import evidence_raw_log
from . import evidence_personal_products
log = logging.getLogger(__name__)

def add_base_source_nodes(nodes: list[EvidenceNode], edges: list[EvidenceEdge], *, start: date, end: date, selected: set[str], mode: CostClass, include_spotify: bool) -> None:
    log.info('evidence_sources: git')
    evidence_git.add_git(nodes, edges, start=start, end=end, selected=selected, mode=mode)
    log.info('evidence_sources: git complete nodes=%d edges=%d', len(nodes), len(edges))
    log.info('evidence_sources: polylogue sessions')
    evidence_polylogue.add_polylogue(nodes, start=start, end=end, selected=selected)
    log.info('evidence_sources: polylogue sessions complete nodes=%d', len(nodes))
    log.info('evidence_sources: polylogue work events')
    evidence_polylogue.add_polylogue_work_events(nodes, start=start, end=end, selected=selected, mode='materialized')
    log.info('evidence_sources: polylogue work events complete nodes=%d', len(nodes))
    log.info('evidence_sources: raw log')
    evidence_raw_log.add_raw_log(nodes, start=start, end=end, selected=selected)
    log.info('evidence_sources: raw log complete nodes=%d', len(nodes))
    log.info('evidence_sources: activitywatch')
    evidence_activitywatch.add_focus(nodes, start=start, end=end, selected=selected, mode=mode)
    log.info('evidence_sources: activitywatch complete nodes=%d', len(nodes))
    log.info('evidence_sources: terminal')
    evidence_terminal.add_terminal(nodes, start=start, end=end, selected=selected)
    log.info('evidence_sources: terminal complete nodes=%d', len(nodes))
    log.info('evidence_sources: web')
    evidence_web_media.add_web(nodes, start=start, end=end, selected=selected)
    log.info('evidence_sources: web complete nodes=%d', len(nodes))
    log.info('evidence_sources: personal products')
    evidence_personal_products.add_personal_products(nodes, start=start, end=end)
    log.info('evidence_sources: personal products complete nodes=%d', len(nodes))
    if include_spotify:
        log.info('evidence_sources: spotify')
        evidence_web_media.add_spotify(nodes, start=start, end=end, selected=selected)
        log.info('evidence_sources: spotify complete nodes=%d', len(nodes))
    log.info('evidence_sources: health')
    evidence_system_signals.add_health(nodes, start=start, end=end)
    log.info('evidence_sources: health complete nodes=%d', len(nodes))
    log.info('evidence_sources: temporal signals')
    evidence_system_signals.add_temporal_signals(nodes, start=start, end=end)
    log.info('evidence_sources: temporal signals complete nodes=%d', len(nodes))
    log.info('evidence_sources: readiness')
    evidence_system_signals.add_readiness(nodes, end=end)
    log.info('evidence_sources: readiness complete nodes=%d', len(nodes))
__all__ = ['add_base_source_nodes']
