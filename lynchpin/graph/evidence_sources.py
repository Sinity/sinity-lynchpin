"""Source-node builders for the range-scoped evidence graph."""
from __future__ import annotations
import logging
from datetime import date
from typing import Callable
from ..core.evidence import CostClass
from ..core.evidence import EvidenceCaveat
from ..core.evidence_graph import EvidenceEdge, EvidenceNode
from . import evidence_system_signals
from . import evidence_git
from . import evidence_polylogue
from . import evidence_activitywatch
from . import evidence_web_media
from . import evidence_terminal
from . import evidence_raw_log
from . import evidence_clipboard
from . import evidence_irc
from . import evidence_personal_products
log = logging.getLogger(__name__)

def add_base_source_nodes(nodes: list[EvidenceNode], edges: list[EvidenceEdge], *, start: date, end: date, selected: set[str], mode: CostClass, include_spotify: bool) -> tuple[EvidenceCaveat, ...]:
    caveats: list[EvidenceCaveat] = []
    _run_source('git', caveats, lambda: evidence_git.add_git(nodes, edges, start=start, end=end, selected=selected, mode=mode), node_count=lambda: len(nodes))
    _run_source('polylogue sessions', caveats, lambda: evidence_polylogue.add_polylogue(nodes, start=start, end=end, selected=selected), caveat_source='polylogue', node_count=lambda: len(nodes))
    _run_source('polylogue work events', caveats, lambda: evidence_polylogue.add_polylogue_work_events(nodes, start=start, end=end, selected=selected), caveat_source='polylogue', node_count=lambda: len(nodes))
    _run_source('raw log', caveats, lambda: evidence_raw_log.add_raw_log(nodes, start=start, end=end, selected=selected), node_count=lambda: len(nodes))
    _run_source('clipboard', caveats, lambda: evidence_clipboard.add_clipboard(nodes, start=start, end=end, selected=selected), node_count=lambda: len(nodes))
    _run_source('irc', caveats, lambda: evidence_irc.add_irc(nodes, start=start, end=end, selected=selected), node_count=lambda: len(nodes))
    _run_source('activitywatch', caveats, lambda: evidence_activitywatch.add_focus(nodes, start=start, end=end, selected=selected), node_count=lambda: len(nodes))
    _run_source('terminal', caveats, lambda: evidence_terminal.add_terminal(nodes, start=start, end=end, selected=selected), node_count=lambda: len(nodes))
    _run_source('web', caveats, lambda: evidence_web_media.add_web(nodes, start=start, end=end, selected=selected), node_count=lambda: len(nodes))
    _run_source('personal daily signals', caveats, lambda: evidence_personal_products.add_personal_daily_signals(nodes, start=start, end=end), node_count=lambda: len(nodes))
    _run_source('personal products', caveats, lambda: evidence_personal_products.add_personal_products(nodes, start=start, end=end), node_count=lambda: len(nodes))
    if include_spotify:
        _run_source('spotify', caveats, lambda: evidence_web_media.add_spotify(nodes, start=start, end=end, selected=selected), node_count=lambda: len(nodes))
    _run_source('health', caveats, lambda: evidence_system_signals.add_health(nodes, start=start, end=end), node_count=lambda: len(nodes))
    _run_source('temporal signals', caveats, lambda: evidence_system_signals.add_temporal_signals(nodes, start=start, end=end), node_count=lambda: len(nodes))
    _run_source('readiness', caveats, lambda: evidence_system_signals.add_readiness(nodes, end=end), node_count=lambda: len(nodes))
    return tuple(caveats)

def _run_source(label: str, caveats: list[EvidenceCaveat], build: Callable[[], None], *, caveat_source: str | None=None, node_count: Callable[[], int]) -> None:
    """Add one source without letting it abort the whole evidence graph."""
    before = node_count()
    log.info('evidence_sources: %s', label)
    try:
        build()
    except Exception as exc:
        log.warning('evidence_sources: %s blocked: %s', label, exc, exc_info=True)
        caveats.append(EvidenceCaveat(caveat_source or label.replace(' ', '_'), 'blocked', f'{label} evidence source failed during graph build: {exc}'))
        return
    log.info('evidence_sources: %s complete nodes=%d (+%d)', label, node_count(), node_count() - before)
__all__ = ['add_base_source_nodes']
