# Lynchpin Explosion Proposal

**Date:** 2025-01-17
**Status:** Draft / Under Consideration

## The Problem

Lynchpin currently conflates multiple concerns that don't naturally belong together:

1. **Data declarations** - defining where files/DBs live on the filesystem (system config concern)
2. **HPI-style library** - programmatic access to personal datasets (library concern)
3. **Legacy pipelines** - scripts for processing data (should be daemon or disappear)
4. **Analysis documents** - markdown reflections on what data implies (knowledgebase concern)
5. **Sinevec service** - vector search (standalone service concern)

The repo was designed as a "launch pad"—you cd into it, run scripts, and it reaches out to everything. But that's backwards. A library should be *available everywhere*, not *launched from somewhere*.

## Core Insight

From `project_architecture_reconceptualization.md`:

> **Polylogue (Library):** Responsible purely for ingesting, normalizing, and providing a semantic API for conversation logs. It has no UI, no database, and no daemon. It is a dependency.
>
> **Lynchpin (Consumer):** Imports the Polylogue library to ingest data into its warehouse, run its watchers, and display its data in the dashboard.

This pattern should apply more broadly. Lynchpin should become a *consumer* of a library, not the library itself bundled with infrastructure.

## Proposed Explosion

### New Repositories

#### 1. `sinity-hpi` - Pure Data Access Library

A library following the HPI (Human Programming Interface) pattern. No daemon, no UI, no scripts—just programmatic access to personal datasets.

```
sinity-hpi/
├── sinity_hpi/
│   ├── __init__.py
│   ├── config.py           # Reads env vars (set by sinnix)
│   ├── sources/
│   │   ├── captures/       # ActivityWatch, Atuin, Codex, webhistory
│   │   ├── exports/        # Reddit, Spotify, Polylogue, health, etc.
│   │   ├── libraries/      # Dendron, finance, substack
│   │   └── indices/        # Git repos, session ledger
│   └── views/
│       ├── calendar.py     # DaySnapshot, load_day()
│       ├── warehouse.py    # DuckDB table definitions + queries
│       └── velocity.py     # Code metrics
├── external/               # Vendored upstream HPI modules
│   ├── hpi/
│   ├── hpi-sinity/
│   └── hpi-purarue/
├── pyproject.toml
└── flake.nix               # Exposes as Python package
```

**Key properties:**
- Importable from anywhere: `from sinity_hpi.sources.captures import activitywatch`
- No hardcoded paths—reads from environment variables
- Depends on `polylogue` library for chat data
- Lazy evaluation, cachew-backed where appropriate

#### 2. `sinevec` - Vector Search Service (Un-merge)

Currently merged into lynchpin as `lynchpin/sinevec/`. Should return to standalone status.

```
sinevec/
├── sinevec/
│   ├── ingest/
│   │   ├── chats.py        # Embed polylogue transcripts
│   │   ├── bookmarks.py    # Embed raindrop
│   │   └── code.py         # Embed repo snapshots
│   ├── search/
│   │   └── core.py         # Qdrant query interface
│   ├── server.py           # FastAPI endpoints
│   └── cli.py              # CLI entry point
├── pyproject.toml
└── flake.nix
```

**Rationale:** Sinevec is a *service* with external dependencies (Qdrant, Voyage AI). It doesn't belong in a pure data library.

### Enhanced Existing Repositories

#### 3. `sinnix` - System Config + Daemon

Sinnix already declares paths via `sinnix.paths.*`. It should also:
- Declare project paths via `sinnix.projects.*` (done)
- Run the lynchpin daemon as a systemd service
- Own the "alive" component that was envisioned in the architecture doc

```
sinnix/
├── modules/
│   ├── foundation.nix      # User, machine options
│   ├── projects.nix        # Ecosystem project paths (done)
│   └── services/
│       ├── lynchpin.nix    # NEW: Daemon service
│       ├── sinevec.nix     # NEW: Vector search service
│       ├── polylogue.nix   # Existing
│       └── qdrant.nix      # Existing
└── flake.nix
    # Inputs: sinity-hpi, sinevec, polylogue
```

