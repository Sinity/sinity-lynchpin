# Data sources

Lynchpin source modules are typed read APIs over owner-native data. Raw
captures and exports stay in their configured locations; source modules expose
availability, coverage, provenance, iterators, and source-local summaries.

## Roles

| Role | Examples | Contract |
| --- | --- | --- |
| Owner-native input | Application database, append-only capture, provider export, repository | Remains authoritative and is not rewritten by analysis. |
| Source API | `lynchpin.sources.*` | Parses lazily, preserves source caveats, and exposes typed values. |
| Canonical product | Derived NDJSON/manifest under the configured data root | Rebuildable normalization for formats that are expensive or ambiguous to query repeatedly. |
| Substrate table | `lynchpin.substrate.*` | Windowed DuckDB read model tied to a coherent `refresh_id`. |
| Analysis artifact | `lynchpin.analysis.*` output | Generated metric, map, diagnostic, or claim product with provenance. |
| Context pack | `lynchpin.graph.context_pack` | Bounded synthesis over graph/substrate evidence. |

## Source families

| Family | Representative modules | Evidence exposed |
| --- | --- | --- |
| Workstation activity | `activitywatch`, `terminal`, `clipboard`, `keylog`, `arbtt` | Focus spans, commands, sessions, recordings, input/activity events. |
| Code and delivery | `git`, `github`, `github_context`, `code_snapshots`, `xtask_history`, `polylogue_verification` | Commits, files, reviews, issues/PRs, snapshots, build/test history. |
| AI work | `polylogue`, `polylogue_timeline` | Session profiles, work events, costs, provider activity, timelines. |
| Machine state | `machine`, `machine_experiments`, `service_health`, `sinnix_generations` | Metrics, pressure, services, experiments, backups, generations. |
| Web and reading | `web`, `takeout_chrome`, `bookmarks`, `raindrop_live` | Visits, domains, bookmarks, content metadata, daily activity. |
| Communications | `communications`, `gmail_takeout`, `irc`, `outlook`, `sms`, export adapters | Events, threads, daily counts, provenance. |
| Health and daily signals | `health`, `sleep`, `personal_signals`, `weather` | Measurements, coverage-aware daily products, longitudinal signals. |
| Media and libraries | `spotify`, `substack`, `spotify_genres`, `audio_features`, export adapters | Streams, sessions, downloaded publication archives, library records, daily media signals. |
| Generated evidence | `analysis_artifacts`, `source_observations`, `observability_catalog` | Artifact inventory, extracted claims, source/role definitions. |

Gmail Takeout materialization retains full message bodies, headers, MIME data and encoded message content alongside summary previews. `find_materialized_gmail_messages` searches the retained body and headers. Undated messages remain in the canonical product with an explicit date status; date-bounded activity excludes them. An unreadable archive prevents replacement of the last complete Gmail product.

The ordinary Raindrop reader uses the canonical CSV, including completed API metadata and highlights. Its schema 3 preserves full native metadata, parent-linked highlights, creation and update dates, observed active/trash state, and separate primary/API observation timestamps and provenance. Explicit CSV locations remain configured when missing, so an inaccessible override cannot silently select another export. It chooses one validated API bundle per rebuild and includes every bundle payload and marker in freshness checks. CSV and API details compete by export observation time; older API metadata cannot overwrite newer CSV details. API state and rich fields retain their own dated provenance when newer CSV details win. Missing input or an incomplete-only API history fails refresh and preserves the preceding product. Creation-day summaries count provider IDs once despite retained URL variants.

The canonical Raindrop CSV preserves observed ID/URL pairs across exports. Details for the same pair come from the newest file modification time, with filename as the deterministic tie breaker. Earlier URLs remain historical observations; absence from a later CSV is not a deletion event. Each row records its winning source, and the manifest records every inspected export, including exports that contribute no winning rows.

