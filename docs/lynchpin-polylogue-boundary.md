# Lynchpin–Polylogue boundary

Polylogue and Lynchpin both expose AI-session information, but they answer
different questions. Polylogue is the archive and archive-native inference
system. Lynchpin is the cross-source evidence and analysis system.

## Polylogue owns

- ingestion from supported AI providers and local agent-session formats;
- the conversation/message/content archive;
- archive schema and migrations;
- session profiles, work events, summaries, threads, cost/usage products, and
  other inference whose primary evidence is the session archive;
- the typed `SyncPolylogue` facade used by local Python consumers.

Lynchpin does not write to the Polylogue database and does not independently
reimplement transcript ingestion.

## Lynchpin owns

- source readiness from the perspective of cross-source analysis;
- promotion of Polylogue products into a coherent DuckDB refresh;
- joins between AI work events and Git, GitHub, ActivityWatch, terminal,
  machine, or personal evidence;
- an optional work-kind overlay whose source and resolved labels remain
  visible;
- context packs, evidence graphs, confidence views, and analyses that span
  multiple owners.

## Read contract

Normal reads use the typed Polylogue facade or stable archive-product tables.
`archive_readiness()` may inspect SQLite directly because it must diagnose the
archive even when facade construction or a required product read is degraded.
That diagnostic exception does not permit ordinary analysis to bypass the
facade or depend on private archive internals.

When direct product reads and facade fallback both exist, readiness reports the
selected lane and any degradation. Missing required Polylogue products are not
represented as empty evidence.

## Label resolution

Polylogue's work-event kind remains the source label. Lynchpin can derive an
overlay from paths, tools, duration, and related cross-source features. The
substrate preserves:

- `source_kind` and source confidence;
- `overlay_kind` and overlay confidence;
- the resolved `kind`;
- `kind_source`, which records agreement, disagreement, or the selected lane.

This allows kind-quality audits without silently rewriting the archive's own
inference.

## Evolution rules

| Change | Owner action |
| --- | --- |
| Polylogue archive schema changes | Polylogue migrates it and preserves/adapts the facade contract. |
| Polylogue adds a stable inference product | Lynchpin adds a promoter only when cross-source queries need it. |
| Polylogue retires a product | Lynchpin removes its promoter and substrate table in the corresponding substrate-version change. |
| Lynchpin adds an overlay feature | Lynchpin updates the resolver and quality tests; Polylogue source labels remain untouched. |
| Cross-source inference proves broadly archive-native | Move the inference contract upstream deliberately, then remove the duplicate Lynchpin layer. |

Readiness flows from Polylogue health into Lynchpin source readiness, then into
substrate and context-pack readiness. Each layer reports its own contract
without claiming ownership of the one below it.
