"""Dependency-aware execution for serializable analysis DAG declarations."""

from __future__ import annotations

import enum
import time
import traceback
from collections import defaultdict, deque
from dataclasses import dataclass, field
from types import MappingProxyType
from datetime import date
from typing import Any, Callable, Mapping, Protocol

from lynchpin.materializers.specs import canonical_json


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


class HandlerResolver(Protocol):
    def resolve(self, identity: str) -> Callable[..., Any]: ...


@dataclass(frozen=True)
class Step:
    """A serializable unit of work in an analysis DAG."""

    name: str
    handler: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()
    # Stable content key supplied by the planner, not a runtime callable.
    fingerprint: str | None = None
    phase: str = "analysis"
    input_generation: str = "analysis-code-v1"
    requested_window: tuple[date, date] | None = None
    effective_window: tuple[date, date] | None = None
    raw_read_permission: str = "declared"
    output_artifact: str | None = None
    resources: tuple[str, ...] = ()
    exclusive: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name or not self.handler:
            raise ValueError("analysis step name and handler are required")
        canonical_json(self.payload)
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))
        object.__setattr__(self, "depends_on", tuple(self.depends_on))

    def __hash__(self) -> int:
        return hash(self.name)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Step):
            return self.name == other.name
        return NotImplemented

    def to_dict(self) -> dict[str, Any]:
        def window(value: tuple[date, date] | None) -> dict[str, str] | None:
            return None if value is None else {"start": value[0].isoformat(), "end": value[1].isoformat()}

        return {
            "name": self.name,
            "handler": self.handler,
            "payload": dict(self.payload),
            "depends_on": list(self.depends_on),
            "fingerprint": self.fingerprint,
            "phase": self.phase,
            "input_generation": self.input_generation,
            "requested_window": window(self.requested_window),
            "effective_window": window(self.effective_window),
            "raw_read_permission": self.raw_read_permission,
            "output_artifact": self.output_artifact,
            "resources": list(self.resources),
            "exclusive": list(self.exclusive),
        }


class DAG:
    """Dependency-aware pipeline runner with a closed handler resolver."""

    def __init__(self, name: str, *, handlers: HandlerResolver | None = None) -> None:
        self.name = name
        self._steps: dict[str, Step] = {}
        self._handlers = handlers

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

        # Dict insertion order is the catalog's explicit phase order; sorting
        # dependents below removes set/hash nondeterminism without moving an
        # intentionally first barrier behind an unrelated lexical name.
        queue: deque[str] = deque(name for name, degree in in_degree.items() if degree == 0)
        order: list[str] = []
        while queue:
            current = queue.popleft()
            order.append(current)
            for child in sorted(dependents[current]):
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

    def _resolve(self, identity: str) -> Callable[..., Any]:
        handlers = self._handlers
        if handlers is None:
            from ..handlers import handler_registry

            handlers = handler_registry()
        return handlers.resolve(identity)

    def _execute_step(self, step: Step) -> Any:
        payload = step.payload
        args = payload.get("args", ())
        kwargs = payload.get("kwargs", {})
        if not isinstance(args, (tuple, list)) or not isinstance(kwargs, Mapping):
            raise ValueError(f"analysis step {step.name} payload must contain args and kwargs")
        return self._resolve(step.handler)(*args, **dict(kwargs))

    def _run_one(self, step: Step) -> StepResult:
        t0 = time.monotonic()
        try:
            value = self._execute_step(step)
            return StepResult(step.name, StepStatus.SUCCESS, round(time.monotonic() - t0, 3), value)
        except Exception as exc:
            return StepResult(step.name, StepStatus.FAILED, round(time.monotonic() - t0, 3), error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")

    def run(
        self,
        *,
        dry_run: bool = False,
        up_to: str | None = None,
        on_step: Callable[[StepResult], None] | None = None,
    ) -> list[StepResult]:
        return self.run_selected(set(self._selected_order(up_to)), dry_run=dry_run, on_step=on_step, up_to=up_to)

    def run_selected(
        self,
        selected: set[str],
        *,
        dry_run: bool = False,
        up_to: str | None = None,
        on_step: Callable[[StepResult], None] | None = None,
    ) -> list[StepResult]:
        """Run only selected steps while preserving dependency failure semantics."""

        results: list[StepResult] = []
        failed: set[str] = set()
        for name in self._selected_order(up_to):
            step = self._steps[name]
            if name not in selected:
                result = StepResult(name=name, status=StepStatus.SKIPPED, result={"materialization": {"status": "ready", "reason": "materialization plan skipped step"}})
            elif dry_run:
                result = StepResult(name=name, status=StepStatus.PENDING)
            else:
                blocked_by = [dep for dep in step.depends_on if dep in failed]
                if blocked_by:
                    result = StepResult(name=name, status=StepStatus.SKIPPED, error=f"Skipped due to failed dependency: {', '.join(blocked_by)}")
                    failed.add(name)
                else:
                    result = self._run_one(step)
                    if result.status == StepStatus.FAILED:
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
            lines.append(f"  - {name} [{step.handler}]{deps}")
        return "\n".join(lines)
