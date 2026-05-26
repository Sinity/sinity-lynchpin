"""Evidence nodes for canonical personal-history products."""

from __future__ import annotations

from datetime import date, timedelta

from ..core.evidence import EvidenceProvenance
from ..core.evidence_graph import EvidenceNode


def add_personal_products(nodes: list[EvidenceNode], *, start: date, end: date) -> None:
    _add_activity_content(nodes, start=start, end=end)
    _add_google_takeout(nodes, start=start, end=end)
    _add_bookmarks(nodes, start=start, end=end)
    _add_communications(nodes, start=start, end=end)
    _add_irc(nodes, start=start, end=end)
    _add_arbtt(nodes, start=start, end=end)


def _add_activity_content(nodes: list[EvidenceNode], *, start: date, end: date) -> None:
    if not _source_overlaps("activity_content", start=start, end=end):
        return

    from ..sources.activity_content import iter_activity_content_days

    for row in iter_activity_content_days():
        if row.date < start or row.date > end:
            continue
        top_activity = _top_bucket(row.activity_seconds)
        top_topic = _top_bucket(row.topic_seconds)
        nodes.append(
            EvidenceNode(
                id=f"activity_content:{row.date.isoformat()}",
                kind="activity_content_day",
                source="activity_content",
                date=row.date,
                project=None,
                summary=(
                    f"{row.focused_seconds / 3600:.1f} focused hours; "
                    f"{row.matched_ratio:.0%} title metadata coverage"
                    + (f"; top activity {top_activity[0]}" if top_activity else "")
                ),
                payload={
                    "focused_seconds": row.focused_seconds,
                    "matched_seconds": row.matched_seconds,
                    "gpt_matched_seconds": row.gpt_matched_seconds,
                    "matched_ratio": row.matched_ratio,
                    "gpt_matched_ratio": row.gpt_matched_ratio,
                    "top_activity": top_activity,
                    "top_topic": top_topic,
                    "source_counts": row.source_counts,
                },
                provenance=EvidenceProvenance("activity_content", "materialized"),
                caveats=(),
            )
        )


def _add_google_takeout(nodes: list[EvidenceNode], *, start: date, end: date) -> None:
    if not _source_overlaps("google_takeout", start=start, end=end):
        return

    from ..sources.google_takeout_products import iter_daily_activity

    for row in iter_daily_activity(start=start, end=end + timedelta(days=1)):
        label = row.product if row.service is None else f"{row.product}/{row.service}"
        nodes.append(
            EvidenceNode(
                id=f"google_takeout:{row.date.isoformat()}:{row.product}:{row.service or 'all'}",
                kind="google_activity_day",
                source="google_takeout",
                date=row.date,
                project=None,
                summary=f"{row.event_count} Google Takeout {label} events",
                payload={
                    "product": row.product,
                    "service": row.service,
                    "event_count": row.event_count,
                },
                provenance=EvidenceProvenance("google_takeout", "materialized"),
                caveats=(),
            )
        )


def _add_bookmarks(nodes: list[EvidenceNode], *, start: date, end: date) -> None:
    if not _source_overlaps("browser_bookmarks", start=start, end=end):
        return

    from ..sources.bookmarks import daily_bookmark_activity

    rows = daily_bookmark_activity(start=start, end=end)
    for row in rows:
        nodes.append(
            EvidenceNode(
                id=f"bookmarks:{row.date.isoformat()}",
                kind="bookmark_activity",
                source="browser_bookmarks",
                date=row.date,
                project=None,
                summary=f"{row.bookmark_count} bookmarks added across {row.domain_count} domains",
                payload={
                    "bookmark_count": row.bookmark_count,
                    "domain_count": row.domain_count,
                    "top_domain": row.top_domain,
                },
                provenance=EvidenceProvenance("browser_bookmarks", "materialized"),
                caveats=(),
            )
        )


def _add_communications(nodes: list[EvidenceNode], *, start: date, end: date) -> None:
    if not _source_overlaps("communications", start=start, end=end):
        return

    from ..sources.communications import daily_communication_activity

    rows = daily_communication_activity(start=start, end=end)
    for row in rows:
        nodes.append(
            EvidenceNode(
                id=f"communications:{row.date.isoformat()}",
                kind="communication_activity",
                source="communications",
                date=row.date,
                project=None,
                summary=f"{row.event_count} communication events across {row.conversation_count} conversations",
                payload={
                    "event_count": row.event_count,
                    "outbound_count": row.outbound_count,
                    "conversation_count": row.conversation_count,
                    "source_count": row.source_count,
                },
                provenance=EvidenceProvenance("communications", "materialized"),
                caveats=(),
            )
        )


def _add_arbtt(nodes: list[EvidenceNode], *, start: date, end: date) -> None:
    if not _source_overlaps("arbtt", start=start, end=end):
        return

    from ..sources.arbtt import daily_arbtt_activity

    rows = daily_arbtt_activity(start=start, end=end)
    for row in rows:
        nodes.append(
            EvidenceNode(
                id=f"arbtt:{row.date.isoformat()}",
                kind="arbtt_focus_activity",
                source="arbtt",
                date=row.date,
                project=None,
                summary=f"{row.active_minutes:.0f} ARBTT active minutes across {row.program_count} programs",
                payload={
                    "active_minutes": row.active_minutes,
                    "event_count": row.event_count,
                    "program_count": row.program_count,
                },
                provenance=EvidenceProvenance("arbtt", "materialized"),
                caveats=(),
            )
        )


def _add_irc(nodes: list[EvidenceNode], *, start: date, end: date) -> None:
    if not _source_overlaps("irc", start=start, end=end):
        return

    from ..sources.irc_raw import daily_irc_activity

    rows = daily_irc_activity(start=start, end=end)
    for row in rows:
        nodes.append(
            EvidenceNode(
                id=f"irc:{row.date.isoformat()}",
                kind="communication_activity",
                source="irc",
                date=row.date,
                project=None,
                summary=(
                    f"{row.total_messages} IRC messages across "
                    f"{len(row.channels)} channels "
                    f"({row.operator_messages} from operator), "
                    f"{row.unique_speakers} speakers, "
                    f"{row.session_count} sessions"
                ),
                payload={
                    "total_messages": row.total_messages,
                    "operator_messages": row.operator_messages,
                    "unique_speakers": row.unique_speakers,
                    "session_count": row.session_count,
                    "conversation_count": row.conversation_count,
                    "channels": list(row.channels),
                    "channel_breakdown": [
                        {"channel": ch, "messages": n}
                        for ch, n in row.channel_breakdown
                    ],
                },
                provenance=EvidenceProvenance("irc", "raw_weechat_logs"),
                caveats=(
                    "WeeChat raw log parsing; meta/server lines excluded from counts",
                    "total_messages includes all channel traffic; operator_messages isolates operator-authored",
                ),
            )
        )


def _source_overlaps(source: str, *, start: date, end: date) -> bool:
    from ..materialization import materialized_window_overlaps

    return materialized_window_overlaps(source, start=start, end=end)


def _top_bucket(values: dict[str, float]) -> tuple[str, float] | None:
    if not values:
        return None
    label, seconds = max(values.items(), key=lambda item: item[1])
    return label, seconds


__all__ = ["add_personal_products"]
