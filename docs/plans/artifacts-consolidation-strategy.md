# Artifacts Consolidation Strategy

## Current Situation Analysis

### Data Architecture
1. **Raw Sources** → captured/exported data files
2. **Lynchpin Modules** (`sources.*`) → Python interfaces to read raw data
3. **Warehouse** (`artefacts/lynchpin/warehouse.duckdb`) → DuckDB with export data
4. **Intermediate Artifacts** → JSON/CSV files in various directories
5. **Web Dashboards** → HTML views (velocity, new dashboard)

### Current Problems

**1. Multiple Representations of Same Data**
- `activity_timeline.json` (baseline) vs `day-*.md` (calendar) = same data, different formats
- Both aggregate from ActivityWatch/Atuin/Codex/Git
- Neither is canonical - both are derived views

**2. Warehouse Not Fully Utilized**
- Warehouse CAN store ActivityWatch/Atuin/Codex (`TABLE_SPECS` exist)
- But current `warehouse.duckdb` only has export data (reddit, goodreads, spotify)
- Activity tracking data still in separate JSON files

**3. No Single Source of Truth**
- Some data in warehouse
- Some data in baseline JSONs
- Some data in calendar markdown
- Ledgers as separate CSVs
- No clear canonical store

**4. Gap: Missing Data in Views**
- Reddit activity (comments, posts, votes) - in warehouse but not visualized
- Spotify listening - in warehouse but not visualized
- Finance transactions - in warehouse but not visualized
- Goodreads reading - in warehouse but not visualized
- Web history - not in current views
- Health/sleep data - minimal representation

## Proper Solution: True Consolidation

### Phase 1: Complete the Warehouse
**Goal**: Make warehouse the single source of truth

1. **Materialize ALL sources into warehouse**
   ```bash
   python -m lynchpin.views.warehouse materialize \
     --sources activitywatch,atuin,codex,gitstats,reddit,spotify,finance,goodreads,webhistory
   ```

2. **Verify warehouse completeness**
   - Check all tables exist
   - Validate row counts
   - Compare against baseline data

3. **Update validation to check warehouse health**
   - Data freshness checks
   - Row count tracking
   - Source coverage metrics

### Phase 2: Build Comprehensive Dashboards
**Goal**: Rich, unified views from warehouse

1. **Activity Dashboard** (expand current)
   - Pull from warehouse tables instead of baseline JSON
   - Add missing dimensions: web browsing, spotify, git detail
   - Interactive filters and drill-downs

2. **Consumption Dashboard** (NEW)
   - Reading: Goodreads books, web articles
   - Listening: Spotify streams
   - Watching: (future: YouTube history)
   - Social: Reddit activity timeline

3. **Financial Dashboard** (NEW)
   - Transaction timeline
   - Category breakdowns
   - Spending patterns

4. **Health Dashboard** (NEW)
   - Sleep patterns
   - Activity tracking
   - Wearable data

5. **Social/Communication Dashboard** (NEW)
   - Reddit: posts, comments, saved, votes
   - Facebook Messenger threads/messages
   - Wykop entries

6. **Development Dashboard** (enhance velocity)
   - Git activity from warehouse
   - Codex session details
   - Project correlations

### Phase 3: Consolidate Analysis Artifacts
**Goal**: Remove redundancy and centralize analysis output

1. **Consolidate analysis artifacts:**
   - `artefacts/analysis/derived/` now hosts all processed analysis outputs (formerly scattered across multiple directories)
   - `artefacts/calendar/` → activity dashboard (sourced from lynchpin modules)
   - `artefacts/core/baseline/` → data now in warehouse
   - `artefacts/knowledge/ledgers/` → query warehouse instead

2. **Keep only:**
   - Warehouse database (canonical)
   - Generated dashboards (views)
   - Velocity charts (enhanced)
   - Validation results (metadata)

3. **Update pipelines:**
   - Remove direct calendar wrapper surfaces in favor of warehouse + dashboard
   - Remove `just baseline` → use warehouse + dashboard
   - Keep `just artifacts-dashboard` but read from warehouse

### Phase 4: Query Interface
**Goal**: Ad-hoc exploration

1. **Warehouse Explorer Dashboard**
   - Schema browser
   - SQL query interface
   - Pre-built query templates
   - Export results as CSV/JSON
   - Visualization builder

## Implementation Plan

### Step 1: Warehouse Population (Next)
- Run materialize with all sources
- Verify data integrity
- Document any gaps or issues

### Step 2: Dashboard Rewrite
- Build replacement views directly from `lynchpin.views.calendar_views` and warehouse-backed summaries
- Add missing data sources to the surviving dashboards
- Create new dashboard pages for consumption/finance/health/social

### Step 3: Surface Consolidation
- Delete superseded artifact exporters once replacements are live
- Update documentation to point only at canonical views
- Keep one output path per user-facing surface

### Step 4: Maintenance
- Regular warehouse refresh (cron/systemd)
- Automated validation checks
- Dashboard regeneration on data updates

## Benefits

1. **Single Source of Truth**: Warehouse is canonical
2. **Rich Exploration**: All data accessible, queryable
3. **No Redundancy**: One place for each piece of data
4. **Extensibility**: Easy to add new sources to warehouse
5. **Performance**: DuckDB fast for analytics
6. **Portability**: Can export warehouse, share snapshots

## Migration Safety

- Validate replacement views before removing any producer
- Keep cutovers atomic so there is one canonical surface after each change
- Regenerate surviving outputs from source modules instead of copying stale artefacts
