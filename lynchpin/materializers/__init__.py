"""Typed, serializable convergence planning and local execution."""

from .executor import ClosedHandlerRegistry, HandlerDefinition, LocalExecutor, StepContext, validate_step_contract
from .plan import ConvergencePlanner, validate_acyclic
from .specs import (
    ArtifactRef,
    ConvergencePlan,
    ConvergenceRequest,
    Dependency,
    PartitionRef,
    PlanStep,
    ProductSpec,
    ResourceHints,
    StepResult,
    canonical_json,
    plan_digest,
)

__all__ = [
    "ArtifactRef",
    "ConvergencePlan",
    "ConvergencePlanner",
    "ConvergenceRequest",
    "Dependency",
    "ClosedHandlerRegistry",
    "HandlerDefinition",
    "LocalExecutor",
    "PartitionRef",
    "PlanStep",
    "ProductSpec",
    "ResourceHints",
    "StepContext",
    "StepResult",
    "canonical_json",
    "plan_digest",
    "validate_step_contract",
    "validate_acyclic",
]
