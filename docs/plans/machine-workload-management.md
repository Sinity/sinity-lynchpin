# Machine Workload Management — Analysis, Realtime Observability, and Anti-Thrash Control

**Status:** Plan (draft 2026-06-09)
**Goal:** Systematic, semantic analysis of what runs on `sinnix-prime` over time;
a realtime API exposing live per-workload resource state; a Noctalia surface that
makes it observable, legible, and controllable; and — the keystone — an automatic
workload **admission/park queue** that keeps only the workloads that *fit in RAM*
active, freezing+reclaiming the rest so the box **never thrashes**.

> Thrash never has to happen: 31 GiB RAM, 8 GiB capped swap. The kernel gives us
> every enforcement primitive (cgroup.freeze, memory.reclaim, PSI epoll, per-cgroup
> memory accounting, runtime MemoryHigh). What's missing is the **policy layer** —
> a userspace daemon — plus the analysis to tune it and the surface to see/steer it.

---

## 0. Problem statement (evidence-grounded)

Observed failure mode (June 7–8 2026): up to **5 concurrent agent sessions**, dozens
of build/nix-build scopes, `io.pressure full avg300 ≈ 75%` — i.e. three-quarters of
wall time, *every* task stalled waiting on I/O. Per-session "IO" reached 1+ TB, but
that figure is **page-cache thrash re-reads**, not unique work: under memory pressure
the build-input cache is evicted and re-read hundreds of times.

Root cause is not "too many agents" (the box ran fine with concurrency before). It is
**unbounded admission**: N heavy workloads start whenever they want, their combined
working set exceeds RAM, the page cache collapses, and the NVMe saturates re-reading
evicted pages. The existing guards don't prevent it:

- **sinnix**: slice CPUWeight/IOWeight/MemoryHigh + earlyoom + oomd. These *weight*
  and *kill*, they don't *queue*. Weighting fair-shares a thrashing system; it doesn't
  stop the thrash. The new flock (this session) serializes nix builds — a crude global
  mutex, not a general fit-the-set scheduler.
- **sinex**: PSI preflight (`xtask check/test/bench` refuse on Severe pressure) — but
  `--allow-contended-host` bypasses it, and agents pass it reflexively. **Advisory
  preflight a flag can skip is not enforcement.**

**Design principle that follows:** enforcement must live at the **resource/admission
layer** (one scope per workload, a daemon that decides what runs), not as an advisory
check inside each tool. A workload should not be able to "opt out" of physics.

---

## 1. What already exists (do not rebuild)

### Telemetry (captured to `/realm/data/captures/machine/`, read by `lynchpin/sources/machine.py`)
- `metric_sample` (10 Hz): full PSI (cpu/io/mem × some/full × 10/60/300 s), load,
  cpu/gpu power+temp, **`mem_avail_mb`, `swap_used_mb`** (system-wide), `latency_oversleep_ms`,
  `dstate_task_count`.
- **`service_state` (1 Hz per systemd unit): `memory_current_bytes`** — the per-workload
  RAM signal. **Already promoted to DuckDB** (`machine_service_state`,
  `load_machine_service_state_summary()` → MAX per unit).
- `process_io_delta_sample`, `service_cgroup_pressure_sample`, `service_cgroup_io_sample`:
  per-PID / per-cgroup IO and PSI. **Live-only** (not in DuckDB → slow MCP).
- `below` store: bounded per-process snapshots, `BelowEntitySummary.max_rss_mb` on demand.

### Analysis (`lynchpin/analysis/machine/`, ~44 modules)
- `episodes.py`: **14 episode kinds** incl. `memory_pressure`, `swap_pressure`,
  `io_pressure`, `system_stall` — a thrash-episode model already exists.
- `workloads.py` / `timeline.py` / `session_profile.py` (this session): semantic
  workload classification, scope timeline w/ temporal attribution, per-session causal IO.
- `attribution.py`, `feature_frames.py`, `mining.py`, `controlled_benchmarks.py`,
  `calibration.py`, `measurement_system.py`: a full causal-inference scaffold.
- ~20 MCP `machine_*` tools.

### Enforcement primitives (verified live, kernel 6.18.34)
- `cgroup.freeze`, `memory.reclaim`, per-cgroup `memory.pressure`/`memory.current`/
  `memory.stat`/`memory.swap.current`, runtime `MemoryHigh`/`MemoryMax`/`MemorySwapMax`,
  PSI epoll triggers. cgroup2 with `memory_recursiveprot`. zswap compiled in (unused).
- Slice tree: `agent`(W=400/300), `nix-build`(W=5/2, Mem 10/18G), `build`(W=5/2, Mem 3/8G),
  `background`(W=3/1, Mem 2/4G). oomd kill at 25%/30% slice pressure. `sinnix-scope` wraps
  work into transient scopes per class.

---

