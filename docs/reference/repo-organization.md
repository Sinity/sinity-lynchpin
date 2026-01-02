# sinity-lynchpin Repository Organization

This reference aggregates how the repo is structured and what each pipeline expects. Treat it as the on-ramp for assistants who need to find the right script, input, or output quickly.

## Top-Level Layout
- `README.md` – project mission, quickstart, and high-level map (`docs/README.md`, `pipelines/README.md`).
- `AGENTS.md` – operating handbook that defines mission, data sources, and priorities for assistants.
- `docs/` – narratives (`analysis/`), stable references (`reference/`), and design notes (`plans/`).
- `pipelines/` – pipeline-specific folders containing specs, scripts, and README files.
- `artefacts/` – regenerable outputs (ignored by git). Pipelines are expected to write there by default.
- `justfile` – canonical command surface for every pipeline (invoked via `direnv exec ... just <recipe>`).
- `flake.nix` / `.envrc` – reproducible devshell with Python 3.12, R, DuckDB, and helper CLIs.

## Documentation Tree
- `docs/analysis/` – long-form narratives (e.g. life timeline syntheses, past-month stories). Generated digests live under `artefacts/` and are referenced here.
- `docs/personal/` – high-sensitivity retrospectives (life timeline narratives/workflows) kept separate from the control-plane docs.
- `docs/analysis-log.md` – chronological ledger of analysis passes; skim before starting new work.
- `docs/backlog.md` – running backlog of enhancements and investigations.
- `docs/reference/` – canonical maps (data sources, realm topology, ActivityWatch heuristics, knowledge-graph notes).
- `docs/plans/` – future-state designs (Sinex adapter, Sinevec integration, secretary agent playbook).

## Command & Automation Surface
- Use `direnv allow` (or `nix develop`) in the repo root to load the devshell described in `flake.nix`.
- Run `just --list` to view all recipes (baseline, ledgers, focus dashboards, life timeline, instrumentation harvesters, etc.).
- Related recipes are grouped behind single entrypoints:
  - `just ledgers target=session|artefact [...]`
  - `just focus-portal [start=YYYY-MM-DD end=YYYY-MM-DD weeks=8]` (wrapper around `calendar-refresh`)
  - `just instrumentation target=asciinema|audio|screen [...]`
  - `just calendar-refresh start=... end=... [sessions_csv=... life_timeline=...]` for per-day/week/month dossiers
  - `just calendar-narrative START END mode=reflective,executive` to generate multi-style LLM stories from those dossiers
- Most commands support `start/end` or `since/until` parameters so you can scope workloads without editing scripts.
- `just clean-generated` removes `artefacts/`, `tmp/`, and cached `__pycache__` folders when you need a clean tree.

