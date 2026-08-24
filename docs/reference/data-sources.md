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
| Code and delivery | `git`, `github`, `github_context`, `code_snapshots`, `xtask_history` | Commits, files, reviews, issues/PRs, snapshots, build/test history. |
| AI work | `polylogue`, `polylogue_timeline` | Session profiles, work events, costs, provider activity, timelines. |
| Machine state | `machine`, `machine_experiments`, `service_health`, `sinnix_generations` | Metrics, pressure, services, experiments, backups, generations. |
| Web and reading | `web`, `takeout_chrome`, `bookmarks`, `raindrop_live` | Visits, domains, bookmarks, content metadata, daily activity. |
| Communications | `communications`, `gmail_takeout`, `irc`, `outlook`, `sms`, export adapters | Events, threads, daily counts, provenance. |
| Health and daily signals | `health`, `sleep`, `personal_signals`, `weather` | Measurements, coverage-aware daily products, longitudinal signals. |
| Media and libraries | `spotify`, `substack`, `spotify_genres`, `audio_features`, export adapters | Streams, sessions, downloaded publication archives, library records, daily media signals. |
| Generated evidence | `analysis_artifacts`, `source_observations`, `observability_catalog` | Artifact inventory, extracted claims, source/role definitions. |

The exact filesystem roots come from `LynchpinConfig`. Tests use temporary
roots and neutral fixtures; the public source tree does not depend on one
operator's data layout.

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

The owner-native archive root is `LYNCHPIN_SUBSTACK_ROOT`, defaulting to `/realm/library/media/substack`. Each publication is a directory containing the original HTML, Markdown, or text files produced by `sbstck-dl`; the downloader checkout and binary may remain alongside that archive. Lynchpin writes the rebuildable canonical index to `LYNCHPIN_DERIVED_ROOT/substack/posts.ndjson` with a sibling manifest. The index keeps publication, slug, title, publication timestamp, original source path, format, content hash, and content, so analyses can read the normalized product without rewriting the archive.

The downloader is configured through `LYNCHPIN_SUBSTACK_DOWNLOADER`, defaulting to `/realm/library/media/substack/sbstck-dl/sbstck-dl`. The integrated command derives the publication directory and then materializes the index:

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

AgentCTL is an owner-native, continuous work-observation source. Lynchpin reads only the versioned `agentctl job list` public envelope. Each promoted row has the durable job ID, public project and lifecycle phase, bounded systemd memory peak when published, public artifact references, a hash over the sanitized public record, and the list snapshot provenance. `created_at` remains a durable record-creation timestamp; the public v1 reconciliation timestamp is not promoted as a terminal completion time or duration. A substrate refresh remains the generation boundary.

The adapter never reads private launch files, raw job records, prompts, shell argv, environment values, logs, or result payloads. `sinnix://` log and result URLs remain opaque external artifact references. The public v1 route does not expose host identity, a restart/recovery marker, or Polylogue/Sinex receipt refs. Those fields are represented as unavailable rather than inferred. If a later public contract emits an explicit `semantic_receipts` entry for `polylogue` or `sinex`, Lynchpin stores only the owner and opaque ref in `work_observation_receipt_ref`; it never derives a join from text or artifact contents.

This source is read-only. Materialization has no route to schedule, cancel, wait for, or supervise jobs. A job-list snapshot is current durable lifecycle state, not a complete lifecycle event stream, and a truncated page is recorded as incomplete coverage.
