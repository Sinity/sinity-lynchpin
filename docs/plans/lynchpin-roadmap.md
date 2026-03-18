# Lynchpin Roadmap

This is the active near-term roadmap for the repository as it exists now: a
Python read-model workspace centered on `lynchpin.*` modules and a small set of
derived views.

Lynchpin is an interim reference implementation over canonical inputs, not the
future runtime substrate. The priority is to encode derivations and contracts
that future Sinex work can port cleanly.

For the cross-cutting target of progressive multi-scale personal understanding,
see `docs/plans/personal-trajectory-program.md`.

## Scope

- Keep the repo focused on canonical source readers, derived views, and operational docs.
- Do not reintroduce a parallel top-level pipeline tree.
- Prefer work that sharpens the current module surface over broad ecosystem expansion.

## Active Priorities

1. **Source and warehouse hardening**
   - Extend `lynchpin.views.warehouse` coverage for already-supported sources.
   - Keep module contracts, warehouse tables, and reference docs aligned.

2. **Trajectory intelligence**
   - Converge the current day-range calendar stack and the monthly life-timeline stack onto the shared `trajectory.*` substrate that already exists.
   - Deepen daily facts, week/month rollups, and period/segment attribution before leaning harder on narrative generation.
   - Treat `docs/plans/personal-trajectory-program.md` as the current target definition.

3. **Calendar views**
   - Build on the shipped day-view workflow with week/month aggregation and stronger summaries.
   - Keep `lynchpin.views.calendar_views` and `lynchpin.views.calendar_narratives` reading the same trajectory-backed helpers.

4. **Session and transcript coverage**
   - Improve Polylogue-driven transcript availability and keep generated session summaries reproducible.
   - Keep the session ledger, Polylogue semantics, and warehouse `session_summaries` table aligned as the stable downstream index.

5. **Sinex handoff contracts**
   - Keep `lynchpin.system.sinex` honest about what exists today: repo and connector state, not a working runtime.
   - Document the canonical inputs, derivations, and warehouse contracts that future Sinex implementation should reproduce, rather than planning runtime adapters inside lynchpin.

6. **Instrumentation metadata**
   - Keep `lynchpin.ingest.instrumentation` aligned with Sinnix capture outputs.
   - Extend metadata harvesters only where the upstream capture path is already real.

## Deferred Ideas

These are legitimate future directions, but they are not the main path for current cleanup and delivery:

- live chat webapp scraping,
- broader finance and social-source collectors,
- richer calendar HTML surfaces beyond the current Markdown-first workflow,
- deeper Sinevec integration beyond existing source compatibility.
