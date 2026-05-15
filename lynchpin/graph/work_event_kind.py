"""Lynchpin re-classifier overlay for Polylogue work-event kinds.

Polylogue produces a ``kind`` per ``session_work_event`` (debugging /
implementation / testing / research / planning / review / conversation /
data_analysis / refactoring). The classifier is heuristic and uneven —
reliable for some surfaces, weak for others. Lynchpin should not inherit that
uncertainty silently.

This module produces an independent rule-based label using the work-event
features Lynchpin can trust (file-path extensions, tools_used patterns,
duration buckets), then compares it against Polylogue's. When the two agree,
combined confidence is boosted; when they disagree, both are surfaced and the
disagreement becomes a caveat.

Three confidence tiers (`high`/`medium`/`low`) propagate downstream — see
`lynchpin/graph/work_correlation.py` (Arc B) and `context_pack.py`
(Arc A.4) for consumers.
"""

from __future__ import annotations

from collections import Counter
from pathlib import PurePosixPath
from typing import Iterable

from ..core.work_event_kind import ConfidenceTier, KindSource, WorkEventKindLabel


# ── Feature extractors ───────────────────────────────────────────────────────


def _path_signal(file_paths: Iterable[str]) -> tuple[Counter[str], int]:
    """Score kinds based on file-extension and directory-name distribution.

    Returns (kind_score, total_paths). Higher scores are stronger signals.
    """
    scores: Counter[str] = Counter()
    paths = [str(p) for p in file_paths if p]
    for raw in paths:
        path = PurePosixPath(raw)
        parts = {part.lower() for part in path.parts}
        suffix = path.suffix.lower()
        name = path.name.lower()

        if "tests" in parts or "test" in parts or name.startswith("test_") or name.endswith("_test.py") or name.endswith(".test.ts") or name.endswith(".test.tsx"):
            scores["testing"] += 2
        if "docs" in parts or "doc" in parts or suffix in {".md", ".rst", ".txt"}:
            scores["research"] += 1
            scores["conversation"] += 1
        if name in {"cargo.toml", "cargo.lock", "pyproject.toml", "package.json", "package-lock.json", "flake.nix", "flake.lock", "go.mod", "go.sum"}:
            scores["dependency_management"] += 3
        if suffix in {".py", ".rs", ".ts", ".tsx", ".go", ".js", ".jsx", ".rb", ".java", ".cpp", ".c", ".h", ".hpp"}:
            # Code paths are weak signal alone; pair with tools_used / kind hints below.
            scores["implementation"] += 1
        if suffix in {".yml", ".yaml", ".toml", ".json", ".nix"} and name not in {"cargo.toml", "pyproject.toml"}:
            scores["dependency_management"] += 1
        if suffix in {".sql", ".prisma"} or "schema" in parts or "migrations" in parts:
            scores["data_analysis"] += 1
            scores["implementation"] += 1
    return scores, len(paths)


def _tools_signal(tools_used: Iterable[str]) -> Counter[str]:
    """Score kinds based on which tools the AI used during the event."""
    scores: Counter[str] = Counter()
    tools = [str(t) for t in tools_used if t]
    counts = Counter(tools)
    if counts.get("Bash", 0) >= 2:
        # Bash-heavy with retries → debugging signature.
        scores["debugging"] += 2
    if counts.get("Edit", 0) + counts.get("Write", 0) >= 2:
        scores["implementation"] += 2
    if counts and counts.get("Read", 0) == sum(counts.values()):
        # Pure read sessions → research/review
        scores["research"] += 2
        scores["review"] += 1
    if "Grep" in counts or "Glob" in counts:
        scores["research"] += 1
    return scores


def _duration_signal(duration_ms: int) -> Counter[str]:
    """Short events skew toward conversation; very long events toward implementation/research."""
    scores: Counter[str] = Counter()
    minutes = duration_ms / 60_000
    if minutes < 1.0:
        scores["conversation"] += 1
    elif minutes >= 30.0:
        scores["implementation"] += 1
        scores["research"] += 1
    return scores


