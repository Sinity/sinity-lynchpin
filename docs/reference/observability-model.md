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
- Use valid same-run monotonic timestamps for duration estimates. Wall timestamps
  locate telemetry; a wall-only duration stays inspection evidence. Failed,
  cancelled, censored, partial, and discarded warmup runs remain visible with
  exclusion reasons instead of entering successful-duration contrasts.
- Use process/cgroup windows for attribution rather than assigning all host
  pressure to the most visible foreground command.
- Report unsupported assumptions and refusal reasons alongside successful
  claims.

Machine-facing MCP actions expose status, metrics, pressure, services,
workloads, observations, benchmark runs, diagnostics, and context windows over
this model.

Machine promotion keeps earlier fact partitions when publishing a new generation.
It reads the previous watermark's UTC day and later source rows in bounded
batches. Typed readers and machine aggregates resolve the current partition
and its predecessors under one logical refresh identity, excluding replaced
dates and clipping retained history to the requested window. Raw SQL tables
contain physical partitions; their rows alone are not a complete generation.

Service-state and cgroup-memory sources may still require a SQLite scan because
they lack timestamp indexes. That scan does not rebuild or index the historical
DuckDB facts. Explicit full rebuilds and uncovered historical prefixes require
a complete population and remain subject to resource limits. An incomplete
Parquet prefix cannot establish a safe tail watermark.

Machine fact tables use columnar storage without ART primary-key or secondary
indexes. Their full historical indexes cannot be evicted by DuckDB's buffer
manager, so they can prevent a bounded initial population. Promoters instead
validate non-null, unique fact keys through spillable SQL before accepting a
partition. A failed append rolls back and a failed staged replacement preserves
the earlier partition. The additive migration copies existing machine rows
exactly inside a transaction; it does not change owner-native captures.
See [DuckDB's indexing guide](https://duckdb.org/docs/guides/performance/indexing).

For explicit typed-fact restoration, run the declared `repair_machine_facts`
operation with `--start`, `--end` and, for an initial repair, `--full`.
It stops the existing `materialize-machine` DAG after source-selected
promotion. It preserves the other retained products and does not run the
Polylogue archive campaign. Publication records its separate machine refresh;
the evidence graph retains its own refresh and coverage until rebuilt.
