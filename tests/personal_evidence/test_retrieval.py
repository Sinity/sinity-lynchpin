"""Production-path retrieval tests using neutral multi-provider fixtures."""

from __future__ import annotations

from datetime import datetime, timezone

from lynchpin.personal_evidence.models import AuthorshipClass
from lynchpin.personal_evidence.retrieval import (
    ContentUnit,
    CoverageCompleteness,
    IndexCoverage,
    IndexFreshness,
    ProviderYear,
    QueryExpansionLedger,
    RetrievalEngine,
    RetrievalMode,
    RetrievalRequest,
    RetrievalScores,
    StratumCoverage,
)


UTC = timezone.utc
ALPHA_2024 = ProviderYear("alpha", 2024)
ALPHA_2025 = ProviderYear("alpha", 2025)
BETA_2025 = ProviderYear("beta", 2025)


def unit(
    identifier: str,
    provider: str,
    year: int,
    text: str,
    content_hash: str,
    *,
    conversation: str = "conversation-1",
    branch: str = "main",
    lineage: str = "lineage-1",
    sequence: int = 0,
    score: float | None = 0.1,
    authorship: AuthorshipClass = AuthorshipClass.OPERATOR_DIRECT,
) -> ContentUnit:
    return ContentUnit(
        content_unit_id=identifier,
        provider=provider,
        occurred_at=datetime(year, 6, 1, 12, sequence, tzinfo=UTC),
        text=text,
        content_hash=content_hash,
        conversation_id=conversation,
        branch_id=branch,
        lineage_id=lineage,
        parent_unit_id=None,
        role="user",
        authorship=authorship,
        source_locator=f"fixture://{provider}/{identifier}",
        sequence=sequence,
        autobiographical_score=score,
    )


def coverage(
    *,
    source: int,
    completeness: CoverageCompleteness = CoverageCompleteness.COMPLETE,
    fts: IndexFreshness = IndexFreshness.FRESH,
    fts_indexed: int | None = None,
    embeddings: IndexFreshness = IndexFreshness.FRESH,
    embeddings_indexed: int | None = None,
    embedding_model: str | None = "neutral-embed-v1",
) -> StratumCoverage:
    return StratumCoverage(
        source_denominator=source,
        completeness=completeness,
        lexical=IndexCoverage(fts, source if fts_indexed is None else fts_indexed, version="fts-v1"),
        semantic=IndexCoverage(
            embeddings,
            source if embeddings_indexed is None else embeddings_indexed,
            version="embedding-index-v1",
            model_version=embedding_model,
        ),
        known_gaps=("fixture gap",) if completeness is CoverageCompleteness.PARTIAL else (),
    )


def engine() -> RetrievalEngine:
    return RetrievalEngine(
        [
            unit("alpha-old", "alpha", 2024, "orchard ledger", "h-old", sequence=0),
            unit("alpha-before", "alpha", 2025, "quiet preface", "h-before", sequence=0),
            unit("alpha-target", "alpha", 2025, "orchard ledger entry", "h-target", sequence=1),
            unit("alpha-after", "alpha", 2025, "quiet suffix", "h-after", sequence=2),
            unit("beta-copy", "beta", 2025, "orchard ledger entry", "h-target", sequence=0),
            unit("beta-low", "beta", 2025, "unrelated weather", "h-low", sequence=1, score=0.05),
            unit(
                "beta-model",
                "beta",
                2025,
                "orchard ledger entry",
                "h-model",
                sequence=2,
                authorship=AuthorshipClass.MODEL_GENERATED,
            ),
        ],
        {
            ALPHA_2024: coverage(source=1),
            ALPHA_2025: coverage(source=3, fts=IndexFreshness.STALE, fts_indexed=1),
            BETA_2025: coverage(
                source=3,
                completeness=CoverageCompleteness.PARTIAL,
                embeddings=IndexFreshness.PARTIAL,
                embeddings_indexed=1,
                embedding_model="neutral-embed-v2",
            ),
            ProviderYear("beta", 2024): coverage(
                source=0,
                completeness=CoverageCompleteness.UNAVAILABLE,
                fts=IndexFreshness.UNAVAILABLE,
                fts_indexed=0,
                embeddings=IndexFreshness.UNAVAILABLE,
                embeddings_indexed=0,
                embedding_model=None,
            ),
        },
    )


