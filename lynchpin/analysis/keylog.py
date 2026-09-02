"""Higher-level keylog analysis.

Keybind usage, text-shape metadata, and text-content metrics are separate
products so callers can choose the level of detail they need.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from lynchpin.core.errors import MaterializationError
from lynchpin.core.io import (
    latest_mtime_iso,
    load_json,
    resolve_analysis_path,
    save_json,
)
from lynchpin.core.primitives import date_to_dt_range, logical_date
from lynchpin.materializers.partition_store import (
    ArtifactStore,
    ProductPartitionKey,
    deterministic_input_digest,
)
from lynchpin.sources import keylog

DEFAULT_HYPRLAND_BINDINGS = Path(
    "/realm/project/sinnix/modules/features/desktop/hyprland/bindings.nix"
)

MODIFIER_KEYCODES = {
    "KEY_LEFTMETA": "SUPER",
    "KEY_RIGHTMETA": "SUPER",
    "KEY_125": "SUPER",
    "KEY_126": "SUPER",
    "KEY_LEFTSHIFT": "SHIFT",
    "KEY_RIGHTSHIFT": "SHIFT",
    "KEY_42": "SHIFT",
    "KEY_54": "SHIFT",
    "KEY_LEFTCTRL": "CTRL",
    "KEY_RIGHTCTRL": "CTRL",
    "KEY_29": "CTRL",
    "KEY_97": "CTRL",
    "KEY_LEFTALT": "ALT",
    "KEY_RIGHTALT": "ALT",
    "KEY_56": "ALT",
    "KEY_100": "ALT",
}

SPECIAL_KEY_ALIASES = {
    "RETURN": "KEY_ENTER",
    "ENTER": "KEY_ENTER",
    "ESCAPE": "KEY_ESC",
    "ESC": "KEY_ESC",
    "SPACE": "KEY_SPACE",
    "TAB": "KEY_TAB",
    "GRAVE": "KEY_GRAVE",
    "PRINT": "KEY_PRINT",
    "PERIOD": "KEY_DOT",
    "COMMA": "KEY_COMMA",
    "KP_LEFT": "KEY_KP4",
    "KP_BEGIN": "KEY_KP5",
    "KP_RIGHT": "KEY_KP6",
    "KP_HOME": "KEY_KP7",
    "KP_UP": "KEY_KP8",
    "KP_PRIOR": "KEY_KP9",
}

TEXT_SHAPE_KEYS = {
    "KEY_BACKSPACE": "backspace",
    "KEY_ENTER": "enter",
    "KEY_KPENTER": "enter",
    "KEY_TAB": "tab",
    "KEY_SPACE": "space",
}

_MAX_INPUT_STABILITY_ATTEMPTS = 3


@dataclass(frozen=True)
class HyprlandKeybind:
    chord: str
    modifiers: tuple[str, ...]
    key: str
    dispatcher: str
    argument: str
    family: str
    source: str

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class KeybindUse:
    date: date
    chord: str
    dispatcher: str
    argument: str
    family: str
    count: int
    confidence: str

    def to_json(self) -> dict[str, Any]:
        row = asdict(self)
        row["date"] = self.date.isoformat()
        return row


@dataclass(frozen=True)
class KeybindSummary:
    chord: str
    dispatcher: str
    argument: str
    family: str
    total_count: int
    active_days: int
    first_date: date
    last_date: date

    def to_json(self) -> dict[str, Any]:
        row = asdict(self)
        row["first_date"] = self.first_date.isoformat()
        row["last_date"] = self.last_date.isoformat()
        return row


@dataclass(frozen=True)
class KeybindTemporalBucket:
    chord: str
    dispatcher: str
    argument: str
    family: str
    weekday: int
    hour: int
    count: int

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class KeybindFamilySummary:
    family: str
    total_count: int
    unique_chords: int
    active_days: int
    first_date: date
    last_date: date

    def to_json(self) -> dict[str, Any]:
        row = asdict(self)
        row["first_date"] = self.first_date.isoformat()
        row["last_date"] = self.last_date.isoformat()
        return row


@dataclass(frozen=True)
class KeylogTextShapeDay:
    date: date
    keypress_count: int
    changed_keypress_count: int
    commandish_keypress_count: int
    backspace_count: int
    enter_count: int
    tab_count: int
    space_count: int

    def to_json(self) -> dict[str, Any]:
        row = asdict(self)
        row["date"] = self.date.isoformat()
        return row


@dataclass(frozen=True)
class KeylogTextTerm:
    term: str
    count: int

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class KeylogTextContentDay:
    date: date
    snapshot_count: int
    char_count: int
    word_count: int
    line_count: int

    def to_json(self) -> dict[str, Any]:
        row = asdict(self)
        row["date"] = self.date.isoformat()
        return row


@dataclass(frozen=True)
class KeylogTextContentAnalysis:
    start: date
    end: date
    snapshot_count: int
    char_count: int
    word_count: int
    line_count: int
    days: tuple[KeylogTextContentDay, ...]
    top_terms: tuple[KeylogTextTerm, ...]
    caveats: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "snapshot_count": self.snapshot_count,
            "char_count": self.char_count,
            "word_count": self.word_count,
            "line_count": self.line_count,
            "days": [row.to_json() for row in self.days],
            "top_terms": [row.to_json() for row in self.top_terms],
            "caveats": list(self.caveats),
        }


@dataclass(frozen=True)
class KeylogAnalysis:
    start: date
    end: date
    source_event_count: int
    keypress_count: int
    matched_keybind_count: int
    keybinds: tuple[HyprlandKeybind, ...]
    keybind_usage: tuple[KeybindUse, ...]
    keybind_summaries: tuple[KeybindSummary, ...]
    keybind_family_summaries: tuple[KeybindFamilySummary, ...]
    keybind_temporal_buckets: tuple[KeybindTemporalBucket, ...]
    text_shape_days: tuple[KeylogTextShapeDay, ...]
    caveats: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "source_event_count": self.source_event_count,
            "keypress_count": self.keypress_count,
            "matched_keybind_count": self.matched_keybind_count,
            "keybinds": [row.to_json() for row in self.keybinds],
            "keybind_usage": [row.to_json() for row in self.keybind_usage],
            "keybind_summaries": [row.to_json() for row in self.keybind_summaries],
            "keybind_family_summaries": [row.to_json() for row in self.keybind_family_summaries],
            "keybind_temporal_buckets": [row.to_json() for row in self.keybind_temporal_buckets],
            "text_shape_days": [row.to_json() for row in self.text_shape_days],
            "caveats": list(self.caveats),
        }


def parse_hyprland_keybinds(path: Path = DEFAULT_HYPRLAND_BINDINGS) -> tuple[HyprlandKeybind, ...]:
    """Parse simple Hyprland bind strings from the Sinnix Nix module."""

    if not path.exists():
        return ()
    rows: list[HyprlandKeybind] = []
    for raw in _quoted_bind_lines(path.read_text(encoding="utf-8")):
        parts = [part.strip() for part in raw.split(",", 3)]
        if len(parts) < 3:
            continue
        modifiers_raw, key_raw, dispatcher = parts[:3]
        argument = parts[3] if len(parts) >= 4 else ""
        key = _normalize_bind_key(key_raw)
        if key is None or key.startswith("mouse:"):
            continue
        modifiers = _normalize_modifiers(modifiers_raw)
        family = _classify_keybind_family(dispatcher, argument, key)
        rows.append(
            HyprlandKeybind(
                chord=_chord(modifiers, key),
                modifiers=modifiers,
                key=key,
                dispatcher=dispatcher,
                argument=argument,
                family=family,
                source=str(path),
            )
        )
    return tuple(rows)


def _quoted_bind_lines(text: str) -> tuple[str, ...]:
    rows = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "#":
            next_newline = text.find("\n", i)
            if next_newline == -1:
                break
            i = next_newline + 1
            continue
        if ch != '"':
            i += 1
            continue

        i += 1
        start = i
        interpolation_depth = 0
        while i < n:
            if text[i] == "\\":
                i += 2
                continue
            if interpolation_depth == 0 and text.startswith("${", i):
                interpolation_depth = 1
                i += 2
                continue
            if interpolation_depth > 0:
                if text.startswith("${", i):
                    interpolation_depth += 1
                    i += 2
                    continue
                if text[i] == "}":
                    interpolation_depth -= 1
                i += 1
                continue
            if text[i] == '"':
                raw = text[start:i]
                if raw.count(",") >= 2:
                    rows.append(raw)
                i += 1
                break
            i += 1
    return tuple(rows)


@dataclass(frozen=True)
class _DayCounters:
    """Per-logical-day accumulation of every keylog analysis measure.

    Each measure is additive across days, so a window's analysis is the sum of
    its days. That is what lets an incremental refresh rescan only the days
    whose raw inputs changed.
    """

    source_event_count: int = 0
    usage: Counter[tuple[str, str]] = field(default_factory=Counter)
    temporal: Counter[tuple[str, int, int]] = field(default_factory=Counter)
    shape: Counter[str] = field(default_factory=Counter)
    text: Counter[str] = field(default_factory=Counter)
    text_terms: Counter[str] = field(default_factory=Counter)

    def to_json(self) -> dict[str, Any]:
        return {
            "source_event_count": self.source_event_count,
            "usage": [[chord, confidence, count] for (chord, confidence), count in sorted(self.usage.items())],
            "temporal": [[chord, weekday, hour, count] for (chord, weekday, hour), count in sorted(self.temporal.items())],
            "shape": dict(sorted(self.shape.items())),
            "text": dict(sorted(self.text.items())),
            "text_terms": dict(sorted(self.text_terms.items())),
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> _DayCounters:
        return cls(
            source_event_count=int(payload.get("source_event_count") or 0),
            usage=Counter({(str(row[0]), str(row[1])): int(row[2]) for row in payload.get("usage", [])}),
            temporal=Counter({(str(row[0]), int(row[1]), int(row[2])): int(row[3]) for row in payload.get("temporal", [])}),
            shape=Counter({str(k): int(v) for k, v in (payload.get("shape") or {}).items()}),
            text=Counter({str(k): int(v) for k, v in (payload.get("text") or {}).items()}),
            text_terms=Counter({str(k): int(v) for k, v in (payload.get("text_terms") or {}).items()}),
        )


def _scan_day_counters(
    *,
    start: date,
    end: date,
    by_chord: dict[str, HyprlandKeybind],
    chord_window_ms: int,
) -> dict[date, _DayCounters]:
    """Stream the raw day files once, accumulating measures per logical day."""

    start_dt, end_dt = date_to_dt_range(start, end)
    usage_by_day: dict[date, Counter[tuple[str, str]]] = defaultdict(Counter)
    temporal_by_day: dict[date, Counter[tuple[str, int, int]]] = defaultdict(Counter)
    shape_by_day: dict[date, Counter[str]] = defaultdict(Counter)
    text_by_day: dict[date, Counter[str]] = defaultdict(Counter)
    terms_by_day: dict[date, Counter[str]] = defaultdict(Counter)
    events_by_day: Counter[date] = Counter()

    recent_modifiers: dict[str, datetime] = {}
    chord_window = timedelta(milliseconds=chord_window_ms)
    # Keylog files are append-only daily JSONL products, and ``keylog.events``
    # yields them in UTC filename order. Process the stream directly so the
    # analysis does not retain and sort millions of press events at once.
    for event in keylog._analysis_records(start=start_dt, end=end_dt):
        day = logical_date(event.ts)
        if event.text is not None:
            text_by_day[day]["snapshots"] += 1
            text_by_day[day]["chars"] += len(event.text)
            words = _content_terms(event.text)
            text_by_day[day]["words"] += len(words)
            text_by_day[day]["lines"] += event.text.count("\n") + 1
            terms_by_day[day].update(words)
        if event.event != "press":
            continue
        events_by_day[day] += 1
        key = _normalize_event_key(event.keycode)
        if key is None:
            continue
        shape_by_day[day]["keypress"] += 1
        if event.changed is True:
            shape_by_day[day]["changed"] += 1
        else:
            shape_by_day[day]["commandish"] += 1
        if key in TEXT_SHAPE_KEYS:
            shape_by_day[day][TEXT_SHAPE_KEYS[key]] += 1

        modifier = MODIFIER_KEYCODES.get(key)
        if modifier is not None:
            recent_modifiers[modifier] = event.ts
            continue

        exact_modifiers = tuple(sorted(event.modifiers))
        if exact_modifiers:
            chord = _chord(exact_modifiers, key)
            if chord in by_chord:
                usage_by_day[day][(chord, "exact_modifier_state")] += 1
                temporal_by_day[day][(chord, event.ts.weekday(), event.ts.hour)] += 1
                continue

        active_modifiers = tuple(
            sorted(
                name
                for name, ts in recent_modifiers.items()
                if event.ts - ts <= chord_window
            )
        )
        if not active_modifiers:
            continue
        chord = _chord(active_modifiers, key)
        if chord in by_chord:
            usage_by_day[day][(chord, "inferred_adjacent_modifier_press")] += 1
            temporal_by_day[day][(chord, event.ts.weekday(), event.ts.hour)] += 1

    days = set(usage_by_day) | set(temporal_by_day) | set(shape_by_day)
    days |= set(text_by_day) | set(terms_by_day) | set(events_by_day)
    return {
        day: _DayCounters(
            source_event_count=events_by_day[day],
            usage=usage_by_day[day],
            temporal=temporal_by_day[day],
            shape=shape_by_day[day],
            text=text_by_day[day],
            text_terms=terms_by_day[day],
        )
        for day in days
    }


def _compose_analysis(
    *,
    start: date,
    end: date,
    bind_rows: tuple[HyprlandKeybind, ...],
    by_chord: dict[str, HyprlandKeybind],
    day_counters: dict[date, _DayCounters],
    text_top_n: int,
) -> tuple[KeylogAnalysis, KeylogTextContentAnalysis]:
    """Aggregate per-day counters into the window's two analysis products."""

    usage_counter: Counter[tuple[date, str, str]] = Counter()
    temporal_counter: Counter[tuple[str, int, int]] = Counter()
    shape_by_day: dict[date, Counter[str]] = defaultdict(Counter)
    text_by_day: dict[date, Counter[str]] = defaultdict(Counter)
    text_terms: Counter[str] = Counter()
    source_event_count = 0

    for day, counters in day_counters.items():
        source_event_count += counters.source_event_count
        for (chord, confidence), count in counters.usage.items():
            usage_counter[(day, chord, confidence)] += count
        temporal_counter.update(counters.temporal)
        if counters.shape:
            shape_by_day[day].update(counters.shape)
        if counters.text:
            text_by_day[day].update(counters.text)
        text_terms.update(counters.text_terms)

    usage = tuple(
        KeybindUse(
            date=day,
            chord=chord,
            dispatcher=by_chord[chord].dispatcher,
            argument=by_chord[chord].argument,
            family=by_chord[chord].family,
            count=count,
            confidence=confidence,
        )
        for (day, chord, confidence), count in sorted(
            usage_counter.items(),
            key=lambda item: (item[0][0], item[1], item[0][1], item[0][2]),
        )
    )
    keybind_summaries = _keybind_summaries(usage, by_chord)
    keybind_family_summaries = _keybind_family_summaries(usage)
    keybind_temporal_buckets = _keybind_temporal_buckets(temporal_counter, by_chord)
    shape_days = tuple(
        KeylogTextShapeDay(
            date=day,
            keypress_count=counts["keypress"],
            changed_keypress_count=counts["changed"],
            commandish_keypress_count=counts["commandish"],
            backspace_count=counts["backspace"],
            enter_count=counts["enter"],
            tab_count=counts["tab"],
            space_count=counts["space"],
        )
        for day, counts in sorted(shape_by_day.items())
    )
    analysis = KeylogAnalysis(
        start=start,
        end=end,
        source_event_count=source_event_count,
        keypress_count=source_event_count,
        matched_keybind_count=sum(row.count for row in usage),
        keybinds=bind_rows,
        keybind_usage=usage,
        keybind_summaries=keybind_summaries,
        keybind_family_summaries=keybind_family_summaries,
        keybind_temporal_buckets=keybind_temporal_buckets,
        text_shape_days=shape_days,
        caveats=(
            "keybind and text-shape metadata are separate from text-content analysis",
            "keybind usage prefers persisted modifier state when present",
            "keybind usage falls back to adjacent modifier keypresses within the chord window",
            "adjacent-modifier inference does not carry across the logical day boundary",
        ),
    )
    text_content_days = tuple(
        KeylogTextContentDay(
            date=day,
            snapshot_count=counts["snapshots"],
            char_count=counts["chars"],
            word_count=counts["words"],
            line_count=counts["lines"],
        )
        for day, counts in sorted(text_by_day.items())
    )
    text_content = KeylogTextContentAnalysis(
        start=start,
        end=end,
        snapshot_count=sum(row.snapshot_count for row in text_content_days),
        char_count=sum(row.char_count for row in text_content_days),
        word_count=sum(row.word_count for row in text_content_days),
        line_count=sum(row.line_count for row in text_content_days),
        days=text_content_days,
        top_terms=tuple(
            KeylogTextTerm(term=term, count=count)
            for term, count in sorted(text_terms.items(), key=lambda item: (-item[1], item[0]))[
                : max(0, text_top_n)
            ]
        ),
        caveats=(
            "text-content analysis only uses explicit snapshot text fields",
            "current captures may contain no snapshot text, yielding zero rows",
        ),
    )
    return analysis, text_content


