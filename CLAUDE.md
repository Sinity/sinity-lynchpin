# sinity-lynchpin – Agent Orientation

> Flat file, no transclusion; `AGENTS.md` is a committed symlink to this file
> (the constellation-wide convention — audit with `verify-agent-topology`).
> Solo repo: verified work is committed and pushed directly on `master`.

## Mission

Personal data analysis hub. Centralizes ActivityWatch, shell history, git, wearables, chat archives, and exports into one coherent system. The LLM (Claude/Codex) operating within this repo is the primary consumer — it imports source modules directly to query data and write retrospective narratives.

**Core pattern**: `Source module → Iterator[Dataclass]` — graduated APIs from raw access to derived analytics. Raw exports stay untouched under `/realm/data/...`. Source modules parse lazily with cachew memoization.

## Beads Issue Tracking

This repository uses `bd` (Beads) for durable project task tracking.

- Run `bd prime` when task context, ready work, blockers, or durable project
  memory matter.
- Use `bd ready --json`, `bd show <id> --json`, `bd update <id> --claim --json`,
  and `bd close <id> --reason "..." --json` for tracked work.
- Create linked Beads issues for discovered follow-up work instead of leaving
  markdown TODO lists as the source of truth.
- `bd dolt push` follows the same repo policy as `git push`: this solo repo may
  push verified `master` work directly unless an active workflow says to hold.

## Public Repository Boundary

Assume every tracked file, commit, branch, tag, Beads issue, Actions log, and
GitHub discussion is public.

- Generic adapters, schemas, analysis code, and neutral synthetic fixtures
  belong here. Secrets, private datasets, raw captures or exports, private
  narratives or transcripts, and unrelated personal information do not.
- Operator datasets, identities, vocabularies, life events, classifications,
  and narrative evidence stay under externally configured data roots.
- `.agent/{reports,scratch,handoff,ops}/`, root `.claude/`, and
  `.beads/interactions.jsonl` are local-only.
- Beads `issues.jsonl` is public technical archaeology; all of its fields must
  satisfy the same publication boundary as source and documentation.
- Before every commit, review the complete staged diff and run
  `scripts/check-publication-boundary`. The checker only catches known path and
  file shapes; it cannot judge prose, fixtures, or arbitrary data.
- If there is any doubt whether content belongs in the public repository,
  confirm with the operator before committing it.
- Publish only `master`. Never push `--mirror`, `--all`, or `--tags`; any new
  branch or tag requires an explicit publication review first.
- If private material was committed, stop publication, rotate any live secret,
  rewrite the allowed branch, and verify a fresh clone. Deleting the current
  file does not remove it from history.

## Position in Lynchpin (the personal system)

`lynchpin/` is the Python analysis layer over `/realm/data/`, local repos, and
processed exports. The knowledgebase is a separate sibling repository for
settled notes and archival personal material.

Methodology and provenance invariants for hard-data work live in
`lynchpin/analysis/METHODOLOGY.md` — that is the contract for analysis
products. LLM-written narratives must be synthesis over deterministic context
packs and analysis products (explicit provenance/caveats), not replacements
for measured evidence.

Tool ownership: root `tool/` holds `current-state`, `devshell-motd`,
`github-frontier`, and `narrative` — lynchpin context-pack and reporting
helpers. `just chisel` packages XML repomix snapshots for GPT-Pro work and
writes the stable snapshot set to
`/realm/data/derived/lynchpin/code-snapshots`.

**What NOT to duplicate across repos:** do not copy knowledgebase
`permanent.concept.*` framings into lynchpin docs, and do not re-author
methodology invariants that already live in `METHODOLOGY.md` — link or
cross-reference instead of inventing parallel vocabulary layers.

## Documentation Map

