# Architecture

Lynchpin is organized around ownership and evidence quality rather than around
one database. Raw data remains with its capture system or export; each layer
adds a more queryable representation without erasing the layer below it.

## Layers

### 1. Owner-native inputs

Inputs include local application databases, append-only captures, provider
exports, repositories, and project-native ledgers. Their owners determine
write and retention policy:

- Sinnix owns host capture and service deployment.
- Polylogue owns AI-session ingestion and archive-native products.
- Git and GitHub own code/change records.
- Provider exports remain immutable source artifacts.

Lynchpin reads these inputs; ordinary analysis does not rewrite them.

### 2. Source APIs

`lynchpin.sources` converts owner-native records into typed Python values and
graduated APIs. A source module owns:

- discovery and availability checks;
- parsing and normalization local to that format;
- coverage bounds and source caveats;
- lazy iteration and safe caching;
- raw access when forensic detail is required;
- daily/session summaries when they can be derived without cross-source state.

Source modules do not become a second warehouse. Cross-source joins belong in
materialized products, the substrate, or analysis.

### 3. Canonical materialized products

Some formats require expensive discovery, deduplication, or normalization
before repeated analysis is practical. Materializers write these canonical,
rebuildable products under the configured derived root and record:

- input fingerprints;
- coverage and row counts;
- the producer and schema version;
- freshness and readiness;
- reasons a product cannot be rebuilt.

`python -m lynchpin.cli.materialize --all` plans the dependency-ordered work.
`--force` invalidates normal freshness decisions; `--strict` turns incomplete
readiness into a non-zero exit.

### 4. DuckDB substrate

The substrate is a coherent analytical snapshot over a selected time window.
Promoters load canonical rows into typed tables for work, projects, personal
signals, machine state, GitHub/review context, claims, and graph products.

Every coherent build has a `refresh_id`. Readers select a materialized refresh
rather than joining arbitrary generations of tables. Substrate schema changes
may rebuild the database because source inputs and materialized products remain
the durable authorities.

The query surface supports stable readers, a structured JSON query DSL, and
SELECT-only SQL with row caps. Mutation is not exposed through query tools.

### 5. Evidence graph

The graph represents facts and qualified relationships such as:

- project ↔ commit ↔ file/symbol;
- AI work event ↔ commit overlap;
- issue ↔ pull request ↔ commit closure;
- activity/focus/terminal evidence ↔ logical day;
- analysis claim ↔ supporting evidence;
- workload ↔ machine/service context.

Edges carry provenance and confidence. Weak keyword or temporal proximity
signals are optional and remain distinguishable from deterministic links.

### 6. Products and interfaces

The graph, substrate readers, and analysis modules feed:

- current-state context packs and chronological timelines;
- project/code dashboards and maps;
- personal and machine analysis artifacts;
- readiness, coverage, and confidence reports;
- the eight-tool public MCP contract;
- explicit materialization and maintenance operations with receipts.

Generated artifacts live under the ignored local root or configured derived
root. Tracked documentation describes contracts, not generated personal
results.

## Freshness and convergence

Read paths may converge an owned materialized product when its contract permits
bounded rebuilding. They do not launch an uncontrolled scan of every raw source
for every query. Status surfaces report whether a result is ready, degraded,
missing, or stale and preserve the reason.

The normal lifecycle is:

1. Discover source readiness and coverage.
2. Plan missing or invalid materializations.
3. Build canonical products in dependency order.
4. Promote one coherent substrate snapshot.
5. Build graph and analysis products against that refresh.
6. Serve CLI/MCP queries with refresh and evidence metadata.

## Evidence levels

Lynchpin distinguishes:

- **fact** — directly parsed or deterministically computed from a named source;
- **association** — observational relationship with coverage and timeframe;
- **qualified inference** — rule/model output with explicit confidence;
- **causal claim** — supported by an experiment or design that justifies the
  causal language;
- **narrative** — bounded synthesis over the preceding evidence.

An upstream summary never becomes raw truth merely because it is convenient.
When evidence is incomplete, the product should say so rather than filling the
gap with a plausible story.
