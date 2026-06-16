"""Dependency-aware DAG execution for the codebase-analysis subsystem."""

from __future__ import annotations

import enum
import time
import traceback
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


class StepStatus(enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class StepResult:
    name: str
    status: StepStatus
    elapsed_seconds: float = 0.0
    result: Any = None
    error: str | None = None


@dataclass
class Step:
    """A named unit of work in an analysis DAG."""

    name: str
    fn: Callable[..., Any]
    depends_on: list[str] = field(default_factory=list)
    # Optional content-key: a callable returning a stable identity of this
    # step's inputs (e.g. a git HEAD sha + code version). When present and
    # unchanged since the last successful run, the step may be memoized
    # (skipped) because its output would be byte-identical. See
    # ``lynchpin.analysis.core.memo``.
    fingerprint: Callable[[], str | None] | None = None

    def __hash__(self) -> int:
        return hash(self.name)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Step):
            return self.name == other.name
        return NotImplemented


class DAG:
    """Dependency-aware pipeline runner for analysis materialization flows."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._steps: dict[str, Step] = {}

    def add(self, step: Step) -> "DAG":
        if step.name in self._steps:
            raise ValueError(f"Duplicate step name: {step.name}")
        self._steps[step.name] = step
        return self

    def _topo_order(self) -> list[str]:
        in_degree: dict[str, int] = defaultdict(int)
        dependents: dict[str, list[str]] = defaultdict(list)
        for step in self._steps.values():
            in_degree.setdefault(step.name, 0)
            for dep in step.depends_on:
                if dep not in self._steps:
                    raise ValueError(f"Step {step.name!r} depends on unknown step {dep!r}")
                dependents[dep].append(step.name)
                in_degree[step.name] += 1

        queue: deque[str] = deque(name for name, degree in in_degree.items() if degree == 0)
        order: list[str] = []
        while queue:
            current = queue.popleft()
            order.append(current)
            for child in dependents[current]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        if len(order) != len(self._steps):
            raise ValueError(f"Cycle detected in DAG {self.name!r}")
        return order

    def _dependency_closure(self, target: str) -> set[str]:
        if target not in self._steps:
            raise ValueError(f"Unknown DAG step: {target}")
        selected: set[str] = set()

        def visit(name: str) -> None:
            if name in selected:
                return
            step = self._steps[name]
            for dep in step.depends_on:
                if dep not in self._steps:
                    raise ValueError(f"Step {name!r} depends on unknown step {dep!r}")
                visit(dep)
            selected.add(name)

        visit(target)
        return selected

    def _selected_order(self, up_to: str | None) -> list[str]:
        order = self._topo_order()
        if up_to is None:
            return order
        selected = self._dependency_closure(up_to)
        return [name for name in order if name in selected]

    def run(
        self,
        *,
        dry_run: bool = False,
        up_to: str | None = None,
        on_step: Optional[Callable[[StepResult], None]] = None,
    ) -> list[StepResult]:
        order = self._selected_order(up_to)
        results: list[StepResult] = []
        failed: set[str] = set()

        for name in order:
            step = self._steps[name]
            blocked_by = [dep for dep in step.depends_on if dep in failed]
            if blocked_by:
                result = StepResult(
                    name=name,
                    status=StepStatus.SKIPPED,
                    error=f"Skipped due to failed dependency: {', '.join(blocked_by)}",
                )
                failed.add(name)
                results.append(result)
                if on_step:
                    on_step(result)
                continue

            if dry_run:
                result = StepResult(name=name, status=StepStatus.PENDING)
                results.append(result)
                if on_step:
                    on_step(result)
                continue

            t0 = time.monotonic()
            try:
                value = step.fn()
                elapsed = time.monotonic() - t0
                result = StepResult(
                    name=name,
                    status=StepStatus.SUCCESS,
                    elapsed_seconds=round(elapsed, 3),
                    result=value,
                )
            except Exception as exc:
                elapsed = time.monotonic() - t0
                result = StepResult(
                    name=name,
                    status=StepStatus.FAILED,
                    elapsed_seconds=round(elapsed, 3),
                    error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
                )
                failed.add(name)

            results.append(result)
            if on_step:
                on_step(result)

        return results

    def run_selected(
        self,
        selected: set[str],
        *,
        up_to: str | None = None,
        on_step: Optional[Callable[[StepResult], None]] = None,
    ) -> list[StepResult]:
        """Run only selected steps while preserving dependency failure semantics."""

        order = self._selected_order(up_to)
        results: list[StepResult] = []
        failed: set[str] = set()

        for name in order:
            step = self._steps[name]
            if name not in selected:
                result = StepResult(
                    name=name,
                    status=StepStatus.SKIPPED,
                    result={
                        "materialization": {
                            "status": "ready",
                            "reason": "materialization plan skipped step",
                        }
                    },
                )
                results.append(result)
                if on_step:
                    on_step(result)
                continue

            blocked_by = [dep for dep in step.depends_on if dep in failed]
            if blocked_by:
                result = StepResult(
                    name=name,
                    status=StepStatus.SKIPPED,
                    error=f"Skipped due to failed dependency: {', '.join(blocked_by)}",
                )
                failed.add(name)
                results.append(result)
                if on_step:
                    on_step(result)
                continue

            t0 = time.monotonic()
            try:
                value = step.fn()
                elapsed = time.monotonic() - t0
                result = StepResult(
                    name=name,
                    status=StepStatus.SUCCESS,
                    elapsed_seconds=round(elapsed, 3),
                    result=value,
                )
            except Exception as exc:
                elapsed = time.monotonic() - t0
                result = StepResult(
                    name=name,
                    status=StepStatus.FAILED,
                    elapsed_seconds=round(elapsed, 3),
                    error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
                )
                failed.add(name)

            results.append(result)
            if on_step:
                on_step(result)

        return results

    def describe(self) -> str:
        lines = [f"DAG: {self.name}", f"Steps: {len(self._steps)}"]
        for name in self._topo_order():
            step = self._steps[name]
            deps = f" (after: {', '.join(step.depends_on)})" if step.depends_on else ""
            lines.append(f"  - {name}{deps}")
        return "\n".join(lines)