Raindrop API acquisition publishes complete metadata bundles from sibling staging directories. Re-exporting preserves the prior package and returns the newly published path, which the CLI also uses for subsequent copy acquisition. Retained metadata and highlights resolve one bundle together, ordered by recorded export time (dated-directory time for older markers). New bundles carry full-file SHA-256 digests; older completion markers require valid payloads, matching row counts, and no payload written after the marker. Unmarked or incomplete packages remain retained and unavailable through the completed-snapshot reader; their presence is not zero activity.

Retained highlights join bookmarks through the provider's `raindropRef`, preserving the highlight's separate string `_id`. A missing parent reference raises a source error. The [Raindrop highlights contract](https://developer.raindrop.io/v1/highlights) documents these separate identities.

Live Raindrop reads preserve creation and modification timestamps separately. An older creation time cannot terminate an update-filtered read: the [API](https://developer.raindrop.io/v1/raindrops/multiple) orders by creation and permits at most 50 items per page. The unused cursor-only poll, which retained no records, is retired. Explicit durable acquisition uses `python -m lynchpin.ingest.raindrop_archive --metadata`; a completed validated bundle is its progress evidence. Historical cursor files remain retained.

Canonical communication event IDs hash the versioned semantic fields, including
the complete message text, normalized timestamp, recipients, media count, and
raw kind. The 240-character excerpt is presentation data only; exact semantic
duplicates still coalesce because providers do not always supply event IDs.

The exact filesystem roots come from `LynchpinConfig`. Tests use temporary
roots and neutral fixtures; the public source tree does not depend on one
operator's data layout.

Managed Sinnix captures resolve placement from `/etc/sinnix/filesystem-layout.json`.
`LYNCHPIN_FILESYSTEM_LAYOUT` selects a registry for standalone deployments and
tests; source-specific explicit root overrides retain precedence. A missing or
inaccessible configured event source raises `SourceUnavailableError`, while an
accessible empty directory returns no records. Availability and inventory
observations preserve that distinction. Transcript readiness and shared file
readiness use unknown row counts for unavailable inputs; readable empty inputs
retain zero counts.

Chisel uses `LYNCHPIN_CHISEL_CACHE_ROOT` for reusable caches and
`LYNCHPIN_CHISEL_SCRATCH_ROOT` for temporary history stores. Defaults are the
configured cache directory's `chisel` child and a task scratch directory.
Managed Chisel and convergence operations declare AgentCTL scratch; inside those
jobs, temporary history uses `AGENTCTL_SCRATCH/chisel` so the job owner measures
and removes it on completion. The host scratch setting applies outside jobs.
Choosing an output directory does not relocate either store. Publication
candidates remain beside their destination for atomic same-filesystem renames.

Outlook activity uses the dated inbox/sent CSV exports. They provide the
message dates, directions, and correspondent fields needed by daily activity
and canonical communications products. Direct PST extraction is unsupported:
the historical `readpst` path read only selected folders, could reuse partial
cache output, and did not establish complete mailbox coverage. Calling
`lynchpin.sources.outlook.iter_pst_emails()` reports this capability gap;
ordinary Outlook reads never invoke an extractor.

The Google Takeout inventory keeps one canonical `members.ndjson` and
`archives.ndjson` pair. Its manifest records each archive's filesystem version
(device, inode, size, mtime, and ctime), output digests and versions,
unreadable archives, and per-run reuse counts. A rebuild reuses rows only when
that archive version and the previous output digests match; changed archives
are traversed once. Missing or unreadable archives, and output files that
differ from the published manifest, remain visible as incomplete coverage in
source readiness.

A web-history visit is identified by its normalized URL and its time to the
second, within the dedup tolerance, whichever carrier observed it: the live
Chrome profile, successive Takeout archives, and manual exports all record the
same visits. The kept row carries its carrier's `source` as provenance. Live
profiles are labelled by browser directory (`chrome-ws`), qualified only for a
non-default profile (`chrome-ws/Profile 1`).

A canonical bookmark row is one occurrence: one bookmark object in one browser
profile, identified by its native GUID, or by folder, exact URL, title, and
added time where the format has none. Repeated copies of one profile's export
collapse; the same URL in another folder, profile, or browser stays distinct.
An active profile's bookmarks carry its live-profile label as their profile.
Daily bookmark activity counts each addition (exact URL and added time) once.
An unreadable archived export is listed in the manifest's
`unreadable_input_files` and the rest of the product is built; an unreadable
active-profile `Bookmarks` file refuses the build.

The machine source reads live telemetry SQLite when available. Its offline canonical fallback uses `processed/manifest.json` to select immutable daily NDJSON partitions. A bounded refresh writes only the requested days and replaces the manifest after those files are durable. Version 1 packages with one NDJSON file per table remain readable; on a bounded refresh that file stays as the historical base while partition entries replace the touched dates for current readers. A fresh package can be built entirely from partitions. A full rebuild over an existing legacy monolith requires a separately verified migration because the live SQLite database alone may not reproduce its complete history. The machine staging cleanup command also identifies unreferenced partition files, retaining them for at least 24 hours so readers holding an earlier manifest can finish.

IRC uses `lynchpin.sources.irc_raw` over the canonical materialized event
product, with raw WeeChat logs as its explicit fallback. It exposes
operator-centered conversation units with a bounded preceding context window,
gap-based boundaries, operator/mention/total counts, and source-file
provenance. The units remain untrimmed structured input: selecting the minimum
context needed for interpretation is a model step. Channel counts are already
part of the IRC materialization manifest; no separate channel-summary product
is maintained.

## sinnix-capture-v1 desktop event lanes

Sinnix writes five small, continuous JSON-lines lanes under
`activity/<lane>/<lane>-YYYYMMDD.jsonl`: `notifications` (desktop
notification bus), `mpris` (media-player state), `audio-index` (speech-segment
index over the `audio` capture — index only, not the audio),
`audio-topology` (PipeWire graph add/remove events), and `screen-frames`
(per-window screen frame capture: geometry, monitor, workspace, and focused
window identity alongside each dedup'd WebP frame). `lynchpin.sources.
sinnix_capture_lanes` reads the shared envelope and exposes each lane as a
typed record (`notification_events`, `mpris_events`, `audio_index_entries`,
`audio_topology_events`, `screen_frame_events`) plus `daily_lane_activity` for
coverage-aware daily counts. All five are registered as capture sources in
`available_sources()` and `CAPTURE_SOURCES`, so they show up in
`source_observations()` like any other continuous capture.

## Atuin repair inputs

Atuin exports keep their existing file identity when the complete serialized
output is byte-identical. Source manifests still record fresh source observations.
ActivityWatch repair fingerprints every normalized Atuin record by logical day,
including duplicates; these hashes describe day records, not the whole raw file.
Metadata-only rewrites therefore do not invalidate historical repair evidence.

Changed days also invalidate retained dates covered by overlapping raw AFK or
window intervals. Incremental refresh expands to those dates before publishing.
Unknown influence refuses reuse. Older stat-only repair manifests require an
initial full repair; the new hashes do not establish retrospective continuity.

## Processed Samsung exports

The existing health processors publish each JSONL product and its manifest in
an immutable generation, then atomically switch one `current` pointer. The
existing filenames remain the entrances. Fused sleep's two entrances and its
statistics, trim log and unmatched-session sidecar share that pointer. Writers
serialize and sync the complete bundle before publication; manifest counts,
date bounds and the full SHA-256 digest describe the emitted bytes.

Readers select one generation per product, while an active input snapshot keeps
its captured identity. Migration preserves original payloads and metadata, and
previous generations remain retained. They are owner-managed recovery material,
not age-based cleanup candidates. These publication guarantees do not establish
raw-export conservation or per-record source-snapshot provenance.

## Health coverage report

`lynchpin.ingest.health_coverage_materialize` reduces the phone events
plane's `health_*` records (the sinnix app's Health Connect capture) into
`derived_root/health/health_coverage.ndjson`: per record-kind × source
package × device model × recording-method groups it reports canonical
unique-record counts (by Health Connect `record_id`), event totals and the
events-per-record ratio (a duplication-defect signal), measurement-time
bounds, largest internal gap plus a gap histogram over unique records,
sweep completion/failure receipts per record type, lane-block reasons,
typed deletion tombstones, pending exercise-route consents, and timestamp
anomalies (epoch-era measurement times are surfaced, never aggregated
over). Registered as the `health_coverage` contract with a transparent
materializer, so `materialize --all` refreshes it whenever an events day
file outgrows the report. Bare event totals are banned as coverage answers
by design — "Samsung sleep: 251 / Band 10 sleep: N" is the intended answer
shape (sinnix-3jnc).

## Xiaomi cloud witness lane

`lynchpin.sources.xiaomi_cloud` reads
`health/xiaomi-cloud/xiaomi-cloud-YYYYMMDD.jsonl`, written every 30
minutes by the sinnix `sinnix-xiaomi-witness` timer: the Mi Band's data as
Xiaomi's servers hold it, independent of the Health Connect path.
Envelopes carry daily aggregates (`vendor_sleep` with
sleep_score/REM/segments, `vendor_*_day`), the band's dense raw series
(`vendor_raw_*`: ~2-minute heart rate, continuous SpO2/stress, 5-minute
steps/calories), and FDS sleep-detail blobs when firmware uploads them.
Write-on-change: one envelope per (kind, day) revision — `xiaomi_envelopes`
streams every revision, `latest_envelopes` keeps the current state per
logical key. The health coverage report joins this lane against the HC
events plane as `witness` rows: per-night vendor sleep vs HC sleep-session
union with overlap minutes, per-day vendor HR sample counts vs HC unique
record counts (counts corroborate presence and density, not equality —
HC records are series-shaped).

## Terminal-session reconstruction

`lynchpin.analysis.terminal_reconstruction.reconstruct_session(session_id)`
joins one asciinema session's own capture lanes into a single record
recreating what that terminal session looked like: the cast (`session.cast`),
its kitty scrollback snapshots (`terminal.kitty_scrollback_captures`, keyed by
`(kitty_pid, window_id)`), and its geometry timeline (`screen_frame_events`
filtered to `window_class == "kitty"` and a matching, glyph-normalized window
title). It is a downstream join over three already-continuous captures, not a
fourth capture lane, per this doc's own cross-source-joins-belong-downstream
invariant.

The cast↔kitty-window link is the one fact nothing on disk records directly:
it is read from the still-running asciinema recorder process's own
environment (`KITTY_PID`/`KITTY_WINDOW_ID`, inherited from the kitty window
that launched it — the same technique sinnix-ops-reducer's
`terminals.py:live_streams()` uses for its live-stream routing), so it only
resolves for a session whose recorder is still alive; a session whose
recorder has exited comes back `link_method = "unresolved"` rather than a
guess. `write_reconstruction` writes the record as JSON under
`derived_root/terminal-reconstruction/<session_id>.json`.

## Capture roots without a dedicated source

Some `activity/*` (and `comms/*`) roots have real, growing owner-native data
(audio, screen recordings, screenshots) but no typed source module yet — the content itself
(audio, images, video) is out of scope for deep parsing.
`lynchpin.sources.capture_inventory` gives these roots the same minimal
visibility `observability_catalog` gives machine/observability inputs: file
count, total bytes, and observed mtime span per root, computed live from the
filesystem — no content parsing. Run `python -m lynchpin.cli.capture_inventory`
for a summary, or `--json` for machine-readable output. Promote a root out of
this catalog into a real typed source (with coverage, a materializer, and
substrate rows) when an analysis actually needs its content, not before — the
four event lanes above made that jump.

`captures/input-dynamics`, `captures/stability-lab`, and
`code/tortoisesvn` (moved from `captures/dev/tortoisesvn` 2026-08-17) are deliberately absent from this catalog: the
operator flagged them as dead/unwanted (2026-08-12) — `input-dynamics` is
superseded by `activity/keylog` (see `keylog_dynamics`), `stability-lab` is
dead or being retired, and the `tortoisesvn` historical import (now at `data/code/tortoisesvn`) is not worth
tracking. The directories themselves were not touched; only this catalog
stopped watching them.

## Substack archives

The owner-native archive root is `LYNCHPIN_SUBSTACK_ROOT`, defaulting to `/realm/library/web/substack`. Each publication is a directory containing the original HTML, Markdown, or text files produced by `sbstck-dl`; the downloader checkout and binary may remain alongside that archive. Lynchpin writes the rebuildable canonical index to `LYNCHPIN_DERIVED_ROOT/substack/posts.ndjson` with a sibling manifest. The index keeps publication, slug, title, publication timestamp, original source path, format, content hash, and content, so analyses can read the normalized product without rewriting the archive.

The downloader is configured through `LYNCHPIN_SUBSTACK_DOWNLOADER`, defaulting to `/realm/library/web/substack/sbstck-dl/sbstck-dl`. The integrated command derives the publication directory and then materializes the index:

```bash
lynchpin-substack download --url https://www.astralcodexten.com/ --publication acx --format html --rate 2
lynchpin-substack materialize
```

`_md` publication directory suffixes are treated as alternate format downloads of the same publication key. HTML wins over Markdown and text when the same publication and slug appear more than once. The original files and all input paths remain preserved for auditability.

## Invariants

- Missing coverage is not zero activity. Sources report observed bounds and
  whether they are continuous captures or bounded exports.
- Source-local normalization belongs in the source module; cross-source joins
  belong downstream.
- Cached values are invalidated by source signatures or explicit freshness
  contracts.
- Substrate rows and summaries are indexes, not replacements for raw logs.
- Generated analysis claims carry their artifact and refresh provenance.
- A legacy format leaves active discovery only after its canonical replacement
  is verified and the migration is complete.

## AgentCTL job observations

Verification runs from each repository's own verifier land in the same `work_observation` model, one source per producer: `xtask_history` reads Sinex's xtask ledger and `polylogue_verification` reads Polylogue's durable verification lane (the JSONL its devtools append one receipt to per run, resolved exactly as the writer resolves it). Both fill the same columns: `source_id` is the run, `project` the repository, `git_commit` the head, `operation` the tier (`check`, `focused-test`, `quick`, ...), `status` the shared outcome vocabulary (`success`, `failed`, `running`, `cancelled`, plus `interrupted`), `outcome_known` true only for settled runs, and the timings. Polylogue's selection and step summary sit in `args`; Sinex's stages and per-test results in their own tables. `git_branch` separates default-branch runs from change runs; xtask always records it and Polylogue receipts carry it when the producer records one. The Polylogue lane is read whole with the last row per run winning. A malformed row, or a receipt describing no real run (a non-commit source revision, an end before its start), is never ingested: it marks the work source degraded. The Polylogue lane carries no host.

Historical work-observation windows keep their exclusive end; only a query ending on the current day includes its live tail. A missing configured Sinex ledger is unavailable coverage, distinct from an observed empty ledger.

AgentCTL is an owner-native, continuous work-observation source. Lynchpin reads only the native JSON rows from `agentctl job list --json --all`. The materialization audit reports this as a live read-only route, never as a Lynchpin materializer or a filesystem product. Each promoted row has the durable job ID, public project, the declared operation name, lifecycle phase, process timestamps when published, a hash over the sanitized public record, and route provenance. The operation is what makes per-operation duration and regression questions answerable inside the substrate instead of only through an external join on the job ID; `label` is exactly `project:operation` and is deliberately not carried as a second representation. When `started_at` is absent, `enqueued_at` is retained as the observation timestamp with an explicit caveat; it does not establish process start. The route does not publish artifact refs, host identity, resource metrics, or snapshot identity. A substrate refresh remains the generation boundary.

The adapter never reads private launch files, raw job records, prompts, shell argv, environment values, logs, or result payloads. The native route does not expose a restart/recovery marker or Polylogue/Sinex receipt refs, so those fields remain unavailable rather than inferred.

This source is read-only. Materialization has no route to schedule, cancel, wait for, or supervise jobs. A job-list response is current durable lifecycle state, not a complete lifecycle event stream.

### Phone and band queries

`lynchpin_personal(action="phone", start=..., end=..., limit=...)` reads phone event captures. Health views `phone_health`, `xiaomi`, and `coverage` expose Health Connect records, the latest Xiaomi revision per measurement day, and the materialized coverage artifact respectively. These reads do not trigger materialization. Phone date filters select capture dates, including backfills; Xiaomi filters select measurement days. Coverage is a whole-history artifact with an explicit modification timestamp and does not accept date filters.

For bounded recovery, `agentctl job start lynchpin promote_incremental -- --only activitywatch_derived activity_content health_coverage personal_daily_signals temporal_signals` selects those products and their dependencies. The planner retains each product's incremental date window; unrelated writers are excluded and existing substrate history is retained through candidate publication. Add `--plan-json` to inspect the selected scope before execution.


Managed capture placement is resolved from Sinnix's exported filesystem-layout registry. `LYNCHPIN_FILESYSTEM_LAYOUT` selects a registry for standalone deployments; its `activity_lanes` map contains relative paths beneath the configured data root's `activity` home. Explicit source roots retain precedence. Event readers raise `SourceUnavailableError` for missing or inaccessible configured directories; an accessible empty directory yields no events. Shallow inventory uses unknown counts and an availability reason for unavailable sources.


### Temporal calculations and retained products

Interval validation, duration and ordering use elapsed instants. Hour allocation retains the starting timezone's hour labels, including repeated hours and fractional offset transitions. Spotify monthly totals use the same logical dates as acquisition filters. ActivityWatch content acquisition uses logical-day boundaries.

Activity content schema 2, personal daily signals schema 3, and ActivityWatch derived schema 6 identify the corrected temporal calculations. ActivityWatch focus spans split at the logical-day boundary, and each derived daily window covers 06:00 through 06:00 the following day. Keypress filtering, counts, silence gaps and AFK inference use elapsed instants across repeated hours; cached press files are searched by interval rather than rescanned for every AFK event. Outage calculations also normalize timezone-aware bounds, measure elapsed instants, split long gaps at logical-day boundaries and count continuing event coverage. An unavailable outage source remains an error instead of becoming zero outage time. A bounded refresh refuses older retained calculations. Run an unbounded rebuild through the owning materialization operation after its inputs are available; failed rebuilds preserve the selected generation. Readiness marks older products partial until that rebuild succeeds.

ActivityWatch event-index manifests record the logical-day boundary, effective timezone identity and rules digest, and deduplication settings. Unchanged source bytes do not make an index current after those rules change. Bounded refreshes refuse missing or different recipes; readiness requests an explicit full rebuild. Current generation readers reject incompatible recipes and resolve one manifest per read. Index schema 3 also records each partition’s latest event end, so long events remain available to overlapping queries without scanning unrelated partitions. Older indexes require a full rebuild; historical packages retain a conservative overlap read route without being marked current. Event timestamps and payloads are preserved.

Disabling refresh with `ensure=False` still reads the retained canonical ActivityWatch index for covered historical windows. Explicit SQLite overrides and unindexed tail windows read the native source. Missing selected index partitions remain explicit errors instead of silently changing the input route.
