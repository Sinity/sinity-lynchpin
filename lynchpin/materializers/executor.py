"""Handler registry and step boundary contracts for typed materialization."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping

from .specs import PlanStep, StepResult

Handler = Callable[["StepContext"], Any]


@dataclass(frozen=True)
class HandlerDefinition:
    """Code-owned execution metadata for one plan handler identity."""

    identity: str
    handler: Handler
    raw_read_permission: str = "none"
    window_policy: str = "exact"


@dataclass(frozen=True)
class StepContext:
    step: PlanStep
    dependency_results: Mapping[str, StepResult]
    runtime: Mapping[str, Any] = MappingProxyType({})

    @property
    def payload(self) -> Mapping[str, Any]:
        return self.step.spec.payload


class ClosedHandlerRegistry:
    """Resolve only identities from a code-owned, immutable handler table."""

    def __init__(self, definitions: Mapping[str, HandlerDefinition]) -> None:
        if any(key != value.identity for key, value in definitions.items()):
            raise ValueError("handler table keys must match their identities")
        self._definitions = MappingProxyType(dict(definitions))

    def resolve(self, identity: str) -> HandlerDefinition:
        try:
            return self._definitions[identity]
        except KeyError as exc:
            raise KeyError(f"unregistered convergence handler: {identity}") from exc


def validate_step_contract(step: PlanStep, definition: HandlerDefinition) -> None:
    """Reject undeclared raw reads and handler window widening at the seam."""

    if definition.raw_read_permission != "none" and step.raw_read_permission == "none":
        raise ValueError(f"handler {definition.identity} requires undeclared raw reads for {step.product}")
    if step.effective_window != step.requested_window and definition.window_policy != "bounded":
        raise ValueError(f"handler {definition.identity} widened the requested window for {step.product}")
