from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass, field
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

def _iter_keyed_spans(start: date, end: date) -> Iterator[tuple[date, str, str, str, str, int]]:
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
        if span.keylog_state != 'covered':
            continue
        if not span.keypress_count or span.keypress_count <= 0:
            continue
        yield (span.start.date(), span.app or '', span.title or '', span.project or '', span.mode or '', span.keypress_count)
_CLAUDE_CODE_SPINNER = frozenset('✳⠁⠂⠃⠄⠅⠆⠇⠈⠉⠊⠋⠌⠍⠎⠏⠐⠑⠒⠓⠔⠕⠖⠗⠘⠙⠚⠛⠜⠝⠞⠟⠠⠡⠢⠣⠤⠥⠦⠧⠨⠩⠪⠫⠬⠭⠮⠯⠰⠱⠲⠳⠴⠵⠶⠷⠸⠹⠺⠻⠼⠽⠾⠿✶✻✽')

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
    a = (app or '').lower()
    t = title or ''
    tl = t.lower()
    if a == 'kitty' and t:
        if t[0] in _CLAUDE_CODE_SPINNER:
            return 'claude-code'
        if 'agent2' in tl:
            return 'claude-code-agent2'
        if 'codex' in tl:
            return 'codex'
        if 'hermes' in tl:
            return 'hermes'
        if 'aider' in tl:
            return 'aider'
        if 'gemini-cli' in tl or 'gemini -' in tl:
            return 'gemini-cli'
        for proj in ('sinex', 'polylogue', 'lynchpin', 'sinnix'):
            if proj in tl:
                return 'in-project-kitty'
    if 'claude.ai' in tl or 'anthropic' in tl:
        return 'claude.ai-web'
    if 'chatgpt' in tl or ('openai' in tl and 'playground' not in tl):
        return 'chatgpt-web'
    if 'ai studio' in tl or 'aistudio.google' in tl:
        return 'gemini-ai-studio'
    if 'antigravity' in a or 'antigravity' in tl:
        return 'antigravity'
    return None

def _classification_for_title(app: str, title: str, classifications: dict[tuple[str, str], dict]) -> dict:
    """Look up the title_classification row for (app, normalized_title).

    Falls back to {} if missing. The classifications map is built once
    by the caller from ``activity_title_usage`` so we don't pay a join
    per span.
    """
    from ..sources.activity_content import iter_activity_title_usage
    return classifications.get((app, title), {})

def _build_title_classification_map() -> dict[tuple[str, str], dict]:
    """Build (app, title) -> classification dict.

    Uses ``activity_title_usage`` which has classification columns
    pre-populated. Spans whose title isn't in the map remain
    unclassified.
    """
    from ..sources.activity_content import iter_activity_title_usage
    from ..sources.title_metadata import normalize_title
    out: dict[tuple[str, str], dict] = {}
    for row in iter_activity_title_usage():
        out[row.app, row.normalized_title] = {'activity': row.activity, 'content_type': row.content_type, 'attention_level': row.attention_level, 'topic_category': row.topic_category, 'platform': row.platform}
    return out

def keystrokes_by(*, dimension: str, start: date, end: date) -> KeystrokeRollup:
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
    valid = {'app', 'project', 'mode', 'activity', 'content_type', 'attention_level', 'topic_category', 'platform', 'ai_tool'}
    if dimension not in valid:
        raise ValueError(f'dimension must be one of {sorted(valid)}, got {dimension!r}')
    needs_classification = dimension in {'activity', 'content_type', 'attention_level', 'topic_category', 'platform'}
    classification_map: dict[tuple[str, str], dict] = _build_title_classification_map() if needs_classification else {}
    if needs_classification:
        from ..sources.title_metadata import normalize_title
        norm_fn = normalize_title
    else:
        norm_fn = None
    buckets: dict[str, int] = defaultdict(int)
    unattributable = 0
    total = 0
    for _d, app, title, project, mode, keys in _iter_keyed_spans(start, end):
        total += keys
        if dimension == 'app':
            key = app or ''
        elif dimension == 'project':
            key = project or ''
        elif dimension == 'mode':
            key = mode or ''
        elif dimension == 'ai_tool':
            key = _classify_ai_tool(app, title) or ''
        elif norm_fn is None:
            key = ''
        else:
            normalized = norm_fn(app, title) if title else ''
            cls = classification_map.get((app, normalized), {})
            value = cls.get(dimension)
            key = str(value) if value else ''
        if not key:
            unattributable += keys
        else:
            buckets[key] += keys
    return KeystrokeRollup(dimension=dimension, start=start, end=end, total_keystrokes=total, buckets=dict(buckets), unattributable=unattributable)

