from __future__ import annotations

import threading
import time
from datetime import date

import pytest

from lynchpin.materializers import (
    ArtifactRef,
    ConvergencePlanner,
    ConvergenceRequest,
    HandlerRegistry,
    LocalExecutor,
    ProductSpec,
)
from lynchpin.materializers.specs import Dependency, PartitionRef, ConvergencePlan, canonical_json


def spec(name: str, *, dependencies: tuple[str, ...] = (), handler: str | None = None) -> ProductSpec:
    return ProductSpec(
        name,
        "1",
        handler or f"test:{name}",
        "input-7",
        ArtifactRef(name, "partitioned", f"artifact-{name}", "output-3", (PartitionRef(name, "2026-01-01", "input-7"),)),
        tuple(Dependency(item) for item in dependencies),
        {"limit": 10},
        "declared-raw" if name == "raw" else "none",
    )


def planned(*names: ProductSpec) -> ConvergencePlan:
    return ConvergencePlanner(names).plan(ConvergenceRequest((names[-1].product,), (date(2026, 1, 1), date(2026, 1, 3))))


def test_plan_round_trip_and_digest_are_deterministic() -> None:
    plan = planned(spec("base"), spec("child", dependencies=("base",)))
    assert ConvergencePlan.from_json(plan.to_json()).to_json() == plan.to_json()
    assert plan.digest == ConvergencePlan.from_json(plan.to_json()).digest
    assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'


def test_cycle_rejected_and_dependency_closure_is_explicit() -> None:
    with pytest.raises(ValueError, match="cycle"):
        ConvergencePlanner((spec("a", dependencies=("b",)), spec("b", dependencies=("a",))))
    plan = planned(spec("base"), spec("middle", dependencies=("base",)), spec("target", dependencies=("middle",)))
    assert [step.product for step in plan.steps] == ["base", "middle", "target"]
    assert plan.steps[-1].dependencies == ("middle",)

    non_alphabetic = planned(
        spec("z-base"),
        spec("a-target", dependencies=("z-base",)),
    )
    assert [step.product for step in non_alphabetic.steps] == ["z-base", "a-target"]


def test_independent_steps_run_in_parallel() -> None:
    entered: list[str] = []
    lock = threading.Lock()
    registry = HandlerRegistry()
    for name in ("a", "b"):
        registry.register(f"test:{name}", lambda _context, name=name: (lock.acquire(), entered.append(name), lock.release(), time.sleep(0.05)))
    plan = ConvergencePlanner((spec("a"), spec("b"))).plan(ConvergenceRequest(("a", "b")))
    started = time.monotonic()
    results = LocalExecutor(registry, max_workers=2).execute(plan)
    elapsed = time.monotonic() - started
    assert {name: result.status for name, result in results.items()} == {"a": "succeeded", "b": "succeeded"}
    assert set(entered) == {"a", "b"}
    assert elapsed < 0.09


def test_failure_propagates_as_explicit_skip_and_reuse_is_reported() -> None:
    registry = HandlerRegistry()
    registry.register("test:base", lambda _context: (_ for _ in ()).throw(RuntimeError("broken")))
    registry.register("test:child", lambda _context: pytest.fail("child must not run"))
    plan = planned(spec("base"), spec("child", dependencies=("base",)))
    results = LocalExecutor(registry).execute(plan)
    assert results["base"].status == "failed"
    assert results["child"].status == "skipped"
    assert results["child"].reason == "dependency base failed"

    registry = HandlerRegistry()
    registry.register("test:base", lambda _context: None)
    registry.register("test:child", lambda _context: None)
    reused = LocalExecutor(registry).execute(plan, reuse={"base"})
    assert reused["base"].status == "reused"
    assert reused["child"].status == "succeeded"


def test_serialized_plan_rejects_arbitrary_callable() -> None:
    with pytest.raises(TypeError, match="callable"):
        ProductSpec("bad", "1", "test:bad", "g", ArtifactRef("bad", "file", "x", "g"), payload={"fn": lambda: None})


def test_legacy_adapter_only_describes_registered_names(monkeypatch: pytest.MonkeyPatch) -> None:
    from lynchpin import materialization

    monkeypatch.setattr(materialization, "_materializers", lambda: {"legacy": lambda: None})
    described = materialization.describe_existing_materializers()
    assert [item.product for item in described] == ["legacy"]
    assert described[0].handler == "lynchpin.materialization:legacy"
