# Data Materialization Audit

This note records the current audit of Lynchpin source paths where raw data,
processed materializations, cachew caches, and query-time fallbacks can become
confused.

## Invariants

- Raw/provider exports are authority.
- Processed/canonical files are reproducible materializations from raw inputs.
- Runtime caches accelerate deterministic readers only; they are not source
  products.
- Query-time analysis should read canonical materializations when a source has
  one. Missing canonical products should be a repair signal, not an implicit
  scan of partial raw/segment directories.
- Alternate parser branches for file formats are acceptable only inside ingest
  or explicit raw-audit code.

## Fixed In This Pass

### Materialization Contract

`python -m lynchpin.cli.materialization_audit` now reports strict
materialization status for the known dataset surface. `ready` means a canonical
materialized product exists for the query surface. Live source DB scans,
single-export selection, and incomplete ontology migrations are reported as
`partial` instead of being treated as normal readiness.

`--ensure-supported` rebuilds the products Lynchpin can safely refresh locally
without extra credentials. Today that includes canonical webhistory plus
Spotify, Reddit, Raindrop, and Facebook Messenger export products.

### Webhistory

Default query-time reads now require
`/realm/data/captures/webhistory/gestalt/derived/full_history.ndjson`.
Segment scans remain available only for explicit `root=` calls and ingest/audit
paths. The materialized file was verified by rebuilding the merge logic over
canonical segments:

- input visits from canonical segments: 1,613,754
- merged rows: 933,799
- duplicate rows removed by URL plus +/-30s timestamp dedup: 679,955
- coverage: 2013-03-27 through 2026-05-23
- manifest:
  `/realm/data/captures/webhistory/gestalt/derived/full_history.manifest.json`

Raw Takeout archives are authoritative for Takeout Chrome history. The retired
`takeout-extracted` cache tree was removed after raw archive presence was
verified.

### Polylogue

`lynchpin.sources.polylogue` now fail-closes for required insight products.
Session profiles, bounded profile reads, work events, day summaries, and message
transcript reads raise `PolylogueMaterializationError` when the Polylogue
products or facade are unavailable. A materialization outage is no longer
collapsed into empty AI-chat evidence.

### Substance

The substance CSV reader had a `cachew` wrapper over a dataclass containing
`datetime.time`, which cachew cannot serialize. That caused a runtime cache
setup error and an implicit non-cached fallback on every read. The wrapper was
removed; the processed CSV is now read directly until/unless a serializable
canonical product is introduced.

### Export Coalescing

Spotify, Reddit, Facebook Messenger, and Raindrop no longer use latest-export
selection as their default query surface. `python -m
lynchpin.ingest.exports_materialize all` writes canonical coalesced products:

- Spotify:
  `/realm/data/exports/spotify/processed/streaming_history.ndjson`
  (`263,254` rows, `2013-02-12` through `2025-12-18`)
- Reddit:
  `/realm/data/exports/reddit/processed/canonical/` (`45` canonical CSV
  products, `67,879` total rows)
- Facebook Messenger:
  `/realm/data/exports/comms/facebook-messenger/processed/canonical/messages.ndjson`
  (`4,262` messages, `18` threads, `2024-02-07` through `2026-01-03`)
- Raindrop:
  `/realm/data/exports/raindrop/processed/bookmarks.csv` (`27,452`
  bookmarks, `2020-03-01` through `2026-01-03`)

The corresponding source modules now require those canonical products for
default reads. Per-file or per-root parser paths remain available only for
explicit ingest/audit calls.

### Terminal History

Atuin command history is now materialized to
`/realm/data/captures/shell/atuin/history.ndjson` with a sibling manifest.
Default terminal source reads use that canonical product instead of opening the
live Atuin SQLite database. Current materialization contains `79,234` commands
covering `2025-04-03` through `2026-05-23`.

### ActivityWatch

ActivityWatch events are now coalesced from the live SQLite database plus
processed archive DBs into
`/realm/data/captures/activitywatch/events.ndjson`. Default ActivityWatch source
reads use that canonical product; direct DB reads are reserved for the
materializer. Current materialization contains `980,808` events across window,
AFK, and browser buckets, covering `2024-10-14` through `2026-05-23`.

### Machine Telemetry

Machine telemetry is materialized from the live SQLite database into canonical
NDJSON tables under `/realm/data/captures/machine/processed/`. Current products
cover metric, GPU, network, and service-state samples with `839,875` total rows
from `2026-05-12` through `2026-05-23`. Default machine source reads use these
products; explicit SQLite reads remain for the materializer.

### Google Takeout Inventory

Raw Google Takeout archives are now inventoried into
`/realm/data/exports/google/processed/takeout-inventory/`. The inventory covers
`28` archives and `101,493` archive members across Google product families.
Chrome history is promoted into canonical webhistory. Non-Chrome products are
materialized into `/realm/data/exports/google/processed/takeout-products/`:
Contacts, Keep notes, My Activity, Google Play Store, purchases/reservations,
Tasks, YouTube CSV products, and typed asset rows for Drive/Fit/Photos/Mail/
YouTube plus structured-member inventories for Location History, Maps, and
Google Pay. Calendar is intentionally not a supported dataset; current Chat/
Gemini exports are empty stubs and are recorded as skipped products in the
manifest rather than reified as first-class empty sources.

## High-Risk Remaining Findings

### Context-Pack Substrate Refresh Semantics

`lynchpin.graph.context_pack` now fails closed when a caller prefers substrate
evidence and no materialized DuckDB graph matches. `--refresh-substrate` is the
explicit live rebuild/materialize path; ordinary current-state reads no longer
silently rebuild a live graph on substrate miss.

### Analysis Artifact Loaders

Several analysis-artifact source modules return empty tuples when expected
artifacts are absent. For optional overlays this is fine; for required evidence
surfaces it should be surfaced as missing readiness rather than collapsed to
zero evidence. Machine-analysis context-pack rendering now surfaces missing or
malformed required artifacts directly in the rendered Machine Analysis section.

## Lower-Risk Or Legitimate Patterns

- `cachew` and `lru_cache` uses in source readers are runtime accelerators when
  their signatures depend on source files.
- Parser alternate-shape handling inside `web.py`, `takeout_chrome.py`, and
  source-specific CSV/JSON loaders is not a materialization fallback.
- ActivityWatch raw access already scans live SQLite plus processed archive DBs
  and dedups rows. Archive processing still deserves a separate audit against
  old aw-server-rust dump formats before declaring complete historical support.
