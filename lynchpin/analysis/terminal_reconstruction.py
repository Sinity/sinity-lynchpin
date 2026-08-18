"""Terminal-session reconstruction: join a session's own capture lanes (sinnix-3w9n).

A terminal session recreatable as-it-looked needs three things joined by the
same window identity and overlapping wall-clock time: the asciinema cast (the
byte-for-byte replay), the kitty scrollback snapshots (full-ANSI, periodic,
same window), and the screen-frames geometry timeline (workspace, monitor,
on-screen geometry at capture time). All three are owner-native captures
already writing continuously; this is a downstream join over them, not a
fourth capture lane -- consistent with this repo's stated doctrine
("source-local normalization belongs in the source module; cross-source joins
belong downstream", `docs/reference/data-sources.md`).

Linking a cast to its kitty window id
--------------------------------------
Nothing captured on disk records "this asciinema session is kitty window 37"
directly. The asciinema recorder process itself carries that fact in its own
environment: sinnix-captured-shell launches ``asciinema session`` as a child
of the kitty window's shell, so the recorder process inherits kitty's own
``KITTY_PID``/``KITTY_WINDOW_ID`` env vars (this is exactly the technique
sinnix-ops-reducer's ``terminals.py:live_streams()`` already uses to find a
window's live-stream port). That link is therefore only readable while the
recorder process is still running -- for a live session, an exact
`/proc/<pid>/environ` read; for a session whose recorder has already exited,
there is no recorded link and reconstruction reports ``link_method =
"unresolved"`` rather than guessing.

Aligning geometry to the window
--------------------------------
Screen-frames records carry ``window_title``/``window_class`` but no kitty
window id (they come from Hyprland/grim, which know nothing about kitty's own
id space). The resolved kitty window's title (from its own scrollback
snapshots) is the join key: screen-frames records where
``window_class == "kitty"`` and the title matches (after stripping the
leading spinner glyph the shell prompt writes into the title) are the
window's geometry timeline.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..core.config import get_config
from ..sources import sinnix_capture_lanes as lanes
from ..sources import terminal

__all__ = [
    "GeometryFrame",
    "TerminalReconstruction",
    "resolve_live_kitty_window",
    "reconstruct_session",
    "write_reconstruction",
]

# Kitty window titles carry a leading spinner/status glyph the shell prompt
# rewrites on every prompt redraw (e.g. "◑ my task", "✳ my task");
# the same glyph shows up verbatim in screen-frames' window_title. Strip it so
# a title match survives the glyph changing between captures.
_TITLE_GLYPH_RE = re.compile(r"^[^\w\s]+\s*")

# session.cast/events.jsonl timestamps must agree on when the session
# actually started within this tolerance for the join to be trusted (AC:
# "timestamps aligned within 1s").
ALIGNMENT_TOLERANCE_S = 1.0


def _normalize_title(title: str) -> str:
    return _TITLE_GLYPH_RE.sub("", title or "").strip()


@dataclass(frozen=True)
class GeometryFrame:
    timestamp: datetime
    trigger: str
    monitor: Optional[str]
    workspace: Optional[str]
    geometry: dict
    raw_ref: Optional[str]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d


@dataclass(frozen=True)
class TerminalReconstruction:
    session_id: str
    cast_path: str
    events_path: Optional[str]
    tty: Optional[str]
    terminal: Optional[str]
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    link_method: str  # "live_proc_environ" | "unresolved"
    kitty_pid: Optional[int]
    kitty_window_id: Optional[int]
    kitty_title: Optional[str]
    scrollback_captures: tuple[str, ...]
    geometry_timeline: tuple[GeometryFrame, ...]
    alignment_max_skew_s: Optional[float]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "cast_path": self.cast_path,
            "events_path": self.events_path,
            "tty": self.tty,
            "terminal": self.terminal,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "link_method": self.link_method,
            "kitty_pid": self.kitty_pid,
            "kitty_window_id": self.kitty_window_id,
            "kitty_title": self.kitty_title,
            "scrollback_captures": list(self.scrollback_captures),
            "geometry_timeline": [f.to_dict() for f in self.geometry_timeline],
            "alignment_max_skew_s": self.alignment_max_skew_s,
            "warnings": list(self.warnings),
        }


def _find_session_dir(session_id: str, *, roots: tuple[Path, ...]) -> Optional[Path]:
    for root in roots:
        if not root.exists():
            continue
        for candidate in root.glob(f"*/*/*/{session_id}"):
            if (candidate / "session.cast").exists():
                return candidate
    return None


def _default_session_roots() -> tuple[Path, ...]:
    cfg = get_config()
    # asciinema_root is the current write target (activity/asciinema, 2026-08-17
    # subject recut); captures_root/asciinema is where long-lived recorder
    # processes launched before that recut are still writing (their
    # --output-file was resolved at process start and does not follow a config
    # change until the shell restarts) -- both are real, live data today.
    return (cfg.asciinema_root, cfg.captures_root / "asciinema")


def resolve_live_kitty_window(session_id: str) -> Optional[tuple[int, int]]:
    """Read (kitty_pid, window_id) for a still-running session's recorder.

    Exact, not heuristic: the asciinema recorder process for this session
    carries its owning window's KITTY_PID/KITTY_WINDOW_ID in its own
    environment (same technique as sinnix-ops-reducer's
    ``terminals.py:live_streams()``). Returns ``None`` once the recorder has
    exited -- there is nothing left on disk to read the link back from.
    """
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if (proc / "comm").read_text().strip() != "asciinema":
                continue
            raw = (proc / "environ").read_bytes()
        except OSError:
            continue
        env = dict(item.split("=", 1) for item in raw.decode("utf-8", "replace").split("\0") if "=" in item)
        if env.get("SINNIX_CAPTURE_SESSION_ID") != session_id:
            continue
        try:
            return (int(env["KITTY_PID"]), int(env["KITTY_WINDOW_ID"]))
        except (KeyError, ValueError):
            return None
    return None


def _read_session_json(session_dir: Path) -> dict:
    path = session_dir / "session.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _read_session_start_events(session_dir: Path) -> Optional[int]:
    path = session_dir / "events.jsonl"
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as handle:
            first = handle.readline()
    except OSError:
        return None
    if not first.strip():
        return None
    try:
        record = json.loads(first)
    except json.JSONDecodeError:
        return None
    if record.get("type") != "session_start":
        return None
    ts_ms = record.get("ts_ms")
    return int(ts_ms) if isinstance(ts_ms, (int, float)) else None


def _ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def reconstruct_session(
    session_id: str,
    *,
    now: Optional[datetime] = None,
    session_roots: Optional[tuple[Path, ...]] = None,
    kitty_scrollback_root: Optional[Path] = None,
    screen_frames_root: Optional[Path] = None,
    resolve_window: Optional[tuple[int, int]] = None,
) -> TerminalReconstruction:
    """Build the reconstruction record for one asciinema session.

    ``resolve_window`` overrides the live /proc lookup (for tests, or for a
    session whose recorder has already exited but whose window is otherwise
    known) with an explicit ``(kitty_pid, window_id)`` pair.
    """
    now = now or datetime.now(timezone.utc)
    roots = session_roots if session_roots is not None else _default_session_roots()
    session_dir = _find_session_dir(session_id, roots=roots)
    if session_dir is None:
        raise FileNotFoundError(f"no session.cast found for session_id={session_id!r} under {roots}")

    payload = _read_session_json(session_dir)
    cast_path = session_dir / "session.cast"
    events_path = session_dir / "events.jsonl"

    started_at_ms = payload.get("started_at_ms")
    started_at = _ms_to_dt(int(started_at_ms)) if started_at_ms is not None else None
    finished_at_ms = payload.get("finished_at_ms")
    finished_at = _ms_to_dt(int(finished_at_ms)) if finished_at_ms is not None else None

    warnings: list[str] = []
    alignment_max_skew_s: Optional[float] = None
    events_start_ms = _read_session_start_events(session_dir)
    if started_at_ms is not None and events_start_ms is not None:
        alignment_max_skew_s = abs(int(started_at_ms) - events_start_ms) / 1000
        if alignment_max_skew_s > ALIGNMENT_TOLERANCE_S:
            warnings.append(
                f"session.json started_at_ms and events.jsonl session_start disagree by "
                f"{alignment_max_skew_s:.3f}s (> {ALIGNMENT_TOLERANCE_S}s tolerance)"
            )

    window = resolve_window if resolve_window is not None else resolve_live_kitty_window(session_id)
    kitty_pid: Optional[int] = None
    kitty_window_id: Optional[int] = None
    kitty_title: Optional[str] = None
    link_method = "unresolved"
    scrollback_paths: list[str] = []
    geometry_timeline: tuple[GeometryFrame, ...] = ()

    window_end = finished_at or now

    if window is not None:
        kitty_pid, kitty_window_id = window
        link_method = "live_proc_environ"

        scrollbacks = [
            cap
            for cap in terminal.kitty_scrollback_captures(root=kitty_scrollback_root)
            if cap.kitty_pid == kitty_pid and cap.window_id == kitty_window_id
        ]
        if started_at is not None:
            scrollbacks = [cap for cap in scrollbacks if started_at <= cap.captured_at <= window_end]
        scrollbacks.sort(key=lambda cap: cap.captured_at)
        scrollback_paths = [cap.meta_path for cap in scrollbacks]
        if scrollbacks:
            kitty_title = scrollbacks[-1].title
        else:
            warnings.append(
                f"resolved kitty window (pid={kitty_pid}, id={kitty_window_id}) but no "
                "kitty-scrollback snapshot covers this session's time range"
            )

        if kitty_title is not None and started_at is not None:
            wanted = _normalize_title(kitty_title)
            frames: list[GeometryFrame] = []
            for event in lanes.screen_frame_events(
                root=screen_frames_root, start=started_at.date(), end=window_end.date()
            ):
                if event.window_class != "kitty":
                    continue
                if _normalize_title(event.window_title or "") != wanted:
                    continue
                if not (started_at <= event.timestamp <= window_end):
                    continue
                frames.append(
                    GeometryFrame(
                        timestamp=event.timestamp,
                        trigger=event.trigger,
                        monitor=event.monitor,
                        workspace=event.workspace,
                        geometry=event.geometry,
                        raw_ref=event.raw_ref,
                    )
                )
            frames.sort(key=lambda f: f.timestamp)
            geometry_timeline = tuple(frames)
            if not geometry_timeline:
                warnings.append(
                    f"no screen-frames record matched kitty window title {wanted!r} "
                    "within the session's time range"
                )
    else:
        warnings.append(
            "no live asciinema recorder process for this session_id -- the "
            "cast<->kitty-window link is only readable while the recorder is running"
        )

    return TerminalReconstruction(
        session_id=session_id,
        cast_path=str(cast_path),
        events_path=str(events_path) if events_path.exists() else None,
        tty=payload.get("tty"),
        terminal=payload.get("terminal"),
        started_at=started_at,
        finished_at=finished_at,
        link_method=link_method,
        kitty_pid=kitty_pid,
        kitty_window_id=kitty_window_id,
        kitty_title=kitty_title,
        scrollback_captures=tuple(scrollback_paths),
        geometry_timeline=geometry_timeline,
        alignment_max_skew_s=alignment_max_skew_s,
        warnings=tuple(warnings),
    )


def write_reconstruction(record: TerminalReconstruction, *, output_dir: Optional[Path] = None) -> Path:
    """Write the reconstruction record as JSON under derived_root/terminal-reconstruction/."""
    out_dir = output_dir or (get_config().derived_root / "terminal-reconstruction")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{record.session_id}.json"
    out_path.write_text(json.dumps(record.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out_path


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Reconstruct one terminal session (cast + kitty window + geometry)")
    parser.add_argument("session_id")
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    record = reconstruct_session(args.session_id)
    out_path = write_reconstruction(record, output_dir=args.output_dir)
    sys.stdout.write(f"wrote {out_path}\n")
    sys.stdout.write(json.dumps(record.to_dict(), indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
