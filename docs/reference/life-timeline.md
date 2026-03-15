# Life Timeline Workflow

The life timeline stack is a direct composition of `lynchpin.system.life_timeline*` modules. Run the modules explicitly; there is no wrapper refresh command anymore.

## Monthly JSON + Drilldowns

### Fixed Range

```bash
direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.system.life_timeline \
  --start 2020-04 \
  --end 2023-04 \
  --output artefacts/lifelog/life-timeline/monthly_life_2020-04_to_2023-04.json \
  --markdown-output artefacts/lifelog/life-timeline/life_2020-04_to_2023-04.generated.md
```

### Open-Ended “Latest” Refresh

```bash
direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.system.life_timeline \
  --markdown-output-dir artefacts/lifelog/life-timeline/life_drilldowns_latest
```

Defaults:
- Start month: `2013-10`
- End month: current month
- JSON output: `artefacts/lifelog/life-timeline/monthly_life_latest.json`

Use a dated JSON filename instead of `monthly_life_latest.json` when you want a pinned snapshot.

## Digest

```bash
direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.system.life_timeline_digest
```

## Quarterly / Annual Narrative

```bash
direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.system.life_timeline_narrative
```

## YouTube oEmbed Enrichment

```bash
direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.system.life_timeline_oembed enrich
```

This appends to the JSONL cache, infers the range from `monthly_life_latest.json`, and is safe to re-run.

## Full Refresh Sequence

```bash
direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.system.life_timeline \
  --markdown-output-dir artefacts/lifelog/life-timeline/life_drilldowns_latest

direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.system.life_timeline_digest

direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.system.life_timeline_narrative
```

Optional fourth step:

```bash
direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.system.life_timeline_oembed enrich
```