## Pipeline Inventory (summary)
| Pipeline | Path | Status | Entry Command(s) | Primary Inputs | Outputs |
| --- | --- | --- | --- | --- | --- |
| Baseline | `pipelines/core/baseline/` | stable | `just baseline` | ActivityWatch DB, Atuin DB, Codex sessions, git repos, sleep merge | `artefacts/core/baseline/latest/*` rollups |
| Ledgers | `pipelines/knowledge/ledgers/` | stable | `just ledgers target=session|artefact`, `just refresh-ledgers` | `docs/reference/sessions/*.md`, artefact catalog JSON | `artefacts/knowledge/ledgers/*.csv` |
| Calendar Dossiers | `pipelines/focus/calendar/` | prototype | `just calendar-refresh`, `just calendar-narrative` | Baseline artefacts (timeline, git numstat, Atuin summary, Codex summary), session ledger CSV, ActivityWatch DB, Atuin DB, instrumentation metadata, `/realm/data/webhistory/gestalt/data`, wearable sleep merge (`/realm/data/health/processed/sleep_merged.jsonl`), chat transcript roots under `/realm/data/chatlog/`, optional life timeline JSON | `artefacts/calendar/days/*.md`, weekly/monthly rollups (with aggregated sleep stats + life overlays), derived ActivityWatch focus/category summaries, git repo churn tables, static site bundles, JSON exports, raw bundles under `artefacts/calendar/raw/`, `artefacts/calendar/narratives/<mode>*.md`, mirrored velocity HTML. Backed by the lazily evaluated `lynchpin` package so scripts and Datasette pull identical metrics. |
| Life Timeline | `pipelines/lifelog/life-timeline/` | stable | `just life-timeline*`, `just life-refresh`, `just life-digest`, `just youtube-oembed` | Reddit/Wykop exports, Google Takeout, finance, health, git, notes | Monthly JSON summaries + drilldown Markdown |
| Life Narrative Auto | `pipelines/lifelog/life-timeline/generate_auto_narrative.py` | experimental | `just life-auto-narrative` (also runs during `just life-refresh`) | `artefacts/lifelog/life-timeline/monthly_life_latest.json` | `artefacts/lifelog/life-timeline/narratives/life_auto_summary.md` (quarter/year Markdown) |
| Wykop Export | `pipelines/lifelog/wykop/` | stable | `just wykop-export` | Wykop API/html + auth token | `/realm/data/wykop/<user>/` |
| Project Bundles | `pipelines/context/project-bundles/` | experimental | `just project-bundles` | Git repos (`sinex`, `polylogue`, etc.) | `artefacts/context/project-bundles/<repo>/` context packs |
| Sessions | `pipelines/knowledge/sessions/` | experimental | `just summarise-session <conversation.md>` | Polylogue Markdown transcripts | `artefacts/knowledge/sessions/summaries/*.json` |
| Instrumentation | `pipelines/ingest/instrumentation/` | experimental | `just instrumentation target=asciinema|audio|screen` | `/realm/data/{asciinema_recording,audio/raw,screenshot}` | Metadata JSONL under `artefacts/ingest/instrumentation/` |
| Knowledge Graph | `pipelines/knowledge/graph/` | experimental | `just knowledge-graph` | Markdown roots (`/realm/project/knowledgebase`, `docs/`) | DuckDB + optional Parquet snapshot |
| Meta / Velocity | `pipelines/meta/velocity/` | experimental | `just velocity` | Git repos (LoC history) | `artefacts/meta/velocity/velocity.html` |

## Pipeline Details
### Baseline (`pipelines/core/baseline/README.md`)
- Script: `build_baseline.py` orchestrates multi-source ingestion with `--mode live|bundle|auto`, `--full/--window-days`, and optional web bucket sampling.
- Inputs: ActivityWatch windows/AFK, Atuin command history, Codex sessions, git numstat, wearable sleep merge; optionally a frozen bundle under `/realm/data/sinity-lynchpin/baseline-inputs/<range>/`.
- Outputs: JSON summaries (ActivityWatch, AFK, Codex cadence, Atuin stats, git deltas, sleep) + `activity_timeline.json` for focus/daily reports.
- Usage: `just baseline session_root=... since=... until=... full=false` for range-bound reruns.

