# Sinex Exocortex: Cumulative System State Report (v3.0)

**Project:** Sinex Exocortex  
**Status:** Unified Architectural Authority  
**Context:** Multi-dimensional System Audit + DX Optimization Phase + Deployment Evolution  

---

## 1. System Identity & Core Principles

Sinex is a **Universal Telemetry and Digital Memory System** designed to capture, persist, and semantically refine the state history of an operating environment. It functions as an **Event Sourcing Engine for a Local OS**.

### Core Invariants:
1.  **Absolute Provenance:** Every event must have a verifiable chain of custody (Material, Synthesis, or Inference).
2.  **Appliance Model:** The system is a self-contained unit (DB + Broker + Logic). Production is a headless instance of the Development environment.
3.  **Timeline Versioning:** History is segmented into "Runs" (Epochs). Raw data is global; interpretations (events) are run-specific.
4.  **Deep Symmetry:** Ingestors (External → Stream) and Automata (Stream → Stream) share a unified processing interface.
5.  **Fluid Boundaries:** The "Tether" allows local development code to safely interleave with production data streams.

---

## 2. Infrastructure & Deployment (The "Appliance")

Sinex collapses the distinction between development environment and production deployment by using **Devenv as the Deployment Artifact**.

### A. The Deployment Unit
*   **Definition:** `devenv.nix` serves as the single source of truth for infrastructure.
*   **Components:** 
    *   **PostgreSQL 16:** TimescaleDB (partitioning), pgvector (embeddings).
    *   **NATS JetStream:** Unifying data transit and backpressure.
    *   **Git-Annex:** Content-addressable store for binary blobs.
    *   **Supervisor:** `process-compose` manages the binary/Wasm lifecycle.
*   **Artifacts:** `sx deploy` builds an OCI container or a Systemd unit directly from the Nix definition.

### B. State Management
*   **Stateless Code:** Binaries reside in the immutable Nix Store.
*   **Persistent Volume:** The `stateRoot` (e.g., `/var/lib/sinex`) is mapped to the host, containing the DB files, JetStream store, and Git-Annex repository.

---

## 3. Data Architecture (The Memory Model)

### A. The Atomic Event (`core.events`)
Stored as a TimescaleDB Hypertable.
*   **ULID Primary Key:** Provides natural time-ordering and efficient B-tree insertion.
*   **JSONB Payload:** Structured data with mandatory schema validation.
*   **Provenance Enum:**
    *   **Material:** Link to `raw.source_material_registry` + byte offset.
    *   **Synthesis:** Link to parent `source_event_ids[]`.
    *   **Inference:** Link to `model_version`, `confidence_score`, and the `random_seed` derived from the input ULID (guaranteeing deterministic probabilistic replay).

### B. Run Management (`core.runs`)
*   **Run Sealing:** `sx run seal` moves the active event set to `audit.archived_events` and truncates derived tables.
*   **Deduplication:** `raw.source_material_registry` and Blobs are never truncated during resets, allowing Run B to reference files already captured in Run A without redundant storage.

---

## 4. The Processor Ecosystem (Satellites & Automata)

Sinex uses a **Tiered SDK** to balance power and ease of use.

### A. Implementation Tiers
1.  **`SimpleProcessor` (Lambda):** A high-level trait for 90% of use cases. Implements "Input Event → Logic → Result." Handles NATS plumbing and provenance automatically.
2.  **`StatefulStreamProcessor` (Native):** Retained for core infrastructure (e.g., `ingestd`, `fs-watcher`). Requires manual checkpointing and state management.

### B. Runtime Environment
*   **Core Logic:** Native binaries for privileged I/O.
*   **Refinement Logic:** Wasm modules (WASI) executed via **Wasmtime** embedded in the gateway, providing memory isolation and hot-swappable plugins.

### C. Ingestion Strategy (`ingestd`)
*   **MaterialAssembler:** Handles the "Begin → Slices → End" protocol.
*   **Bounded Buffering:** Reassembles small streams in RAM; spills large streams to sparse files.
*   **Semantic Backpressure:** Uses semaphores to limit concurrent assemblies, preventing inode/FD exhaustion.

---

## 5. The Omni-Plane (Control & Interaction)

The system is managed by `sinex-gateway`, acting as a **Headless System Kernel** communicating via JSON-RPC (Unix Socket) and NATS.

### Three Control Heads:
1.  **CLI (`sx`):** Command-line tool for scripting, deployment, and ad-hoc queries.
2.  **TUI (`sx monitor`):** Real-time dashboard for log tailing, resource monitoring, and process control (restarts/swaps).
3.  **Web UI:** High-fidelity visualizer for the Knowledge Graph, Timeline scrubbing, and Media/Blob exploration.

---

## 6. The "Holographic" Development Environment

The `sx dev` command projects a functional Sinex cluster around local code.

### A. Dependency Auto-Detection
On invocation, `sx` performs a **Capability Scan**:
*   **DB Need:** Spins up `pg_tmp` (Postgres on RAM) and applies `sinex-schema` migrations.
*   **Broker Need:** Starts an embedded `nats-server` with JetStream.
*   **Input Need:** If an automaton is being developed, `sx` auto-starts the required upstream satellites to generate mock traffic.

### B. State-Preserving Hot-Reload
1.  **Watch:** `notify` monitors source changes.
2.  **Handoff:** V1 process receives a signal, serializes its internal state (Aggregation windows, cursors) to a memory-mapped file.
3.  **Resume:** V2 process starts, reads the state, and binds to NATS without dropping messages.

### C. The Tether (Live Debugging)
*   **Tunneling:** `sx dev --tether prod` establishes an mTLS tunnel to production.
*   **Shadow Consumer:** The local process joins a new, ephemeral NATS consumer group. It receives a **copy** of production events (fan-out) rather than stealing them.
*   **Write Sandbox:** Writes are redirected to local "Shadow Tables" to prevent production corruption while testing local fixes against real data.

---

## 7. Replay, Oracle, and Correction

Sinex solves the **Open World Assumption** to allow deterministic correction of history.

1.  **Oracle Pattern:** Satellites emit "Context Events" (e.g., weather, system load). Automata query the Event Store for these contexts based on the trigger event's `ts_orig`, ensuring the replay uses the "world as it was."
2.  **Shadow Evaluation:** To fix logic, an operator runs a new version of an automaton in a "Cascade Analysis Session." The system diffs the output against history.
3.  **Transactional Restore:** If the diff is accepted, `sx` atomically archives the old events and commits the new interpretations.

---

## 8. Security & Isolation

*   **Sanitization:** The `SanitizedPath` type and `SecurityValidator` block traversal and injection at the type level.
*   **Capabilities:** Wasm satellites are restricted to specific NATS subjects and subdirectory scopes.
*   **Auth:** Unix Socket permissions protect local access; mTLS/Tokens protect the Network Gateway. Insecure modes are stripped at compile-time for release builds.

---

## 9. Cumulative Ranking

| Category | Rating (1-10) | Status |
| :--- | :--- | :--- |
| **Architectural Health** | 10 | Unified, consistent, and follows Event Sourcing best practices. |
| **Security** | 9 | Deep path validation and planned Wasm sandboxing. |
| **Scalability** | 8 | TimescaleDB hypertable design is excellent; Ingestd is the known bottleneck. |
| **Developer Experience** | 9 | Holographic dev + Tethering provides an industry-leading feedback loop. |

**Final Assessment:** The system has matured from a collection of utilities into a **Programmable Digital Memory Appliance**. The critical technical path forward is the implementation of the `sx` unified binary and the `SimpleProcessor` SDK abstraction.
