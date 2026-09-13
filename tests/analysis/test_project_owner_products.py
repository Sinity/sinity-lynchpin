from copy import deepcopy
from datetime import date, datetime, timezone
import json

import pytest

from lynchpin.analysis.projects import context, owner_products
from lynchpin.core.evidence_graph import EvidenceGraph
from lynchpin.mcp.registry import public_action_spec
from lynchpin.mcp.tools.public import lynchpin_project
from lynchpin.sources.beads import read_tasks
from tests.analysis.test_campaign_evidence import REF, snapshot


def native_snapshot():
    return {
        "revision": "revision-one",
        "items": [{"id": "demo-1", "status": "open"}],
        "temporal": {"requested": "HEAD", "resolved_revision": "revision-one"},
        "edges": [],
        "has_more": False,
        "closure": {"complete": True},
    }


def test_task_snapshot_pins_all_rows_and_exposes_exact_followup():
    calls = []

    def loader(project, request):
        calls.append((project, request))
        return native_snapshot()

    result = read_tasks("demo", roots=[REF], at="before", loader=loader)
    assert calls[0][1]["at"] == "before"
    assert calls[0][1]["roots"] == ["demo-1"]
    assert result["nodes"][0]["task_revision"] == "revision-one"
    assert result["owner_request"]["at"] == "revision-one"
    assert result["complete"]


def test_task_owner_provenance_and_leaf_census_preserved_without_expanding_scope():
    payload = native_snapshot()
    payload["items"][0].update(
        bead_revision="9223372036854775807", revision=9223372036854775807
    )
    payload["provenance_edges"] = [
        {
            "from": "demo-1",
            "to": "external-1",
            "relation": "discovered_from",
            "target_kind": "external",
        }
    ]
    payload["provenance_coverage"] = {"complete": True, "state": "complete"}
    payload["graph_leaves"] = ["demo-1"]
    calls = []

    def loader(project, request):
        calls.append(request)
        return payload

    result = read_tasks("demo", roots=["demo-1"], loader=loader)
    assert calls[0]["provenance"] is True
    assert result["provenance_edges"] == payload["provenance_edges"]
    assert result["provenance_coverage"]["complete"]
    assert result["graph_leaves"] == ["demo-1"]
    assert result["nodes"][0]["bead_revision"] == "9223372036854775807"
    assert len(result["nodes"]) == 1


def test_declared_analytical_roles_do_not_replace_native_readiness():
    tasks = {
        "nodes": [
            {"id": "demo-1", "metadata": {"closure_role": "gate"}},
            {"id": "demo-2", "issue_type": "decision"},
            {"id": "demo-3"},
        ],
        "readiness": {"demo-1": {"state": "blocked"}},
    }
    result = owner_products.classify_task_scope(tasks)
    assert result["declared_gates"] == ["demo-1"]
    assert result["declared_decisions"] == ["demo-2"]
    assert result["unknown_roles"] == ["demo-3"]
    assert result["readiness"] == tasks["readiness"]


def test_mixed_task_generations_and_partial_closure_cannot_prove_denominator():
    payload = native_snapshot()
    payload["temporal"]["resolved_revision"] = "different"
    result = read_tasks("demo", loader=lambda *_: payload)
    assert result["coverage"] == "unavailable"
    assert not result["complete"]
    payload = native_snapshot()
    payload["has_more"] = True
    result = read_tasks("demo", loader=lambda *_: payload)
    assert result["coverage"]["state"] == "partial"
    assert not result["complete"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("closure", []),
        ("closure", ["unexpected"]),
        ("closure", {"complete": []}),
        ("edges", {}),
        ("edges", [None]),
        ("provenance_edges", "unexpected"),
        ("provenance_edges", [[]]),
        ("provenance_coverage", []),
        ("provenance_coverage", {"complete": "yes"}),
        ("graph_leaves", [{}]),
    ],
)
def test_malformed_task_owner_shapes_report_unavailable(field, value):
    payload = native_snapshot()
    payload[field] = value
    result = read_tasks("demo", loader=lambda *_: payload)
    assert result["coverage"] == "unavailable"
    assert result["complete"] is False
    assert result["nodes"] == []
    assert result["gaps"]


def test_campaign_route_acquires_both_owner_snapshots(monkeypatch):
    calls = []

    def tasks(project, **kwargs):
        calls.append(kwargs)
        return snapshot()

    monkeypatch.setattr(owner_products, "read_tasks", tasks)
    monkeypatch.setattr(
        owner_products,
        "campaign_evidence",
        lambda **kw: {"counts": {"complete_scope": kw["task_snapshot"]["complete"]}},
    )
    response = lynchpin_project(
        action="campaign_progress",
        project="demo",
        roots=["demo-1"],
        at="target",
        baseline="before",
    )
    assert response["ok"]
    assert [call["at"] for call in calls] == ["target", "before"]
    assert response["data"]["closure"]["task_revision"] == "dolt-snapshot-v3"
    assert response["data"]["scope_delta"]["product"] == "campaign_scope_delta"


