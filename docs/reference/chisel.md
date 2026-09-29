# Chisel evidence packages

Chisel packages locally pinned source, preserved work, and owner evidence for offline inspection. It produces deterministic measurements and source pointers. It does not assign quality scores or treat task closure, command success, or matching commits as acceptance.

## Commands and defaults

`chisel`, `just chisel`, `python -m lynchpin.cli.chisel`, and `python -m lynchpin.analysis.projects chisel` share one CLI. The older `--projects`, `--output-root`, `--max-workers`, and `--list` flags remain supported. Just forwards arguments as separate shell arguments; the old positional output and worker arguments are replaced by named options.

```bash
just chisel
just chisel polylogue sinex --output /path/to/packages --workers 4
just chisel knowledgebase
just chisel polylogue --ref polylogue=candidate-branch
just chisel polylogue --target worktree
just chisel polylogue --task-root polylogue:task-id
just chisel --plan
just chisel --refresh
chisel list
chisel inspect /path/to/packages
chisel validate /path/to/packages
chisel render /path/to/project-package --format xml
```

The default selection is Sinex, Sinnix, Polylogue, and Sinity-Lynchpin. Knowledgebase requires explicit selection or `--all`. `--exclude` removes projects. `--profile source|review|evidence` expands to visible dataset selections; review is the default. `--dataset` and `--exclude-dataset` adjust those selections. `--plan` resolves local refs and reports selected datasets without acquisition, materialization, or network access. Size estimates use the previous published manifest when available; otherwise they remain unknown.

Ordinary builds read local inputs. GitHub materialization and hosted checks require `--refresh`. A remote-tracking ref is a local observation, not evidence of a fresh fetch. Owner export failures remain explicit coverage gaps. `--events FILE` appends project and stage events in JSONL. The index retains elapsed times and queue waits. The GitHub context manifest separates provider inventory, detail, and commit-reference fetch time from product writing and substrate promotion; its counts still describe coverage and reuse independently of timing.

On a terminal, progress is one line redrawn in place; in a log or pipe, each progress change prints one line. Library warnings raised during a build go to `.<output>.chisel-warnings.log` beside the output root, never into a package, and `--verbose` also prints them. A warning raised while parsing a captured Python file is recorded on that file as `parse_warnings` in `structure/file_metrics.csv`, and `structure/coverage.json` counts the affected files.

XML rendering, duplicate source archives, and SQLite indexing are optional (`--xml`, `--sqlite`). Source, JSONL evidence, and the standard-library reader are the normal inspection surfaces. Completion definitions are in `completions/`: source `_chisel` after Zsh completion initialization for both commands, `chisel.bash` for Bash, or install `chisel.fish` in Fish's completion directory. The Zsh wrapper delegates other Just recipes to `_just`. Ref completion reads local Git refs.

## Snapshot identity and source

`snapshots.json` is a versioned catalogue. The primary snapshot is the locally resolved default branch unless `--target worktree` is selected. It records the ref, commit, observation time, commit time, and unknown remote freshness. Explicit `--ref PROJECT=REF` selections become candidates. `source/` contains directly readable primary bytes, acquired through batched reads of pinned Git objects without changing the checkout. Git archive export-ignore attributes do not remove source.

`snapshots/worktree/` and candidate directories contain manifests plus changed file contents. Manifests record deleted paths, modes, hashes, and the base snapshot. Unchanged files refer to `source/`. Worktree collection verifies source coherence while collecting it; later development does not change captured bytes. Alternate metrics and structure products identify the alternate snapshot. Sinnix activation observations retain their owner clock and can identify a locally available last-activated source snapshot. They do not establish current installed or running state.

