"""Lossy physical-key candidates with character-level source references."""

from __future__ import annotations

from bisect import bisect_right
from datetime import datetime
from difflib import SequenceMatcher
import unicodedata
from typing import Any, Iterable, Sequence

from .input_activity import ContextSpan, aware, event_ref, input_kind
from lynchpin.sources.keylog import KeylogEvent


_SYMBOLS = {
    "KEY_SPACE": " ", "KEY_12": "-", "KEY_13": "=", "KEY_26": "[",
    "KEY_27": "]", "KEY_39": ";", "KEY_40": "'", "KEY_41": "`",
    "KEY_43": "\\", "KEY_51": ",", "KEY_52": ".", "KEY_53": "/",
    **{f"KEY_{i + 2}": str((i + 1) % 10) for i in range(10)},
}


def reconstruct_fragments(
    events: Iterable[KeylogEvent], contexts: Sequence[ContextSpan], *, max_gap_s: float = 120,
) -> list[dict[str, Any]]:
    """Produce candidate text, never a claim about final application contents.

    Missing releases prevent modifier reconstruction. Cursor operations, paste,
    context changes and capture restarts end fragments instead of guessing edits.
    """
    if max_gap_s <= 0:
        raise ValueError("max_gap_s must be positive")
    ordered = sorted(contexts, key=lambda c: c.start)
    starts = [c.start for c in ordered]
    fragments: list[dict[str, Any]] = []
    chars: list[dict[str, str]] = []
    current: tuple[str | None, str | None] | None = None
    previous: datetime | None = None
    flags: set[str] = set()

    def flush(reason: str) -> None:
        if chars:
            fragments.append(dict(
                id=f"fragment-{len(fragments)}", context_id=current[0] if current else None,
                text="".join(c["char"] for c in chars), characters=list(chars),
                start=chars[0]["ts"], end=chars[-1]["ts"], ended_by=reason,
                flags=sorted(flags | {"physical_key_candidate", "layout_assumed_us", "not_final_application_text"}),
            ))
        chars.clear()
        flags.clear()

    for ev in sorted(events, key=lambda e: e.ts):
        if ev.event != "press":
            if ev.event == "pointer_button_press":
                flush("pointer_click")
            continue
        ts = aware(ev.ts)
        index = bisect_right(starts, ts) - 1
        context_id = ordered[index].id if index >= 0 and ts < ordered[index].end else None
        identity = (context_id, ev.session)
        if identity != current or (previous is not None and (ts - previous).total_seconds() > max_gap_s):
            flush("context_session_or_gap")
        current, previous = identity, ts
        key = ev.keycode or ""
        kind = input_kind(ev)
        if not ev.modifier_state_known:
            flags.add("modifier_state_missing")
        unchanged_space = key == "KEY_SPACE" and kind == "nontext_key"
        if unchanged_space:
            flags.add("recorder_unchanged_space_candidate")
        if ev.has_clipboard or kind in {"shortcut_key", "navigation_key"} or (kind == "nontext_key" and not unchanged_space):
            flush("unmodelled_edit_or_shortcut")
            continue
        if kind == "modifier_key":
            flush("modifier_boundary")
            continue
        if key == "KEY_BACKSPACE":
            if chars:
                chars.pop()
                flags.add("backspace_assumed_one_character")
            else:
                flags.add("backspace_beyond_fragment")
            continue
        char = key[4:].lower() if len(key) == 5 and key[4:].isalpha() else _SYMBOLS.get(key)
        if char is None:
            flush("unmodelled_key")
            continue
        chars.append({"char": char, "ts": ts.isoformat(), "event_ref": event_ref(ev)})
    flush("end_of_input")
    return fragments


def _normalized(text: str) -> tuple[str, list[int]]:
    chars, positions = [], []
    for index, char in enumerate(text):
        # Matching ignores case/diacritics/punctuation, but keeps original offsets.
        for expanded in unicodedata.normalize("NFKD", char.lower().replace("ł", "l")):
            if expanded.isalnum():
                chars.append(expanded)
                positions.append(index)
    return "".join(chars), positions


def match_anchors(
    anchors: Sequence[dict[str, Any]], fragments: Sequence[dict[str, Any]], *,
    lookback_s: float = 1200, minimum_chars: int = 24,
) -> list[dict[str, Any]]:
    """Match explicit human messages only to event-backed preceding substrings.

    Matches annotate typing evidence. They do not label an entire focus span or
    agent runtime. Repeated matches retain ambiguity rather than picking a winner.
    """
    if lookback_s <= 0 or minimum_chars < 1:
        raise ValueError("matching bounds must be positive")
    result = []
    for anchor in anchors:
        if not anchor.get("id") or not anchor.get("source_ref") or not anchor.get("timestamp"):
            raise ValueError("anchors require id, source_ref and timestamp")
        timestamp = aware(datetime.fromisoformat(anchor["timestamp"].replace("Z", "+00:00")))
        expected, _ = _normalized(anchor["text"])
        candidates = []
        for fragment in fragments:
            ending = aware(datetime.fromisoformat(fragment["end"]))
            beginning = aware(datetime.fromisoformat(fragment["start"]))
            if ending > timestamp or (timestamp - beginning).total_seconds() > lookback_s:
                continue
            candidate, offsets = _normalized(fragment["text"])
            block = SequenceMatcher(None, expected, candidate, autojunk=False).find_longest_match()
            if block.size < minimum_chars:
                continue
            first, last = offsets[block.b], offsets[block.b + block.size - 1]
            evidence = fragment["characters"][first:last + 1]
            candidates.append(dict(
                fragment_id=fragment["id"], context_id=fragment["context_id"],
                normalized_matching_characters=block.size,
                message_fraction=block.size / len(expected),
                match_type="near_complete" if block.size / len(expected) >= .8 else "partial",
                start=evidence[0]["ts"], end=evidence[-1]["ts"],
                first_event_ref=evidence[0]["event_ref"], last_event_ref=evidence[-1]["event_ref"],
            ))
        result.append(dict(
            anchor_id=anchor["id"], source_ref=anchor["source_ref"], matches=candidates,
            ambiguous=len(candidates) > 1, attribution_scope="matched_input_only",
        ))
    return result
