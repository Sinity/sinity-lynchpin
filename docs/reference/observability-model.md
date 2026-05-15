# Observability Model

This document is the concrete map for machine/performance observability. It is
not a new data lake. Each surface below must have one role: canonical raw
capture, project-native ledger, derived substrate, operational view, or legacy
backfill.

Raw precision is a hard invariant. Summaries, daily rollups, event windows,
and search indexes are derived accelerators; they must never be the only copy
of a high-entropy source such as journald, asciinema, screenshots, audio,
keylogs, or raw provider exports.

## Problem Model

Performance questions are joins over four kinds of state:

1. Workload state: command, repo revision, inputs, cache profile, service
   profile, start/end time, exit status.
2. Machine state: CPU/GPU power, thermals, PCIe link, PSI, D-state tasks,
   scheduler oversleep, memory and swap.
3. Service state: systemd/user-unit active state, resource counters, backup and
   capture services, Sinex/Polylogue runtime state.
4. Data-quality state: collector gaps, schema version, legacy field loss,
   cache contamination, missing backup/migration evidence.

The substrate should preserve those dimensions rather than collapse them into a
single "fast/slow" number. Causal claims require controlled treatment manifests;
observational claims must say "associated with".

## Current Input Roles

The machine-readable version lives in
`lynchpin.sources.observability_catalog`.

| Input | Owner | Role | Current action |
| --- | --- | --- | --- |
| `machine.telemetry` | Sinnix | Canonical raw capture | Promote live SQLite samples into `machine_metric_sample` and `machine_service_state`. |
| `machine.power_watchdog_legacy` | Sinnix | Legacy backfill | Backfilled into `machine_metric_sample`; migrated parquet/manifests moved out of active captures into `/realm/inbox/quarantine/20260515/machine-legacy-power-watchdog-unified/`. |
| `machine.below` | Sinnix | Operational view | Keep short-retention time-travel history; export bounded incident/experiment windows only. |
| `machine.network` | Sinnix | Canonical raw capture | Integrated into `captures/machine/telemetry.sqlite` as `network_sample`, promoted into `machine_network_sample`. |
| `machine.sinnix_observe` | Sinnix | Derived/operator report | Keep as a report, not a canonical dataset. Shrink as sources get first-class tables. |
| `sinex.self_observation` | Sinex | Project-native ledger | Read Sinex rollups from Lynchpin; do not copy Sinex internals into Sinnix. |
| `polylogue.run_ledger` | Polylogue | Project-native ledger | Join to machine/service windows for agent-load experiments. |
| `machine.experiment_run` | Sinnix | Canonical raw capture | Promote immutable run manifests into `machine_experiment_run`. |

## Integration Rules

- Sinnix captures host facts and operator smoke checks.
- Sinex and Polylogue own their native runtime ledgers.
- Lynchpin owns cross-source modelling, promotion, joins, readiness, and
  statistical analysis.
- `below` is a microscope, not a lake. Promote bounded windows when needed.
- `sinnix-observe` is an operator view, not an authority.
- Legacy raw data may leave a live namespace only when it is duplicated by a
  canonical raw location or is a confirmed corrupt/derived/intermediate
  byproduct. Typed substrate rows and summaries are indexes, not replacements
  for raw logs.

## Near-Term Work Queue

1. Keep network probing inside machine telemetry; `/realm/data/captures/network`
   is a retired source after historical JSONL is imported.
2. Add a Sinex self-observation source and substrate table that reads stable telemetry rollups.
3. Reduce `sinnix-observe` to a thin report over first-class sources.
4. For benchmark claims, use randomized run manifests and join telemetry by
   timestamp. Do not compare uncontrolled time periods causally.
