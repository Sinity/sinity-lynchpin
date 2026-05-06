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

    def __hash__(self) -> int:
        return hash(self.name)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Step):
            return self.name == other.name
        return NotImplemented


class DAG:
    """Dependency-aware pipeline runner for analysis refresh flows."""

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

    def run(
        self,
        *,
        dry_run: bool = False,
        up_to: str | None = None,
        on_step: Optional[Callable[[StepResult], None]] = None,
    ) -> list[StepResult]:
        if up_to is not None and up_to not in self._steps:
            raise ValueError(f"Unknown DAG step: {up_to}")
        order = self._topo_order()
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
                if name == up_to:
                    break
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
            if name == up_to:
                break

        return results

    def describe(self) -> str:
        lines = [f"DAG: {self.name}", f"Steps: {len(self._steps)}"]
        for name in self._topo_order():
            step = self._steps[name]
            deps = f" (after: {', '.join(step.depends_on)})" if step.depends_on else ""
            lines.append(f"  - {name}{deps}")
        return "\n".join(lines)