| Topic | Location |
|-------|----------|
| Data source contracts + coverage | `docs/reference/data-sources.md` |
| Observability model (machine telemetry) | `docs/reference/observability-model.md` |
| Materialization audit | `docs/reference/data-materialization-audit.md` |
| Polylogue/Lynchpin ownership boundary | `docs/lynchpin-polylogue-boundary.md` |
| Active plans | `docs/plans/` |
| Analysis methodology contract | `lynchpin/analysis/METHODOLOGY.md` |

## Architecture

```
lynchpin/
├── core/             Shared infrastructure (config, classify, periods, primitives, analytics,
│                     parse, cache, coverage, errors, evidence/evidence_graph/work_event_kind DTOs)
├── sources/          Data sources — each file is a self-contained graduated API
├── graph/            Cross-source read models: evidence-graph builder, context packs, movement,
│                     readiness, work correlation, current-state, narrative, intent_delivery
├── analysis/         LARGEST layer — codebase + cross-source analytics toolkit. Subpackages incl.
│                     machine, ecosystem, active, change, code_index, frontier, projects,
│                     sinex, interpretation, knowledge, maps; plus operator_daily, life_phase,
│                     anomaly_crossref, productivity_predictors, substance_health, health_modeling
├── ingest/           Data acquisition/import tools (webhistory, etc.)
├── cli/              Command-line entrypoints (current_state, process_health, …)
├── mcp/              Read-only FastMCP server over the DuckDB substrate (stdio transport)
├── substrate/        DuckDB derived/read store (schema, connection, promoters, views, readers)
└── materialization.py  Orchestrates promoting source rows into the substrate
```

Use `python -m lynchpin.analysis lynchpin-self` and `pytest -q` for current
size/test counts; static counts in this file are intentionally avoided.

### Core Modules

| Module | Purpose |
|--------|---------|
| `config.py` | `LynchpinConfig` — all paths from env vars. `get_config()`. `available_sources()` → which data exists on disk |
| `classify.py` | `classify(app, title, url, cwd)` → `Attribution(mode, project, topic)`. Also `resolve_project()`, `extract_topics()` |
| `periods.py` | Period algebra: 6 scales (day→week→month→quarter→half→year). `parse_period()`, `child_keys()`, `hierarchical_relpath()` for narrative paths |
| `analytics.py` | `detect_trend()` (Mann-Kendall), `detect_changepoints()` (PELT), `detect_periodicity()` (FFT), `cross_correlate()`, `cluster_days()` (k-means), `anomaly_score()` (IQR/MAD) |
| `primitives.py` | `TopN` (ranked accumulator), `group_by_gap()` (session merge), `Interval` arithmetic (`merge_intervals`, `intersect_intervals`, `split_by_day/hour`), `date_to_dt_range()`, `logical_date()` — THE day bucketer |
| `parse.py` | Date/int/float parsing, `month_key()`, `iter_dates()`, `as_local()`, `local_tz()` |
| `cache.py` | `persistent_cache` decorator (cachew SQLite), `file_signature()`, `files_signature()`, `file_digest()`, `write_text_if_changed()` |
| `projects.py` | `ALL_PROJECTS` registry + file classifiers. `project_profiles()` for analysis |
| `coverage.py` | `CoverageBounds` (observed `[first, last]` per source, capture vs export `kind`), `partition_by_coverage()` (split dates into in-coverage vs out-of-coverage so missing ≠ zero) |
| `errors.py` | Typed error taxonomy: `LynchpinError` base + `SourceUnavailableError`, `SchemaVersionError`, `MaterializationError`, `DataCoverageError`. Prefer these over bare exceptions at source/substrate boundaries |
| `claude_sdk.py` | Claude Max subscription LLM backend (no API key). The only LLM backend in the tree |

### Source Modules — Per-Source APIs

#### `activitywatch.py` — Focus tracking, graduated layers

