from __future__ import annotations


def test_mcp_app_instantiates() -> None:
    from lynchpin.mcp.server import app

    assert app.name == "lynchpin"


def test_only_collapsed_public_tools_are_registered() -> None:
    from lynchpin.mcp.registry import PUBLIC_TOOL_NAMES
    from lynchpin.mcp.server import app

    tools = getattr(app._tool_manager, "_tools", {})

    assert tuple(sorted(tools)) == tuple(sorted(PUBLIC_TOOL_NAMES))
    assert len(tools) == 8


def test_legacy_mutating_helpers_are_not_exported_as_tools() -> None:
    from lynchpin.mcp.server import app
    from lynchpin.mcp.tools import health
    from lynchpin.mcp.tools import substrate

    assert callable(substrate.ai_attribution_backfill)
    assert callable(substrate.substrate_prune)
    assert callable(health.promote_analysis_product)
    assert "ai_attribution_backfill" not in app._tool_manager._tools
    assert "substrate_prune" not in app._tool_manager._tools
    assert "promote_analysis_product" not in app._tool_manager._tools
