from __future__ import annotations

import inspect
import json
import os
from contextlib import contextmanager
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace


def _step(
    product: str,
    generation: str,
    *,
    dependencies: tuple[str, ...] = (),
    window: tuple[date, date] | None = None,
    action: str = "materialize",
    status: str = "pending",
):
    return SimpleNamespace(
        product=product,
        input_generation=generation,
        action=action,
        dependencies=dependencies,
        effective_window=window,
        status=status,
    )


def test_agentctl_plan_preserves_dependencies_and_exact_node_generations(
    monkeypatch,
) -> None:
    from lynchpin.cli import agentctl_plan
    from lynchpin.cli import materialize

    end = date(2026, 8, 26)
    monkeypatch.setattr(
        agentctl_plan,
        "plan_materializations",
        lambda **_kwargs: [
            _step("activitywatch", "aw-gen", window=(date(2026, 8, 24), end)),
            _step(
                "activitywatch_event_index",
                "index-gen",
                dependencies=("activitywatch",),
                window=(date(2026, 8, 24), end),
            ),
        ],
    )
    monkeypatch.setattr(
        materialize,
        "_all_history_window",
        lambda: (date(2020, 1, 1), end),
    )

    plan = agentctl_plan.build_agentctl_plan(maintenance_end=end)
    nodes = {node["id"]: node for node in plan["nodes"]}

    assert nodes["product:activitywatch"]["parameters"]["source_generation"] == "aw-gen"
    assert (
        nodes["product:activitywatch_event_index"]["input_generation"]
        != nodes["product:activitywatch_event_index"]["parameters"]["source_generation"]
    )
    assert nodes["product:activitywatch_event_index"]["depends_on"] == [
        "product:activitywatch"
    ]
    assert nodes["substrate:promotion"]["depends_on"] == [
        "product:activitywatch",
        "product:activitywatch_event_index",
    ]
    assert nodes["substrate:promotion"]["operation"] == "promote_node"
    assert plan["input_generation"]


def test_agentctl_plan_still_promotes_machine_and_live_sources_when_products_are_reusable(
    monkeypatch,
) -> None:
    from lynchpin import materialization
    from lynchpin.cli import agentctl_plan
    from lynchpin.cli import materialize

    end = date(2026, 8, 26)
    monkeypatch.setattr(agentctl_plan, "plan_materializations", lambda **_kwargs: [])
    monkeypatch.setattr(
        materialize,
        "_all_history_window",
        lambda: (date(2020, 1, 1), end),
    )
    monkeypatch.setattr(
        materialization,
        "plan_read_convergence",
        lambda **_kwargs: SimpleNamespace(action="skip", tail_start=None, reason="ready"),
    )

    plan = agentctl_plan.build_agentctl_plan(maintenance_end=end)

    assert [node["id"] for node in plan["nodes"]] == ["substrate:promotion"]
    assert plan["nodes"][0]["depends_on"] == []


def test_agentctl_plan_uses_bounded_graph_catchup_end(monkeypatch) -> None:
    from lynchpin import materialization
    from lynchpin.cli import agentctl_plan, materialize

    start = date(2020, 1, 1)
    end = date(2026, 9, 1)
    chunk_end = date(2026, 7, 25)
    monkeypatch.setattr(agentctl_plan, "plan_materializations", lambda **_kwargs: [])
    monkeypatch.setattr(materialize, "_all_history_window", lambda: (start, end))
    monkeypatch.setattr(
        materialization,
        "plan_read_convergence",
        lambda **_kwargs: materialization.ReadConvergencePlan(
            "evidence_graph_substrate", (start, end), (start, chunk_end),
            "converge", "bounded catch-up", "fingerprint",
            predecessor_refresh_id="base", tail_start=date(2026, 6, 24),
        ),
    )

    plan = agentctl_plan.build_agentctl_plan(maintenance_end=end)
    promotion = plan["nodes"][0]
    assert promotion["parameters"]["end"] == chunk_end.isoformat()
    assert promotion["parameters"]["tail_start"] == "2026-06-24"


