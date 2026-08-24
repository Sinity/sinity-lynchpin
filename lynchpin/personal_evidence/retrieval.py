"""Coverage-aware retrieval over normalized private content units.

This module is deliberately index-agnostic.  A source adapter supplies content
units, index coverage, and any FTS or embedding scores it can read.  Retrieval
does not mutate an index, infer facts from text, or promote generated text to
biographical evidence.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
import re

from lynchpin.personal_evidence.models import AuthorshipClass


class RetrievalMode(StrEnum):
    """Closed set of retrieval and audit sampling routes."""

    LEXICAL = "lexical"
    SEMANTIC = "semantic"
    RANDOM = "deterministic_random"
    LOW_SCORE_RANDOM = "low_score_deterministic_random"


class CoverageCompleteness(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"


class IndexFreshness(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, order=True)
class ProviderYear:
    provider: str
    year: int

    def __post_init__(self) -> None:
        if not self.provider:
            raise ValueError("provider must be non-empty")
        if self.year < 1:
            raise ValueError("year must be positive")


@dataclass(frozen=True)
class ContentUnit:
    """One normalized, addressable content unit from a private source."""

    content_unit_id: str
    provider: str
    occurred_at: datetime
    text: str
    content_hash: str
    conversation_id: str | None
    branch_id: str | None
    lineage_id: str | None
    parent_unit_id: str | None
    role: str
    authorship: AuthorshipClass
    source_locator: str
    sequence: int = 0
    autobiographical_score: float | None = None

    def __post_init__(self) -> None:
        for field_name in ("content_unit_id", "provider", "content_hash", "role", "source_locator"):
            if not getattr(self, field_name):
                raise ValueError(f"{field_name} must be non-empty")
        if self.autobiographical_score is not None and not 0 <= self.autobiographical_score <= 1:
            raise ValueError("autobiographical_score must be between zero and one")

    @property
    def stratum(self) -> ProviderYear:
        return ProviderYear(self.provider, self.occurred_at.year)

    @property
    def is_biographical_evidence_candidate(self) -> bool:
        """Generated prose is retrievable provenance, never biography by itself."""

        return self.authorship not in {
            AuthorshipClass.MODEL_GENERATED,
            AuthorshipClass.AGENT_GENERATED,
        }


@dataclass(frozen=True)
class IndexCoverage:
    freshness: IndexFreshness
    indexed_denominator: int
    version: str | None = None
    model_version: str | None = None

    def __post_init__(self) -> None:
        if self.indexed_denominator < 0:
            raise ValueError("indexed_denominator must be non-negative")


@dataclass(frozen=True)
class StratumCoverage:
    """Coverage declared by the owning source adapter for one provider/year."""

    source_denominator: int
    completeness: CoverageCompleteness
    lexical: IndexCoverage
    semantic: IndexCoverage
    known_gaps: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.source_denominator < 0:
            raise ValueError("source_denominator must be non-negative")
        if self.lexical.indexed_denominator > self.source_denominator:
            raise ValueError("lexical index exceeds source denominator")
        if self.semantic.indexed_denominator > self.source_denominator:
            raise ValueError("semantic index exceeds source denominator")


@dataclass(frozen=True)
class StratumPlan:
    stratum: ProviderYear
    unit_count: int
    coverage: StratumCoverage


@dataclass(frozen=True)
class RetrievalRequest:
    mode: RetrievalMode
    limit_per_stratum: int
    query: str | None = None
    seed: str | None = None
    context_before: int = 1
    context_after: int = 1
    low_score_maximum: float | None = None
    strata: tuple[ProviderYear, ...] = ()

    def __post_init__(self) -> None:
        if self.limit_per_stratum < 1:
            raise ValueError("limit_per_stratum must be positive")
        if self.context_before < 0 or self.context_after < 0:
            raise ValueError("context sizes must be non-negative")
        if self.mode in {RetrievalMode.LEXICAL, RetrievalMode.SEMANTIC} and not self.query:
            raise ValueError(f"{self.mode.value} retrieval requires a query")
        if self.mode in {RetrievalMode.RANDOM, RetrievalMode.LOW_SCORE_RANDOM} and not self.seed:
            raise ValueError(f"{self.mode.value} retrieval requires a seed")
        if self.mode is RetrievalMode.LOW_SCORE_RANDOM and self.low_score_maximum is None:
            raise ValueError("low-score sampling requires low_score_maximum")
        if self.low_score_maximum is not None and not 0 <= self.low_score_maximum <= 1:
            raise ValueError("low_score_maximum must be between zero and one")

    @property
    def query_fingerprint(self) -> str | None:
        if self.query is None:
            return None
        return sha256(self.query.encode()).hexdigest()


@dataclass(frozen=True)
class RetrievalScores:
    """Read-only scores supplied by FTS and embedding adapters.

    Similarity scores rank candidates only.  They are intentionally separate
    from content units and from any claim or evidence representation.
    """

    fts: Mapping[str, float] = field(default_factory=dict)
    semantic: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievalReceipt:
    mode: RetrievalMode
    stratum: ProviderYear
    query_fingerprint: str | None
    seed_fingerprint: str | None
    source_denominator: int
    indexed_denominator: int
    missing_coverage: int
    coverage_completeness: CoverageCompleteness
    index_freshness: IndexFreshness
    index_version: str | None
    model_version: str | None
    candidate_count: int
    result_count: int
    strategy: str
    similarity_is_retrieval_evidence_only: bool
    known_gaps: tuple[str, ...]


@dataclass(frozen=True)
class ContextWindow:
    """Neighboring units from the same conversation, preserving lineage."""

    center: ContentUnit
    before: tuple[ContentUnit, ...]
    after: tuple[ContentUnit, ...]


@dataclass(frozen=True)
class DuplicateOccurrence:
    """One repeated copy, retained as provenance rather than corroboration."""

    content_unit_id: str
    provider: str
    conversation_id: str | None
    branch_id: str | None
    lineage_id: str | None
    source_locator: str


@dataclass(frozen=True)
class RetrievalHit:
    unit: ContentUnit
    score: float | None
    context: ContextWindow
    duplicate_occurrences: tuple[DuplicateOccurrence, ...]

    @property
    def duplicate_unit_ids(self) -> tuple[str, ...]:
        return tuple(copy.content_unit_id for copy in self.duplicate_occurrences)

    @property
    def duplicate_locators(self) -> tuple[str, ...]:
        return tuple(copy.source_locator for copy in self.duplicate_occurrences)

    @property
    def is_biographical_evidence_candidate(self) -> bool:
        return self.unit.is_biographical_evidence_candidate


@dataclass(frozen=True)
class RetrievalBatch:
    hits: tuple[RetrievalHit, ...]
    receipts: tuple[RetrievalReceipt, ...]


@dataclass(frozen=True)
class MarginalYield:
    round_number: int
    query_fingerprint: str | None
    retrieved_unique_count: int
    novel_unique_count: int
    novel_yield: float
    coverage_documented: bool
    diminishing_returns: bool
    may_stop_expansion: bool


class QueryExpansionLedger:
    """Records novelty over query rounds without inspecting or storing prose."""

    def __init__(self, diminishing_yield_at_most: float) -> None:
        if not 0 <= diminishing_yield_at_most <= 1:
            raise ValueError("diminishing_yield_at_most must be between zero and one")
        self._threshold = diminishing_yield_at_most
        self._seen_hashes: set[str] = set()
        self._rounds: list[MarginalYield] = []

    @property
    def rounds(self) -> tuple[MarginalYield, ...]:
        return tuple(self._rounds)

    def record(self, batch: RetrievalBatch) -> MarginalYield:
        hashes = {hit.unit.content_hash for hit in batch.hits}
        novel = hashes - self._seen_hashes
        total = len(hashes)
        yield_value = len(novel) / total if total else 0.0
        coverage_documented = all(
            receipt.coverage_completeness is not CoverageCompleteness.UNKNOWN
            for receipt in batch.receipts
        )
        diminishing = yield_value <= self._threshold
        result = MarginalYield(
            round_number=len(self._rounds) + 1,
            query_fingerprint=_batch_query_fingerprint(batch),
            retrieved_unique_count=total,
            novel_unique_count=len(novel),
            novel_yield=yield_value,
            coverage_documented=coverage_documented,
            diminishing_returns=diminishing,
            may_stop_expansion=diminishing and coverage_documented,
        )
        self._seen_hashes.update(hashes)
        self._rounds.append(result)
        return result


class RetrievalEngine:
    """Execute coverage-aware retrieval over caller-provided normalized units."""

    def __init__(
        self,
        units: Iterable[ContentUnit],
        coverage_by_stratum: Mapping[ProviderYear, StratumCoverage],
    ) -> None:
        self._units = tuple(units)
        identifiers = [unit.content_unit_id for unit in self._units]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("content_unit_id values must be unique")
        self._coverage = dict(coverage_by_stratum)
        grouped: dict[ProviderYear, list[ContentUnit]] = defaultdict(list)
        for unit in self._units:
            grouped[unit.stratum].append(unit)
        self._units_by_stratum = {
            stratum: tuple(sorted(group, key=_unit_order)) for stratum, group in grouped.items()
        }
        self._plans = self._make_plans()

    @property
    def plans(self) -> tuple[StratumPlan, ...]:
        return self._plans

    def retrieve(self, request: RetrievalRequest, scores: RetrievalScores = RetrievalScores()) -> RetrievalBatch:
        requested = request.strata or tuple(plan.stratum for plan in self._plans)
        unknown = set(requested) - {plan.stratum for plan in self._plans}
        if unknown:
            raise ValueError(f"requested unknown strata: {sorted(unknown)!r}")
        candidates: list[tuple[ContentUnit, float | None]] = []
        receipts: list[RetrievalReceipt] = []
        for stratum in requested:
            units = self._units_by_stratum.get(stratum, ())
            coverage = self._coverage_for(stratum, len(units))
            matched, strategy = self._retrieve_stratum(request, units, coverage, scores)
            limited = matched[: request.limit_per_stratum]
            candidates.extend(limited)
            index = coverage.lexical if request.mode is RetrievalMode.LEXICAL else coverage.semantic
            if request.mode in {RetrievalMode.RANDOM, RetrievalMode.LOW_SCORE_RANDOM}:
                index = IndexCoverage(IndexFreshness.UNAVAILABLE, 0)
            receipts.append(
                RetrievalReceipt(
                    mode=request.mode,
                    stratum=stratum,
                    query_fingerprint=request.query_fingerprint,
                    seed_fingerprint=_seed_fingerprint(request.seed),
                    source_denominator=coverage.source_denominator,
                    indexed_denominator=index.indexed_denominator,
                    missing_coverage=max(coverage.source_denominator - index.indexed_denominator, 0),
                    coverage_completeness=coverage.completeness,
                    index_freshness=index.freshness,
                    index_version=index.version,
                    model_version=index.model_version if request.mode is RetrievalMode.SEMANTIC else None,
                    candidate_count=len(matched),
                    result_count=len(limited),
                    strategy=strategy,
                    similarity_is_retrieval_evidence_only=request.mode is RetrievalMode.SEMANTIC,
                    known_gaps=coverage.known_gaps,
                )
            )
        return RetrievalBatch(
            hits=self._deduplicated_hits(candidates, request),
            receipts=tuple(receipts),
        )

    def _make_plans(self) -> tuple[StratumPlan, ...]:
        strata = sorted(set(self._units_by_stratum) | set(self._coverage))
        return tuple(
            StratumPlan(
                stratum=stratum,
                unit_count=len(self._units_by_stratum.get(stratum, ())),
                coverage=self._coverage_for(stratum, len(self._units_by_stratum.get(stratum, ()))),
            )
            for stratum in strata
        )

    def _coverage_for(self, stratum: ProviderYear, observed_count: int) -> StratumCoverage:
        declared = self._coverage.get(stratum)
        if declared is not None:
            return declared
        return StratumCoverage(
            source_denominator=observed_count,
            completeness=CoverageCompleteness.UNKNOWN,
            lexical=IndexCoverage(IndexFreshness.STALE, 0),
            semantic=IndexCoverage(IndexFreshness.UNAVAILABLE, 0),
            known_gaps=("source adapter did not declare coverage",),
        )

    def _retrieve_stratum(
        self,
        request: RetrievalRequest,
        units: tuple[ContentUnit, ...],
        coverage: StratumCoverage,
        scores: RetrievalScores,
    ) -> tuple[list[tuple[ContentUnit, float | None]], str]:
        if request.mode is RetrievalMode.LEXICAL:
            direct = [(unit, _lexical_score(unit.text, request.query or "")) for unit in units]
            matched = [(unit, score) for unit, score in direct if score > 0]
            if coverage.lexical.freshness is IndexFreshness.FRESH and scores.fts:
                ranked = [
                    (unit, scores.fts[unit.content_unit_id])
                    for unit in units
                    if unit.content_unit_id in scores.fts
                ]
                return _rank(ranked), "fresh_fts_with_exact_scan"
            return _rank(matched), "direct_scan_stale_or_unavailable_fts"
        if request.mode is RetrievalMode.SEMANTIC:
            matched = [
                (unit, scores.semantic[unit.content_unit_id])
                for unit in units
                if unit.content_unit_id in scores.semantic
            ]
            return _rank(matched), "embedding_scores_retrieval_only"
        if request.mode is RetrievalMode.RANDOM:
            return _deterministic_sample(units, request.seed or "", request.limit_per_stratum), "deterministic_stratum_sample"
        eligible = [
            unit
            for unit in units
            if unit.autobiographical_score is not None
            and unit.autobiographical_score <= (request.low_score_maximum or 0)
        ]
        return (
            _deterministic_sample(eligible, request.seed or "", request.limit_per_stratum),
            "deterministic_low_score_false_negative_sample",
        )

    def _deduplicated_hits(
        self,
        candidates: list[tuple[ContentUnit, float | None]],
        request: RetrievalRequest,
    ) -> tuple[RetrievalHit, ...]:
        by_hash: dict[str, list[tuple[ContentUnit, float | None]]] = defaultdict(list)
        for candidate in candidates:
            by_hash[candidate[0].content_hash].append(candidate)
        hits: list[RetrievalHit] = []
        for copies in by_hash.values():
            canonical, score = sorted(copies, key=_candidate_order)[0]
            all_copies = sorted(copies, key=lambda item: _unit_order(item[0]))
            hits.append(
                RetrievalHit(
                    unit=canonical,
                    score=score,
                    context=self._context(canonical, request.context_before, request.context_after),
                    duplicate_occurrences=tuple(
                        DuplicateOccurrence(
                            content_unit_id=unit.content_unit_id,
                            provider=unit.provider,
                            conversation_id=unit.conversation_id,
                            branch_id=unit.branch_id,
                            lineage_id=unit.lineage_id,
                            source_locator=unit.source_locator,
                        )
                        for unit, _ in all_copies
                    ),
                )
            )
        return tuple(sorted(hits, key=lambda hit: _candidate_order((hit.unit, hit.score))))

    def _context(self, center: ContentUnit, before: int, after: int) -> ContextWindow:
        if center.conversation_id is None:
            return ContextWindow(center, (), ())
        conversation = [
            unit
            for unit in self._units
            if unit.provider == center.provider
            and unit.conversation_id == center.conversation_id
            and unit.branch_id == center.branch_id
            and unit.lineage_id == center.lineage_id
        ]
        conversation.sort(key=_unit_order)
        index = next(index for index, unit in enumerate(conversation) if unit.content_unit_id == center.content_unit_id)
        return ContextWindow(
            center=center,
            before=tuple(conversation[max(index - before, 0) : index]),
            after=tuple(conversation[index + 1 : index + 1 + after]),
        )


def _lexical_score(text: str, query: str) -> float:
    normalized_text = text.casefold()
    normalized_query = query.casefold().strip()
    if not normalized_query:
        return 0.0
    phrase_matches = normalized_text.count(normalized_query)
    query_tokens = set(re.findall(r"\w+", normalized_query))
    text_tokens = set(re.findall(r"\w+", normalized_text))
    return float(phrase_matches * 10 + len(query_tokens & text_tokens))


def _rank(
    candidates: Iterable[tuple[ContentUnit, float | None]],
) -> list[tuple[ContentUnit, float | None]]:
    return sorted(candidates, key=_candidate_order)


def _deterministic_sample(
    units: Iterable[ContentUnit], seed: str, limit: int
) -> list[tuple[ContentUnit, float | None]]:
    ranked = sorted(
        units,
        key=lambda unit: sha256(f"{seed}\0{unit.stratum.provider}\0{unit.stratum.year}\0{unit.content_unit_id}".encode()).hexdigest(),
    )
    return [(unit, None) for unit in ranked[:limit]]


def _candidate_order(candidate: tuple[ContentUnit, float | None]) -> tuple[float, str]:
    unit, score = candidate
    return (-(score if score is not None else 0.0), unit.content_unit_id)


def _unit_order(unit: ContentUnit) -> tuple[datetime, int, str]:
    return (unit.occurred_at, unit.sequence, unit.content_unit_id)


def _seed_fingerprint(seed: str | None) -> str | None:
    if seed is None:
        return None
    return sha256(seed.encode()).hexdigest()


def _batch_query_fingerprint(batch: RetrievalBatch) -> str | None:
    values = {receipt.query_fingerprint for receipt in batch.receipts}
    return next(iter(values)) if len(values) == 1 else None


__all__ = [
    "ContentUnit",
    "ContextWindow",
    "CoverageCompleteness",
    "DuplicateOccurrence",
    "IndexCoverage",
    "IndexFreshness",
    "MarginalYield",
    "ProviderYear",
    "QueryExpansionLedger",
    "RetrievalBatch",
    "RetrievalEngine",
    "RetrievalHit",
    "RetrievalMode",
    "RetrievalReceipt",
    "RetrievalRequest",
    "RetrievalScores",
    "StratumCoverage",
    "StratumPlan",
]
