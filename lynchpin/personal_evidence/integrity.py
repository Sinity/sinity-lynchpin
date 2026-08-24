"""Pure integrity checks for personal-evidence claim records.

The helpers deliberately accept mappings and dataclass-like objects.  They do
not interpret prose or depend on a source adapter: callers supply structured
authorship, scope, lineage, framing, and clock fields at their own boundary.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum


class Authorship(StrEnum):
    """Who supplied the material represented by a record."""

    OPERATOR = "operator"
    THIRD_PARTY = "third_party"
    MACHINE = "machine"
    DETERMINISTIC = "deterministic"
    MODEL = "model"
    UNKNOWN = "unknown"


class EpistemicClass(StrEnum):
    """What kind of assertion a record makes."""

    OBSERVATION = "observation"
    QUOTATION = "quotation"
    INFERENCE = "inference"
    HYPOTHESIS = "hypothesis"
    CURRENT_FACT = "current_fact"
    ADOPTION = "adoption"
    RETRACTION = "retraction"


class EvidenceClass(StrEnum):
    """Lineage role of evidence, independent of its authorship."""

    PRIMARY_OPERATOR = "primary_operator"
    PRIMARY_THIRD_PARTY = "primary_third_party"
    PRIMARY_MACHINE = "primary_machine"
    DETERMINISTIC = "deterministic"
    DERIVED = "derived"
    MODEL = "model"


class ContaminationOutcome(StrEnum):
    """Result of an audit for unintended material in an evidence item."""

    CLEAR = "clear"
    CONTAMINATED = "contaminated"
    INDETERMINATE = "indeterminate"
    UNAUDITED = "unaudited"


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
    contamination: Mapping[str, ContaminationOutcome]

    @property
    def valid(self) -> bool:
        return not any(finding.severity == "error" for finding in self.findings)


Record = Mapping[str, object] | object

_PRIMARY_EVIDENCE = frozenset(
    {
        EvidenceClass.PRIMARY_OPERATOR.value,
        EvidenceClass.PRIMARY_THIRD_PARTY.value,
        EvidenceClass.PRIMARY_MACHINE.value,
        EvidenceClass.DETERMINISTIC.value,
    }
)


def validate_authorship(value: object) -> Authorship | None:
    """Return the normalized authorship class, or ``None`` when invalid."""

    try:
        return Authorship(str(value))
    except ValueError:
        return None


def validate_epistemic_class(value: object) -> EpistemicClass | None:
    """Return the normalized epistemic class, or ``None`` when invalid."""

    try:
        return EpistemicClass(str(value))
    except ValueError:
        return None


def classify_contamination(audit: Record) -> ContaminationOutcome:
    """Classify structured audit data without turning missing audit into clear."""

    explicit = _value(audit, "contamination_outcome", "audit_outcome")
    if explicit is not None:
        normalized = str(explicit).lower()
        aliases = {
            "clean": ContaminationOutcome.CLEAR,
            "clear": ContaminationOutcome.CLEAR,
            "contaminated": ContaminationOutcome.CONTAMINATED,
            "indeterminate": ContaminationOutcome.INDETERMINATE,
            "unknown": ContaminationOutcome.INDETERMINATE,
            "unaudited": ContaminationOutcome.UNAUDITED,
            "not_audited": ContaminationOutcome.UNAUDITED,
        }
        return aliases.get(normalized, ContaminationOutcome.INDETERMINATE)

    contaminated = _value(audit, "contaminated")
    if contaminated is True:
        return ContaminationOutcome.CONTAMINATED
    if contaminated is False:
        return ContaminationOutcome.CLEAR
    return ContaminationOutcome.UNAUDITED


def independence_groups(evidence: Iterable[Record]) -> tuple[frozenset[str], ...]:
    """Group duplicate, quoted, and common-ancestor evidence as non-independent.

    Each declared lineage edge is treated as a shared provenance edge.  The
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
    claims: Iterable[Record], evidence: Iterable[Record]
) -> IntegrityReport:
    """Validate generic claim records against structured personal evidence.

    Major claims need at least one support path ending in primary operator,
    third-party, machine, or deterministic evidence.  The function reports,
    rather than removes, contradictions and retractions so a caller can retain
    the complete evidentiary record.
    """

    claim_records = tuple(claims)
    evidence_records = tuple(evidence)
    evidence_by_id = {
        _record_id(record, index): record for index, record in enumerate(evidence_records)
    }
    claim_ids = {
        _record_id(record, index) for index, record in enumerate(claim_records)
    }
    findings: list[IntegrityFinding] = []
    contamination = {
        evidence_id: classify_contamination(record)
        for evidence_id, record in evidence_by_id.items()
    }

    for evidence_id, record in evidence_by_id.items():
        _validate_classes(record, evidence_id, findings)
        if _evidence_class(record) is None:
            findings.append(
                IntegrityFinding(
                    "INVALID_EVIDENCE_CLASS",
                    evidence_id,
                    "evidence_class must name a supported lineage role",
                )
            )

    contradictions, retractions = _relations(claim_records, claim_ids, findings)
    for index, claim in enumerate(claim_records):
        claim_id = _record_id(claim, index)
        _validate_classes(claim, claim_id, findings)
        support_ids = _references(_value(claim, "evidence_ids", "supports", "support_ids"))

        if _major(claim) and not _has_primary_path(support_ids, evidence_by_id):
            findings.append(
                IntegrityFinding(
                    "MISSING_PRIMARY_EVIDENCE_PATH",
                    claim_id,
                    "major claim has no support path ending in primary evidence",
                )
            )

        _validate_quote(claim, claim_id, evidence_by_id, findings)
        _validate_scope(claim, claim_id, evidence_by_id, findings)
        _validate_framing(claim, claim_id, evidence_by_id, findings)
        _validate_model_laundering(claim, claim_id, support_ids, evidence_by_id, findings)
        _validate_current_fact_clock(claim, claim_id, support_ids, evidence_by_id, findings)

    return IntegrityReport(
        findings=tuple(findings),
        independence_groups=independence_groups(evidence_records),
        contradictions=tuple(contradictions),
        retractions=tuple(retractions),
        contamination=contamination,
    )


