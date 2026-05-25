# Lynchpin—Polylogue Boundary

**Arc K.4 / P.10.** Formal boundary between the two systems.
**Updated:** 2026-05-09, reflecting Arc 1–K completion.

## What polylogue owns

- **Archive storage** — raw conversations, messages, content blocks, provider
  events in sqlite (polylogue.db). Lynchpin never writes to this database.
- **Ingestion** — file watcher daemon (polylogued), browser capture, Google
  Drive polling, inbox imports.
- **Derived products** — session profiles, work events, day/week summaries,
  phase inference, work threads, tag rollups, cost rollups, provider analytics.
  These are the *authoritative inference surface*.
- **API contracts** — `SyncPolylogue` facade, `SessionProfileInsight`,
  `SessionWorkEventInsight`, `DaySessionSummaryInsight` types. Lynchpin
  consumes these exclusively — no raw sqlite reads outside `archive_readiness`.

## What lynchpin owns

- **Substrate** — DuckDB mirror of polylogue's work events (plus git, AW,
  terminal, health, etc.) for cross-source SQL joins. polylogue data is
  *promoted* into the substrate, not used directly.
- **Re-classifier overlay** (`lynchpin/graph/work_event_kind.py`) — three feature
  extractors (path, tools, duration) produce resolved labels with confidence
  tiers (high/medium/low). The overlay may disagree with the upstream event
  label; disagreement is tracked in `kind_source`.
- **Kind audit** (`kind_audit` MCP tool) — quantitative surface for agreement
  rates, tier distributions, and disagreement cases. Current: 25.2% disagreement
  rate across 1,295 work events.
- **Cross-source correlation** — joins polylogue work events with git commits,
  ActivityWatch focus, terminal sessions, and GitHub items. polylogue data is one
  dimension among many in `project_day_correlation`.

## The boundary rules

1. **Lynchpin reads polylogue through `SyncPolylogue` only.** The sole
   exception is `archive_readiness()` — an escape-hatch sqlite probe that
   must function when the facade itself is broken (schema-version mismatch,
   pydantic validation error on legacy records).

2. **Lynchpin never writes to polylogue.db.** All derived data lives in the
   DuckDB substrate or in lynchpin's JSON artifacts.

3. **Polylogue owns archive inference; Lynchpin owns cross-source resolution.**
   Lynchpin's overlay is a *re-classifier*, not a replacement. When the overlay
   disagrees with the upstream event label, both labels are preserved
   (`source_kind` + `overlay_kind`), and the disagreement is tracked. The
   `kind` column represents the final resolved label.

4. **Schema versioning is decoupled.** Polylogue's `SCHEMA_VERSION` and
   lynchpin's `SUBSTRATE_VERSION` evolve independently. When polylogue's
   schema changes, the `SyncPolylogue` facade is the schema-adaptation layer;
   lynchpin's promotion path uses the facade's contract, not raw sqlite.

5. **Readiness flows upward.** Polylogue's `health_check` /
   `session_insight_status` → lynchpin's `archive_readiness` →
   lynchpin's `substrate_readiness_report`. Each layer surfaces its own
   readiness without duplicating the layer below.

## When the boundary shifts

| Event | Lynchpin action |
|---|---|
| Polylogue schema version bump | Bump `inputs.polylogue` in sinnix flake; no lynchpin code change needed (facade absorbs). |
| New polylogue inference product (e.g., embeddings) | Add the table-family promoter in the owning `lynchpin/substrate/*.py` module; add substrate table; wire into MCP. |
| Polylogue retires a product | Remove the corresponding lynchpin promoter and substrate table in the same `SUBSTRATE_VERSION` bump. |
| Lynchpin adds a new re-classifier feature | Add extractor to `work_event_kind.py`; column already exists in `ai_work_event` schema. |

## Verification

```bash
# Check polylogue readiness from lynchpin's perspective
direnv exec . python -c "
from lynchpin.sources.polylogue import archive_readiness
print(archive_readiness())
"

# Check kind agreement rates
direnv exec . python -c "
from lynchpin.mcp.tools.views import kind_audit
r = kind_audit()
print(f'disagreement_rate: {r[\"disagreement_rate\"]}')
print(f'tiers: {r[\"tier_distribution\"]}')
"
```