```
L0: events(start, end) → AWEvent
L1: active_intervals(start, end) → Interval[]     — AFK-aware active windows
    afk_intervals(start, end) → Interval[]
L2: focus_spans(start, end) → FocusSpan            — app, title, mode, project, topic, duration
L3: app_sessions(start, end) → AppSession           — grouped focus spans (same app context)
L4: deep_work(start, end) → DeepWorkBlock           — sustained coding/writing blocks ≥25min
    circadian(start, end) → CircadianProfile        — hourly heatmap (active_min, mode, project)
    loops(start, end) → FocusLoop                   — A↔B alternation patterns
    fragmentation(start, end) → FragmentationMetrics — context switch rate, focus stretch duration
    attention(start, end) → AttentionMetrics         — project scatter (entropy, gini, top project)
Daily: daily_activity(start, end) → AWDayActivity   — composite (hours, deep work, frag, hourly_active[24])
Scalar: active_seconds_by_date(start, end) → dict[date, float]
```

Project attribution: `focus_spans` calls `_enrich_with_polylogue`
(`window_session_attribution.attribute_spans`) to attribute window sessions
from polylogue work-event windows. Measure attribution coverage fresh rather
than quoting historical figures.

#### `git.py` — Code activity (live git log, never stale)

```
Raw:     commits_in_range(start, end) → GitCommit (live git log + baseline JSONL deduped)
Facts:   commit_facts(start, end) → GitCommitFact (authored_at datetime, paths, per-file detail)
         file_change_facts(start, end) → GitFileChangeFact
Daily:   daily_activity(start, end) → GitDayActivity (per repo: commits, churn, burst count)
Session: commit_sessions(start, end) → CommitSession (temporal grouping, duration_min, commit_count)
Repo:    repos() → RepoInfo, repo_files(repo) → RepoFile, repo_tokei(path) → TokeiReport
Raw:     iter_numstat(), iter_commit_activity(), summarize_commit_activity()
```

AI attribution: Co-Authored-By trailers alone are unreliable (~50% coverage;
some repos omit them). `analysis/active/ai_attribution.py` joins commits
against `polylogue.iter_session_profiles()` and assigns `high`/`medium`/`none`
tiers by same-project window/day overlap; combine with `sources.git`
`ai_coauthored` for fuller coverage. Degrades gracefully when polylogue
session products are rematerializing.

#### `terminal.py` — Shell + recordings

```
Raw:     commands(start, end) → AtuinCommand (timestamp, command, cwd, exit_code, duration)
Session: shell_sessions(start, end) → ShellSession (project, category, error_count, commands_summary)
Cast:    recordings(start, end) → TerminalRecording (asciinema .cast files)
Daily:   daily_terminal_activity(start, end) → DailyTerminalActivity
```

#### `polylogue.py` — AI chat (reads polylogue archive DB)

```
Profile: iter_session_profiles() → SessionProfile (messages, words, engaged_ms, cost, projects, tags)
Daily:   daily_activity(start, end) → ChatDayActivity (per provider)
Cost:    cost_summary(start, end) → CostSummary (daily LLM spend per provider)
Work:    work_pattern(start, end) → WorkPattern (which work kinds get AI assistance, top projects)
Stats:   archive_stats() → {conversations, messages, providers}

Covers: claude-ai, claude-code, codex, chatgpt, gemini.
Reads the Polylogue archive database directly. Session-profile and work-event
products are required for semantic chat coverage; if they are stale or absent,
Lynchpin reports Polylogue as degraded instead of synthesizing empty evidence.
```

#### `sleep.py` — Wearable sleep

```
Raw:     entries() → SleepEntry (segments, total_minutes, avg_score, quality_label)
Lookup:  sleep_for_date(date) → SleepEntry?
Range:   entries_in_range(start, end) → SleepEntry[]
Cross:   sleep_productivity(start, end) → SleepProductivity (joins next-day AW performance)
```

#### `web.py` — Browser history

```
Entries: iter_entries(start?, end?) → Dict (raw records from gestalt/NDJSON)
Visits:  iter_gestalt_events(root), iter_ndjson_events(path) → WebHistoryVisit
Daily:   daily_browsing(start, end) → WebDayActivity (visit count, domains, top sites)
Dist:    domain_breakdown(start, end, top_n?) → (domain, count, pct)[]
Summary: summarize_events_by_month(events, start, end) → (counts, domains, reddit_subs, title_tokens)
Raw:     raw_files(), iter_raw_entries(), iter_raw_file_entries(), load_raw_file()
```

