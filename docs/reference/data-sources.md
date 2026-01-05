# Canonical Data Sources

This repo is a “control plane”: it should not accumulate large raw datasets. Instead, pipelines read from canonical locations under `/realm/data/...` or `~/.local/share/...`, and write regenerable outputs under `artefacts/` (ignored). Vendored `my.*` modules read their paths from `config/my/config.py` (loaded via `MY_CONFIG` in the devshell).

## Primary sources (by domain)

| Domain | Source | Owner / Tool | Canonical path | Refresh / Notes | Used by |
|---|---|---|---|---|---|
| ActivityWatch | Raw DB | ActivityWatch (`aw-server-rust`) | `~/.local/share/activitywatch/aw-server-rust/sqlite.db` | Updated continuously while watchers run. Manual snapshots (if needed) live under `/realm/data/captures/activitywatch/raw/`. | `pipelines/core/baseline/`, `lynchpin.views.calendar_views` |
| Shell history | Atuin DB | Atuin | `~/.local/share/atuin/history.db` | Updated continuously by Atuin. | `pipelines/core/baseline/`, `lynchpin.views.calendar_views` |
| Git activity | Local repos | git | `/realm/project/*`, `/realm/project/sinnix`, `/realm/project/knowledgebase` | “Canonical” is the repos themselves; no exports stored here. | `pipelines/core/baseline/`, `lynchpin.views.calendar_views`, `pipelines/lifelog/life-timeline/`, `lynchpin.views.project_bundles`, `lynchpin.sources.indices.gitstats` |
| Codex sessions | Raw JSONL logs | Codex CLI | `~/.codex/sessions/` (local; not mirrored into `/realm/data`) | Updated continuously by Codex. | `pipelines/core/baseline/` (cadence), `docs/reference/sessions/README.md` (via Polylogue + summaries) |
| Chat transcripts | Normalised Markdown | Polylogue | `/realm/data/exports/chatlog/processed/markdown/{codex,claude-code,chatgpt,...}/**/conversation.md` | Often empty until you run `polylogue run`. | `docs/reference/sessions/README.md` |
| Polylogue runs | Run metadata JSON | Polylogue | `/realm/data/exports/chatlog/archive/runs/run-*.json` | Written every `polylogue run` for ingest/render/index telemetry. | `lynchpin.sources.exports.polylogue`, `lynchpin.views.warehouse` |
| Chat exports | Provider export zips | Chat providers | `/realm/data/exports/chatlog/raw/chatgpt/chatgpt-data-*.zip`, `/realm/data/exports/chatlog/raw/claude/claude-ai-data-*.zip`, etc. | Download new exports here, then use Polylogue to render. | Polylogue → `docs/reference/sessions/README.md` |
| Messenger (GDPR) | JSON backup export | Facebook | `/realm/data/exports/comms/facebook-messenger/processed/gdpr/<date>/messages/` | Extract `backup-messages.zip` into a dated folder; keep the raw zip under `raw/gdpr/`. | `lynchpin.sources.exports.fbmessenger` |
| Messenger (API export) | fbmessengerexport SQLite | fbmessengerexport | `/realm/data/exports/comms/facebook-messenger/processed/fbmessengerexport.sqlite` | Refresh via `python -m lynchpin.ingest.fbmessenger_export` (Chrome cookies → keyring unlock retry → headless Chrome fallback). If Chrome is already running with DevTools enabled, pass `--remote-debug-port 9222`, or add `--launch-debug-chrome` (clones the profile to a temp dir because Chrome blocks remote debugging on the default profile). Use `--cookies`/`--cookies-file` or `--cookie-db` if needed. | `my.fbmessenger.export` |
| Wykop | Canonical JSON/JSONL export | `python -m lynchpin.ingest.wykop_export` | `/realm/data/exports/wykop/raw/<username>/` | Refresh with `direnv exec /realm/project/sinity-lynchpin just wykop-export`. | `pipelines/lifelog/life-timeline/`, `lynchpin.sources.exports.wykop` |
| Reddit | Official export (unpacked) | Reddit export | `/realm/data/exports/reddit/processed/<date>/` | New exports land under a new `<date>/`; raw zip drops stay in `raw/<date>/`. | `pipelines/lifelog/life-timeline/`, `lynchpin.sources.exports.reddit` |
| Web history (raw) | Browser exports (JSON/CSV/NDJSON) | webhistory / gestalt | `/realm/data/captures/webhistory/gestalt/raw/` | Append-only exports; dedup with `python -m lynchpin.ingest.webhistory dedup`. | `lynchpin.ingest.webhistory` |
| Web history | Canonical NDJSON (preferred) | webhistory / gestalt | `/realm/data/captures/webhistory/gestalt/derived/full_history.ndjson` | Derived from raw via the dedup + merge steps; treat this as canonical for reads. | `pipelines/lifelog/life-timeline/` |
| Web history | Deduped segments (intermediate) | webhistory / gestalt | `/realm/data/captures/webhistory/gestalt/data/` | Per-export dedup output used to build the canonical NDJSON. | `lynchpin.ingest.webhistory` |
| Google Takeout | Archive bundles | Google | `/realm/data/exports/google/raw/takeout/*.tgz` (plus `*.zip`) | Download via https://takeout.google.com; keep all parts together in raw. | `pipelines/lifelog/life-timeline/`, `lynchpin.sources.exports.takeout`, `my.google.takeout.parser` |
| Bookmarks | Raindrop CSV export | Raindrop | `/realm/data/exports/raindrop/raw/raindrop_bookmarks_*.csv` | Drop new exports here; update pipeline default if filename changes. | `pipelines/lifelog/life-timeline/`, `lynchpin.sources.exports.raindrop` |
| Reading | Goodreads library export | Goodreads | `/realm/data/exports/goodreads/raw/library_export.csv` | Replace with latest export. | `lynchpin.sources.exports.goodreads`, `pipelines/lifelog/life-timeline/` |
| Substack archives | sbstck-dl exports + manual rips | Substack | `/realm/data/libraries/substack/` | Keep raw HTML/Markdown (`sbstck-dl`, manual downloads). | `lynchpin.sources.libraries.substack` |
| Photos | Personal photo archive | Manual dumps | `/realm/data/libraries/media/photos` | Migrated from legacy `personal-data/my_pics`; treat as primary photo root. | `my.photos.main` |
| Documents (personal) | Scans/cards | Manual dumps | `/realm/data/libraries/doc/personal` | Migrated from legacy `personal-data/my_docs`. | (planned) |
| Music | Spotify exports (Account Data + Extended Streaming) | Spotify | `/realm/data/exports/spotify/processed/<date>/` | Replace with latest Account Data / Extended Streaming dumps. | `pipelines/lifelog/life-timeline/` |
| Health | Processed sleep merge | health pipeline | `/realm/data/exports/health/processed/sleep_merged.jsonl` | Regenerate via your health merge pipeline (outside this repo). | `pipelines/core/baseline/` |
| Health | Samsung Health raw export | Samsung Health | `/realm/data/exports/health/raw/samsung-health/<YYYY-MM-DD>/` | Replace with latest export (keep zips under `raw/samsung-health/`). | `pipelines/lifelog/life-timeline/` |
| Finance | Ledger journal | Ledger | `/realm/data/libraries/finance/journal_clean` | Update via your finance workflow; this repo only reads it. | `pipelines/lifelog/life-timeline/` |
| Finance | Revolut + mBank statements | Bank exports | `/realm/data/libraries/finance/data/statements/` | Drop new CSVs into the statements tree. | `pipelines/lifelog/life-timeline/` |
| Notes | OneNote journal export | Knowledgebase | `/realm/project/knowledgebase/logs.log-journal-onenote-2020.md` | Regenerate/re-export as needed. | `pipelines/lifelog/life-timeline/` |
| Notes | Substance log | Knowledgebase | `/realm/project/knowledgebase/logs.log-substance.md` | Maintain in the vault. | `pipelines/lifelog/life-timeline/` |
| Passwords | LastPass export | LastPass | `/realm/data/exports/lastpass/raw/` | Manual export from LastPass vault; not yet ingested. | (planned) |
| Instrumentation | Asciinema recordings | sinnix services | `/realm/data/captures/asciinema/` | Updated continuously while recording. | `lynchpin.ingest.instrumentation` |
| Instrumentation | Audio capture | sinnix services | `/realm/data/captures/audio/raw/` | Updated continuously while recording; legacy phone exports are archived under `/realm/data/captures/audio/archive/`. | `lynchpin.ingest.instrumentation` |
| Instrumentation | Screenshots/screencap | sinnix services | `/realm/data/captures/screenshot/` | Updated continuously while recording. | `lynchpin.ingest.instrumentation` |
| Spotify streaming history | Export bundles | Spotify | `/realm/data/exports/spotify/processed/<date>/` | Refresh via Spotify Account Data + Extended Streaming exports. | `lynchpin.sources.exports.spotify`, `lynchpin.views.warehouse` |
| Reddit comments/posts | Export bundles | Reddit | `/realm/data/exports/reddit/processed/<latest>/` | Drop fresh exports here; raw zip drops stay in `raw/<date>/`. | `lynchpin.sources.exports.reddit`, `lynchpin.views.warehouse` |
| Knowledgebase (Dendron vault) | Markdown vault | Dendron | `/realm/project/knowledgebase/` | Manage via Dendron CLI (`npm run dendron -- <command>`) and keep it as the canonical PKM root. | `lynchpin.sources.libraries.dendron`, `lynchpin.views.knowledge_graph` |
| Finance journal | Ledger CLI | Personal ledger workflow | `/realm/data/libraries/finance/journal_clean` | Keep ledger updated; lynchpin ingests postings for DuckDB. | `lynchpin.sources.libraries.finance`, `lynchpin.views.warehouse` |
| Polylogue Markdown transcripts | Polylogue render output | Polylogue | `/realm/data/exports/chatlog/processed/markdown/<provider>/**/conversation.md` | Run Polylogue renderers to refresh Markdown; `lynchpin.sources.exports.polylogue` inventories files. | `lynchpin.sources.exports.polylogue`, `lynchpin.views.warehouse` |
| Sinevec embedding state | Sinevec pipelines | `/realm/project/sinevec` | `/realm/data/indices/sinevec/state/embedding_state_v3.json` (fallback: `/realm/project/sinevec/var/state/…` until migration completes) | Generated by sinevec embedding runs; `lynchpin.sinevec` exposes both stats and embedding/search helpers. | `lynchpin.sinevec`, `lynchpin.views.warehouse` |

## Curated bundles (manual exports)

| Bundle | Purpose | Canonical path | Refresh / Notes | Used by |
|---|---|---|---|---|
| Baseline inputs (optional) | A frozen input set to reproduce a baseline run even if upstream DBs/repos drift | `/realm/data/sinity-lynchpin/baseline-inputs/<range>/` (optionally symlink `latest/`) | Create on demand when you want a pinned rerun; keep out of Git. | `pipelines/core/baseline/` |
