# Data Organization Proposal (Filesystem + Lynchpin Modules)

## Goals
- Reduce entropy under `/realm/data/` without breaking existing pipelines.
- Keep exports append-only and traceable (raw inputs preserved, derived outputs regenerable).
- Make module namespaces mirror the data domains to simplify discovery.

## Observations (current state)
- `/realm/data/<domain>/` is already the canonical pattern for GDPR/exports (reddit/spotify/google/health/wykop/etc.).
- Internal layouts vary (`raw/`, `processed/`, `derived/`, `archive/` appear in some but not all domains).
- Lynchoin modules are grouped by layer (`sources/ingest/views/system`) but not by domain.

## Proposed filesystem conventions (non-breaking)
Use a shared internal layout under each `/realm/data/<domain>/` root:

- `raw/`: immutable raw exports (ZIP/TAR/CSV/JSON) as downloaded.
- `processed/`: cleaned/merged inputs (e.g., `sleep_merged.jsonl`).
- `derived/`: regenerable artifacts built from `raw/` + `processed/` (e.g., `full_history.ndjson`).
- `archive/`: legacy or superseded data kept for audit.
- `INVENTORY.md`: in-place doc describing structure + source URL.

This preserves the existing top-level roots, while making each domain predictable.

## Proposed low-bucket grouping (conceptual)
No new directories required; this is a *classification* to guide inventories + module layout.
Keep the number of buckets low (3–4) and based on *how data arrives* rather than domain.

### Option A: 3 buckets (minimal)
- **captures**: continuous/local telemetry and logs (ActivityWatch, Atuin, asciinema, screenshots, audio, webhistory raw)
- **exports**: GDPR/Takeout/zip dumps (reddit, spotify, google takeout, health exports, raindrop, goodreads, etc.)
- **collections**: long-lived libraries (substack archives, documents, bookmarks, vaults)

### Option B: 4 buckets (slightly clearer)
- **captures**: local telemetry + instrumentation
- **exports**: GDPR/Takeout/zip dumps
- **libraries**: curated long-lived collections
- **indices**: derived stores (duckdb warehouse, qdrant, sinevec state)

## Proposed lynchpin namespace grouping (breaking change, requires approval)
Keep the layer split (`sources`, `ingest`, `views`, `system`) but add *bucket* subpackages:

```
lynchpin/sources/
  captures/{activitywatch,atuin,webhistory,webhistory_raw,instrumentation,codex}
  exports/{reddit,spotify,health,goodreads,raindrop,takeout}
  libraries/{substack,dendron,chatlog,polylogue}
  indices/{gitstats,repos}
```

This would replace `lynchpin.sources.reddit` with `lynchpin.sources.exports.reddit`, etc.
No shims unless explicitly requested.

### Migration strategy (if approved)
1. Move modules into domain subpackages.
2. Update all imports + CLI entrypoints in repo.
3. Update `docs/reference/lynchpin-module-map.md` to include domain category.
4. Optional: add `lynchpin/domains.py` (single map of domain → data root → module list).

## Decisions needed
- Choose bucket scheme (3 or 4) or keep it flat.
- Do we want a purely *conceptual* grouping (docs only), or a full module namespace migration?
- Should `/realm/data/` remain the flat domain root, or should we consider a new `exports/captures/...` hierarchy later (larger change)?

## Next actions (if approved)
- Apply the domain subpackage migration for `lynchpin.sources`.
- Harmonize inventory layouts for any remaining domain roots lacking `raw/`/`processed/`.
- Add a `lynchpin/domains` map to make it easy to enumerate sources by category.
