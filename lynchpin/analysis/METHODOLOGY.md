# Methodology Contract

This repository operates in hard-data mode.

Canonical truth:
- deterministic metrics computed from code/git artifacts,
- formulas traceable in code,
- validated by `python -m lynchpin.analysis analysis-validate`.

Portable history cleanup:
- housed canonically under [history cleanup README](/realm/project/sinnix/dots/codex/skills/history-cleanup/README.md),
- used when a project needs commit-message rewrite, atomicity analysis, and
  structural split/merge/reorder prep,
- never allowed to silently redefine the canonical metric layer in this repo.

Contextual interpretation:
- allowed for maps, validation heuristics, and behavioral joins,
- never allowed to override canonical denominators without explicit contract change.

Operational command spine:
- `python -m lynchpin.analysis materialize`
- `python -m lynchpin.analysis analysis-validate`
- `python -m lynchpin.analysis project-maps`
- `python -m lynchpin.analysis dependency-map`
- `python -m lynchpin.analysis change-surface-map`

Generated markdown map summaries belong under
`.lynchpin/generated/analysis/maps/`,
not tracked docs.

## Canonical Artifact Set

Source of truth contract:
- `analysis_spec.json`

Required artifacts:
- `.lynchpin/generated/analysis/sinex_structure_metrics.json`
- `.lynchpin/generated/analysis/sinex_temporal_metrics.json`
- `.lynchpin/generated/analysis/ecosystem_comparison.json`
- `.lynchpin/generated/analysis/cross_project_metrics.json`
- `.lynchpin/generated/analysis/analysis_snapshot.json`

## Regeneration

Preferred (DAG-orchestrated):
```sh
python -m lynchpin.analysis materialize
python -m lynchpin.analysis analysis-validate
```

The DAG handles dependency ordering and parallelism automatically.
Individual steps can be run for debugging via `python -m lynchpin.analysis <step>`.

## Explicitly Forbidden for Canonical Claims

1. Commit-message-only semantic intent inference.
2. Ticket-ID/message-snippet LLM adjudication.
3. Promoting external contextual packets to top-line truth without contract revision.

## Reporting Requirements

Every top-line number must include:
1. artifact path,
2. timeframe/window,
3. denominator/unit.

Ambiguous metrics should be removed, not defended.

## Relationship to History Cleanup

This repo carries two distinct methods:

1. deterministic codebase analysis
2. portable history cleanup

They can inform each other, but they are not the same thing.

Deterministic analysis remains the source of truth for:

- counts
- denominators
- commit-surface metrics
- cross-project comparisons

History cleanup improves the analysis layer by making commit history more
atomic, messages more semantically useful, and readiness/blocker state
machine-readable.

When a repo has undergone the history-cleanup process, the analysis suite can trust
its launch pack and audit-grade message corpus more than ordinary raw git
history. Until then, commit messages remain secondary evidence only.
