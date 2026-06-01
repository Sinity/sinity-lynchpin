"""Causal-model validation for machine attribution plans and claims."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class MachineCausalModelAssessment:
    status: str
    support_ceiling: str
    issues: tuple[str, ...]
    warnings: tuple[str, ...]
    treatment_variable: str | None
    outcome_variable: str | None
    blocking_variables: tuple[str, ...]
    adjustment_variables: tuple[str, ...]
    forbidden_post_treatment_variables: tuple[str, ...]
    known_unobserved_confounders: tuple[str, ...]
    identification_note: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def assess_causal_model(
    model: dict[str, Any],
    *,
    support_ceiling: str,
) -> MachineCausalModelAssessment:
    """Assess whether a compact causal model can support the requested design."""
    treatment = _str_or_none(model.get("treatment_variable"))
    outcome = _str_or_none(model.get("outcome_variable"))
    blocking = _string_tuple(model.get("blocking_variables"))
    adjustment = _string_tuple(model.get("adjustment_variables"))
    forbidden = _string_tuple(model.get("forbidden_post_treatment_variables"))
    unobserved = _string_tuple(model.get("known_unobserved_confounders"))
    identification = _str_or_none(model.get("identification_note"))

    issues: list[str] = []
    warnings: list[str] = []
    if treatment is None:
        issues.append("causal_model missing treatment_variable")
    if outcome is None:
        issues.append("causal_model missing outcome_variable")

    post_treatment_adjustments = sorted(set(adjustment) & set(forbidden))
    if post_treatment_adjustments:
        issues.append(
            "causal_model adjustment_variables include forbidden post-treatment variables: "
            + ", ".join(post_treatment_adjustments)
        )

    if not blocking:
        warnings.append("causal_model has no blocking_variables; support depends on unblocked comparability")
    if not adjustment:
        warnings.append("causal_model has no adjustment_variables; observational support should remain capped")
    if not forbidden:
        warnings.append("causal_model has no forbidden_post_treatment_variables; leakage guard is incomplete")
    if unobserved:
        warnings.append(
            "causal_model names unobserved confounders: "
            + ", ".join(unobserved[:4])
        )
    if support_ceiling == "controlled" and identification is None:
        warnings.append("causal_model missing identification_note for controlled support")

    return MachineCausalModelAssessment(
        status="failed" if issues else "passed",
        support_ceiling=support_ceiling,
        issues=tuple(issues),
        warnings=tuple(warnings),
        treatment_variable=treatment,
        outcome_variable=outcome,
        blocking_variables=blocking,
        adjustment_variables=adjustment,
        forbidden_post_treatment_variables=forbidden,
        known_unobserved_confounders=unobserved,
        identification_note=identification,
    )


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value if str(item).strip())


def _str_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "MachineCausalModelAssessment",
    "assess_causal_model",
]
