set shell := ["bash", "-euo", "pipefail", "-c"]

default:
    @just --list

test:
    pytest -q

lint:
    ruff check lynchpin tests

# --- Analysis -----------------------------------------------------------------------

analysis-refresh spec="lynchpin/analysis/analysis_spec.json":
    python -m lynchpin.analysis refresh --spec "{{spec}}"

analysis-dry-run spec="lynchpin/analysis/analysis_spec.json":
    python -m lynchpin.analysis refresh --spec "{{spec}}" --dry-run

# --- Utilities ----------------------------------------------------------------------

clean-generated:
    rm -rf artefacts tmp scratch
    find . -type d -name '__pycache__' -prune -exec rm -rf {} +
