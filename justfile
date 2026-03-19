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
    python -c 'from pathlib import Path; from lynchpin.analysis.projects import build_velocity_dashboard; projects = """{{projects}}""".split() or None; exclude = """{{exclude}}""".split() or None; aggregate = """{{aggregate}}""".strip().lower() in {"1", "true", "yes", "on"}; build_velocity_dashboard(output=Path("""{{output}}"""), project_names=projects, exclude_names=exclude, aggregate=aggregate, log=print)'

# Materialize repomix-backed project bundles.
project-bundles output_root="/realm/project/_context-project-bundles" projects="" logs_count="30" include_diffs="false" include_compressed="true":
    python -c 'from pathlib import Path; from lynchpin.analysis.projects import build_project_bundles; projects = """{{projects}}""".split() or None; include_diffs = """{{include_diffs}}""".strip().lower() in {"1", "true", "yes", "on"}; include_compressed = """{{include_compressed}}""".strip().lower() in {"1", "true", "yes", "on"}; build_project_bundles(output_root=Path("""{{output_root}}"""), project_names=projects, logs_count=int("""{{logs_count}}"""), include_diffs=include_diffs, include_compressed=include_compressed, log=print)'

# Materialize richer structural project bundles with git-history shards.
project-bundles-rich output_root="/realm/project/_context-project-bundles/rich" projects="" patch_window="10" summary_window="100" patch_commits="200" summary_commits="":
    python -c 'from pathlib import Path; from lynchpin.analysis.projects import build_rich_project_bundles; projects = """{{projects}}""".split() or None; patch_commits = """{{patch_commits}}""".strip(); summary_commits = """{{summary_commits}}""".strip(); build_rich_project_bundles(output_root=Path("""{{output_root}}"""), project_names=projects, patch_window=int("""{{patch_window}}"""), summary_window=int("""{{summary_window}}"""), patch_commits=int(patch_commits) if patch_commits else None, summary_commits=int(summary_commits) if summary_commits else None, log=print)'

# Write the session ledger CSV.
session-index sessions_dir="docs/reference/sessions" output="artefacts/knowledge/ledgers/session_index.csv":
    python -c 'from pathlib import Path; from lynchpin.analysis.knowledge import write_session_ledger; result = write_session_ledger(sessions_dir=Path("""{{sessions_dir}}"""), output=Path("""{{output}}""")); status = ("Wrote " + str(result.row_count) + " session rows to") if result.wrote else "Session ledger unchanged at"; print(status, result.output)'

# Write the artefact ledger CSV.
artefact-index catalog="docs/reference/ledgers/artefact_catalog.json" output="artefacts/knowledge/ledgers/artefact_index.csv":
    python -c 'from pathlib import Path; from lynchpin.analysis.knowledge import write_artefact_ledger; result = write_artefact_ledger(catalog=Path("""{{catalog}}"""), output=Path("""{{output}}"""), base_dir=Path(".").resolve()); missing_list = ", ".join(result.missing_artifacts); missing = f" (missing paths: {missing_list})" if missing_list else ""; action = "Wrote" if result.wrote else ("Reused" if result.missing_artifacts else "Artefact ledger unchanged at"); message = f"{action} {result.artefact_count} artefacts -> {result.output}{missing}" if action != "Artefact ledger unchanged at" else f"{action} {result.output}"; print(message)'

# Summarize a session transcript into artefacts/knowledge/sessions.
summarise-session input output="" model="" max_chars="20000" force="false":
    python -c 'from pathlib import Path; from lynchpin.analysis.knowledge import summarise_session_transcript; output = Path("""{{output}}""") if """{{output}}""".strip() else None; force = """{{force}}""".strip().lower() in {"1", "true", "yes", "on"}; result = summarise_session_transcript(Path("""{{input}}"""), output=output, model="""{{model}}""", max_chars=int("""{{max_chars}}"""), force=force, log=print); print(f"Summary already exists at {result.output_path}" if result.skipped else f"Summary written to {result.output_path}")'

# --- Ingest -------------------------------------------------------------------------

# Refresh terminal session artefact (speeds up trajectory signal loading 400x)
ingest-terminal:
    python -m lynchpin.ingest.instrumentation terminal-metadata

# Refresh polylogue signal artefact (speeds up trajectory signal loading ~30x for polylogue)
ingest-polylogue:
    python -m lynchpin.ingest.polylogue

# Refresh git commit signal artefact (speeds up trajectory signal loading ~5x for git)
ingest-git:
    python -m lynchpin.ingest.git

# Refresh ActivityWatch window/web signal artefacts (speeds up trajectory loading ~10x for AW)
ingest-aw:
    python -m lynchpin.ingest.aw

# Refresh ActivityWatch artefacts for the last N months only (faster incremental refresh)
ingest-aw-recent months="2":
    python -m lynchpin.ingest.aw --months {{months}}

# Refresh all ingestible sources
ingest-all: ingest-terminal ingest-polylogue ingest-git ingest-aw

# --- Trajectory / Warehouse ---------------------------------------------------------

# Materialize all trajectory tables to the DuckDB warehouse
trajectory-refresh:
    python -m lynchpin.views.warehouse refresh --sources trajectory

# Emit current context state JSON (default 14-day window)
context-state days="14":
    python -m lynchpin.context.state --days {{days}} --stdout

# Update persistent memory store (claims + themes) from 90-day trajectory window
context-memory-update:
    python -m lynchpin.context.memory update

# Export trajectory episodes + themes as temporally-grounded KG nodes/edges
knowledge-graph:
    python -m lynchpin.views.knowledge_graph build-trajectory

# Render weekly calendar views for a date range (e.g. just calendar-weeks 2026-03-01 2026-03-31)
calendar-weeks start end:
    python -m lynchpin.views.calendar_views build-weeks {{start}} {{end}}

# Render monthly calendar views for a date range (e.g. just calendar-months 2026-01-01 2026-03-31)
calendar-months start end:
    python -m lynchpin.views.calendar_views build-months {{start}} {{end}}

# Render quarterly calendar views for a date range (e.g. just calendar-quarters 2026-01-01 2026-12-31)
calendar-quarters start end:
    python -m lynchpin.views.calendar_views build-quarters {{start}} {{end}}

# Render yearly calendar views for a date range (e.g. just calendar-years 2025-01-01 2026-12-31)
calendar-years start end:
    python -m lynchpin.views.calendar_views build-years {{start}} {{end}}

# --- Utilities ----------------------------------------------------------------------
