from __future__ import annotations

import json
import threading
from contextvars import ContextVar
from datetime import date
from types import SimpleNamespace

import pytest

from lynchpin.materializers import (
    ArtifactRef,
    ClosedHandlerRegistry,
    ConvergencePlanner,
    ConvergenceRequest,
    HandlerDefinition,
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


def production_harness(monkeypatch, handlers, receipts):
    from lynchpin.materializers import production

    monkeypatch.setattr(production, "handler_registry", lambda: registry(*handlers))
    monkeypatch.setattr(production, "_audit", lambda: SimpleNamespace(
        _record_materialization_step=lambda _refresh, product, status, *_args, **_kwargs: receipts.append((product, status)),
        _int_or_none=lambda value: value,
        _window_payload=lambda value: [item.isoformat() for item in value] if value else None,
        _PRODUCT_REFRESHED_AT={},
        monotonic=lambda: 1.0,
    ))


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

    non_alphabetic = planned(
        spec("z-base"),
        spec("a-target", dependencies=("z-base",)),
    )
    assert [step.product for step in non_alphabetic.steps] == ["z-base", "a-target"]


def test_personal_signals_plan_reconverges_communications_first() -> None:
    plan = ConvergencePlanner(PRODUCT_CATALOG.values()).plan(
        ConvergenceRequest(
            ("personal_daily_signals",),
            (date(2026, 1, 1), date(2026, 1, 3)),
        )
    )
    products = [step.product for step in plan.steps]
    assert products.index("communications") < products.index("personal_daily_signals")


def test_production_starts_dependent_before_unrelated_slow_step_finishes(monkeypatch) -> None:
    from lynchpin.materializers import production

    entered: list[str] = []
    child_started = threading.Event()
    context_value = ContextVar("materialization_test_context", default="missing")
    observed: list[str] = []

    def work(context):
        entered.append(context.step.product)
        observed.append(context_value.get())
        if context.step.product == "slow":
            assert child_started.wait(1), "dependent waited for unrelated work"
        if context.step.product == "child":
            assert context.dependency_results["parent"].status == "succeeded"
            child_started.set()

    receipts = []
    production_harness(monkeypatch, [(f"test:{name}", work) for name in ("parent", "slow", "child")], receipts)
    plan = ConvergencePlanner((spec("parent"), spec("slow"), spec("child", dependencies=("parent",)))).plan(ConvergenceRequest(("child", "slow")))
    steps = tuple(replace_step(step, action="materialize") for step in plan.steps)
    token = context_value.set("candidate")
    try:
        assert {step.product for step in production.run_materialization_plan(steps)} == {"parent", "slow", "child"}
    finally:
        context_value.reset(token)
    assert set(entered) == {"parent", "slow", "child"}
    assert observed == ["candidate"] * 3


def test_production_exclusive_writers_do_not_overlap(monkeypatch) -> None:
    from lynchpin.materializers import production
    from lynchpin.materializers.specs import ResourceHints

    active = 0
    overlaps = []
    lock = threading.Lock()

    def work(_context):
        nonlocal active
        with lock:
            active += 1
            overlaps.append(active)
        threading.Event().wait(0.02)
        with lock:
            active -= 1

    receipts = []
    production_harness(monkeypatch, [("test:a", work), ("test:b", work)], receipts)
    plan = ConvergencePlanner((spec("a"), spec("b"))).plan(ConvergenceRequest(("a", "b")))
    steps = tuple(replace_step(step, action="materialize", resources=ResourceHints(exclusive=("shared-writer",))) for step in plan.steps)

    assert {step.product for step in production.run_materialization_plan(steps)} == {"a", "b"}
    assert overlaps == [1, 1]


def test_production_failure_skips_dependent_and_reports_reuse(monkeypatch) -> None:
    from lynchpin.core.errors import MaterializationError
    from lynchpin.materializers import production

    calls = []
    receipts = []
    progress = []

    def broken(_context):
        raise RuntimeError("broken")

    plan = planned(spec("base"), spec("child", dependencies=("base",)))
    steps = tuple(replace_step(step, action="materialize") for step in plan.steps)
    production_harness(monkeypatch, [("test:base", broken), ("test:child", lambda _context: calls.append("child"))], receipts)
    with pytest.raises(MaterializationError, match="base, child"):
        production.run_materialization_plan(steps, continue_on_error=True, progress=progress.append)
    assert calls == []
    assert ("base", "error") in receipts
    assert ("child", "skipped") in receipts
    assert {(event["product"], event["event"]) for event in progress} == {
        ("base", "started"), ("base", "failed"), ("child", "skipped")
    }
    assert all(event["project"] == "lynchpin" and event["refresh_id"] for event in progress)
    assert progress[0]["queue_wait_seconds"] >= 0

    production_harness(monkeypatch, [("test:child", lambda context: calls.append(context.dependency_results["base"].status))], receipts)
    reused = (replace_step(steps[0], action="skip", status="ready"), steps[1])
    assert production.run_materialization_plan(reused) == [steps[1]]
    assert calls == ["reused"]


def test_progress_callback_failure_does_not_change_materialization_result(monkeypatch) -> None:
    from lynchpin.materializers import production

    receipts = []
    production_harness(monkeypatch, [("test:a", lambda _context: None)], receipts)
    step = replace_step(planned(spec("a")).steps[0], action="materialize")

    def broken_progress(_event):
        raise OSError("log sink unavailable")

    assert production.run_materialization_plan((step,), progress=broken_progress) == [step]
    assert ("a", "ok") in receipts


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
        SimpleNamespace(
            name=name,
            status="pending",
            reason="test",
            first_date=None,
            last_date=None,
            covered_dates=(),
            row_count=0,
            materialized_paths=(),
            raw_roots=(),
            tail_stale=False,
            repair_required=False,
        )
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


def test_nightly_maintenance_does_not_rebuild_chisel(monkeypatch) -> None:
    from lynchpin import materialization

    row = SimpleNamespace(
        name="code_snapshots", status="missing", reason="new Git ref",
        first_date=None, last_date=None, covered_dates=(), row_count=0,
        materialized_paths=(), raw_roots=(), tail_stale=False,
        repair_required=False,
    )
    monkeypatch.setattr(materialization, "audit_materialization", lambda **_kwargs: [row])

    nightly = plan_materializations(cfg=object(), maintenance=True)
    explicit = plan_materializations(cfg=object())
    assert [(step.product, step.action) for step in nightly] == [("code_snapshots", "check-only")]
    assert [(step.product, step.action) for step in explicit] == [("code_snapshots", "materialize")]


def test_live_machine_source_does_not_rebuild_offline_fallback_on_read_or_maintenance(monkeypatch) -> None:
    from lynchpin import materialization
    from lynchpin.core.source_contracts import source_contract
    from lynchpin.materializers import production

    row = SimpleNamespace(
        name="machine", status="ready", reason="live SQLite is active; offline fallback is stale",
        first_date=date(2026, 1, 1), last_date=date(2026, 8, 1), covered_dates=(), row_count=1,
        materialized_paths=(), raw_roots=(), tail_stale=True, repair_required=False,
    )
    monkeypatch.setattr(materialization, "audit_materialization", lambda **_kwargs: [row])

    nightly = plan_materializations(cfg=object(), maintenance=True)
    explicit = plan_materializations(cfg=object(), force=True)

    assert source_contract("machine").materialization_mode == "live"
    assert source_contract("machine").materialization_executor.kind == "none"
    assert [(step.product, step.action) for step in nightly] == [("machine", "check-only")]
    assert [(step.product, step.action) for step in explicit] == [("machine", "materialize")]
    missing = SimpleNamespace(**{**vars(row), "status": "missing", "reason": "no live source or offline copy"})
    monkeypatch.setattr(materialization, "audit_materialization", lambda **_kwargs: [missing])
    assert [(step.product, step.action) for step in plan_materializations(cfg=object(), maintenance=True)] == [("machine", "check-only")]

    monkeypatch.setattr(materialization, "_audit_one", lambda *_args, **_kwargs: row)
    monkeypatch.setattr(materialization, "_materialized_enough_for_window", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(materialization, "_materialization_result", lambda _row, **kwargs: kwargs)
    monkeypatch.setattr(production, "handler_registry", lambda: (_ for _ in ()).throw(AssertionError("offline rebuild")))
    read = production.ensure_materialized("machine", cfg=object(), window=(date(2026, 8, 1), date(2026, 8, 2)))
    assert read["status"] == "ready"
    assert read["changed"] is False


def test_nightly_maintenance_advances_a_long_source_backlog_in_bounded_chunks(monkeypatch, tmp_path) -> None:
    from lynchpin.materializers import production

    end = date(2026, 5, 1)
    row = SimpleNamespace(
        name="atuin", status="partial", reason="new source days",
        first_date=date(2026, 1, 1), last_date=date(2026, 1, 10),
        materialized_paths=(tmp_path / "atuin.manifest.json",), repair_required=False, tail_stale=False,
    )
    audit = SimpleNamespace(
        audit_materialization=lambda **_kwargs: [row],
        _dataset_fingerprint=lambda _row: "fixture",
        source_contract=lambda _name: SimpleNamespace(materialization_hint="fixture"),
        _record_materialization_step=lambda *_args, **_kwargs: None,
        _int_or_none=lambda value: value,
        _window_payload=lambda value: [day.isoformat() for day in value] if value else None,
        _PRODUCT_REFRESHED_AT={},
        monotonic=lambda: 1.0,
    )
    monkeypatch.setattr(production, "_audit", lambda: audit)
    product = PRODUCT_CATALOG["atuin"]
    fail_once = True

    def materialize(context):
        nonlocal fail_once
        if fail_once:
            fail_once = False
            raise RuntimeError("temporary source failure")
        row.status = "ready"
        row.tail_stale = False
        row.materialized_paths[0].write_text(json.dumps({
            "window_start": context.step.effective_window[0].isoformat(),
            "window_end": context.step.effective_window[1].isoformat(),
        }))
        return {"row_count": 1}

    monkeypatch.setattr(production, "handler_registry", lambda: ClosedHandlerRegistry({
        product.handler: HandlerDefinition(
            product.handler, materialize,
            raw_read_permission=product.raw_read_permission,
            window_policy=product.window_policy,
        ),
    }))

    first = production.plan_materializations(cfg=object(), maintenance=True, maintenance_end=end)[0]
    with pytest.raises(RuntimeError, match="temporary source failure"):
        production.run_materialization_plan((first,))
    assert production.plan_materializations(cfg=object(), maintenance=True, maintenance_end=end)[0].effective_window == first.effective_window

    windows = []
    for _ in range(5):
        step = production.plan_materializations(cfg=object(), maintenance=True, maintenance_end=end)[0]
        assert step.action == "materialize"
        assert step.effective_window is not None
        start, chunk_end = step.effective_window
        assert row.last_date < chunk_end <= end
        assert (chunk_end - start).days <= production._INCREMENTAL_MAX_CATCHUP_DAYS
        windows.append(step.effective_window)
        assert production.run_materialization_plan((step,)) == [step]
        if chunk_end == end:
            break

    assert len(windows) > 1
    assert windows[-1][1] == end
    assert [item[1] for item in windows] == sorted(item[1] for item in windows)
    assert len({item[0] for item in windows}) == len(windows)
    assert production.plan_materializations(cfg=object(), maintenance=True, maintenance_end=end) == []


def test_resource_and_dependency_order_is_deterministic() -> None:
    specs = (spec("z", dependencies=("a",)), spec("a"), spec("b"))
    plan = ConvergencePlanner(specs).plan(ConvergenceRequest(("z", "b")))
    waves = materializer_execution_waves(tuple(replace_step(step, action="materialize") for step in plan.steps))
    assert [[step.product for step in wave] for wave in waves] == [["a", "b"], ["z"]]


def test_substrate_promoters_are_not_scheduled_in_one_wave() -> None:
    """The shared DuckDB candidate writer admits one promoter at a time."""
    plan = ConvergencePlanner(
        (PRODUCT_CATALOG["code_snapshots"], PRODUCT_CATALOG["github_context"])
    ).plan(ConvergenceRequest(("code_snapshots", "github_context")))
    steps = tuple(replace_step(step, action="materialize") for step in plan.steps)
    waves = materializer_execution_waves(steps)
    assert [[step.product for step in wave] for wave in waves] == [["code_snapshots"], ["github_context"]]


def test_production_consumers_follow_their_canonical_inputs() -> None:
    for consumer, producer in (
        ("communications", "facebook_messenger"),
        ("spotify_daily", "spotify"),
        ("sleep_productivity", "activitywatch_derived"),
    ):
        assert producer in {dependency.product for dependency in PRODUCT_CATALOG[consumer].dependencies}
        plan = ConvergencePlanner(PRODUCT_CATALOG.values()).plan(ConvergenceRequest((consumer,)))
        waves = materializer_execution_waves(tuple(replace_step(step, action="materialize") for step in plan.steps))
        if consumer == "sleep_productivity":
            products = [step.product for wave in waves for step in wave]
            assert products.index(producer) < products.index(consumer)
            assert next(i for i, wave in enumerate(waves) if consumer in {step.product for step in wave}) > next(
                i for i, wave in enumerate(waves) if producer in {step.product for step in wave}
            )
        else:
            assert [[step.product for step in wave] for wave in waves] == [[producer], [consumer]]


def test_sleep_productivity_is_skipped_when_activitywatch_derived_fails(monkeypatch) -> None:
    from lynchpin.core.errors import MaterializationError
    from lynchpin.materializers import production

    plan = ConvergencePlanner(PRODUCT_CATALOG.values()).plan(ConvergenceRequest(("sleep_productivity",)))
    calls: list[str] = []
    receipts = []

    def handler(context):
        product = context.step.product
        calls.append(product)
        if product == "activitywatch_derived":
            raise RuntimeError("synthetic ActivityWatch failure")

    production_harness(monkeypatch, [(PRODUCT_CATALOG[step.product].handler, handler) for step in plan.steps], receipts)
    steps = tuple(replace_step(step, action="materialize") for step in plan.steps)
    with pytest.raises(MaterializationError, match="activitywatch_derived"):
        production.run_materialization_plan(steps, continue_on_error=True)

    assert ("activitywatch_derived", "error") in receipts
    assert ("sleep_productivity", "skipped") in receipts
    assert "sleep_productivity" not in calls


def test_production_failure_blocks_reusable_child_and_preserves_sibling(monkeypatch) -> None:
    from lynchpin.core.errors import MaterializationError
    from lynchpin.materializers import production

    calls: list[str] = []
    receipts: list[tuple[str, str]] = []
    broken = True

    def parent(_context):
        calls.append("parent")
        if broken:
            raise RuntimeError("synthetic failure")

    def sibling(_context):
        calls.append("sibling")

    handlers = registry(
        ("test:parent", parent),
        ("test:child", lambda _context: calls.append("child")),
        ("test:sibling", sibling),
    )
    monkeypatch.setattr(production, "handler_registry", lambda: handlers)
    monkeypatch.setattr(production, "_audit", lambda: SimpleNamespace(
        _record_materialization_step=lambda _refresh, product, status, *_args, **_kwargs: receipts.append((product, status)),
        _int_or_none=lambda value: value,
        _window_payload=lambda value: [item.isoformat() for item in value] if value else None,
        _PRODUCT_REFRESHED_AT={},
        monotonic=lambda: 1.0,
    ))
    plan = planned(spec("parent"), spec("child", dependencies=("parent",)))
    steps = tuple(replace_step(step, action="materialize") for step in plan.steps)
    sibling_step = replace_step(ConvergencePlanner((spec("sibling"),)).plan(ConvergenceRequest(("sibling",))).steps[0], action="materialize")

    with pytest.raises(MaterializationError, match="parent, child|child, parent"):
        production.run_materialization_plan((*steps, sibling_step), continue_on_error=True)
    assert calls == ["parent", "sibling"]
    assert ("child", "skipped") in receipts

    broken = False
    calls.clear()
    receipts.clear()
    completed = production.run_materialization_plan((*steps, replace_step(sibling_step, action="skip", status="ready")), continue_on_error=True)
    assert {step.product for step in completed} == {"parent", "child"}
    assert calls == ["parent", "child"]

    calls.clear()
    with pytest.raises(MaterializationError, match="child"):
        production.run_materialization_plan((replace_step(steps[0], action="check-only", status="pending"), steps[1]), continue_on_error=True)
    assert calls == []


def test_ready_consumer_is_invalidated_by_planned_producer(monkeypatch) -> None:
    from lynchpin.materializers import production

    rows = [
        SimpleNamespace(name=name, status=status, reason=status, first_date=None, last_date=None, materialized_paths=(), repair_required=False, tail_stale=False)
        for name, status in (("parent", "pending"), ("child", "ready"), ("sibling", "ready"))
    ]
    monkeypatch.setattr(production, "PRODUCT_CATALOG", {
        "parent": spec("parent"),
        "child": spec("child", dependencies=("parent",)),
        "sibling": spec("sibling"),
    })
    monkeypatch.setattr(production, "_audit", lambda: SimpleNamespace(
        audit_materialization=lambda **_kwargs: rows,
        _dataset_fingerprint=lambda row: row.status,
        source_contract=lambda _name: SimpleNamespace(materialization_hint="fixture"),
    ))
    plan = production.plan_materializations(cfg=object())
    assert [(step.product, step.action) for step in plan] == [("parent", "materialize"), ("child", "materialize")]
    assert [[step.product for step in wave] for wave in production.materializer_execution_waves(plan)] == [["parent"], ["child"]]

    maintenance = production.plan_materializations(cfg=object(), maintenance=True)
    assert [(step.product, step.action) for step in maintenance] == [("parent", "check-only"), ("child", "materialize")]


def test_ready_sleep_productivity_is_invalidated_by_derived_refresh(monkeypatch) -> None:
    from lynchpin.materializers import production

    producer = PRODUCT_CATALOG["activitywatch_derived"]
    consumer = PRODUCT_CATALOG["sleep_productivity"]
    rows = [
        SimpleNamespace(
            name=producer.product,
            status="pending",
            reason="derived product changed",
            first_date=None,
            last_date=None,
            materialized_paths=(),
            repair_required=False,
            tail_stale=False,
        ),
        SimpleNamespace(
            name=consumer.product,
            status="ready",
            reason="canonical product is ready",
            first_date=None,
            last_date=None,
            materialized_paths=(),
            repair_required=False,
            tail_stale=False,
        ),
    ]
    monkeypatch.setattr(production, "PRODUCT_CATALOG", {producer.product: producer, consumer.product: consumer})
    monkeypatch.setattr(production, "_audit", lambda: SimpleNamespace(
        audit_materialization=lambda **_kwargs: rows,
        _dataset_fingerprint=lambda row: row.status,
        source_contract=lambda _name: SimpleNamespace(materialization_hint="fixture"),
    ))

    plan = production.plan_materializations(cfg=object())

    assert [(step.product, step.action) for step in plan] == [
        ("activitywatch_derived", "materialize"),
        ("sleep_productivity", "materialize"),
    ]
    assert [[step.product for step in wave] for wave in production.materializer_execution_waves(plan)] == [
        ["activitywatch_derived"],
        ["sleep_productivity"],
    ]


def test_maintenance_debounce_uses_the_newest_product_output(tmp_path) -> None:
    from os import utime
    from time import time
    from types import SimpleNamespace

    from lynchpin.materializers.production import _recently_materialized

    stale = tmp_path / "stale.json"
    recent = tmp_path / "recent.json"
    stale.write_text("{}")
    recent.write_text("{}")
    old = time() - 3600
    utime(stale, (old, old))

    assert _recently_materialized(
        SimpleNamespace(materialized_paths=(stale, recent))
    )
    utime(recent, (old, old))
    assert not _recently_materialized(
        SimpleNamespace(materialized_paths=(stale, recent))
    )
