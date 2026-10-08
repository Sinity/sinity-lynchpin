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
SQL and DSL results include the selected serving refresh and, in
`freshness`, the recorded status, row count, window, and observation time of
the sources the query's tables map to, with per-status counts for every other
source; `detail: true` returns every source row. A table without a recorded
source mapping is listed as unmapped rather than attributed to a guessed
source. These rows describe the retained promotion. The separate freshness
status reports whether current inputs are known to differ; a recorded `ok`
source does not claim current acquisition coverage.

Raw tables keep every retained promotion, so a raw SQL result is history, not
one current snapshot, and `grain` says what each referenced table holds.
Lineage products (personal signals, activity content, title classifications,
and graph tables) store one partition per promotion: filtering `refresh_id`
returns only what that promotion replaced. Typed readers resolve a lineage
product at its newest eligible head, so a revised key reads its latest value
and a replaced or tombstoned key is absent, while the raw partitions keep each
earlier revision as evidence.

Explicit database paths are compared by resolved filesystem identity, so a
relative, `..`, or symlinked spelling of the serving database meets the same
write guard. A read-only fallback opens only the selected database's own
snapshot, and serving observations report the file DuckDB actually opened.
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

Materialization manifests retain the identity of each declared input file, including the SQLite WAL where applicable. A missing identity is unverified, and an input replacement invalidates the product even when the newest mtime and file count are unchanged. A materializer observes that identity before it reads, so a manifest never names a later input state than the one consumed; when a commit races the read, the manifest records `input_changed_during_read` and the product is not reused at that identity. A missing, locked, or unreadable owner database fails the acquisition and leaves the last published product in place; only a readable database with no rows is an observed-empty input. Date extrema describe observed event extent; only explicit covered dates or a source-specific verified index establish coverage between them. Raw capture directory mtimes do not supply event dates.

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
The nightly `lynchpin converge` operation uses the same candidate-based
incremental route as `promote_incremental`. It clones the previous serving
generation, refreshes selected source tails, and atomically publishes the graph
and personal-product overlays after verification. It does not run the separate
complete substrate import over all history. Products requiring an explicit
schema migration or lacking verified historical input identities remain
check-only, with their unavailable coverage recorded in the new generation.
Explicit plans read their selected upstream products during temporal and
ActivityContent computation, rather than starting recursive refreshes. The
nightly source window ends at the current logical day boundary. ActivityContent
keeps title-use fact replacements in one SQLite transaction and can migrate a
damaged compatibility export from its retained facts.
The explicit `repair_webhistory_base` operation calls the existing merge with
`--merge-only --output <candidate>`: it reads every retained canonical segment,
records their current identities, and skips raw browser extraction. Verify that
candidate before replacing a legacy merged carrier; its identities describe the
new base rather than an earlier run.
Unavailable owner readiness becomes missing dataset coverage with an unknown
count. Optional source errors remain visible and degrade the snapshot; required
source and graph errors still reject publication.
The incremental graph tail starts no later than the last compatible published
graph coverage end, even when source checkpoints advanced during a failed
publication. Its nightly window is capped at the current logical day boundary.
Each incremental candidate has a distinct graph refresh identity, including
repeated runs over the same date window, so its predecessor remains eligible.
A source success does not by itself establish a published graph generation.
Runtime and compact status report the recorded promotion time and freshness
alongside the refresh ID. They expose the existing systemd timer's next trigger
and bounded journal evidence for the latest completion and failure; an old
serving promotion or a newer failed nightly run cannot report healthy readiness.
For a dated source with a proven historical product more than 31 days behind,
each maintenance pass processes one bounded window. A manifest with an
unfinished window stays eligible on the next pass even if its input fingerprint
is current. Publication stops at the earliest scheduled source window end until
later passes catch up.
ActivityWatch derived reads resolve retained partitions under the configured
derived root after a root move. A failed window materialization is reported
to the reader rather than silently returning an older partition set.

High-amplification canonical products use immutable logical partitions selected by an atomic manifest. ActivityWatch events and activity-content daily rows use logical days, keylog analysis uses logical days, and title metadata uses source months. A maintenance tail may replace only affected selections. Artifact bytes are content-addressed, so an unchanged partition keeps its path and inode on a warm rerun. The previous manifest remains the serving manifest until every new artifact has been staged, validated, and the replacement manifest has been fsynced.

Personal substrate products (daily signals, activity-content days, buckets and title usage, title classifications) are promoted as refresh partitions over a predecessor chain in `substrate_product_lineage`. A partition's rows, tombstones and lineage row commit in one transaction, and its input is read completely before any write, so a failed read or write leaves the earlier partition and its metadata as they were; the promotion then records an `error` status and the candidate generation is rejected. An incremental partition replaces its predecessors' dated rows only inside the half-open range it read (`replacement_start`, `replacement_end`); rows outside that range stay visible, and every newer range in the chain hides ancestor rows, not only the nearest one. Title-classification readiness reports the titles the refresh serves (`logical_row_count`), including titles inherited unchanged, rather than the rows that run wrote.

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

Canonical analyses use the configured subject analysis directory. Chisel publishes each native project package to `projects/<project>/snapshots/current`, attachment archives to its `snapshots/exports`, and earlier generations to `snapshots/history`. The shared portfolio under `projects/shared/snapshots` holds navigation and explicit package locations. Portable output roots keep their self-contained layout. Publication validates all selected packages before changing a home, retains predecessors, and records interruption evidence before substrate promotion. An unfinished publication journal prevents another writer from replacing its recovery evidence.
