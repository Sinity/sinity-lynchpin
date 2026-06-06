"""Web and Spotify source-node builders for the evidence graph."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ..core.evidence import EvidenceCaveat, EvidenceProvenance
from ..core.evidence_graph import EvidenceNode
from .evidence_projects import include_project


def daily_browsing(*args: Any, **kwargs: Any) -> Any:
    from ..sources.web import daily_browsing as impl

    return impl(*args, **kwargs)


def add_web(
    nodes: list[EvidenceNode],
    *,
    start: date,
    end: date,
    selected: set[str],
) -> None:
    from ..materialization import ensure_materialized

    ensure_materialized("webhistory", window=(start, end + timedelta(days=1)))
    days = daily_browsing(start=start, end=end, ensure=False)
    _add_web_days(nodes, days=days, selected=selected)


def _add_web_days(
    nodes: list[EvidenceNode],
    *,
    days: Any,
    selected: set[str],
) -> None:
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
    """Add daily listening evidence nodes from the canonical Spotify product."""
    from ..materialization import ensure_materialized
    from ..sources.personal_signals import iter_spotify_daily_signals

    ensure_materialized("spotify_daily", window=(start, end + timedelta(days=1)))
    for row in iter_spotify_daily_signals(start=start, end=end + timedelta(days=1), ensure=False):
        top_artists = list(row.top_artists[:5])
        top_tracks = list(row.top_tracks[:5])
        nodes.append(
            EvidenceNode(
                id=f"spotify:listening:{row.date.isoformat()}",
                kind="listening_session",
                source="spotify",
                date=row.date,
                project=None,
                summary=(
                    f"{row.track_count} tracks, {row.minutes_played:.0f}min - "
                    f"top: {', '.join(top_artists[:3])}"
                ),
                payload={
                    "track_count": row.track_count,
                    "minutes": round(row.minutes_played, 1),
                    "unique_artists": row.unique_artists,
                    "unique_tracks": row.unique_tracks,
                    "top_artists": top_artists,
                    "top_tracks": top_tracks,
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
