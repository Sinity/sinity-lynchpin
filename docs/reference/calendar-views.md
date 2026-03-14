# Calendar Views (Lynchpin-first)

The legacy calendar batch pipeline has been retired. Calendar dossiers are now
rendered directly from the Lynchpin modules so every view stays consistent with
the live data mirror.

## Commands

### Render Day Views
```
direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.views.calendar_views 2025-12-28 2025-12-31
```
This runs the `lynchpin.views.calendar_views` CLI, calling
`lynchpin.views.calendar.load_day()` for each date. By default it writes Markdown under
`artefacts/calendar/views/day-YYYY-MM-DD.md`, but you can stream results instead:

```
direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.views.calendar_views 2025-12-28 2025-12-31 --no-write-files --json
```

Set `--no-write-files` to skip disk writes, and `--json` to emit one JSON object per line (the `DaySummary.to_dict()` payload) for downstream tooling.

Each generated view (or streamed payload) includes:
- Focus/AFK hour totals and top applications/domains from ActivityWatch.
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
`lynchpin.views.calendar_narratives` reads directly from `lynchpin.views.calendar`, so narratives
always reflect the same data the view builder
produces. Prompts and outputs live under `artefacts/calendar/narratives/`.

## Next Steps
1. Add week/month aggregations on top of the view builder output.
2. Replace the last references to the deprecated HTML site with lightweight
   Markdown to static HTML converters once the new view is stable.
3. Extend the calendar summaries with more warehouse-backed source slices where
   the current per-day dossier is still thin.