The `code_snapshots` product is current when each default project's retained `snapshots.json` still matches local Git: the primary and merged views match the default ref's current commit, and each candidate matches its own ref. A new commit on the selected branch therefore makes the view stale even when the checkout's `HEAD` file is untouched. Only a `--target worktree` primary compares the checkout: its `HEAD`, its Git status fingerprint, and the hashes of the paths Git reports as dirty. Commit-pinned and candidate views do not follow live edits. Git resolves linked worktrees. A missing repository, a project with no retained catalogue, and a package whose commit was never promoted are reported by name; none counts as current.

`inventory.jsonl` retains inclusion/exclusion reasons, source kinds, hashes, modes, and policy identity. Unsafe symlinks are excluded explicitly; safe in-repository file links retain their link provenance and captured target bytes. Git-ignored files enter only through declared local-context selections. Full committed history remains in the Git bundle. Captures and generated personal products belong outside Git.

## Measurements and relationships

Classification policy version 5 separates purpose, material, and component while retaining legacy role fields. Production Nix definitions remain identifiable as production configuration. Only eligible source/configuration files enter maintained LOC. Binary, fixtures, generated material, documentation, and context do not become maintained source. Metrics include measured subtotals, complete totals where established, and measured/excluded/inapplicable/unknown populations. An incomplete total remains unknown without erasing the measured subtotal. Rust inline test counts are non-additive subsets; test source volume is not coverage.

Python import records include scope, enclosing symbol, aliases, explicit re-exports, type guards, optional imports, and conditions. Separate named projections describe all static imports, imports excluding type-only references, and module-initialization imports excluding deferred function bodies. Each is a predicate over the canonical dependency-edge table, exposed by `neighbors --projection`; duplicate full edge tables are not written. They are static projections, not execution graphs. Import occurrence counts and distinct-neighbor counts are separate. Standard-library, internal, and declared distribution-name matches retain their resolution methods; unresolved names remain unresolved.

`reports/` includes snapshot differences, changed Python symbols, task dependencies, candidate evidence, operation declarations, conservative references, and preserved-work comparisons. Exact hashes, text patch comparisons, and similarity are review aids. They do not establish supersession or retirement. Campaign counts require explicit task roots. Campaign evidence reuses the versioned acceptance join over frozen owner snapshots. Partial graphs expose missing nodes, unfinished leaves, cycles, and shortest blocking paths.

Snapshot differences compare the policy-filtered primary capture with named captured overlays in `snapshots.json`; they remain bound to those IDs when checkout refs advance. Chisel does not attach a live `git diff` from the current checkout. Selecting `history` still includes the complete captured history bundle and keeps its full-history meaning; source-profile filtering does not redact selected history.

Python declarations and literal registries, AgentCTL operation TOML, and selected Rust, SQL, and Nix patterns carry locations and extraction methods. Text-pattern relationships retain candidate status. Same-file symbol-name references do not resolve shadowing. Read the report coverage document before treating a missing relationship as absent.

## Owner evidence and context

`owners/` preserves the native evidence, job observations, and selected task/campaign snapshots used by joins. The complete retained AgentCTL job list supplies lifecycle observations; up to 24 terminal verification jobs per project also receive a launch-reference-bound `job get` detail read. Captured-revision hints in the job list take priority, then up to eight exact captured-checkout workspace matches, then recency; missing hints remain unknown. The selection window, cap, and hint availability are recorded in the owner detail coverage. Workspace paths guide selection but are not copied into lifecycle records. Details are stored once in the owner snapshot. Unrelated revisions retain endpoint summaries; selected revisions retain deduplicated content manifests by hash. A detailed job appears in candidate evidence only when its endpoint receipt binds to that snapshot. A complete content match supports that association; clean matching endpoints provide a weaker association only when no content manifest is available. An incomplete or contradictory manifest never falls back to the clean-endpoint association. The candidate-evidence coverage reports compact reasons for sampled unbound details and does not represent uncaptured jobs. Lifecycle observations, execution results, and acceptance remain separate. Missing dirty state remains null in the adapter and substrate. Unchanged endpoints are not immutable-execution attestations. Wrapper success and a matching revision cannot establish acceptance.

