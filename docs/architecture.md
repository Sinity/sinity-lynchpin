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

AgentCTL job observations are promoted into the work substrate through the native `agentctl job list --json --all` read route. They retain their source revision, route provenance, and lifecycle caveats while fields absent from that route remain unavailable. This does not make Lynchpin an AgentCTL client for execution: scheduling, cancellation, waiting, and supervision remain outside the source and substrate layers.

Every coherent build has a `refresh_id`. Readers select a materialized refresh
rather than joining arbitrary generations of tables. Substrate schema changes
may rebuild the database because source inputs and materialized products remain
the durable authorities.

The serving database also carries a `publication_id`, distinct from the logical
promotion `refresh_id`. Every candidate publication changes this ID even when
it updates a product inside the same promotion. The status manifest records the
ID, publication time, changed products, and a publication reason. Old
generations without this metadata have an unknown publication ID. An audited
index rebuild seeds from the newest publication and prefers serving on a
legacy timestamp tie.

Claim pages and claim detail share the highest-coverage materialized claims
refresh by default. A page returns both its `refresh_id` and serving
`publication_id`; pass the refresh ID with `next_offset` to keep later pages on
that logical generation. Detail responses include the same identities, including
when the pinned generation does not contain the requested claim. Explicit
refresh IDs are authoritative and never fall back to another generation.

The query surface supports stable readers, a structured JSON query DSL, and
SELECT-only SQL with bounded response pages. In the DSL, `limit` is the total
requested result scope and `max_rows` is the page size (at most 10,000 rows).
Continue with the returned `next_offset`, the same `order_by`, and the serving
`publication_id` as `expected_publication_id`; a changed publication is rejected.
SQL and DSL results include the selected serving refresh and its recorded
per-source status, row count, window, and observation time in `freshness`.
These rows describe the retained promotion. The separate freshness status
reports whether current inputs are known to differ; a recorded `ok` source
does not claim current acquisition coverage.
Unknown query fields and public action filters are rejected. Mutation is not
exposed through query tools.

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

The current `evidence_edge` table has a known integrity defect: 929,084 of
937,615 edges (99.09%) are orphaned because one or both endpoint nodes are
absent. Graph readers filter those rows out, so edge counts and traversals are
the resolving subset unless a response includes the `graph_integrity` caveat.
The defect is recorded on graph-serving responses and is not repaired in this
maintenance-only implementation.

The planned absorption drops this `evidence_edge` implementation rather than
migrating it. The replacement uses typed parent references with foreign-key
enforcement, which does not represent this defect class.

### 6. Products and interfaces

The graph, substrate readers, and analysis modules feed:

- current-state context packs and chronological timelines;
- project/code dashboards and maps;
- personal and machine analysis artifacts;
- readiness, coverage, and confidence reports;
- the eight-tool public MCP contract;
- explicit materialization and maintenance operations with receipts.

`lynchpin_status(view="materialization")` returns compact product status and
partition counts. Supply `source` to inspect one product and `detail=true` for
its paths and covered dates. Paired `start` and `end` dates report coverage of
that inclusive window without refreshing products. `tail_stale` distinguishes
new live input from historical repair requirements.

Materialization manifests retain the identity of each declared input file, including the SQLite WAL where applicable. A missing identity is unverified, and an input replacement invalidates the product even when the newest mtime and file count are unchanged. Date extrema describe observed event extent; only explicit covered dates or a source-specific verified index establish coverage between them. Raw capture directory mtimes do not supply event dates.

Generated artifacts live under the ignored local root or configured derived
root. Tracked documentation describes contracts, not generated personal
results.

`lynchpin_project` owns [campaign evidence and historical task products](reference/campaign-evidence.md). Its owner adapter acquires exact Beads revisions; callers do not construct task snapshots.

The `project_context` action returns independently covered task, graph evidence, trajectory, and verification components. It reads one retained graph generation and reuses the graph context-pack projections without refreshing or reading raw sessions. Task and runtime revisions remain independent owner observations; they are never labeled as members of the graph generation. A missing graph or runtime owner leaves the other components available. The byte budget is a presentation target. Complete component data, owner references, source revisions, coverage, and payload digests survive even when that target is exceeded, so the gateway can retain the exact observation before budgeting its presentation. Exact graph/task replay selectors remain in `owner_ref`; runtime observations can change on replay. `lynchpin_catalog` publishes complete JSON input schemas for these project actions, generated from the same models that validate their public routes.

## Freshness and convergence

Read paths may converge an owned materialized product when its contract permits
bounded rebuilding. Normal unpinned substrate reads use the typed convergence
plan, durable coverage, source fingerprints, and a short freshness interval to
reuse warm decisions and single-flight identical work. They do not launch an
uncontrolled scan of every raw source for every query. Explicit refresh-pinned
reads remain immutable. Status surfaces report whether a result is ready,
degraded, missing, or stale and preserve the reason.

AgentCTL or nightly semantic scheduling can call
`python -m lynchpin.cli.converge --start YYYY-MM-DD --end YYYY-MM-DD --json`
for a serializable dry-run plan, adding `--execute` to publish bounded work and
record the receipt in the existing freshness ledger.
Nightly tail maintenance checks the code snapshot state but leaves Chisel
portfolio builds to an explicit `just chisel` or code-snapshot materialization;
a Git ref moving does not schedule a full portfolio rebuild overnight.
`agentctl job start lynchpin materialize_plan` previews this bounded maintenance
selection, including check-only products. An explicit all-products plan is
available through `python -m lynchpin.cli.materialize --all --plan-json`.
The live machine telemetry SQLite database serves machine reads and graph
promotion; nightly maintenance checks that source without rebuilding the large
NDJSON offline copy. `python -m lynchpin.ingest.machine_materialize` can refresh
that copy explicitly.
The declared `lynchpin converge` operation emits one JSON progress event per
source start and outcome, with product identity, queue wait, elapsed time, and
the selected window. These events describe work in progress; a source success
does not by itself establish a published graph generation.
For a dated source with a proven historical product more than 31 days behind,
each maintenance pass processes one bounded window. A manifest with an
unfinished window stays eligible on the next pass even if its input fingerprint
is current. Publication stops at the earliest scheduled source window end until
later passes catch up.
ActivityWatch derived reads resolve retained partitions under the configured
derived root after a root move. A failed window materialization is reported
to the reader rather than silently returning an older partition set.

High-amplification canonical products use immutable logical partitions selected by an atomic manifest. ActivityWatch events and activity-content daily rows use logical days, keylog analysis uses logical days, and title metadata uses source months. A maintenance tail may replace only affected selections. Artifact bytes are content-addressed, so an unchanged partition keeps its path and inode on a warm rerun. The previous manifest remains the serving manifest until every new artifact has been staged, validated, and the replacement manifest has been fsynced.

The monolithic NDJSON and JSON carriers remain bounded migration adapters. They are retained until a full partition migration has passed row-count and coverage validation, all default readers have consumed the logical manifest for one release cycle, and the compatibility-read test has been retired. A future retirement change must remove the adapter and carrier together after those conditions are recorded in the release notes.

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
