"""Registered-handler local execution with dependency-aware concurrency."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable

from .specs import ConvergencePlan, PlanStep, StepResult

Handler = Callable[["StepContext"], Any]


@dataclass(frozen=True)
class StepContext:
    step: PlanStep
    dependency_results: dict[str, StepResult]


class HandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}

    def register(self, identity: str, handler: Handler) -> None:
        if not identity or identity in self._handlers:
            raise ValueError(f"handler identity is empty or already registered: {identity!r}")
        self._handlers[identity] = handler

    def resolve(self, identity: str) -> Handler:
        try:
            return self._handlers[identity]
        except KeyError as exc:
            raise KeyError(f"unregistered convergence handler: {identity}") from exc


class LocalExecutor:
    def __init__(self, registry: HandlerRegistry, *, max_workers: int = 4) -> None:
        self.registry = registry
        self.max_workers = max_workers

    def execute(self, plan: ConvergencePlan, *, reuse: set[str] | None = None) -> dict[str, StepResult]:
        reuse = reuse or set()
        steps = {step.product: step for step in plan.steps}
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
                        futures[pool.submit(self._run, step, {dep: results[dep] for dep in step.dependencies})] = name
                    pending.remove(name)
                for future in as_completed(futures):
                    name = futures[future]
                    try:
                        results[name] = future.result()
                    except Exception as exc:  # handler failures are data in the plan result
                        results[name] = StepResult(name, "failed", "handler raised", error=f"{type(exc).__name__}: {exc}")
        return {name: results[name] for name in sorted(results)}

    def _run(self, step: PlanStep, dependencies: dict[str, StepResult]) -> StepResult:
        value = self.registry.resolve(step.spec.handler)(StepContext(step, dependencies))
        if isinstance(value, StepResult):
            return value
        return StepResult(step.product, "succeeded", artifact=step.output)
