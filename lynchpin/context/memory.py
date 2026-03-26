"""Persistent belief store for claims and themes across context refreshes.

Claims and themes produced per-run by generate_claims() and detect_themes()
are ephemeral. This module accumulates them across sessions using an
exponential moving average (alpha=0.3) for confidence updates.

Storage: artefacts/context/memory.json (written atomically)
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Sequence, Any


_MEMORY_PATH = Path("artefacts/context/memory.json")
_NORMALIZED_SPACE_RE = re.compile(r"\s+")
_NORMALIZED_PUNCT_RE = re.compile(r"[^a-z0-9]+")


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


def _normalize_statement(statement: str) -> str:
    lowered = _NORMALIZED_PUNCT_RE.sub(" ", statement.lower())
    return _NORMALIZED_SPACE_RE.sub(" ", lowered).strip()


def _match_claim(existing_claims: list[ClaimRecord], statement: str) -> ClaimRecord | None:
    normalized = _normalize_statement(statement)
    if not normalized:
        return None

    for claim in existing_claims:
        if _normalize_statement(claim.statement) == normalized:
            return claim

    prefix_matches = [
        claim
        for claim in existing_claims
        if (existing := _normalize_statement(claim.statement))
        and (existing.startswith(normalized) or normalized.startswith(existing))
    ]
    if not prefix_matches:
        return None
    return max(prefix_matches, key=lambda claim: len(_normalize_statement(claim.statement)))


def _merge_evidence_refs(existing: ClaimRecord, new_claim: Any) -> list[str]:
    return sorted({*existing.evidence_refs, *map(str, getattr(new_claim, "evidence_refs", ()))})


def _build_updated_claim(
    existing: ClaimRecord,
    new_claim: Any,
    *,
    today: str,
    alpha: float,
) -> ClaimRecord:
    new_confidence = float(new_claim.confidence)
    updated_confidence = alpha * new_confidence + (1 - alpha) * existing.confidence
    revision_entry = None
    if abs(updated_confidence - existing.confidence) > 0.05:
        revision_entry = {
            "date": today,
            "old_conf": round(existing.confidence, 3),
            "new_conf": round(updated_confidence, 3),
        }

    is_refutation = new_confidence < existing.confidence
    return ClaimRecord(
        statement=existing.statement,
        confidence=updated_confidence,
        category=getattr(new_claim, "category", existing.category),
        first_seen=existing.first_seen,
        last_seen=today,
        support_count=existing.support_count + (0 if is_refutation else 1),
        refutation_count=existing.refutation_count + (1 if is_refutation else 0),
        evidence_refs=_merge_evidence_refs(existing, new_claim),
        revisions=existing.revisions + ([revision_entry] if revision_entry else []),
    )


def _coerce_theme_month_count(theme: Any) -> int:
    return int(getattr(theme, "month_count", getattr(theme, "months_active", 1)))


def _build_updated_theme(existing: ThemeRecord, new_theme: Any) -> ThemeRecord:
    new_hours = float(getattr(new_theme, "total_hours", existing.total_hours))
    new_first_seen = str(getattr(new_theme, "first_seen", existing.first_seen))
    new_last_seen = str(getattr(new_theme, "last_seen", existing.last_seen))
    return ThemeRecord(
        name=existing.name,
        kind=existing.kind,
        total_hours=max(existing.total_hours, new_hours),
        first_seen=min(existing.first_seen or new_first_seen, new_first_seen),
        last_seen=max(existing.last_seen or new_last_seen, new_last_seen),
        trend=str(getattr(new_theme, "trend", existing.trend)),
        months_active=max(existing.months_active, _coerce_theme_month_count(new_theme)),
        peak_month=existing.peak_month or new_last_seen,
    )


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

    existing_claims = list(store.claims)
    updated_claims: list[ClaimRecord] = []
    matched_claim_ids: set[int] = set()
    for new_claim in new_claims:
        statement = str(new_claim.statement)
        existing = _match_claim(existing_claims, statement)
        if existing is None:
            updated_claims.append(
                ClaimRecord(
                    statement=statement,
                    confidence=float(new_claim.confidence),
                    category=str(new_claim.category),
                    first_seen=today,
                    last_seen=today,
                    support_count=1,
                    refutation_count=0,
                    evidence_refs=list(map(str, getattr(new_claim, "evidence_refs", ()))),
                    revisions=[],
                )
            )
            continue

        matched_claim_ids.add(id(existing))
        updated_claims.append(
            _build_updated_claim(existing, new_claim, today=today, alpha=alpha)
        )

    for existing in existing_claims:
        if id(existing) not in matched_claim_ids:
            updated_claims.append(existing)

    existing_themes = list(store.themes)
    existing_themes_index = {(t.name, t.kind): t for t in existing_themes}
    updated_themes: list[ThemeRecord] = []
    matched_theme_keys: set[tuple[str, str]] = set()
    for new_theme in new_themes:
        key = (str(new_theme.name), str(new_theme.kind))
        matched_theme_keys.add(key)
        existing = existing_themes_index.get(key)
        if existing is None:
            updated_themes.append(
                ThemeRecord(
                    name=key[0],
                    kind=key[1],
                    total_hours=float(new_theme.total_hours),
                    first_seen=str(new_theme.first_seen),
                    last_seen=str(new_theme.last_seen),
                    trend=str(new_theme.trend),
                    months_active=_coerce_theme_month_count(new_theme),
                    peak_month=str(getattr(new_theme, "peak_month", getattr(new_theme, "last_seen", ""))),
                )
            )
            continue
        updated_themes.append(_build_updated_theme(existing, new_theme))

    for existing in existing_themes:
        if (existing.name, existing.kind) not in matched_theme_keys:
            updated_themes.append(existing)

    store.claims = sorted(updated_claims, key=lambda claim: (-claim.confidence, claim.statement))
    store.themes = sorted(updated_themes, key=lambda theme: (-theme.total_hours, theme.kind, theme.name))
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


def update_memory_from_state(state: dict[str, Any], *, alpha: float = 0.3) -> MemoryStore:
    claims_payload = state.get("claims") if isinstance(state.get("claims"), dict) else {}
    themes_payload = state.get("themes") if isinstance(state.get("themes"), list) else []
    claim_objs = [
        type(
            "ClaimLike",
            (),
            {
                "statement": claim.get("statement", ""),
                "confidence": float(claim.get("confidence", 0.5)),
                "category": str(claim.get("category", "unknown")),
                "evidence_refs": tuple(claim.get("evidence_refs") or ()),
            },
        )()
        for claim in claims_payload.get("claims", [])
        if isinstance(claim, dict)
    ]
    theme_objs = [
        type(
            "ThemeLike",
            (),
            {
                "name": theme.get("name", ""),
                "kind": theme.get("kind", "project"),
                "total_hours": float(theme.get("total_hours", 0.0)),
                "trend": str(theme.get("trend", "stable")),
                "month_count": int(theme.get("month_count", theme.get("months_active", 1))),
                "first_seen": str(theme.get("first_seen", "")),
                "last_seen": str(theme.get("last_seen", "")),
                "peak_month": str(theme.get("last_seen", "")),
            },
        )()
        for theme in themes_payload
        if isinstance(theme, dict)
    ]
    return update_memory(claim_objs, theme_objs, alpha=alpha)


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    if cmd == "update":
        from lynchpin.context.packet_builders import build_current_state

        state = build_current_state(days=90, tier="full")
        store = update_memory_from_state(state)
        print(f"Updated: {len(store.claims)} claims, {len(store.themes)} themes")
    elif cmd == "show":
        store = load_memory()
        print(f"Memory: {len(store.claims)} claims, {len(store.themes)} themes, updated={store.last_updated}")
        for c in sorted(store.claims, key=lambda x: -x.confidence)[:10]:
            print(f"  [{c.confidence:.2f}] {c.statement}")
    else:
        print(f"Unknown command: {cmd}. Use: update | show")
        sys.exit(1)