#### `spotify.py` — Music

```
Raw:     iter_streams() → SpotifyStream
Summary: summarize_streaming() → SpotifyStreamingSummary
Session: listening_sessions() → ListeningSession
Daily:   daily_listening(start?, end?) → DailyListening (hours, top artists/tracks)
TopN:    top_names(field, n) → (name, count)[]
```

#### `reddit.py` — Social

```
Raw:     iter_comments/posts/saved_posts/saved_comments/comment_votes/post_votes/message_headers
Summary: summarize_activity(start, end) → RedditActivitySummary
Daily:   daily_activity(start, end) → RedditDayActivity
Dist:    subreddit_distribution(start, end) → (subreddit, count, pct)[]
```

#### `takeout_chrome.py` — Google Takeout Chrome history

```
Raw:     iter_takeout_chrome_visits(path, source_label?) → WebHistoryVisit
Used by: lynchpin.ingest.webhistory discovery/import path
Note:    broader Google Takeout life/activity/Gmail/location parsers are not
         present in the current source tree.
```

#### `exports.py` — Facade re-exporting the split export modules

`exports.py` is a thin facade: it re-exports the symbols below, but the
implementations live in dedicated per-format modules (`exports_goodreads.py`,
`exports_raindrop.py`, `exports_messenger.py`, `exports_wykop.py`,
`exports_dendron.py`). `from lynchpin.sources.exports import daily_raindrop_activity`
still works; import from the specific module when you want the narrowest surface.

```
Goodreads  (exports_goodreads.py):  iter_goodreads_books(), summarize_goodreads_library()
Raindrop   (exports_raindrop.py):   iter_raindrop_bookmarks(), iter_raindrop_bookmarks_all(),
                                    summarize_raindrop_bookmarks()
           daily_raindrop_activity(start, end) → RaindropDayActivity (bookmarks_added, unique_tags)
Messenger  (exports_messenger.py):  iter_fbmessenger_threads(), iter_fbmessenger_messages()
           daily_messenger_activity(start, end) → MessengerDayActivity (message_count, thread_count, sent_count)
Wykop      (exports_wykop.py):      iter_wykop_link_comments/entries/entry_comments(), summarize_wykop_activity()
Dendron    (exports_dendron.py):    iter_dendron_notes()
```

#### `health.py` — Samsung Health (steps, stress, HR, HRV, SpO2, weight, skin temp, floors, mood, snoring)

```
Raw:     daily_steps(start, end) → StepDay
         stress_measurements(start, end) → StressMeasurement
         heart_rate_measurements(start, end) → HeartRateMeasurement (avg, min, max BPM)
         hrv_measurements(start, end) → HRVMeasurement (sdnn_avg, rmssd_avg)
         spo2_measurements(start, end) → SpO2Measurement (spo2%, min, max)
         weight_measurements(start, end) → WeightMeasurement (kg, body_fat%, muscle, BMR)
         skin_temperature(start, end) → SkinTemperature (avg, min, max °C)
         floors_climbed(start, end) → FloorClimbed
         mood_entries(start, end) → MoodEntry (mood_type 1-5)
         snoring_records(start, end) → SnoringRecord (duration_s)
         respiratory_rate(start, end) → RespiratoryMeasurement (avg_rate)
         daily_vitality(start, end) → VitalityDay
Daily:   daily_stress(start, end) → DailyStressSummary (avg, min, max score)
         daily_heart_rate(start, end) → DailyHeartRateSummary (avg, resting HR)
         daily_health_summary(start, end) → DailyHealthSummary (ALL health signals per day)

Data: /realm/data/exports/health/processed/health_*.jsonl
Refresh: python -m lynchpin.cli.process_health
Coverage: bounded by the most recent Samsung Health export — health data is
export-kind (complete as of export date), see coverage-bounds invariant below.
```