def _analyze_keylog_bundle(
    *,
    start: date,
    end: date,
    bindings_path: Path = DEFAULT_HYPRLAND_BINDINGS,
    chord_window_ms: int = 1500,
    text_top_n: int = 1000,
) -> tuple[KeylogAnalysis, KeylogTextContentAnalysis]:
    """Analyze metadata and optional text in one pass over the raw day files."""

    bind_rows = parse_hyprland_keybinds(bindings_path)
    by_chord = {row.chord: row for row in bind_rows}
    day_counters = _scan_day_counters(
        start=start, end=end, by_chord=by_chord, chord_window_ms=chord_window_ms
    )
    return _compose_analysis(
        start=start,
        end=end,
        bind_rows=bind_rows,
        by_chord=by_chord,
        day_counters=day_counters,
        text_top_n=text_top_n,
    )


def analyze_keylog(
    *,
    start: date,
    end: date,
    bindings_path: Path = DEFAULT_HYPRLAND_BINDINGS,
    chord_window_ms: int = 1500,
) -> KeylogAnalysis:
    """Analyze keylog metadata over an inclusive date window."""

    analysis, _text_content = _analyze_keylog_bundle(
        start=start,
        end=end,
        bindings_path=bindings_path,
        chord_window_ms=chord_window_ms,
    )
    return analysis


