# Artifacts System Redesign

## Current Problems

**Fragmentation**
- Multiple overlapping representations: calendar (day markdown), baseline (timeline JSON), ledgers (CSVs)
- Intermediate files that provide no value over final outputs
- No clear hierarchy or navigation between artifacts
- Inconsistent formatting and accessibility

**Inefficiency**
- Separate pipelines doing similar work
- Calendar and baseline are conceptually the same but stored differently
- Ledgers track metadata but aren't integrated with main views
- Manual commands needed to regenerate different artifact types

**Poor Presentation**
- Most artifacts are raw JSON/CSV/markdown files
- Velocity.html is great but isolated from other artifacts
- No unified interface to explore all data
- Hard to understand relationships between artifacts

## Design Principles

**Single Source of Truth**
- Warehouse DuckDB database as canonical storage
- All views derived from warehouse tables
- No intermediate JSON/CSV files unless they serve external tools

**Presentation Layer**
- Rich, interactive HTML dashboards
- Connected navigation between views
- Consistent design language (like velocity.html)
- Self-contained (no external dependencies for viewing)

**Derived from Modules**
- Lynchpin modules (sources, ingest, views) model the domain
- Artifacts are just presentation of that data
- Minimal computation in artifact generation

## Proposed Structure

```
artefacts/
├── index.html              # Main dashboard with overview
├── timeline/
│   ├── index.html          # Timeline explorer (replaces calendar + baseline)
│   └── data.js             # Embedded timeline data
├── velocity/
│   └── index.html          # Keep existing velocity dashboard
├── sources/
│   ├── index.html          # Data source health dashboard
│   └── details/
│       ├── activitywatch.html
│       ├── atuin.html
│       ├── git.html
│       └── ...
├── projects/
│   ├── index.html          # All projects overview
│   └── [project]/
│       ├── index.html      # Project details
│       └── metrics.html    # Project-specific metrics
├── warehouse/
│   └── index.html          # DuckDB explorer interface
└── validation/
    └── index.html          # Validation results dashboard
```

## Implementation Phases

### Phase 1: Core Infrastructure ✅ COMPLETE
- ✅ Create main dashboard (index.html) with navigation
- ✅ Set up shared CSS/design system (minimal, clean, 4K-optimized)
- ✅ Create data export utilities from baseline/calendar
- ✅ Add justfile command `artifacts-dashboard`

### Phase 2: Timeline View ✅ COMPLETE
- ✅ Unified timeline view combining calendar + baseline data
- ✅ Interactive date picker and jump-to-date
- ✅ Daily detail cards with activity indicators
- ✅ Load-more pagination for performance
- ✅ Navigation between overview and timeline

### Phase 3: Source Health Dashboard
- Show all configured data sources
- Validation status and data freshness
- Integration with validation results
- Links to source-specific detail pages

### Phase 4: Project Dashboard
- List all projects with key metrics
- Integrate velocity charts per-project
- Recent commits and activity
- Link to project bundles

### Phase 5: Warehouse Explorer
- Interactive SQL query interface
- Pre-built queries for common patterns
- Table browser with schema info
- Export results as CSV/JSON

## Data Flow

```
lynchpin sources → warehouse (DuckDB) → view modules → artifact generation → HTML + embedded data
```

Key change: Stop generating intermediate JSON/CSV. Instead, views query warehouse directly and embed data in HTML.

## Migration Strategy

**Keep Working:**
- Don't break existing functionality
- Old artifacts can coexist during transition
- Gradually migrate to new system

**Deprecation Path:**
1. Build new system alongside old
2. Mark old artifacts as deprecated in docs
3. Update justfile to generate new artifacts
4. Remove old pipelines after validation

**Backwards Compatibility:**
- Keep warehouse structure stable
- Maintain DuckDB schema
- Existing queries should still work

## Design System

Based on velocity.html aesthetic:
- Dark theme with subtle gradients
- ECharts for interactive visualizations
- Consistent typography (JetBrains Mono + Outfit)
- Smooth animations and transitions
- Responsive layout
- Self-contained (embedded fonts, libraries)

## Next Steps

1. Create shared design system (CSS + utilities)
2. Implement main dashboard skeleton
3. Build timeline view as proof of concept
4. Validate approach with user feedback
5. Iterate and expand to other views
