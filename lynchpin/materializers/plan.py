"""Dependency closure and deterministic topological planning."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from .specs import ConvergencePlan, ConvergenceRequest, PlanStep, ProductSpec


def validate_acyclic(specs: Mapping[str, ProductSpec]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visiting:
            raise ValueError(f"convergence dependency cycle includes {name}")
        if name in visited:
            return
        if name not in specs:
            raise ValueError(f"unknown convergence product: {name}")
        visiting.add(name)
        for dependency in specs[name].dependencies:
            visit(dependency.product)
        visiting.remove(name)
        visited.add(name)

    for name in sorted(specs):
        visit(name)


class ConvergencePlanner:
    def __init__(self, specs: Iterable[ProductSpec]) -> None:
        declared = tuple(specs)
        values = {spec.product: spec for spec in declared}
        if len(values) != len(declared):
            raise ValueError("convergence product names must be unique")
        validate_acyclic(values)
        self._specs = values

    def plan(self, request: ConvergenceRequest) -> ConvergencePlan:
        closure: set[str] = set()
        ordered: list[str] = []

        def include(name: str) -> None:
            if name not in self._specs:
                raise ValueError(f"unknown convergence product: {name}")
            if name in closure:
                return
            closure.add(name)
            for dependency in sorted(
                self._specs[name].dependencies,
                key=lambda item: item.product,
            ):
                include(dependency.product)
            ordered.append(name)

        for product in sorted(request.products):
            include(product)
        steps: list[PlanStep] = []
        for product in ordered:
            spec = self._specs[product]
            generation = request.input_generations.get(product, spec.input_generation)
            steps.append(PlanStep(product, spec, tuple(sorted(d.product for d in spec.dependencies if d.product in closure)), request.requested_window, request.requested_window, generation, spec.raw_read_permission, spec.output))
        return ConvergencePlan(tuple(steps), request)
