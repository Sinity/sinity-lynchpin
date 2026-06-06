"""Operator-authored public-text daily rollup across human-facing channels.

Aggregates every channel where the operator writes text *to other humans*:

- IRC operator messages (canonical ``sinity``-family nicks)
- Reddit comment own_text (the >-quote-stripped half — see
  ``sources.reddit.split_quoted_text``)
- Reddit post bodies
- Wykop entries / entry comments / link comments
- Facebook Messenger outbound messages (direction='outbound')
- Gmail outbound (sender address matches an operator address)

This is the surface that the cross-source analysis of 2026-05-27 showed
declined together across all platforms during 2025 while AI-assistant
sessions exploded. Having it as a single per-day stream means an
evidence-graph node, MCP tool, or narrative writer can ask
"how much did the operator write publicly today" in one call.

Construct-validity boundary:
- "operator-authored" depends on per-source detection that is imperfect.
  IRC uses the operator-nick list; if sinity used another nick on a
  channel, those would be miscounted as ambient. Messenger uses the
  display-name match; an account-name change would orphan history.
- Reddit and wykop are unambiguous because the export only contains
  rows the operator authored.
- Characters and word counts count the text as-stored; reddit's own_text
  excludes the >-blockquoted parent, which is the right baseline for
  "what the operator added to the conversation".
- "Public" here means "to a human audience". AI-chat (polylogue) is
  intentionally excluded — its growth is the substitute that this
  rollup makes visible by being absent from it.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass(frozen=True)
class OperatorPublicTextDay:
    """One day of operator-authored public-text activity.

    ``by_channel`` carries per-source counts so consumers can show the
    decomposition; the top-level ``total_chars`` / ``message_count`` /
    ``channel_count`` are convenience rollups.
    """

    date: date
    total_chars: int
    message_count: int
    channel_count: int  # number of distinct channels active this day
    by_channel: dict[str, dict[str, int]] = field(default_factory=dict)
    # by_channel[channel_name] = {"chars": N, "messages": M}


def _bump(
    bucket: dict[date, dict[str, dict[str, int]]],
    d: date,
    channel: str,
    chars: int,
) -> None:
    if chars <= 0:
        return
    bucket[d].setdefault(channel, {"chars": 0, "messages": 0})
    bucket[d][channel]["chars"] += chars
    bucket[d][channel]["messages"] += 1


def _collect_irc(bucket: dict[date, dict[str, dict[str, int]]], start: date, end: date) -> None:
    from ..sources.irc_raw import iter_messages, normalize_nick, _OPERATOR_NICKS

    for m in iter_messages():
        if not m.timestamp or m.is_meta:
            continue
        d = m.timestamp.date()
        if d < start or d > end:
            continue
        if normalize_nick(m.speaker).lower() not in _OPERATOR_NICKS:
            continue
        _bump(bucket, d, f"irc:{m.channel}", len(m.text or ""))


def _collect_reddit(bucket: dict[date, dict[str, dict[str, int]]], start: date, end: date) -> None:
    from ..sources.reddit import iter_comments, iter_posts, split_quoted_text

    for c in iter_comments():
        if not c.created:
            continue
        d = c.created.date()
        if d < start or d > end:
            continue
        own, _quotes = split_quoted_text(c.body or "")
        _bump(bucket, d, f"reddit:{c.subreddit or 'unknown'}", len(own))

    for p in iter_posts():
        if not p.created:
            continue
        d = p.created.date()
        if d < start or d > end:
            continue
        # Posts have title + body; both are operator-authored.
        chars = len(p.title or "") + len(p.body or "")
        _bump(bucket, d, f"reddit-post:{p.subreddit or 'unknown'}", chars)


def _collect_wykop(bucket: dict[date, dict[str, dict[str, int]]], start: date, end: date) -> None:
    from ..sources.exports import (
        iter_wykop_entries,
        iter_wykop_entry_comments,
        iter_wykop_link_comments,
    )

    for e in iter_wykop_entries():
        if e.created_at:
            d = e.created_at.date()
            if start <= d <= end:
                _bump(bucket, d, "wykop:entries", len(e.content or ""))
    for ec in iter_wykop_entry_comments():
        if ec.created_at:
            d = ec.created_at.date()
            if start <= d <= end:
                _bump(bucket, d, "wykop:entry_comments", len(ec.content or ""))
    for lc in iter_wykop_link_comments():
        if lc.created_at:
            d = lc.created_at.date()
            if start <= d <= end:
                _bump(bucket, d, "wykop:link_comments", len(lc.content or ""))


def _collect_messenger(bucket: dict[date, dict[str, dict[str, int]]], start: date, end: date) -> None:
    from ..sources.communications import iter_communication_events

    for e in iter_communication_events():
        if e.direction != "outbound" or not e.timestamp:
            continue
        d = e.timestamp.date()
        if start <= d <= end:
            # text_length is preferred to text_excerpt (which is truncated).
            chars = e.text_length or len(e.text_excerpt or "")
            _bump(bucket, d, f"{e.source}:out", chars)


def _collect_gmail(bucket: dict[date, dict[str, dict[str, int]]], start: date, end: date) -> None:
    from ..sources.gmail_takeout import iter_materialized_gmail_messages, _looks_outbound

    for m in iter_materialized_gmail_messages():
        if not m.timestamp or not _looks_outbound(m.sender):
            continue
        d = m.timestamp.date()
        if start <= d <= end:
            # GmailMessage has body_preview (first ~500 chars). The full body
            # isn't stored in the materialized NDJSON; the preview is an
            # underestimate of total chars but consistent across messages.
            chars = len(m.body_preview or "") + len(m.subject or "")
            _bump(bucket, d, "gmail:out", chars)


def operator_public_text_daily(
    *,
    start: date,
    end: date,
    sources: Optional[set[str]] = None,
) -> list[OperatorPublicTextDay]:
    """Build per-day operator-public-text rows.

    ``sources`` filters which collectors run. Valid values:
    ``"irc"``, ``"reddit"``, ``"wykop"``, ``"messenger"``, ``"gmail"``.
    Default = all. Rows are returned in ascending date order; days with
    zero activity across all selected sources are omitted (consistent
    with the construct-validity note in the module docstring).
    """
    collectors = {
        "irc": _collect_irc,
        "reddit": _collect_reddit,
        "wykop": _collect_wykop,
        "messenger": _collect_messenger,
        "gmail": _collect_gmail,
    }
    selected = set(sources) if sources else set(collectors)
    bucket: dict[date, dict[str, dict[str, int]]] = defaultdict(dict)
    for name, fn in collectors.items():
        if name in selected:
            fn(bucket, start, end)

    out: list[OperatorPublicTextDay] = []
    for d in sorted(bucket):
        ch_data = bucket[d]
        total_chars = sum(c["chars"] for c in ch_data.values())
        message_count = sum(c["messages"] for c in ch_data.values())
        out.append(
            OperatorPublicTextDay(
                date=d,
                total_chars=total_chars,
                message_count=message_count,
                channel_count=len(ch_data),
                by_channel=dict(ch_data),
            )
        )
    return out


@dataclass(frozen=True)
class SourceCoverageStatus:
    """Per-source coverage envelope for a query window.

    Distinguishes "operator wrote nothing through this source in the
    window" from "this source's data doesn't cover the window". A zero
    contribution under ``available`` coverage is real silence; a zero
    contribution under ``out_of_range`` is missing data.
    """
    source: str
    status: str          # "available" | "partial" | "out_of_range" | "missing" | "untracked"
    last_date: object    # date | None
    reason: str


def coverage_summary(*, start: date, end: date) -> list[SourceCoverageStatus]:
    """Return per-source coverage for the same set of sources
    ``operator_public_text_daily`` consults.

    Helpful for narrative-writing: when a source shows zero contribution
    in a window where its coverage doesn't intersect, that zero is data
    absence, not behavioral signal. The lynchpin ``coverage_report``
    knows this — this function exposes it in the same vocabulary the
    rollup uses.
    """
    from ..graph.coverage import coverage_report

    # Map operator-public-text channel-prefix → coverage_report source name.
    # coverage_report uses display-aliased names: "messenger" not
    # "facebook_messenger". See lynchpin/graph/coverage.py _OVERRIDES.
    relevant = {
        "irc": "irc",
        "reddit": "reddit",
        "wykop": None,            # wykop has no entry in coverage_report
        "messenger": "messenger",
        "gmail": None,            # not yet in coverage_report
    }
    report = coverage_report(start=start, end=end)
    by_source = {sr.source: sr for sr in report.sources}
    out: list[SourceCoverageStatus] = []
    for channel, src_name in relevant.items():
        if src_name is None or src_name not in by_source:
            # Source not represented in coverage_report; do not report
            # zero-contribution days as proven silence.
            out.append(SourceCoverageStatus(
                source=channel, status="untracked",
                last_date=None,
                reason="not represented in coverage_report",
            ))
            continue
        sr = by_source[src_name]
        out.append(SourceCoverageStatus(
            source=channel,
            status=sr.status,
            last_date=sr.last_date,
            reason=sr.reason or "",
        ))
    return out


def monthly_rollup(rows: list[OperatorPublicTextDay]) -> list[tuple[str, int, int, int]]:
    """Collapse to per-month (month_str, total_chars, message_count, active_days)."""
    by_month: dict[str, dict[str, int]] = defaultdict(
        lambda: {"chars": 0, "messages": 0, "days": 0}
    )
    for r in rows:
        m = r.date.strftime("%Y-%m")
        by_month[m]["chars"] += r.total_chars
        by_month[m]["messages"] += r.message_count
        by_month[m]["days"] += 1
    return [
        (m, b["chars"], b["messages"], b["days"])
        for m, b in sorted(by_month.items())
    ]
