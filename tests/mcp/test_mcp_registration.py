from __future__ import annotations


def test_mcp_app_instantiates() -> None:
    from lynchpin.mcp.server import app

    assert app.name == "lynchpin"


def test_mcp_tools_registered() -> None:
    from lynchpin.mcp.tools.change import (
        ai_tool_usage,
        breaking_changes,
        commit_kind_attribution,
        conventional_commits,
        file_hotspots,
        refactor_candidates,
        symbol_churn_hotspots,
    )
    from lynchpin.mcp.tools.machine import (
        machine_below_attributions,
        machine_context_windows,
        machine_episodes,
        machine_experiment_claims,
        machine_metrics_daily,
        machine_observational_baselines,
        machine_service_state_summary,
    )
    from lynchpin.mcp.tools.personal import (
        bookmark_daily,
        bookmarks_search,
        communication_daily,
        communication_events,
        focus_daily,
        google_takeout_daily,
        google_takeout_events,
        terminal_daily,
        terminal_sessions,
        contract_status,
        materialization_status,
        personal_daily_signals,
        spotify_daily,
        title_metadata_audit,
        web_daily,
    )
    from lynchpin.mcp.tools.capability import mcp_capability_matrix
    from lynchpin.mcp.tools.review import pr_review_rows, review_bottlenecks
    from lynchpin.mcp.tools.runtime import mcp_runtime_status, mcp_surface_self_check
    from lynchpin.mcp.tools.signals import source_observation_bounds, verify_vs_edit_ratio
    from lynchpin.mcp.tools.substrate import (
        list_evidence_graph_builds,
        list_substrate_tables,
        load_evidence_graph_summary,
        query_substrate,
        substrate_readiness_report,
        substrate_source_status,
        analysis_readiness,
        analysis_claims,
        claim_evidence,
        contract_coverage,
        mcp_capability_map,
        promotion_runs,
        substrate_run_steps,
    )
    from lynchpin.mcp.tools.views import (
        closure_chain_walks,
        file_overlap_edges,
        project_day_correlations,
        symbol_overlap_edges,
    )

    for fn in [
        query_substrate,
        list_substrate_tables,
        list_evidence_graph_builds,
        load_evidence_graph_summary,
        substrate_source_status,
        substrate_readiness_report,
        project_day_correlations,
        closure_chain_walks,
        file_overlap_edges,
        symbol_overlap_edges,
        pr_review_rows,
        review_bottlenecks,
        spotify_daily,
        web_daily,
        bookmarks_search,
        bookmark_daily,
        communication_events,
        communication_daily,
        focus_daily,
        google_takeout_daily,
        google_takeout_events,
        terminal_daily,
        terminal_sessions,
        personal_daily_signals,
        contract_status,
        materialization_status,
        title_metadata_audit,
        analysis_readiness,
        analysis_claims,
        claim_evidence,
        contract_coverage,
        mcp_capability_map,
        mcp_capability_matrix,
        promotion_runs,
        substrate_run_steps,
        refactor_candidates,
        file_hotspots,
        conventional_commits,
        ai_tool_usage,
        breaking_changes,
        commit_kind_attribution,
        symbol_churn_hotspots,
        machine_episodes,
        machine_context_windows,
        machine_below_attributions,
        machine_observational_baselines,
        machine_experiment_claims,
        machine_metrics_daily,
        machine_service_state_summary,
        mcp_runtime_status,
        mcp_surface_self_check,
        source_observation_bounds,
        verify_vs_edit_ratio,
    ]:
        assert callable(fn)


def test_default_mcp_does_not_export_mutating_maintenance_tools() -> None:
    from lynchpin.mcp.tools import substrate
    from lynchpin.mcp.tools import health

    assert callable(substrate.ai_attribution_backfill)
    assert callable(substrate.substrate_prune)
    assert callable(health.promote_analysis_product)
    assert not hasattr(substrate.ai_attribution_backfill, "name")
    assert not hasattr(substrate.substrate_prune, "name")
    assert not hasattr(health.promote_analysis_product, "name")
