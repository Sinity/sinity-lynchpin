# Ecosystem Context: Sinnix ↔ Lynchpin ↔ Sinex

> Extracted from analysis sessions. This documents the relationship between the three main projects.

## The Three Layers

The personal infrastructure stack has three complementary layers, each with distinct responsibilities:

```
┌─────────────────────────────────────────────────────────────┐
│  SINNIX (Static Infrastructure)                             │
│  ─────────────────────────────────                          │
│  • NixOS system configuration                               │
│  • Home-manager user environment                            │
│  • Declarative package management                           │
│  • Secrets via sops/agenix                                  │
│  • "The static configuration layer"                         │
└─────────────────────────────────────────────────────────────┘
                              │
                              │ configures & deploys
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  LYNCHPIN (Read/Query Layer)                                │
│  ─────────────────────────────                              │
│  • Pull-based data integration                              │
│  • SQLite views over external sources                       │
│  • Personal dashboards & reports                            │
│  • "The derived read layer until Sinex captures it"         │
└─────────────────────────────────────────────────────────────┘
                              │
                              │ migrates to / queries from
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  SINEX (Event Core)                                         │
│  ─────────────────────────────                              │
│  • Push-based event capture                                 │
│  • NATS JetStream transport                                 │
│  • PostgreSQL/TimescaleDB event store                       │
│  • Nodes + automata constellation                           │
│  • "The event-sourced kernel / exocortex core"              │
└─────────────────────────────────────────────────────────────┘
```

## Division of Responsibility

| Concern | Owner | Rationale |
|---------|-------|-----------|
| System packages, dotfiles, services | **Sinnix** | Declarative, reproducible, version-controlled |
| Pull-based external data (bank feeds, calendars) | **Lynchpin** | Until Sinex has appropriate nodes |
| Event capture, provenance, synthesis | **Sinex** | Push-based, real-time, auditable |
| Dashboards over external data | **Lynchpin** | Temporary home until data flows through Sinex |
| Long-term memory, queryable history | **Sinex** | Event-sourced storage with replay capability |

## Migration Path

When deciding where new functionality belongs:

1. **"Make X part of the exocortex"** typically means:
   - Does X belong as a Sinex **event source/schema/processor**?
   - Or as a Lynchpin-derived view **until** it migrates into Sinex?

2. **Lynchpin is a waystation**, not a destination:
   - External data sources start in Lynchpin (pull-based SQLite)
   - Once Sinex has appropriate nodes, data flows through the event bus
   - Lynchpin continues serving read-only dashboards and ad-hoc queries

## REALM Filesystem Structure

The physical layout follows a clear pattern:

```
/realm/
├── project/
│   ├── sinnix/     # NixOS configuration
│   ├── lynchpin/   # Read-layer views
│   └── sinex/      # Event-sourced core
│
└── data/
    ├── captures/   # Raw source material (git-annex)
    ├── exports/    # User-facing exports
    ├── libraries/  # Reference data
    └── indices/    # Search indices, embeddings
```

## Operational Model

```
"Deploying Sinex is not committing to the final exocortex."
```

The system is designed for incremental deployment:
- Run 0 (first deployment) validates the core pipeline
- New nodes are added incrementally as capture sources mature
- Lynchpin continues serving use cases Sinex doesn't yet cover
- Sinnix manages the declarative deployment of both

---

*This is cross-project context. For Sinex-specific architecture, see `../current/architecture/`.*