def analyze_keylog_text_content(
    *,
    start: date,
    end: date,
    top_n: int = 25,
) -> KeylogTextContentAnalysis:
    """Analyze explicit keylog snapshot text when capture records include it."""

    start_dt, end_dt = date_to_dt_range(start, end)
    by_day: dict[date, Counter[str]] = defaultdict(Counter)
    terms: Counter[str] = Counter()
    for snapshot in keylog.text_snapshots(start=start_dt, end=end_dt):
        day = logical_date(snapshot.ts)
        text = snapshot.text
        by_day[day]["snapshots"] += 1
        by_day[day]["chars"] += len(text)
        words = _content_terms(text)
        by_day[day]["words"] += len(words)
        by_day[day]["lines"] += text.count("\n") + 1
        terms.update(words)
    days = tuple(
        KeylogTextContentDay(
            date=day,
            snapshot_count=counts["snapshots"],
            char_count=counts["chars"],
            word_count=counts["words"],
            line_count=counts["lines"],
        )
        for day, counts in sorted(by_day.items())
    )
    top_terms = tuple(KeylogTextTerm(term=term, count=count) for term, count in terms.most_common(max(0, top_n)))
    return KeylogTextContentAnalysis(
        start=start,
        end=end,
        snapshot_count=sum(row.snapshot_count for row in days),
        char_count=sum(row.char_count for row in days),
        word_count=sum(row.word_count for row in days),
        line_count=sum(row.line_count for row in days),
        days=days,
        top_terms=top_terms,
        caveats=(
            "text-content analysis only uses explicit snapshot text fields",
            "current captures may contain no snapshot text, yielding zero rows",
        ),
    )