**Daemon service sketch:**

```nix
# modules/services/lynchpin.nix
{ config, lib, pkgs, inputs, ... }:
let
  cfg = config.sinnix.services.lynchpin;
  sinity-hpi = inputs.sinity-hpi.packages.${pkgs.system}.default;
in
{
  options.sinnix.services.lynchpin = {
    enable = lib.mkEnableOption "Lynchpin data coordination daemon";

    warehouse = {
      autoRefresh = lib.mkEnableOption "Automatic warehouse refresh";
      refreshInterval = lib.mkOption {
        type = lib.types.str;
        default = "hourly";
        description = "Systemd calendar expression for refresh schedule";
      };
    };

    server = {
      enable = lib.mkEnableOption "Lynchpin query server";
      port = lib.mkOption {
        type = lib.types.port;
        default = 8420;
      };
    };
  };

  config = lib.mkIf cfg.enable {
    systemd.services.lynchpin-sync = lib.mkIf cfg.warehouse.autoRefresh {
      description = "Lynchpin warehouse sync";
      serviceConfig = {
        Type = "oneshot";
        ExecStart = "${sinity-hpi}/bin/lynchpin warehouse refresh";
        User = config.sinnix.user.name;
      };
      environment = {
        LYNCHPIN_DATA_ROOT = config.sinnix.paths.dataRoot;
        LYNCHPIN_REPO_ROOT = config.sinnix.projects.lynchpin;
      };
    };

    systemd.timers.lynchpin-sync = lib.mkIf cfg.warehouse.autoRefresh {
      wantedBy = [ "timers.target" ];
      timerConfig.OnCalendar = cfg.warehouse.refreshInterval;
    };

    systemd.services.lynchpin-server = lib.mkIf cfg.server.enable {
      description = "Lynchpin query server";
      wantedBy = [ "multi-user.target" ];
      serviceConfig = {
        ExecStart = "${sinity-hpi}/bin/lynchpin serve --port ${toString cfg.server.port}";
        User = config.sinnix.user.name;
        Restart = "always";
      };
      environment = {
        LYNCHPIN_DATA_ROOT = config.sinnix.paths.dataRoot;
      };
    };
  };
}
```

#### 4. `knowledgebase` - Absorbs Analysis Docs

Move `lynchpin/docs/analysis/` and `lynchpin/docs/personal/` to knowledgebase.

```
knowledgebase/
├── analysis/               # FROM lynchpin/docs/analysis/
│   ├── data-narratives/
│   └── retrospectives/
├── personal/               # FROM lynchpin/docs/personal/
└── ...existing structure...
```

**Rationale:** Analysis and reflection belong in a PKM system, not a code repo.

## Migration Map

| Current Location | New Home | Notes |
|------------------|----------|-------|
| `lynchpin/sources/` | `sinity-hpi/sinity_hpi/sources/` | Core of the library |
| `lynchpin/views/` | `sinity-hpi/sinity_hpi/views/` | Query functions |
| `lynchpin/core/config.py` | `sinity-hpi/sinity_hpi/config.py` | Reads env vars |
| `lynchpin/core/cache.py` | `sinity-hpi/sinity_hpi/cache.py` | Cachew wrapper |
| `lynchpin/sinevec/` | `sinevec/` repo | Un-merge |
| `lynchpin/system/sinnix.py` | `sinnix` (if needed) or drop | Introspection |
| `lynchpin/system/sinex.py` | `sinex` repo | Belongs with sinex |
| `lynchpin/system/meta.py` | Drop | Was self-referential |
| `lynchpin/ingest/` | `sinity-hpi` or drop | Evaluate case-by-case |
| `external/hpi*` | `sinity-hpi/external/` | Vendored dependencies |
| `pipelines/` | Drop or daemon commands | Legacy scripts |
| `docs/analysis/` | `knowledgebase/analysis/` | Reflection belongs in PKM |
| `docs/personal/` | `knowledgebase/personal/` | High-sensitivity docs |
| `docs/plans/` | `knowledgebase/plans/` or `sinity-hpi/docs/` | TBD |
| `docs/reference/` | `sinity-hpi/docs/` or sinnix | Data source docs |
| `artefacts/` | `/realm/data/artefacts/` | Output, not source |
| `config/my/` | `sinity-hpi/config/` | HPI config |
| `justfile` | `sinity-hpi` (library commands) + sinnix (system commands) | Split by concern |

