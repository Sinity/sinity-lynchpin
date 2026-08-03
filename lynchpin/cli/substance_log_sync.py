"""Sync dose entries from the operator raw-log into the unified substance CSV.

``substance_log_unified.csv`` was documented as hand-edited; its ``raw_log``
rows were produced by a one-off parse and went stale while the raw-log kept
accumulating doses. This module makes the raw-log -> CSV step re-runnable:
it scans raw-log entries newer than the newest existing ``raw_log`` row,
extracts dose statements for substances the log already knows about, and
appends them.

Conservative by design: only timestamps strictly newer than the existing
high-water mark are considered (re-running cannot duplicate), only known
substance names are matched (from the CSV itself plus the pharmacokinetics
half-life table), and every appended row carries the raw entry text in
``note`` so an operator can audit any misparse.

Usage:
    python -m lynchpin.cli.substance_log_sync [--dry-run]
"""

from __future__ import annotations

import csv
import os
import re
import sys
from pathlib import Path

from lynchpin.core.config import get_config

# "- **2026-07-28 12:25:12** 42mg 2-FMA ..." (seconds optional)
_ENTRY_RE = re.compile(
    r"^- \*\*(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})(?::\d{2})?\*\*\s*(.+)$"
)
# "42mg 2-FMA" / "32mgg 2-FMA" (typo tolerated)   and   "2-FMA 78mg"
_DOSE_BEFORE = r"(\d{{1,3}}(?:\.\d)?)\s*mgg?\s+({names})"
_DOSE_AFTER = r"({names})\s+(\d{{1,3}}(?:\.\d)?)\s*mgg?"

_MAX_NOTE_LEN = 240


def _rawlog_path() -> Path:
    root = os.environ.get("KNOWLEDGEBASE_ROOT", "/realm/data/knowledgebase")
    return Path(root) / "logs.raw-log.md"


def _csv_path() -> Path:
    return get_config().exports_root / "health/processed/substance_log_unified.csv"


def _known_substances(rows: list[dict[str, str]]) -> dict[str, str]:
    """lowercased name -> canonical spelling, from the CSV + half-life table."""
    known: dict[str, str] = {}
    for row in rows:
        name = (row.get("substance") or "").strip()
        if name:
            known.setdefault(name.lower(), name)
    from lynchpin.analysis.substance_kinetics import HALF_LIVES_HOURS

    for name in HALF_LIVES_HOURS:
        known.setdefault(name.lower(), name.upper() if len(name) <= 4 else name)
    return known


def _extract_doses(text: str, known: dict[str, str]) -> list[tuple[str, float]]:
    names = "|".join(re.escape(n) for n in sorted(known, key=len, reverse=True))
    doses: list[tuple[str, float]] = []
    seen_spans: list[tuple[int, int]] = []
    for pattern, sub_group, mg_group in (
        (re.compile(_DOSE_BEFORE.format(names=names), re.IGNORECASE), 2, 1),
        (re.compile(_DOSE_AFTER.format(names=names), re.IGNORECASE), 1, 2),
    ):
        for m in pattern.finditer(text):
            if any(a < m.end() and m.start() < b for a, b in seen_spans):
                continue
            seen_spans.append((m.start(), m.end()))
            doses.append(
                (known[m.group(sub_group).lower()], float(m.group(mg_group)))
            )
    return doses


def sync_substance_log(dry_run: bool = False) -> int:
    """Append new raw-log dose rows to the unified CSV. Returns rows added."""
    csv_path = _csv_path()
    rawlog = _rawlog_path()
    if not csv_path.exists() or not rawlog.exists():
        print(f"missing input: {csv_path if not csv_path.exists() else rawlog}")
        return 0

    with open(csv_path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    high_water = ""
    for row in rows:
        if row.get("source") == "raw_log" and row.get("date"):
            stamp = f"{row['date']} {row.get('time') or '00:00'}"
            high_water = max(high_water, stamp)

    known = _known_substances(rows)
    existing_keys = {
        (r.get("date"), r.get("time"), (r.get("substance") or "").lower())
        for r in rows
    }

    added: list[dict[str, str]] = []
    for line in rawlog.read_text(encoding="utf-8").splitlines():
        m = _ENTRY_RE.match(line)
        if not m:
            continue
        day, clock, text = m.group(1), m.group(2), m.group(3).strip()
        if f"{day} {clock}" <= high_water:
            continue
        for substance, mg in _extract_doses(text, known):
            key = (day, clock, substance.lower())
            if key in existing_keys:
                continue
            existing_keys.add(key)
            added.append(
                {
                    "date": day,
                    "time": clock,
                    "substance": substance,
                    "amount_mg": f"{mg:g}",
                    "source": "raw_log",
                    "note": text[:_MAX_NOTE_LEN],
                }
            )

    if dry_run:
        print(f"[dry-run] Would append {len(added)} dose rows (after {high_water or 'beginning'})")
        for row in added[:10]:
            print(f"  {row['date']} {row['time']} {row['amount_mg']}mg {row['substance']}")
        return len(added)

    if added:
        merged = rows + added
        merged.sort(key=lambda r: (r.get("date") or "", r.get("time") or ""))
        with open(csv_path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(merged)
    print(f"Substance log: +{len(added)} raw-log rows -> {csv_path}")
    return len(added)


def main(argv: list[str] | None = None) -> int:
    dry_run = "--dry-run" in (argv if argv is not None else sys.argv[1:])
    sync_substance_log(dry_run=dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["sync_substance_log", "main"]