def _content_terms(text: str) -> tuple[str, ...]:
    return tuple(term.lower() for term in re.findall(r"\b[^\W_]{2,}\b", text, flags=re.UNICODE))


def write_keylog_analysis(
    out: Path | None = None,
    *,
    start: date,
    end: date,
    bindings_path: Path = DEFAULT_HYPRLAND_BINDINGS,
) -> KeylogAnalysis:
    target = out or Path(resolve_analysis_path("keylog_analysis.json"))
    store = ArtifactStore(target.with_name(f".{target.stem}.partitions"))
    if not store.manifest_path.exists() and target.exists():
        legacy = load_json(target)
        if isinstance(legacy, dict) and legacy.get("start") and legacy.get("end"):
            migrated: dict[ProductPartitionKey, Any] = {}
            for day in _date_range(date.fromisoformat(str(legacy["start"])), date.fromisoformat(str(legacy["end"]))):
                day_payload = _daily_payload(legacy, day)
                key = ProductPartitionKey.day("keylog.analysis", day)
                migrated[key] = store.put(
                    key, (json.dumps(day_payload, ensure_ascii=False, sort_keys=True) + "\n").encode(),
                    format="ndjson", row_count=1, first_date=day, last_date=day, publish=False,
                )
            if migrated:
                store.publish(migrated, metadata={"migration": "legacy-monolith", "validated": True})

    cache_path = target.with_name(f".{target.stem}.day-counters.json")
    analysis: KeylogAnalysis
    text_content: KeylogTextContentAnalysis
    for _attempt in range(_MAX_INPUT_STABILITY_ATTEMPTS):
        input_files = _analysis_input_files(start=start, end=end, bindings_path=bindings_path)
        input_signature = _input_signature(input_files)
        if store.selection_is_readable() and store.metadata.get("input_signature") == input_signature and target.exists():
            payload = load_json(target)
            if isinstance(payload, dict):
                return _analysis_from_payload(payload)
        bind_rows = parse_hyprland_keybinds(bindings_path)
        by_chord = {row.chord: row for row in bind_rows}
        analysis, text_content = _compose_analysis(
            start=start,
            end=end,
            bind_rows=bind_rows,
            by_chord=by_chord,
            day_counters=_windowed_day_counters(
                start=start,
                end=end,
                bindings_path=bindings_path,
                by_chord=by_chord,
                chord_window_ms=1500,
                cache_path=cache_path,
            ),
            text_top_n=1000,
        )
        current_files = _analysis_input_files(start=start, end=end, bindings_path=bindings_path)
        if _input_signature(current_files) == input_signature:
            break
    else:
        raise MaterializationError(
            "keylog_analysis",
            reason="keylog or binding inputs changed throughout the bounded refresh stability window",
        )

    payload = analysis.to_json()
    payload.update(
        {
            "dataset": "lynchpin.keylog_analysis",
            "schema_version": 1,
            "input_files": [str(path) for path in input_files],
            "input_file_count": len(input_files),
            "input_latest_mtime": latest_mtime_iso(input_files),
        }
    )
    payload["text_content"] = text_content.to_json()
    save_json(target, payload, sort_keys=True)
    selected = store.logical_partitions()
    for day in _date_range(start, end):
        key = ProductPartitionKey.day("keylog.analysis", day)
        selected.pop(key, None)
    for day in _date_range(start, end):
        day_payload = _daily_payload(payload, day)
        key = ProductPartitionKey.day("keylog.analysis", day)
        selected[key] = store.put(
            key,
            (json.dumps(day_payload, ensure_ascii=False, sort_keys=True) + "\n").encode(),
            format="ndjson", input_digest=input_signature, row_count=1,
            first_date=day, last_date=day, publish=False,
        )
    store.publish(selected, metadata={"dataset": "lynchpin.keylog_analysis", "input_signature": input_signature})
    return analysis