## 2. Gap analysis → what to build

| Capability | Have | Gap |
|---|---|---|
| Per-workload RAM footprint over time | `memory_current_bytes` 1 Hz, in DuckDB | No **anon-vs-cache split** (need `memory.stat:anon` — the true RAM-binding figure); no **capacity timeline** ("at T, resident set = X, headroom = Y, what was forced out") |
| Thrash episodes | `episodes.py` 14 kinds | Not **attributed to the workload set** that caused them; no "this episode = these 4 scopes' combined anon exceeded RAM" |
| Live state | nothing | **All analysis is batch/historical.** No realtime reader of cgroupfs |
| Control | nothing | No freeze/park; no admission queue |
| Observability surface | nothing | No Noctalia widget |

Four build pillars follow. They are layered: **A (analysis)** sharpens the model and
calibrates thresholds; **B (realtime API)** exposes live state; **C (control plane)** is
the daemon that prevents thrash; **D (Noctalia)** is the human surface. C is the keystone;
A and B are its sensors; D is its dashboard and manual override.

---

## Pillar A — Sharpen the analysis (lynchpin)

**A1. Promote the live-only telemetry to DuckDB.**
`process_io_delta_sample`, `service_cgroup_pressure_sample`, `service_cgroup_io_sample`
are re-parsed from 400–770 MB NDJSON on every MCP call. Add promoters + readers following
the existing `machine_service_state` pattern (`substrate/machine.py`). Bump `SUBSTRATE_VERSION`.
- Files: `substrate/schema.py` (3 table DDLs), `substrate/machine.py` (promoters + loaders),
  `analysis/materialize.py` (DAG steps).
- Tests: round-trip per table in `tests/substrate/`.

**A2. Capture the anon/cache split.**
`memory_current_bytes` alone overstates RAM need by the (free-to-evict) file cache. The
RAM-binding figure is `memory.stat:anon`. The capture daemon (sinex/machine-telemetry —
see §integration) must additionally sample, per managed cgroup: `memory.stat` (anon, file,
slab_unreclaimable, kernel_stack, sock), `memory.swap.current`, `memory.peak`. Add fields
to `MachineServiceState` (or a new `MachineCgroupMemorySample`).
- **This is a capture-side change in sinex** — coordinate; see §integration.

**A3. Memory-capacity timeline (`analysis/machine/capacity.py`).**
New module. For a window, reconstruct at each sample tick:
`resident_anon = Σ scope.anon`, `headroom = mem_total − reserve − resident_anon`,
`would_not_fit = [scopes that, by start order, pushed resident_anon over budget]`.
Output `CapacityTimeline` / `CapacitySlice` with the same dataclass+to_dict idiom.
Answers: "when did the active set stop fitting, and which scope tipped it over?"

**A4. Thrash attribution (`analysis/machine/thrash.py` or extend `attribution.py`).**
Join `episodes.py` memory/io_pressure episodes against the capacity timeline + scope
timeline: each thrash episode → the set of scopes whose combined anon exceeded budget
during it, ranked by contribution. This is the empirical "what causes contention"
deliverable. MCP tool `machine_thrash_attribution(start, end)`.

**A5. Workload footprint model (`analysis/machine/footprint.py`).**
Per workload *class* (nix-build, rust-build, pytest, agent-session, …), aggregate historical
`memory.peak` / `anon` percentiles → an estimated peak-anon distribution. This is the
admission daemon's sizing input (Pillar C). MCP tool `machine_workload_footprints()`.

Pillar A deliverable: a calibrated, queryable model of *what fits, what doesn't, and what
thrashes* — and the per-class footprint estimates that let the daemon size admissions.

---

## Pillar B — Realtime resource API (new daemon, lightweight)

Everything in lynchpin is historical. The control plane and the Noctalia widget need
**live** state, read directly from cgroupfs (not the capture pipeline).

**B1. `lynchpin/machine/live.py` — a pure-read live snapshot library.**
Walks `/sys/fs/cgroup/.../user@1000.service/{agent,build,background,nix}.slice/*.scope`
and the slices themselves, reading per scope: `memory.current`, `memory.stat` (anon/file),
`memory.swap.current`, `cpu.pressure`/`memory.pressure`/`io.pressure` (some+full),
`cgroup.events:frozen`, `cgroup.freeze`. Cross-references scope unit → comm/cmdline (from
`/proc/<pid>`) and → polylogue session (existing enrichment). Returns a typed
`LiveSnapshot{ scopes: list[LiveScope], totals, headroom, psi }`. Sub-10ms, no I/O amplification.

**B2. `lynchpin.machine.live` daemon mode + IPC.**
A tiny long-running process (systemd user service `lynchpin-machine-live.service`) that
samples `live.py` every ~1 s and publishes the latest snapshot as:
- a JSON file at `/run/user/1000/lynchpin/machine-live.json` (atomic write-rename), and
- a unix socket (`/run/user/1000/lynchpin/machine-live.sock`) emitting newline-delimited
  JSON snapshots for subscribers (Noctalia, the control daemon).
