# Focus Dashboards Requirements

## Objective
Deliver day/week/month dashboards that surface attention patterns by combining ActivityWatch windows (AFK-adjusted), Atuin command density, git deltas, chat/session cadence, and wearable recovery metrics.

## Views
1. **Daily Timeline (24h ribbon)**
   - Input: `activity_timeline.json`, ActivityWatch window segments, AFK flags.
   - Visuals: stacked timeline (colour-coded categories), overlay markers for Codex/Claude sessions, command spikes, git commits.
   - AFK handling: collapse idle blocks >15 minutes into dimmed bands; highlight “long AFK” (>4h) using AFK window stats.

2. **Weekly Rollup**
   - Aggregate daily metrics into 7-day columns.
   - Metrics: active hours, AFK hours, codex sessions, command totals, git churn (lines changed), wearable recovery average (sleep hours, HRV placeholder).
   - Provide “focus index” = (active_hours / (active + AFK)) weighted by command density and session counts.

3. **Monthly Overview**
   - Trend lines for active vs AFK hours, codex session volume, git lines changed.
   - Top project focus: derive from Atuin project counts + git repo totals for the month.

## Data Pipeline
1. Run `scripts/build_baseline.py` to refresh core JSONs.  
2. Extend pipeline with `scripts/build_wearables.py` (TODO) for steps, HR, stress.  
3. Materialise a DuckDB dataset (`data/derived/focus_dashboard.duckdb`) joining:
   - `activity_timeline.json`
   - Atuin category pivot
   - Git per-day churn
   - Codex/Claude session index
   - Wearable daily aggregates
4. Use either Plotly Dash or a static Observable notebook hosted locally; ensure exports write to `results/<date>/dashboards/`.

## Implementation Tasks
- [ ] Sketch DuckDB schema + SQL that joins timeline, git, wearable data.  
- [ ] Build prototype view (Plotly/Altair) for the daily ribbon.  
- [ ] Define colour palette + category taxonomy (coding, research, comms, entertainment).  
- [ ] Add CLI entrypoint `python scripts/render_dashboards.py --date-range ...` to regenerate assets.  
- [ ] Document usage in `docs/pipelines/dashboards.md`.
