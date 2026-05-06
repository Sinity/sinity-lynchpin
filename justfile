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