### Ledgers (`pipelines/knowledge/ledgers/README.md`)
- Scripts: `build_session_index.py` parses `docs/reference/sessions/*.md`, `build_artefact_index.py` renders `artefact_catalog.json` into CSV.
- Purpose: provide machine-readable CSVs used by downstream dashboards/agents without scraping Markdown.
- Outputs land in `artefacts/knowledge/ledgers/` and are regenerated via `just session-index`, `just artefact-index`, or `just refresh-ledgers`.
### Calendar Dossiers (`pipelines/focus/calendar/README.md`)
- Scripts: `build_calendar.py` merges baseline timeline metrics, git numstat events, Atuin daily counts, Codex session counts, session ledger entries, wearable sleep merges, auto-discovered chat exports, and optional instrumentation metadata into per-day dictionaries; it also computes ActivityWatch focus heuristics (top applications/domains, category minutes) and git repo churn stats inspired by the legacy minute-timeline and meta/velocity pipelines. Week/month bundles pull life timeline overlays when `monthly_life_latest.json` is present and emit raw bundles (`artefacts/calendar/raw/YYYY-MM-DD/`) containing full ActivityWatch events, Atuin commands, git diffs, chat transcripts (Codex/Claude/Polylogue/Markdown renders), wearable JSONL slices, instrumentation metadata, and filtered webhistory. `generate_narrative.py` packages those dictionaries into LLM prompts (via `codex prompt`) for multiple narrative styles.
- Outputs: Per-day Markdown (`artefacts/calendar/days/YYYY-MM-DD.md`) with Health & Recovery and Focus sections, static HTML (`artefacts/calendar/site/day/.../index.html`), JSON payloads with raw asset manifests plus `focus`/`repo_lines` structures, weekly/monthly summaries (Markdown/HTML/JSON) that aggregate sleep hours, focus categories, repo churn, and life overlays, prompt archives (`artefacts/calendar/narratives/prompts/*.txt`), generated narratives for each requested mode (`artefacts/calendar/narratives/<mode>.md`), and raw bundles under `artefacts/calendar/raw/`.
- Commands: `just calendar-refresh start=YYYY-MM-DD end=YYYY-MM-DD [baseline_dir=... sessions_csv=... life_timeline=... activitywatch_db=... atuin_db=... webhistory_dir=... sleep_jsonl=... --chat-root ...]`, `just calendar-narrative START END mode=reflective,executive [model=...]`.
- Notes: Weekly/monthly summaries integrate life timeline overlays, session highlights, wearable rest stats, ActivityWatch-derived focus categories, and git churn so downstream dashboards don’t need to call the old minute-timeline or velocity utilities. All relevant chat exports are copied into the day bundles so downstream prompt packs can stream complete transcripts without re-querying Polylogue.

### Life Timeline (`pipelines/lifelog/life-timeline/README.md`)
- Scripts: `build_life_timeline.py`, `render_monthly_digest.py`, `enrich_youtube_oembed.py`.
- Inputs: multi-source ingest spanning Reddit, Wykop, Google Takeouts (My Activity, Gmail, Chrome history), Goodreads, Samsung Health, finance, git, notes, etc.
- Outputs: `monthly_life_<start>_to_<end>.json`, drilldown Markdown directories, stable `latest` symlinks, and optional digests + YouTube oEmbed cache.
- Commands: `just life-timeline`, `just life-timeline-range`, `just life-refresh`, `just life-digest`, `just youtube-oembed`.
- Notes: high-sensitivity, long runtimes; configure `start/end` in YYYY-MM format, and prefer canonical `/realm/data/...` sources.

### Life Narrative Auto (`pipelines/lifelog/life-timeline/generate_auto_narrative.py`)
- Script: `generate_auto_narrative.py` ingests the latest monthly JSON and emits Markdown summaries for the most recent quarters/years.
- Command: `just life-auto-narrative` (runs as part of `just life-refresh`).
- Inputs: `artefacts/lifelog/life-timeline/monthly_life_latest.json`.
- Outputs: `artefacts/lifelog/life-timeline/narratives/life_auto_summary.md` with per-quarter and per-year bullet summaries (counts + top repos/subs/tokens).
- Notes: Keep outputs under `artefacts/` (ignored by Git); copy highlights into `docs/personal/life/life_narrative_master.md` when curating the human narrative.
### Wykop Export (`pipelines/lifelog/wykop/README.md`)
- Script: `scrape_wykop.py` (HTML/API backends, resumable with `scrape_state.json`).
- Inputs: Wykop refresh token (auto-discovered or passed), optional Chrome/Brave profiles for token retrieval.
- Outputs: JSON/JSONL exports stored under `/realm/data/wykop/<username>/`, plus manifests.
- Commands: `just wykop-export username=Sinity backend=api extras=true` (see `just` arguments for other combos).

### Project Bundles (`pipelines/context/project-bundles/README.md`)
- Script: `generate_project_bundles.py` uses `rg`, `git`, and optionally `tokei` to build combined Markdown contexts + gitlog splits for Sinex, Polylogue, etc.
- Outputs: `artefacts/context/project-bundles/<project>/combined*.md`, `gitlog_diffs.md`, chunked splits for LLM context windows.
- Run all or individual projects via `just project-bundles` or `... --projects sinex`.