def test_source_chunk_caps_published_generation_in_both_routes(monkeypatch, capsys) -> None:
    from lynchpin import materialization
    from lynchpin.cli import agentctl_plan, materialize

    start = date(2020, 1, 1)
    end = date(2026, 9, 1)
    chunk_end = date(2026, 7, 25)
    step = _step("atuin", "atuin-gen", window=(date(2026, 6, 24), chunk_end))
    monkeypatch.setattr(agentctl_plan, "plan_materializations", lambda **_kwargs: [step])
    monkeypatch.setattr(materialize, "_all_history_window", lambda: (start, end))

    planned = agentctl_plan.build_agentctl_plan(maintenance_end=end)
    assert planned["nodes"][-1]["parameters"]["end"] == chunk_end.isoformat()

    def run_steps(steps, **kwargs):
        kwargs["progress"]({"project": "lynchpin", "stage": "source-materialize", "product": "atuin", "event": "started"})
        return list(steps)

    monkeypatch.setattr(agentctl_plan, "run_materialization_plan", run_steps)
    monkeypatch.setattr(materialization, "_audit_one", lambda *_args, **_kwargs: SimpleNamespace(status="ready", reason="ready"))
    monkeypatch.setattr(materialization, "_materialized_enough_for_window", lambda *_args, **_kwargs: True)
    published = []
    monkeypatch.setattr(agentctl_plan, "run_promotion_node", lambda **kwargs: published.append(kwargs) or {"status": "succeeded"})

    agentctl_plan.run_convergence(maintenance_end=end)
    assert published[0]["end"] == chunk_end
    assert '"product": "atuin"' in capsys.readouterr().err


def test_agentctl_plan_keeps_unavailable_prerequisites_out_of_execution_dag(
    monkeypatch,
) -> None:
    from lynchpin.cli import agentctl_plan
    from lynchpin.cli import materialize

    end = date(2026, 8, 26)
    plan_steps = [
        _step("activitywatch", "aw-gen", action="check-only", status="missing"),
        _step(
            "activitywatch_event_index",
            "index-gen",
            dependencies=("activitywatch",),
        ),
        _step("atuin", "atuin-gen"),
    ]
    monkeypatch.setattr(agentctl_plan, "plan_materializations", lambda **_kwargs: plan_steps)
    monkeypatch.setattr(
        materialize,
        "_all_history_window",
        lambda: (date(2020, 1, 1), end),
    )

    plan = agentctl_plan.build_agentctl_plan(maintenance_end=end)
    node_ids = {node["id"] for node in plan["nodes"]}

    assert node_ids == {"product:atuin"}
    assert plan["unavailable_products"] == ["activitywatch"]
    assert plan["blocked_products"] == ["activitywatch_event_index"]


def test_scheduled_convergence_propagates_unavailable_prerequisite_and_keeps_sibling(
    monkeypatch,
) -> None:
    import pytest

    from lynchpin.cli import agentctl_plan, materialize
    from lynchpin.core.errors import MaterializationError
    from lynchpin.materializers import production
    from lynchpin.materializers.executor import ClosedHandlerRegistry, HandlerDefinition

    end = date(2026, 8, 26)
    calls: list[str] = []
    receipts: list[tuple[str, str]] = []
    products = ("activitywatch", "activitywatch_event_index", "atuin")
    actions = {"activitywatch": "check-only"}
    audit = SimpleNamespace(
        _dataset_fingerprint=lambda row: f"{row.name}:generation",
        source_contract=lambda _product: SimpleNamespace(materialization_hint="fixture"),
        _record_materialization_step=lambda _refresh, product, status, *_args, **_kwargs: receipts.append((product, status)),
        _int_or_none=lambda value: value,
        _window_payload=lambda value: value,
        _PRODUCT_REFRESHED_AT={},
        monotonic=lambda: 1.0,
    )
    monkeypatch.setattr(production, "_audit", lambda: audit)
    planned = tuple(
        production._step(
            production.PRODUCT_CATALOG[name],
            row=SimpleNamespace(name=name, status="missing"),
            action=actions.get(name, "materialize"),
            reason="neutral fixture",
            window=None,
        )
        for name in products
    )
    monkeypatch.setattr(agentctl_plan, "plan_materializations", lambda **_kwargs: planned)
    monkeypatch.setattr(
        materialize, "_all_history_window", lambda: (date(2020, 1, 1), end)
    )

    def handler(product: str):
        return lambda _context: calls.append(product) or {"row_count": 1}

    registry = ClosedHandlerRegistry(
        {
            production.PRODUCT_CATALOG[name].handler: HandlerDefinition(
                production.PRODUCT_CATALOG[name].handler,
                handler(name),
                raw_read_permission=production.PRODUCT_CATALOG[name].raw_read_permission,
                window_policy=production.PRODUCT_CATALOG[name].window_policy,
            )
            for name in products
        }
    )
    monkeypatch.setattr(production, "handler_registry", lambda: registry)
    monkeypatch.setattr(
        agentctl_plan,
        "run_promotion_node",
        lambda **_kwargs: pytest.fail("promotion ran with an unavailable prerequisite"),
    )

    with pytest.raises(MaterializationError, match="activitywatch_event_index"):
        agentctl_plan.run_convergence(maintenance_end=end)

    assert calls == ["atuin"]
    assert ("activitywatch_event_index", "skipped") in receipts


