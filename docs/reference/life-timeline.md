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
END="$(date +%Y-%m)"

direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.system.life_timeline \
  --start 2013-10 \
  --end "$END" \
  --output artefacts/lifelog/life-timeline/monthly_life_latest.json \
  --markdown-output-dir artefacts/lifelog/life-timeline/life_drilldowns_latest
```

Use a dated JSON filename instead of `monthly_life_latest.json` when you want a pinned snapshot.

## Digest

```bash
direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.system.life_timeline_digest \
  --life-json artefacts/lifelog/life-timeline/monthly_life_latest.json \
  --output artefacts/lifelog/life-timeline/digests/life_earliest_to_now.monthly.md
```

## Quarterly / Annual Narrative

```bash
direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.system.life_timeline_narrative \
  --life-json artefacts/lifelog/life-timeline/monthly_life_latest.json \
  --output artefacts/lifelog/life-timeline/narratives/life_auto_summary.md
```

## YouTube oEmbed Enrichment

```bash
direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.system.life_timeline_oembed enrich \
  --life-json artefacts/lifelog/life-timeline/monthly_life_latest.json \
  --cache artefacts/lifelog/life-timeline/youtube_oembed_cache.jsonl \
  --start 2013-10 \
  --end "$END"
```

This appends to the JSONL cache and is safe to re-run.

## Full Refresh Sequence

```bash
END="$(date +%Y-%m)"

direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.system.life_timeline \
  --start 2013-10 \
  --end "$END" \
  --output artefacts/lifelog/life-timeline/monthly_life_latest.json \
  --markdown-output-dir artefacts/lifelog/life-timeline/life_drilldowns_latest

direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.system.life_timeline_digest \
  --life-json artefacts/lifelog/life-timeline/monthly_life_latest.json \
  --output artefacts/lifelog/life-timeline/digests/life_earliest_to_now.monthly.md

direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.system.life_timeline_narrative \
  --life-json artefacts/lifelog/life-timeline/monthly_life_latest.json \
  --output artefacts/lifelog/life-timeline/narratives/life_auto_summary.md
```

Optional fourth step:

```bash
direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.system.life_timeline_oembed enrich \
  --life-json artefacts/lifelog/life-timeline/monthly_life_latest.json \
  --cache artefacts/lifelog/life-timeline/youtube_oembed_cache.jsonl \
  --start 2013-10 \
  --end "$END"
```