# ── Public classifier ────────────────────────────────────────────────────────


def overlay_label(
    *,
    polylogue_kind: str | None,
    polylogue_confidence: float,
    file_paths: Iterable[str],
    tools_used: Iterable[str],
    duration_ms: int,
) -> WorkEventKindLabel:
    """Produce a re-classified label for a single work-event.

    The overlay computes its own kind/confidence from features Lynchpin can
    independently verify. If it converges with Polylogue's classification, we
    return `source="agreement"` with combined confidence. If they diverge, the
    stronger signal wins but both labels stay visible.

    Tier mapping is conservative:
      - `high`   ≥ 0.8 (agreement OR strong overlay features alone)
      - `medium` 0.5 ≤ c < 0.8
      - `low`    < 0.5 OR single-feature only
    """
    path_scores, path_count = _path_signal(file_paths)
    tool_scores = _tools_signal(tools_used)
    duration_scores = _duration_signal(duration_ms)

    combined: Counter[str] = Counter()
    combined.update(path_scores)
    combined.update(tool_scores)
    combined.update(duration_scores)

    overlay_kind: str | None = None
    overlay_conf = 0.0
    feature_count = 0
    if combined:
        overlay_kind, top_score = combined.most_common(1)[0]
        feature_count = (1 if path_count else 0) + (1 if tool_scores else 0) + (1 if duration_scores else 0)
        # Confidence rises with feature dimensionality and total score.
        # Caps at 0.95 so we never claim certainty.
        raw = top_score / 4.0 + 0.15 * feature_count
        overlay_conf = min(0.95, max(0.0, raw))
        if feature_count <= 1:
            overlay_conf = min(overlay_conf, 0.45)

    polylogue_kind_norm = polylogue_kind or None
    if polylogue_kind_norm and overlay_kind and polylogue_kind_norm == overlay_kind:
        kind = overlay_kind
        confidence = min(0.95, (polylogue_confidence + overlay_conf) / 1.5)
        source: KindSource = "agreement"
    elif overlay_kind and overlay_conf >= polylogue_confidence:
        kind = overlay_kind
        confidence = overlay_conf
        source = "lynchpin_overlay" if polylogue_kind_norm is None else "disagreement"
    elif polylogue_kind_norm:
        kind = polylogue_kind_norm
        confidence = polylogue_confidence
        source = "polylogue"
    elif overlay_kind:
        kind = overlay_kind
        confidence = overlay_conf
        source = "lynchpin_overlay"
    else:
        kind = "unknown"
        confidence = 0.0
        source = "polylogue"

    tier: ConfidenceTier
    if confidence >= 0.8 and (source == "agreement" or feature_count >= 2):
        tier = "high"
    elif confidence >= 0.5:
        tier = "medium"
    else:
        tier = "low"

    return WorkEventKindLabel(
        kind=kind,
        confidence=confidence,
        source=source,
        tier=tier,
        polylogue_kind=polylogue_kind_norm,
        polylogue_confidence=polylogue_confidence,
        overlay_kind=overlay_kind,
        overlay_confidence=overlay_conf,
        features={
            "path_scores": dict(path_scores),
            "tool_scores": dict(tool_scores),
            "duration_scores": dict(duration_scores),
            "feature_count": feature_count,
            "path_count": path_count,
        },
    )


_TIER_WEIGHTS: dict[ConfidenceTier, float] = {
    "high": 1.0,
    "medium": 0.6,
    "low": 0.25,
}


def tier_weight(tier: ConfidenceTier) -> float:
    """Per-Arc-K.3 weighting used by Arc B's `ai_kind_weighted` aggregator."""
    return _TIER_WEIGHTS[tier]


__all__ = [
    "overlay_label",
    "tier_weight",
]
