# Sinevec Integration Plan

## Goal
Ensure all new derived artefacts (session summaries, wearable stats, instrumentation metadata) gain semantic search coverage via the Sinevec + Voyage embedding stack.

## Collections
- `sessions-v1` – Level-1 progressive summaries (see `pipelines/sessions/README.md`).
- `wearables-daily-v1` – Daily aggregates (sleep, steps, HR, stress) once pipelines land.
- `instrumentation-v1` – Metadata rows for terminal/audio/screencap events.
- `dashboards-v1` – Narrative blurbs accompanying dashboard exports.

## Workflow
1. Prepare `.env` with `VOYAGE_API_KEY` and start Sinevec devshell (`nix develop`).
2. For each artefact type, emit an NDJSON payload with fields: `id`, `text`, `metadata`, `channel`, `category`.
3. Run `sinevec embed --input artefacts.ndjson --collection <name> --category <category>` (CLI wrapper to write to Qdrant).
4. Record embedding ids back into the source index (e.g., `artefacts/ledgers/session_index.csv`).
5. Use `sinevec search` to quick-check retrieval, then expose saved queries in dashboard notebooks.

## Automation Hooks
- Add `just embed-sessions` to call the CLI for new Level-1 summaries.
- Schedule nightly job: summarise sessions → embed → update index.
- Log embedding runs under `logs/sinevec/` (already supported by CLI) and link them in `docs/analysis-log.md` when major batches complete.

## Follow-ups
- [ ] Define metadata schema (provider, project, time range, source path, summary level).
- [ ] Draft NDJSON template and helper in `pipelines/sinevec/sinevec_helpers.py`.
- [ ] Backfill existing baseline summaries once the helper ships.
