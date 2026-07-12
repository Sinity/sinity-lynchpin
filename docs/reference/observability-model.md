# Observability model

Machine and performance analysis is a join across workload, host, service, and
data-quality state. Lynchpin preserves these dimensions instead of collapsing
them into one health or speed score.

## Evidence dimensions

1. **Workload:** command/task, repository revision, inputs, cache profile,
   service profile, timestamps, and outcome.
2. **Machine:** CPU/GPU power and thermals, pressure stall information,
   memory/swap, scheduler delay, storage/network observations, and cgroup
   counters.
3. **Service:** systemd/user-unit state, runtime inventory, resource class,
   capture health, backups, and dependent service readiness.
4. **Data quality:** collector gaps, sampling cadence, schema version, coverage,
   clock alignment, and benchmark validity.

Observational evidence can support an association. Causal language requires a
controlled treatment or a design that explicitly justifies it.

## Ownership

| Surface | Owner | Lynchpin role |
| --- | --- | --- |
| Machine telemetry SQLite | Sinnix | Promote canonical metric, GPU, network, process, cgroup, and service samples. |
| `below` recordings | Sinnix | Use as a time-travel microscope and import bounded workload windows. |
| Runtime inventory | Sinnix | Read service/resource metadata as deployment context. |
| `sinnix-observe` | Sinnix | Treat as an operator report, not a fact database. |
| Sinex runtime ledgers | Sinex | Read stable project-native rollups; do not copy internal state ownership into Sinnix. |
| Polylogue work/session ledgers | Polylogue | Join AI workload evidence to machine and service windows. |
| Experiment manifests | Capture/benchmark runner | Promote immutable run design, treatment, environment, and outcome metadata. |
| Cross-source attribution | Lynchpin | Build workload windows, matched evidence, diagnostics, and calibrated claims. |

## Analysis contract

- Keep high-frequency raw measurements at their owning capture source.
- Materialize typed rows with explicit timestamps, host, unit, and refresh
  provenance.
- Preserve gaps; do not interpolate through collector outages without marking
  the result.
- Separate pre-treatment context from post-treatment outcomes.
- Record cache/warmup state and reject contaminated benchmark comparisons.
- Use process/cgroup windows for attribution rather than assigning all host
  pressure to the most visible foreground command.
- Report unsupported assumptions and refusal reasons alongside successful
  claims.

Machine-facing MCP actions expose status, metrics, pressure, services,
workloads, observations, benchmark runs, diagnostics, and context windows over
this model.
