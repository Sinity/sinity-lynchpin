# Canonical Data Sources

This repo is a “control plane”: it should not accumulate large raw datasets. Instead, pipelines read from canonical locations under `/realm/data/...` or `~/.local/share/...`, and write regenerable outputs under `artefacts/` (ignored).

## Primary sources (by domain)

| Domain | Source | Owner / Tool | Canonical path | Refresh / Notes | Used by |
|---|---|---|---|---|---|
| ActivityWatch | Raw DB | ActivityWatch (`aw-server-rust`) | `~/.local/share/activitywatch/aw-server-rust/sqlite.db` | Updated continuously while watchers run. | `pipelines/core/baseline/`, `pipelines/focus/calendar/` |
| Shell history | Atuin DB | Atuin | `~/.local/share/atuin/history.db` | Updated continuously by Atuin. | `pipelines/core/baseline/`, `pipelines/focus/calendar/` |
| Git activity | Local repos | git | `/realm/project/*`, `/realm/project/sinnix`, `/realm/project/knowledgebase` | “Canonical” is the repos themselves; no exports stored here. | `pipelines/core/baseline/`, `pipelines/focus/calendar/`, `pipelines/lifelog/life-timeline/`, `pipelines/context/project-bundles/`, `lynchpin/gitstats` |
| Codex sessions | Raw JSONL logs | Codex CLI | `~/.codex/sessions/` (symlink: `/realm/data/chatlog/codex_sessions/`) | Updated continuously by Codex. | `pipelines/core/baseline/` (cadence), `pipelines/knowledge/sessions/` (via Polylogue) |
| Chat transcripts | Normalised Markdown | Polylogue | `/realm/data/chatlog/markdown/{codex,claude-code,chatgpt,...}/**/conversation.md` | Often empty until you run Polylogue sync/render. | `pipelines/knowledge/sessions/` |
| Chat exports | Provider export zips | Chat providers | `/realm/data/chatlog/chatgpt-data-*.zip`, `/realm/data/chatlog/claude-ai-data-*.zip`, etc. | Download new exports here, then use Polylogue to render. | Polylogue → `pipelines/knowledge/sessions/` |
| Wykop | Canonical JSON/JSONL export | `pipelines/lifelog/wykop/` | `/realm/data/wykop/<username>/` | Refresh with `direnv exec /realm/project/sinity-lynchpin just wykop-export`. | `pipelines/lifelog/life-timeline/`, `lynchpin.wykop` |
| Reddit | Comment corpus CSV | (external ingest) | `/realm/data/reddit/reddit_comments.csv` | Replace/extend via your Reddit ingest process. | `pipelines/lifelog/life-timeline/`, `lynchpin.reddit` |
| Reddit | Official export | Reddit export | `/realm/data/reddit/gdpr/raw/<date>/` | New exports land under a new `<date>/`; update pipeline defaults when you roll forward. | `pipelines/lifelog/life-timeline/`, `lynchpin.reddit` |
| Web history | Canonical segments (preferred) | webhistory / gestalt | `/realm/data/webhistory/gestalt/data/` | Treat as canonical when present; see `/realm/data/webhistory/INVENTORY.md`. | `pipelines/lifelog/life-timeline/` |
| Web history | Legacy merged NDJSON (fallback) | webhistory | `/realm/data/webhistory/manual_merge_output/full_history.ndjson` | Used only if gestalt segments are missing. | `pipelines/lifelog/life-timeline/` |
| Bookmarks | Raindrop CSV export | Raindrop | `/realm/data/raindrop/raindrop_bookmarks_*.csv` | Drop new exports here; update pipeline default if filename changes. | `pipelines/lifelog/life-timeline/`, `lynchpin.raindrop` |
| Reading | Goodreads library export | Goodreads | `/realm/data/goodreads/library_export.csv` | Replace with latest export. | `pipelines/lifelog/life-timeline/` |
| Substack archives | sbstck-dl exports + manual rips | Substack | `/realm/data/doc/substack/` | Keep raw HTML/Markdown (`sbstck-dl`, manual downloads). | `lynchpin/substack.py` |
| Music | Spotify exports (Account Data + Extended Streaming) | Spotify | `/realm/data/spotify/` | Replace with latest Account Data / Extended Streaming dumps. | `pipelines/lifelog/life-timeline/` |
| Health | Processed sleep merge | health pipeline | `/realm/data/health/processed/sleep_merged.jsonl` | Regenerate via your health merge pipeline (outside this repo). | `pipelines/core/baseline/` |
| Health | Samsung Health raw export | Samsung Health | `/realm/data/health/raw/samsunghealth.tar` | Replace with latest export. | `pipelines/lifelog/life-timeline/` |
| Finance | Ledger journal | Ledger | `/realm/data/finance/journal_clean` | Update via your finance workflow; this repo only reads it. | `pipelines/lifelog/life-timeline/` |
| Finance | Revolut + mBank statements | Bank exports | `/realm/data/finance/data/statements/` | Drop new CSVs into the statements tree. | `pipelines/lifelog/life-timeline/` |
| Notes | OneNote journal export | Knowledgebase | `/realm/project/knowledgebase/logs.log-journal-onenote-2020.md` | Regenerate/re-export as needed. | `pipelines/lifelog/life-timeline/` |
| Notes | Substance log | Knowledgebase | `/realm/project/knowledgebase/logs.log-substance.md` | Maintain in the vault. | `pipelines/lifelog/life-timeline/` |
| Instrumentation | Asciinema recordings | sinnix services | `/realm/data/asciinema_recording/` | Updated continuously while recording. | `pipelines/ingest/instrumentation/` |
| Instrumentation | Audio capture | sinnix services | `/realm/data/audio/raw/` | Updated continuously while recording. | `pipelines/ingest/instrumentation/` |
| Instrumentation | Screenshots/screencap | sinnix services | `/realm/data/screenshot/` | Updated continuously while recording. | `pipelines/ingest/instrumentation/` |
| Spotify streaming history | GDPR exports | Spotify | `/realm/data/spotify/` | Refresh via Spotify Account Data + Extended Streaming exports. | `lynchpin/spotify.py`, `lynchpin/warehouse.py` |
| Reddit comments/posts | GDPR exports + local aggregations | Reddit | `/realm/data/reddit/reddit_comments.csv`, `/realm/data/reddit/gdpr/raw/<latest>/` | Drop fresh exports here; aggregated CSV updated separately. | `lynchpin/reddit.py`, `lynchpin/warehouse.py` |
| Knowledgebase (Dendron vault) | Markdown vault | Dendron | `/realm/project/knowledgebase/` | Manage via Dendron CLI (`npm run dendron -- <command>`) and keep it as the canonical PKM root. | `lynchpin.dendron`, `pipelines/knowledge/graph/` |
| Finance journal | Ledger CLI | Personal ledger workflow | `/realm/data/finance/journal_clean` | Keep ledger updated; lynchpin ingests postings for DuckDB. | `lynchpin/finance.py`, `lynchpin/warehouse.py` |
| Polylogue Markdown transcripts | Polylogue render output | Polylogue | `/realm/data/chatlog/markdown/<provider>/**/conversation.md` | Run Polylogue renderers to refresh Markdown; `lynchpin.polylogue` inventories files. | `lynchpin/polylogue.py`, `lynchpin/warehouse.py` |
| Sinevec embedding state | Sinevec pipelines | `/realm/project/sinevec` | `/realm/project/sinevec/var/state/embedding_state_v3.json` (and related) | Generated by sinevec embedding runs; `lynchpin.sinevec` exposes both stats and embedding/search helpers. | `lynchpin/sinevec.py`, `lynchpin/warehouse.py` |

## Curated bundles (manual exports)

| Bundle | Purpose | Canonical path | Refresh / Notes | Used by |
|---|---|---|---|---|
| Baseline inputs (optional) | A frozen input set to reproduce a baseline run even if upstream DBs/repos drift | `/realm/data/sinity-lynchpin/baseline-inputs/<range>/` (optionally symlink `latest/`) | Create on demand when you want a pinned rerun; keep out of Git. | `pipelines/core/baseline/` |
