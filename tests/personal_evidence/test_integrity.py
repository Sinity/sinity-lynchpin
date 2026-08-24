from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from lynchpin.personal_evidence.integrity import (
    ContaminationOutcome,
    EpistemicClass,
    classify_contamination,
    independence_groups,
    validate_authorship,
    validate_epistemic_class,
    validate_integrity,
)


def _evidence(identifier: str, **overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "id": identifier,
        "authorship": "operator",
        "epistemic_class": "observation",
        "evidence_class": "primary_operator",
        "text": "A lamp was switched off.",
        "scope": ["lamp"],
        "observed_at": "2026-08-01",
    }
    record.update(overrides)
    return record


def _claim(identifier: str, **overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "id": identifier,
        "major": True,
        "authorship": "operator",
        "epistemic_class": "observation",
        "evidence_ids": ["operator-note"],
        "scope": ["lamp"],
    }
    record.update(overrides)
    return record


@dataclass
class DataclassEvidence:
    id: str
    authorship: str = "machine"
    epistemic_class: str = "observation"
    evidence_class: str = "primary_machine"
    observed_at: date = date(2026, 8, 1)


def _codes(report: object) -> set[str]:
    return {finding.code for finding in report.findings}  # type: ignore[attr-defined]


def test_accepts_primary_lineage_for_mapping_and_dataclass_records() -> None:
    report = validate_integrity(
        [_claim("c1"), _claim("c2", evidence_ids=["sensor"])],
        [_evidence("operator-note"), DataclassEvidence("sensor")],
    )

    assert report.valid
    assert _codes(report) == set()


def test_rejects_invalid_classes_and_major_claim_without_primary_path() -> None:
    report = validate_integrity(
        [_claim("c1", authorship="narrator", epistemic_class="certainty", evidence_ids=["derived"])],
        [
            _evidence(
                "derived",
                authorship="instrument",
                epistemic_class="summary",
                evidence_class="derived",
            )
        ],
    )

    assert _codes(report) >= {
        "INVALID_AUTHORSHIP",
        "INVALID_EPISTEMIC_CLASS",
        "MISSING_PRIMARY_EVIDENCE_PATH",
    }
    assert validate_authorship("operator") is not None
    assert validate_authorship("narrator") is None
    assert validate_epistemic_class("hypothesis") is EpistemicClass.HYPOTHESIS
    assert validate_epistemic_class("certainty") is None


def test_rejects_quotation_scope_and_framing_changes() -> None:
    source = _evidence(
        "operator-note",
        text="The lamp might not be on.",
        scope=["lamp"],
        negated=True,
        hypothetical=True,
    )
    report = validate_integrity(
        [
            _claim(
                "c1",
                quote="The lamp is on.",
                quote_source_id="operator-note",
                scope=["lamp", "hallway"],
                negated=False,
                hypothetical=False,
            )
        ],
        [source],
    )

    assert _codes(report) >= {
        "QUOTATION_MISMATCH",
        "SCOPE_EXPANSION",
        "LOST_NEGATION",
        "LOST_HYPOTHETICAL_FRAMING",
    }


def test_rejects_model_to_operator_laundering() -> None:
    report = validate_integrity(
        [_claim("c1", evidence_ids=["model-output"])],
        [
            _evidence(
                "model-output",
                authorship="model",
                epistemic_class="inference",
                evidence_class="model",
                parent_ids=["operator-note"],
            ),
            _evidence("operator-note"),
        ],
    )

    assert "MODEL_TO_OPERATOR_LAUNDERING" in _codes(report)


def test_accepts_later_operator_adoption_without_backdating() -> None:
    model = _evidence(
        "model-output",
        authorship="model",
        epistemic_class="inference",
        evidence_class="model",
        observed_at="2026-08-02",
    )
    adoption = _evidence(
        "operator-adoption",
        epistemic_class="adoption",
        parent_ids=["model-output"],
        adopted_at="2026-08-03",
        observed_at="2026-08-03",
    )
    report = validate_integrity(
        [
            _claim(
                "c1",
                epistemic_class="current_fact",
                evidence_ids=["operator-adoption"],
                as_of="2026-08-03",
            )
        ],
        [model, adoption],
    )

    assert report.valid
    assert "MODEL_TO_OPERATOR_LAUNDERING" not in _codes(report)
    assert "CURRENT_FACT_BACKDATING" not in _codes(report)


def test_rejects_current_fact_backdating() -> None:
    report = validate_integrity(
        [
            _claim(
                "c1",
                epistemic_class="current_fact",
                as_of="2026-08-01",
            )
        ],
        [_evidence("operator-note", observed_at="2026-08-04")],
    )

    assert "CURRENT_FACT_BACKDATING" in _codes(report)


def test_groups_duplicate_shared_prefix_and_quoted_ancestry() -> None:
    groups = independence_groups(
        [
            _evidence("root"),
            _evidence("derived-a", evidence_class="derived", parent_ids=["root"]),
            _evidence("derived-b", evidence_class="derived", parent_ids=["root"]),
            _evidence("copy", duplicate_of="derived-a", evidence_class="derived"),
            _evidence("quote", quoted_from="derived-b", evidence_class="derived"),
            _evidence("separate"),
        ]
    )

    assert frozenset({"root", "derived-a", "derived-b", "copy", "quote"}) in groups
    assert frozenset({"separate"}) in groups


def test_preserves_contradictions_and_retractions() -> None:
    report = validate_integrity(
        [
            _claim("original"),
            _claim("counter", contradicts=["original"]),
            _claim("withdrawal", epistemic_class="retraction", retracts=["original"]),
        ],
        [_evidence("operator-note")],
    )

    assert {relation.claim_id for relation in report.contradictions} == {"counter"}
    assert {relation.claim_id for relation in report.retractions} == {"withdrawal"}
    assert report.valid


def test_classifies_contamination_without_treating_missing_audit_as_clear() -> None:
    assert classify_contamination({"contaminated": False}) is ContaminationOutcome.CLEAR
    assert classify_contamination({"audit_outcome": "contaminated"}) is ContaminationOutcome.CONTAMINATED
    assert classify_contamination({"audit_outcome": "unknown"}) is ContaminationOutcome.INDETERMINATE
    assert classify_contamination({}) is ContaminationOutcome.UNAUDITED

    report = validate_integrity(
        [_claim("c1")],
        [_evidence("operator-note", audit_outcome="contaminated")],
    )
    assert report.contamination["operator-note"] is ContaminationOutcome.CONTAMINATED