_DAY_COUNTER_CACHE_SCHEMA = "lynchpin.keylog-day-counters.v1"


def _day_input_signature(day: date, bindings_path: Path) -> str:
    """Signature of every raw file a single logical day's counters depend on.

    ``keylog._candidate_files`` pads by one day on each side because files are
    named by UTC date while the logical day is local, so a day's counters can
    change when any of those three files changes.
    """
    inputs = list(
        keylog.log_files(start=day - timedelta(days=1), end=day + timedelta(days=1), ensure=False)
    )
    if bindings_path.exists():
        inputs.append(bindings_path)
    return _input_signature(tuple(sorted(dict.fromkeys(inputs))))


def _load_day_counter_cache(path: Path, chord_window_ms: int) -> dict[date, tuple[str, _DayCounters]]:
    if not path.exists():
        return {}
    try:
        payload = load_json(path)
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    if payload.get("schema") != _DAY_COUNTER_CACHE_SCHEMA:
        return {}
    if int(payload.get("chord_window_ms") or -1) != chord_window_ms:
        return {}
    cached: dict[date, tuple[str, _DayCounters]] = {}
    for raw_day, entry in (payload.get("days") or {}).items():
        try:
            day = date.fromisoformat(str(raw_day))
        except ValueError:
            continue
        if not isinstance(entry, dict) or not entry.get("digest"):
            continue
        cached[day] = (str(entry["digest"]), _DayCounters.from_json(entry.get("counters") or {}))
    return cached


