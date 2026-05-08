"""DuckDB substrate for Lynchpin's relational data.

The substrate is the source of truth for evidence-graph nodes/edges, commit
facts, file changes, AI work events, symbol changes, and PR review rows.
Source modules (Python + cachew) parse raw exports into typed rows; promoters
INSERT those rows into DuckDB; readers SELECT and hydrate back to dataclasses;
views replace Python double-loops with SQL JOINs.

Plan: see /home/sinity/.claude/plans/anyway-do-pick-up-enchanted-diffie.md §4.
"""

from __future__ import annotations

from lynchpin.duck.connection import (
    SUBSTRATE_VERSION,
    connect,
    substrate_path,
    apply_schema,
    reset_substrate,
)

__all__ = [
    "SUBSTRATE_VERSION",
    "connect",
    "substrate_path",
    "apply_schema",
    "reset_substrate",
]
