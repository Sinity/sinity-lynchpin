set shell := ["bash", "-euo", "pipefail", "-c"]

default:
    @just --list

test:
    pytest -q

lint:
    ruff check lynchpin tests

typecheck:
    mypy

check:
    just lint
    just typecheck
    just test

verify-polylogue-contract:
    just lint
    just typecheck
    pytest -q tests/sources/test_source_polylogue.py tests/sources/test_polylogue_archive.py tests/sources/test_polylogue_verification.py tests/analysis/test_polylogue_archive_shape.py tests/sources/test_polylogue_owner_contract.py tests/sources/test_polylogue_profile_freshness.py

verify-activity-content:
    just lint
    just typecheck
    pytest -q tests/ingest/test_activity_content_materialize.py tests/sources/test_activity_content.py tests/test_materialization.py tests/test_typed_materializers.py tests/cli/test_materialize.py tests/analysis/test_substrate_promote_personal.py tests/graph/test_movement_context_pack.py tests/analysis/test_chisel_publication.py tests/analysis/test_snapshot_homes.py

# --- Analysis -----------------------------------------------------------------------

# Materialize the default cross-project velocity dashboard.
velocity output=".lynchpin/generated/meta/velocity.html" projects="" exclude="" aggregate="true":
    python -m lynchpin.analysis.projects velocity --output "{{output}}" --projects "{{projects}}" --exclude "{{exclude}}" --aggregate "{{aggregate}}"

# Build selected offline evidence packages. Arguments are passed as argv.
[positional-arguments]
chisel *args:
    python -m lynchpin.cli.chisel "$@"

# Operator-facing narrative of recent machine state (default: last 24h).
machine-explain args="":
    python -m lynchpin.cli.machine_explain {{args}}

# --- Tool inventory -----------------------------------------------------------------

# List all Lynchpin tools: MCP tools (enumerated live), CLI entry points, dev recipes.
tool-inventory:
    @echo "=== CLI Entry Points ==="
    @echo "  python -m lynchpin.cli.current_state"
    @echo "  python -m lynchpin.analysis materialize"
    @echo "  python -m lynchpin.cli.process_health"
    @echo "  python -m lynchpin.cli.machine_explain"
    @echo "  python -m lynchpin.analysis.projects velocity"
    @echo "  python -m lynchpin.analysis.projects chisel"
    @echo "  python -m lynchpin.analysis ecosystem-dashboard"
    @echo ""
    @echo "=== MCP Tools ($(python -c 'import lynchpin.mcp.server, lynchpin.mcp.tools; from lynchpin.mcp.tools._utils import registered_tool_names; print(len(registered_tool_names()))' 2>/dev/null) registered) ==="
    @echo "  python -m lynchpin.mcp  # Start MCP server"
    @echo "  Use mcp_capability_map tool from the server for the live catalog,"
    @echo "  or list_substrate_tables for substrate schema."
    @echo ""
    @echo "=== Dev ==="
    @echo "  just test | just lint | just typecheck | just check"

[positional-arguments]
verify-generation-selection *args:
    just lint
    just typecheck
    pytest -q tests/substrate/test_product_lineage.py tests/substrate/test_read_snapshot.py tests/substrate/test_graph_lineage_readers.py tests/mcp/test_best_materialized_refresh_id.py tests/mcp/test_mcp_personal.py tests/analysis/test_substrate_promote_personal.py tests/mcp/test_mcp_substrate.py tests/mcp/test_mcp_public.py tests/analysis/test_analysis_machine_experiments.py tests/analysis/test_commit_fact_default_readers.py tests/analysis/test_workflow_mechanics.py tests/mcp/test_engineering_throughput.py tests/mcp/test_mcp_velocity.py "$@"

# Lifetime title corrections use canonical keyed snapshots.
verify-title-aggregates:
    just lint
    just typecheck
    pytest -q tests/substrate/test_product_lineage.py tests/analysis/test_substrate_promote_personal.py tests/mcp/test_analytical_read_provenance.py

[positional-arguments]
verify-temporal-consumers *args:
    just lint
    just typecheck
    pytest -q tests/core/test_primitives.py tests/sources/test_source_activitywatch.py tests/sources/test_source_spotify.py tests/ingest/test_activity_content_materialize.py tests/ingest/test_temporal_signals_materialize.py tests/ingest/test_personal_materializers.py "$@"

[positional-arguments]
verify-index-recipe *args:
    just lint
    just typecheck
    pytest -q tests/ingest/test_activitywatch_event_index_materialize.py tests/sources/test_activitywatch_derived.py tests/test_materialization.py -k 'event_index or indexed or recipe' "$@"

[positional-arguments]
verify-partition-integrity *args:
    just lint
    just typecheck
    pytest -q tests/materializers/test_partition_store.py tests/ingest/test_activity_content_materialize.py "$@"

[positional-arguments]
verify-machine-promotion *args:
    just lint
    just typecheck
    pytest -q tests/substrate/test_machine_promote.py tests/sources/test_source_machine.py tests/substrate/test_promote_rows_transaction.py tests/substrate/test_duck_substrate_schema.py tests/substrate/test_product_lineage.py tests/substrate/test_read_snapshot.py tests/analysis/test_machine_context_spine.py tests/substrate/test_machine_metric_series_by_context.py tests/graph/test_machine_promotion_staleness.py tests/mcp/test_mcp_machine_explain.py tests/analysis/test_operator_daily_machine_pressure.py "$@"
