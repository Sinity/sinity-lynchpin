# Sinevec Integration Plan

## Goal
Ensure all new derived artefacts (session summaries, wearable stats, instrumentation metadata) gain semantic search coverage via the Sinevec + Voyage embedding stack.

## Collections
- `sessions-v1` – Level-1 progressive summaries (see `docs/reference/sessions/README.md`).
- `wearables-daily-v1` – Daily aggregates (sleep, steps, HR, stress) once the relevant source integrations land.
- `instrumentation-v1` – Metadata rows for terminal/audio/screencap events.
- `dashboards-v1` – Narrative blurbs accompanying dashboard exports.

## Workflow
1. Prepare `.env` with `VOYAGE_API_KEY` and start Sinevec devshell (`nix develop`).
2. For each artefact type, emit an NDJSON payload with fields: `id`, `text`, `metadata`, `channel`, `category`, using direct `lynchpin.*` module outputs or warehouse extracts rather than an ad-hoc pipeline tree.
3. Run `sinevec embed --input artefacts.ndjson --collection <name> --category <category>` to write into the vector store.
4. Record embedding ids back into the source index (for example `artefacts/knowledge/ledgers/session_index.csv`).
5. Use `sinevec search` to quick-check retrieval, then expose saved queries in the relevant downstream docs or views.

## Automation Hooks
- If a dedicated exporter is needed, keep it under `lynchpin.analysis` or `lynchpin.views`, not under `pipelines/`.
- Schedule the sequence explicitly as: summarise sessions → export NDJSON → embed → update index.
- Log embedding runs under `logs/sinevec/` (already supported by CLI) and link them in `docs/analysis-log.md` when major batches complete.

## Follow-ups
- [ ] Define metadata schema (provider, project, time range, source path, summary level).
- [ ] Draft an NDJSON template and helper under the live `lynchpin/` package surface if repeated exports justify it.
- [ ] Backfill existing baseline summaries once the export shape is stable.
