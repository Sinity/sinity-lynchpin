# Lynchpin Roadmap (2026-01-01)

## Mission & Naming
- Repository lives at `/realm/project/sinity-lynchpin`; every command example, direnv invocation, and `just` recipe should assume this root.
- `/realm/project/` now hosts every actively maintained repo (Sinex, Sinnix, Polylogue, Sinevec, knowledgebase, etc.) to avoid special-case roots like `/realm/sinnix`. Any automation must glob `/realm/project/*`.
- “Lynchpin” refers to the Python API + control-plane workspace. Treat it as the canonical stopgap until Sinex fully absorbs these capabilities.

## Data & Tooling Principles
1. **Store raw, parse on access.** Keep GDPR/API exports exactly as received; never funnel them into a "master" database. Parsing/merging happens lazily in Lynchpin modules, optionally cached via DuckDB (`artefacts/lynchpin/warehouse.duckdb`) or `cachew`.
2. **Append, don’t mutate.** When inputs arrive as rolling windows (Reddit 1k limit, Chrome 90-day history, etc.), persist each slice with a timestamp. Reconstruction happens by iterating slices, preventing corruption when IDs reset (see karlicoss’ “Parse, don’t normalise”).
3. **Everything is a module.** Every canonical source should have a Lynchpin iterator/data class. Higher layers (calendar, Sinex, Sinnix, external skills) depend on these modules rather than bespoke parsers.
4. **Views > bespoke pipelines.** Pipelines such as calendar dossiers should treat Lynchpin modules (or the DuckDB warehouse) as their source of truth; the pipeline is just formatting + orchestration.
5. **Prompt runners.** Use `codex prompt "…"` for narratives by default. Other runners can wrap the same prompt payloads later, but prompts should be generated/stored under `artefacts/calendar/narratives/` so history stays reproducible.

## Existing Lynchpin Modules
| Module | What it exposes | Canonical inputs |
| --- | --- | --- |
| `activitywatch`, `sleep` | AW window/web/AFK buckets + wearables | `~/.local/share/activitywatch/`, `/realm/data/exports/health/processed/sleep_merged.jsonl` |
| `atuin` | Shell command history | `~/.local/share/atuin/history.db` |
| `calendar` | Legacy calendar bundle reader | `artefacts/calendar/raw/**` |
| `finance` | Ledger postings + statements | `/realm/data/libraries/finance/journal_clean`, `/realm/data/libraries/finance/data/statements/` |
| `gitstats` | Repo metadata, churn, tokei stats | `/realm/project/*`, `artefacts/meta/velocity/git_numstat.jsonl` |
| `polylogue` | Markdown-rendered chat transcripts | `/realm/data/exports/chatlog/processed/markdown/**/conversation.md` |
| `raindrop` | Bookmark CSVs | `/realm/data/exports/raindrop/raw/raindrop_bookmarks_*.csv` |
| `reddit` | Export bundles | `/realm/data/exports/reddit/processed/<date>/` (raw zips in `raw/<date>/`) |
| `sessions` | Session ledger rows | `artefacts/knowledge/ledgers/session_index.csv` |
| `sinevec` | Embedding state | `/realm/project/sinevec/var/state/**` |
| `sinnix` | Sinnix flake hosts/features + target doc | `/realm/project/sinnix/` |
| `sleep` | Sleep segments (Samsung Health + Sleep as Android merge) | `/realm/data/exports/health/processed/sleep_merged.jsonl` |
| `spotify` | Account Data + Extended Streaming JSON | `/realm/data/exports/spotify/processed/<date>/` |
| `substack` | sbstck-dl repo + manual rips | `/realm/data/libraries/substack/**` |
| `dendron` | Knowledgebase Markdown vault | `/realm/project/knowledgebase/` |
| `webhistory` | Gestalt merged history (derived from raw via dedup) | `/realm/data/captures/webhistory/gestalt/derived/full_history.ndjson` |
| `wykop` | API + scrape exports | `/realm/data/exports/wykop/raw/<user>/` |
| `meta` | Analysis log, backlog, and plan docs | `docs/analysis-log.md`, `docs/backlog.md`, `docs/plans/*.md` |

All modules cache into `artefacts/lynchpin/cache/` and register tables when `python -m lynchpin.views.warehouse` runs.

## Modules & Integrations to Build
1. **`lynchpin.system.sinex`**
   - Enumerate connectors/DAG manifests, ingest logs, and health metrics stored under `/realm/project/sinex` so Lynchpin dashboards can report ingestion state.
   - Long-term: Sinex dev-edition can import these modules (or mount the DuckDB warehouse) instead of reparsing raw exports.
2. **Chat webapp connectors**
   - Build Chrome-profile-aware scrapers for ChatGPT, Claude, Gemini, etc. so transcripts can be fetched even when Polylogue hasn’t rendered Markdown yet.
   - Store raw HTML/JSON under `/realm/data/exports/chatlog/live/<provider>/` before rendering.
3. **Twitter/Wykop/Reddit scrapers**
   - Wrap existing scraping workflows (Wykop scraper, Reddit API collectors, `sbstck-dl`, etc.) so they can be triggered via Lynchpin CLI.
   - Add Twitter module focused on bookmarks/likes/thread snapshots; rely on live API access when possible, fall back to GDPR exports.
4. **Finance extensions**
   - Go beyond hledger: integrate Allegro shopping data, multi-bank statements, blockchain transactions tied to `sinity.eth`, etc.
5. **Filesystem / knowledge sources**
   - Modules for `/realm/data/libraries/doc/**` (Substack, Gmail MBOX, Dendron derivatives), `/realm/data/libraries/media/**`, syslog/screenshot archives, dendron knowledgebase, Polylogue ingestion state.
