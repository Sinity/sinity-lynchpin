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

# Materialize the default cross-project velocity dashboard.
velocity output="artefacts/meta/velocity/velocity.html" projects="" exclude="" aggregate="true":
    python -m lynchpin.analysis.projects velocity --output "{{output}}" --projects "{{projects}}" --exclude "{{exclude}}" --aggregate "{{aggregate}}"

# Materialize repomix-backed project bundles.
project-bundles output_root="/realm/project/_context-project-bundles" projects="" logs_count="30" include_diffs="false" include_compressed="true":
    python -m lynchpin.analysis.projects bundles --output-root "{{output_root}}" --projects "{{projects}}" --logs-count "{{logs_count}}" --include-diffs "{{include_diffs}}" --include-compressed "{{include_compressed}}"

# Materialize richer structural project bundles with git-history shards.
project-bundles-rich output_root="/realm/project/_context-project-bundles/rich" projects="" patch_window="10" summary_window="100" patch_commits="200" summary_commits="":
    python -m lynchpin.analysis.projects rich-bundles --output-root "{{output_root}}" --projects "{{projects}}" --patch-window "{{patch_window}}" --summary-window "{{summary_window}}" --patch-commits "{{patch_commits}}" --summary-commits "{{summary_commits}}"

# Write the session ledger CSV.
session-index sessions_dir="docs/reference/sessions" output="artefacts/knowledge/ledgers/session_index.csv":
    python -m lynchpin.analysis.knowledge session-index --sessions-dir "{{sessions_dir}}" --output "{{output}}"

# Write the artefact ledger CSV.
artefact-index catalog="docs/reference/ledgers/artefact_catalog.json" output="artefacts/knowledge/ledgers/artefact_index.csv":
    python -m lynchpin.analysis.knowledge artefact-index --catalog "{{catalog}}" --output "{{output}}" --base-dir .

# Summarize a session transcript into artefacts/knowledge/sessions.
summarise-session input output="" model="" backend="" max_chars="20000" force="false":
    python -m lynchpin.analysis.knowledge summarise-session --input "{{input}}" --output "{{output}}" --model "{{model}}" --backend "{{backend}}" --max-chars "{{max_chars}}" --force "{{force}}"

# --- Ingest -------------------------------------------------------------------------

# Refresh terminal session artefact (speeds up activity-signal loading 400x)
ingest-terminal:
    python -m lynchpin.ingest.instrumentation terminal-metadata

# Refresh polylogue signal artefact (speeds up activity-signal loading ~30x for polylogue)
ingest-polylogue:
    python -m lynchpin.ingest.polylogue

# Refresh git commit signal artefact (speeds up activity-signal loading ~5x for git)
ingest-git:
    python -m lynchpin.ingest.git

# Refresh ActivityWatch window/web signal artefacts (speeds up activity-signal loading ~10x for AW)
ingest-aw:
    python -m lynchpin.ingest.aw

# Refresh ActivityWatch artefacts for the last N months only (faster incremental refresh)
ingest-aw-recent months="2":
    python -m lynchpin.ingest.aw --months {{months}}

# Refresh all ingestible sources
ingest-all: ingest-terminal ingest-polylogue ingest-git ingest-aw

# --- Derived / Warehouse ------------------------------------------------------------

# Materialize derived day/week/month/episode tables to the DuckDB warehouse
derived-refresh:
    python -m lynchpin.views.warehouse refresh --sources trajectory

# Emit an evidence bundle JSON for a narrative period
context-state scale="day" key="":
    python -m lynchpin.context.state --scale {{scale}} --key "{{key}}" --stdout

# Update persistent memory store (claims + themes) from a 90-day activity window
context-memory-update:
    python -m lynchpin.context.memory update

# Export derived episodes + themes as temporally-grounded KG nodes/edges
knowledge-graph:
    python -m lynchpin.views.knowledge_graph build-temporal

# Render generic period reports for a date range (e.g. just period-reports week 2026-03-01 2026-03-31)
period-reports scale start end:
    python -m lynchpin.context.reports {{start}} {{end}} --scale {{scale}}

# --- Utilities ----------------------------------------------------------------------