#### `substance.py` — Substance tracking

```
Raw:     entries() → SubstanceEntry (date, time, substance, amount_mg)
         entries_for_date(d) → SubstanceEntry[]
         entries_in_range(start, end) → SubstanceEntry[]
Daily:   daily_summary(start, end) → SubstanceDaySummary (dose_count, substances, total_mg)
Monthly: monthly_summary(start, end) → SubstanceMonthlySummary (by_substance_mg, dose_days)

Data: /realm/data/exports/health/processed/substance_log_unified.csv
```

### Cross-Source Intelligence

Use the graph/context-pack spine, not retired scaffold-era composite helpers.

```
context_pack(start, end, projects?, mode?, semantic?) → ContextPack
  LLM-facing bundle with readiness, inventory, evidence graph summary,
  chronological graph evidence, graph relations, supported work claims,
  movement, project/day correlations, caveats, and optional deterministic
  semantic moments.

build_evidence_graph(start, end, projects?, mode?) → EvidenceGraph
  Typed evidence nodes from git, Polylogue, raw-log, ActivityWatch, terminal,
  GitHub refs/items, and generated analysis artifacts.

evidence_timeline(graph, projects?, limit?) → EvidenceTimelineEntry[]
  Chronological view over evidence graph nodes. Timed rows preserve order;
  date-only aggregates remain graph-backed evidence rather than a parallel
  timeline model.

evidence_relations(graph, projects?, relation_types?, limit?) → EvidenceRelationEntry[]
  Relationship view over graph edges, especially issue/PR references and
  cross-source temporal overlap. Same-day project co-presence stays available
  in the graph but is usually summarized by work_day_correlations().

work_day_correlations(start, end, include_github_context?, graph?) → CorrelatedWorkDay[]
  Project/day rows that preserve source dimensions instead of collapsing
  commits, issues, AI sessions, focus, and shell activity into one scalar.

supported_work_claims(rows, graph?) → WorkEvidenceClaim[]
  Project/day claims scored by source dimensions and distinct graph-relation
  dimensions. Raw proximity edge volume is intentionally not the score.

movement_summary(start, end, rows?) → MovementSummary
  Project movement with explicit caveats about commit counts, GitHub lifecycle,
  AI-session interpretation, and ActivityWatch attribution.
```

### Cross-Source Analysis Modules

High-level analytics composing multiple sources into unified views.

```
operator_daily_matrix(start, end) → list[OperatorDay]
  Panoramic daily matrix joining ALL sources into one typed record per date.
  Foundation for all downstream cross-source analysis. 60+ fields covering
  AW, git, SVN, health, substance, social, comms, AI, web, terminal, music;
  clipboard, irc, raw_log, keylog, and samsung_binning are joined in as well
  (clipboard and irc also produce evidence-graph nodes via
  graph/evidence_clipboard.py and graph/evidence_irc.py).

# Health
health_modeling.align_signals(stress, hrv, hr) → list[AlignedSignals]
  Time-align Samsung per-minute stress to HRV windows + nearest HR.
health_modeling.build_report(rows) → StressModelReport
  Linear regression + RandomForest for stress-score formula with diagnostics.

# Cross-source correlation
substance_health.analyze(start, end) → SubstanceHealthReport
  Substance × health lag correlation (0-7 day) + dose-response + abstinence periods.

# Anomaly / phase detection
anomaly_crossref.analyze(start, end) → AnomalyCrossReference
  When one source is anomalous, what do OTHER sources show?
life_phase.analyze(start, end) → LifePhaseReport
  Multi-signal phase boundary detection + characterization with known event alignment.

# Predictive
productivity_predictors.analyze(start, end) → ProductivityReport
  Predict tomorrow's deep-work hours from today's signals (RandomForest).
```

### Data Flow

```
/realm/data/... (raw)  →  sources/*.py (lazy iterators + cachew)
                                    ↓
                          evidence_graph + work_correlation + source_readiness
                                    ↓
                          context packs for deterministic narrative input
```

