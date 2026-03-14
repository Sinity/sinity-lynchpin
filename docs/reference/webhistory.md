# Webhistory

## Purpose

Maintain the canonical gestalt webhistory dataset under `/realm/data/captures/webhistory/gestalt/`.

## Commands

- `python -m lynchpin.ingest.webhistory dedup`
  - Deduplicates raw exports under `/realm/data/captures/webhistory/gestalt/raw/`.
  - Writes deduped segments into `/realm/data/captures/webhistory/gestalt/data/`.
  - Writes audit manifests and reports into `/realm/data/captures/webhistory/gestalt/derived/`.

- `python -m lynchpin.ingest.webhistory full-history`
  - Rebuilds `/realm/data/captures/webhistory/gestalt/derived/full_history.ndjson` from deduped segments.

- `python -m lynchpin.ingest.webhistory compare`
  - Compares the canonical NDJSON against the deduped segment set.
  - Writes a JSON report to `artefacts/webhistory/gestalt_compare.json`.

## Notes

- Treat `/realm/data/captures/webhistory/gestalt/derived/full_history.ndjson` as the canonical read target.
- Earlier manual merge experiments were removed once the Typer CLI became the canonical surface.
