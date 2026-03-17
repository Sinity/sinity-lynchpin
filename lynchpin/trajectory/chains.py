from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Optional

from .rules import AttributedSignal, classify_chain_topics, classify_signals, mode_family
from .signal import TrajectorySignal

_CHAIN_GAP = timedelta(minutes=5)
_RECOVERY_GAP = timedelta(minutes=1)


@dataclass(frozen=True)
class TrajectoryChain:
    chain_id: str
    start: datetime
    end: datetime
    mode: str
    project: Optional[str]
    mode_confidence: float
    project_confidence: float
    signal_count: int
    source_count: int
    sources: tuple[str, ...]
    apps: tuple[str, ...]
    domains: tuple[str, ...]
    titles: tuple[str, ...]
    reasons: tuple[str, ...]
    signals: tuple[AttributedSignal, ...]
    topic: Optional[str] = None
    topic_confidence: float = 0.0
    topic_seconds: tuple[tuple[str, float], ...] = ()
    quality_flags: tuple[str, ...] = ()
    thread_ids: frozenset[str] = frozenset()

    @property
    def duration_seconds(self) -> float:
        return max((self.end - self.start).total_seconds(), 0.0)

    def to_dict(self) -> dict[str, object]:
        return {
            "chain_id": self.chain_id,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "duration_seconds": round(self.duration_seconds, 3),
            "mode": self.mode,
            "project": self.project,
            "mode_confidence": self.mode_confidence,
            "project_confidence": self.project_confidence,
            "signal_count": self.signal_count,
            "source_count": self.source_count,
            "sources": list(self.sources),
            "apps": list(self.apps),
            "domains": list(self.domains),
            "titles": list(self.titles),
            "reasons": list(self.reasons),
            "signals": [signal.to_dict() for signal in self.signals],
            "topic": self.topic,
            "topic_confidence": self.topic_confidence,
            "topic_seconds": [[t, s] for t, s in self.topic_seconds],
            "quality_flags": list(self.quality_flags),
        }


def build_chains(signals: Iterable[TrajectorySignal]) -> list[TrajectoryChain]:
    return build_chains_from_attributed(classify_signals(signals))


def build_chains_from_attributed(signals: Iterable[AttributedSignal]) -> list[TrajectoryChain]:
    ordered = sorted(signals, key=lambda signal: (signal.start, signal.end, signal.signal_id))
    chains: list[TrajectoryChain] = []
    current: Optional[_ChainAccumulator] = None

    for signal in ordered:
        if current is None:
            current = _ChainAccumulator(signal)
            continue
        if current.can_accept(signal):
            current.add(signal)
            continue
        if current.start < signal.start < current.end:
            # Recovery (AFK) chains are authoritative inactivity markers.
            # Other signals falling inside them are background noise; skip
            # them rather than truncating the AFK chain.
            if current.mode == "recovery":
                continue
            current.truncate(signal.start)
        chains.append(current.finalize())
        current = _ChainAccumulator(signal)

    if current is not None:
        chains.append(current.finalize())
    return chains


