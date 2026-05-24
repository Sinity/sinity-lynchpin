"""Web and Spotify source-node builders for the evidence graph."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from typing import Any

from ..core.evidence import EvidenceCaveat, EvidenceProvenance
from ..core.evidence_graph import EvidenceNode
from ..core.primitives import logical_date
from .evidence_projects import include_project

def iter_streams(*args: Any, **kwargs: Any) -> Any:
    from ..sources.spotify import iter_streams as impl

    return impl(*args, **kwargs)


def daily_browsing(*args: Any, **kwargs: Any) -> Any:
    from ..sources.web import daily_browsing as impl

    return impl(*args, **kwargs)


def add_web(
    nodes: list[EvidenceNode], *, start: date, end: date, selected: set[str]
) -> None:
    from ..materialization import materialized_window_overlaps

    if not materialized_window_overlaps("webhistory", start=start, end=end):
        return
    days = daily_browsing(start=start, end=end)
    for day in days:
        if day.visit_count == 0:
            continue
        top_domains = [(d, round(p, 3)) for d, p in day.top_domains[:5]]
        domain_names = [d for d, _ in top_domains]
        project = _domain_project(domain_names[0]) if domain_names else None
        if not include_project(project, selected):
            project = None
        nodes.append(
            EvidenceNode(
                id=f"web:{day.date.isoformat()}",
                kind="web_domain_day",
                source="web",
                date=day.date,
                project=project,
                summary=f"{day.visit_count} visits, {day.unique_domains} domains, top: {', '.join(domain_names[:3])}",
                payload={
                    "visit_count": day.visit_count,
                    "unique_domains": day.unique_domains,
                    "top_domains": top_domains,
                    "top_titles": list(day.top_titles[:3]),
                },
                provenance=EvidenceProvenance("web", "materialized"),
                caveats=(
                    EvidenceCaveat(
                        "web",
                        "partial",
                        "Web domain data is domain-level; individual page content is not inspected.",
                    ),
                ),
            )
        )


def add_spotify(
    nodes: list[EvidenceNode],
    *,
    start: date,
    end: date,
    selected: set[str],
) -> None:
    """Add listening-session evidence nodes from Spotify streaming history."""
    from ..materialization import materialized_window_overlaps

    if not materialized_window_overlaps("spotify", start=start, end=end):
        return
    streams = list(iter_streams())
    if not streams:
        return
    by_day: dict[date, list[Any]] = defaultdict(list)
    for s in streams:
        end_time = getattr(s, "end_time", None)
        if end_time is None:
            continue
        d = logical_date(end_time)
        if start <= d < end:
            by_day[d].append(s)

    for d, day_streams in by_day.items():
        top_artists = Counter(
            s.artist for s in day_streams if hasattr(s, "artist")
        ).most_common(5)
        top_tracks = Counter(
            s.track for s in day_streams if hasattr(s, "track")
        ).most_common(5)
        minutes = sum((getattr(s, "ms_played", 0) or 0) / 60_000 for s in day_streams)
        nodes.append(
            EvidenceNode(
                id=f"spotify:listening:{d.isoformat()}",
                kind="listening_session",
                source="spotify",
                date=d,
                project=None,
                summary=(
                    f"{len(day_streams)} tracks, {minutes:.0f}min - "
                    f"top: {', '.join(a for a, _ in top_artists[:3])}"
                ),
                payload={
                    "track_count": len(day_streams),
                    "minutes": round(minutes, 1),
                    "top_artists": [(a, c) for a, c in top_artists],
                    "top_tracks": [(t, c) for t, c in top_tracks],
                },
                provenance=EvidenceProvenance("spotify", "materialized"),
            )
        )


def _domain_project(domain: str) -> str | None:
    mapping = {
        "github.com": None,
        "gitlab.com": None,
        "chatgpt.com": None,
        "claude.ai": None,
        "aistudio.google.com": None,
        "lesswrong.com": None,
        "stackoverflow.com": None,
        "reddit.com": None,
        "youtube.com": None,
        "docs.rs": None,
        "pypi.org": None,
        "crates.io": None,
        "nixos.org": None,
    }
    return mapping.get(domain)