Candidate evidence can attach a native criterion receipt when the exported authored criterion has a nonempty `revision` and a `verification` object with exact `selector` argv and `workload` fields. The native task binding and worker claim must repeat the criterion ID, version, and text; the selected check must name that ID and have a checked, eligible, successful owner observation with the same receipt reference, selector, and workload. The content comparison must cover the complete declared scope. The resulting `criterion_receipt_bound` state identifies a corroborated criterion link, not task closure or immutable execution. Missing owner command observation leaves `acceptance` unknown, including for ordinary AgentCTL lifecycle details.

Sinex execution exports use one read-only owner-ledger transaction with bounded invocation, stage, and test selections. Resource fields retain their workload, environment, units, sampling and avg/max distinctions. Under `--refresh`, selected-revision GitHub checks and statuses include provider contexts such as CircleCI when GitHub exposes them. Provider detail APIs are not inferred from a status URL.

Session context uses Polylogue's public facade with an indexed workspace filter. Defaults are 30 days, at most 200 excerpts, and 2,000,000 serialized bytes per project, configurable with `--context-days`, `--context-limit`, and `--context-bytes`. Excerpts retain session/message references, timestamps, selection reasons, origin, and truncation. Explicit selected-task session links take priority over workspace matches. The route samples the latest ten messages of selected sessions; project work notes come from the canonical Beads export, with field and line references. Task-update times are not presented as note-creation times.

## Attachments and offline browsing

The default attachment limit is 500,000,000 bytes per archive, configurable with `--attachment-bytes`. The default `auto` layout publishes both a combined portfolio archive and one self-contained archive per selected project. An oversized archive splits at file boundaries and then into numbered parts when necessary. `project` and `dataset` request only their respective layouts. No source or Git history is silently discarded to satisfy the cap. The project size in the terminal table measures unpacked contents; attachment sizes are listed separately in `attachments.json`.

`attachments.json` lists the actual selection, attachments, sizes, hashes, and reconstruction requirements. Every normal archive carries its file manifest under `attachments/`; numbered parts carry part manifests. Extract the portfolio group for all projects or one project's group for that project. Include every companion named by the chosen archives. For numbered parts, run `python3 reconstruct.py` from the extraction directory. The command verifies chunks and the reconstructed artifact.

```bash
python3 browse.py --package . snapshots --json
python3 browse.py --package . source path/to/file.py --start 20 --end 45
python3 browse.py --package . source path/to/file.py --snapshot worktree
python3 browse.py --package . reconstruct-snapshot worktree --output /new/output/path
python3 browse.py --package . tasks task-id --json
python3 browse.py --package . blockers task-id --json
python3 browse.py --package . symbols symbol_name --limit 50 --offset 0 --json
python3 browse.py --package . neighbors module.name --json
python3 browse.py --package . differences --json
python3 browse.py --package . candidate-evidence --json
python3 browse.py --package . history --path path/to/file.py
```

These reads require neither SQLite, Git, network, nor a Lynchpin installation. SQL requires an explicitly built SQLite index. Historical Git operations can use the included bundle when Git is installed. Existing packages without a catalogue remain readable as a single capture.

Publication builds a sibling candidate, verifies selected project manifests, and atomically exchanges directories. A failure retains the prior publication. Selected builds preserve unselected published project directories while the new attachment manifest contains only the requested selection. Structure caches are keyed by source and parser/policy versions; committed history uses a consolidated immutable-record cache. Complete frozen-history products are reused by refs, revision, logical date, and policy identity; `history/coverage.json` records cache hit, validation and restore time, and restored artifact bytes. Complete structure products are reused by snapshot and parser/policy identity. Delivery history separates default-branch first-parent commits and endpoint changes from all-ref activity. Subsequent selected-task builds reuse campaign scope-delta logic against the prior frozen owner snapshot.
