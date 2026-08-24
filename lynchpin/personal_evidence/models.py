"""Exact private-evidence vocabulary."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class AuthorshipClass(StrEnum):
    OPERATOR_DIRECT = "operator_direct"
    OPERATOR_QUOTED_OR_FORWARDED = "operator_quoted_or_forwarded"
    THIRD_PARTY_DIRECT = "third_party_direct"
    MACHINE_OBSERVATION = "machine_observation"
    DETERMINISTIC_DERIVATION = "deterministic_derivation"
    MODEL_GENERATED = "model_generated"
    AGENT_GENERATED = "agent_generated"
    UNKNOWN_AUTHORSHIP = "unknown_authorship"


class EpistemicRole(StrEnum):
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


class PrivacyClass(StrEnum):
    RAW_PRIVATE = "raw_private"
    ANALYSIS_PRIVATE = "analysis_private"
    THERAPY_CANDIDATE_PRIVATE = "therapy_candidate_private"
    OPERATOR_REVIEWED_EXPORT = "operator_reviewed_export"


class ClaimStatus(StrEnum):
    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    CONTESTED = "contested"
    RETRACTED = "retracted"
    SUPERSEDED = "superseded"
    MODEL_ONLY_UNSUBSTANTIATED = "model_only_unsubstantiated"
    NOT_FOUND_WITH_COVERAGE = "not_found_with_coverage"
    UNKNOWN = "unknown"


class ClaimEvidenceRelation(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    QUALIFIES = "qualifies"
    CONTEXTUALIZES = "contextualizes"
    DERIVED_FROM = "derived_from"
    DUPLICATES = "duplicates"
    QUOTES = "quotes"
    RETRACTS = "retracts"
    SUPERSEDES = "supersedes"
    ADOPTS_LANGUAGE_FROM = "adopts_language_from"


class TemporalPrecision(StrEnum):
    UNKNOWN = "unknown"
    DATE = "date"
    MINUTE = "minute"
    SECOND = "second"
    INSTANT = "instant"
    INTERVAL = "interval"


@dataclass(frozen=True)
class BitemporalFields:
    event_start: datetime | None
    event_end: datetime | None
    event_time_precision: TemporalPrecision
    asserted_at: datetime | None
    observed_at: datetime | None
    ingested_at: datetime
    valid_from: datetime | None
    valid_to: datetime | None
    superseded_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.event_start and self.event_end and self.event_end < self.event_start:
            raise ValueError("event_end must not precede event_start")
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValueError("valid_to must not precede valid_from")


__all__ = [
    "AuthorshipClass",
    "BitemporalFields",
    "ClaimEvidenceRelation",
    "ClaimStatus",
    "EpistemicRole",
    "PrivacyClass",
    "TemporalPrecision",
]
