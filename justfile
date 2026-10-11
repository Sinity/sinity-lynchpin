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
