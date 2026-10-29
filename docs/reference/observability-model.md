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

## Machine telemetry

### Reading the SQLite source

The collector may publish `DATABASE.segments.json`
(`sinnix-machine-sqlite-segments-v1`) beside its current SQLite file: sealed
member files, the current file, and each file's recorded identity. Without the
manifest, the single file is the source. A manifest that is transitioning,
malformed or incomplete makes the source unavailable; it never makes history
empty. Sealed members must be checkpointed and unchanged.

Readers hold a shared lock on `DATABASE.segments.lock` (owned by the collector)
for the whole read, check each member's identity before reading it, and merge
per-member results in timestamp order. A reader given an explicit file path
reads only that file. Bounded reads seek the existing composite indexes; they
never build indexes or write statistics on the source.

### Readiness and freshness

Readiness checks each known table for one row and seeks the newest timestamp
through an existing time index. It never counts history. Missing files, schema
failures and read errors stay distinct from empty tables; without a usable
index, freshness is reported as unknown. Samples older than 24 hours are stale.

Promotion freshness compares the newest live timestamp with the newest row in
the serving generation, per table. A windowed check also compares row counts
over the same UTC range within a two-second budget; equal counts show matching
cardinality, not equal payloads.

Producer-service evidence (coverage CLI, context packs, MCP readiness) reads
service state for the units declared in the runtime inventory, within a
three-second budget. Inactive and unobserved time are reported separately.
Live application health (`sinexctl runtime health`) is
reported only by the MCP readiness tool, never by graph or dataset readiness.
`LYNCHPIN_APPLICATION_HEALTH=0` disables those probes;
`LYNCHPIN_APPLICATION_HEALTH_TIMEOUT`, `SINEX_RUNTIME_TARGET_CONFIG` and
`LYNCHPIN_SINEX_HEALTH_CA_CERT` configure them.

### Promotion

Promotion is incremental: it reads from the previous watermark's UTC day in
bounded batches and keeps earlier partitions under one logical refresh
identity. Fact keys are validated through spillable SQL instead of DuckDB
indexes. A failed append rolls back; a failed replacement keeps the earlier
partition. Inheriting earlier partitions requires the same source location
(path, device, inode). GPU and cgroup rows keep the native record ID, so equal
timestamps never merge distinct samples.

`repair_machine_facts --start D --end D [--full]` restores typed facts for a
window; `--reuse-machine-candidate PATH` admits completed, schema- and
count-matching stages from a retained candidate. `materialize-machine --full`
bypasses incremental and memoized skips.

Canonical machine carriers also support an explicit affected-day refresh with
`python -m lynchpin.ingest.machine_materialize --start D --end NEXT_D` (end
exclusive). For the separate Parquet archive, ordinary
`python -m lynchpin.cli.machine_telemetry_export` verifies existing days and
refuses a source/partition mismatch. Use its explicit `--full` rebuild to
regenerate published days after an intentional source change: new output is
staged and verified before atomic replacement. A failed staged verification
preserves the previous published partition. `--tables` limits the table set;
`--full` still rebuilds every sealed day of those tables, not only the affected
day. Source SQLite segments remain read-only.

Optional bounded Below bundles default to `stability-lab` beneath the
configured machine-host root (`LYNCHPIN_MACHINE_HOST_ROOT`). The analysis APIs
and CLI share that default; `--root` still selects an explicit retained
bundle location. This directory is an exported-window collection, distinct
from Below's native live store. Cartography writes beneath the configured
analysis-output directory through the common resolver, rather than creating
a canonical source under the published report tree. Historical locations and
bundle formats remain valid explicit inputs.
