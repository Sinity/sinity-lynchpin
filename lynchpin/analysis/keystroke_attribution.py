"""Keystroke volume attributed to apps / projects / content categories.

ActivityWatch's focus_spans already carry ``keypress_count`` per span,
joined to ``app``, ``title``, ``project``, ``mode``. This module rolls
those up across dimensions so questions like "how many keystrokes did
the operator type into claude-code vs codex during work on the sinex
project" are answerable in one call.

Adds a join with ``title_classification`` (via activity_title_usage
when present) to also expose keystroke volume by ``content_type``,
``topic_category``, ``activity``, ``attention_level``, ``platform``.

Construct-validity notes:
- **All keypresses are operator-originated.** Per the operator
  2026-05-27: ~100% of code-writing is done by AI agents, but the
  agent doesn't keylog as the operator. Even keypresses inside a
  claude-code or codex session are the operator typing prompts /
  steering / corrections. ``keypress_count`` is operator-direction
  volume, not agent-output volume.
- ``keypress_count`` per span is the raw key event count from
  scribe-tap when the span's app accepted keystrokes. Many apps strip
  modifiers / repeat keys; not all keystrokes equal one character.
- ``keylog_state`` indicates whether the span actually had a keylog
  bucket (``"covered"``) or not. Spans without keylog state are
  excluded from rollups to avoid the "zero typing because not measured"
  trap.
- Title-classification coverage is partial (~25-50% of titles in
  practice). Unclassified keystrokes are aggregated under the
  ``"unclassified"`` bucket per dimension so volumes balance.
- The ``ai_tool`` dimension recognizes claude-code via spinner-prefix
  in kitty titles (``✳``, ``⠐``, etc) since the title doesn't otherwise
  contain "claude". An earlier keyword-only classifier missed ~half
  the claude-code volume that way.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Iterator, Optional


@dataclass(frozen=True)
class KeystrokeRollup:
    """Keystroke totals across one dimension within a date window.

    ``dimension`` describes the axis (``"app"`` / ``"project"`` /
    ``"content_type"`` / etc); ``buckets`` maps value → keystroke count.
    ``unattributable`` is keystrokes that had keylog_state observed but
    no value for this dimension (e.g. project was unresolved); they're
    real keystrokes but absent the requested attribution.
    """

    dimension: str
    start: date
    end: date
    total_keystrokes: int
    buckets: dict[str, int]
    unattributable: int


def _iter_keyed_spans(
    start: date, end: date,
) -> Iterator[tuple[date, str, str, str, str, int]]:
    """Yield (date, app, title, project, mode, keypress_count) tuples
    for spans within [start, end) that have keylog observations.

    Spans crossing midnight are not split here — each is attributed to
    its start date. Keypress volume in a single span is small enough
    (typically seconds to minutes) that this is fine.
    """
    from ..sources.activitywatch import focus_spans
    from ..core.parse import local_tz

    tz = local_tz()
    span_start = datetime.combine(start, time.min, tzinfo=tz)
    span_end = datetime.combine(end, time.min, tzinfo=tz)
    for span in focus_spans(start=span_start, end=span_end):
        # Actual keylog_state values in aw-server-rust's keylog enrichment
        # are "covered" (capture is present for the span's time window) and
        # variants like "not_requested" / "missing". "covered" means the
        # keystroke total below is authoritative for this span.
        if span.keylog_state != "covered":
            continue
        if not span.keypress_count or span.keypress_count <= 0:
            continue
        yield (
            span.start.date(),
            span.app or "",
            span.title or "",
            span.project or "",
            span.mode or "",
            span.keypress_count,
        )


# Spinner / braille glyphs that prefix claude-code titles in kitty.
# Claude Code emits a rotating spinner (✳, ⠐, ⠂, ⠦, ⠧, ⠼, …) followed by
# either "Claude Code" (idle / starting) or the current task description.
# A keyword-only "claude" match misses these because the title is just
# "✳ Investigate suspicious data anomalies" — no string "claude" anywhere.
_CLAUDE_CODE_SPINNER = frozenset(
    "✳⠁⠂⠃⠄⠅⠆⠇⠈⠉⠊⠋⠌⠍⠎⠏⠐⠑⠒⠓⠔⠕⠖⠗⠘⠙⠚⠛⠜⠝⠞⠟"
    "⠠⠡⠢⠣⠤⠥⠦⠧⠨⠩⠪⠫⠬⠭⠮⠯⠰⠱⠲⠳⠴⠵⠶⠷⠸⠹⠺⠻⠼⠽⠾⠿"
    "✶✻✽"
)


def _classify_ai_tool(app: str, title: str) -> Optional[str]:
    """Best-effort classifier mapping (app, title) → AI tool name.

    Returns ``None`` for spans that don't look like operator-to-AI
    direction (raw shell, browser tab on a non-AI site, etc).

    The classifier is intentionally precise: when it doesn't recognize
    a tool, it returns ``None`` (caller groups into ``unattributable``)
    rather than guessing. Notable bucket: ``"in-project-kitty"`` for
    kitty sessions showing just a project name — these are usually
    still AI-direction surfaces (claude-code's title rewrites can lag
    behind focus changes) but the specific tool isn't determinable.
    """
    a = (app or "").lower()
    t = title or ""
    tl = t.lower()
    if a == "kitty" and t:
        if t[0] in _CLAUDE_CODE_SPINNER:
            return "claude-code"
        if "agent2" in tl:
            return "claude-code-agent2"
        if "codex" in tl:
            return "codex"
        if "hermes" in tl:
            return "hermes"
        if "aider" in tl:
            return "aider"
        if "gemini-cli" in tl or "gemini -" in tl:
            return "gemini-cli"
        # Bare project name in title — often still AI but unspecified.
        for proj in ("sinex", "polylogue", "lynchpin", "sinnix"):
            if proj in tl:
                return "in-project-kitty"
    if "claude.ai" in tl or "anthropic" in tl:
        return "claude.ai-web"
    if "chatgpt" in tl or ("openai" in tl and "playground" not in tl):
        return "chatgpt-web"
    if "ai studio" in tl or "aistudio.google" in tl:
        return "gemini-ai-studio"
    if "antigravity" in a or "antigravity" in tl:
        return "antigravity"
    return None


def _classification_for_title(
    app: str, title: str, classifications: dict[tuple[str, str], dict],
) -> dict:
    """Look up the title_classification row for (app, normalized_title).

    Falls back to {} if missing. The classifications map is built once
    by the caller from ``activity_title_usage`` so we don't pay a join
    per span.
    """
    from ..sources.activity_content import iter_activity_title_usage  # noqa: F401
    # Caller passes a pre-built map keyed by (app, title_hash) so this
    # function is just a dict lookup.
    return classifications.get((app, title), {})


def _build_title_classification_map(start: date, end: date) -> dict[tuple[str, str], dict]:
    """Build (app, title) -> classification dict.

    Uses ``activity_title_usage`` which has classification columns
    pre-populated. Spans whose title isn't in the map remain
    unclassified.
    """
    from ..sources.activity_content import iter_activity_title_usage

    out: dict[tuple[str, str], dict] = {}
    for row in iter_activity_title_usage(start=start, end=end):
        # The usage rows already have a normalized_title; we can match
        # on (app, normalized_title) by normalizing the span title too.
        out[(row.app, row.normalized_title)] = {
            "activity": row.activity,
            "content_type": row.content_type,
            "attention_level": row.attention_level,
            "topic_category": row.topic_category,
            "platform": row.platform,
        }
    return out


def keystrokes_by(
    *,
    dimension: str,
    start: date,
    end: date,
) -> KeystrokeRollup:
    """Roll up keystroke counts across one of:
    ``"app"``, ``"project"``, ``"mode"``, ``"activity"``,
    ``"content_type"``, ``"attention_level"``, ``"topic_category"``,
    ``"platform"``, ``"ai_tool"``.

    Returns a ``KeystrokeRollup`` whose ``buckets`` maps dimension
    value → keystroke count. ``unattributable`` counts keystrokes
    we observed but couldn't attribute (e.g., project unknown,
    classification missing).

    The ``ai_tool`` dimension classifies operator-to-AI direction
    surfaces: ``"claude-code"`` (spinner-prefixed kitty title),
    ``"codex"`` (``codex ...`` in kitty title), ``"chatgpt-web"``,
    ``"claude.ai-web"``, ``"hermes"``, ``"antigravity"``,
    ``"gemini-cli"``, ``"gemini-ai-studio"``,
    ``"in-project-kitty"`` (kitty showing a project name without
    a tool label — typically still AI-direction but tool unclear),
    and ``"none"`` for everything else (social/entertainment/shell).
    """
    valid = {
        "app", "project", "mode",
        "activity", "content_type", "attention_level",
        "topic_category", "platform",
        "ai_tool",
    }
    if dimension not in valid:
        raise ValueError(
            f"dimension must be one of {sorted(valid)}, got {dimension!r}"
        )

    needs_classification = dimension in {
        "activity", "content_type", "attention_level",
        "topic_category", "platform",
    }
    classification_map: dict[tuple[str, str], dict] = (
        _build_title_classification_map(start, end) if needs_classification else {}
    )

    # For title-classification we need to match by normalized_title.
    if needs_classification:
        from ..sources.title_metadata import normalize_title  # type: ignore[attr-defined]
        norm_fn = normalize_title
    else:
        norm_fn = None

    buckets: dict[str, int] = defaultdict(int)
    unattributable = 0
    total = 0
    for _d, app, title, project, mode, keys in _iter_keyed_spans(start, end):
        total += keys
        if dimension == "app":
            key = app or ""
        elif dimension == "project":
            key = project or ""
        elif dimension == "mode":
            key = mode or ""
        elif dimension == "ai_tool":
            key = _classify_ai_tool(app, title) or ""
        else:
            if norm_fn is None:
                key = ""
            else:
                normalized = norm_fn(app, title) if title else ""
                cls = classification_map.get((app, normalized), {})
                value = cls.get(dimension)
                key = str(value) if value else ""
        if not key:
            unattributable += keys
        else:
            buckets[key] += keys
    return KeystrokeRollup(
        dimension=dimension,
        start=start,
        end=end,
        total_keystrokes=total,
        buckets=dict(buckets),
        unattributable=unattributable,
    )


def keystrokes_daily(
    *,
    start: date,
    end: date,
    offline_hours_threshold: float = 2.0,
) -> list[dict]:
    """Per-day total keystrokes + presence/delegation classification.

    Returned rows:
    ``{"date", "keystrokes", "active_hours", "ai_spinner_spans",
      "is_offline", "had_autonomous_ai", "presence_pattern"}``.

    ``presence_pattern`` summarizes the day:
    - ``"online_active"``     — AW ≥ threshold AND keystrokes > 0
    - ``"online_passive"``    — AW ≥ threshold AND keystrokes == 0
                                (reading, watching, no typing)
    - ``"ai_delegated"``      — AW < threshold AND spinner-titled
                                spans > 0 (operator away, AI sessions
                                running, e.g. claude-code task left to
                                run while away)
    - ``"truly_offline"``     — AW < threshold AND no AI spinner spans
                                (nothing happening on the machine at all)

    The split between ``ai_delegated`` and ``truly_offline`` matters
    because the former still produces branch commits and AI work, while
    the latter produces nothing. Conflating them under a single
    ``is_offline`` flag was a methodology gap in the prior shape.
    """
    from ..sources.activitywatch import active_seconds_by_date, focus_spans
    from ..core.parse import local_tz
    from datetime import datetime, time as _time

    daily_keys: dict[date, int] = defaultdict(int)
    for d, _app, _title, _project, _mode, keys in _iter_keyed_spans(start, end):
        daily_keys[d] += keys

    daily_seconds = active_seconds_by_date(start=start, end=end)

    # Count spinner-prefixed kitty title-span events per day. These are
    # claude-code task indicators that fire even when the operator isn't
    # typing — high count + zero keystrokes = AI delegation.
    daily_spinner: dict[date, int] = defaultdict(int)
    tz = local_tz()
    span_start = datetime.combine(start, _time.min, tzinfo=tz)
    span_end = datetime.combine(end, _time.min, tzinfo=tz)
    for sp in focus_spans(start=span_start, end=span_end):
        if sp.app == "kitty" and sp.title and sp.title[0] in _CLAUDE_CODE_SPINNER:
            daily_spinner[sp.start.date()] += 1

    threshold_seconds = offline_hours_threshold * 3600
    out: list[dict] = []
    cur = start
    while cur < end:
        active_s = daily_seconds.get(cur, 0)
        keys = daily_keys.get(cur, 0)
        spinner = daily_spinner.get(cur, 0)
        is_off = active_s < threshold_seconds
        had_ai = spinner > 0
        if not is_off and keys > 0:
            pattern = "online_active"
        elif not is_off:
            pattern = "online_passive"
        elif had_ai:
            pattern = "ai_delegated"
        else:
            pattern = "truly_offline"
        out.append({
            "date": cur.isoformat(),
            "keystrokes": keys,
            "active_hours": round(active_s / 3600, 2),
            "ai_spinner_spans": spinner,
            "is_offline": is_off,
            "had_autonomous_ai": had_ai,
            "presence_pattern": pattern,
        })
        cur += timedelta(days=1)
    return out


@dataclass(frozen=True)
class AIToolAttributionComparison:
    """Comparison of two AI-tool attribution methods for a window.

    ``keystroke_buckets`` is what the AW-title classifier reports
    (operator-direction keystrokes per tool).
    ``polylogue_sessions`` is what polylogue's session archive reports
    (sessions actually run, regardless of operator title-rewrite).
    ``discrepancies`` lists tools where the two disagree by more than
    a heuristic threshold — typically because the operator's window
    title doesn't carry the tool name even though the tool was active.
    """
    start: date
    end: date
    keystroke_buckets: dict[str, int]   # tool -> total keystrokes
    polylogue_sessions: dict[str, int]  # tool -> session count
    discrepancies: list[dict]


def compare_ai_tool_attribution(*, start: date, end: date) -> AIToolAttributionComparison:
    """Compare AW-title vs polylogue session-archive AI-tool attribution.

    Title-based classification undercounts codex by ~99% because codex sessions
    typically don't rewrite the kitty title.

    Flags a discrepancy when polylogue reports ≥10 sessions for a tool
    in the window but the keystroke classifier shows 0 keystrokes for
    the same tool name. Mismatch direction matters; the classifier
    can also over-count if a window title incidentally matches a tool
    keyword.
    """
    import sqlite3
    from pathlib import Path

    # Keystroke side
    rollup = keystrokes_by(dimension="ai_tool", start=start, end=end)

    # Polylogue side — direct sqlite read.
    poly_db = Path("/realm/data/captures/polylogue/polylogue.db")
    polylogue_counts: dict[str, int] = {}
    if poly_db.exists():
        try:
            conn = sqlite3.connect(poly_db)
            rows = conn.execute("""
                SELECT source_name, COUNT(*)
                FROM conversations
                WHERE DATE(created_at) >= ? AND DATE(created_at) < ?
                GROUP BY source_name
            """, [start.isoformat(), end.isoformat()]).fetchall()
            polylogue_counts = dict(rows)
        finally:
            conn.close()

    # The two vocabularies don't share names. Map polylogue source_name
    # → classifier ai_tool name where they correspond, then look for gaps.
    poly_to_keystroke = {
        "claude-code": "claude-code",
        "codex": "codex",
        "hermes": "hermes",
        "antigravity": "antigravity",
        "gemini-cli": "gemini-cli",
    }

    discrepancies = []
    for poly_name, sess_count in polylogue_counts.items():
        keystroke_name = poly_to_keystroke.get(poly_name)
        if keystroke_name is None:
            continue
        keystrokes = rollup.buckets.get(keystroke_name, 0)
        # Heuristic: each polylogue session is roughly comparable to ~1,000
        # operator keystrokes (operator typing prompts/responses for ~minutes).
        # If keystrokes/session is < ~10, the classifier is almost certainly
        # missing this tool.
        if sess_count >= 10 and (keystrokes / max(sess_count, 1)) < 10:
            discrepancies.append({
                "tool": poly_name,
                "polylogue_sessions": sess_count,
                "keystroke_classifier": keystrokes,
                "keys_per_session": round(keystrokes / max(sess_count, 1), 1),
                "reason": (
                    f"polylogue saw {sess_count} sessions but the AW-title "
                    f"classifier found only {keystrokes} operator keystrokes "
                    f"(<10 keys/session). Likely cause: operator's sessions "
                    f"don't rewrite the kitty title."
                ),
            })

    return AIToolAttributionComparison(
        start=start,
        end=end,
        keystroke_buckets=rollup.buckets,
        polylogue_sessions=polylogue_counts,
        discrepancies=discrepancies,
    )
