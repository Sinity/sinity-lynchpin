# Lynchpin (HPI + Datasette) Integration

Goal: turn the repository into a “lynchpin” layer of lazy, cacheable Python modules (HPI style) plus Datasette views. The calendar dossiers can eventually be reproduced by calling the new module (or phased out entirely once the API + HTML views exist). This note tracks which upstream HPI providers/modules are worth porting or wrapping and how they map to the `/realm/data/...` landscape and `config/my/config.py`. See `docs/reference/lynchpin-module-map.md` for the detailed mapping report.

## Why HPI + public APIs
- HPI’s `cachew` model gives us on-demand computation with memoisation (great match for interactive Codex skills). Vendored sources live under `external/hpi/`, `external/hpi-madelinecameron/`, and `external/hpi-purarue/` so we can port modules directly and track upstream changes explicitly.
- Many upstream modules (Reddit, Telegram, ActivityWatch, Taskwarrior, Last.fm, LinkedIn exports, etc.) already exist; we can vendor/adapt them instead of rewriting.
- Public APIs (e.g., Reddit live data, GitHub REST, Spotify) complement GDPR exports so we always have a “most recent” view even before a takeout lands locally.

## Active configured modules

These are the vendored HPI modules that currently have a stable local config plus an active validation contract:

| Source / module | Repo reference | Notes / local mapping |
| --- | --- | --- |
| `my.coding.commits` | HPI core | Raw commit feed over local repos; complements `lynchpin.sources.indices.gitstats`. |
| `my.calendar.holidays` | HPI core | Holiday metadata for future overlays. |
| `my.fbmessenger` | HPI core | Messenger chat surface over the processed `fbmessengerexport` SQLite export. |
| `my.smscalls` | HPI core | Reads the local SMS/call export tree. |
| `my.sleep.manual` | hpi-sinity | Adapter over the merged sleep JSONL already used by Lynchpin. |
| `my.money` | hpi-sinity | Adapter over the local ledger journal. |
| `my.webhistory` | hpi-sinity | Adapter over the canonical merged webhistory NDJSON. |
| `my.browser` | HPI core | Secondary browserexport-compatible view over filtered Gestalt raw exports plus the live Chrome profile DB. |
| `my.google.takeout.parser` | HPI core | Secondary parser surface over the local Takeout archive set. |
| `my.goodreads` | HPI core | Goodreads export support where the expected export exists. |
| `my.spotify.gdpr` | purarue fork | Secondary parser over Spotify GDPR exports. |
| `my.activitywatch`, `my.activitywatch.active_window` | madelinecameron, purarue | ActivityWatch companion surface. |
| `my.atuin` | hpi-sinity | HPI-style view over the same Atuin DB Lynchpin reads directly. |

## Dormant vendored modules

These stay vendored as source material, but they are out of the default contract until a concrete local use-case reactivates them:

| Source / module | Repo reference | Notes / local mapping |
| --- | --- | --- |
| `my.body.weight`, `my.body.exercise.all`, `my.sleep.manual` | HPI core | Map to `/realm/data/exports/health/processed/` plus manual logs. |
| `my.github.*` (`all`, `gdpr`, `ghexport`) | HPI core | Merge GDPR data with live API pulls. |
| `my.lastfm` | HPI core | Already partly represented via life timeline; integrate for richer stats. |
| `my.location.google` | HPI core | Feed location overlays from Takeout exports. |
| `my.photos.main` | HPI core | Dormant until a stable canonical photo root exists under `/realm/data/...`. |
| `my.reddit` | HPI core + API | Use GDPR exports + live API to keep recency. |
| `my.twitter.*` (`all`, `archive`, `twint`) | HPI core | Merge GDPR/Twint data with live API. |
| `my.taskwarrior` / `my.zsh` / `my.bash` | https://github.com/purarue/HPI | Extra CLI history/task surfaces not needed in the current contract. |
| `my.linkedin.privacy_export` | Fork variant | Useful once LinkedIn privacy exports are captured. |
| `my.steam.scraper` | Fork variant | Sync Steam library/play history when needed. |

## Integration plan (stopgap-friendly)
1. **Vendor once, keep in-tree.** Keep the upstream HPI snapshots (plus madelinecameron/purarue forks) under `external/` without tying our Git history to upstream. This keeps things lightweight and makes selective pruning easy.
2. **Strip down the active contract.** Keep vendored snapshots available for archaeology or future reuse, but default docs, validation, and runbooks stay limited to the modules with stable local inputs and active consumers.
3. **Adapt paths + caching.** Point `my.config` at `/realm/data/...` via `config/my/config.py` (loaded through `MY_CONFIG`). `lynchpin.cache` now wraps `cachew` and all core lazy modules use it.
4. **Expose a consistent API.** Keep `lynchpin.*` as the local export-facing surface, and use vendored `my.*` modules for live/API or upstream exports. Calendar dossiers (if we keep them) should become thin wrappers around these functions; otherwise, HTML generation can read directly from the DuckDB/Parquet cache that the modules maintain.
5. **Datasette + DuckDB.** After modules populate the DuckDB warehouse, use direct CLI entrypoints for local browsing. Use DuckDB as the memoized backing store by default; mirror to SQLite only when Datasette or other tools need it. *(Status: `python -m lynchpin.views.warehouse build` builds `artefacts/lynchpin/warehouse.duckdb`; `datasette artefacts/lynchpin/warehouse.duckdb` browses it.)*
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
- DuckDB builder + Datasette helper (now covering Reddit, Spotify, finance, Polylogue, Sinevec) (`lynchpin.views.warehouse`, `python -m lynchpin.views.warehouse build`, `datasette artefacts/lynchpin/warehouse.duckdb`)

Upcoming modules will hook directly into ChatGPT/Claude webapps via the logged-in Chrome profile (share cookies/session tokens) so we can scrape conversations even when Polylogue hasn’t rendered Markdown yet. For GitHub/Twitter/LinkedIn/Steam, the current vendored HPI modules still require exported files or scraper outputs; see [hpi-service-bootstrap.md](/realm/project/sinity-lynchpin/docs/reference/hpi-service-bootstrap.md).

## Naming reminder
- Repo now lives at `sinity-lynchpin`; the API/package remains “Lynchpin” (rename again only if we eventually align with a broader `sin*` naming scheme such as `sinchpin` or `sintrum-lynchpin`).
