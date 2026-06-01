from __future__ import annotations

from lynchpin.analysis.claim_calibration import calibrate_claims


def test_claim_calibration_flags_missing_evidence_and_causal_overclaim() -> None:
    report = calibrate_claims([
        {
            "claim_id": "claim:1",
            "support_level": "strong",
            "confidence": 0.6,
            "summary": "X causes slower builds",
            "source_ids": [],
            "relation_ids": [],
            "caveats": [],
            "payload": {},
        },
        {
            "claim_id": "claim:2",
            "support_level": "moderate",
            "confidence": 0.65,
            "summary": "Y is associated with slower builds",
            "source_ids": ["node:1"],
            "relation_ids": [],
            "caveats": ["observational"],
            "payload": {},
        },
    ])

    assert report.claim_count == 2
    assert report.evidence_backed_count == 1
    assert report.issue_counts["missing_evidence_ids"] == 1
    assert report.issue_counts["support_confidence_mismatch"] == 1
    assert report.issue_counts["causal_language_without_control"] == 1