def _save_day_counter_cache(
    path: Path, chord_window_ms: int, entries: dict[date, tuple[str, _DayCounters]]
) -> None:
    save_json(
        path,
        {
            "schema": _DAY_COUNTER_CACHE_SCHEMA,
            "chord_window_ms": chord_window_ms,
            "days": {
                day.isoformat(): {"digest": digest, "counters": counters.to_json()}
                for day, (digest, counters) in sorted(entries.items())
            },
        },
        sort_keys=True,
    )


def _windowed_day_counters(
    *,
    start: date,
    end: date,
    bindings_path: Path,
    by_chord: dict[str, HyprlandKeybind],
    chord_window_ms: int,
    cache_path: Path,
) -> dict[date, _DayCounters]:
    """Return the window's per-day counters, rescanning only stale days.

    Raw day files are append-only, so on a routine refresh only the current day
    (and whatever the capture appended to its neighbour) is stale; the rest are
    served from the counter cache instead of reparsing gigabytes of JSONL.
    """
    days = _date_range(start, end)
    digests = {day: _day_input_signature(day, bindings_path) for day in days}
    cached = _load_day_counter_cache(cache_path, chord_window_ms)
    stale = [day for day in days if cached.get(day, (None,))[0] != digests[day]]

    counters = {day: cached[day][1] for day in days if day not in stale}
    if stale:
        # One streaming pass over the contiguous stale span: the reader pays per
        # file, so scanning a range once beats scanning each day separately.
        scanned = _scan_day_counters(
            start=min(stale), end=max(stale), by_chord=by_chord, chord_window_ms=chord_window_ms
        )
        for day in days:
            if min(stale) <= day <= max(stale):
                counters[day] = scanned.get(day, _DayCounters())
        _save_day_counter_cache(
            cache_path,
            chord_window_ms,
            {day: (digests[day], counters[day]) for day in days},
        )
    return counters


def _input_signature(input_files: tuple[Path, ...]) -> str:
    values: list[tuple[str, int, int]] = []
    for path in input_files:
        try:
            stat = path.stat()
        except OSError:
            continue
        values.append((str(path), stat.st_size, stat.st_mtime_ns))
    return deterministic_input_digest(values)


def _date_range(start: date, end: date) -> tuple[date, ...]:
    return tuple(start + timedelta(days=offset) for offset in range((end - start).days + 1))


def _daily_payload(payload: dict[str, Any], day: date) -> dict[str, Any]:
    raw_day = day.isoformat()
    usage = [row for row in payload.get("keybind_usage", []) if row.get("date") == raw_day]
    shape = [row for row in payload.get("text_shape_days", []) if row.get("date") == raw_day]
    text = dict(payload.get("text_content") or {})
    text["start"] = raw_day
    text["end"] = raw_day
    text["days"] = [row for row in text.get("days", []) if row.get("date") == raw_day]
    text["snapshot_count"] = sum(int(row.get("snapshot_count") or 0) for row in text["days"])
    text["char_count"] = sum(int(row.get("char_count") or 0) for row in text["days"])
    text["word_count"] = sum(int(row.get("word_count") or 0) for row in text["days"])
    text["line_count"] = sum(int(row.get("line_count") or 0) for row in text["days"])
    return {
        **payload,
        "start": raw_day,
        "end": raw_day,
        "source_event_count": sum(int(row.get("keypress_count") or 0) for row in shape) or sum(int(row.get("count") or 0) for row in usage),
        "keypress_count": sum(int(row.get("keypress_count") or 0) for row in shape),
        "matched_keybind_count": sum(int(row.get("count") or 0) for row in usage),
        "keybind_usage": usage,
        "text_shape_days": shape,
        "text_content": text,
    }


