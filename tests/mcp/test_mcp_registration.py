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
    from lynchpin.mcp.tools.personal import spotify_daily
    from lynchpin.mcp.tools.review import pr_review_rows, review_bottlenecks
    from lynchpin.mcp.tools.substrate import (
        list_evidence_graph_builds,
        list_substrate_tables,
        load_evidence_graph_summary,
        query_substrate,
        substrate_readiness_report,
        substrate_source_status,
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
    ]:
        assert callable(fn)
