# Chisel project packages

Chisel builds evidence packages for attaching to ChatGPT. The package is a
navigation and measurement aid. ChatGPT is expected to inspect the project
evidence and do the analysis; Chisel does not generate a project briefing,
recommendations, or a quality score.

Run the configured portfolio:

```bash
just chisel
```

Or select repositories and an explicit output root:

```bash
just chisel "polylogue sinex sinnix sinity-lynchpin" /realm/tmp/chisel
```

Each project package has a `START_HERE.md` with its capture identity, available
datasets, methods, gaps, and offline navigation commands. The portfolio root
also has `START_HERE.md`, `portfolio.json`, comparative `growth/` outputs, and
`portfolio-all.tar.gz`. Extracting the portfolio archive exposes the selected
project directories directly, alongside portfolio evidence. `portfolio.json`
binds each project to its snapshot ID and records that project captures may
have different times. Source snapshot IDs identify captured bytes and policy;
the portfolio also records each complete project manifest's SHA-256, binding
tracker and history evidence to the exact package contents.

## Captured source and representations

`source/` is the directly browsable copy of captured files. Chisel inventories
Git tracked files, non-ignored untracked files, and explicitly selected local
context files. Safety exclusions still apply; this is not a copy of every
ignored file or every private runtime directory. `inventory.jsonl` records
each discovered path, its SHA-256 and size when readable, role and reason,
slice memberships, exclusions, source kind, and inclusion state. `capture.json`
records the revision, dirty state, capture time, policy version, and snapshot
ID. The copy is checked against the recorded hashes during capture.

XML slices are generated from their recorded memberships. The compressed XML
uses the union of configured slice memberships, so its file set is the
combined slice set; compression changes representation and may omit binary
content. `representations/` records expected and represented paths and binary
files available only in `source/`. XML and compressed files are alternate
views, not substitutes for the captured source. When present, the Git bundle
retains repository history; `working-tree.tar.gz` archives the captured source
tree.

Project-local documentation, scratchpads, plans, agent instructions, demos,
and other context files retain their paths and hashes where they are captured.
Their presence is provenance, not evidence that a note is current. Chisel does
not add unrelated AI conversation archives to the package.

## Roles and source metrics

Inventory roles are `implementation`, `tests`, `tooling`, `documentation`,
`context`, `evidence`, and `unclassified`. Only included implementation,
tests, and tooling files enter maintained source LOC. Documentation and
context bytes are reported separately; scratchpad prose, fenced code examples,
fixtures, generated evidence, and unknown-role files do not become maintained
LOC. An unknown classification remains `unclassified`, never production.
Dependency lockfiles are preserved as generated evidence, outside maintained
LOC. Captured `.ignore` and `.tokeignore` rules further restrict metric inputs.

`metrics/` contains per-file and per-role CSV, JSON, and Markdown measures.
Code, comments, and blank lines are separate. The policy version and coverage
gaps are recorded. Rust inline test lines are subsets of their physical source
files, so do not add them to implementation totals. Split test files remain in
the tests role. Test-source share is not test coverage. If a parser or file
result is missing, the affected measure is unavailable with a coverage gap,
not a zero.

Historical metrics have a different meaning. `history/` covers commits
reachable from refs captured at the start and classifies historical paths
under the current role-policy version. Renames retain old and new paths and
their roles. `git numstat` additions and deletions measure changed text, not
executable LOC, effort, or semantic change. All repository text activity is
separate from maintained-code text changes. Binary change counts have unknown
line totals. Merge commit patches use a first-parent diff and are labeled as
such. Author logical dates drive daily and 30/90-day summaries. Tracked staged
and unstaged changes are recorded separately; untracked paths are not included
in those patches. These measures do not infer quality or causation.
Summing activity across refs and first-parent merge diffs can count the same
change at multiple commits; cumulative changed text is not current repository
size. Git text converters and external diff drivers are disabled for these
exports. Bundle refs must match the history index before publication.

## Structure, history, and project records

`structure/` derives file-level measurements, symbols, imports, package
membership, and dependency edges from captured source. Python and Rust symbol
extraction are supported when their parsers are available. Python imports,
Cargo workspace/package declarations, and selected static configuration path
references are represented with their extraction status. These records are
source pointers and declared/static relations, not a complete call graph;
unsupported languages or unresolved relations are coverage gaps or explicit
unsupported statuses, not inferred edges. Check `structure/coverage.json` for
scope and parser availability.

`history/` contains JSONL commit, changed-path, and ref records, daily CSV,
rolling-window and growth summaries, coverage metadata, and readable patches
for reachable commits. Commit text references remain textual references;
they are not asserted as resolved GitHub or task links. The Git bundle is the
repository-native history source.

`trackers/` contains available GitHub issue and pull-request records in their
reported states, including discussion, reviews, and inline review comments,
plus the exported Beads records and readable indexes. Missing owner exports
are reported as unavailable. Chisel does not claim that filenames or temporal
proximity establish a cross-project dependency.

`verification/` contains only evidence returned through owner-published routes
such as Lynchpin native evidence and AgentCTL job observations. Records retain
revision and dirty-state fields when supplied; applicability is tied to the
captured revision. A configured test command is not proof that tests ran.
Coverage metadata distinguishes missing evidence from passing or failing
results. GitHub Actions run records are read through GitHub's API over a bounded
90-day window (up to 1,000 records), with raw owner fields and explicit partial
or unavailable coverage. A successful workflow is not proof that every test
ran. Benchmark and coverage-report exports remain unavailable where no stable
owner export is provided. Chisel does not execute project tests or benchmarks
while building packages.

`cross-project-links.jsonl` in the portfolio contains explicit repository URL
and local dependency path matches to selected projects, with source pointers
and both source snapshot IDs. Package names alone do not create edges.

## Offline navigation

Each package includes `browse.py` and `index.sqlite3`. The helper uses only
Python's standard library; it requires no project checkout, Git, Repomix,
network access, or Lynchpin installation. SQLite mirrors the JSONL/NDJSON
evidence streams and captured source metadata. FTS5 is used when available;
search falls back to literal text scanning otherwise. JSONL and source files
remain the inspectable evidence.

```bash
python3 browse.py --package . --help
python3 browse.py --package . search 'symbol or phrase'
python3 browse.py --package . source path/to/file.py --start 20 --end 45
python3 browse.py --package . history --path path/to/file.py
python3 browse.py --package . sql 'SELECT dataset, count(*) FROM datasets GROUP BY dataset'
```

SQL accepts read-only `SELECT` or `WITH` statements. Check `START_HERE.md` and
the relevant `coverage.json` before interpreting a missing dataset or parser
result. Missing means unavailable or not exported, never zero.

## Publication and runtime

Chisel constructs outputs in a sibling candidate directory, validates each
selected project's manifest against its actual files and hashes, then
publishes by Linux `renameat2` directory exchange. A writer lock rejects
overlapping builds. A failed build or validation leaves the previously
published output visible; unsupported atomic exchange fails closed rather
than silently using a non-atomic replacement. Prior combined project archives
are retained under the output archive directory.

Stage messages identify their project. Project summaries print as complete
blocks, and the root index records stage timing including queue and Repomix
wait/run time. A small report can still require a large source or history
scan. The CLI exits unsuccessfully for missing, failed, or partial projects,
including invalid XML and archive failures. A GitHub refresh fallback is
identified in the snapshot audit. Per-project manifests list artifact sizes
and hashes; the manifest's own size is included and its own hash is omitted.
