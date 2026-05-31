"""DuckDB substrate for Lynchpin's relational data.

The substrate is a derived read store, not a warehouse: raw exports stay
external under ``/realm/data`` and remain the canonical source, re-parseable on
demand. The substrate is the materialized source of truth only for *promoted*
relational rows — evidence-graph nodes/edges, commit facts, file changes, AI
work events, symbol changes, and PR review rows. Source modules (Python +
cachew) parse raw exports into typed rows; promoters INSERT those rows into
DuckDB; readers SELECT and hydrate back to dataclasses; views replace Python
double-loops with SQL JOINs.

Plan: see /home/sinity/.claude/plans/anyway-do-pick-up-enchanted-diffie.md §4.
"""

from __future__ import annotations

from lynchpin.substrate.connection import (
    SUBSTRATE_VERSION,
    connect,
    substrate_path,
    apply_schema,
    reset_substrate,
    prune_commit_history,
)

__all__ = [
    "SUBSTRATE_VERSION",
    "connect",
    "substrate_path",
    "apply_schema",
    "reset_substrate",
    "prune_commit_history",
]
