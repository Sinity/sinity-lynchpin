"""Higher-level keylog analysis.

Keybind usage, text-shape metadata, and text-content metrics are separate
products so callers can choose the level of detail they need.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
import re
from typing import Any, Iterable

from lynchpin.core.io import latest_mtime_iso, resolve_analysis_path, save_json
from lynchpin.core.primitives import date_to_dt_range
from lynchpin.core.primitives import logical_date
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


def analyze_keylog(
    *,
    start: date,
    end: date,
    bindings_path: Path = DEFAULT_HYPRLAND_BINDINGS,
    chord_window_ms: int = 1500,
) -> KeylogAnalysis:
    """Analyze keylog metadata over an inclusive date window."""

    start_dt, end_dt = date_to_dt_range(start, end)
    presses = list(keylog.events(start=start_dt, end=end_dt, kinds={"press"}))
    bind_rows = parse_hyprland_keybinds(bindings_path)
    by_chord = {row.chord: row for row in bind_rows}
    usage_counter: Counter[tuple[date, str, str]] = Counter()
    temporal_counter: Counter[tuple[str, int, int]] = Counter()
    shape_by_day: dict[date, Counter[str]] = defaultdict(Counter)

    recent_modifiers: dict[str, datetime] = {}
    chord_window = timedelta(milliseconds=chord_window_ms)
    for event in sorted(presses, key=lambda row: row.ts):
        day = logical_date(event.ts)
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
                usage_counter[(day, chord, "exact_modifier_state")] += 1
                temporal_counter[(chord, event.ts.weekday(), event.ts.hour)] += 1
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
            usage_counter[(day, chord, "inferred_adjacent_modifier_press")] += 1
            temporal_counter[(chord, event.ts.weekday(), event.ts.hour)] += 1

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
    text_days = tuple(
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
    return KeylogAnalysis(
        start=start,
        end=end,
        source_event_count=len(presses),
        keypress_count=len(presses),
        matched_keybind_count=sum(row.count for row in usage),
        keybinds=bind_rows,
        keybind_usage=usage,
        keybind_summaries=keybind_summaries,
        keybind_family_summaries=keybind_family_summaries,
        keybind_temporal_buckets=keybind_temporal_buckets,
        text_shape_days=text_days,
        caveats=(
            "keybind and text-shape metadata are separate from text-content analysis",
            "keybind usage prefers persisted modifier state when present",
            "keybind usage falls back to adjacent modifier keypresses within the chord window",
        ),
    )


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
    analysis = analyze_keylog(start=start, end=end, bindings_path=bindings_path)
    payload = analysis.to_json()
    input_files = _analysis_input_files(start=start, end=end, bindings_path=bindings_path)
    payload.update(
        {
            "dataset": "lynchpin.keylog_analysis",
            "schema_version": 1,
            "input_files": [str(path) for path in input_files],
            "input_file_count": len(input_files),
            "input_latest_mtime": latest_mtime_iso(input_files),
        }
    )
    payload["text_content"] = analyze_keylog_text_content(
        start=start,
        end=end,
        top_n=1000,
    ).to_json()
    save_json(target, payload, sort_keys=True)
    return analysis


def _analysis_input_files(*, start: date, end: date, bindings_path: Path) -> tuple[Path, ...]:
    log_start = start - timedelta(days=1)
    log_end = end + timedelta(days=1)
    inputs = list(keylog.log_files(start=log_start, end=log_end))
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
    "parse_hyprland_keybinds",
    "write_keylog_analysis",
]
