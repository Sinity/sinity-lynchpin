"""Immutable declarations and deterministic wire representations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from types import MappingProxyType
from typing import Any, Mapping

JSON = None | bool | int | float | str | list["JSON"] | dict[str, "JSON"]
Window = tuple[date, date]


def _jsonable(value: Any) -> JSON:
    if callable(value):
        raise TypeError("callables are not valid in a serialized convergence plan")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"unsupported plan value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Return the stable JSON wire form used for persistence and digests."""
    return json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _window(value: Window | None) -> dict[str, str] | None:
    return None if value is None else {"start": value[0].isoformat(), "end": value[1].isoformat()}


def _parse_window(value: Mapping[str, str] | None) -> Window | None:
    return None if value is None else (date.fromisoformat(value["start"]), date.fromisoformat(value["end"]))


@dataclass(frozen=True)
class PartitionRef:
    product: str
    partition: str
    generation: str

    def to_dict(self) -> dict[str, str]:
        return {"product": self.product, "partition": self.partition, "generation": self.generation}


@dataclass(frozen=True)
class ArtifactRef:
    product: str
    kind: str
    identity: str
    generation: str
    partitions: tuple[PartitionRef, ...] = ()

    def to_dict(self) -> dict[str, JSON]:
        return {"product": self.product, "kind": self.kind, "identity": self.identity, "generation": self.generation, "partitions": [p.to_dict() for p in self.partitions]}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ArtifactRef":
        return cls(value["product"], value["kind"], value["identity"], value["generation"], tuple(PartitionRef(**item) for item in value.get("partitions", [])))


@dataclass(frozen=True)
class Dependency:
    product: str
    required: bool = True

    def to_dict(self) -> dict[str, JSON]:
        return {"product": self.product, "required": self.required}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Dependency":
        return cls(value["product"], value.get("required", True))


@dataclass(frozen=True)
class ProductSpec:
    product: str
    version: str
    handler: str
    input_generation: str
    output: ArtifactRef
    dependencies: tuple[Dependency, ...] = ()
    payload: Mapping[str, JSON] = field(default_factory=dict)
    raw_read_permission: str = "none"

    def __post_init__(self) -> None:
        if not self.product or not self.handler or not self.input_generation:
            raise ValueError("product, handler, and input_generation are required")
        if callable(self.payload) or any(callable(value) for value in self.payload.values()):
            raise TypeError("product payload cannot contain callables")
        object.__setattr__(self, "dependencies", tuple(self.dependencies))
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))

    def to_dict(self) -> dict[str, JSON]:
        return {"product": self.product, "version": self.version, "handler": self.handler, "input_generation": self.input_generation, "output": self.output.to_dict(), "dependencies": [d.to_dict() for d in self.dependencies], "payload": dict(self.payload), "raw_read_permission": self.raw_read_permission}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ProductSpec":
        return cls(value["product"], value["version"], value["handler"], value["input_generation"], ArtifactRef.from_dict(value["output"]), tuple(Dependency.from_dict(item) for item in value.get("dependencies", [])), value.get("payload", {}), value.get("raw_read_permission", "none"))


@dataclass(frozen=True)
class ConvergenceRequest:
    products: tuple[str, ...]
    requested_window: Window | None = None
    input_generations: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "products", tuple(self.products))
        object.__setattr__(self, "input_generations", MappingProxyType(dict(self.input_generations)))

    def to_dict(self) -> dict[str, JSON]:
        return {"products": list(self.products), "requested_window": _window(self.requested_window), "input_generations": dict(self.input_generations)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ConvergenceRequest":
        return cls(tuple(value["products"]), _parse_window(value.get("requested_window")), value.get("input_generations", {}))


@dataclass(frozen=True)
class PlanStep:
    product: str
    spec: ProductSpec
    dependencies: tuple[str, ...]
    requested_window: Window | None
    effective_window: Window | None
    input_generation: str
    raw_read_permission: str
    output: ArtifactRef

    def to_dict(self) -> dict[str, JSON]:
        return {"product": self.product, "spec": self.spec.to_dict(), "dependencies": list(self.dependencies), "requested_window": _window(self.requested_window), "effective_window": _window(self.effective_window), "input_generation": self.input_generation, "raw_read_permission": self.raw_read_permission, "output": self.output.to_dict()}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PlanStep":
        return cls(value["product"], ProductSpec.from_dict(value["spec"]), tuple(value["dependencies"]), _parse_window(value.get("requested_window")), _parse_window(value.get("effective_window")), value["input_generation"], value["raw_read_permission"], ArtifactRef.from_dict(value["output"]))


@dataclass(frozen=True)
class ConvergencePlan:
    steps: tuple[PlanStep, ...]
    request: ConvergenceRequest

    def to_dict(self) -> dict[str, JSON]:
        return {"request": self.request.to_dict(), "steps": [step.to_dict() for step in self.steps]}

    def to_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ConvergencePlan":
        return cls(tuple(PlanStep.from_dict(item) for item in value["steps"]), ConvergenceRequest.from_dict(value["request"]))

    @classmethod
    def from_json(cls, value: str) -> "ConvergencePlan":
        return cls.from_dict(json.loads(value))

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.to_json().encode()).hexdigest()


def plan_digest(plan: ConvergencePlan) -> str:
    return plan.digest


@dataclass(frozen=True)
class StepResult:
    product: str
    status: str
    reason: str = ""
    artifact: ArtifactRef | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, JSON]:
        return {"product": self.product, "status": self.status, "reason": self.reason, "artifact": self.artifact.to_dict() if self.artifact else None, "error": self.error}
