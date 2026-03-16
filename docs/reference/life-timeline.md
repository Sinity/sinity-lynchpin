# Life Timeline Workflow

The life timeline stack is a direct composition of `lynchpin.system.life_timeline*` modules. Run the modules explicitly; there is no wrapper refresh command anymore.

Current note: month payloads now also include a bounded recent-trajectory overlay
for months inside the latest local signal window, so `life_timeline` can reuse
the newer `lynchpin.trajectory.*` substrate without trying to replay the entire
2013→now span through ActivityWatch-derived signals.

The current month-level `output`, `work`, `intake`, `mail`, `location`,
`money`, `health`, `notes`, and recent `trajectory` slices are now built
through `lynchpin.context.life_timeline`, and the Markdown drilldown renderer
lives there too, so the system command is shedding schema/render ownership
rather than continuing to accumulate it inline.

The remaining source-heavy shaping is also moving downward: Spotify top-name
selection and YouTube watch-history/title-token fallback logic now live in the
source modules instead of being reimplemented inside the system command.

The Google Takeout fan-out is also centralized now: archive opening and the
standard life-timeline Takeout bundle parse live in `lynchpin.sources.exports.takeout`,
so `lynchpin.system.life_timeline` no longer owns that large archive-walking block.

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

Recent months are also enriched with canonical `lynchpin.trajectory.*`
rollups, so the current quarter carries shared active/recovery/mode/project
signals instead of relying only on the older bespoke monthly payload.

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