The JSON file is the cheap poll path; the socket is the push path. Both are read-only;
no analysis, no DuckDB, no capture coupling.

**B3. MCP bridge.** `machine_live_snapshot()` tool returns the current `/run` JSON so an
agent can ask "what's running right now and is anything frozen?" without shelling out.

Pillar B deliverable: live per-workload {anon, swap, PSI, frozen?} at 1 Hz over a file +
socket, consumable by widgets, the daemon, and agents.

---

## Pillar C — Anti-thrash admission & park queue (the keystone daemon)

`lynchpin-workload-manager.service` (user service). A small, auditable daemon. Policy only;
all mechanism is kernel/systemd. **This is what makes thrash structurally impossible.**

**C1. One scope per workload (already true).** `sinnix-scope` already wraps agent/build/
nix-build/background work into transient scopes. The daemon manages *these* scopes — no
change to how work is launched, except routing heavy work through `sinnix-scope` (already
the convention). Direct `nix build` etc. are covered by the existing flock as a coarse backstop.

**C2. Budget.** `RAM_BUDGET_ANON = MemTotal − RESERVE`. RESERVE protects kernel + a
page-cache floor + the foreground/interactive set (agent.slice gets `MemoryLow` recursive
protection). Start ~24 GiB anon budget on 31 GiB; tune from Pillar A's capacity timeline.

**C3. Admission.** Before a managed scope is allowed to run heavy work, the daemon checks
`Σ est_peak_anon(active) + est(new) ≤ RAM_BUDGET_ANON` (estimates from Pillar A's
footprint model; unknown classes admitted conservatively + observed). If it fits → run.
If not → **queue** it (the scope is created **frozen** at birth, or held pre-spawn).

**C4. Feedback / park loop (PSI-driven, not polling).** epoll on `memory.pressure full`
(global + per-slice) with a trigger below oomd's 25% kill threshold (e.g. `full` stall
> ~8% sustained). On fire:
1. Rank active managed scopes by (priority asc, anon desc). Foreground agent + interactive
   are top priority (never parked). Background/build/nix-build are park candidates.
2. **Park the lowest-priority victim: `cgroup.freeze` → `memory.reclaim <its anon>`** (the
   two-step; freeze alone frees nothing). Record swap consumed.
3. Re-measure; repeat until `full` pressure clears.
On recovery (pressure low + headroom restored): **thaw** the most-recently-parked / highest-
priority parked scope, admit the next queued workload if it now fits.

**C5. Swap ceiling guard.** Aggregate parked anon ≤ swap headroom (8 GiB − system swap).
Track `memory.swap.current`; if parking the next victim would exceed swap, the daemon must
**not** park-and-reclaim (it would just move thrash to swap) — instead hold admission / let
oomd kill / surface to the user. **This is where the 8 GiB cap becomes the design's spine:**
the daemon keeps the *active anon set within RAM* and the *parked anon set within swap*, so
neither RAM nor swap ever oversubscribes → no thrash, by construction.
- **Open decision (§risks): enable zswap** to multiply park capacity beyond 8 GiB physical
  swap. Interfaces present; costs CPU. Recommend evaluating once the daemon exists.

**C6. Priority model.** Static defaults by slice (agent/interactive > build > nix-build >
background), overridable per-scope at launch (`sinnix-scope --priority`) and live via the
Noctalia surface / a control socket. Manual "pin" (never park) and "park now" / "kill" verbs.

**C7. Backstop.** `MemoryMax` per scope + oomd remain as the last line; the daemon's job is
to act *before* them. earlyoom stays the global emergency guard.

Pillar C deliverable: a daemon that admits work only when it fits, parks (freeze+reclaim)
the lowest-priority overflow to swap within the 8 GiB budget, and resumes on recovery —
making sustained thrash impossible while keeping the top workloads fully active.

---

## Pillar D — Noctalia observability & control surface

Noctalia v5 is a Quickshell/Qt Wayland shell, but **custom widgets are Lua scripts** (not
QML) via the `barWidget` API. The mechanism maps cleanly onto Pillars B and C:
- `barWidget.define({...})` manifest + `barWidget.setUpdateInterval(ms)` + `function update()`
  for periodic polling; `update()` can read files / parse `/proc` / run commands directly.
- Display: `barWidget.setText/setColor/setGlyph`; config via `barWidget.getConfig`.
- Interaction: `onClick()`, `onRightClick()`, `onIpc(event, payload)`; actions via
  `noctalia.runAsync(cmd)`, `noctalia.notify(...)`, `noctalia.runInTerminal(...)`.
