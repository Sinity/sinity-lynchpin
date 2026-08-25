"""Render and execute Lynchpin nodes through Sinnix project plans."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from lynchpin.core.config import get_config
from lynchpin.materializers import canonical_json
from lynchpin.materializers.catalog import PRODUCT_CATALOG
from lynchpin.materializers.production import (
    _step,
    plan_materializations,
    run_materialization_plan,
)


def _window(start: str | None, end: str | None) -> tuple[date, date] | None:
    if (start is None) != (end is None):
        raise ValueError("start and end must be supplied together")
    if start is None or end is None:
        return None
    value = (date.fromisoformat(start), date.fromisoformat(end))
    if value[1] <= value[0]:
        raise ValueError("end must be after start")
    return value


def build_agentctl_plan(*, maintenance_end: date | None = None) -> dict[str, Any]:
    """Build the bounded runtime DAG from the canonical typed production plan."""
    from lynchpin.cli.materialize import _all_history_window

    maintenance_end = maintenance_end or (date.today() + timedelta(days=1))
    typed = plan_materializations(
        maintenance=True, maintenance_end=maintenance_end
    )
    runnable = {step.product: step for step in typed if step.action == "materialize"}
    scheduled_generations: dict[str, str] = {}

    def scheduled_generation(product: str, visiting: frozenset[str] = frozenset()) -> str:
        if product in scheduled_generations:
            return scheduled_generations[product]
        if product in visiting:
            raise RuntimeError(f"materialization dependency cycle at {product}")
        step = runnable[product]
        dependencies = {
            dependency: scheduled_generation(dependency, visiting | {product})
            for dependency in sorted(step.dependencies)
            if dependency in runnable
        }
        generation = hashlib.sha256(
            canonical_json(
                {"source_generation": step.input_generation, "dependencies": dependencies}
            ).encode()
        ).hexdigest()
        scheduled_generations[product] = generation
        return generation

    nodes: list[dict[str, Any]] = []
    for product, step in sorted(runnable.items()):
        dependency_nodes = [
            dependency for dependency in step.dependencies if dependency in runnable
        ]
        generation = scheduled_generation(product)
        payload: dict[str, Any] = {
            "product": product,
            "input_generation": generation,
            "source_generation": step.input_generation,
            "planned_dependencies": bool(dependency_nodes),
        }
        if step.effective_window is not None:
            payload.update(
                start=step.effective_window[0].isoformat(),
                end=step.effective_window[1].isoformat(),
            )
        nodes.append(
            {
                "id": f"product:{product}",
                "operation": "materialize_node",
                "depends_on": [
                    f"product:{dependency}"
                    for dependency in dependency_nodes
                ],
                "input_generation": generation,
                "parameters": payload,
            }
        )

    history_start, observed_history_end = _all_history_window()
    history_end = min(observed_history_end, maintenance_end)
    if history_end <= history_start:
        raise RuntimeError("canonical history has no bounded maintenance window")
    tail_starts = [
        step.effective_window[0]
        for step in runnable.values()
        if step.effective_window is not None
    ]
    tail_start = min(tail_starts, default=max(history_start, history_end - timedelta(days=7)))
    product_generations = {
        product: scheduled_generation(product) for product in sorted(runnable)
    }
    promotion_tail_start = tail_start
    if not runnable:
        from lynchpin.materialization import plan_read_convergence

        graph_plan = plan_read_convergence(window=(history_start, history_end))
        if graph_plan.action in {"skip", "inspect"}:
            return {
                "schema": "lynchpin.agentctl-plan.v1",
                "input_generation": hashlib.sha256(canonical_json(nodes).encode()).hexdigest(),
                "nodes": nodes,
            }
        if graph_plan.action != "converge" or graph_plan.tail_start is None:
            raise RuntimeError(f"substrate maintenance is blocked: {graph_plan.reason}")
        promotion_tail_start = graph_plan.tail_start
    promotion_generation = hashlib.sha256(
        canonical_json(
            {
                "products": product_generations,
                "start": history_start,
                "end": history_end,
                "tail_start": promotion_tail_start,
            }
        ).encode()
    ).hexdigest()
    nodes.append(
        {
            "id": "substrate:promotion",
            "operation": "promote_node",
            "depends_on": [f"product:{name}" for name in sorted(runnable)],
            "input_generation": promotion_generation,
            "parameters": {
                "start": history_start.isoformat(),
                "end": history_end.isoformat(),
                "tail_start": promotion_tail_start.isoformat(),
                "input_generation": promotion_generation,
            },
        }
    )
    plan_generation = hashlib.sha256(canonical_json(nodes).encode()).hexdigest()
    return {
        "schema": "lynchpin.agentctl-plan.v1",
        "input_generation": plan_generation,
        "nodes": nodes,
    }


def run_product_node(
    *,
    product: str,
    input_generation: str,
    source_generation: str,
    planned_dependencies: bool,
    window: tuple[date, date] | None,
) -> dict[str, Any]:
    """Execute exactly one descriptor-validated typed product node."""
    from lynchpin import materialization

    try:
        spec = PRODUCT_CATALOG[product]
    except KeyError as exc:
        raise ValueError(f"unknown canonical product: {product}") from exc
    row = materialization._audit_one(product, cfg=get_config())
    step = _step(
        spec,
        row=row,
        action="materialize",
        reason="AgentCTL typed product node",
        window=window,
    )
    if not planned_dependencies and step.input_generation != source_generation:
        raise RuntimeError(
            f"scheduled generation for {product} is stale: "
            f"expected {source_generation}, observed {step.input_generation}"
        )
    step = replace(
        step,
        input_generation=input_generation,
        spec=replace(
            step.spec,
            input_generation=input_generation,
            output=replace(step.output, generation=input_generation),
        ),
        output=replace(step.output, generation=input_generation),
    )
    completed = run_materialization_plan((step,), window=window)
    if not completed:
        raise RuntimeError(f"typed product node did not complete: {product}")
    return {
        "schema": "lynchpin.materialization-node-result.v1",
        "product": product,
        "input_generation": input_generation,
        "window": step.to_json()["window"],
        "artifact": step.output.to_dict(),
        "status": "succeeded",
    }


def run_promotion_node(
    *, start: date, end: date, tail_start: date, input_generation: str
) -> dict[str, Any]:
    """Atomically publish one graph tail after all canonical nodes complete."""
    from lynchpin.cli.substrate_snapshot import _snapshot_refresh_id
    from lynchpin.cli.substrate_snapshot import main as snapshot_main
    from lynchpin.substrate.connection import bind_candidate_publication, candidate_generation

    environment_key = "LYNCHPIN_GRAPH_GENERATION"
    previous_generation = os.environ.get(environment_key)
    os.environ[environment_key] = input_generation[:16]
    try:
        refresh_id = _snapshot_refresh_id(start=start, end=end, projects=())
        with candidate_generation(receipt_refresh_id=refresh_id) as generation:
            code = snapshot_main(
                [
                    "--start",
                    start.isoformat(),
                    "--end",
                    end.isoformat(),
                    "--incremental-tail-start",
                    tail_start.isoformat(),
                    "--existing-products",
                    "--graph-only",
                    "--progress",
                    "quiet",
                ]
            )
            if code:
                raise RuntimeError(f"substrate promotion exited with code {code}")
            bind_candidate_publication(generation, refresh_id)
    finally:
        if previous_generation is None:
            os.environ.pop(environment_key, None)
        else:
            os.environ[environment_key] = previous_generation
    return {
        "schema": "lynchpin.promotion-node-result.v1",
        "status": "succeeded",
        "input_generation": input_generation,
        "refresh_id": refresh_id,
        "tail_start": tail_start.isoformat(),
    }


def submit_agentctl_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Submit the typed node graph to the canonical runtime client."""
    with tempfile.TemporaryDirectory(prefix="lynchpin-plan-") as directory:
        path = Path(directory) / "plan.json"
        path.write_text(canonical_json({"nodes": plan["nodes"]}) + "\n")
        completed = subprocess.run(
            [
                "agentctl",
                "plan",
                "submit",
                "lynchpin",
                "--input-generation",
                str(plan["input_generation"]),
                "--plan-file",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    result = json.loads(completed.stdout)
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise RuntimeError("AgentCTL rejected the Lynchpin convergence plan")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("--end")
    submit_parser = subparsers.add_parser("submit")
    submit_parser.add_argument("--end")
    node_parser = subparsers.add_parser("node")
    node_parser.add_argument("--product", required=True)
    node_parser.add_argument("--input-generation", required=True)
    node_parser.add_argument("--source-generation", required=True)
    node_parser.add_argument("--planned-dependencies", action="store_true")
    node_parser.add_argument("--start")
    node_parser.add_argument("--end")
    promote_parser = subparsers.add_parser("promote")
    promote_parser.add_argument("--start", required=True)
    promote_parser.add_argument("--end", required=True)
    promote_parser.add_argument("--tail-start", required=True)
    promote_parser.add_argument("--input-generation", required=True)
    args = parser.parse_args(argv)
    if args.command in {"plan", "submit"}:
        plan = build_agentctl_plan(
            maintenance_end=date.fromisoformat(args.end) if args.end else None
        )
        payload = submit_agentctl_plan(plan) if args.command == "submit" else plan
    elif args.command == "node":
        payload = run_product_node(
            product=args.product,
            input_generation=args.input_generation,
            source_generation=args.source_generation,
            planned_dependencies=args.planned_dependencies,
            window=_window(args.start, args.end),
        )
    else:
        payload = run_promotion_node(
            start=date.fromisoformat(args.start),
            end=date.fromisoformat(args.end),
            tail_start=date.fromisoformat(args.tail_start),
            input_generation=args.input_generation,
        )
    print(canonical_json(payload), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