6. **Sinevec integration**
   - Consider folding Sinevec functionality into Lynchpin (shared embeddings), or ensure sinevec pipelines can import Lynchipin modules for source data.
7. **Skill interfaces**
   - Where sensible, wrap modules as Codex “skills” so assistants can call e.g. `$lynchpinCalendar` to fetch a day view or `$lynchpinSessions` to search transcripts.

## Calendar & Narrative System
- **Goal:** present per-day/weekly/monthly (eventually quarterly/yearly) dossiers as Markdown + clean static HTML. Each day bundle should include *full* ActivityWatch slices, Atuin commands, git diffs, Polylogue chats, wearable metrics, instrumentation events, and links to repos/commits—sized for ≥128k token LLM runs.
- **Raw bundles**: continue storing under `artefacts/calendar/raw/YYYY-MM-DD/`, but populate exclusively via Lynchpin modules to avoid duplicated ingestion logic.
- **Views:** `lynchpin.views.calendar_views` becomes a “view layer” that:
  1. Queries Lynchpin modules (or DuckDB) for the requested window.
  2. Emits Markdown summaries (day/week/month) with consistent sections.
  3. Builds HTML (Plotly/vega-lite sparklines, embed cross-repo `artefacts/meta/velocity/velocity.html`).
  4. Writes JSON payloads for LLM prompts.
- **Narratives:** `just calendar-narrative <start> <end> mode=reflective,executive,...` should:
  - Use `codex prompt` runner by default (works today) but abstract prompt generation so future LLMs share the same input.
  - Generate multiple styles (reflective, executive, playful, retro, tactical, clinical) and store prompts + outputs.
  - Support block-level narratives (e.g., 4-hour slices) when context size allows.
- **Velocity / git overlays:** Continue running `python -m lynchpin.views.velocity` (or `just velocity`) before each refresh so the velocity HTML is mirrored into the calendar site. Consider embedding tokei stats and repo bundle summaries per day/week.
- **Portal deprecation:** once HTML views match/beat the old portal/focus timelines, retire `pipelines/focus/portal` entirely and keep only the Lynchpin-driven calendar site.

## HPI/Datasette Inspiration
- Treat Lynchpin as a local fork/reimagining of [`karlicoss/HPI`](https://github.com/karlicoss/HPI): lazy Python bindings, optional `cachew` caches, append-only storage. Where helpful, reuse HPI modules directly (reddit, ActivityWatch, TaskWarrior, etc.) but point them at `/realm/data/...`.
- [`Datasette`](https://datasette.io/) remains the lightweight browsing interface: `just lynchpin-datasette` should open `artefacts/lynchpin/warehouse.duckdb` for ad-hoc inspection.
- Consider vendoring select HPI modules (reddit, browser history, Fitbit) into `lynchpin/external/` if they reduce maintenance.

## Repository & Filesystem Layout
- `/realm/project`: flat directory containing every repo (`sinity-lynchpin`, `sinex`, `sinnix`, `polylogue`, `sinevec`, `scribe-tap`, `intercept-bounce`, `knowledgebase`, etc.).
- `/realm/data`: bucketed into `captures/`, `exports/`, `libraries/`, `indices/`. Maintain `docs/reference/data-sources.md` as the canonical inventory for domain paths.
- `/realm/home`, `/realm/inbox`: reserved for future flows; document any automation touching them.

## Integration Strategy (Sinnix, Sinex, future Sinex merge)
- **Sinnix:** treat Lynchpin as its data source for dashboards. Implement `lynchpin.system.sinnix` soon so Sinnix CLIs/skills can read instrumentation configs + statuses without shelling out.
- **Sinex:** longer term, Sinex dev-edition should depend on Lynchpin for data introspection (import modules, mount DuckDB). Calendar/narrative generation may ultimately migrate into Sinex once ready.
- **Merge possibility:** keep code modular so Lynchpin can be absorbed into Sinex with minimal effort—e.g., expose a clean Python package, keep pipeline scripts small wrappers around module calls.

## Immediate Work Queue
1. **Document & communication**
   - Keep `AGENTS.md`, README, and `docs/reference/data-sources.md` aligned with this roadmap (paths, modules, workflow expectations).
2. **Module implementation**
   - Harden `lynchpin.system.sinnix`/`lynchpin.system.meta` and build the remaining `lynchpin.system.sinex` scaffolding.
   - Prototype chat webapp scraping module (even if it just shells out to an existing Chrome profile + Playwright at first).
3. **Calendar refactor**
   - Rework `lynchpin.views.calendar_views` to query modules/DuckDB.
   - Ensure day/week/month outputs include everything needed for narratives (full AW slices, git diffs, Atuin commands, Polylogue transcripts, instrumentation).
4. **Warehouse & Datasette**
   - Expand `lynchpin.views.warehouse` ingest to cover Reddit, Spotify, finance, Polylogue, instrument metadata, etc., and publish a metadata JSON for Datasette.
5. **Scraper integrations**
   - Wrap Wykop/Reddit/Twitter/Substack collectors so they can be triggered via CLI (potentially `just lynchpin-refresh --source reddit`).
6. **Sinex/Sinnix coordination**
   - Determine how Sinex dev-edition consumes Lynchpin data (module import vs. API). Document the decision in `docs/plans/sinex-integration.md`.
7. **Narrative prompts**
   - Draft prompt templates that stress “full context” usage (ActivityWatch + git diffs + chat logs) and experiment with multi-mode outputs per day/week/month.

Keep this document updated as modules land or requirements evolve so future passes inherit a clear playbook.
