# Canonical Data Sources

This repo is a “control plane”: it should not accumulate large raw datasets. Instead, pipelines read from canonical locations under `/realm/data/...` or `~/.local/share/...`, and write regenerable outputs under `artefacts/` (ignored). Vendored `my.*` modules read their paths from `config/my/config.py` (loaded via `MY_CONFIG` in the devshell).

## Primary sources (by domain)

| Domain | Source | Owner / Tool | Canonical path | Refresh / Notes | Used by |
|---|---|---|---|---|---|
| ActivityWatch | Raw DB | ActivityWatch (`aw-server-rust`) | `~/.local/share/activitywatch/aw-server-rust/sqlite.db` | Updated continuously while watchers run. Manual snapshots (if needed) live under `/realm/data/activitywatch/raw/`. | `pipelines/core/baseline/`, `lynchpin.views.calendar_views` |
| Shell history | Atuin DB | Atuin | `~/.local/share/atuin/history.db` | Updated continuously by Atuin. | `pipelines/core/baseline/`, `lynchpin.views.calendar_views` |
| Git activity | Local repos | git | `/realm/project/*`, `/realm/project/sinnix`, `/realm/project/knowledgebase` | “Canonical” is the repos themselves; no exports stored here. | `pipelines/core/baseline/`, `lynchpin.views.calendar_views`, `pipelines/lifelog/life-timeline/`, `lynchpin.views.project_bundles`, `lynchpin.sources.gitstats` |
| Codex sessions | Raw JSONL logs | Codex CLI | `~/.codex/sessions/` (symlink: `/realm/data/chatlog/codex_sessions/`) | Updated continuously by Codex. | `pipelines/core/baseline/` (cadence), `docs/reference/sessions/README.md` (via Polylogue + summaries) |
| Chat transcripts | Normalised Markdown | Polylogue | `/realm/data/chatlog/markdown/{codex,claude-code,chatgpt,...}/**/conversation.md` | Often empty until you run Polylogue sync/render. | `docs/reference/sessions/README.md` |
| Chat exports | Provider export zips | Chat providers | `/realm/data/chatlog/chatgpt-data-*.zip`, `/realm/data/chatlog/claude-ai-data-*.zip`, etc. | Download new exports here, then use Polylogue to render. | Polylogue → `docs/reference/sessions/README.md` |
| Wykop | Canonical JSON/JSONL export | `python -m lynchpin.ingest.wykop_export` | `/realm/data/wykop/<username>/` | Refresh with `direnv exec /realm/project/sinity-lynchpin just wykop-export`. | `pipelines/lifelog/life-timeline/`, `lynchpin.sources.wykop` |
| Reddit | Official export (unpacked) | Reddit export | `/realm/data/reddit/gdpr/<date>/` | New exports land under a new `<date>/`; raw zip drops stay in `gdpr/raw/`. | `pipelines/lifelog/life-timeline/`, `lynchpin.sources.reddit` |
| Web history (raw) | Browser exports (JSON/CSV/NDJSON) | webhistory / gestalt | `/realm/data/webhistory/gestalt/raw/` | Append-only exports; dedup with `python -m lynchpin.ingest.webhistory dedup`. | `lynchpin.ingest.webhistory` |
| Web history | Canonical segments (preferred) | webhistory / gestalt | `/realm/data/webhistory/gestalt/data/` | Derived from raw via the dedup step; legacy manual segments archived under `/realm/data/archive/2026-01-03-webhistory-canon/gestalt_data`. See `/realm/data/webhistory/INVENTORY.md`. | `pipelines/lifelog/life-timeline/` |
| Web history | Legacy merged NDJSON (fallback) | webhistory | `/realm/data/webhistory/gestalt/derived/full_history.ndjson` | Used only if gestalt segments are missing. | `pipelines/lifelog/life-timeline/` |
| Google Takeout | Archive bundles | Google | `/realm/data/google/takeout/raw/*.tgz` (plus `*.zip`) | Download via https://takeout.google.com; keep all parts together in raw. | `pipelines/lifelog/life-timeline/`, `lynchpin.sources.takeout`, `my.google.takeout.parser` |
| Bookmarks | Raindrop CSV export | Raindrop | `/realm/data/raindrop/raindrop_bookmarks_*.csv` | Drop new exports here; update pipeline default if filename changes. | `pipelines/lifelog/life-timeline/`, `lynchpin.sources.raindrop` |
| Reading | Goodreads library export | Goodreads | `/realm/data/goodreads/library_export.csv` | Replace with latest export. | `lynchpin.sources.goodreads`, `pipelines/lifelog/life-timeline/` |
| Substack archives | sbstck-dl exports + manual rips | Substack | `/realm/data/doc/substack/` | Keep raw HTML/Markdown (`sbstck-dl`, manual downloads). | `lynchpin.sources.substack` |
| Music | Spotify exports (Account Data + Extended Streaming) | Spotify | `/realm/data/spotify/gdpr/<date>/` | Replace with latest Account Data / Extended Streaming dumps. | `pipelines/lifelog/life-timeline/` |
| Health | Processed sleep merge | health pipeline | `/realm/data/health/processed/sleep_merged.jsonl` | Regenerate via your health merge pipeline (outside this repo). | `pipelines/core/baseline/` |
| Health | Samsung Health raw export | Samsung Health | `/realm/data/health/raw/samsunghealth.tar` | Replace with latest export. | `pipelines/lifelog/life-timeline/` |
| Finance | Ledger journal | Ledger | `/realm/data/finance/journal_clean` | Update via your finance workflow; this repo only reads it. | `pipelines/lifelog/life-timeline/` |
| Finance | Revolut + mBank statements | Bank exports | `/realm/data/finance/data/statements/` | Drop new CSVs into the statements tree. | `pipelines/lifelog/life-timeline/` |
| Notes | OneNote journal export | Knowledgebase | `/realm/project/knowledgebase/logs.log-journal-onenote-2020.md` | Regenerate/re-export as needed. | `pipelines/lifelog/life-timeline/` |
| Notes | Substance log | Knowledgebase | `/realm/project/knowledgebase/logs.log-substance.md` | Maintain in the vault. | `pipelines/lifelog/life-timeline/` |
| Passwords | LastPass export | LastPass | `/realm/data/lastpass/raw/` | Manual export from LastPass vault; not yet ingested. | (planned) |
| Instrumentation | Asciinema recordings | sinnix services | `/realm/data/asciinema_recording/` | Updated continuously while recording. | `lynchpin.ingest.instrumentation` |
| Instrumentation | Audio capture | sinnix services | `/realm/data/audio/raw/` | Updated continuously while recording; legacy phone exports are archived under `/realm/data/audio/archive/`. | `lynchpin.ingest.instrumentation` |
| Instrumentation | Screenshots/screencap | sinnix services | `/realm/data/screenshot/` | Updated continuously while recording. | `lynchpin.ingest.instrumentation` |
| Spotify streaming history | GDPR exports | Spotify | `/realm/data/spotify/gdpr/<date>/` | Refresh via Spotify Account Data + Extended Streaming exports. | `lynchpin.sources.spotify`, `lynchpin.views.warehouse` |
| Reddit comments/posts | GDPR exports | Reddit | `/realm/data/reddit/gdpr/<latest>/` | Drop fresh exports here; raw zip drops stay in `gdpr/raw/`. | `lynchpin.sources.reddit`, `lynchpin.views.warehouse` |
| Knowledgebase (Dendron vault) | Markdown vault | Dendron | `/realm/project/knowledgebase/` | Manage via Dendron CLI (`npm run dendron -- <command>`) and keep it as the canonical PKM root. | `lynchpin.sources.dendron`, `lynchpin.views.knowledge_graph` |
| Finance journal | Ledger CLI | Personal ledger workflow | `/realm/data/finance/journal_clean` | Keep ledger updated; lynchpin ingests postings for DuckDB. | `lynchpin.sources.finance`, `lynchpin.views.warehouse` |
| Polylogue Markdown transcripts | Polylogue render output | Polylogue | `/realm/data/chatlog/markdown/<provider>/**/conversation.md` | Run Polylogue renderers to refresh Markdown; `lynchpin.sources.polylogue` inventories files. | `lynchpin.sources.polylogue`, `lynchpin.views.warehouse` |
| Sinevec embedding state | Sinevec pipelines | `/realm/project/sinevec` | `/realm/project/sinevec/var/state/embedding_state_v3.json` (and related) | Generated by sinevec embedding runs; `lynchpin.sinevec` exposes both stats and embedding/search helpers. | `lynchpin.sinevec`, `lynchpin.views.warehouse` |

## Curated bundles (manual exports)

| Bundle | Purpose | Canonical path | Refresh / Notes | Used by |
|---|---|---|---|---|
| Baseline inputs (optional) | A frozen input set to reproduce a baseline run even if upstream DBs/repos drift | `/realm/data/sinity-lynchpin/baseline-inputs/<range>/` (optionally symlink `latest/`) | Create on demand when you want a pinned rerun; keep out of Git. | `pipelines/core/baseline/` |