Context packs are the canonical narrative input surface. The old tracked
scaffold corpus and the ad hoc timeline/statistics/delivery composite modules
were retired; do not reintroduce parallel narrative-input APIs unless they are
views over the evidence graph with provenance and caveats.

## MCP Server

`lynchpin/mcp/` is a read-only FastMCP server (stdio) over the DuckDB
substrate: SELECT-only, `read_only=True`, with source-module fallback when a
table is missing. Sinnix wraps it as `mcp-lynchpin` (sets
`LYNCHPIN_REPO_ROOT` and `LYNCHPIN_LOCAL_ROOT=<repo>/.lynchpin`) and registers
it for Claude/Codex/Gemini. Start manually with `python -m lynchpin.mcp`;
enumerate the live tool catalog with the `mcp_capability_map` tool or
`just tool-inventory`.

## Data Sources

| Source | Location | Quick Access |
|--------|----------|-------------|
| ActivityWatch | `~/.local/share/activitywatch/` | `sources.activitywatch.daily_activity(start, end)` |
| Atuin | `~/.local/share/atuin/history.db` | `sources.terminal.commands(start, end)` |
| Git | baseline JSONL + live subprocess | `sources.git.daily_activity(start, end)` |
| Polylogue | archive DB (`~/.local/share/polylogue`) | `sources.polylogue.daily_activity(start, end)` |
| Sleep | `/realm/data/exports/health/processed/` | `sources.sleep.entries()` |
| Health | `/realm/data/exports/health/processed/` | `sources.health.daily_health_summary(start, end)` |
| Substance | `/realm/data/exports/health/processed/` | `sources.substance.daily_summary(start, end)` |
| Browser | `/realm/data/captures/webhistory/` | `sources.web.daily_browsing(start, end)` |
| Spotify | `/realm/data/exports/spotify/` | `sources.spotify.daily_listening(start, end)` |
| Reddit | `/realm/data/exports/reddit/` | `sources.reddit.daily_activity(start, end)` |
| Messenger | `/realm/data/exports/comms/` | `sources.exports.daily_messenger_activity(start, end)` |
| Raindrop | `/realm/data/exports/raindrop/` | `sources.exports.daily_raindrop_activity(start, end)` |
| Google Takeout Chrome | `/realm/data/exports/google/` | `sources.takeout_chrome.iter_takeout_chrome_visits(path)` |
| SVN (historical workplace) | `/realm/data/captures/dev/tortoisesvn/` | `sources.svn.daily_activity()` |
| Wykop (social) | `/realm/data/exports/wykop/raw/Sinity/` | `sources.wykop.daily_activity()` |
| SMS | `/realm/data/exports/samsung/processed/.../SMS/` | `sources.sms.daily_activity()` |
| Outlook (historical work email) | `/realm/data/exports/comms/outlook/` | `sources.outlook.daily_activity()` |
| Samsung binning | `/realm/data/exports/samsung/processed/.../` | `sources.samsung_binning.iter_stress_bins()` |
| Cross-source | All of the above | `lynchpin.graph.context_pack.context_pack(...)` |
| Evidence graph | Git, Polylogue, raw-log, AW, terminal, GitHub, analysis products | `lynchpin.graph.evidence_graph.build_evidence_graph(...)` |

Check availability: `get_config().available_sources()` → dict of source → bool.
Full source contracts: `docs/reference/data-sources.md`.

## Key Commands

```bash
just                                                  # List all recipes
python -m lynchpin.analysis materialize               # DAG-orchestrated full materialization
python -m lynchpin.analysis materialize --dry-run     # Show plan
python -m lynchpin.cli.current_state --start 2026-05-01 --end 2026-05-05 --weak-tags
python -m lynchpin.cli.process_health                 # Refresh health JSONL from raw Samsung exports
just velocity                                         # Cross-project velocity dashboard
just chisel                                           # XML repomix snapshots for GPT-Pro packaging
just ecosystem-dashboard                              # Ecosystem analysis dashboard
just tool-inventory                                   # List CLI entry points + live MCP tool count
pytest tests/ -q                                      # Run tests
just typecheck                                        # Strict mypy on the hardened file slice in pyproject.toml [tool.mypy]
just check                                            # Ruff + strict mypy slice + pytest
```

