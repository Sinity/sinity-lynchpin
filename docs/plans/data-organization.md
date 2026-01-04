# Data Organization (Filesystem + Lynchpin Modules)

## Goals
- Reduce entropy under `/realm/data/` without breaking existing pipelines.
- Keep exports append-only and traceable (raw inputs preserved, derived outputs regenerable).
- Make module namespaces mirror the data domains to simplify discovery.

## Observations (current state)
- `/realm/data/` is now physically bucketed into `captures/`, `exports/`, `libraries/`, `indices/`.
- GDPR/Takeout exports live under `/realm/data/exports/<domain>/` (reddit/spotify/google/health/wykop/etc.).
- Internal layouts vary (`raw/`, `processed/`, `derived/`, `staging/` appear in some but not all domains).
- Lynchpin modules are grouped by layer (`sources/ingest/views/system`) but not by domain.

## Filesystem conventions (current)
Use a shared internal layout under each `/realm/data/<domain>/` root:

- `raw/`: immutable raw exports (ZIP/TAR/CSV/JSON) as downloaded.
- `processed/`: cleaned/merged inputs (e.g., `sleep_merged.jsonl`).
- `derived/`: regenerable artifacts built from `raw/` + `processed/` (e.g., `full_history.ndjson`).
- `staging/` (optional): temporary holding area for data awaiting review; prefer `/realm/inbox/` over per-domain archives.
- `INVENTORY.md`: in-place doc describing structure + source URL.

This preserves the bucketed layout while making each domain predictable.

## Bucket layout (current)
- **captures**: local telemetry + instrumentation (ActivityWatch snapshots, Atuin, asciinema, screenshots, webhistory raw, syslog).
- **exports**: GDPR/Takeout/zip dumps (reddit, spotify, google takeout, health exports, raindrop, goodreads, chatlog, wykop).
- **libraries**: curated long-lived collections (finance, substack, document/media libraries, model assets).
- **indices**: derived stores (duckdb warehouse, qdrant, sinevec state, sinex state).

## Lynchpin namespace grouping (implemented)
Keep the layer split (`sources`, `ingest`, `views`, `system`) and add *bucket* subpackages:

```
lynchpin/sources/
  captures/{activitywatch,atuin,codex,instrumentation,webhistory,webhistory_raw}
  exports/{chatlog,comms,polylogue,reddit,spotify,health,sleep,goodreads,raindrop,takeout,wykop}
  libraries/{dendron,finance,substack}
  indices/{gitstats,repos,sessions}
```

This replaces `lynchpin.sources.reddit` with `lynchpin.sources.exports.reddit`, etc.
No shims or compatibility aliases are provided.

### Migration status
1. ✅ Modules moved into bucket subpackages.
2. ✅ Imports + CLI entrypoints updated.
3. ✅ `docs/reference/lynchpin-module-map.md` updated with bucketed modules.
4. ⏳ Optional: add `lynchpin/domains.py` (single map of domain → data root → module list).

## Decisions remaining
- Whether to add a registry helper (`lynchpin/domains.py`) for enumerating sources by bucket.

## Next actions
- Harmonize inventory layouts for any remaining domain roots lacking `raw/`/`processed/`.
- Add a `lynchpin/domains` map to make it easy to enumerate sources by category.
- Document `/realm/inbox/` usage in the realm map and inventories (staging instead of `/realm/data/archive/`).