def test_stratum_plan_and_stale_fts_fallback_cover_zero_result_strata() -> None:
    retrieval = engine()

    assert [(plan.stratum, plan.unit_count) for plan in retrieval.plans] == [
        (ALPHA_2024, 1),
        (ALPHA_2025, 3),
        (ProviderYear("beta", 2024), 0),
        (BETA_2025, 3),
    ]
    batch = retrieval.retrieve(
        RetrievalRequest(RetrievalMode.LEXICAL, limit_per_stratum=10, query="orchard ledger"),
        RetrievalScores(fts={}),
    )

    alpha_2025 = next(receipt for receipt in batch.receipts if receipt.stratum == ALPHA_2025)
    zero = next(receipt for receipt in batch.receipts if receipt.stratum == ProviderYear("beta", 2024))
    assert alpha_2025.strategy == "direct_scan_stale_or_unavailable_fts"
    assert alpha_2025.result_count == 1
    assert zero.result_count == 0
    assert zero.coverage_completeness is CoverageCompleteness.UNAVAILABLE
    assert {hit.unit.content_unit_id for hit in batch.hits} >= {"alpha-old", "alpha-target"}


def test_semantic_receipts_show_partial_embeddings_and_similarity_is_not_biography() -> None:
    batch = engine().retrieve(
        RetrievalRequest(RetrievalMode.SEMANTIC, limit_per_stratum=10, query="opaque-query"),
        RetrievalScores(semantic={"beta-copy": 0.91, "beta-model": 0.90}),
    )

    beta = next(receipt for receipt in batch.receipts if receipt.stratum == BETA_2025)
    model = next(hit for hit in batch.hits if hit.unit.content_unit_id == "beta-model")
    assert (beta.model_version, beta.indexed_denominator, beta.missing_coverage) == (
        "neutral-embed-v2",
        1,
        2,
    )
    assert beta.similarity_is_retrieval_evidence_only
    assert not model.is_biographical_evidence_candidate


def test_fresh_fts_results_are_not_limited_to_exact_scan_matches() -> None:
    batch = engine().retrieve(
        RetrievalRequest(
            RetrievalMode.LEXICAL,
            limit_per_stratum=10,
            query="stemming-sensitive-query",
            strata=(ALPHA_2024,),
        ),
        RetrievalScores(fts={"alpha-old": 0.75}),
    )

    assert [hit.unit.content_unit_id for hit in batch.hits] == ["alpha-old"]
    assert batch.receipts[0].strategy == "fresh_fts_with_exact_scan"


def test_content_hash_dedup_keeps_copy_locators_and_context_neighbors() -> None:
    batch = engine().retrieve(
        RetrievalRequest(RetrievalMode.LEXICAL, limit_per_stratum=10, query="orchard ledger"),
    )

    target = next(hit for hit in batch.hits if hit.unit.content_hash == "h-target")
    assert target.duplicate_unit_ids == ("beta-copy", "alpha-target")
    assert target.duplicate_locators == (
        "fixture://beta/beta-copy",
        "fixture://alpha/alpha-target",
    )
    assert [(copy.conversation_id, copy.branch_id, copy.lineage_id) for copy in target.duplicate_occurrences] == [
        ("conversation-1", "main", "lineage-1"),
        ("conversation-1", "main", "lineage-1"),
    ]
    assert [item.content_unit_id for item in target.context.before] == ["alpha-before"]
    assert [item.content_unit_id for item in target.context.after] == ["alpha-after"]
    assert target.context.center.branch_id == "main"
    assert target.context.center.lineage_id == "lineage-1"


def test_deterministic_random_and_low_score_false_negative_samples_are_stable() -> None:
    retrieval = engine()
    random_request = RetrievalRequest(RetrievalMode.RANDOM, limit_per_stratum=2, seed="fixture-seed")
    first = retrieval.retrieve(random_request)
    second = retrieval.retrieve(random_request)
    low = retrieval.retrieve(
        RetrievalRequest(
            RetrievalMode.LOW_SCORE_RANDOM,
            limit_per_stratum=10,
            seed="fixture-seed",
            low_score_maximum=0.05,
        )
    )

    assert [hit.unit.content_unit_id for hit in first.hits] == [hit.unit.content_unit_id for hit in second.hits]
    assert {hit.unit.content_unit_id for hit in low.hits} == {"beta-low"}
    assert {receipt.strategy for receipt in low.receipts} == {"deterministic_low_score_false_negative_sample"}


def test_marginal_yield_can_stop_only_after_diminishing_results_with_documented_coverage() -> None:
    retrieval = engine()
    request = RetrievalRequest(
        RetrievalMode.LEXICAL,
        limit_per_stratum=10,
        query="orchard ledger",
        strata=(ALPHA_2024, ALPHA_2025),
    )
    ledger = QueryExpansionLedger(diminishing_yield_at_most=0)

    first = ledger.record(retrieval.retrieve(request))
    second = ledger.record(retrieval.retrieve(request))

    assert first.novel_unique_count == 2
    assert not first.may_stop_expansion
    assert second.novel_unique_count == 0
    assert second.coverage_documented
    assert second.may_stop_expansion
