# Life Range Workflow

Long-range life synthesis is housed under `lynchpin.retrospective.life*`,
not under a separate `system.life_timeline` subsystem.

It is part of the same coherent evidence-first system as the rest of Lynchpin:
- canonical sources and semantic evidence planes,
- warehouse query surfaces and context bundles,
- interactive narrative writing under `artefacts/retrospective/narratives/`.

## Implementation Surface

For agents, the canonical orchestration surface is still the `lynchpin-ops`
skill plus direct source, warehouse, and context queries. The APIs and commands
below are documented because they materialize the canonical long-range life
artefacts today.

Recent month context now flows through `lynchpin.context.reports` and stored
period evidence bundles rather than querying `context_month` directly.
Treat any remaining `trajectory_*` references in historical notes as stale,
not as a reason to rebuild a trajectory-first architecture.

## API

```bash
direnv exec /realm/project/sinity-lynchpin python - <<'PY'
from pathlib import Path

from lynchpin.retrospective.life_range import LifeRangeInputs, build_life_range

result = build_life_range(
    start_month="2020-04",
    end_month="2020-06",
    output=Path("artefacts/retrospective/life-range/example.json"),
    inputs=LifeRangeInputs(),
)
print(result)
PY
```

## Monthly JSON + Drilldowns

### Fixed Range

```bash
direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.retrospective.life build \
  --start 2020-04 \
  --end 2023-04 \
  --output artefacts/retrospective/life-range/monthly_life_2020-04_to_2023-04.json \
  --markdown-output artefacts/retrospective/life-range/life_2020-04_to_2023-04.generated.md
```

### Open-Ended “Latest” Refresh

```bash
direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.retrospective.life build \
  --markdown-output-dir artefacts/retrospective/life-range/life_drilldowns_latest
```

Defaults:
- Start month: `2013-10`
- End month: current month
- JSON output: `artefacts/retrospective/life-range/monthly_life_latest.json`

Use a dated JSON filename instead of `monthly_life_latest.json` when you want a pinned snapshot.

## Digest

The digest renderer is reusable as `lynchpin.retrospective.life_rendering.render_life_digest(...)`.

```bash
direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.retrospective.life_digest
```

## Quarterly / Annual Rollups

The quarterly/annual rollup renderer is reusable as
`lynchpin.retrospective.life_rendering.render_life_rollups(...)`.

```bash
direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.retrospective.life_rollups
```

## YouTube oEmbed Enrichment

```bash
direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.retrospective.life_oembed enrich
```

This appends to the JSONL cache, infers the range from `monthly_life_latest.json`, and is safe to re-run.

## Full Refresh Sequence

```bash
direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.retrospective.life build \
  --markdown-output-dir artefacts/retrospective/life-range/life_drilldowns_latest

direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.retrospective.life_digest

direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.retrospective.life_rollups
```

Optional fourth step:

```bash
direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.retrospective.life_oembed enrich
```

This is the canonical long-range artefact surface. It should evolve by sharing
more evidence/context infrastructure with the rest of Lynchpin, not by growing a
parallel subsystem.
