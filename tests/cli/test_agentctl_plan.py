from __future__ import annotations

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
):
    return SimpleNamespace(
        product=product,
        input_generation=generation,
        action="materialize",
        dependencies=dependencies,
        effective_window=window,
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
    monkeypatch.setattr(
        "lynchpin.analysis.active.substrate_promote.run_substrate_promote",
        lambda **_kwargs: SimpleNamespace(status="ok", counts={}),
    )

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
        "publication_refresh_id": expected,
    }
    assert result["refresh_id"] == expected
