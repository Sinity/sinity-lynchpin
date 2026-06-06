"""IRC raw-log source — reads WeeChat-format logs directly.

Supersedes the processed-file parser in ``irc.py`` for raw-log access.
The older module's ``IRCConversation`` concept is preserved as an L3 product
here (``extract_conversations``), but this module reads the canonical raw
WeeChat logs under ``_raw/<channel>/`` rather than the pre-processed
``_processed/sinity/*.log`` extraction output.

Graduated API layers:
  L0: raw messages with date/channel filtering
  L1: session extraction (gap-based, configurable idle threshold)
  L2: speaker stats + identity (nick normalization, bot detection, classification)
  L3: conversation extraction (dense interaction clusters)
  Daily: daily_irc_activity(start, end) → IRCDayActivity
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Iterator, Optional

from ..core.config import get_config
from ..core.parse import as_local
from ..core.primitives import logical_date

__all__ = [
    "IRCRawMessage",
    "IRCRawSession",
    "IRCSpeakerClass",
    "IRCSpeakerIdentity",
    "IRCSpeakerStats",
    "IRCConversation",
    "IRCDayActivity",
    "irc_raw_root",
    "irc_channels",
    "irc_events_path",
    "irc_manifest_path",
    "iter_messages",
    "iter_raw_messages",
    "iter_messages_in_range",
    "extract_sessions",
    "normalize_nick",
    "classify_speaker",
    "speaker_identities",
    "speaker_stats",
    "extract_conversations",
    "daily_irc_activity",
]

# ── Data types ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class IRCRawMessage:
    timestamp: datetime
    speaker: str
    text: str
    channel: str
    source_file: str
    line_no: int

    @property
    def date(self) -> date:
        return logical_date(self.timestamp)

    @property
    def is_meta(self) -> bool:
        """True for server lines, join/part/quit notifications, and /me actions."""
        return self.speaker in ("--", "-->", "<--", "=!=", "*")

    @property
    def word_count(self) -> int:
        return len(self.text.split()) if self.text else 0


@dataclass(frozen=True)
class IRCRawSession:
    """A contiguous span of messages within ``max_idle`` of each other."""
    channel: str
    start: datetime
    end: datetime
    message_count: int
    unique_speakers: int
    speakers: tuple[str, ...]
    messages: tuple[IRCRawMessage, ...]

    @property
    def date(self) -> date:
        return self.start.date()

    @property
    def duration_minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60.0


@dataclass(frozen=True)
class IRCSpeakerStats:
    speaker: str
    channel: str
    message_count: int
    total_words: int
    avg_message_length: float
    active_hours: int
    reply_to: tuple[tuple[str, int], ...]  # (target_speaker, count)
    first_seen: datetime
    last_seen: datetime


@dataclass(frozen=True)
class IRCConversation:
    """A dense interaction cluster — multiple speakers exchanging messages.

    This is the raw-log equivalent of the older ``irc.IRCConversation``.
    """
    conversation_id: str
    channel: str
    start: datetime
    end: datetime
    message_count: int
    unique_speakers: int
    speakers: tuple[str, ...]
    messages: tuple[IRCRawMessage, ...]

    @property
    def date(self) -> date:
        return self.start.date()

    @property
    def duration_minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60.0


@dataclass(frozen=True)
class IRCDayActivity:
    date: date
    channels: tuple[str, ...]
    total_messages: int
    operator_messages: int
    unique_speakers: int
    session_count: int
    conversation_count: int
    channel_breakdown: tuple[tuple[str, int], ...]  # (channel, msg_count)


class IRCSpeakerClass(Enum):
    HUMAN = "human"
    BOT_RELAY = "bot_relay"       # e.g., feepbot relaying <gwern> messages
    BOT_LINK = "bot_link"         # e.g., +Robomot posting URLs
    BOT_OTHER = "bot_other"       # name contains "bot"/"serv"
    ACTION = "action"             # IRC /me actions (speaker = "*")
    GUEST = "guest"               # Guest12345 patterns
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class IRCSpeakerIdentity:
    raw_nick: str
    canonical_nick: str
    speaker_class: IRCSpeakerClass
    is_operator: bool
    aliases: tuple[str, ...]   # known alternative nicks
    relay_target: str | None   # if relay bot, who is being relayed (e.g. "gwern")


# ── Path resolution ────────────────────────────────────────────────────────────


def irc_raw_root() -> Path:
    return get_config().irc_root / "_raw"


def irc_channels(root: Optional[Path] = None) -> list[str]:
    base = root or irc_raw_root()
    if not base.exists():
        return []
    return sorted(
        d.name for d in base.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )


def irc_events_path(root: Optional[Path] = None) -> Path:
    base = root or get_config().irc_root
    return base / "processed/events.ndjson"


def irc_manifest_path(root: Optional[Path] = None) -> Path:
    return irc_events_path(root).with_suffix(".manifest.json")


def _ensure_irc_materialized(
    *,
    start: date | None = None,
    end: date | None = None,
) -> None:
    from ..materialization import ensure_materialized

    window = (start, end) if start is not None and end is not None else None
    ensure_materialized("irc", window=window)

# ── Nickname normalization ────────────────────────────────────────────────────

# Known aliases: canonical → set of raw nicks (lowercased)
_KNOWN_ALIASES: dict[str, set[str]] = {
    "sinity": {"sinity", "sinity2"},
    "obormot": {"obormot\\arcturus", "obormot_\\gaia", "obormot\\gaia", "obormot_\\arcturu"},
    "dbohdan": {"dbohdan[phone]", "dbohdan[goguma]", "dbohdan[quassel]", "dbohdan"},
    "logos": {"logos01", "logos01_", "logos02", "logos49"},
    "pie": {"pie_", "pie__"},
    "feep": {"feep", "feep[webchat]", "feep[work]", "feep_"},
    "sdr": {"sdr4", "sdr8", "sdr|^^lw__", "sdr|^^lw__0", "sdr"},
    "shminux": {"shminux17", "shminux28", "shminux49", "shminux66", "shminux"},
    "robomot": {"+robomot", "robomot", "+robomot_"},
    "galambo": {"galambo", "galambo2"},
    "shamoe": {"shamoe", "shamoe1"},
    "mesaoptimizer": {"mesaoptimizer", "mesaoptimizer0"},
    "two2thehead": {"two2thehead", "two2theheadpc1"},
    "omnifunctor": {"omnifunctor", "omnifunctor_"},
}

# Reverse lookup: lowercased raw nick → canonical
_ALIAS_LOOKUP: dict[str, str] = {}
for _canonical, _nicks in _KNOWN_ALIASES.items():
    for _nick in _nicks:
        _ALIAS_LOOKUP[_nick.lower()] = _canonical

# Heuristic nick normalization patterns.
# WeeChat escapes literal spaces in nicks as backslash-space.
_NICK_ESC_SPACE_RE = re.compile(r"\\.")
_NICK_BRACKET_SUFFIX_RE = re.compile(r"[|`\[\(].*$")
# Digit-only suffix: nick2 → nick
_NICK_DIGIT_SUFFIX_RE = re.compile(r"\d+$")
# Short _ suffix: nick_web, nick_, nick__ → nick (but preserve _longcompound)
_NICK_SHORT_USCORE_RE = re.compile(r"(_[a-z]{1,6}|_\d*)$")
# Guest normalization: Guest\d+ → Guest
_GUEST_NUM_RE = re.compile(r"^guest\d+$", re.IGNORECASE)


def normalize_nick(raw_speaker: str) -> str:
    """Return the canonical nick for a raw speaker string.

    First checks the known-aliases table. Falls back to heuristic suffix
    stripping (``nick|afk`` → ``nick``, ``nick[phone]`` → ``nick``,
    ``nick_web`` → ``nick``, ``nick2`` → ``nick``).

    Underscore suffixes are only stripped when the remaining name contains
    no other underscores — so ``totally_unique_nick`` is preserved (compound)
    but ``kuudes_web`` → ``kuudes`` (genuine suffix).
    """
    key = raw_speaker.lower()
    if key in _ALIAS_LOOKUP:
        return _ALIAS_LOOKUP[key]
    if _GUEST_NUM_RE.match(raw_speaker):
        return "Guest"
    stripped = raw_speaker
    stripped = _NICK_ESC_SPACE_RE.sub("", stripped)
    stripped = _NICK_BRACKET_SUFFIX_RE.sub("", stripped)
    # Only strip _suffix if base has no other underscores
    match = _NICK_SHORT_USCORE_RE.search(stripped)
    if match:
        candidate = stripped[:match.start()]
        if "_" not in candidate:
            stripped = candidate
    stripped = _NICK_DIGIT_SUFFIX_RE.sub("", stripped)
    stripped = stripped.rstrip("-_")
    return stripped if stripped else raw_speaker


# Known operator nicks (lowercased)
_OPERATOR_NICKS: set[str] = {
    "sinity", "sinity2",
    "ezodev", "ilukbas",
}


def classify_speaker(
    raw_speaker: str,
    messages: list[IRCRawMessage] | None = None,
) -> IRCSpeakerClass:
    """Classify a speaker as human, bot, action, or guest.

    Heuristics (in priority order):
    1. ``*`` speaker → ACTION (/me messages)
    2. ``GuestNNN`` pattern → GUEST
    3. Name contains "bot"/"serv" → BOT_OTHER
    4. URL fraction > 30% in sample → BOT_LINK
    5. ``<speaker> text`` relay pattern in > 20% of messages → BOT_RELAY
    6. Otherwise → HUMAN
    """
    lower = raw_speaker.lower()

    if raw_speaker == "*":
        return IRCSpeakerClass.ACTION

    if _GUEST_NUM_RE.match(raw_speaker):
        return IRCSpeakerClass.GUEST

    # Content-based heuristics first (stronger signal than name)
    if messages and len(messages) >= 10:
        sample = messages[:100]

        url_count = sum(1 for m in sample if "http" in m.text)
        url_frac = url_count / len(sample)

        # <speaker> relay pattern at start of message
        relay_count = sum(
            1 for m in sample
            if m.text.startswith("<") and ">" in m.text[:50]
        )
        relay_frac = relay_count / len(sample)

        if relay_frac > 0.3:
            return IRCSpeakerClass.BOT_RELAY
        if url_frac > 0.35:
            return IRCSpeakerClass.BOT_LINK

    # Name-based classification (weaker signal, checked after content)
    if any(token in lower for token in ("bot", "serv", "relay", "feed")):
        if "+" in raw_speaker:
            return IRCSpeakerClass.BOT_RELAY
        return IRCSpeakerClass.BOT_OTHER

    return IRCSpeakerClass.HUMAN


def _extract_relay_target(messages: list[IRCRawMessage]) -> str | None:
    """Extract the most common relay target from ``<speaker>`` prefixes."""
    relay_re = re.compile(r"^<(\S+)>")
    targets = Counter()
    for m in messages[:200]:
        match = relay_re.match(m.text)
        if match:
            targets[match.group(1).lower()] += 1
    if targets:
        top, count = targets.most_common(1)[0]
        if count >= 3:
            return top
    return None


def speaker_identities(
    *,
    start: Optional[date] = None,
    end: Optional[date] = None,
    channel: Optional[str] = None,
    root: Optional[Path] = None,
    ensure: bool = True,
) -> list[IRCSpeakerIdentity]:
    """Yield speaker identity records with classification and normalization.

    Builds a per-speaker view: canonical nick, bot/human classification,
    relay target (if applicable), and known aliases.
    """
    # Collect messages per raw speaker
    by_speaker: dict[str, list[IRCRawMessage]] = defaultdict(list)
    if start is not None and end is not None:
        message_iter = iter_messages_in_range(
            start=start,
            end=end,
            channel=channel,
            root=root,
            ensure=ensure,
        )
    else:
        message_iter = iter_messages(channel=channel, root=root, ensure=ensure)
    for msg in message_iter:
        if msg.is_meta:
            continue
        by_speaker[msg.speaker].append(msg)

    results: list[IRCSpeakerIdentity] = []
    seen_canonical: set[str] = set()

    for raw_nick, msgs in sorted(by_speaker.items()):
        canonical = normalize_nick(raw_nick)
        sp_class = classify_speaker(raw_nick, msgs)
        relay_target = _extract_relay_target(msgs) if sp_class == IRCSpeakerClass.BOT_RELAY else None
        is_op = canonical.lower() in _OPERATOR_NICKS or raw_nick.lower() in _OPERATOR_NICKS
        aliases = tuple(
            sorted(n for n in _KNOWN_ALIASES.get(canonical.lower(), set()) if n.lower() != canonical.lower())
        )

        results.append(IRCSpeakerIdentity(
            raw_nick=raw_nick,
            canonical_nick=canonical,
            speaker_class=sp_class,
            is_operator=is_op,
            aliases=aliases,
            relay_target=relay_target,
        ))
        seen_canonical.add(canonical.lower())

    return results


# ── WeeChat log parsing ───────────────────────────────────────────────────────

_WEE_CHAT_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\t(?P<speaker>[^\t]*)\t(?P<text>.*)$"
)

# Meta/status lines have speaker "--" and text like "-->", "irc:", etc.
# We keep them but flag via is_meta.
_META_PREFIXES = ("-->", "<--", "--", "irc:", "tantalum", "platinum", "zirconium",
                  "Welcome to", "Your host is", "This server was",
                  "There are", "You are now", "SASL", "***", "Option",
                  "Options saved", "Plugin", "Plugins loaded")


def _is_meta_text(text: str) -> bool:
    return any(text.startswith(p) for p in _META_PREFIXES)


def iter_messages(
    *,
    channel: Optional[str] = None,
    root: Optional[Path] = None,
    ensure: bool = True,
) -> Iterator[IRCRawMessage]:
    """Yield all canonical IRC messages.

    Args:
        channel: filter to a specific channel directory (e.g. "libera" or "#lesswrong").
                 If None, yields from all channels.
        root: override the raw-log root directory. Passing a root explicitly
              selects raw parsing for materializers/tests; ordinary reads use
              the canonical events product when present.
    """
    if root is None:
        if ensure:
            _ensure_irc_materialized()
        product = irc_events_path()
        if product.exists():
            yield from _iter_materialized_messages(product, channel=channel)
            return
    yield from iter_raw_messages(channel=channel, root=root)


def iter_raw_messages(
    *,
    channel: Optional[str] = None,
    root: Optional[Path] = None,
) -> Iterator[IRCRawMessage]:
    """Yield parsed IRC messages directly from raw WeeChat log files."""
    base = root or irc_raw_root()
    if not base.exists():
        return

    channels_to_read = [channel] if channel else irc_channels(root=base)
    for ch in channels_to_read:
        ch_dir = base / ch
        if not ch_dir.is_dir():
            continue
        for log_file in sorted(ch_dir.iterdir()):
            if not log_file.suffix == ".log" and not log_file.name.endswith(".log"):
                continue
            yield from _parse_weechat_file(log_file, channel=ch)


def _iter_materialized_messages(
    path: Path,
    *,
    channel: Optional[str] = None,
) -> Iterator[IRCRawMessage]:
    with path.open("r", encoding="utf-8") as handle:
        for fallback_line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                continue
            msg_channel = str(payload.get("channel") or "")
            if channel is not None and msg_channel != channel:
                continue
            yield IRCRawMessage(
                timestamp=as_local(
                    datetime.fromisoformat(
                        str(payload["timestamp"]).replace("Z", "+00:00")
                    )
                ),
                speaker=str(payload.get("speaker_raw") or payload.get("speaker") or ""),
                text=str(payload.get("text") or ""),
                channel=msg_channel,
                source_file=str(payload.get("source_file") or path),
                line_no=int(payload.get("line_no") or fallback_line_no),
            )


def _parse_weechat_file(path: Path, *, channel: str) -> Iterator[IRCRawMessage]:
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line_no, raw in enumerate(fh, 1):
            line = raw.rstrip("\n").rstrip("\r")
            match = _WEE_CHAT_LINE_RE.match(line)
            if not match:
                continue
            try:
                ts = as_local(datetime.fromisoformat(match.group("ts")))
            except ValueError:
                continue
            speaker = match.group("speaker")
            text = match.group("text")
            yield IRCRawMessage(
                timestamp=ts,
                speaker=speaker,
                text=text,
                channel=channel,
                source_file=str(path),
                line_no=line_no,
            )


def iter_messages_in_range(
    *,
    start: date,
    end: date,
    channel: Optional[str] = None,
    root: Optional[Path] = None,
    ensure: bool = True,
) -> Iterator[IRCRawMessage]:
    """Yield messages within [start, end] inclusive."""
    if ensure and root is None:
        _ensure_irc_materialized(start=start, end=end + timedelta(days=1))
    for msg in iter_messages(channel=channel, root=root, ensure=False):
        if msg.date > end:
            continue
        if msg.date < start:
            continue
        yield msg

# ── L1: Session extraction ────────────────────────────────────────────────────


def extract_sessions(
    *,
    start: Optional[date] = None,
    end: Optional[date] = None,
    channel: Optional[str] = None,
    root: Optional[Path] = None,
    max_idle_minutes: int = 30,
    min_messages: int = 2,
    ensure: bool = True,
) -> Iterator[IRCRawSession]:
    """Group messages into sessions by idle-gap detection.

    A session ends when ``max_idle_minutes`` elapses between consecutive
    messages. Meta/status lines (speaker == "--") do not reset the idle
    clock — only human messages count.
    """
    if start is not None and end is not None:
        messages = list(
            iter_messages_in_range(
                start=start,
                end=end,
                channel=channel,
                root=root,
                ensure=ensure,
            )
        )
    else:
        messages = list(iter_messages(channel=channel, root=root, ensure=ensure))
        if start is not None:
            start_dt = as_local(datetime.combine(start, datetime.min.time()))
            messages = [m for m in messages if m.timestamp >= start_dt]
        if end is not None:
            end_dt = as_local(datetime.combine(end + timedelta(days=1), datetime.min.time()))
            messages = [m for m in messages if m.timestamp < end_dt]

    if not messages:
        return

    messages.sort(key=lambda m: m.timestamp)
    max_idle = timedelta(minutes=max_idle_minutes)

    buf: list[IRCRawMessage] = [messages[0]]
    last_human_ts = messages[0].timestamp if not messages[0].is_meta else None

    for msg in messages[1:]:
        if not msg.is_meta:
            if last_human_ts is not None:
                gap = msg.timestamp - last_human_ts
                if gap > max_idle:
                    if len(buf) >= min_messages:
                        yield _build_session(buf)
                    buf = []
            last_human_ts = msg.timestamp
        elif last_human_ts is None and buf:
            last_human_ts = buf[0].timestamp
        buf.append(msg)

    if len(buf) >= min_messages:
        yield _build_session(buf)


def _build_session(messages: list[IRCRawMessage]) -> IRCRawSession:
    speakers = sorted(set(m.speaker for m in messages if not m.is_meta))
    return IRCRawSession(
        channel=messages[0].channel,
        start=messages[0].timestamp,
        end=messages[-1].timestamp,
        message_count=len(messages),
        unique_speakers=len(speakers),
        speakers=tuple(speakers),
        messages=tuple(messages),
    )

# ── L2: Speaker stats ─────────────────────────────────────────────────────────


def speaker_stats(
    *,
    start: Optional[date] = None,
    end: Optional[date] = None,
    channel: Optional[str] = None,
    root: Optional[Path] = None,
    use_normalized: bool = True,
    ensure: bool = True,
) -> list[IRCSpeakerStats]:
    """Compute per-speaker statistics including reply-to patterns.

    When ``use_normalized=True`` (default), nicks are normalized via
    ``normalize_nick()`` so that ``dbohdan[phone]`` and ``dbohdan[goguma]``
    are counted as one speaker. Reply-to targets are also normalized.

    Reply detection: if a message's text contains another speaker's nick
    (case-insensitive prefix match or ``nick:`` / ``nick,`` pattern),
    it counts as a reply to that speaker.
    """
    if start is not None and end is not None:
        messages = list(
            iter_messages_in_range(
                start=start,
                end=end,
                channel=channel,
                root=root,
                ensure=ensure,
            )
        )
    else:
        messages = list(iter_messages(channel=channel, root=root, ensure=ensure))
        if start is not None:
            start_dt = as_local(datetime.combine(start, datetime.min.time()))
            messages = [m for m in messages if m.timestamp >= start_dt]
        if end is not None:
            end_dt = as_local(datetime.combine(end + timedelta(days=1), datetime.min.time()))
            messages = [m for m in messages if m.timestamp < end_dt]

    human = [m for m in messages if not m.is_meta]
    if not human:
        return []

    # Build identity map for classification context
    identities = {
        si.raw_nick.lower(): si
        for si in speaker_identities(
            start=start,
            end=end,
            channel=channel,
            root=root,
            ensure=ensure,
        )
    }

    # Build nick set for reply detection (both raw and normalized)
    all_raw_nicks = {m.speaker.lower() for m in human}
    all_canonical_nicks = {normalize_nick(n) for n in all_raw_nicks}

    # Collect per-speaker data (normalized if requested)
    by_speaker: dict[str, list[IRCRawMessage]] = defaultdict(list)
    for m in human:
        key = normalize_nick(m.speaker) if use_normalized else m.speaker.lower()
        by_speaker[key].append(m)

    results: list[IRCSpeakerStats] = []
    for speaker_key, msgs in sorted(by_speaker.items()):
        display = speaker_key
        ident = identities.get(msgs[0].speaker.lower())
        words = sum(m.word_count for m in msgs)
        hours = len(set(m.timestamp.hour for m in msgs))
        # Collect all raw nicks that map to this canonical nick
        all_raw = sorted(set(m.speaker for m in msgs))
        reply_counts: dict[str, int] = defaultdict(int)
        for m in msgs:
            # Use canonical nicks for reply detection
            targets = _detect_replies(
                m.text,
                all_canonical_nicks if use_normalized else all_raw_nicks,
                exclude=speaker_key,
            )
            for target in targets:
                reply_counts[target] += 1
        replies_sorted = tuple(
            (t, c) for t, c in
            sorted(reply_counts.items(), key=lambda x: -x[1])
        )
        # Build label: canonical nick with classification tag
        if ident and ident.speaker_class != IRCSpeakerClass.HUMAN:
            label = f"{display} [{ident.speaker_class.value}]"
            if ident.relay_target:
                label += f" → {ident.relay_target}"
        elif len(all_raw) > 1:
            label = f"{display} (aka {', '.join(all_raw)})"
        else:
            label = display

        results.append(IRCSpeakerStats(
            speaker=label,
            channel=msgs[0].channel,
            message_count=len(msgs),
            total_words=words,
            avg_message_length=words / len(msgs) if msgs else 0.0,
            active_hours=hours,
            reply_to=replies_sorted,
            first_seen=min(m.timestamp for m in msgs),
            last_seen=max(m.timestamp for m in msgs),
        ))
    return results


_NICK_RE = re.compile(r"(?:^|\s)(?P<nick>[a-zA-Z_\[\]{}^`|][a-zA-Z0-9_\[\]{}^`|-]{0,15})(?:[:,]\s|$|\s)")


def _detect_replies(text: str, known_nicks: set[str], *, exclude: str) -> set[str]:
    targets: set[str] = set()
    for match in _NICK_RE.finditer(text):
        nick = match.group("nick").lower()
        if nick in known_nicks and nick != exclude:
            targets.add(nick)
    return targets


# ── L3: Conversation extraction ────────────────────────────────────────────────


def extract_conversations(
    *,
    start: Optional[date] = None,
    end: Optional[date] = None,
    channel: Optional[str] = None,
    root: Optional[Path] = None,
    max_idle_minutes: int = 5,
    min_speakers: int = 2,
    min_messages: int = 4,
    ensure: bool = True,
) -> Iterator[IRCConversation]:
    """Extract dense interaction clusters from sessions.

    Conversations are tighter than sessions: shorter ``max_idle`` (default 5 min),
    require ``min_speakers`` (default 2), and a higher message-density threshold.
    Result is comparable to the older ``irc.IRCConversation`` but derived from
    raw logs rather than pre-processed extraction output.
    """
    sessions = list(extract_sessions(
        start=start, end=end, channel=channel, root=root,
        max_idle_minutes=max_idle_minutes, min_messages=min_messages,
        ensure=ensure,
    ))
    _conv_counter = 0
    for session in sessions:
        if session.unique_speakers < min_speakers:
            continue
        _conv_counter += 1
        yield IRCConversation(
            conversation_id=f"irc-{session.channel}-{session.start.strftime('%Y%m%dT%H%M%S')}-{_conv_counter:04d}",
            channel=session.channel,
            start=session.start,
            end=session.end,
            message_count=session.message_count,
            unique_speakers=session.unique_speakers,
            speakers=session.speakers,
            messages=session.messages,
        )


# ── Daily rollup ───────────────────────────────────────────────────────────────


def daily_irc_activity(
    *,
    start: date,
    end: date,
    channel: Optional[str] = None,
    root: Optional[Path] = None,
    ensure: bool = True,
) -> list[IRCDayActivity]:
    """Daily IRC activity rollup for cross-source correlation."""
    messages = list(
        iter_messages_in_range(
            start=start,
            end=end,
            channel=channel,
            root=root,
            ensure=ensure,
        )
    )
    human = [m for m in messages if not m.is_meta]

    by_date: dict[date, dict[str, list[IRCRawMessage]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for m in human:
        by_date[m.date][m.channel].append(m)

    result: list[IRCDayActivity] = []
    for d in sorted(by_date):
        ch_data = by_date[d]
        total = sum(len(msgs) for msgs in ch_data.values())
        speakers = set(
            normalize_nick(m.speaker) for msgs in ch_data.values() for m in msgs
        )
        operator = sum(
            1
            for msgs in ch_data.values()
            for m in msgs
            if normalize_nick(m.speaker).lower() in _OPERATOR_NICKS
        )
        session_count = sum(
            _count_gap_groups(msgs, max_idle_minutes=30, min_messages=2)
            for msgs in ch_data.values()
        )
        conversation_count = sum(
            _count_gap_groups(
                msgs,
                max_idle_minutes=5,
                min_messages=4,
                min_speakers=2,
            )
            for msgs in ch_data.values()
        )
        channel_breakdown = tuple(
            (ch, len(msgs)) for ch, msgs in
            sorted(ch_data.items(), key=lambda x: -len(x[1]))
        )
        result.append(IRCDayActivity(
            date=d,
            channels=tuple(sorted(ch_data.keys())),
            total_messages=total,
            operator_messages=operator,
            unique_speakers=len(speakers),
            session_count=session_count,
            conversation_count=conversation_count,
            channel_breakdown=channel_breakdown,
        ))
    return result


def _count_gap_groups(
    messages: list[IRCRawMessage],
    *,
    max_idle_minutes: int,
    min_messages: int,
    min_speakers: int = 1,
) -> int:
    """Count session-like groups from already-filtered same-day messages."""
    if not messages:
        return 0
    sorted_messages = sorted(messages, key=lambda msg: msg.timestamp)
    max_idle = timedelta(minutes=max_idle_minutes)
    count = 0
    buf: list[IRCRawMessage] = []

    def flush() -> None:
        nonlocal count, buf
        if len(buf) >= min_messages:
            speakers = {normalize_nick(msg.speaker) for msg in buf}
            if len(speakers) >= min_speakers:
                count += 1
        buf = []

    last_ts: datetime | None = None
    for msg in sorted_messages:
        if last_ts is not None and msg.timestamp - last_ts > max_idle:
            flush()
        buf.append(msg)
        last_ts = msg.timestamp
    flush()
    return count
