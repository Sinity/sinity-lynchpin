# Lynchpin Roadmap

This is the active near-term roadmap for the repository as it exists now: a Python read-model workspace centered on `lynchpin.*` modules and a small set of derived views.

## Scope

- Keep the repo focused on canonical source readers, derived views, and operational docs.
- Do not reintroduce a parallel top-level pipeline tree.
- Prefer work that sharpens the current module surface over broad ecosystem expansion.

## Active Priorities

1. **Source and warehouse hardening**
   - Extend `lynchpin.views.warehouse` coverage for already-supported sources.
   - Keep module contracts, warehouse tables, and reference docs aligned.

2. **Calendar views**
   - Build on the shipped day-view workflow with week/month aggregation and stronger summaries.
   - Keep `lynchpin.views.calendar_views` and `lynchpin.views.calendar_narratives` reading the same underlying helpers.

3. **Session and transcript coverage**
   - Improve Polylogue-driven transcript availability and keep session summaries reproducible.
   - Maintain the session ledger as the stable downstream index.

4. **Sinex boundary**
   - Finish `lynchpin.system.sinex` enough to report meaningful ingest state.
   - Decide, and document, whether Sinex should consume Lynchpin through direct module imports or warehouse tables.

5. **Instrumentation metadata**
   - Keep `lynchpin.ingest.instrumentation` aligned with Sinnix capture outputs.
   - Extend metadata harvesters only where the upstream capture path is already real.

## Deferred Ideas

These are legitimate future directions, but they are not the main path for current cleanup and delivery:

- live chat webapp scraping,
- broader finance and social-source collectors,
- richer calendar HTML surfaces beyond the current Markdown-first workflow,
- deeper Sinevec integration beyond existing source compatibility.
