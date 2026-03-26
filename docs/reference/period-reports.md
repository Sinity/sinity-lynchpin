# Period Reports Workflow

Period reports are generic rendered summaries over the shared period and
evidence-bundle surfaces. They replace the old calendar-specific report layer.

The canonical reusable API is:

- `lynchpin.context.reports.build_period_report(...)`
- `lynchpin.context.reports.build_period_reports(...)`

The CLI is:

- `python -m lynchpin.context.reports START END --scale <day|week|month|quarter|half|year>`

## Commands

### Render Day Reports Via API
```bash
direnv exec /realm/project/sinity-lynchpin python - <<'PY'
from datetime import date

from lynchpin.context.reports import build_period_reports

reports = build_period_reports(
    date(2025, 12, 28),
    date(2025, 12, 31),
    scale="day",
    write_files=False,
)
print(reports[0].markdown)
PY
```

### Render Day Reports Via CLI
```bash
direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.context.reports 2025-12-28 2025-12-31 --scale day
```

By default this writes Markdown under `artefacts/context/reports/` using the
same hierarchical period layout as the narrative tree.

To emit structured payloads instead:

```bash
direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.context.reports 2025-12-28 2025-12-31 --scale day --no-write-files --json
```

With `write_files=False` / `--no-write-files`, neither the Markdown report nor
its evidence bundle is persisted.

Each generated report is backed by a colocated evidence bundle and summarizes:

- freshness/trust state for the core warehouse surfaces,
- delivery telemetry,
- project attention,
- chat/provider activity,
- polylogue session profiles and session titles,
- git churn and hot paths,
- focus spans and focus loops,
- derived pattern signals such as episodes, anomaly kinds, and recent focus loops,
- and circadian/context-switch structure.

The same API and CLI handle week, month, quarter, half-year, and year reports
through the `--scale` option.

## Range Narratives

Use the `lynchpin-ops` skill when you want an agent to assemble and generate a
date-range retrospective. Period reports are evidence-first operator artefacts;
narratives are the authored synthesis built on top of them.