class _ChainAccumulator:
    def __init__(self, first: AttributedSignal) -> None:
        self.signals: list[AttributedSignal] = [first]
        self._signal_ids: set[str] = {first.signal_id}
        self.start = first.start
        self.end = first.end
        self.mode_counter: Counter[str] = Counter()
        self.project_counter: Counter[str] = Counter()
        self.topic_counter: Counter[str] = Counter()
        self.mode_conf_weight = 0.0
        self.project_conf_weight = 0.0
        self.thread_ids: set[str] = set()
        # Incrementally tracked dominants — avoids calling _dominant_label on every can_accept()
        self._dominant_mode: str = "unknown"
        self._dominant_project: Optional[str] = None
        # Incrementally tracked sets — avoids re-iterating self.signals in finalize()
        self._sources: set[str] = set()
        self._apps: set[str] = set()
        self._domains: set[str] = set()
        self._total_weight: float = 0.0
        self.add(first)

    def add(self, signal: AttributedSignal) -> None:
        sid = signal.signal_id
        if sid not in self._signal_ids:
            self.signals.append(signal)
            self._signal_ids.add(sid)
        self.start = min(self.start, signal.start)
        self.end = max(self.end, signal.end)
        weight = max(signal.duration_seconds, 1.0)
        self.mode_counter[signal.mode] += weight
        # Incremental dominant update: check only if the updated mode can overtake current dominant
        new_mw = self.mode_counter[signal.mode]
        dom_mw = self.mode_counter.get(self._dominant_mode, 0)
        if new_mw > dom_mw or (new_mw == dom_mw and signal.mode < self._dominant_mode):
            self._dominant_mode = signal.mode
        if signal.project:
            self.project_counter[signal.project] += weight
            self.project_conf_weight += weight * signal.project_confidence
            new_pw = self.project_counter[signal.project]
            dom_pw = self.project_counter.get(self._dominant_project or "", 0)
            if new_pw > dom_pw or (new_pw == dom_pw and (self._dominant_project is None or signal.project < self._dominant_project)):
                self._dominant_project = signal.project
        self.mode_conf_weight += weight * signal.mode_confidence
        self._total_weight += weight
        self._sources.add(signal.source)
        if signal.app:
            self._apps.add(signal.app)
        if signal.domain:
            self._domains.add(signal.domain)
        if signal.topic:
            self.topic_counter[signal.topic] += weight
        tid = signal.evidence.get("thread_id") if isinstance(signal.evidence, dict) else None
        if tid:
            self.thread_ids.add(str(tid))

    @property
    def mode(self) -> str:
        return self._dominant_mode

    @property
    def project(self) -> Optional[str]:
        return self._dominant_project

    def can_accept(self, signal: AttributedSignal) -> bool:
        gap = signal.start - self.end
        allowed_gap = _RECOVERY_GAP if "recovery" in {self.mode, signal.mode} else _CHAIN_GAP
        if gap > allowed_gap:
            return False
        if mode_family(signal.mode) != mode_family(self.mode):
            return False
        project = self.project
        if project and signal.project and project != signal.project:
            return False
        return True

    def finalize(self) -> TrajectoryChain:
        source_set = tuple(sorted(self._sources))
        app_set = tuple(sorted(self._apps))
        domain_set = tuple(sorted(self._domains))
        title_set = tuple(sorted({signal.title for signal in self.signals if signal.title})[:5])
        reasons = tuple(dict.fromkeys(reason for signal in self.signals for reason in signal.reasons))
        chain_id = _chain_id(self.start, self.end, self.mode, self.project, self.signals)
        signal_count = len(self.signals)
        source_count = len(self._sources)
        total_weight = self._total_weight
        mode_confidence = round(self.mode_conf_weight / total_weight if total_weight else 0.0, 3)
        project_confidence = 0.0
        if self.project:
            project_weight = self.project_counter[self.project]
            project_confidence = round(self.project_conf_weight / project_weight if project_weight else 0.0, 3)
        topic, topic_confidence, ranked_topics = classify_chain_topics(self.signals)
        topic_seconds = tuple(ranked_topics)
        duration_seconds = max((self.end - self.start).total_seconds(), 0.0)
        quality_flags: list[str] = []
        if source_count <= 1:
            quality_flags.append("single_source")
        if mode_confidence < 0.5:
            quality_flags.append("low_confidence")
        if duration_seconds > 0:
            signal_coverage = total_weight / duration_seconds
            if signal_coverage < 0.5:
                quality_flags.append("gap_heavy")
        if duration_seconds < 60:
            quality_flags.append("short")
        return TrajectoryChain(
            chain_id=chain_id,
            start=self.start,
            end=self.end,
            mode=self.mode,
            project=self.project,
            mode_confidence=mode_confidence,
            project_confidence=project_confidence,
            signal_count=signal_count,
            source_count=source_count,
            sources=source_set,
            apps=app_set,
            domains=domain_set,
            titles=title_set,
            reasons=reasons,
            signals=tuple(sorted(self.signals, key=lambda signal: (signal.start, signal.signal_id))),
            topic=topic,
            topic_confidence=topic_confidence,
            topic_seconds=topic_seconds,
            quality_flags=tuple(quality_flags),
            thread_ids=frozenset(self.thread_ids),
        )

    def truncate(self, new_end: datetime) -> None:
        if new_end <= self.start:
            self.end = self.start
            return
        self.end = min(self.end, new_end)


def _dominant_label(counter: Counter[str], fallback):
    if not counter:
        return fallback
    # O(n) via max+min; avoids sort allocation for 2-5 item counters (called ~130k times)
    max_weight = max(counter.values())
    return min(k for k, v in counter.items() if v == max_weight)


def _chain_id(
    start: datetime,
    end: datetime,
    mode: str,
    project: Optional[str],
    signals: Iterable[AttributedSignal],
) -> str:
    payload = "|".join(
        [
            start.isoformat(),
            end.isoformat(),
            mode,
            project or "",
            *[signal.signal_id for signal in signals],
        ]
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