## CLI Design Post-Explosion

The `lynchpin` CLI becomes a client to the daemon and/or direct library calls:

```bash
# Query operations (direct library calls or daemon queries)
lynchpin calendar 2025-01-15           # Get day snapshot
lynchpin calendar 2025-01-01 2025-01-15 --format json
lynchpin warehouse query "SELECT * FROM activitywatch LIMIT 10"
lynchpin search "topic"                # Vector search (via sinevec)

# Daemon control (if daemon model adopted)
lynchpin status                        # Health check
lynchpin warehouse refresh             # Trigger sync
lynchpin warehouse status              # Last refresh, row counts

# System integration
systemctl status lynchpin-server       # Daemon via systemd
systemctl start lynchpin-sync          # Manual sync trigger
```

## Environment Variables

Sinnix sets these; `sinity-hpi` reads them:

```bash
# Set by sinnix modules
LYNCHPIN_DATA_ROOT=/realm/data
LYNCHPIN_CAPTURES_ROOT=/realm/data/captures
LYNCHPIN_EXPORTS_ROOT=/realm/data/exports
LYNCHPIN_LIBRARIES_ROOT=/realm/data/libraries
LYNCHPIN_INDICES_ROOT=/realm/data/indices
LYNCHPIN_ARTEFACTS_ROOT=/realm/data/artefacts

# Project roots (from sinnix.projects.*)
LYNCHPIN_REPO_ROOT=/realm/project/sinity-lynchpin  # Legacy, phase out
SINITY_HPI_ROOT=/realm/project/sinity-hpi          # If needed
POLYLOGUE_ROOT=/realm/project/polylogue
SINEX_ROOT=/realm/project/sinex
```

## What Remains of `sinity-lynchpin`?

After explosion, the repo could:

**Option A: Archive**
- Move everything out, archive the repo
- History preserved, no active development

**Option B: Become `sinity-hpi`**
- Rename in place, remove non-library code
- Keeps git history for the library portions

**Option C: Keep as coordination layer**
- Thin wrapper that imports sinity-hpi
- Hosts the daemon entry point
- But this seems like it should just be sinnix

**Recommendation:** Option B (rename to `sinity-hpi`, strip non-library code)

## Open Questions

1. **Daemon vs no daemon?** Could the "alive" component just be systemd timers calling library functions, without a persistent daemon?

2. **Where does the web dashboard live?** Currently `sinevec/server.py` serves search. Should there be a unified dashboard? In sinnix? Separate repo?

3. **What about `pipelines/`?** These are one-off analysis scripts. Do they become:
   - Part of the library (if general-purpose)
   - Knowledgebase (if tied to specific analysis)
   - Just deleted (if obsolete)

4. **Gradual vs big-bang migration?** Could extract `sinity-hpi` first, keep lynchpin as consumer, then gradually shrink lynchpin.

5. **Naming:** `sinity-hpi` vs `sinity-data` vs `sinity-core` vs something else?

## Next Steps

1. [ ] Decide on daemon model (persistent service vs systemd timers)
2. [ ] Create `sinity-hpi` repo with minimal structure
3. [ ] Move `lynchpin/sources/` and `lynchpin/views/` to new repo
4. [ ] Update sinnix flake to consume `sinity-hpi`
5. [ ] Create `sinnix/modules/services/lynchpin.nix`
6. [ ] Move analysis docs to knowledgebase
7. [ ] Decide sinevec fate (un-merge or keep with daemon)
8. [ ] Archive or rename `sinity-lynchpin`

## References

- `docs/project_architecture_reconceptualization.md` - Original architectural vision
- `sinnix/modules/foundation.nix` - Path declaration patterns
- `sinnix/modules/projects.nix` - Project path formalization (just added)
- Polylogue architecture - Model for "pure library" pattern
