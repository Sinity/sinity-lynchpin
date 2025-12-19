# Ledgers (Machine-Readable Indexes)

This repo keeps a few small CSV/JSON/JSONL “ledger” files under `artefacts/ledgers/` so other tooling (dashboards, embeddings) can consume structured metadata without scraping Markdown.

## Artefact ledger
- Source of truth: `pipelines/ledgers/artefact_catalog.json`
- Generated output: `artefacts/ledgers/artefact_index.csv`
- Regenerate:
  ```bash
  direnv exec /realm/project/sinity-analysis just artefact-index
  ```

## Session ledger
- Source of truth: `docs/reference/sessions/*.md`
- Generated output: `artefacts/ledgers/session_index.csv`
- Regenerate:
  ```bash
  direnv exec /realm/project/sinity-analysis just session-index
  ```