def _value(record: Record, *names: str) -> object | None:
    for name in names:
        if isinstance(record, Mapping) and name in record:
            return record[name]
        if hasattr(record, name):
            return getattr(record, name)
    return None


def _record_id(record: Record, index: int) -> str:
    value = _value(record, "id", "record_id", "claim_id", "evidence_id")
    return str(value) if value is not None else f"record:{index}"


def _references(value: object | None) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence):
        return tuple(str(item) for item in value)
    return (str(value),)


def _validate_classes(record: Record, record_id: str, findings: list[IntegrityFinding]) -> None:
    if validate_authorship(_value(record, "authorship")) is None:
        findings.append(
            IntegrityFinding("INVALID_AUTHORSHIP", record_id, "authorship is not a supported class")
        )
    if validate_epistemic_class(_value(record, "epistemic_class")) is None:
        findings.append(
            IntegrityFinding(
                "INVALID_EPISTEMIC_CLASS",
                record_id,
                "epistemic_class is not a supported class",
            )
        )


def _evidence_class(record: Record) -> EvidenceClass | None:
    value = _value(record, "evidence_class", "kind")
    try:
        return EvidenceClass(str(value))
    except ValueError:
        return None


def _major(claim: Record) -> bool:
    return bool(_value(claim, "major", "is_major"))


def _has_primary_path(support_ids: Sequence[str], evidence_by_id: Mapping[str, Record]) -> bool:
    def reaches_primary(evidence_id: str, visiting: frozenset[str]) -> bool:
        if evidence_id in visiting:
            return False
        record = evidence_by_id.get(evidence_id)
        if record is None:
            return False
        evidence_class = _evidence_class(record)
        if evidence_class is not None and evidence_class.value in _PRIMARY_EVIDENCE:
            return True
        return any(
            reaches_primary(parent_id, visiting | {evidence_id})
            for parent_id in _references(_value(record, "parent_ids", "parents"))
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
    source_quote = _value(source, "quoted_text", "text") if source is not None else None
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
    source_scope = _scope(_value(source, "scope")) if source is not None else None
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
    if validate_authorship(_value(claim, "authorship")) is not Authorship.OPERATOR:
        return
    direct_support = [evidence_by_id[evidence_id] for evidence_id in support_ids if evidence_id in evidence_by_id]
    if direct_support and all(
        _evidence_class(record) is EvidenceClass.MODEL
        or validate_authorship(_value(record, "authorship")) is Authorship.MODEL
        for record in direct_support
    ):
        findings.append(
            IntegrityFinding(
                "MODEL_TO_OPERATOR_LAUNDERING",
                claim_id,
                "operator-authored claim has only model-authored direct support",
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


def _validate_current_fact_clock(
    claim: Record,
    claim_id: str,
    support_ids: Sequence[str],
    evidence_by_id: Mapping[str, Record],
    findings: list[IntegrityFinding],
) -> None:
    if validate_epistemic_class(_value(claim, "epistemic_class")) is not EpistemicClass.CURRENT_FACT:
        return
    as_of = _as_of(_value(claim, "as_of", "asserted_at"))
    if as_of is None:
        findings.append(
            IntegrityFinding(
                "MISSING_CURRENT_FACT_CLOCK",
                claim_id,
                "current fact needs an as_of or asserted_at date",
            )
        )
        return
    for evidence_id in support_ids:
        record = evidence_by_id.get(evidence_id)
        if record is None or _evidence_class(record) is EvidenceClass.DETERMINISTIC:
            continue
        observed_at = _as_of(_value(record, "observed_at", "recorded_at", "adopted_at"))
        if observed_at is not None and as_of < observed_at:
            findings.append(
                IntegrityFinding(
                    "CURRENT_FACT_BACKDATING",
                    claim_id,
                    f"claim as_of precedes direct evidence {evidence_id!r}",
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