### Sessions (`pipelines/knowledge/sessions/README.md`)
- Script: `summarise_session.py` consumes a Markdown transcript and emits a structured JSON summary (default OpenAI `gpt-4o-mini`, override via `--model`/`--api-base`).
- Workflow: Polylogue renders provider exports to Markdown → docs/reference session entries capture metadata → `just summarise-session` produces Level‑1 summaries in `artefacts/knowledge/sessions/summaries/`.
- Notes: respects `OPENAI_API_KEY`; future levels feed into Sinevec per `docs/plans/sinevec-integration.md`.

### Instrumentation (`pipelines/ingest/instrumentation/README.md`)
- Scripts: `collect_asciinema_metadata.py`, `collect_audio_metadata.py`, `collect_screen_metadata.py` (telemetry planned).
- Inputs: directories under `/realm/data/{asciinema_recording,audio/raw,screenshot}` produced by Sinnix services.
- Outputs: NDJSON metadata pools under `artefacts/ingest/instrumentation/` with filenames, timestamps, durations, sample info, etc.
- Commands: `just instrumentation target=asciinema|audio|screen`.

### Knowledge Graph (`pipelines/knowledge/graph/README.md`)
- Script: `build_knowledge_graph.py` crawls Markdown roots, extracts headings/backlinks, and writes DuckDB tables (`nodes`, `edges`), manifest, and optional Parquet.
- Inputs default to `/realm/project/knowledgebase` plus this repo’s `docs/`; override via positional CLI args.
- Outputs: `artefacts/knowledge/graph/knowledge_graph.duckdb`, `manifest.json`, optional `parquet/`.

### Meta / Velocity (`pipelines/meta/velocity/README.md`)
- Script: `plot_velocity.py` calculates LoC growth + churn across configured repos and categories (Rust src/tests/docs, Sinnix modules, etc.) and renders `artefacts/meta/velocity/velocity.html` (ECharts dashboard).
- Command: `just velocity`.
- Notes: classification logic defined per project; extend `PROJECT_SPECS` to add repos.

### Lynchpin package (`lynchpin/`)
- Status: `experimental`
- What: HPI-style Python modules (`lynchpin.activitywatch`, `.atuin`, `.gitstats`, `.sleep`, `.webhistory`, `.sessions`, `.polylogue`, `.reddit`, `.spotify`, `.finance`, `.sinevec`, `.calendar`, etc.) exposing canonical `/realm/data/...` sources as lazy generators with simple dataclasses.
- Run: invoke Python directly (see README snippet) or use `just lynchpin-warehouse` to build/refresh `artefacts/lynchpin/warehouse.duckdb`, then `just lynchpin-datasette` to browse it via Datasette.
- Outputs: memoized JSON caches under `artefacts/lynchpin/cache` plus DuckDB snapshots for Datasette/warehouse queries (tables now include Reddit comments/posts, Spotify streams, finance postings, Polylogue Markdown inventory, and Sinevec token usage summaries).
- Notes: Roadmap + extended module list lives in `docs/plans/lynchpin-hpi.md`. Expand modules there (e.g., Taskwarrior, blockchain holdings) as time allows.

## Supporting Artefacts
- `artefacts/knowledge/ledgers/artefact_index.csv` – generated ledger describing major artefacts and owners (source: `pipelines/knowledge/ledgers/artefact_catalog.json`).
- `artefacts/meta/velocity/velocity.html` – project velocity dashboard (refresh via `just velocity`).
- `artefacts/focus/`, `artefacts/core/baseline/`, `artefacts/lifelog/life-timeline/`, etc. – each pipeline has a dedicated namespace under `artefacts/`; delete/regenerate as needed.

## How to Extend
1. Add new pipelines under `pipelines/<name>/` with a README describing status, purpose, inputs, outputs, and run instructions.
2. Wire a `just` recipe so assistants can run it uniformly (document any required env vars/paths).
3. Update `docs/reference/data-sources.md` if the pipeline depends on new canonical locations.
4. Add derived artefacts to `pipelines/knowledge/ledgers/artefact_catalog.json` when they’re stable so ledgers stay current.