def test_declared_converge_runs_typed_nodes_without_agentctl_plan_api(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    import pytest

    from lynchpin import materialization
    from lynchpin.cli import agentctl_plan, materialize

    end = date(2026, 8, 26)
    step = _step("activitywatch", "aw-generation", window=(date(2026, 8, 24), end))
    transitions: list[str] = []
    monkeypatch.setattr(agentctl_plan, "plan_materializations", lambda **_kwargs: [step])
    monkeypatch.setattr(materialize, "_all_history_window", lambda: (date(2020, 1, 1), end))
    monkeypatch.setattr(
        agentctl_plan,
        "run_materialization_plan",
        lambda steps, **_kwargs: transitions.append("materialize") or list(steps),
    )
    monkeypatch.setattr(
        materialization,
        "_audit_one",
        lambda *_args, **_kwargs: SimpleNamespace(status="ready", reason=None),
    )
    monkeypatch.setattr(
        materialization,
        "_materialized_enough_for_window",
        lambda *_args, **_kwargs: True,
    )

    def promote(**kwargs):
        transitions.append("promote")
        return {
            "schema": "lynchpin.promotion-node-result.v1",
            "status": "succeeded",
            **kwargs,
        }

    monkeypatch.setattr(agentctl_plan, "run_promotion_node", promote)

    # This fake deliberately rejects the removed plan API if any code tries to
    # invoke it while the declared converge operation runs.
    agentctl = tmp_path / "agentctl"
    invocation = tmp_path / "agentctl-called"
    agentctl.write_text(f"#!/bin/sh\ntouch '{invocation}'\nexit 73\n")
    agentctl.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")

    with pytest.raises(SystemExit) as removed_submit:
        agentctl_plan.main(["submit"])
    assert removed_submit.value.code == 2
    assert agentctl_plan.main(["converge", "--end", end.isoformat()]) == 0

    result = json.loads(capsys.readouterr().out)
    assert transitions == ["materialize", "promote"]
    assert result["schema"] == "lynchpin.convergence-result.v1"
    assert result["status"] == "succeeded"
    assert result["materialized_products"] == ["activitywatch"]
    assert result["promotion"]["schema"] == "lynchpin.promotion-node-result.v1"
    assert not invocation.exists()


def test_product_node_uses_the_scheduled_generation(monkeypatch) -> None:
    from lynchpin import materialization
    from lynchpin.cli import agentctl_plan

    row = materialization.MaterializedDataset(
        name="activitywatch",
        status="missing",
        authority="fixture",
        query_surface="fixture",
        materialized_paths=(Path("fixture.ndjson"),),
        raw_roots=(),
        row_count=0,
        first_date=None,
        last_date=None,
        materialization_hint="fixture",
        reason="fixture",
    )
    after = replace(
        row,
        status="ready",
        first_date=date(2026, 8, 1),
        last_date=date(2026, 8, 25),
        covered_dates=tuple(
            date.fromordinal(day)
            for day in range(
                date(2026, 8, 1).toordinal(), date(2026, 8, 26).toordinal()
            )
        ),
    )
    audit_rows = iter((row, after))
    monkeypatch.setattr(
        materialization,
        "_audit_one",
        lambda *_args, **_kwargs: next(audit_rows),
    )
    completed = []
    monkeypatch.setattr(
        agentctl_plan,
        "run_materialization_plan",
        lambda steps, **_kwargs: completed.extend(steps) or list(steps),
    )

    observed_generation = agentctl_plan._step(
        agentctl_plan.PRODUCT_CATALOG["activitywatch"],
        row=row,
        action="materialize",
        reason="fixture",
        window=(date(2026, 8, 24), date(2026, 8, 26)),
    ).input_generation
    result = agentctl_plan.run_product_node(
        product="activitywatch",
        input_generation=observed_generation,
        source_generation=observed_generation,
        planned_dependencies=False,
        window=(date(2026, 8, 24), date(2026, 8, 26)),
    )

    assert completed[0].input_generation == observed_generation
    assert completed[0].output.generation == observed_generation
    assert result["status"] == "succeeded"


def test_product_node_rejects_a_stale_scheduled_generation(monkeypatch) -> None:
    import pytest

    from lynchpin import materialization
    from lynchpin.cli import agentctl_plan

    row = materialization.MaterializedDataset(
        name="activitywatch",
        status="missing",
        authority="fixture",
        query_surface="fixture",
        materialized_paths=(Path("fixture.ndjson"),),
        raw_roots=(),
        row_count=0,
        first_date=None,
        last_date=None,
        materialization_hint="fixture",
        reason="fixture",
    )
    monkeypatch.setattr(materialization, "_audit_one", lambda *_args, **_kwargs: row)

    with pytest.raises(RuntimeError, match="scheduled generation.*is stale"):
        agentctl_plan.run_product_node(
            product="activitywatch",
            input_generation="stale-generation",
            source_generation="stale-generation",
            planned_dependencies=False,
            window=(date(2026, 8, 24), date(2026, 8, 26)),
        )


def test_promotion_uses_an_immutable_generation_refresh_id(monkeypatch) -> None:
    from lynchpin.cli import agentctl_plan, substrate_snapshot
    from lynchpin.substrate import connection

    observed: dict[str, object] = {}

    @contextmanager
    def candidate_generation(*, receipt_refresh_id: str):
        observed["receipt_refresh_id"] = receipt_refresh_id
        yield "candidate"

    monkeypatch.setattr(connection, "candidate_generation", candidate_generation)
    monkeypatch.setattr(
        agentctl_plan.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex="0123456789abcdef"),
    )
    monkeypatch.setattr(
        connection,
        "bind_candidate_publication",
        lambda generation, refresh_id: observed.update(
            generation=generation, publication_refresh_id=refresh_id
        ),
    )
    monkeypatch.setattr(
        substrate_snapshot,
        "main",
        lambda _argv: observed.update(
            snapshot_refresh_id=substrate_snapshot._snapshot_refresh_id(
                start=date(2026, 8, 20), end=date(2026, 8, 27), projects=()
            )
        )
        or 0,
    )
    from lynchpin.analysis.active import substrate_promote

    real_signature = inspect.signature(substrate_promote.run_substrate_promote)

    def promote(**kwargs):
        # The typed-fact promotion must accept exactly what the node passes;
        # a stale keyword failed every node before any typed fact was written.
        real_signature.bind(**kwargs)
        observed["promote_refresh_id"] = kwargs["refresh_id"]
        return SimpleNamespace(status="ok", counts={})

    monkeypatch.setattr(substrate_promote, "run_substrate_promote", promote)

    result = agentctl_plan.run_promotion_node(
        start=date(2026, 8, 20),
        end=date(2026, 8, 27),
        tail_start=date(2026, 8, 25),
        input_generation="abcdef0123456789remainder",
    )

    expected = (
        "current-state:2026-08-20:2026-08-27:all:"
        "generation:abcdef0123456789-0123456789ab"
    )
    assert observed == {
        "receipt_refresh_id": expected,
        "snapshot_refresh_id": expected,
        "generation": "candidate",
        "promote_refresh_id": expected,
        "publication_refresh_id": expected,
    }
    assert result["refresh_id"] == expected