## LLM Backend Configuration

**Claude Agent SDK** (`core/claude_sdk.py`): Uses Claude Max subscription via `claude` CLI subprocess. `ANTHROPIC_API_KEY` suppressed. This is the only LLM backend currently in the tree — there is no `core/codex_exec.py` / `CodexExec` backend. ("codex" elsewhere refers to reading Codex *chat logs* — `config.codex_sessions_root`, `source_observations` — and to keystroke attribution in `analysis/keystroke_attribution.py`, not to an LLM call path.)

Never use API-key LLM calls. Always use subscription-backed paths.

## Operating Principles & Invariants

1. **Source modules are the API.** `from lynchpin.sources.activitywatch import deep_work`
2. **No second RAW store — but a DERIVED substrate exists.** Raw data stays
   external; there is no second copy of the raw exports. There *is* a DuckDB
   substrate (`lynchpin/substrate/`): a legitimate derived, rebuildable,
   non-authoritative read store. Source modules (Python + cachew) parse raw
   exports into typed rows; `materialization.py` + the substrate promoters
   INSERT those rows into DuckDB; readers SELECT and hydrate back to
   dataclasses; SQL views replace Python double-loops. The read path for
   agents is **MCP → substrate views/readers → (source fallback when needed)**.
   The accurate invariant is "no second raw store, and the substrate is
   derived/rebuildable, never the source of truth for raw data."
3. **Raw data stays external.** `/realm/data/...` untouched. Source modules
   parse on demand. Exception worth knowing: a few source modules read
   *promoted* substrate data rather than only raw files — `machine.py`,
   `activitywatch_raw.py`, `source_observations.py`, `observability_catalog.py`,
   `sinnix_generations.py`, `sinnix_runtime_inventory.py` import
   `lynchpin.substrate`. This is a deliberate, documented inversion of the
   nominal raw→sources→substrate flow (those sources read back derived/promoted
   rows), not raw data living in the warehouse.
4. **Cross-source via evidence graph/context packs.** Preserve dimensions and
   caveats; do not collapse commit counts, issue counts, AI sessions, focus,
   and shell activity into one velocity scalar.
5. **Coverage bounds, not staleness.** Exports under `/realm/data/exports/`
   are one-shot dumps — complete *as of* their export date, not stale (the
   `exports/` vs `captures/` split encodes whether continuous refresh is even
   expected). `core/coverage.py` (`CoverageBounds` with capture-vs-export
   `kind`, `partition_by_coverage()`) and
   `sources/source_observations.py` `coverage_bounds()` give every analysis
   the observed `[first, last]` per source. Clamp analyses to coverage and
   treat missing days as missing — never coerce absent days to `0` (that
   fabricates abstinence / flat physiology). Only captures (activitywatch,
   git, atuin, webhistory, machine) can have a *gap* that signals a real
   fault; that is gap-detection (`source_anomalies` / `machine_gap_summary`),
   not a staleness binary.
6. **Day bucketing is unified** on `core.primitives.logical_date` — THE
   bucketer. It localizes via `as_local` then applies `DAY_BOUNDARY_HOUR`
   (06:00 local), so late-night activity lands on the prior logical day.
   Callers must use it instead of raw `dt.date()`.
7. **Privacy boundary.** All analysis stays local. No API-key billing.

## Known Issues

- **Polylogue product readiness**: current-state packs surface whether
  Polylogue product tables are populated. Missing or stale session products are
  degraded source readiness, not a fallback semantic signal.
- **Test coverage**: run `pytest -q` for current count. Source modules have
  basic coverage; edge-case tests still needed.