def setup_context(monkeypatch, *, huge=False):
    graph = EvidenceGraph(
        start=date(2026, 1, 1),
        end=date(2026, 1, 14),
        generated_at=datetime(2026, 1, 14, tzinfo=timezone.utc),
        refresh_id="graph-one",
    )
    calls = []

    def load(*args):
        calls.append(args)
        return graph

    monkeypatch.setattr(context, "_graph", load)
    task = {
        **snapshot(),
        "source_ref": "beads://projects/demo/owner/read",
        "revision": "task-one",
    }
    if huge:
        task["nodes"][0]["description"] = "large fixture " * 10000
    monkeypatch.setattr(context, "read_tasks", lambda *a, **kw: deepcopy(task))
    monkeypatch.setattr(
        context,
        "read_batches",
        lambda project: {
            "rows": [],
            "revision": "runtime-one",
            "coverage": "unavailable",
            "gaps": ["Owner offline"],
        },
    )
    return calls


def test_context_is_graph_pinned_and_owner_failure_is_independent(monkeypatch):
    calls = setup_context(monkeypatch)
    result = context.project_context(
        project="demo", start="2026-01-01", end="2026-01-14", refresh_id="graph-one"
    )
    assert len(calls) == 1
    assert result["temporal"]["refresh_id"] == "graph-one"
    rows = {item["name"]: item for item in result["components"]}
    assert rows["evidence"]["refresh_id"] == "graph-one"
    assert rows["tasks"]["status"] == "available"
    assert rows["tasks"]["refresh_id"] is None
    assert rows["trajectory"]["status"] == "unavailable"
    assert result["owner_ref"]["arguments"]["at"] == "task-one"


@pytest.mark.parametrize(
    "failure,unavailable",
    [
        ("read_tasks", {"tasks"}),
        ("classify_task_scope", {"tasks"}),
        ("read_batches", {"trajectory", "verification"}),
        ("project_trajectory", {"trajectory"}),
        ("verification_regression", {"verification"}),
    ],
)
def test_context_isolates_owner_and_product_exceptions(
    monkeypatch, failure, unavailable
):
    setup_context(monkeypatch)
    monkeypatch.setattr(
        context,
        "read_batches",
        lambda project: {
            "rows": [],
            "revision": "runtime-one",
            "coverage": "retained_records",
            "gaps": [],
        },
    )

    def fail(*args, **kwargs):
        raise RuntimeError("Synthetic owner failure")

    monkeypatch.setattr(context, failure, fail)
    result = context.project_context(
        project="demo", roots=["demo-1"], start="2026-01-01", end="2026-01-14"
    )
    components = {row["name"]: row for row in result["components"]}
    assert components["evidence"]["data"]["refresh_id"] == "graph-one"
    assert {
        name for name, row in components.items() if row["status"] == "unavailable"
    } == unavailable
    assert result["outcome"] == "partial"
    for name in unavailable:
        assert "Synthetic owner failure" in components[name]["gaps"]


def test_malformed_beads_owner_keeps_graph_context(monkeypatch):
    setup_context(monkeypatch)
    payload = native_snapshot()
    payload["closure"] = []
    monkeypatch.setattr(
        context,
        "read_tasks",
        lambda project, **kw: read_tasks(project, loader=lambda *_: payload),
    )
    result = context.project_context(
        project="demo", start="2026-01-01", end="2026-01-14"
    )
    assert result["components"][0]["status"] == "available"
    assert result["components"][1]["status"] == "unavailable"
    assert result["components"][0]["data"]["refresh_id"] == "graph-one"


def test_unserializable_product_cannot_erase_healthy_components(monkeypatch):
    setup_context(monkeypatch)
    monkeypatch.setattr(
        context, "project_trajectory", lambda **kw: {"unexpected": object()}
    )
    result = context.project_context(
        project="demo", start="2026-01-01", end="2026-01-14"
    )
    components = {row["name"]: row for row in result["components"]}
    assert components["evidence"]["status"] == "available"
    assert components["tasks"]["status"] == "available"
    assert components["trajectory"]["status"] == "unavailable"
    json.dumps(result)


def test_oversized_component_keeps_exact_owner_payload_and_coverage(monkeypatch):
    setup_context(monkeypatch, huge=True)
    result = context.project_context(
        project="demo", start="2026-01-01", end="2026-01-14", budget_bytes=8192
    )
    task = next(row for row in result["components"] if row["name"] == "tasks")
    assert task["presentation_budget_exceeded"]
    assert task["data"]["nodes"][0]["description"] == "large fixture " * 10000
    assert task["source_ref"] == "beads://projects/demo/owner/read"
    assert task["source_revision"] == "task-one"
    assert task["coverage"]["complete"]
    assert result["presentation_budget_exceeded"]
    assert len(json.dumps(result, ensure_ascii=False).encode()) > 8192
    assert task["payload_revision"] == context.revision(task["data"])


