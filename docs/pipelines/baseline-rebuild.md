# Baseline Rebuild Pipeline

## Purpose
Recompute the 2025-10-23 baseline artefacts (ActivityWatch windows + AFK, Codex cadence, Atuin command stats, git deltas, wearable sleep summary) from the canonical local datasets so regenerated outputs stay reproducible and explainable.

## Requirements
- ActivityWatch exports under `data/raw/activitywatch/session-2025-10-23/`.
- Merged wearable data at `/realm/data/health/processed/sleep_merged.jsonl`.
- Python devshell (`direnv allow` or `nix develop`) which provides pandas, numpy, typer.

## Usage
```bash
python scripts/build_baseline.py \
  --session-root data/raw/activitywatch/session-2025-10-23 \
  --health-root /realm/data/health/processed \
  --output-dir results/2025-10-23-baseline-rebuilt
```

Optional flags:
- `--include-web-sample --web-bucket aw-watcher-web-firefox_sinnix-prime` to snapshot the web bucket via the local ActivityWatch API if it’s running.
- `--activitywatch-api http://127.0.0.1:5600/api/0` to override the AW endpoint.

Outputs land in the chosen directory with the same filenames as the original baseline (`activitywatch_*`, `atuin_summary.json`, `codex_sessions_summary.json`, `git_activity_summary.json`, `sleep_summary.json`, `activity_timeline.json`, plus `supporting/git_numstat_summary.json` and a copy of `git_numstat.jsonl`).

## What the Script Does
- Aggregates ActivityWatch window buckets into daily/monthly totals and per-month top apps.
- Aligns AFK spans into daily/monthly active vs idle hours and long/short AFK block diagnostics.
- Summarises Codex sessions by day, month, and hour profile.
- Counts Atuin commands per day/month, derives per-project totals, and coarsely categorises command activity for timeline overlays.
- Collates git numstat data into per-repo/per-month rollups, repo totals, and supporting daily/weekly surge reports.
- Rebuilds the sleep segment statistics (segment histogram, daily totals, per-block aggregates) from the merged Samsung Health + Sleep As Android export.
- Fuses the above streams into `activity_timeline.json` with AFK-adjusted hours, window hours, Codex sessions, and Atuin command density per day.

## Notes
- The script is idempotent; reruns overwrite the existing JSON outputs in `--output-dir`.
- To keep large raw data out of Git, only derived JSON is written. If you need full repro filings, point `--output-dir` at a dated folder under `results/`.
- Command categories are heuristic (based on `cwd` patterns); adjust `_categorise_command` in `scripts/build_baseline.py` if new project roots appear.
- Wearable metrics currently focus on sleep. Extending the pipeline to steps/heart-rate/stress is the next planned iteration.
