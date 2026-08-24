"""Pure integrity checks for structured personal-evidence records.

The helpers deliberately accept mappings and dataclass-like objects. They do
not interpret prose or depend on a source adapter: callers provide structured
authorship, epistemic role, lineage, lifecycle, scope, framing, and clock
fields at their own boundary.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum


class Authorship(StrEnum):
    """Who supplied the material represented by a record."""

    OPERATOR_DIRECT = "operator_direct"
    OPERATOR_QUOTED_OR_FORWARDED = "operator_quoted_or_forwarded"
    THIRD_PARTY_DIRECT = "third_party_direct"
    MACHINE_OBSERVATION = "machine_observation"
    DETERMINISTIC_DERIVATION = "deterministic_derivation"
    MODEL_GENERATED = "model_generated"
    AGENT_GENERATED = "agent_generated"
    UNKNOWN_AUTHORSHIP = "unknown_authorship"


class EpistemicClass(StrEnum):
    """What a record asserts, independently of its authorship and lifecycle."""

    MEASURED_FACT = "measured_fact"
    CONTEMPORANEOUS_SELF_REPORT = "contemporaneous_self_report"
    RETROSPECTIVE_SELF_REPORT = "retrospective_self_report"
    THIRD_PARTY_REPORT = "third_party_report"
    REPORTED_EVENT = "reported_event"
    DIRECT_COMMUNICATION = "direct_communication"
    DERIVED_STATISTIC = "derived_statistic"
    ASSOCIATION = "association"
    QUALIFIED_INFERENCE = "qualified_inference"
    HYPOTHESIS = "hypothesis"
    NARRATIVE = "narrative"
    UNKNOWN = "unknown"


class ClaimStatus(StrEnum):
    """Lifecycle status for a claim, not an epistemic role."""

    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    CONTESTED = "contested"
    RETRACTED = "retracted"
    SUPERSEDED = "superseded"
    MODEL_ONLY_UNSUBSTANTIATED = "model_only_unsubstantiated"
    NOT_FOUND_WITH_COVERAGE = "not_found_with_coverage"
    UNKNOWN = "unknown"


class ContaminationOutcome(StrEnum):
    """Mission-defined provenance-audit outcomes for a claim or evidence record."""

    PRIMARY_SUPPORTED = "primary_supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    OPERATOR_ADOPTED_LATER = "operator_adopted_later"
    MODEL_ONLY_UNSUBSTANTIATED = "model_only_unsubstantiated"
    CONTRADICTED = "contradicted"
    NOT_FOUND_WITH_COVERAGE = "not_found_with_coverage"


@dataclass(frozen=True)
class IntegrityFinding:
    code: str
    record_id: str
    detail: str
    severity: str = "error"


@dataclass(frozen=True)
class ClaimRelation:
    kind: str
    claim_id: str
    target_id: str


@dataclass(frozen=True)
class IntegrityReport:
    findings: tuple[IntegrityFinding, ...]
    independence_groups: tuple[frozenset[str], ...]
    contradictions: tuple[ClaimRelation, ...]
    retractions: tuple[ClaimRelation, ...]
    contamination: Mapping[str, ContaminationOutcome | None]

    @property
    def valid(self) -> bool:
        return not any(finding.severity == "error" for finding in self.findings)


Record = Mapping[str, object] | object

_PRIMARY_TERMINAL_AUTHORSHIP = frozenset(
    {
        Authorship.OPERATOR_DIRECT,
        Authorship.THIRD_PARTY_DIRECT,
        Authorship.MACHINE_OBSERVATION,
        Authorship.DETERMINISTIC_DERIVATION,
    }
)
_ANSWER_CARD_SUPPORTED_STATUSES = frozenset(
    {ClaimStatus.SUPPORTED, ClaimStatus.PARTIALLY_SUPPORTED}
)


def validate_authorship(value: object) -> Authorship | None:
    """Return the normalized mission authorship class, or ``None`` when invalid."""

    try:
        return Authorship(str(value))
    except ValueError:
        return None


def validate_epistemic_class(value: object) -> EpistemicClass | None:
    """Return the normalized mission epistemic class, or ``None`` when invalid."""

    try:
        return EpistemicClass(str(value))
    except ValueError:
        return None


def validate_claim_status(value: object) -> ClaimStatus | None:
    """Return a claim lifecycle status, or ``None`` when invalid."""

    try:
        return ClaimStatus(str(value))
    except ValueError:
        return None


def classify_contamination(audit: Record) -> ContaminationOutcome | None:
    """Return an explicit mission audit outcome without inventing an audit result."""

    outcome = _value(audit, "contamination_outcome", "audit_outcome")
    try:
        return ContaminationOutcome(str(outcome)) if outcome is not None else None
    except ValueError:
        return None


def independence_groups(evidence: Iterable[Record]) -> tuple[frozenset[str], ...]:
    """Group duplicate, quoted, and common-ancestor evidence as non-independent.

    Each declared lineage edge is treated as a shared provenance edge. The
    result is stable and includes singleton groups, which lets callers count
    independent observations without silently discarding isolated evidence.
    """

    records = tuple(evidence)
    ids = {_record_id(record, index) for index, record in enumerate(records)}
    parent = {record_id: record_id for record_id in ids}

    def find(item: str) -> str:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    for index, record in enumerate(records):
        record_id = _record_id(record, index)
        for reference in _references(
            _value(record, "parent_ids", "parents", "duplicate_of", "quoted_from", "quote_source_id")
        ):
            if reference in ids:
                union(record_id, reference)

    grouped: dict[str, set[str]] = defaultdict(set)
    for record_id in ids:
        grouped[find(record_id)].add(record_id)
    return tuple(
        frozenset(grouped[root])
        for root in sorted(grouped, key=lambda root: tuple(sorted(grouped[root])))
    )


def validate_integrity(
    claims: Iterable[Record],
    evidence: Iterable[Record],
    *,
    answer_cards: Iterable[Record] = (),
) -> IntegrityReport:
    """Validate claims, evidence, and answer-card reuse against mission integrity rules.

    Major claims must have a lineage path terminating in operator-direct,
    third-party-direct, machine-observation, or deterministic-derivation
    evidence. Model- and agent-generated leaves cannot satisfy that gate.
    """

    claim_records = tuple(claims)
    evidence_records = tuple(evidence)
    answer_card_records = tuple(answer_cards)
    evidence_by_id = {
        _record_id(record, index): record for index, record in enumerate(evidence_records)
    }
    claim_by_id = {
        _record_id(record, index): record for index, record in enumerate(claim_records)
    }
    findings: list[IntegrityFinding] = []
    contamination = {
        evidence_id: classify_contamination(record)
        for evidence_id, record in evidence_by_id.items()
    }

    for evidence_id, record in evidence_by_id.items():
        _validate_classes(record, evidence_id, findings)

    contradictions, retractions = _relations(claim_records, set(claim_by_id), findings)
    primary_paths: dict[str, bool] = {}
    for claim_id, claim in claim_by_id.items():
        _validate_classes(claim, claim_id, findings)
        _validate_claim_lifecycle(claim, claim_id, findings)
        support_ids = _references(_value(claim, "evidence_ids", "supports", "support_ids"))
        primary_paths[claim_id] = _has_primary_path(support_ids, evidence_by_id)

        if _major(claim) and not primary_paths[claim_id]:
            findings.append(
                IntegrityFinding(
                    "MISSING_PRIMARY_EVIDENCE_PATH",
                    claim_id,
                    "major claim has no path ending in mission-primary authorship",
                )
            )

        _validate_quote(claim, claim_id, evidence_by_id, findings)
        _validate_scope(claim, claim_id, evidence_by_id, findings)
        _validate_framing(claim, claim_id, evidence_by_id, findings)
        _validate_model_laundering(claim, claim_id, support_ids, evidence_by_id, findings)
        _validate_adoption_clock(claim, claim_id, support_ids, evidence_by_id, findings)

    _validate_answer_card_reuse(answer_card_records, claim_by_id, primary_paths, findings)

    return IntegrityReport(
        findings=tuple(findings),
        independence_groups=independence_groups(evidence_records),
        contradictions=tuple(contradictions),
        retractions=tuple(retractions),
        contamination=contamination,
    )


def _value(record: Record | None, *names: str) -> object | None:
    if record is None:
        return None
    for name in names:
        if isinstance(record, Mapping) and name in record:
            return record[name]
        if hasattr(record, name):
            return getattr(record, name)
    return None


def _record_id(record: Record, index: int) -> str:
    value = _value(record, "id", "record_id", "claim_id", "evidence_id", "answer_card_id")
    return str(value) if value is not None else f"record:{index}"


def _references(value: object | None) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence):
        return tuple(str(item) for item in value)
    return (str(value),)


def _authorship(record: Record) -> Authorship | None:
    return validate_authorship(_value(record, "authorship", "authorship_class"))


def _epistemic_class(record: Record) -> EpistemicClass | None:
    return validate_epistemic_class(_value(record, "epistemic_class", "evidence_class"))


def _validate_classes(record: Record, record_id: str, findings: list[IntegrityFinding]) -> None:
    if _authorship(record) is None:
        findings.append(
            IntegrityFinding("INVALID_AUTHORSHIP", record_id, "authorship is not a mission class")
        )
    if _epistemic_class(record) is None:
        findings.append(
            IntegrityFinding(
                "INVALID_EPISTEMIC_CLASS",
                record_id,
                "epistemic_class or evidence_class is not a mission class",
            )
        )


def _validate_claim_lifecycle(
    claim: Record, claim_id: str, findings: list[IntegrityFinding]
) -> None:
    status_value = _value(claim, "status", "claim_status")
    status = validate_claim_status(status_value) if status_value is not None else None
    if status_value is not None and status is None:
        findings.append(
            IntegrityFinding("INVALID_CLAIM_STATUS", claim_id, "status is not a mission claim status")
        )
    current_endorsement = _value(claim, "current_endorsement", "currently_endorsed")
    if current_endorsement is not None and not isinstance(current_endorsement, bool):
        findings.append(
            IntegrityFinding(
                "INVALID_CURRENT_ENDORSEMENT",
                claim_id,
                "current_endorsement must be a boolean when supplied",
            )
        )
    if status is ClaimStatus.RETRACTED and current_endorsement is True:
        findings.append(
            IntegrityFinding(
                "RETRACTED_CLAIM_CURRENTLY_ENDORSED",
                claim_id,
                "a retracted claim cannot also be currently endorsed",
            )
        )


def _major(claim: Record) -> bool:
    return bool(_value(claim, "major", "is_major"))


def _has_primary_path(support_ids: Sequence[str], evidence_by_id: Mapping[str, Record]) -> bool:
    def reaches_primary(evidence_id: str, visiting: frozenset[str]) -> bool:
        if evidence_id in visiting:
            return False
        record = evidence_by_id.get(evidence_id)
        if record is None:
            return False
        if _authorship(record) in _PRIMARY_TERMINAL_AUTHORSHIP:
            return True
        parents = _references(_value(record, "parent_ids", "parents"))
        return any(
            reaches_primary(parent_id, visiting | {evidence_id}) for parent_id in parents
        )

    return any(reaches_primary(evidence_id, frozenset()) for evidence_id in support_ids)


def _source_for(claim: Record, evidence_by_id: Mapping[str, Record]) -> Record | None:
    source_id = _value(claim, "quote_source_id", "source_evidence_id")
    if source_id is not None:
        return evidence_by_id.get(str(source_id))
    support_ids = _references(_value(claim, "evidence_ids", "supports", "support_ids"))
    return evidence_by_id.get(support_ids[0]) if len(support_ids) == 1 else None


def _validate_quote(
    claim: Record,
    claim_id: str,
    evidence_by_id: Mapping[str, Record],
    findings: list[IntegrityFinding],
) -> None:
    quoted = _value(claim, "quote", "quoted_text")
    if quoted is None:
        return
    source = _source_for(claim, evidence_by_id)
    source_quote = _value(source, "quoted_text", "text")
    if source_quote != quoted:
        findings.append(
            IntegrityFinding(
                "QUOTATION_MISMATCH",
                claim_id,
                "quoted text does not exactly match its declared evidence source",
            )
        )


def _scope(value: object | None) -> frozenset[str] | None:
    if value is None:
        return None
    return frozenset(_references(value))


def _validate_scope(
    claim: Record,
    claim_id: str,
    evidence_by_id: Mapping[str, Record],
    findings: list[IntegrityFinding],
) -> None:
    claim_scope = _scope(_value(claim, "scope"))
    source = _source_for(claim, evidence_by_id)
    source_scope = _scope(_value(source, "scope"))
    if claim_scope is not None and source_scope is not None and not claim_scope <= source_scope:
        findings.append(
            IntegrityFinding(
                "SCOPE_EXPANSION",
                claim_id,
                "claim scope includes subjects absent from its source scope",
            )
        )


def _validate_framing(
    claim: Record,
    claim_id: str,
    evidence_by_id: Mapping[str, Record],
    findings: list[IntegrityFinding],
) -> None:
    source = _source_for(claim, evidence_by_id)
    if source is None:
        return
    if _value(source, "negated") is True and _value(claim, "negated") is not True:
        findings.append(
            IntegrityFinding(
                "LOST_NEGATION",
                claim_id,
                "claim removed negation recorded by its source",
            )
        )
    if _value(source, "hypothetical") is True and _value(claim, "hypothetical") is not True:
        findings.append(
            IntegrityFinding(
                "LOST_HYPOTHETICAL_FRAMING",
                claim_id,
                "claim removed hypothetical framing recorded by its source",
            )
        )


def _validate_model_laundering(
    claim: Record,
    claim_id: str,
    support_ids: Sequence[str],
    evidence_by_id: Mapping[str, Record],
    findings: list[IntegrityFinding],
) -> None:
    if _authorship(claim) is not Authorship.OPERATOR_DIRECT:
        return
    direct_support = [
        evidence_by_id[evidence_id] for evidence_id in support_ids if evidence_id in evidence_by_id
    ]
    model_or_agent_authorship = {
        Authorship.MODEL_GENERATED,
        Authorship.AGENT_GENERATED,
    }
    if direct_support and all(
        _authorship(record) in model_or_agent_authorship for record in direct_support
    ):
        findings.append(
            IntegrityFinding(
                "MODEL_OR_AGENT_TO_OPERATOR_LAUNDERING",
                claim_id,
                "operator-direct claim has only model- or agent-generated direct support",
            )
        )


def _as_of(value: object | None) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                return date.fromisoformat(value)
            except ValueError:
                return None
    return None


def _validate_adoption_clock(
    claim: Record,
    claim_id: str,
    support_ids: Sequence[str],
    evidence_by_id: Mapping[str, Record],
    findings: list[IntegrityFinding],
) -> None:
    adoption_dates = [
        _as_of(_value(evidence_by_id[evidence_id], "adopted_at", "adoption_timestamp"))
        for evidence_id in support_ids
        if evidence_id in evidence_by_id
    ]
    adoption_dates = [adopted_at for adopted_at in adoption_dates if adopted_at is not None]
    if not adoption_dates:
        return
    effective_from = _as_of(
        _value(claim, "valid_from", "as_of", "current_endorsed_at", "asserted_at")
    )
    if effective_from is None:
        findings.append(
            IntegrityFinding(
                "MISSING_ADOPTION_CLOCK",
                claim_id,
                "a claim using adopted language needs valid_from, as_of, or current_endorsed_at",
            )
        )
        return
    adoption_date = max(adoption_dates)
    if effective_from < adoption_date:
        findings.append(
            IntegrityFinding(
                "ADOPTION_BACKDATING",
                claim_id,
                "claim effective date precedes the operator adoption timestamp",
            )
        )


def _validate_answer_card_reuse(
    answer_cards: Sequence[Record],
    claim_by_id: Mapping[str, Record],
    primary_paths: Mapping[str, bool],
    findings: list[IntegrityFinding],
) -> None:
    for index, card in enumerate(answer_cards):
        card_id = _record_id(card, index)
        for claim_id in _references(_value(card, "claim_ids", "supporting_claim_ids")):
            claim = claim_by_id.get(claim_id)
            status_value = _value(claim, "status", "claim_status")
            status = validate_claim_status(status_value) if status_value is not None else None
            supported = (
                claim is not None
                and primary_paths.get(claim_id, False)
                and status in _ANSWER_CARD_SUPPORTED_STATUSES
            )
            if not supported:
                findings.append(
                    IntegrityFinding(
                        "UNSUPPORTED_ANSWER_CARD_CLAIM_REUSE",
                        card_id,
                        f"answer card reuses unsupported claim {claim_id!r}",
                    )
                )


def _relations(
    claims: Sequence[Record], claim_ids: set[str], findings: list[IntegrityFinding]
) -> tuple[list[ClaimRelation], list[ClaimRelation]]:
    contradictions: list[ClaimRelation] = []
    retractions: list[ClaimRelation] = []
    for index, claim in enumerate(claims):
        claim_id = _record_id(claim, index)
        for kind, destination in (("contradiction", contradictions), ("retraction", retractions)):
            field_name = "contradicts" if kind == "contradiction" else "retracts"
            for target_id in _references(_value(claim, field_name)):
                if target_id not in claim_ids:
                    findings.append(
                        IntegrityFinding(
                            "UNKNOWN_RELATION_TARGET",
                            claim_id,
                            f"{kind} target {target_id!r} is not present",
                        )
                    )
                destination.append(ClaimRelation(kind, claim_id, target_id))
    return contradictions, retractions