- A daemon can **push** into the widget: `noctalia msg scripted-widget <name> <target> <event> <payload>`.
- Register in `config.toml`: `[widget.workload-monitor] type="scripted" script="widgets/workload-monitor.lua"`.
- Lives in **sinnix** at `dots/noctalia/widgets/workload-monitor.lua` (out-of-store symlink →
  edits propagate without rebuild); enable in `dots/noctalia/config.toml` bar widget list.

**D1. Live workload widget** (`dots/noctalia/widgets/workload-monitor.lua`).
`update()` (every ~500–1000 ms) reads `/run/user/1000/lynchpin/machine-live.json` (Pillar B)
and renders the bar glyph + a dropdown/tooltip panel: one row per managed scope showing
comm/project, anon (GiB), swap, a PSI indicator, and a frozen/active badge. The bar header
shows RAM headroom + global `memory.pressure full`, colored green→amber→red on thrash risk.
No `/proc` walking in the widget — Pillar B already did the cheap read; the widget just
displays JSON. (Avoids the I/O-amplification trap of N widgets each walking cgroupfs.)

**D2. Control verbs.** Per-row actions in the dropdown (`onClick`/`onRightClick`, or panel
buttons) write a control intent to the workload-manager daemon's control socket
(`/run/user/1000/lynchpin/workload-ctl.sock`) via `noctalia.runAsync`: **pin** (never park),
**park now** (freeze+reclaim), **thaw**, **raise/lower priority**, **kill**. This realizes
"pick workloads to pause/freeze." The daemon, conversely, pushes alerts to the widget with
`noctalia msg scripted-widget workload-monitor all <event>` (e.g. flash red + `noctalia.notify`
when it parks something or when swap headroom is low).

**D3. Mode toggle.** A widget control (or `noctalia msg`) flips the daemon between **auto**
(manages admission/park automatically — the default, the answer to "queue system that does so
automatically") and **manual** (daemon observes + advises via notifications; the user drives
park/thaw from the widget). Stored in the daemon, surfaced as a glyph state.

Pillar D deliverable: a glanceable, clickable bar surface showing the live resource truth,
letting the user steer individual workloads or fully delegate to the daemon — and receiving
proactive alerts when the daemon acts or when the swap ceiling is approached.

---

## Integration & coordination

- **Capture-side changes (A2) live in sinex** (the `machine-telemetry` recorder), not
  lynchpin. Coordinate: add `memory.stat`/`memory.swap.current`/`memory.peak` sampling per
  managed cgroup. Until then, Pillar B's live reader can read `memory.stat` directly (it does
  cgroupfs reads anyway), so the daemon is not blocked on the capture change.
- **sinnix owns the units**: `lynchpin-machine-live.service` and
  `lynchpin-workload-manager.service` are NixOS user services defined in sinnix (alongside
  `performance.nix`), pointing at lynchpin entrypoints. The daemon's budget/thresholds are
  sinnix config (they belong with the slice definitions).
- **Supersede, don't stack**: once the workload-manager daemon parks by fitting-the-set, the
  sinex `--allow-contended-host` preflight becomes redundant for managed work (physics is
  enforced upstream). Keep it only for unmanaged invocations; revisit removing it.
- **The flock stays** as a coarse backstop for direct nix builds outside the queue.

---

## Phasing

1. **A1 + B1/B2** (promote telemetry; live reader + daemon + IPC file/socket). Low risk,
   immediately useful (observability), unblocks everything.
2. **A2 + A3 + A5** (anon split capture; capacity timeline; footprint model). Calibrates C.
3. **C** behind a **manual/observe-only mode first** — daemon computes what it *would* park
   and logs it; verify against real episodes before it acts. Then enable auto-park.
4. **D** (Noctalia surface) — observability rows first (read-only), then control verbs.
5. **A4** (thrash attribution) — retrospective validation that the daemon prevents the
   episodes the model used to detect.
6. **zswap evaluation** (C5 open decision).

## Risks / open decisions

- **zswap on/off** given 8 GiB hard swap (C5). Recommend: evaluate after C exists.
- **Foreground protection**: getting `RESERVE` + `MemoryLow` right so the interactive agent
  is never parked/throttled into unusability. Calibrate from Pillar A.
- **PSI threshold calibration** — io.pressure idles ~3.7% here; set triggers above noise and
  below oomd's 25% kill. Per-box tuning, not constants.
- **Park-storm safety**: reclaiming a victim itself causes swap-out IO; ensure the daemon
  doesn't trade memory thrash for swap-IO thrash (rate-limit reclaim, prefer file-cache drop
  first via per-call `swappiness`).
- **Verify-before-ship** (kernel specifics): exact `-EAGAIN` on `memory.reclaim` shortfall;
  `swappiness=` arg acceptance on 6.18.34; live oomd kill threshold vs daemon park threshold.
