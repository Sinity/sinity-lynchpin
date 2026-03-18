# Calendar Views Workflow

Calendar dossiers are rendered directly from the trajectory stack so the
delivery surface stays aligned with the actual typed read model.

## Commands

### Render Day Views
```bash
direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.views.calendar_views 2025-12-28 2025-12-31
```

This runs the `lynchpin.views.calendar_views` CLI. Internally it resolves the
date window, loads raw signals, classifies them, stitches activity chains, and
summarizes them into `TrajectoryDay` rows via `lynchpin.trajectory.*`.

By default it writes Markdown under
`artefacts/calendar/views/day-YYYY-MM-DD.md`, but you can stream the structured
payloads instead:

```bash
direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.views.calendar_views 2025-12-28 2025-12-31 --no-write-files --json
```

Set `--no-write-files` to skip disk writes, and `--json` to emit one JSON
object per line from `TrajectoryDay.to_dict()` for downstream tooling.

Each generated view or streamed payload includes:
- trajectory-derived active and recovery totals,
- dominant modes, projects, and topics,
- chain, signal, command, transcript, and commit counts,
- project breakdowns and anomaly annotations,
- source coverage summaries,
- current `lynchpin.system.sinex` repo-state metadata and Sinnix host toggles.

### Generate Narratives
```bash
direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.views.calendar_narratives 2025-12-28 2025-12-31 --mode reflective
```

`lynchpin.views.calendar_narratives` reads the same trajectory-backed day
substrate as the view builder, so Markdown dossiers and generated narratives
stay aligned. Prompts and outputs live under `artefacts/calendar/narratives/`.
