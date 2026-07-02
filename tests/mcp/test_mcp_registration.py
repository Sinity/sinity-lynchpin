from __future__ import annotations

import inspect
from pathlib import Path


def test_mcp_app_instantiates() -> None:
    from lynchpin.mcp.server import app

    assert app.name == "lynchpin"


def test_only_collapsed_public_tools_are_registered() -> None:
    from lynchpin.mcp.registry import PUBLIC_TOOL_NAMES
    from lynchpin.mcp.server import app

    tools = getattr(app._tool_manager, "_tools", {})

    assert tuple(sorted(tools)) == tuple(sorted(PUBLIC_TOOL_NAMES))
    assert len(tools) == 8


def test_internal_mutating_helpers_are_not_exported_as_tools() -> None:
    from lynchpin.mcp.server import app
    from lynchpin.mcp.tools import health
    from lynchpin.mcp.tools import substrate

    assert callable(substrate.ai_attribution_backfill)
    assert callable(substrate.substrate_prune)
    assert callable(health.promote_analysis_product)
    assert "ai_attribution_backfill" not in app._tool_manager._tools
    assert "substrate_prune" not in app._tool_manager._tools
    assert "promote_analysis_product" not in app._tool_manager._tools


def test_internal_tool_modules_do_not_register_fastmcp_tools() -> None:
    root = Path(__file__).resolve().parents[2] / "lynchpin/mcp/tools"
    offenders = [
        path.relative_to(root).as_posix()
        for path in root.glob("*.py")
        if path.name != "public.py" and "@app.tool()" in path.read_text(encoding="utf-8")
    ]

    assert offenders == []


def test_public_router_bodies_cover_registered_actions() -> None:
    from lynchpin.mcp.registry import PUBLIC_TOOLS
    from lynchpin.mcp.tools import public

    router_functions = {
        "lynchpin_status": public.lynchpin_status,
        "lynchpin_catalog": public.lynchpin_catalog,
        "lynchpin_query": public.lynchpin_query,
        "lynchpin_evidence": public.lynchpin_evidence,
        "lynchpin_project": public.lynchpin_project,
        "lynchpin_personal": public.lynchpin_personal,
        "lynchpin_machine": public.lynchpin_machine,
        "lynchpin_ops": public.lynchpin_ops,
    }

    for tool in PUBLIC_TOOLS:
        source = inspect.getsource(router_functions[tool.name])
        missing = [
            action.name
            for action in tool.actions
            if repr(action.name) not in source and f'"{action.name}"' not in source
        ]
        assert missing == [], f"{tool.name} metadata actions missing from router body: {missing}"
