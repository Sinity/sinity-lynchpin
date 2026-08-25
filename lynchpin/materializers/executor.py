"""Closed-registry execution for serializable convergence plans."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping

from .specs import ConvergencePlan, PlanStep, StepResult

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


class LocalExecutor:
    def __init__(self, registry: ClosedHandlerRegistry, *, max_workers: int = 4) -> None:
        self.registry = registry
        self.max_workers = max_workers

    def execute(
        self,
        plan: ConvergencePlan,
        *,
        reuse: set[str] | None = None,
        runtime: Mapping[str, Any] | None = None,
    ) -> dict[str, StepResult]:
        reuse = reuse or set()
        steps = {step.product: step for step in plan.steps}
        if len(steps) != len(plan.steps):
            raise ValueError("plan product names must be unique")
        results: dict[str, StepResult] = {}
        pending = set(steps)
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            while pending:
                ready = sorted(name for name in pending if all(dep in results for dep in steps[name].dependencies))
                if not ready:
                    raise ValueError("plan contains an unresolved dependency")
                futures = {}
                for name in ready:
                    step = steps[name]
                    failed = next((results[dep] for dep in step.dependencies if results[dep].status in {"failed", "skipped"}), None)
                    if failed is not None:
                        results[name] = StepResult(name, "skipped", f"dependency {failed.product} {failed.status}")
                    elif name in reuse:
                        results[name] = StepResult(name, "reused", "existing artifact reused", step.output)
                    else:
                        futures[pool.submit(self._run, step, {dep: results[dep] for dep in step.dependencies}, runtime or {})] = name
                    pending.remove(name)
                for future in as_completed(futures):
                    name = futures[future]
                    try:
                        results[name] = future.result()
                    except Exception as exc:  # handler failures are data in the plan result
                        results[name] = StepResult(name, "failed", "handler raised", error=f"{type(exc).__name__}: {exc}")
        return {name: results[name] for name in sorted(results)}

    def _run(self, step: PlanStep, dependencies: dict[str, StepResult], runtime: Mapping[str, Any]) -> StepResult:
        definition = self.registry.resolve(step.spec.handler)
        validate_step_contract(step, definition)
        value = definition.handler(StepContext(step, dependencies, runtime))
        if isinstance(value, StepResult):
            return value
        return StepResult(step.product, "succeeded", artifact=step.output)
