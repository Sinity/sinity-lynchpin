"""Coverage-bounds primitives for Lynchpin data sources.

Design intent
-------------
Lynchpin data splits into two categories:

EXPORTS — one-shot GDPR/Takeout dumps that are **complete as of their export
date**.  An export ending on 2025-12-18 is not stale; it is complete through
that date.  "Stale vs. fresh" is undefined for exports.

CAPTURES — continuous telemetry (ActivityWatch, git, Atuin/terminal, web
history, machine, SVN, keylog/scribe …).  These may have internal gaps and are
expected to grow day-by-day.

This module provides ``CoverageBounds`` — a frozen typed record that answers:

  - What date range did a source actually observe?
  - Is a requested date inside that range?
  - What is the intersection of a requested analysis window with coverage?
  - Which dates in a range are absent (no coverage) vs. potentially-zero?

The last point is the **missing ≠ zero** primitive.  Downstream analyses
(operator_daily, substance_health, life_phase, …) must use ``covers()`` or
``partition_by_coverage()`` before treating absent rows as confirmed zeros.
Absent days silently coerced to 0 is the root cause of fabricated abstinence
periods and flat-physiology artefacts.

Error handling
--------------
Internal invariants raise ``ValueError`` with diagnostic messages.  Wave-3
can swap those to ``DataCoverageError`` from ``core.errors`` without changing
callers — the import is intentionally omitted here to avoid cycles.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, Literal

# ---------------------------------------------------------------------------
# Source classification
# ---------------------------------------------------------------------------

#: Source names (as used in ``available_sources()`` and ``SourceObservation``)
#: that represent **continuous captures** rather than one-shot exports.
#: Captures may have internal gaps; exports are complete-as-of their end date.
#:
#: Audit checklist: align with ``config.py::available_sources()`` keys and the
#: source module names in ``lynchpin/sources/``.  Any source key NOT listed
#: here is classified as an export.
CAPTURE_SOURCES: frozenset[str] = frozenset(
    {
        # ActivityWatch window/AFK tracking — continuous daemon
        "activitywatch",
        # ARBTT window capture daemon
        "arbtt",
        # Atuin shell history database — grows on every command
        "atuin",
        # git baseline JSONL + live subprocess — grows on every commit
        "git_baseline",
        # Browser history captures — continuous
        "webhistory",
        # Machine telemetry SQLite/JSONL — sinex daemon
        "machine",
        # SVN commit log (historical workplace) — continuous during work hours
        "svn",
        # Keystroke capture log files — scribe-tap daemon
        "keylog",
        # Asciinema terminal recordings — continuous
        "asciinema",
        # Raw log file from sinex/operator — continuous capture
        "raw_log",
        # Polylogue archive — daemon tails ~/.claude/projects/ continuously
        "polylogue",
        # Codex session archive — grows with each session
        "codex",
        # Clipboard live file — continuous capture
        "clipboard",
        # IRC raw logs — continuous when connected
        "irc",
        "irc_raw",
        # Raindrop live cursor — continuous sync
        "raindrop_live",
        # Sinnix runtime inventory — updated on each rebuild
        "sinnix_runtime_inventory",
        # Browser bookmarks — updated continuously
        "browser_bookmarks",
    }
)

SourceKind = Literal["capture", "export"]


def _classify_source(source: str) -> SourceKind:
    """Return ``"capture"`` for continuous telemetry, ``"export"`` otherwise."""
    return "capture" if source in CAPTURE_SOURCES else "export"


# ---------------------------------------------------------------------------
# CoverageBounds
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CoverageBounds:
    """Observed date coverage for one Lynchpin data source.

    Attributes
    ----------
    source:
        Source key as used in ``available_sources()`` and ``SourceObservation``.
    first:
        Earliest date for which data has been observed.  ``None`` means the
        lower bound is unknown (source unavailable or not yet observed).
    last:
        Latest date for which data has been observed.  ``None`` means no upper
        bound is known (source unavailable or not yet observed).
    kind:
        ``"capture"`` — continuous telemetry; may have internal gaps.
        ``"export"`` — one-shot complete dump; end date = export date.
    """

    source: str
    first: date | None
    last: date | None
    kind: SourceKind

    # Convenience alias kept for readability in callers that only care about
    # the binary classification without importing the Literal type.
    @property
    def is_capture(self) -> bool:
        """True if this source is continuous telemetry (not a one-shot export)."""
        return self.kind == "capture"

    def covers(self, d: date) -> bool:
        """Return True if *d* falls within observed coverage.

        A date is covered when both ``first`` and ``last`` are known and
        ``first <= d <= last``.  If either bound is unknown the result is
        ``False`` — unknown coverage cannot be assumed to include the date.
        """
        if self.first is None or self.last is None:
            return False
        return self.first <= d <= self.last

    def clamp(self, start: date, end: date) -> tuple[date, date] | None:
        """Intersect the requested *[start, end]* window with observed coverage.

        Returns the overlapping sub-range as ``(clamped_start, clamped_end)``,
        or ``None`` if the requested range is entirely outside coverage or
        coverage bounds are unknown.

        Parameters
        ----------
        start:
            Inclusive start of the requested analysis window.
        end:
            Inclusive end of the requested analysis window.

        Raises
        ------
        ValueError
            If ``start > end``.
        """
        if start > end:
            raise ValueError(f"clamp: start {start} > end {end}")
        if self.first is None or self.last is None:
            return None
        lo = max(start, self.first)
        hi = min(end, self.last)
        if lo > hi:
            return None
        return lo, hi

    def provenance(self) -> str:
        """Human-readable coverage description for narrative/context packs.

        Examples
        --------
        ``"stress: covers 2026-01-01 → 2026-03-29 (export)"``
        ``"activitywatch: covers 2022-11-15 → 2026-05-30 (capture)"``
        ``"spotify: no observed coverage (export)"``
        """
        if self.first is None and self.last is None:
            return f"{self.source}: no observed coverage ({self.kind})"
        first_s = self.first.isoformat() if self.first is not None else "unknown"
        last_s = self.last.isoformat() if self.last is not None else "unknown"
        return f"{self.source}: covers {first_s} → {last_s} ({self.kind})"


# ---------------------------------------------------------------------------
# partition_by_coverage — missing ≠ zero primitive
# ---------------------------------------------------------------------------


def partition_by_coverage(
    dates: Iterable[date],
    bounds: CoverageBounds,
) -> tuple[list[date], list[date]]:
    """Split *dates* into covered and uncovered sets.

    Parameters
    ----------
    dates:
        The full set of dates an analysis wants to query.
    bounds:
        Coverage bounds for the source being queried.

    Returns
    -------
    (in_coverage, out_of_coverage):
        *in_coverage* — dates that fall within ``[bounds.first, bounds.last]``.
        A zero value for these dates is a genuine zero (no activity recorded).
        *out_of_coverage* — dates outside observed coverage, or all dates when
        bounds are unknown.  A zero value here means **absent / not observed**,
        not "no activity".  Analyses MUST NOT treat these as confirmed zeros.

    Notes
    -----
    This is the primary defence against the missing-vs-zero bug: absent days
    silently coerced to 0 produce fabricated abstinence periods, flat-physiology
    artefacts, and spurious correlation zeros.  Partition first; compute stats
    only over *in_coverage*.
    """
    in_coverage: list[date] = []
    out_of_coverage: list[date] = []
    for d in dates:
        if bounds.covers(d):
            in_coverage.append(d)
        else:
            out_of_coverage.append(d)
    return in_coverage, out_of_coverage


# ---------------------------------------------------------------------------
# Convenience: enumerate all dates in a range
# ---------------------------------------------------------------------------


def date_range(start: date, end: date) -> list[date]:
    """Return the inclusive list of dates ``[start, end]``.

    Raises
    ------
    ValueError
        If ``start > end``.
    """
    if start > end:
        raise ValueError(f"date_range: start {start} > end {end}")
    out: list[date] = []
    current = start
    while current <= end:
        out.append(current)
        current += timedelta(days=1)
    return out


__all__ = [
    "CAPTURE_SOURCES",
    "CoverageBounds",
    "SourceKind",
    "date_range",
    "partition_by_coverage",
]
