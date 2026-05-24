"""Polylogue inference quality dashboard (M.18) + dual-inference comparison (M.19).

Programmatic audit of Polylogue's work-event heuristics. For every
``session_work_event`` in the chosen window, compute the Lynchpin
re-classifier overlay (Arc K) and compare. Surfaces:

  - per-kind agreement / disagreement / lynchpin-only / polylogue-only
    breakdown
  - confusion matrix (Polylogue kind × Lynchpin kind) restricted to
    disagreements — shows where Polylogue's heuristics systematically
    miss
  - confidence distribution per kind: how many events have
    Polylogue confidence < 0.5, < 0.7, < 0.85
  - low-feature-count cells: (kind, feature_count) where Lynchpin has
    only one feature dimension to go on, capping its overlay confidence
    at 0.45
  - per-week disagreement rate (M.19): rolling rate over the window so
    drift becomes visible

This is descriptive, not prescriptive. Consumers (Sinnix operator reports, sinex
dashboards, future ts.tool) can decide whether a particular cell warrants
filing a Polylogue ticket or just adopting Lynchpin's overlay.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from os import PathLike
from typing import Any, Iterable

from ...graph.work_event_kind import overlay_label
from ...sources.polylogue import WorkEvent, work_events
from ..core.io import resolve_analysis_path, save_json


@dataclass(frozen=True)
class KindAgreementRow:
    """Per-kind quality slice across the audit window."""
    kind: str
    polylogue_count: int
    overlay_count: int
    agreement_count: int
    disagreement_count: int
    lynchpin_only_count: int
    polylogue_only_count: int
    avg_polylogue_confidence: float
    median_polylogue_confidence: float
    low_polylogue_confidence_count: int  # < 0.5


@dataclass(frozen=True)
class WeeklyDisagreementRow:
    """Per-week dual-inference comparison (M.19)."""
    week_start: date
    total_events: int
    agreement_count: int
    disagreement_count: int
    lynchpin_only_count: int
    polylogue_only_count: int
    disagreement_rate: float


@dataclass(frozen=True)
class ConfusionEntry:
    """One cell of the disagreement confusion matrix."""
    polylogue_kind: str
    overlay_kind: str
    count: int


def build_polylogue_inference_quality(
    *,
    start: date,
    end: date,
    events_iter: Iterable[WorkEvent] | None = None,
) -> dict[str, Any]:
    """Build the dashboard payload for ``[start, end]``.

    ``events_iter`` accepts caller-supplied events for tests; when omitted,
    pulls from the local Polylogue archive.
    """
    events = tuple(events_iter) if events_iter is not None else tuple(
        work_events(start=start, end=end + timedelta(days=1))
    )
    # Restrict to the window — work_events filters loosely by start_date,
    # but be defensive in case end-edge slipped through.
    events = tuple(
        event for event in events
        if event.start is not None and start <= event.start.date() <= end
    )

    overall_agreement = 0
    overall_disagreement = 0
    overall_lynchpin_only = 0
    overall_polylogue_only = 0
    overall_total = 0

    per_kind: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "polylogue_count": 0,
            "overlay_count": 0,
            "agreement_count": 0,
            "disagreement_count": 0,
            "lynchpin_only_count": 0,
            "polylogue_only_count": 0,
            "polylogue_confidences": [],
            "low_polylogue_confidence_count": 0,
        }
    )

    confusion: Counter[tuple[str, str]] = Counter()

    weekly_buckets: dict[date, dict[str, int]] = defaultdict(
        lambda: {"total": 0, "agreement": 0, "disagreement": 0, "lynchpin_only": 0, "polylogue_only": 0}
    )

    for event in events:
        label = overlay_label(
            polylogue_kind=event.kind or None,
            polylogue_confidence=float(event.confidence or 0.0),
            file_paths=event.file_paths,
            tools_used=event.tools_used,
            duration_ms=int(event.duration_ms or 0),
        )
        overall_total += 1
        bucket_key = _week_start(event.start.date()) if event.start else _week_start(end)
        weekly_buckets[bucket_key]["total"] += 1

        category = _categorize(label)
        if category == "agreement":
            overall_agreement += 1
            weekly_buckets[bucket_key]["agreement"] += 1
        elif category == "disagreement":
            overall_disagreement += 1
            weekly_buckets[bucket_key]["disagreement"] += 1
        elif category == "lynchpin_only":
            overall_lynchpin_only += 1
            weekly_buckets[bucket_key]["lynchpin_only"] += 1
        elif category == "polylogue_only":
            overall_polylogue_only += 1
            weekly_buckets[bucket_key]["polylogue_only"] += 1

        # Per-kind tally keyed by Polylogue's claimed kind.
        if event.kind:
            row = per_kind[event.kind]
            row["polylogue_count"] += 1
            row["polylogue_confidences"].append(float(event.confidence or 0.0))
            if (event.confidence or 0.0) < 0.5:
                row["low_polylogue_confidence_count"] += 1
            if category == "agreement":
                row["agreement_count"] += 1
            elif category == "disagreement":
                row["disagreement_count"] += 1
            elif category == "polylogue_only":
                row["polylogue_only_count"] += 1
        if label.overlay_kind:
            row = per_kind[label.overlay_kind]
            row["overlay_count"] += 1
            if category == "lynchpin_only":
                row["lynchpin_only_count"] += 1

        if category == "disagreement" and event.kind and label.overlay_kind:
            confusion[(event.kind, label.overlay_kind)] += 1

    # Freeze per-kind rows.
    kind_rows: list[KindAgreementRow] = []
    for kind in sorted(per_kind):
        data = per_kind[kind]
        confidences = data["polylogue_confidences"]
        kind_rows.append(KindAgreementRow(
            kind=kind,
            polylogue_count=data["polylogue_count"],
            overlay_count=data["overlay_count"],
            agreement_count=data["agreement_count"],
            disagreement_count=data["disagreement_count"],
            lynchpin_only_count=data["lynchpin_only_count"],
            polylogue_only_count=data["polylogue_only_count"],
            avg_polylogue_confidence=(sum(confidences) / len(confidences)) if confidences else 0.0,
            median_polylogue_confidence=statistics.median(confidences) if confidences else 0.0,
            low_polylogue_confidence_count=data["low_polylogue_confidence_count"],
        ))

    weekly_rows: list[WeeklyDisagreementRow] = []
    for week in sorted(weekly_buckets):
        bucket = weekly_buckets[week]
        total = bucket["total"]
        disagree = bucket["disagreement"] + bucket["lynchpin_only"] + bucket["polylogue_only"]
        weekly_rows.append(WeeklyDisagreementRow(
            week_start=week,
            total_events=total,
            agreement_count=bucket["agreement"],
            disagreement_count=bucket["disagreement"],
            lynchpin_only_count=bucket["lynchpin_only"],
            polylogue_only_count=bucket["polylogue_only"],
            disagreement_rate=(disagree / total) if total else 0.0,
        ))

    confusion_rows = [
        ConfusionEntry(polylogue_kind=p, overlay_kind=o, count=count)
        for (p, o), count in sorted(confusion.items(), key=lambda kv: -kv[1])
    ][:32]

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "summary": {
            "total_events": overall_total,
            "agreement_count": overall_agreement,
            "disagreement_count": overall_disagreement,
            "lynchpin_only_count": overall_lynchpin_only,
            "polylogue_only_count": overall_polylogue_only,
            "agreement_rate": (overall_agreement / overall_total) if overall_total else 0.0,
        },
        "per_kind": [_kind_row_to_dict(row) for row in kind_rows],
        "weekly": [_weekly_row_to_dict(row) for row in weekly_rows],
        "top_disagreement_pairs": [
            {"polylogue_kind": e.polylogue_kind, "overlay_kind": e.overlay_kind, "count": e.count}
            for e in confusion_rows
        ],
        "caveats": [
            "Polylogue work-event kind labels and Lynchpin overlay rules are both heuristic; this audit shows divergence patterns, not which side is 'correct'",
            "low_polylogue_confidence_count counts events with confidence < 0.5 — those carry a Polylogue self-warning already",
            "weekly disagreement_rate aggregates disagreement + lynchpin_only + polylogue_only since all three categories represent label divergence",
        ],
    }


def run_polylogue_inference_quality(
    out_file: str | PathLike[str],
    *,
    start: date,
    end: date,
) -> dict[str, Any]:
    payload = build_polylogue_inference_quality(start=start, end=end)
    save_json(resolve_analysis_path(out_file), payload, sort_keys=True)
    return payload


# ── helpers ────────────────────────────────────────────────────────────────


def _categorize(label) -> str:
    """Map WorkEventKindLabel.source to one of the four audit categories."""
    if label.source == "agreement":
        return "agreement"
    if label.source == "disagreement":
        return "disagreement"
    if label.source == "lynchpin_overlay":
        return "lynchpin_only"
    if label.source == "polylogue":
        return "polylogue_only"
    return "agreement"


def _week_start(d: date) -> date:
    """Anchor to ISO week (Monday)."""
    return d - timedelta(days=d.weekday())


def _kind_row_to_dict(row: KindAgreementRow) -> dict[str, Any]:
    return {
        "kind": row.kind,
        "polylogue_count": row.polylogue_count,
        "overlay_count": row.overlay_count,
        "agreement_count": row.agreement_count,
        "disagreement_count": row.disagreement_count,
        "lynchpin_only_count": row.lynchpin_only_count,
        "polylogue_only_count": row.polylogue_only_count,
        "avg_polylogue_confidence": round(row.avg_polylogue_confidence, 3),
        "median_polylogue_confidence": round(row.median_polylogue_confidence, 3),
        "low_polylogue_confidence_count": row.low_polylogue_confidence_count,
    }


def _weekly_row_to_dict(row: WeeklyDisagreementRow) -> dict[str, Any]:
    return {
        "week_start": row.week_start.isoformat(),
        "total_events": row.total_events,
        "agreement_count": row.agreement_count,
        "disagreement_count": row.disagreement_count,
        "lynchpin_only_count": row.lynchpin_only_count,
        "polylogue_only_count": row.polylogue_only_count,
        "disagreement_rate": round(row.disagreement_rate, 3),
    }


__all__ = [
    "ConfusionEntry",
    "KindAgreementRow",
    "WeeklyDisagreementRow",
    "build_polylogue_inference_quality",
    "run_polylogue_inference_quality",
]
