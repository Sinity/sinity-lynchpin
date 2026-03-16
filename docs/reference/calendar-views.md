# Calendar Views Workflow

Calendar dossiers are rendered directly from the current `lynchpin.views.*`
surface so every view stays consistent with the live data mirror.

## Commands

### Render Day Views
```
direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.views.calendar_views 2025-12-28 2025-12-31
```
This runs the `lynchpin.views.calendar_views` CLI, calling
`lynchpin.context.calendar.load_day_summaries()` for the requested range. By default it writes Markdown under
`artefacts/calendar/views/day-YYYY-MM-DD.md`, but you can stream results instead:

```
direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.views.calendar_views 2025-12-28 2025-12-31 --no-write-files --json
```

Set `--no-write-files` to skip disk writes, and `--json` to emit one JSON object per line (the `DaySummary.to_dict()` payload) for downstream tooling.

Each generated view (or streamed payload) includes:
- Trajectory-derived active/recovery/focus totals, dominant modes/projects, and activity-chain highlights.
- Top applications/domains from the underlying signal layer.
- Atuin command counts and shell hot spots.
- Git commit counts/lines per repo.
- Session/chat highlights pulled from the session ledger.
- Wearable sleep summary when available.
- Current Sinex branch/head summary and Sinnix instrumentation toggles.

### Generate Narratives
```
direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.views.calendar_narratives 2025-12-28 2025-12-31 --mode reflective
```
`lynchpin.views.calendar_narratives` reads the same `lynchpin.context.calendar`
range summaries as the view builder, so narratives always reflect the same
typed day substrate. Prompts and outputs live under
`artefacts/calendar/narratives/`.
