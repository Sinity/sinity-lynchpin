# Calendar Views Workflow

Calendar dossiers are rendered directly from the trajectory stack so the
delivery surface stays aligned with the actual typed read model. The canonical
reusable surface is `lynchpin.retrospective.build_calendar_views(...)`; the CLI
is now just a thin file-writing wrapper over that API.

Important correction: date-range narratives are no longer a separate
`lynchpin.views.calendar_narratives` CLI. That surface was pure delivery glue.
The reusable API now lives in `lynchpin.retrospective.narrative`, and agent
orchestration belongs in the `lynchpin-ops` skill.

## Commands

### Render Day Views Via API
```bash
direnv exec /realm/project/sinity-lynchpin python - <<'PY'
from datetime import date

from lynchpin.retrospective import CalendarScale, build_calendar_views

views = build_calendar_views(
    date(2025, 12, 28),
    date(2025, 12, 31),
    scale=CalendarScale.day,
    write_files=False,
)
print(views[0].markdown)
PY
```

### Render Day Views Via CLI
```bash
direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.views.calendar_views 2025-12-28 2025-12-31
```

This runs the thin `lynchpin.views.calendar_views` wrapper. Internally it
delegates to `lynchpin.retrospective.build_calendar_views(...)`, which resolves
the date window and summarizes it into `TrajectoryDay` rows via
`lynchpin.trajectory.*`.

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

The same API also handles week/month/quarter/year dossier builds via
`CalendarScale.week|month|quarter|year`.

## Range Narratives

Use the `lynchpin-ops` skill when you want an agent to assemble and generate a
date-range retrospective. The canonical reusable surface is the Python API:

```bash
direnv exec /realm/project/sinity-lynchpin python - <<'PY'
import asyncio
from datetime import date

from lynchpin.retrospective import generate_date_range_narrative

result = asyncio.run(
    generate_date_range_narrative(
        date(2025, 12, 28),
        date(2025, 12, 31),
        mode="reflective",
        backend="codex-exec",
    )
)
print(result.text)
PY
```

Notes:
- `codex-exec` is the default backend because it uses the local Codex CLI
  login and ChatGPT/Codex subscription path rather than an API key.
- `claude-agent-sdk` remains available if you explicitly select it.
- Successful runs append logs under `artefacts/retrospective/narratives/logs/`.
