from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pytest

from lynchpin.personal_evidence.integrity import (
    Authorship,
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
        "authorship": "operator_direct",
        "evidence_class": "contemporaneous_self_report",
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
        "authorship": "operator_direct",
        "epistemic_class": "contemporaneous_self_report",
        "status": "supported",
        "evidence_ids": ["operator-note"],
        "scope": ["lamp"],
    }
    record.update(overrides)
    return record


@dataclass
class DataclassEvidence:
    id: str
    authorship_class: str = "machine_observation"
    evidence_class: str = "measured_fact"
    observed_at: date = date(2026, 8, 1)


def _codes(report: object) -> set[str]:
    return {finding.code for finding in report.findings}  # type: ignore[attr-defined]


@pytest.mark.parametrize("authorship", [member.value for member in Authorship])
def test_accepts_every_exact_authorship_value(authorship: str) -> None:
    assert validate_authorship(authorship) is Authorship(authorship)


@pytest.mark.parametrize("epistemic_class", [member.value for member in EpistemicClass])
def test_accepts_every_exact_epistemic_value(epistemic_class: str) -> None:
    assert validate_epistemic_class(epistemic_class) is EpistemicClass(epistemic_class)


def test_rejects_legacy_or_unknown_vocabularies() -> None:
    report = validate_integrity(
        [_claim("c1", authorship="operator", epistemic_class="current_fact")],
        [_evidence("operator-note", authorship="machine", evidence_class="observation")],
    )

    assert _codes(report) >= {"INVALID_AUTHORSHIP", "INVALID_EPISTEMIC_CLASS"}
    assert validate_authorship("operator") is None
    assert validate_epistemic_class("adoption") is None


def test_accepts_primary_lineage_for_mapping_and_dataclass_records() -> None:
    report = validate_integrity(
        [_claim("c1"), _claim("c2", evidence_ids=["sensor"])],
        [_evidence("operator-note"), DataclassEvidence("sensor")],
    )

    assert report.valid
    assert _codes(report) == set()


@pytest.mark.parametrize(
    "authorship",
    [
        "operator_direct",
        "third_party_direct",
        "machine_observation",
        "deterministic_derivation",
    ],
)
def test_each_mission_primary_authorship_can_terminate_a_major_path(authorship: str) -> None:
    report = validate_integrity(
        [_claim("c1")],
        [_evidence("operator-note", authorship=authorship)],
    )

    assert report.valid


@pytest.mark.parametrize(
    "authorship",
    ["operator_quoted_or_forwarded", "unknown_authorship"],
)
def test_nonprimary_authorship_cannot_terminate_a_major_path(authorship: str) -> None:
    report = validate_integrity(
        [_claim("c1", evidence_ids=["nonprimary"])],
        [_evidence("nonprimary", authorship=authorship)],
    )

    assert "MISSING_PRIMARY_EVIDENCE_PATH" in _codes(report)


@pytest.mark.parametrize("authorship", ["model_generated", "agent_generated"])
def test_model_and_agent_only_paths_fail_the_terminal_gate(authorship: str) -> None:
    report = validate_integrity(
        [_claim("c1", evidence_ids=["generated"])],
        [_evidence("generated", authorship=authorship)],
    )

    assert "MISSING_PRIMARY_EVIDENCE_PATH" in _codes(report)
    assert "MODEL_OR_AGENT_TO_OPERATOR_LAUNDERING" in _codes(report)


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


def test_later_operator_adoption_is_valid_only_from_its_adoption_clock() -> None:
    model = _evidence(
        "model-output",
        authorship="model_generated",
        evidence_class="qualified_inference",
        observed_at="2026-08-02",
    )
    adoption = _evidence(
        "operator-adoption",
        parent_ids=["model-output"],
        adopted_at="2026-08-03",
        observed_at="2026-08-03",
    )
    valid = validate_integrity(
        [
            _claim(
                "c1",
                evidence_ids=["operator-adoption"],
                current_endorsement=True,
                valid_from="2026-08-03",
            )
        ],
        [model, adoption],
    )
    backdated = validate_integrity(
        [
            _claim(
                "c2",
                evidence_ids=["operator-adoption"],
                current_endorsement=True,
                valid_from="2026-08-01",
            )
        ],
        [model, adoption],
    )

    assert valid.valid
    assert "ADOPTION_BACKDATING" not in _codes(valid)
    assert "ADOPTION_BACKDATING" in _codes(backdated)


def test_groups_duplicate_shared_prefix_and_quoted_ancestry() -> None:
    groups = independence_groups(
        [
            _evidence("root"),
            _evidence("derived-a", parent_ids=["root"]),
            _evidence("derived-b", parent_ids=["root"]),
            _evidence("copy", duplicate_of="derived-a"),
            _evidence("quote", quoted_from="derived-b"),
            _evidence("separate"),
        ]
    )

    assert frozenset({"root", "derived-a", "derived-b", "copy", "quote"}) in groups
    assert frozenset({"separate"}) in groups


def test_preserves_contradictions_retractions_and_lifecycle_separately() -> None:
    report = validate_integrity(
        [
            _claim("original"),
            _claim("counter", contradicts=["original"], status="contested"),
            _claim(
                "withdrawal",
                epistemic_class="direct_communication",
                status="retracted",
                current_endorsement=False,
                retracts=["original"],
            ),
        ],
        [_evidence("operator-note")],
    )

    assert {relation.claim_id for relation in report.contradictions} == {"counter"}
    assert {relation.claim_id for relation in report.retractions} == {"withdrawal"}
    assert report.valid


def test_rejects_unsupported_answer_card_claim_reuse() -> None:
    report = validate_integrity(
        [_claim("model-claim", evidence_ids=["model-output"], status="model_only_unsubstantiated")],
        [_evidence("model-output", authorship="model_generated")],
        answer_cards=[{"answer_card_id": "card-1", "claim_ids": ["model-claim"]}],
    )

    assert "MISSING_PRIMARY_EVIDENCE_PATH" in _codes(report)
    assert "UNSUPPORTED_ANSWER_CARD_CLAIM_REUSE" in _codes(report)


@pytest.mark.parametrize("outcome", [member.value for member in ContaminationOutcome])
def test_classifies_every_mission_contamination_outcome(outcome: str) -> None:
    assert classify_contamination({"audit_outcome": outcome}) is ContaminationOutcome(outcome)


def test_does_not_invent_an_outcome_for_missing_or_unknown_audit_data() -> None:
    assert classify_contamination({}) is None
    assert classify_contamination({"audit_outcome": "unclear"}) is None
