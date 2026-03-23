# Life Timeline Workflow

For agents, the canonical orchestration surface is the `lynchpin-ops` skill
plus the reusable `lynchpin.retrospective.run_life_timeline(...)` API.
`lynchpin.system.life_timeline*` remains as the thin delivery layer that writes
the long-range artefacts.

Current note: month payloads now also include a bounded recent-trajectory overlay
for months inside the latest local signal window, so `life_timeline` can reuse
the newer `lynchpin.trajectory.*` substrate without trying to replay the entire
2013→now span through ActivityWatch-derived signals.

The current month-level `output`, `work`, `intake`, `mail`, `location`,
`money`, `health`, `notes`, and recent `trajectory` slices are now built
through `lynchpin.retrospective.life_timeline`, while the source-heavy build
orchestration lives in `lynchpin.retrospective.life_pipeline` and the digest /
rollup renderers live in `lynchpin.retrospective.life_outputs`.

The remaining source-heavy shaping is also moving downward: Spotify top-name
selection and YouTube watch-history/title-token fallback logic now live in the
source modules instead of being reimplemented inside the system command.

The Google Takeout fan-out is also centralized now: archive opening and the
standard life-timeline Takeout bundle parse live in `lynchpin.sources.exports.takeout`,
so `lynchpin.system.life_timeline` no longer owns that large archive-walking block.

## API

```bash
direnv exec /realm/project/sinity-lynchpin python - <<'PY'
from pathlib import Path

from lynchpin.retrospective import LifeTimelineInputs, run_life_timeline

result = run_life_timeline(
    start_month="2020-04",
    end_month="2020-06",
    output=Path("artefacts/lifelog/life-timeline/example.json"),
    inputs=LifeTimelineInputs(),
)
print(result)
PY
```

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

Add `--narrative --narrative-backend codex-exec` to generate month
retrospectives through the local Codex CLI login/subscription path.
`claude-agent-sdk` remains available if you explicitly select it.

Defaults:
- Start month: `2013-10`
- End month: current month
- JSON output: `artefacts/lifelog/life-timeline/monthly_life_latest.json`

Recent months are also enriched with canonical `lynchpin.trajectory.*`
rollups, so the current quarter carries shared active/recovery/mode/project
signals instead of relying only on the older bespoke monthly payload.

Use a dated JSON filename instead of `monthly_life_latest.json` when you want a pinned snapshot.

## Digest

The digest renderer is reusable as `lynchpin.retrospective.render_life_timeline_digest(...)`.

```bash
direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.system.life_timeline.digest
```

## Quarterly / Annual Narrative

The quarterly/annual rollup renderer is reusable as
`lynchpin.retrospective.render_life_timeline_rollups(...)`.

```bash
direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.system.life_timeline.narrative
```

## YouTube oEmbed Enrichment

```bash
direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.system.life_timeline.oembed enrich
```

This appends to the JSONL cache, infers the range from `monthly_life_latest.json`, and is safe to re-run.

## Full Refresh Sequence

```bash
direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.system.life_timeline \
  --markdown-output-dir artefacts/lifelog/life-timeline/life_drilldowns_latest

direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.system.life_timeline.digest

direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.system.life_timeline.narrative
```

Optional fourth step:

```bash
direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.system.life_timeline.oembed enrich
```