def test_missing_graph_does_not_refresh_or_erase_tasks(monkeypatch):
    setup_context(monkeypatch)

    def missing(*args):
        raise ValueError("Requested retained generation is missing")

    monkeypatch.setattr(context, "_graph", missing)
    result = context.project_context(project="demo", refresh_id="missing")
    assert result["temporal"]["refresh_id"] is None
    assert result["components"][0]["status"] == "unavailable"
    assert result["components"][1]["status"] == "available"


def test_graph_reader_rejects_generation_substitution(monkeypatch):
    from contextlib import nullcontext

    graph = EvidenceGraph(
        start=date(2026, 1, 1),
        end=date(2026, 1, 14),
        generated_at=datetime(2026, 1, 14, tzinfo=timezone.utc),
        refresh_id="different",
    )
    monkeypatch.setattr(
        "lynchpin.substrate.connection.connect", lambda **kw: nullcontext(object())
    )
    monkeypatch.setattr(
        "lynchpin.substrate.graph.load_evidence_graph", lambda *a, **kw: graph
    )
    with pytest.raises(ValueError, match="different refresh"):
        context._graph("demo", date(2026, 1, 1), date(2026, 1, 14), "requested")


def test_graph_context_excludes_other_project_evidence():
    from lynchpin.core.evidence_graph import EvidenceNode
    from lynchpin.graph.context_pack import project_graph_context

    graph = EvidenceGraph(
        start=date(2026, 1, 1),
        end=date(2026, 1, 14),
        generated_at=datetime(2026, 1, 14, tzinfo=timezone.utc),
        refresh_id="selected",
        nodes=(
            EvidenceNode(
                id="commit:one",
                kind="commit",
                source="git",
                project="sinnix",
                date=date(2026, 1, 2),
                summary="Synthetic change",
                payload={"commit": "a" * 40},
            ),
            EvidenceNode(
                id="commit:other",
                kind="commit",
                source="git",
                project="polylogue",
                date=date(2026, 1, 2),
                summary="Other synthetic change",
                payload={"commit": "b" * 40},
            ),
        ),
    )
    result = project_graph_context(
        graph, project="sinnix", start=graph.start, end=graph.end
    )
    assert result["source_refs"] == ["commit:one"]
    assert result["projects"][0]["rows"][0]["commit_count"] == 1


@pytest.mark.parametrize(
    "action",
    [
        "campaign_evidence",
        "campaign_progress",
        "campaign_scope_delta",
        "verification_regression",
        "project_trajectory",
        "project_context",
    ],
)
def test_owner_actions_publish_complete_typed_inputs(action):
    spec = public_action_spec("lynchpin_project", action)
    schema = spec.to_json()["input_schema"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["action"]["const"] == action
    assert set(spec.parameters) == set(schema["properties"]) - {"action"}
    assert all(
        "type" in value or "anyOf" in value for value in schema["properties"].values()
    )


def test_invalid_owner_bounds_rejected_before_source_access():
    result = lynchpin_project(
        action="campaign_progress", project="demo", roots=["demo-1"], max_nodes=1001
    )
    assert result["ok"] is False
    result = lynchpin_project(
        action="campaign_scope_delta", project="demo", roots=["demo-1"]
    )
    assert result["ok"] is False


@pytest.mark.parametrize(
    "action,owner_route",
    [
        ("campaign_evidence", "campaign"),
        ("campaign_progress", "campaign"),
        ("campaign_scope_delta", "campaign"),
        ("verification_regression", "history"),
        ("project_trajectory", "history"),
        ("project_context", "context"),
    ],
)
def test_project_owner_actions_dispatch_validated_inputs(
    monkeypatch, action, owner_route
):
    monkeypatch.setattr(
        owner_products,
        "campaign_product",
        lambda **kw: {"owner_route": "campaign", "arguments": kw},
    )
    monkeypatch.setattr(
        owner_products,
        "history_product",
        lambda **kw: {"owner_route": "history", "arguments": kw},
    )
    monkeypatch.setattr(
        context,
        "project_context",
        lambda **kw: {"owner_route": "context", "arguments": kw},
    )
    result = lynchpin_project(
        action=action,
        project="demo",
        roots=["demo-1"],
        **({"baseline": "before"} if owner_route == "campaign" else {}),
    )
    assert result["ok"]
    assert result["data"]["owner_route"] == owner_route
    assert result["data"]["arguments"]["project"] == "demo"
    assert result["data"]["arguments"]["roots"] == ["demo-1"]
