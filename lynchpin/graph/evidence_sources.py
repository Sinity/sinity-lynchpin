"""Source-node builders for the range-scoped evidence graph."""
from __future__ import annotations
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

def add_base_source_nodes(nodes: list[EvidenceNode], edges: list[EvidenceEdge], *, start: date, end: date, selected: set[str], mode: CostClass, include_spotify: bool) -> None:
    evidence_git.add_git(nodes, edges, start=start, end=end, selected=selected, mode=mode)
    evidence_polylogue.add_polylogue(nodes, start=start, end=end, selected=selected)
    if mode != 'local-fast':
        evidence_polylogue.add_polylogue_work_events(nodes, start=start, end=end, selected=selected, mode=mode)
    evidence_raw_log.add_raw_log(nodes, start=start, end=end, selected=selected)
    evidence_activitywatch.add_focus(nodes, start=start, end=end, selected=selected, mode=mode)
    evidence_terminal.add_terminal(nodes, start=start, end=end, selected=selected)
    evidence_web_media.add_web(nodes, start=start, end=end, selected=selected)
    if include_spotify and mode != 'local-fast':
        evidence_web_media.add_spotify(nodes, start=start, end=end, selected=selected)
    evidence_system_signals.add_health(nodes, start=start, end=end)
    if mode != 'local-fast':
        evidence_system_signals.add_temporal_signals(nodes, start=start, end=end)
        evidence_system_signals.add_readiness(nodes, end=end)
__all__ = ['add_base_source_nodes']
