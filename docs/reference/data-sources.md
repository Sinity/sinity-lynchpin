# Canonical Data Sources

This repo is a read-model workspace: it should not accumulate large raw datasets. `lynchpin.*` modules read from canonical locations under `/realm/data/...` or `~/.local/share/...` and write regenerable outputs under `artefacts/` (ignored). Vendored `my.*` modules read their paths from `config/my/config.py` (loaded via `MY_CONFIG` in the devshell).

The `Used by` column lists canonical readers and representative architectural
consumers. It intentionally does not try to enumerate every historical command
surface; removed `life_timeline` entrypoints may still appear in old notes, but
they are not part of the live architecture.

## Primary sources (by domain)

| Domain | Source | Owner / Tool | Canonical path | Refresh / Notes | Used by |
|---|---|---|---|---|---|
| ActivityWatch | Raw DB | ActivityWatch (`aw-server-rust`) | `~/.local/share/activitywatch/aw-server-rust/sqlite.db` | Updated continuously while watchers run. Manual snapshots (if needed) live under `/realm/data/captures/activitywatch/raw/`. | `lynchpin.system.baseline`, `lynchpin.context.reports` |
| Shell history | Atuin DB | Atuin | `~/.local/share/atuin/history.db` | Updated continuously by Atuin. | `lynchpin.system.baseline`, `lynchpin.context.reports` |
| Git activity | Local repos | git | `/realm/project/*`, `/realm/project/sinnix`, `/realm/project/knowledgebase` | “Canonical” is the repos themselves; no exports stored here. | `lynchpin.system.baseline`, `lynchpin.context.reports`, `lynchpin.analysis.projects`, `lynchpin.sources.indices.gitstats`, retrospective/context evidence bundles |
| Codex sessions | Raw JSONL logs | Codex CLI | `~/.codex/sessions/` (local; not mirrored into `/realm/data`) | Updated continuously by Codex. | `lynchpin.system.baseline` (cadence), `docs/reference/sessions/README.md` (via Polylogue + summaries) |
| Chat transcripts | Normalised Markdown | Polylogue | `/realm/data/exports/chatlog/processed/markdown/{codex,claude-code,chatgpt,...}/**/conversation.md` | Often empty until you run `polylogue run`. | `docs/reference/sessions/README.md` |
| Polylogue runs | Run metadata JSON | Polylogue | `/realm/data/exports/chatlog/archive/runs/run-*.json` | Written every `polylogue run` for ingest/render/index telemetry. | `lynchpin.sources.exports.polylogue`, `lynchpin.views.warehouse` |
| Chat exports | Provider export zips | Chat providers | `/realm/data/exports/chatlog/raw/chatgpt/chatgpt-data-*.zip`, `/realm/data/exports/chatlog/raw/claude/claude-ai-data-*.zip`, etc. | Download new exports here, then use Polylogue to render. | Polylogue → `docs/reference/sessions/README.md` |
| Messenger (GDPR) | JSON backup export | Facebook | `/realm/data/exports/comms/facebook-messenger/processed/gdpr/<date>/messages/` | Extract `backup-messages.zip` into a dated folder; keep the raw zip under `raw/gdpr/`. | `lynchpin.sources.exports.fbmessenger` |
| Messenger (API export) | fbmessengerexport SQLite | fbmessengerexport | `/realm/data/exports/comms/facebook-messenger/processed/fbmessengerexport.sqlite` | Refresh via `python -m lynchpin.ingest.fbmessenger_export` (cookie acquisition in `lynchpin.ingest.fbmessenger_cookies`, Chrome/DevTools control in `lynchpin.ingest.fbmessenger_chrome`, exporter patching in `lynchpin.ingest.fbmessenger_patch`). If Chrome is already running with DevTools enabled, pass `--remote-debug-port 9222`, or add `--launch-debug-chrome` (clones the profile to a temp dir because Chrome blocks remote debugging on the default profile). Use `--cookies`/`--cookies-file` or `--cookie-db` if needed. | `my.fbmessenger.export` |
| Browser history (HPI export) | Filtered browserexport inputs | webhistory / gestalt | `/realm/data/captures/webhistory/gestalt/raw/*.json`, `/realm/data/captures/webhistory/gestalt/raw/*.csv` | Secondary HPI surface over the same browser export tree. The filtered glob matters because `*.pre_dedup` artefacts in the raw directory make `browserexport` crash in full mode. | `my.browser.export` |
| Browser history (live) | Chrome profile DB | Google Chrome | `~/.config/google-chrome/Default/History` | Local live profile DB. `my.browser.active_browser` reads it through a SQLite backup flow; this is separate from the canonical Gestalt export tree under `/realm/data/captures/webhistory/`. | `my.browser` |
| Wykop | Canonical JSON/JSONL export | `python -m lynchpin.ingest.wykop_export` | `/realm/data/exports/wykop/raw/<username>/` | Refresh with `direnv exec /realm/project/sinity-lynchpin python -m lynchpin.ingest.wykop_export ...`. | `lynchpin.sources.exports.wykop`, long-range retrospective evidence gathering |
| Reddit | Official export (unpacked) | Reddit export | `/realm/data/exports/reddit/processed/<date>/` | New exports land under a new `<date>/`; raw zip drops stay in `raw/<date>/`. | `lynchpin.sources.exports.reddit`, `lynchpin.views.warehouse`, long-range retrospective evidence gathering |
| Web history (raw) | Browser exports (JSON/CSV/NDJSON) | webhistory / gestalt | `/realm/data/captures/webhistory/gestalt/raw/` | Append-only exports; dedup with `python -m lynchpin.ingest.webhistory dedup`. | `lynchpin.ingest.webhistory` |
| Web history | Canonical NDJSON (preferred) | webhistory / gestalt | `/realm/data/captures/webhistory/gestalt/derived/full_history.ndjson` | Derived from raw via the dedup + merge steps; treat this as canonical for reads. | `lynchpin.sources.captures.webhistory`, long-range retrospective evidence gathering |
| Web history | Deduped segments (intermediate) | webhistory / gestalt | `/realm/data/captures/webhistory/gestalt/data/` | Per-export dedup output used to build the canonical NDJSON. | `lynchpin.ingest.webhistory` |
| Google Takeout | Archive bundles | Google | `/realm/data/exports/google/raw/takeout/*.tgz` (plus `*.zip`) | Download via https://takeout.google.com; keep all parts together in raw. | `lynchpin.sources.exports.takeout_archives`, `lynchpin.sources.exports.takeout_life`, `lynchpin.sources.exports.takeout_youtube`, `my.google.takeout.parser`, long-range retrospective evidence gathering |
| Bookmarks | Raindrop CSV export | Raindrop | `/realm/data/exports/raindrop/raw/raindrop_bookmarks_*.csv` | Drop new exports here; update pipeline default if filename changes. | `lynchpin.sources.exports.raindrop`, long-range retrospective evidence gathering |
| Reading | Goodreads library export | Goodreads | `/realm/data/exports/goodreads/raw/library_export.csv` | Replace with latest export. | `lynchpin.sources.exports.goodreads`, long-range retrospective evidence gathering |
| Substack archives | sbstck-dl exports + manual rips | Substack | `/realm/data/libraries/substack/` | Keep raw HTML/Markdown (`sbstck-dl`, manual downloads). | `lynchpin.sources.libraries.substack` |
| Photos | Personal photo archive | Manual dumps | Unconfigured | No stable canonical photo root is configured currently. | none |
| Documents (personal) | Scans/cards | Manual dumps | `/realm/data/libraries/doc/personal` | Migrated from legacy `personal-data/my_docs`. | (planned) |
| Music | Spotify exports (Account Data + Extended Streaming) | Spotify | `/realm/data/exports/spotify/processed/<date>/` | Replace with latest Account Data / Extended Streaming dumps. | `lynchpin.sources.exports.spotify`, long-range retrospective evidence gathering |
| Health | Processed sleep merge | health pipeline | `/realm/data/exports/health/processed/sleep_merged.jsonl` | Regenerate via your health merge pipeline (outside this repo). The older in-repo sleep merge script is obsolete. | `lynchpin.system.baseline`, `lynchpin.sources.exports.sleep` |
| Health | Samsung Health raw export | Samsung Health | `/realm/data/exports/health/raw/samsung-health/<YYYY-MM-DD>/` | Replace with latest export (keep zips under `raw/samsung-health/`). | health merge pipeline, long-range retrospective evidence gathering |
| Finance | Ledger journal | Ledger | `/realm/data/libraries/finance/journal_clean` | Update via your finance workflow; this repo only reads it. | `lynchpin.sources.libraries.finance`, long-range retrospective evidence gathering |
| Finance | Revolut + mBank statements | Bank exports | `/realm/data/libraries/finance/data/statements/` | Drop new CSVs into the statements tree. | finance ingestion workflows, long-range retrospective evidence gathering |
| Notes | OneNote journal export | Knowledgebase | `/realm/project/knowledgebase/logs.log-journal-onenote-2020.md` | Regenerate/re-export as needed. | long-range retrospective evidence gathering |
| Notes | Substance log | Knowledgebase | `/realm/project/knowledgebase/logs.log-substance.md` | Maintain in the vault. | long-range retrospective evidence gathering |
| Passwords | LastPass export | LastPass | `/realm/data/exports/lastpass/raw/` | Manual export from LastPass vault; not yet ingested. | (planned) |
| Instrumentation | Asciinema recordings | sinnix services | `/realm/data/captures/asciinema/` | Updated continuously while recording. | `lynchpin.ingest.instrumentation` |
| Instrumentation | Audio capture | sinnix services | `/realm/data/captures/audio/raw/` | Updated continuously while recording; legacy phone exports are archived under `/realm/data/captures/audio/archive/`. | `lynchpin.ingest.instrumentation` |
| Instrumentation | Screenshots/screencap | sinnix services | `/realm/data/captures/screenshot/` | Updated continuously while recording. | `lynchpin.ingest.instrumentation` |
| Spotify streaming history | Export bundles | Spotify | `/realm/data/exports/spotify/processed/<date>/` | Refresh via Spotify Account Data + Extended Streaming exports. | `lynchpin.sources.exports.spotify`, `lynchpin.views.warehouse` |
| Reddit comments/posts | Export bundles | Reddit | `/realm/data/exports/reddit/processed/<latest>/` | Drop fresh exports here; raw zip drops stay in `raw/<date>/`. | `lynchpin.sources.exports.reddit`, `lynchpin.views.warehouse` |
| Knowledgebase (Dendron vault) | Markdown vault | Dendron | `/realm/project/knowledgebase/` | Manage via Dendron CLI (`npm run dendron -- <command>`) and keep it as the canonical PKM root. | `lynchpin.sources.libraries.dendron`, `lynchpin.views.knowledge_graph` |
| Finance journal | Ledger CLI | Personal ledger workflow | `/realm/data/libraries/finance/journal_clean` | Keep ledger updated; lynchpin ingests postings for DuckDB. | `lynchpin.sources.libraries.finance`, `lynchpin.views.warehouse` |
| Polylogue Markdown transcripts | Polylogue render output | Polylogue | `/realm/data/exports/chatlog/processed/markdown/<provider>/**/conversation.md` | Run Polylogue renderers to refresh Markdown; `lynchpin.sources.exports.polylogue` inventories files. | `lynchpin.sources.exports.polylogue`, `lynchpin.views.warehouse` |
| Qdrant export archive (decommissioned) | One-time migration export | local qdrant export utility | `/realm/data/exports/vector-index/qdrant/20260306T180309Z/` | Archived NDJSON + collection metadata after qdrant decommission; includes vectors in `collections/*.points.jsonl` (see `VERIFICATION.md`). | ad-hoc DuckDB analysis (`read_ndjson_auto`) |

## Curated bundles (manual exports)

| Bundle | Purpose | Canonical path | Refresh / Notes | Used by |
|---|---|---|---|---|
| Baseline inputs (optional) | A frozen input set to reproduce a baseline run even if upstream DBs/repos drift | `/realm/data/sinity-lynchpin/baseline-inputs/<range>/` (optionally symlink `latest/`) | Create on demand when you want a pinned rerun; keep out of Git. | `lynchpin.system.baseline` |
