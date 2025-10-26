# Progressive Summaries Plan

## Goal
Establish a reproducible summarisation workflow that scales across Codex, Claude Code, Gemini, and manual chat exports while keeping raw transcripts accessible and progressively distilled.

## Workflow
1. **Ingestion (Polylogue-first)**  
   - Run `polylogue import`/`polylogue sync` per provider inside `/realm/project/polylogue` (direnv-enabled devshell).  
   - Normalised Markdown lives under `~/polylogue-data/{codex,claude-code,chatgpt,...}` with canonical `conversation.md` files.

2. **Session Registry**  
   - Mirror key runs into `docs/reference/sessions/` with raw path pointers, short highlights, and next actions (see `docs/reference/sessions/2025-10-24-codex.md` for template).  
   - Maintain a lightweight CSV (`data/derived/session_index.csv`, TODO) with columns: date, provider, project tag, token count, markdown path, embedding id.

3. **Hierarchical Summaries**  
   - Level 0: raw Markdown (Polylogue output).  
   - Level 1: per-session `summary.md` (500–800 tokens) generated via `python scripts/summarise_session.py <conversation.md>` (script TBD).  
   - Level 2: weekly/initiative rollups stored in `docs/reference/sessions/weekly/YYYY-Www.md`.  
   - Level 3: thematic digests (e.g., “ActivityWatch sensitivity history”) collated under `docs/reference/themes/`.

4. **Embedding + Search (Sinevec)**  
   - Use `sinevec embed-chats --platform codex --limit N --force` once Level-1 summaries exist.  
   - Attach metadata: `{"provider": "...", "project": "...", "summary_level": 1, "source_markdown": "..."}`.  
   - Store collection name `sessions-v1`; longer term, split by provider or summary level.

5. **Automation Hooks**  
   - Add `just summarise-session FILE=...` to orchestrate Markdown → Level-1 summary → embedding.  
   - Schedule (systemd timer or cron) nightly sync that runs Polylogue watchers, summarises fresh sessions, and pushes embeddings.

## Near-Term Tasks
- [x] Write `scripts/summarise_session.py` scaffolding (currently emits structured templates; hook in LLM once ready).  
- [ ] Prototype `data/derived/session_index.csv` (or DuckDB) linking sessions, categories, embeddings, and timeline IDs.  
- [ ] Embed historical Claude Code runs (bulk) after validating Polylogue renders; backfill metadata into Sinevec.  
- [ ] Add ActivityWatch/Git alignment columns (start/end timestamps, dominant repo) before building dashboards.
