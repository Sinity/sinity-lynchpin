"""Typed, serializable convergence planning and local execution."""

from .executor import HandlerRegistry, LocalExecutor, StepContext
from .legacy import describe_existing_materializers
from .plan import ConvergencePlanner, validate_acyclic
from .specs import (
    ArtifactRef,
    ConvergencePlan,
    ConvergenceRequest,
    Dependency,
    PartitionRef,
    PlanStep,
    ProductSpec,
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
    "HandlerRegistry",
    "LocalExecutor",
    "PartitionRef",
    "PlanStep",
    "ProductSpec",
    "StepContext",
    "StepResult",
    "canonical_json",
    "describe_existing_materializers",
    "plan_digest",
    "validate_acyclic",
]