def keystrokes_daily(*, start: date, end: date, offline_hours_threshold: float=2.0) -> list[dict]:
    from ..sources.activitywatch import active_seconds_by_date, focus_spans
    from ..core.parse import local_tz
    from datetime import datetime, time as _time
    daily_keys: dict[date, int] = defaultdict(int)
    for d, _app, _title, _project, _mode, keys in _iter_keyed_spans(start, end):
        daily_keys[d] += keys
    daily_seconds = active_seconds_by_date(start=start, end=end)
    daily_spinner: dict[date, int] = defaultdict(int)
    tz = local_tz()
    span_start = datetime.combine(start, _time.min, tzinfo=tz)
    span_end = datetime.combine(end, _time.min, tzinfo=tz)
    for sp in focus_spans(start=span_start, end=span_end):
        if sp.app == 'kitty' and sp.title and (sp.title[0] in _CLAUDE_CODE_SPINNER):
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
            pattern = 'online_active'
        elif not is_off:
            pattern = 'online_passive'
        elif had_ai:
            pattern = 'ai_delegated'
        else:
            pattern = 'truly_offline'
        out.append({'date': cur.isoformat(), 'keystrokes': keys, 'active_hours': round(active_s / 3600, 2), 'ai_spinner_spans': spinner, 'is_offline': is_off, 'had_autonomous_ai': had_ai, 'presence_pattern': pattern})
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
    keystroke_buckets: dict[str, int]
    polylogue_sessions: dict[str, int]
    discrepancies: list[dict]

def compare_ai_tool_attribution(*, start: date, end: date) -> AIToolAttributionComparison:
    import sqlite3
    from pathlib import Path
    rollup = keystrokes_by(dimension='ai_tool', start=start, end=end)
    poly_db = Path('/realm/data/captures/polylogue/polylogue.db')
    polylogue_counts: dict[str, int] = {}
    if poly_db.exists():
        try:
            conn = sqlite3.connect(poly_db)
            rows = conn.execute('\n                SELECT source_name, COUNT(*)\n                FROM conversations\n                WHERE DATE(created_at) >= ? AND DATE(created_at) < ?\n                GROUP BY source_name\n            ', [start.isoformat(), end.isoformat()]).fetchall()
            polylogue_counts = dict(rows)
        finally:
            conn.close()
    poly_to_keystroke = {'claude-code': 'claude-code', 'codex': 'codex', 'hermes': 'hermes', 'antigravity': 'antigravity', 'gemini-cli': 'gemini-cli'}
    discrepancies = []
    for poly_name, sess_count in polylogue_counts.items():
        keystroke_name = poly_to_keystroke.get(poly_name)
        if keystroke_name is None:
            continue
        keystrokes = rollup.buckets.get(keystroke_name, 0)
        if sess_count >= 10 and keystrokes / max(sess_count, 1) < 10:
            discrepancies.append({'tool': poly_name, 'polylogue_sessions': sess_count, 'keystroke_classifier': keystrokes, 'keys_per_session': round(keystrokes / max(sess_count, 1), 1), 'reason': f"polylogue saw {sess_count} sessions but the AW-title classifier found only {keystrokes} operator keystrokes (<10 keys/session). Likely cause: operator's sessions don't rewrite the kitty title."})
    return AIToolAttributionComparison(start=start, end=end, keystroke_buckets=rollup.buckets, polylogue_sessions=polylogue_counts, discrepancies=discrepancies)
