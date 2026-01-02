# Lynchpin (HPI + Datasette) Integration

Goal: turn the repository into a “lynchpin” layer of lazy, cacheable Python modules (HPI style) plus Datasette views. The calendar dossiers can eventually be reproduced by calling the new module (or phased out entirely once the API + HTML views exist). This note tracks which upstream HPI providers/modules are worth porting or wrapping and how they map to the `/realm/data/...` landscape.

## Why HPI + public APIs
- HPI’s `cachew` model gives us on-demand computation with memoisation (great match for interactive Codex skills).
- Many upstream modules (Reddit, Telegram, ActivityWatch, Taskwarrior, Last.fm, LinkedIn exports, etc.) already exist; we can vendor/adapt them instead of rewriting.
- Public APIs (e.g., Reddit live data, GitHub REST, Spotify) complement GDPR exports so we always have a “most recent” view even before a takeout lands locally.

## Candidate modules (in addition to existing canonical exports)

| Source / module | Repo reference | Notes / local mapping |
| --- | --- | --- |
| `my.activitywatch`, `my.activitywatch.active_window` | HPI core + https://github.com/madelinecameron/hpi | Replace or wrap our ActivityWatch connector; still read `~/.local/share/activitywatch/aw-server-rust/sqlite.db`. |
| `my.atuin` / `my.zsh` / `my.bash` | https://github.com/purarue/HPI | Use Atuin DB for canonical data; fall back to history files only if needed. |
| `my.calendar.holidays` | HPI core | Combine with calendar dossiers to annotate public holidays. |
| `my.body.weight`, `my.body.exercise.*`, `my.sleep.manual` | HPI core | Map to `/realm/data/health/processed/` JSON/CSV plus manual trackers. |
| `my.money` | HPI core | Hook into `/realm/data/finance/...` or ledger exports. |
| `my.webhistory`, `my.browser`, `my.google.takeout.parser` | HPI core | Wrap `/realm/data/webhistory/...` instead of raw Chrome history. |
| `my.location.google`, `my.photos.main` | HPI core | Feed from Google Takeout directories already kept outside the repo. |
| `my.twitter.*` (`all`, `archive`, `twint`) | HPI core | Source from GDPR/Twint plus optional API refresh. |
| `my.reddit` | HPI core + API | Use GDPR JSON for history, fall back to live API for recency (this is desirable). |
| `my.github.*` (`all`, `gdpr`, `ghexport`) | HPI core | Pair GDPR exports with `pipelines/meta/velocity` outputs. |
| `my.github`, `my.goodreads`, `my.lastfm`, `my.spotify.gdpr` | HPI core | Some already overlap with life-timeline inputs; consider unified ingest. |
| `my.linkedin.privacy_export`, `my.fbmessenger`, `my.smscalls`, `my.twitter`, `my.smscalls` | HPI core / purarue HPI | All candidates once local privacy exports exist. |
| `my.steam.scraper`, `my.taskwarrior` | purarue/madeline forks | Hook into existing config directories. |
| `my.spotify.gdpr`, `my.lastfm` | HPI core | Already have data under `/realm/data/spotify/`; integrate for richer lifelog dashboards. |

> Also keep local modules for: `my.coding.commits`, `my.github.all`, `my.photos.main`, `my.messenger`, `my.activitywatch`, `my.datasette` queries, etc.—basically every “my.*” label in the user’s list can map to an HPI-style provider inside `lynchpin/`.

## Integration plan (stopgap-friendly)
1. **Fork once, copy the code in-tree.** Grab the current HPI snapshot (and any forked modules like madelinecameron/purarue) into a `third_party/hpi/` folder without tying our Git history to the upstream repo. This keeps things lightweight and makes selective pruning easy.
2. **Strip down to what we need.** Delete unused providers immediately so the footprint stays lean; keep the remaining modules as scaffolding we can reshape into `lynchpin/`.
3. **Adapt paths + caching.** Update modules so `cachew`/memoization points at `/realm/data/...`, DuckDB, or Parquet caches. Embrace lazy evaluation everywhere so running a function once populates its cache and subsequent calls return instantly. *(Status: initial modules live under `lynchpin.*`; caching currently uses simple JSON memo stores, future work may adopt cachew/Parquet.)*
4. **Expose a consistent API.** Wrap the curated modules under `lynchpin/my/*.py` so Codex skills can `import lynchpin.my.reddit` etc. Calendar dossiers (if we keep them) should become thin wrappers around these functions; otherwise, HTML generation can read directly from the DuckDB/Parquet cache that the modules maintain. *(Status: modules are importable directly from Python; see README snippet for an example `calendar.load_day()` inspector.)*
5. **Datasette + DuckDB.** After modules populate the DuckDB warehouse, add a `just datasette` command for local browsing. Use DuckDB as the memoized backing store by default; mirror to SQLite only when Datasette or other tools need it. *(Status: `just lynchpin-warehouse` builds `artefacts/lynchpin/warehouse.duckdb`; `just lynchpin-datasette` launches Datasette on top of it.)*
6. **Document and move on.** Update `AGENTS.md`/`repo-organization` to point assistants at the new API, but keep reminding ourselves that this is still a stopgap: invest only where it unblocks current work, avoid over-polishing, and leave room for Sinex to replace it later.

## Datasette
- Once the warehouse tables exist (DuckDB + mirrored SQLite), keep Datasette config in `docs/reference/datasette.yml`.
- Potential quick wins: `focus_by_day`, `git_churn`, `webhistory_domains`, `health_sleep`, `sessions`.

## Current module coverage
- ActivityWatch window/AFK/web events (`lynchpin.activitywatch`)
- Atuin command history (`lynchpin.atuin`)
- Git numstat deltas + repo/tokei coverage (`lynchpin.gitstats`)
- Wearable sleep merges (`lynchpin.sleep`)
- Session ledger CSV (`lynchpin.sessions`)
- Webhistory gestalt exports (`lynchpin.webhistory`)
- Polylogue Markdown transcripts (Codex/Claude/etc.) (`lynchpin.polylogue`)
- Dendron vault notes (`lynchpin.dendron`)
- Raindrop bookmarks (`lynchpin.raindrop`)
- Substack HTML/Markdown archives (`lynchpin.substack`)
- Reddit GDPR exports + aggregated CSV (`lynchpin.reddit`)
- Wykop JSONL streams + CLI wrapper stubs (`lynchpin.wykop`)
- Spotify account + extended streaming history (`lynchpin.spotify`)
- Ledger journal (`lynchpin.finance`)
- Sinevec embedding state/token usage (`lynchpin.sinevec`)
- Calendar snapshots that stitch the above (`lynchpin.calendar`)
- DuckDB builder + Datasette helper (now covering Reddit, Spotify, finance, Polylogue, Sinevec) (`lynchpin.warehouse`, `just lynchpin-warehouse`, `just lynchpin-datasette`)

Upcoming modules will hook directly into ChatGPT/Claude webapps via the logged-in Chrome profile (share cookies/session tokens) so we can scrape conversations even when Polylogue hasn’t rendered Markdown yet.

## Naming reminder
- Repo now lives at `sinity-lynchpin`; the API/package remains “Lynchpin” (rename again only if we eventually align with a broader `sin*` naming scheme such as `sinchpin` or `sintrum-lynchpin`).