def _analysis_from_payload(payload: dict[str, Any]) -> KeylogAnalysis:
    """Rehydrate the compatibility return value without reading raw logs."""
    def _date_value(value: object) -> date:
        return date.fromisoformat(str(value))

    return KeylogAnalysis(
        start=_date_value(payload["start"]), end=_date_value(payload["end"]),
        source_event_count=int(payload.get("source_event_count") or 0),
        keypress_count=int(payload.get("keypress_count") or 0),
        matched_keybind_count=int(payload.get("matched_keybind_count") or 0),
        keybinds=tuple(HyprlandKeybind(**row) for row in payload.get("keybinds", [])),
        keybind_usage=tuple(KeybindUse(**{**row, "date": _date_value(row["date"])}) for row in payload.get("keybind_usage", [])),
        keybind_summaries=tuple(KeybindSummary(**{**row, "first_date": _date_value(row["first_date"]), "last_date": _date_value(row["last_date"])}) for row in payload.get("keybind_summaries", [])),
        keybind_family_summaries=tuple(KeybindFamilySummary(**{**row, "first_date": _date_value(row["first_date"]), "last_date": _date_value(row["last_date"])}) for row in payload.get("keybind_family_summaries", [])),
        keybind_temporal_buckets=tuple(KeybindTemporalBucket(**row) for row in payload.get("keybind_temporal_buckets", [])),
        text_shape_days=tuple(KeylogTextShapeDay(**{**row, "date": _date_value(row["date"])}) for row in payload.get("text_shape_days", [])),
        caveats=tuple(str(value) for value in payload.get("caveats", [])),
    )


def load_keylog_analysis_payload(*, start: date, end: date) -> dict[str, Any] | None:
    """Read the requested logical day selection without consulting raw logs."""
    target = Path(resolve_analysis_path("keylog_analysis.json"))
    store = ArtifactStore(target.with_name(f".{target.stem}.partitions"))
    selected = store.logical_partitions()
    if not selected:
        payload = load_json(target) if target.exists() else None
        return payload if isinstance(payload, dict) else None
    rows: list[dict[str, Any]] = []
    cursor = start
    while cursor <= end:
        ref = selected.get(ProductPartitionKey.day("keylog.analysis", cursor))
        if ref is None:
            return None
        for line in store.read(ref).decode().splitlines():
            if line.strip():
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
        cursor += timedelta(days=1)
    return _merge_daily_payloads(rows, start=start, end=end)


def _merge_daily_payloads(rows: list[dict[str, Any]], *, start: date, end: date) -> dict[str, Any]:
    if not rows:
        return {"start": start.isoformat(), "end": end.isoformat()}
    first = rows[0]
    usage = [item for row in rows for item in row.get("keybind_usage", [])]
    shapes = [item for row in rows for item in row.get("text_shape_days", [])]
    text_rows = [item for row in rows for item in (row.get("text_content") or {}).get("days", [])]
    text = dict(first.get("text_content") or {})
    text.update({"start": start.isoformat(), "end": end.isoformat(), "days": text_rows})
    text["snapshot_count"] = sum(int(item.get("snapshot_count") or 0) for item in text_rows)
    text["char_count"] = sum(int(item.get("char_count") or 0) for item in text_rows)
    text["word_count"] = sum(int(item.get("word_count") or 0) for item in text_rows)
    text["line_count"] = sum(int(item.get("line_count") or 0) for item in text_rows)
    return {
        **first,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "source_event_count": sum(int(row.get("source_event_count") or 0) for row in rows),
        "keypress_count": sum(int(row.get("keypress_count") or 0) for row in rows),
        "matched_keybind_count": sum(int(row.get("matched_keybind_count") or 0) for row in rows),
        "keybind_usage": usage,
        "text_shape_days": shapes,
        "text_content": text,
    }


def _open_log_date(now: datetime | None = None) -> date:
    """The UTC-named day file the capture is still appending to.

    Log files are named by UTC date, so every file older than this one is
    append-complete. This one is not, and never will be while capture runs.
    """
    return (now or datetime.now(UTC)).astimezone(UTC).date()


def _analysis_input_files(*, start: date, end: date, bindings_path: Path) -> tuple[Path, ...]:
    """The closed inputs an artifact's freshness may be judged against.

    The current day's file is excluded even though it is analyzed: it grows
    with every keypress, so including it would make the stability retry and
    the manifest freshness check unsatisfiable rather than merely strict.
    """
    log_start = start - timedelta(days=1)
    log_end = min(end + timedelta(days=1), _open_log_date() - timedelta(days=1))
    inputs = list(keylog.log_files(start=log_start, end=log_end)) if log_end >= log_start else []
    if bindings_path.exists():
        inputs.append(bindings_path)
    return tuple(sorted(dict.fromkeys(inputs)))


