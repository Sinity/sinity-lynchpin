from __future__ import annotations

import threading
import time
from datetime import date
from types import SimpleNamespace

import pytest

from lynchpin.materializers import (
    ArtifactRef,
    ClosedHandlerRegistry,
    ConvergencePlanner,
    ConvergenceRequest,
    HandlerDefinition,
    LocalExecutor,
    ProductSpec,
    ResourceHints,
    validate_step_contract,
)
from lynchpin.materializers.specs import Dependency, PartitionRef, ConvergencePlan, canonical_json
from lynchpin.materializers.catalog import PRODUCT_CATALOG
from lynchpin.materializers.production import materializer_execution_waves, plan_materializations


def spec(name: str, *, dependencies: tuple[str, ...] = (), handler: str | None = None, raw: str = "none") -> ProductSpec:
    return ProductSpec(
        name,
        "1",
        handler or f"test:{name}",
        "input-7",
        ArtifactRef(name, "partitioned", f"artifact-{name}", "output-3", (PartitionRef(name, "2026-01-01", "input-7"),)),
        tuple(Dependency(item) for item in dependencies),
        {"limit": 10},
        raw,
        resources=ResourceHints(reads=(f"owner-native:{name}",) if raw != "none" else (), writes=(f"canonical-product:{name}",), exclusive=(f"canonical-product:{name}",)),
    )


def planned(*names: ProductSpec) -> ConvergencePlan:
    return ConvergencePlanner(names).plan(ConvergenceRequest((names[-1].product,), (date(2026, 1, 1), date(2026, 1, 3))))


def registry(*handlers):
    return ClosedHandlerRegistry({identity: HandlerDefinition(identity, fn) for identity, fn in handlers})


def test_plan_round_trip_and_digest_are_deterministic() -> None:
    plan = planned(spec("base"), spec("child", dependencies=("base",)))
    assert ConvergencePlan.from_json(plan.to_json()).to_json() == plan.to_json()
    assert plan.digest == ConvergencePlan.from_json(plan.to_json()).digest
    assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'
    assert all(not callable(value) for step in plan.steps for value in step.spec.payload.values())


def test_cycle_rejected_and_dependency_closure_is_explicit() -> None:
    with pytest.raises(ValueError, match="cycle"):
        ConvergencePlanner((spec("a", dependencies=("b",)), spec("b", dependencies=("a",))))
    plan = planned(spec("base"), spec("middle", dependencies=("base",)), spec("target", dependencies=("middle",)))
    assert [step.product for step in plan.steps] == ["base", "middle", "target"]
    assert plan.steps[-1].dependencies == ("middle",)


def test_independent_steps_run_in_parallel() -> None:
    entered: list[str] = []
    lock = threading.Lock()

    def work(context):
        with lock:
            entered.append(context.step.product)
        time.sleep(0.05)

    handlers = [(f"test:{name}", work) for name in ("a", "b")]
    plan = ConvergencePlanner((spec("a"), spec("b"))).plan(ConvergenceRequest(("a", "b")))
    started = time.monotonic()
    results = LocalExecutor(registry(*handlers), max_workers=2).execute(plan)
    elapsed = time.monotonic() - started
    assert {name: result.status for name, result in results.items()} == {"a": "succeeded", "b": "succeeded"}
    assert set(entered) == {"a", "b"}
    assert elapsed < 0.09


def test_failure_propagates_as_explicit_skip_and_reuse_is_reported() -> None:
    def broken(_context):
        raise RuntimeError("broken")

    plan = planned(spec("base"), spec("child", dependencies=("base",)))
    results = LocalExecutor(registry(("test:base", broken), ("test:child", lambda _context: None))).execute(plan)
    assert results["base"].status == "failed"
    assert results["child"].status == "skipped"
    assert results["child"].reason == "dependency base failed"

    reused = LocalExecutor(registry(("test:base", lambda _context: None), ("test:child", lambda _context: None))).execute(plan, reuse={"base"})
    assert reused["base"].status == "reused"
    assert reused["child"].status == "succeeded"


def test_serialized_plan_rejects_arbitrary_callable() -> None:
    with pytest.raises(TypeError, match="callable"):
        ProductSpec("bad", "1", "test:bad", "g", ArtifactRef("bad", "file", "x", "g"), payload={"fn": lambda: None})


def test_undeclared_raw_reads_and_window_widening_are_rejected() -> None:
    raw_spec = spec("raw", raw="none")
    raw_plan = ConvergencePlanner((raw_spec,)).plan(ConvergenceRequest(("raw",), (date(2026, 1, 1), date(2026, 1, 2))))
    with pytest.raises(ValueError, match="undeclared raw"):
        validate_step_contract(raw_plan.steps[0], HandlerDefinition("test:raw", lambda _context: None, raw_read_permission="owner-native"))

    widened = replace_step(raw_plan.steps[0], effective_window=(date(2025, 1, 1), date(2026, 1, 2)))
    with pytest.raises(ValueError, match="widened"):
        validate_step_contract(widened, HandlerDefinition("test:raw", lambda _context: None))


def replace_step(step, **changes):
    from dataclasses import replace

    return replace(step, **changes)


def test_old_callable_registry_and_step_fn_apis_are_absent() -> None:
    from lynchpin import materialization
    from lynchpin.analysis.core.dag import Step

    assert not hasattr(materialization, "_materializers")
    assert not hasattr(Step, "fn")
    assert not hasattr(ClosedHandlerRegistry, "register")


def test_production_catalog_and_analysis_plans_are_callable_free() -> None:
    from lynchpin.analysis.materialize import current_state_dag

    assert PRODUCT_CATALOG
    assert all(spec.phase and spec.input_generation and spec.output and spec.resources for spec in PRODUCT_CATALOG.values())
    dag = current_state_dag(start=date(2026, 1, 1), end=date(2026, 1, 2))
    assert all(not callable(value) for step in dag._steps.values() for value in step.payload.values())
    assert all(step.handler.startswith("analysis:") for step in dag._steps.values())


def test_production_plans_are_serializable_and_fully_declared(monkeypatch) -> None:
    from lynchpin import materialization

    rows = [
        SimpleNamespace(name=name, status="pending", reason="test")
        for name in PRODUCT_CATALOG
    ]
    monkeypatch.setattr(materialization, "audit_materialization", lambda **_kwargs: rows)
    plan = plan_materializations(cfg=object(), force=True)

    assert len(plan) == len(PRODUCT_CATALOG)
    assert all(
        {
            "phase",
            "input_generation",
            "requested_window",
            "effective_window",
            "raw_read_permission",
            "output",
            "dependencies",
            "resources",
        }
        <= set(step.to_json())
        for step in plan
    )
    assert canonical_json([step.to_json() for step in plan])


def test_resource_and_dependency_order_is_deterministic() -> None:
    specs = (spec("z", dependencies=("a",)), spec("a"), spec("b"))
    plan = ConvergencePlanner(specs).plan(ConvergenceRequest(("z", "b")))
    waves = materializer_execution_waves(tuple(replace_step(step, action="materialize") for step in plan.steps))
    assert [[step.product for step in wave] for wave in waves] == [["a", "b"], ["z"]]
