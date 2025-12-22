# Canonical Data Sources

This repo is a “control plane”: it should not accumulate large raw datasets. Instead, pipelines read from canonical locations under `/realm/data/...` or `~/.local/share/...`, and write regenerable outputs under `artefacts/` (ignored).

## Primary sources (by domain)

| Domain | Source | Owner / Tool | Canonical path | Refresh / Notes | Used by |
|---|---|---|---|---|---|
| ActivityWatch | Raw DB | ActivityWatch (`aw-server-rust`) | `~/.local/share/activitywatch/aw-server-rust/sqlite.db` | Updated continuously while watchers run. | `pipelines/activitywatch-minute-timeline/`, `pipelines/focus/` |
| ActivityWatch | Local API | ActivityWatch | `http://127.0.0.1:5600/api/0` | Requires the AW server to be running. | `pipelines/focus/`, optional web sample in `pipelines/baseline/` |
| Shell history | Atuin DB | Atuin | `~/.local/share/atuin/history.db` | Updated continuously by Atuin. | `pipelines/baseline/`, `pipelines/focus/` |
| Git activity | Local repos | git | `/realm/project/*`, `/realm/sinnix`, `/realm/knowledgebase` | “Canonical” is the repos themselves; no exports stored here. | `pipelines/baseline/`, `pipelines/focus/`, `pipelines/life-timeline/`, `pipelines/project-bundles/` |
| Codex sessions | Raw JSONL logs | Codex CLI | `~/.codex/sessions/` (symlink: `/realm/data/chatlog/codex_sessions/`) | Updated continuously by Codex. | `pipelines/baseline/` (cadence), `pipelines/sessions/` (via Polylogue) |
| Chat transcripts | Normalised Markdown | Polylogue | `/realm/data/chatlog/markdown/{codex,claude-code,chatgpt,...}/**/conversation.md` | Often empty until you run Polylogue sync/render. | `pipelines/sessions/` |
| Chat exports | Provider export zips | Chat providers | `/realm/data/chatlog/chatgpt-data-*.zip`, `/realm/data/chatlog/claude-ai-data-*.zip`, etc. | Download new exports here, then use Polylogue to render. | Polylogue → `pipelines/sessions/` |
| Wykop | Canonical JSON/JSONL export | `pipelines/wykop/` | `/realm/data/personal-data/my_external_exports/wykop/<username>/` | Refresh with `direnv exec /realm/project/sinity-analysis just wykop-export`. | `pipelines/life-timeline/` |
| Reddit | Comment corpus CSV | (external ingest) | `/realm/data/reddit_comments/reddit_comments.csv` | Replace/extend via your Reddit ingest process. | `pipelines/life-timeline/` |
| Reddit | Official export | Reddit export | `/realm/data/personal-data/my_external_exports/reddit/<date>/` | New exports land under a new `<date>/`; update pipeline defaults when you roll forward. | `pipelines/life-timeline/` |
| Web history | Canonical segments (preferred) | webhistory / gestalt | `/realm/data/webhistory/gestalt/data/` | Treat as canonical when present; see `/realm/data/webhistory/INVENTORY.md`. | `pipelines/life-timeline/` |
| Web history | Legacy merged NDJSON (fallback) | webhistory | `/realm/data/webhistory/manual_merge_output/full_history.ndjson` | Used only if gestalt segments are missing. | `pipelines/life-timeline/` |
| Bookmarks | Raindrop CSV export | Raindrop | `/realm/data/raindrop/raindrop_bookmarks_*.csv` | Drop new exports here; update pipeline default if filename changes. | `pipelines/life-timeline/` |
| Reading | Goodreads library export | Goodreads | `/realm/data/personal-data/my_external_exports/goodreads_library_export.csv` | Replace with latest export. | `pipelines/life-timeline/` |
| Music | Spotify “MyData” export | Spotify | `/realm/data/personal-data/my_external_exports/spotify/MyData/` | Replace with latest export. | `pipelines/life-timeline/` |
| Health | Processed sleep merge | health pipeline | `/realm/data/health/processed/sleep_merged.jsonl` | Regenerate via your health merge pipeline (outside this repo). | `pipelines/baseline/` |
| Health | Samsung Health raw export | Samsung Health | `/realm/data/personal-data/samsunghealth.tar` | Replace with latest export. | `pipelines/life-timeline/` |
| Finance | Ledger journal | Ledger | `/realm/data/finance/journal_clean` | Update via your finance workflow; this repo only reads it. | `pipelines/life-timeline/` |
| Finance | Revolut + mBank statements | Bank exports | `/realm/data/finance/data/statements/` | Drop new CSVs into the statements tree. | `pipelines/life-timeline/` |
| Notes | OneNote journal export | Knowledgebase | `/realm/knowledgebase/logs.log-journal-onenote-2020.md` | Regenerate/re-export as needed. | `pipelines/life-timeline/` |
| Notes | Substance log | Knowledgebase | `/realm/knowledgebase/logs.log-substance.md` | Maintain in the vault. | `pipelines/life-timeline/` |
| Instrumentation | Asciinema recordings | sinnix services | `/realm/data/asciinema_recording/` | Updated continuously while recording. | `pipelines/instrumentation/` |
| Instrumentation | Audio capture | sinnix services | `/realm/data/audio/raw/` | Updated continuously while recording. | `pipelines/instrumentation/` |
| Instrumentation | Screenshots/screencap | sinnix services | `/realm/data/screenshot/` | Updated continuously while recording. | `pipelines/instrumentation/` |

## Curated bundles (manual exports)

| Bundle | Purpose | Canonical path | Refresh / Notes | Used by |
|---|---|---|---|---|
| Baseline inputs (optional) | A frozen input set to reproduce a baseline run even if upstream DBs/repos drift | `/realm/data/sinity-analysis/baseline-inputs/<range>/` (optionally symlink `latest/`) | Create on demand when you want a pinned rerun; keep out of Git. | `pipelines/baseline/` |
