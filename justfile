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

ecosystem-dashboard spec="lynchpin/analysis/analysis_spec.json":
    python -m lynchpin.analysis ecosystem-dashboard --spec "{{spec}}"

ecosystem-dashboard-serve spec="lynchpin/analysis/analysis_spec.json" host="127.0.0.1" port="8765":
    python -m lynchpin.analysis ecosystem-dashboard-serve --spec "{{spec}}" --host "{{host}}" --port "{{port}}"

scaffold-browse host="127.0.0.1" port="8766":
    python -m lynchpin.scripts.scaffold_browser --host "{{host}}" --port "{{port}}"

# Materialize the default cross-project velocity dashboard.
velocity output="/realm/project/knowledgebase/lynchpin/repo-artefacts/meta/velocity/velocity.html" projects="" exclude="" aggregate="true":
    python -m lynchpin.analysis.projects velocity --output "{{output}}" --projects "{{projects}}" --exclude "{{exclude}}" --aggregate "{{aggregate}}"

# Materialize repomix-backed project bundles.
project-bundles output_root="/realm/project/_context-project-bundles" projects="" logs_count="30" include_diffs="false" include_compressed="true":
    python -m lynchpin.analysis.projects bundles --output-root "{{output_root}}" --projects "{{projects}}" --logs-count "{{logs_count}}" --include-diffs "{{include_diffs}}" --include-compressed "{{include_compressed}}"

# Materialize richer structural project bundles with git-history shards.
project-bundles-rich output_root="/realm/project/_context-project-bundles/rich" projects="" patch_window="10" summary_window="100" patch_commits="200" summary_commits="":
    python -m lynchpin.analysis.projects rich-bundles --output-root "{{output_root}}" --projects "{{projects}}" --patch-window "{{patch_window}}" --summary-window "{{summary_window}}" --patch-commits "{{patch_commits}}" --summary-commits "{{summary_commits}}"
