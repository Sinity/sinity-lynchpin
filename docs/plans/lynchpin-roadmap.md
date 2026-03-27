# Lynchpin Roadmap

This roadmap is for the repository as it should become: one coherent personal
understanding system over canonical local data.

Lynchpin should be treated as a reference implementation for that system:
useful because it encodes canonical inputs, derived semantics, and narrative
workflows concretely, but not because it should survive as a pile of parallel
architectures.

The active target architecture is documented in
[`docs/plans/personal-trajectory-program.md`](/realm/project/sinity-lynchpin/docs/plans/personal-trajectory-program.md).

## Scope

- Keep the repo focused on canonical source readers, semantic evidence planes, warehouse contracts, context assembly, and retrospective outputs.
- Do not preserve overlapping architectural worlds just because they already exist.
- Prefer moving useful logic into coherent package boundaries over adding more wrappers around old ones.

## Active Priorities

1. **Unify the architecture**
   - Remove repo-level documentation that still treats `life_timeline` or `trajectory` as target architecture.
   - Define the surviving package boundaries clearly: `sources`, `views.warehouse`, `context`, `retrospective`, and any new `periods` helpers.

2. **Strengthen semantic evidence planes**
   - Make the focus, delivery, conversation, intake, body, and output planes explicit in code and warehouse docs.
   - Keep table contracts, module contracts, and freshness expectations aligned.

3. **Build context orchestration as a first-class product layer**
   - Add explicit evidence-bundle APIs.
   - Add trust/freshness accounting.
   - Persist the raw query inputs and outputs used for narrative work.

4. **Absorb long-range functionality**
   - Re-home the useful functionality currently trapped in `life_timeline` onto the shared evidence and context layers.
   - Eliminate the separate long-range JSON/pipeline world once the logic has moved.

5. **Keep context/signals as the canonical understanding surface**
   - Preserve the moved functionality under `lynchpin.context` and `lynchpin.signals`.
   - Keep derived rollup tables only where they remain genuinely useful query surfaces.
   - Do not reintroduce a trajectory-first package boundary.

6. **Narrative workflows**
   - Make evidence-bundle-driven interactive narrative work the default path.
   - Keep `_narratives` as style/reference only.
   - Keep narrative files as the canonical output artifacts, with provenance and raw evidence links.

7. **Warehouse and transcript freshness**
   - Improve freshness and visibility of chat/session-derived tables.
   - Keep warehouse coverage, Polylogue semantics, and narrative-facing evidence surfaces in sync.

## Deferred

These are valid directions, but not ahead of the architecture cleanup above:

- richer live dashboards and HTML surfaces,
- live chat webapp scraping,
- broader finance and social ingestion,
- deeper knowledge-graph and embedding layers,
- Sinex runtime handoff beyond stable contracts and reproducible read models.
