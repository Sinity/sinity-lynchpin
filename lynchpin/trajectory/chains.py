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
            current.truncate(signal.start)
        chains.append(current.finalize())
        current = _ChainAccumulator(signal)

    if current is not None:
        chains.append(current.finalize())
    return chains


class _ChainAccumulator:
    def __init__(self, first: AttributedSignal) -> None:
        self.signals: list[AttributedSignal] = [first]
        self.start = first.start
        self.end = first.end
        self.mode_counter: Counter[str] = Counter()
        self.project_counter: Counter[str] = Counter()
        self.topic_counter: Counter[str] = Counter()
        self.mode_conf_weight = 0.0
        self.project_conf_weight = 0.0
        self.thread_ids: set[str] = set()
        self.add(first)

    def add(self, signal: AttributedSignal) -> None:
        if len(self.signals) == 1 and self.signals[0] is signal:
            pass
        elif signal not in self.signals:
            self.signals.append(signal)
        self.start = min(self.start, signal.start)
        self.end = max(self.end, signal.end)
        weight = max(signal.duration_seconds, 1.0)
        self.mode_counter[signal.mode] += weight
        if signal.project:
            self.project_counter[signal.project] += weight
            self.project_conf_weight += weight * signal.project_confidence
        self.mode_conf_weight += weight * signal.mode_confidence
        if signal.topic:
            self.topic_counter[signal.topic] += weight
        tid = signal.evidence.get("thread_id") if isinstance(signal.evidence, dict) else None
        if tid:
            self.thread_ids.add(str(tid))

    @property
    def mode(self) -> str:
        return _dominant_label(self.mode_counter, fallback="unknown")

    @property
    def project(self) -> Optional[str]:
        if not self.project_counter:
            return None
        return _dominant_label(self.project_counter, fallback=None)

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
        source_set = tuple(sorted({signal.source for signal in self.signals}))
        app_set = tuple(sorted({signal.app for signal in self.signals if signal.app}))
        domain_set = tuple(sorted({signal.domain for signal in self.signals if signal.domain}))
        title_set = tuple(sorted({signal.title for signal in self.signals if signal.title})[:5])
        reasons = tuple(dict.fromkeys(reason for signal in self.signals for reason in signal.reasons))
        chain_id = _chain_id(self.start, self.end, self.mode, self.project, self.signals)
        signal_count = len(self.signals)
        source_count = len(source_set)
        total_weight = sum(max(signal.duration_seconds, 1.0) for signal in self.signals)
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
    return sorted(counter.items(), key=lambda item: (-item[1], item[0]))[0][0]


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