def _keybind_summaries(
    usage: tuple[KeybindUse, ...],
    by_chord: dict[str, HyprlandKeybind],
) -> tuple[KeybindSummary, ...]:
    by_usage: dict[str, list[KeybindUse]] = defaultdict(list)
    for row in usage:
        by_usage[row.chord].append(row)
    summaries: list[KeybindSummary] = []
    for chord, rows in by_usage.items():
        bind = by_chord[chord]
        days = sorted({row.date for row in rows})
        summaries.append(
            KeybindSummary(
                chord=chord,
                dispatcher=bind.dispatcher,
                argument=bind.argument,
                family=bind.family,
                total_count=sum(row.count for row in rows),
                active_days=len(days),
                first_date=days[0],
                last_date=days[-1],
            )
        )
    return tuple(sorted(summaries, key=lambda row: (-row.total_count, row.chord)))


def _keybind_family_summaries(usage: tuple[KeybindUse, ...]) -> tuple[KeybindFamilySummary, ...]:
    by_family: dict[str, list[KeybindUse]] = defaultdict(list)
    for row in usage:
        by_family[row.family].append(row)
    summaries = []
    for family, rows in by_family.items():
        days = sorted({row.date for row in rows})
        summaries.append(
            KeybindFamilySummary(
                family=family,
                total_count=sum(row.count for row in rows),
                unique_chords=len({row.chord for row in rows}),
                active_days=len(days),
                first_date=days[0],
                last_date=days[-1],
            )
        )
    return tuple(sorted(summaries, key=lambda row: (-row.total_count, row.family)))


def _keybind_temporal_buckets(
    temporal_counter: Counter[tuple[str, int, int]],
    by_chord: dict[str, HyprlandKeybind],
) -> tuple[KeybindTemporalBucket, ...]:
    rows = []
    for (chord, weekday, hour), count in temporal_counter.items():
        bind = by_chord[chord]
        rows.append(
            KeybindTemporalBucket(
                chord=chord,
                dispatcher=bind.dispatcher,
                argument=bind.argument,
                family=bind.family,
                weekday=weekday,
                hour=hour,
                count=count,
            )
        )
    return tuple(sorted(rows, key=lambda row: (row.chord, row.weekday, row.hour)))


def _classify_keybind_family(dispatcher: str, argument: str, key: str) -> str:
    dispatcher_l = dispatcher.lower()
    argument_l = argument.lower()
    key_l = key.lower()
    text = f"{dispatcher_l} {argument_l} {key_l}"
    if dispatcher_l == "workspace" or "workspace" in argument_l:
        return "workspace"
    if dispatcher_l in {"movewindow", "movetoworkspace", "movetoworkspacesilent"}:
        return "window_move"
    if dispatcher_l in {"movefocus", "cyclenext", "alterzorder"} or "hypr-nav" in argument_l:
        return "navigation"
    if dispatcher_l in {"exec", "global"}:
        if any(token in text for token in ("grim", "slurp", "screenshot", "hyprpicker", "picker")):
            return "capture"
        if any(token in text for token in ("playerctl", "wpctl", "volume", "brightnessctl", "xf86")):
            return "media"
        if any(token in text for token in ("lock", "suspend", "logout", "shutdown", "reboot")):
            return "system"
        return "launch"
    if dispatcher_l in {"fullscreen", "togglefloating", "pin", "pseudo", "togglesplit", "killactive", "closewindow"}:
        return "window_state"
    if dispatcher_l in {"resizeactive", "moveactive", "resizewindowpixel", "movewindowpixel"}:
        return "layout"
    if "xf86" in key_l:
        return "media"
    return "other"


def _normalize_modifiers(raw: str) -> tuple[str, ...]:
    names = []
    for part in raw.replace("+", " ").split():
        name = part.strip().upper()
        if name == "CONTROL":
            name = "CTRL"
        if name in {"SUPER", "SHIFT", "CTRL", "ALT"}:
            names.append(name)
    return tuple(sorted(dict.fromkeys(names)))


def _normalize_bind_key(raw: str) -> str | None:
    key = raw.strip()
    if not key:
        return None
    if key.startswith("mouse:"):
        return key
    upper = key.upper()
    if upper in SPECIAL_KEY_ALIASES:
        return SPECIAL_KEY_ALIASES[upper]
    if upper.startswith("XF86"):
        return f"KEY_{upper}"
    if upper.startswith("KP_"):
        return SPECIAL_KEY_ALIASES.get(upper, f"KEY_{upper}")
    if len(upper) == 1 and upper.isalnum():
        return f"KEY_{upper}"
    if upper.startswith("F") and upper[1:].isdigit():
        return f"KEY_{upper}"
    return f"KEY_{upper}"


def _normalize_event_key(raw: str | None) -> str | None:
    if not raw:
        return None
    key = raw.upper()
    if key == "KEY_RETURN":
        return "KEY_ENTER"
    return key


def _chord(modifiers: Iterable[str], key: str) -> str:
    return "+".join([*sorted(modifiers), key])


__all__ = [
    "HyprlandKeybind",
    "KeybindFamilySummary",
    "KeybindSummary",
    "KeybindTemporalBucket",
    "KeybindUse",
    "KeylogAnalysis",
    "KeylogTextShapeDay",
    "analyze_keylog",
    "analyze_keylog_text_content",
    "load_keylog_analysis_payload",
    "parse_hyprland_keybinds",
    "write_keylog_analysis",
]
