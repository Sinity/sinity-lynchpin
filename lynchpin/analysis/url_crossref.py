"""Cross-source URL mention analysis.

Aggregates URL references across every personal source that carries URLs —
web history visits, raindrop bookmarks, reddit posts/comments (split into
own vs extrinsic quoted), wykop links/entries/comments, IRC messages — under
a single normalized-URL key. Useful for questions like:

- "How often did this URL come up across my channels, and where?"
- "What links did I share on IRC versus bookmark versus actually visit?"
- "Of the URLs that came up in reddit comments I wrote, how many did I
  actually click through to?"

Every mention carries a ``source``, ``role`` (visit | bookmark | own | quoted
| link | mention), ``timestamp``, ``url`` (normalized), ``snippet`` (small
context), and ``raw_url`` (pre-normalization). The role distinction matters:
a URL in someone-else's-quote inside a reddit comment is *not* a URL sinity
shared — it's an extrinsic reference sinity was responding to.

Output is intentionally per-mention, not pre-aggregated; callers aggregate
the way they need (by URL, by domain, by week, by source).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Iterator, Literal, Optional
from urllib.parse import urlparse

from ..sources.web_urls import normalize_url


MentionRole = Literal[
    "visit",     # web history page visit
    "bookmark",  # raindrop / browser bookmark
    "own",       # operator-authored mention (own reddit body, IRC line, wykop comment)
    "quoted",    # extrinsic — URL inside reddit > blockquote
    "link",      # the URL field of a link-share item (wykop link, reddit post URL)
    "mention",   # generic in-text mention (wykop entry content, etc.)
]


_URL_RE = re.compile(
    # Allow parens in the URL body — Wikipedia and similar use them
    # legitimately. Bracket / quote / whitespace still terminate.
    r"https?://[^\s<>\"'`\[\]{}]+",
    flags=re.IGNORECASE,
)


def extract_urls(text: str) -> tuple[str, ...]:
    """Pull URLs out of free text.

    Captures ``http(s)://...`` runs terminated by whitespace or bracket /
    quote characters. Does NOT normalize — call ``normalize_url`` on each
    result. Trailing punctuation is stripped two ways:

    - Sentence-terminal punctuation (``.``, ``,``, ``;``, ``!``, ``?``, ``"``,
      ``]``, smart quotes) is always stripped.
    - A trailing close-paren is stripped only when it would be unbalanced
      relative to opens inside the URL — so ``Foo_(bar)`` survives but
      ``(see https://example.com/foo)`` strips the trailing ``)``.
    """
    if not text:
        return ()
    out: list[str] = []
    for match in _URL_RE.finditer(text):
        url = match.group(0)
        url = url.rstrip(".,;:!?\"]'\u201d\u2019")
        # Balance close-parens against opens. Strip from the right as long
        # as there are more ``)`` than ``(`` in the URL.
        while url.endswith(")") and url.count(")") > url.count("("):
            url = url[:-1]
        if url:
            out.append(url)
    return tuple(out)


@dataclass(frozen=True)
class URLMention:
    """One URL-mention observation from a source.

    Fields:
    - ``url``: the normalized canonical form (utm/fbclid stripped, host
      lowercased, www. removed, scheme upgraded to https).
    - ``raw_url``: the original form as extracted, before normalization.
    - ``domain``: convenience — the canonical host of ``url``.
    - ``source``: ``"web"`` | ``"raindrop"`` | ``"reddit"`` | ``"wykop"`` |
      ``"irc"``.
    - ``role``: see ``MentionRole`` — distinguishes visited vs bookmarked vs
      authored-mention vs quoted-mention. Critical: ``"quoted"`` URLs are
      not sinity-shared, they're URLs the parent comment cited.
    - ``timestamp``: when the mention happened (UTC, naive ok if source
      doesn't carry tz).
    - ``snippet``: ~120 chars of surrounding context (None for visit /
      bookmark / link where the URL itself is the content).
    - ``ref_id``: source-specific identifier (reddit permalink, irc
      channel#line, wykop comment id, bookmark id) for back-reference.
    """

    url: str
    raw_url: str
    domain: str
    source: str
    role: MentionRole
    timestamp: Optional[datetime]
    snippet: Optional[str]
    ref_id: Optional[str]


def _norm(raw: str) -> tuple[str, str]:
    """Return (normalized_url, domain) — empty strings if junk."""
    normalized = normalize_url(raw)
    try:
        domain = urlparse(normalized).netloc
    except Exception:
        domain = ""
    return normalized, domain


def _snippet(text: str, match_at: str, window: int = 60) -> str:
    """Return small context window around the URL match."""
    idx = text.find(match_at)
    if idx < 0:
        return text[: 2 * window].strip()
    start = max(0, idx - window)
    end = min(len(text), idx + len(match_at) + window)
    return text[start:end].strip()


# ── per-source emitters ─────────────────────────────────────────────────────


def _iter_reddit_mentions(start: date, end: date) -> Iterator[URLMention]:
    from ..sources.reddit import (
        iter_comments,
        iter_posts,
        split_quoted_text,
    )

    def _in_range(dt: Optional[datetime]) -> bool:
        if dt is None:
            return False
        d = dt.date()
        return start <= d <= end

    for comment in iter_comments(start=start, end=end + timedelta(days=1)):
        if not _in_range(comment.created):
            continue
        body = comment.body or ""
        if "http" not in body:
            continue
        own, quotes = split_quoted_text(body)
        # operator-authored URLs in own text
        for raw in extract_urls(own):
            norm, domain = _norm(raw)
            if not norm:
                continue
            yield URLMention(
                url=norm,
                raw_url=raw,
                domain=domain,
                source="reddit",
                role="own",
                timestamp=comment.created,
                snippet=_snippet(own, raw),
                ref_id=comment.permalink or comment.id,
            )
        # extrinsic URLs from the parent comment being responded to
        for q in quotes:
            for raw in extract_urls(q):
                norm, domain = _norm(raw)
                if not norm:
                    continue
                yield URLMention(
                    url=norm,
                    raw_url=raw,
                    domain=domain,
                    source="reddit",
                    role="quoted",
                    timestamp=comment.created,
                    snippet=_snippet(q, raw),
                    ref_id=comment.permalink or comment.id,
                )

    for post in iter_posts(start=start, end=end + timedelta(days=1)):
        if not _in_range(post.created):
            continue
        # The post.url field is the link the post points to (link-share posts).
        if post.url and post.url.startswith("http"):
            norm, domain = _norm(post.url)
            if norm:
                yield URLMention(
                    url=norm,
                    raw_url=post.url,
                    domain=domain,
                    source="reddit",
                    role="link",
                    timestamp=post.created,
                    snippet=None,
                    ref_id=post.id,
                )
        body = post.body or ""
        if "http" in body:
            for raw in extract_urls(body):
                norm, domain = _norm(raw)
                if not norm:
                    continue
                yield URLMention(
                    url=norm,
                    raw_url=raw,
                    domain=domain,
                    source="reddit",
                    role="own",
                    timestamp=post.created,
                    snippet=_snippet(body, raw),
                    ref_id=post.id,
                )


def _iter_irc_mentions(start: date, end: date) -> Iterator[URLMention]:
    from ..sources.irc_raw import iter_messages_in_range, normalize_nick, _OPERATOR_NICKS

    for msg in iter_messages_in_range(start=start, end=end):
        if not msg.timestamp:
            continue
        # Skip server / meta lines: weechat captures the libera channel as
        # plain logs alongside chat, plus per-channel join/part/quit/mode
        # notifications appear in chat logs too. These can carry URLs in
        # MOTD, channel topic, server notices — they are NOT operator-shared
        # nor real human mentions and would otherwise inflate URL counts.
        # In a quick audit: 6.2% of URL-bearing rows are meta lines.
        if msg.is_meta:
            continue
        text = msg.text or ""
        if "http" not in text:
            continue
        # Distinguish operator messages from ambient. Both interesting but
        # roles differ — operator-shared URLs are ``own``, ambient channel
        # URLs are ``mention``.
        is_op = normalize_nick(msg.speaker).lower() in _OPERATOR_NICKS
        role: MentionRole = "own" if is_op else "mention"
        for raw in extract_urls(text):
            norm, domain = _norm(raw)
            if not norm:
                continue
            yield URLMention(
                url=norm,
                raw_url=raw,
                domain=domain,
                source="irc",
                role=role,
                timestamp=msg.timestamp,
                snippet=_snippet(text, raw),
                ref_id=f"{msg.channel}#{msg.line_no}",
            )


def _iter_wykop_mentions(start: date, end: date) -> Iterator[URLMention]:
    from ..sources.exports import (
        iter_wykop_link_comments,
        iter_wykop_entries,
        iter_wykop_entry_comments,
    )

    def _in_range(dt: Optional[datetime]) -> bool:
        if dt is None:
            return False
        d = dt.date()
        return start <= d <= end

    for lc in iter_wykop_link_comments(start=start, end=end + timedelta(days=1)):
        if not _in_range(lc.created_at):
            continue
        # the link itself — what the thread is about (extrinsic link, but
        # sinity engaged enough to comment on it)
        if lc.link_url and lc.link_url.startswith("http"):
            norm, domain = _norm(lc.link_url)
            if norm:
                yield URLMention(
                    url=norm,
                    raw_url=lc.link_url,
                    domain=domain,
                    source="wykop",
                    role="link",
                    timestamp=lc.created_at,
                    snippet=lc.link_title or None,
                    ref_id=f"wykop:link:{lc.link_id}",
                )
        for raw in extract_urls(lc.content or ""):
            norm, domain = _norm(raw)
            if not norm:
                continue
            yield URLMention(
                url=norm,
                raw_url=raw,
                domain=domain,
                source="wykop",
                role="own",
                timestamp=lc.created_at,
                snippet=_snippet(lc.content or "", raw),
                ref_id=f"wykop:lc:{lc.id}",
            )

    for entry in iter_wykop_entries(start=start, end=end + timedelta(days=1)):
        if not _in_range(entry.created_at):
            continue
        for raw in extract_urls(entry.content or ""):
            norm, domain = _norm(raw)
            if not norm:
                continue
            yield URLMention(
                url=norm,
                raw_url=raw,
                domain=domain,
                source="wykop",
                role="own",
                timestamp=entry.created_at,
                snippet=_snippet(entry.content or "", raw),
                ref_id=f"wykop:entry:{entry.id}",
            )

    for ec in iter_wykop_entry_comments(start=start, end=end + timedelta(days=1)):
        if not _in_range(ec.created_at):
            continue
        for raw in extract_urls(ec.content or ""):
            norm, domain = _norm(raw)
            if not norm:
                continue
            yield URLMention(
                url=norm,
                raw_url=raw,
                domain=domain,
                source="wykop",
                role="own",
                timestamp=ec.created_at,
                snippet=_snippet(ec.content or "", raw),
                ref_id=f"wykop:ec:{ec.id}",
            )


def _iter_raindrop_mentions(start: date, end: date) -> Iterator[URLMention]:
    # ``iter_raindrop_bookmarks`` returns the canonical/deduped stream of
    # RaindropBookmark dataclasses; ``_all`` returns (export, bookmark) tuples
    # across every raw export and is not what we want here.
    from ..sources.exports import iter_raindrop_bookmarks

    for b in iter_raindrop_bookmarks(start=start, end=end + timedelta(days=1)):
        if not b.created:
            continue
        d = b.created.date()
        if d < start or d > end:
            continue
        if not b.url or not b.url.startswith("http"):
            continue
        norm, domain = _norm(b.url)
        if not norm:
            continue
        yield URLMention(
            url=norm,
            raw_url=b.url,
            domain=domain,
            source="raindrop",
            role="bookmark",
            timestamp=b.created,
            snippet=b.title or None,
            ref_id=f"raindrop:{b.id}",
        )


def _iter_web_visits(start: date, end: date) -> Iterator[URLMention]:
    from ..sources.web import iter_entries

    for entry in iter_entries(start=start, end=end):
        url = entry.get("url") if isinstance(entry, dict) else getattr(entry, "url", None)
        iso = entry.get("iso_time") if isinstance(entry, dict) else getattr(entry, "iso_time", None)
        title = entry.get("title") if isinstance(entry, dict) else getattr(entry, "title", None)
        if not url or not url.startswith("http"):
            continue
        ts: Optional[datetime] = None
        if iso:
            try:
                ts = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            except Exception:
                ts = None
        if ts is None:
            continue
        d = ts.date()
        if d < start or d > end:
            continue
        norm, domain = _norm(url)
        if not norm:
            continue
        yield URLMention(
            url=norm,
            raw_url=url,
            domain=domain,
            source="web",
            role="visit",
            timestamp=ts,
            snippet=title,
            ref_id=None,
        )


def iter_url_mentions(
    *,
    start: date,
    end: date,
    sources: Optional[Iterable[str]] = None,
) -> Iterator[URLMention]:
    """Stream URLMention rows across every source that carries URLs.

    ``sources`` filters which emitters run. Valid values:
    ``"reddit"``, ``"irc"``, ``"wykop"``, ``"raindrop"``, ``"web"``.
    Default: all five.

    Note: web visits are by far the largest stream (typically 100k+ rows for
    a wide window). Filter ``sources={"reddit","irc","wykop"}`` for
    "URLs sinity discussed somewhere" without dragging the visit firehose.
    """
    emitters = {
        "reddit": _iter_reddit_mentions,
        "irc": _iter_irc_mentions,
        "wykop": _iter_wykop_mentions,
        "raindrop": _iter_raindrop_mentions,
        "web": _iter_web_visits,
    }
    selected = set(sources) if sources else set(emitters)
    for name, fn in emitters.items():
        if name not in selected:
            continue
        yield from fn(start, end)


# ── aggregation helpers ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class URLAggregate:
    """Aggregate of mentions for a single canonical URL."""

    url: str
    domain: str
    total_mentions: int
    by_source: dict[str, int]
    by_role: dict[str, int]
    first_seen: Optional[datetime]
    last_seen: Optional[datetime]
    sample_snippets: tuple[str, ...]  # up to 3 distinct snippets


def aggregate_by_url(mentions: Iterable[URLMention]) -> list[URLAggregate]:
    """Collapse a mention stream into per-URL rows.

    Result is sorted by total_mentions descending.
    """
    from collections import defaultdict

    by_url: dict[str, dict] = defaultdict(
        lambda: {
            "by_source": defaultdict(int),
            "by_role": defaultdict(int),
            "first": None,
            "last": None,
            "snippets": [],
            "domain": "",
        }
    )
    for m in mentions:
        bucket = by_url[m.url]
        bucket["by_source"][m.source] += 1
        bucket["by_role"][m.role] += 1
        bucket["domain"] = m.domain
        if m.timestamp:
            ts = m.timestamp
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if bucket["first"] is None or ts < bucket["first"]:
                bucket["first"] = ts
            if bucket["last"] is None or ts > bucket["last"]:
                bucket["last"] = ts
        if m.snippet and len(bucket["snippets"]) < 3 and m.snippet not in bucket["snippets"]:
            bucket["snippets"].append(m.snippet)

    out: list[URLAggregate] = []
    for url, bucket in by_url.items():
        out.append(
            URLAggregate(
                url=url,
                domain=bucket["domain"],
                total_mentions=sum(bucket["by_source"].values()),
                by_source=dict(bucket["by_source"]),
                by_role=dict(bucket["by_role"]),
                first_seen=bucket["first"],
                last_seen=bucket["last"],
                sample_snippets=tuple(bucket["snippets"]),
            )
        )
    out.sort(key=lambda r: r.total_mentions, reverse=True)
    return out


def cross_referenced_urls(
    mentions: Iterable[URLMention],
    *,
    min_sources: int = 2,
) -> list[URLAggregate]:
    """Return aggregates for URLs that appear in at least ``min_sources``
    distinct sources.

    This surfaces URLs that crossed channels — a link that came up on IRC
    AND was later bookmarked AND was visited, for example, is signal that
    the URL had real significance vs. one-off appearance.
    """
    return [
        agg
        for agg in aggregate_by_url(mentions)
        if len(agg.by_source) >= min_sources
    ]
