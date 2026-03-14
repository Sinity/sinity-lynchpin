# Baseline Workflow

`lynchpin.system.baseline` is the canonical baseline rebuild entrypoint. Run it directly; there is no separate wrapper surface.

## Default Rebuild

```bash
direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.system.baseline \
  --mode auto \
  --output-dir artefacts/core/baseline/latest
```

This prefers a frozen input bundle under `/realm/data/sinity-lynchpin/baseline-inputs/latest` when present, then falls back to live local sources.

## Scoped Live Rebuild

```bash
direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.system.baseline \
  --mode live \
  --since 2026-01-01T00:00:00Z \
  --until 2026-03-01T00:00:00Z \
  --output-dir artefacts/core/baseline/2026-01_to_2026-03
```

Use `--window-days N` instead of `--since/--until` when a rolling window is enough.

## Frozen Bundle Rebuild

```bash
direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.system.baseline \
  --mode bundle \
  --session-root /realm/data/sinity-lynchpin/baseline-inputs/2026-03-01 \
  --output-dir artefacts/core/baseline/2026-03-01
```

Use this when you need a rerun that will not drift with live DBs, repos, or session logs.

## Optional Web Sample

```bash
direnv exec /realm/project/sinity-lynchpin \
  python -m lynchpin.system.baseline \
  --mode live \
  --include-web-sample \
  --web-bucket aw-watcher-web-firefox_sinnix-prime \
  --output-dir artefacts/core/baseline/latest
```

## Outputs

- `git_numstat.jsonl`
- `activitywatch_window_summary.json`
- `activitywatch_afk_summary.json`
- `atuin_summary.json`
- `codex_summary.json`
- `sleep_summary.json`

All outputs land under the selected `--output-dir`.
