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

# --- Analysis -----------------------------------------------------------------------

analysis-refresh spec="lynchpin/analysis/analysis_spec.json":
    python -m lynchpin.analysis refresh --spec "{{spec}}"

analysis-dry-run spec="lynchpin/analysis/analysis_spec.json":
    python -m lynchpin.analysis refresh --spec "{{spec}}" --dry-run

ecosystem-dashboard spec="lynchpin/analysis/analysis_spec.json":
    python -m lynchpin.analysis ecosystem-dashboard --spec "{{spec}}"

ecosystem-dashboard-serve spec="lynchpin/analysis/analysis_spec.json" host="127.0.0.1" port="8765":
    python -m lynchpin.analysis ecosystem-dashboard-serve --spec "{{spec}}" --host "{{host}}" --port "{{port}}"

# Materialize the default cross-project velocity dashboard.
velocity output=".lynchpin/generated/meta/velocity.html" projects="" exclude="" aggregate="true":
    python -m lynchpin.analysis.projects velocity --output "{{output}}" --projects "{{projects}}" --exclude "{{exclude}}" --aggregate "{{aggregate}}"

# Build XML repomix snapshots with semantic splitting + issues + git log.
chisel projects="" output_root="" max_workers="4":
    python -m lynchpin.analysis.projects chisel \
        --projects "{{projects}}" \
        --output-root "{{output_root}}" \
        --max-workers {{max_workers}}

# --- Tool inventory -----------------------------------------------------------------

# List all Lynchpin tools: MCP tools (enumerated live), CLI entry points, dev recipes.
tool-inventory:
    @echo "=== CLI Entry Points ==="
    @echo "  python -m lynchpin.cli.current_state"
    @echo "  python -m lynchpin.analysis refresh"
    @echo "  python -m lynchpin.cli.process_health"
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
