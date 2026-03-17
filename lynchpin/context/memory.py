"""Persistent belief store for claims and themes across trajectory runs.

Claims and themes produced per-run by generate_claims() and detect_themes()
are ephemeral. This module accumulates them across sessions using an
exponential moving average (alpha=0.3) for confidence updates.

Storage: artefacts/context/memory.json (written atomically)
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Sequence, Any


_MEMORY_PATH = Path("artefacts/context/memory.json")


@dataclass
class ClaimRecord:
    statement: str
    confidence: float
    category: str
    first_seen: str       # ISO date
    last_seen: str        # ISO date
    support_count: int = 1
    refutation_count: int = 0
    evidence_refs: list[str] = field(default_factory=list)
    revisions: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ThemeRecord:
    name: str
    kind: str              # "project" | "topic"
    total_hours: float
    first_seen: str        # ISO date
    last_seen: str         # ISO date
    trend: str             # "rising" | "stable" | "declining"
    months_active: int
    peak_month: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MemoryStore:
    claims: list[ClaimRecord] = field(default_factory=list)
    themes: list[ThemeRecord] = field(default_factory=list)
    last_updated: str = ""
    version: int = 1

    def to_dict(self) -> dict:
        return {
            "claims": [c.to_dict() for c in self.claims],
            "themes": [t.to_dict() for t in self.themes],
            "last_updated": self.last_updated,
            "version": self.version,
        }


def load_memory() -> MemoryStore:
    """Load memory from disk; return empty MemoryStore if file missing or invalid."""
    if not _MEMORY_PATH.exists():
        return MemoryStore()

    try:
        with open(_MEMORY_PATH, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError):
        return MemoryStore()

    # Deserialize claims
    claims: list[ClaimRecord] = []
    for c in data.get("claims", []):
        claims.append(ClaimRecord(
            statement=c.get("statement", ""),
            confidence=float(c.get("confidence", 0.5)),
            category=c.get("category", "unknown"),
            first_seen=c.get("first_seen", ""),
            last_seen=c.get("last_seen", ""),
            support_count=int(c.get("support_count", 1)),
            refutation_count=int(c.get("refutation_count", 0)),
            evidence_refs=c.get("evidence_refs", []),
            revisions=c.get("revisions", []),
        ))

    # Deserialize themes
    themes: list[ThemeRecord] = []
    for t in data.get("themes", []):
        themes.append(ThemeRecord(
            name=t.get("name", ""),
            kind=t.get("kind", "project"),
            total_hours=float(t.get("total_hours", 0.0)),
            first_seen=t.get("first_seen", ""),
            last_seen=t.get("last_seen", ""),
            trend=t.get("trend", "stable"),
            months_active=int(t.get("months_active", 1)),
            peak_month=t.get("peak_month", ""),
        ))

    return MemoryStore(
        claims=claims,
        themes=themes,
        last_updated=data.get("last_updated", ""),
        version=int(data.get("version", 1)),
    )


def save_memory(store: MemoryStore) -> None:
    """Atomically write memory to disk."""
    _MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = _MEMORY_PATH.with_suffix(".tmp")

    with open(tmp_path, "w") as f:
        json.dump(store.to_dict(), f, indent=2)

    os.replace(tmp_path, _MEMORY_PATH)


def update_memory(
    new_claims: Sequence[Any],
    new_themes: Sequence[Any],
    *,
    alpha: float = 0.3,
) -> MemoryStore:
    """Update memory with new claims and themes using exponential moving average.

    Args:
        new_claims: List of Claim objects with .statement, .confidence, .category, .evidence_refs
        new_themes: List of Theme objects with .name, .kind, .total_hours, .trend, .month_count, .first_seen, .last_seen
        alpha: Confidence smoothing factor (default 0.3)

    Returns:
        Updated MemoryStore
    """
    store = load_memory()
    today = datetime.now(timezone.utc).date().isoformat()

    # Build index of existing claims by statement
    existing_claims_index: dict[str, ClaimRecord] = {c.statement: c for c in store.claims}

    # Process new claims
    updated_claims: list[ClaimRecord] = []
    for new_claim in new_claims:
        statement = new_claim.statement
        new_confidence = new_claim.confidence

        if statement in existing_claims_index:
            existing = existing_claims_index[statement]
            # Compute EMA
            updated_confidence = alpha * new_confidence + (1 - alpha) * existing.confidence

            # Track revision if delta > 0.05
            revision_entry = {}
            if abs(updated_confidence - existing.confidence) > 0.05:
                revision_entry = {
                    "date": today,
                    "old": round(existing.confidence, 3),
                    "new": round(updated_confidence, 3),
                }

            # Merge evidence_refs
            merged_refs = list(set(existing.evidence_refs) | set(new_claim.evidence_refs))

            updated_record = ClaimRecord(
                statement=statement,
                confidence=updated_confidence,
                category=new_claim.category,
                first_seen=existing.first_seen,
                last_seen=today,
                support_count=existing.support_count + 1,
                refutation_count=existing.refutation_count,
                evidence_refs=merged_refs,
                revisions=existing.revisions + ([revision_entry] if revision_entry else []),
            )
            updated_claims.append(updated_record)
        else:
            # New claim
            new_record = ClaimRecord(
                statement=statement,
                confidence=new_confidence,
                category=new_claim.category,
                first_seen=today,
                last_seen=today,
                support_count=1,
                refutation_count=0,
                evidence_refs=list(new_claim.evidence_refs),
                revisions=[],
            )
            updated_claims.append(new_record)

    # Build index of existing themes by (name, kind)
    existing_themes_index: dict[tuple[str, str], ThemeRecord] = {
        (t.name, t.kind): t for t in store.themes
    }

    # Process new themes
    updated_themes: list[ThemeRecord] = []
    for new_theme in new_themes:
        key = (new_theme.name, new_theme.kind)

        if key in existing_themes_index:
            existing = existing_themes_index[key]
            updated_record = ThemeRecord(
                name=new_theme.name,
                kind=new_theme.kind,
                total_hours=new_theme.total_hours,
                first_seen=existing.first_seen,
                last_seen=today,
                trend=new_theme.trend,
                months_active=new_theme.month_count,
                peak_month=new_theme.first_seen,  # Could be enhanced to track actual peak
            )
            updated_themes.append(updated_record)
        else:
            # New theme
            new_record = ThemeRecord(
                name=new_theme.name,
                kind=new_theme.kind,
                total_hours=new_theme.total_hours,
                first_seen=new_theme.first_seen,
                last_seen=new_theme.last_seen,
                trend=new_theme.trend,
                months_active=new_theme.month_count,
                peak_month=new_theme.first_seen,
            )
            updated_themes.append(new_record)

    # Update store
    store.claims = updated_claims
    store.themes = updated_themes
    store.last_updated = datetime.now(timezone.utc).isoformat()

    save_memory(store)
    return store


def build_memory_packet(store: MemoryStore, *, top_n: int = 10) -> list[dict]:
    """Build a memory packet with top-N claims by confidence.

    Args:
        store: MemoryStore to extract from
        top_n: Number of top claims to include

    Returns:
        List of dict representations of top claims
    """
    today = date.today()

    top_claims = sorted(store.claims, key=lambda c: -c.confidence)[:top_n]
    return [
        {
            "statement": c.statement,
            "confidence": round(c.confidence, 3),
            "category": c.category,
            "age_days": (today - date.fromisoformat(c.first_seen)).days,
            "support_count": c.support_count,
        }
        for c in top_claims
    ]


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    if cmd == "update":
        from lynchpin.trajectory.day import summarize_days
        from lynchpin.trajectory.week import summarize_weeks
        from lynchpin.trajectory.month import summarize_months as summarize_trajectory_months
        from lynchpin.context.claims import generate_claims
        from lynchpin.context.themes import detect_themes
        from lynchpin.trajectory import signal as trajectory_signal
        from lynchpin.trajectory import chains as trajectory_chains

        # Load trajectory data
        window_start, window_end = trajectory_signal.resolve_window(end=None, days=90)
        signals = trajectory_signal.load_signals(start=window_start, end=window_end, days=90)
        chains = trajectory_chains.build_chains(signals)
        days = summarize_days(
            signals=signals,
            chains=chains,
            start=window_start,
            end=window_end,
            days=90,
        )
        weeks = summarize_weeks(days)
        months = summarize_trajectory_months(days, signals=signals)

        # Generate claims and themes
        claims = generate_claims(months, weeks, days)
        themes = detect_themes(months, weeks)

        # Update memory
        store = update_memory(claims, themes)
        print(f"Updated: {len(store.claims)} claims, {len(store.themes)} themes")
    elif cmd == "show":
        store = load_memory()
        print(f"Memory: {len(store.claims)} claims, {len(store.themes)} themes, updated={store.last_updated}")
        for c in sorted(store.claims, key=lambda x: -x.confidence)[:10]:
            print(f"  [{c.confidence:.2f}] {c.statement}")
    else:
        print(f"Unknown command: {cmd}. Use: update | show")
        sys.exit(1)
