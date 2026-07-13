# Chisel repository reports

Chisel turns the configured repositories into reviewable snapshot bundles. It
combines concern-specific Repomix slices with Git history, Tokei attribution,
GitHub and Beads context, branch deltas, local-state audits, and a portfolio
growth report.

Run the complete configured portfolio:

```bash
just chisel
```

Or select repositories and an explicit output root:

```bash
just chisel "polylogue sinex sinnix sinity-lynchpin" /realm/tmp/chisel
```

The root `growth/README.md` links the comparative charts and machine-readable
CSV/JSON series. Each project directory also contains a project-specific
growth report with:

- default-branch additions, deletions, net growth, and gross churn;
- daily, weekly, monthly, rolling 28-day, and 30/90-day velocity;
- historical churn attributed through the current project bucket model;
- current production, test, documentation/context, and evidence composition;
- conventional commit-kind mix and activity concentration;
- Beads creation, closure, open-backlog, and lead-time reconstruction.

## LOC boundary

Chisel asks Git for the union of tracked files and non-ignored untracked files,
then applies `.ignore`, `.tokeignore`, and its safety exclusions before passing
that explicit file set to Tokei. This preserves tracked source under selectively
ignored roots while excluding local archives, dependency trees, caches, private
demo exports, and runtime state merely present in a checkout.

Git `numstat` growth and Tokei composition are deliberately separate. History
measures all tracked text at the time of each commit. Composition measures the
current maintained tree after repository reporting policy. Neither is a labor,
quality, or originality metric.

## Beads browser

When a repository has a Beads workspace, Chisel writes
`<project>-beads.html`. It is a self-contained searchable board that works by
opening the file directly in a browser. The packaged board includes the full
exported analysis context:

- issue metadata, descriptions, design, acceptance criteria, and notes;
- comments, ownership, scheduling fields, readiness, and dependencies;
- durable memory records exported by `bd export --include-memories`;
- tracker-specific fields retained as a complete JSON-backed record.

The HTML, Markdown, XML, and JSONL outputs are private analysis artifacts. They
make a Chisel package browsable without throwing away the context that makes
the tracker useful.

The public repositories expose a separate GitHub Pages projection directly
from their committed `.beads/issues.jsonl` files:

- [Polylogue](https://sinity.github.io/polylogue/main/beads/)
- [Sinex](https://sinity.github.io/sinex/beads/)
- [Sinnix](https://sinity.github.io/sinnix/beads/)
- [Lynchpin](https://sinity.github.io/sinity-lynchpin/beads/)

Those boards retain issue descriptions, designs, acceptance criteria, notes,
dependencies, and closure records, but do not ingest the interaction stream or
memory export. A pinned composite action in Polylogue assembles the shared
visual shell, copies the current branch's committed issue JSONL, and validates
local links on every Pages deployment. Chisel itself does not publish its
private packages.

## Historical caveats

The Beads trajectory uses the current exported issue set's `created_at` and
`closed_at` timestamps. It does not reconstruct reopen cycles or issues removed
by compaction. Exact tracker history requires querying the Beads Dolt history;
the generated chart is a current-set delivery trajectory, not a forensic event
ledger.
