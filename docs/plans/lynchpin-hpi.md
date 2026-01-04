# Lynchpin (HPI + Datasette) Integration

Goal: turn the repository into a “lynchpin” layer of lazy, cacheable Python modules (HPI style) plus Datasette views. The calendar dossiers can eventually be reproduced by calling the new module (or phased out entirely once the API + HTML views exist). This note tracks which upstream HPI providers/modules are worth porting or wrapping and how they map to the `/realm/data/...` landscape and `config/my/config.py`. See `docs/reference/lynchpin-module-map.md` for the detailed mapping report.

## Why HPI + public APIs
- HPI’s `cachew` model gives us on-demand computation with memoisation (great match for interactive Codex skills). Vendored sources live under `external/hpi/`, `external/hpi-madelinecameron/`, and `external/hpi-purarue/` so we can port modules directly and track upstream changes explicitly.
- Many upstream modules (Reddit, Telegram, ActivityWatch, Taskwarrior, Last.fm, LinkedIn exports, etc.) already exist; we can vendor/adapt them instead of rewriting.
- Public APIs (e.g., Reddit live data, GitHub REST, Spotify) complement GDPR exports so we always have a “most recent” view even before a takeout lands locally.

## Candidate modules (in addition to existing canonical exports)

| Source / module | Repo reference | Notes / local mapping |
| --- | --- | --- |
| `my.coding.commits` | HPI core | Complements `lynchpin.sources.indices.gitstats` + calendar dashboards. |
| `my.calendar.holidays` | HPI core | Annotate focus reports with public holidays. |
| `my.body.weight`, `my.body.exercise.all`, `my.sleep.manual` | HPI core | Map to `/realm/data/exports/health/processed/` plus manual logs. |
| `my.fbmessenger` | HPI core | Live/GDPR connector for Messenger chat history. |
| `my.github.*` (`all`, `gdpr`, `ghexport`) | HPI core | Merge GDPR data with live API pulls. |
| `my.lastfm` | HPI core | Already partly represented via life timeline; integrate for richer stats. |
| `my.location.google` | HPI core | Feed location overlays from Takeout exports. |
| `my.photos.main` | HPI core | Index Takeout photos; tie into life timeline. |
| `my.reddit` | HPI core + API | Use GDPR exports + live API to keep recency. |
| `my.smscalls` | HPI core | Once call/SMS exports are stabilized locally. |
| `my.twitter.*` (`all`, `archive`, `twint`) | HPI core | Merge GDPR/Twint data with live API. |
| `my.money` | HPI core | Stitch finance exports into ledger dashboards. |
| `my.webhistory`, `my.browser`, `my.google.takeout.parser` | HPI core | Replace bespoke parsers for Chrome/Takeout data. |
| `my.goodreads` | HPI core | Hook into reading stats already surfaced in life timeline. |
| `my.spotify.gdpr` | purarue fork | Alternative to the local `lynchpin.sources.exports.spotify` parser. |
| `my.activitywatch`, `my.activitywatch.active_window` | HPI core + https://github.com/madelinecameron/hpi | Slot into the ActivityWatch DB we already mirror. |
| `my.taskwarrior` / `my.atuin` / `my.zsh` / `my.bash` | https://github.com/purarue/HPI | Expose CLI history + task data (Atuin DB lives under `/realm/data/` already). |
| `my.linkedin.privacy_export` | Fork variant | Useful once LinkedIn privacy exports are captured. |
| `my.steam.scraper` | Fork variant | Sync Steam library/play history when needed. |

> Also keep local modules for: `my.coding.commits`, `my.github.all`, `my.photos.main`, `my.messenger`, `my.activitywatch`, `my.datasette` queries, etc.—basically every “my.*” label in the user’s list can map to an HPI-style provider inside `lynchpin/`.

## Integration plan (stopgap-friendly)
1. **Vendor once, keep in-tree.** Keep the upstream HPI snapshots (plus madelinecameron/purarue forks) under `external/` without tying our Git history to upstream. This keeps things lightweight and makes selective pruning easy.
2. **Strip down to what we need.** Delete unused providers only when we’re confident they’re redundant; keep the remaining modules as scaffolding we can reshape into `lynchpin/`.
3. **Adapt paths + caching.** Point `my.config` at `/realm/data/...` via `config/my/config.py` (loaded through `MY_CONFIG`). `lynchpin.cache` now wraps `cachew` and all core lazy modules use it.
4. **Expose a consistent API.** Keep `lynchpin.*` as the local export-facing surface, and use vendored `my.*` modules for live/API or upstream exports. Calendar dossiers (if we keep them) should become thin wrappers around these functions; otherwise, HTML generation can read directly from the DuckDB/Parquet cache that the modules maintain.
5. **Datasette + DuckDB.** After modules populate the DuckDB warehouse, add a `just datasette` command for local browsing. Use DuckDB as the memoized backing store by default; mirror to SQLite only when Datasette or other tools need it. *(Status: `just lynchpin-warehouse` builds `artefacts/lynchpin/warehouse.duckdb`; `just lynchpin-datasette` launches Datasette on top of it.)*
6. **Document and move on.** Update `AGENTS.md`/`repo-organization` to point assistants at the new API, but keep reminding ourselves that this is still a stopgap: invest only where it unblocks current work, avoid over-polishing, and leave room for Sinex to replace it later.

## Datasette
- Once the warehouse tables exist (DuckDB + mirrored SQLite), keep Datasette config in `docs/reference/datasette.yml`.
- Potential quick wins: `focus_by_day`, `git_churn`, `webhistory_domains`, `health_sleep`, `sessions`.

## Current module coverage
- ActivityWatch window/AFK/web events (`lynchpin.sources.captures.activitywatch`) + upstream `my.activitywatch.*` (vendored)
- Atuin command history (`lynchpin.sources.captures.atuin`)
- Git numstat deltas + repo/tokei coverage (`lynchpin.sources.indices.gitstats`)
- Wearable sleep merges (`lynchpin.sources.exports.sleep`)
- Session ledger CSV (`lynchpin.sources.indices.sessions`)
- Webhistory gestalt exports (`lynchpin.sources.captures.webhistory`)
- Polylogue Markdown transcripts (Codex/Claude/etc.) (`lynchpin.sources.exports.polylogue`)
- Dendron vault notes (`lynchpin.sources.libraries.dendron`)
- Raindrop bookmarks (`lynchpin.sources.exports.raindrop`)
- Substack HTML/Markdown archives (`lynchpin.sources.libraries.substack`)
- Reddit GDPR exports + aggregated CSV (`lynchpin.sources.exports.reddit`)
- Wykop JSONL streams + CLI wrapper stubs (`lynchpin.sources.exports.wykop`)
- Spotify account + extended streaming history (`lynchpin.sources.exports.spotify`)
- Ledger journal (`lynchpin.sources.libraries.finance`)
- Sinevec embedding state/token usage (`lynchpin.sinevec`)
- Calendar snapshots that stitch the above (`lynchpin.views.calendar`)
- DuckDB builder + Datasette helper (now covering Reddit, Spotify, finance, Polylogue, Sinevec) (`lynchpin.views.warehouse`, `just lynchpin-warehouse`, `just lynchpin-datasette`)

Upcoming modules will hook directly into ChatGPT/Claude webapps via the logged-in Chrome profile (share cookies/session tokens) so we can scrape conversations even when Polylogue hasn’t rendered Markdown yet.

## Naming reminder
- Repo now lives at `sinity-lynchpin`; the API/package remains “Lynchpin” (rename again only if we eventually align with a broader `sin*` naming scheme such as `sinchpin` or `sintrum-lynchpin`).
