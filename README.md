# sinity-lynchpin

A Python analysis platform over the operator's own captured and exported
personal data (git activity, terminal sessions, AI chat archives, browsing
history, health exports, and more). It reads from a data lake that lives
outside this repo (`/realm/data/...`) and builds evidence graphs, context
packs, and analysis products from it.

Private notes and operator-specific datasets are not stored in this
repository. Lynchpin reads identity mappings, vocabularies, domain
classifications, known events, and similar operator configuration from
external configured roots. Generic defaults and neutral synthetic fixtures
keep adapters and analyses usable without the private profile.

## Start Here

```bash
cd /realm/project/sinity-lynchpin
direnv allow   # or nix develop
pytest -q
python -m lynchpin.analysis --help
tool/current-state --start 2026-05-01 --end 2026-05-05 --weak-tags
```

Use the owning tool surface directly:

- `tool/current-state` → lynchpin context-pack generation
- `just chisel` → XML repomix snapshots and upload bundles under
  `/realm/data/derived/lynchpin/code-snapshots`

Most lynchpin-native entrypoints remain direct `python -m lynchpin...` commands.

This repo is code-first. Repo-local caches and generated working state
live under the gitignored `.lynchpin/`; personal dataset overrides and
chisel snapshot output live under `/realm/data/derived/lynchpin/`, outside
the checkout entirely.

## Current Surfaces

| Surface | Entry point | Notes |
| --- | --- | --- |
| Source APIs | `from lynchpin.sources...` | Canonical read-only interfaces over `/realm/data/...`, `~/.local/share/...`, and local repos. |
| Current-state context packs | `python -m lynchpin.cli.current_state --start 2026-05-01 --end 2026-05-05 --weak-tags` | Canonical graph-backed product for project inventory, source readiness, project/day correlations, and optional weak keyword/proximity tags. Use `--refresh-substrate` when the DuckDB evidence graph should be rebuilt. |
| Analysis suite | `python -m lynchpin.analysis --help` | Codebase and self-analysis materializers under `.lynchpin/generated/analysis/`. Cross-project "ecosystem" comparison covers the other Sinity projects (sinex, polylogue, sinnix, and this repo). |
| Project dashboards | `python -m lynchpin.analysis.projects --help` | Velocity dashboard plus the maintained `chisel` project snapshot generator. Chisel's default output root is `/realm/data/derived/lynchpin/code-snapshots`, not `.lynchpin/generated/` or `/realm/inbox/store`. |
| Knowledge ledgers | `python -m lynchpin.analysis knowledge-session-index --help` | Session and artefact ledger exports; the package-local `python -m lynchpin.analysis.knowledge --help` entrypoint remains available. |

## Repo Shape

| Path | Purpose |
| --- | --- |
| `lynchpin/core/` | Shared config, parsing, caching, periods, and primitives. |
| `lynchpin/sources/` | Read-only source modules imported directly by agents and scripts. |
| `lynchpin/analysis/` | Materializers and diagnostics for code, projects, and knowledge artifacts. |
| `lynchpin/cli/` | Explicit command-line entrypoints for current-state and source refresh workflows. |
| `tool/` | Lynchpin-native root commands only. |
| `.lynchpin/` | Ignored local work root for caches, generated outputs, and handoff bundles. |
| `.lynchpin/generated/registry/` | Curated session and artefact registries consumed by `lynchpin.analysis.knowledge`. |
| `.lynchpin/generated/` | Generated personal outputs, ledgers, dashboards, baselines, and local analysis products. |

Personal authored materials, registries, and generated datasets live under
`.lynchpin/generated/` (repo-local, gitignored) or `/realm/data/derived/lynchpin/`
(outside the checkout) depending on whether they're regenerable working
state or a hand-authored override.

## Canonical Inputs

| Surface | Canonical inputs |
| --- | --- |
| `lynchpin.sources.activitywatch` | `~/.local/share/activitywatch/aw-server-rust/sqlite.db` |
| `lynchpin.sources.terminal` | `~/.local/share/atuin/history.db`, `/realm/data/captures/asciinema/` |
| `lynchpin.sources.git` | Local repos under `/realm/project/*` |
| `lynchpin.sources.polylogue` | Polylogue facade over `$LYNCHPIN_POLYLOGUE_DB` / `$POLYLOGUE_DB_PATH` (default `~/.local/share/polylogue/polylogue.db`); markdown/chatlog exports are Polylogue-owned inputs |
| `lynchpin.sources.sleep` | `/realm/data/exports/health/processed/sleep_merged.jsonl` |
| `lynchpin.sources.web` | `/realm/data/captures/webhistory/gestalt/{raw,data,derived/full_history.ndjson}` |
| `lynchpin.sources.reddit` | `/realm/data/exports/reddit/processed/<latest>/` |
| `lynchpin.sources.spotify` | `/realm/data/exports/spotify/processed/<date>/` |
| `lynchpin.sources.takeout_chrome` | Chrome history JSON from Google Takeout exports |
| `lynchpin.sources.exports` | `/realm/data/exports/{goodreads,raindrop,comms/facebook-messenger,wykop}/...`, `/realm/data/knowledgebase/` |
| `lynchpin.sources.machine` | `/realm/data/captures/machine/telemetry.sqlite` |

External ownership boundaries:
- `sinnix` owns host/runtime wiring and capture services.
- `sinnix` owns machine telemetry capture; Lynchpin owns machine telemetry analysis and substrate promotion.
- `polylogue` owns transcript import and normalization.
- `sinex` is an analysis subject and future runtime target, not an embedded dependency.
- `knowledgebase` owns personal notes and archives.

## Knowledge Inputs And Outputs

- Session registry inputs: `.lynchpin/generated/registry/sessions/*.md`
- Artefact catalog input: `.lynchpin/generated/registry/artefact_catalog.json`
- Session ledger output: `.lynchpin/generated/knowledge/ledgers/session_index.csv`
- Artefact ledger output: `.lynchpin/generated/knowledge/ledgers/artefact_index.csv`
- Generated analysis markdown summaries: `.lynchpin/generated/analysis/maps/*.md`
- Velocity dashboard: `.lynchpin/generated/meta/velocity.html`
- Archived repo-history docs: `.lynchpin/generated/archive/repo-history/`

## Notes

- Source modules stay read-only. Explicit writes and refreshes belong under `lynchpin.analysis` or `lynchpin.cli`.
- Context packs are the canonical narrative input surface.
- Repo-level guidance lives here. Module-local behavior belongs in docstrings.
- Current contracts are defined by the code, this README, and `docs/reference/`.
