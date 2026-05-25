"""Build a per-day asciinema header index.

The full asciinema corpus is ~181 GB across ~2,900 session.cast files;
parsing event streams in source modules is impractical. Instead we
extract a header-only index that gives downstream consumers the
session metadata they need for cross-referencing with Atuin / keylog
/ AW: when did this terminal start, on which pty, with which shell.

Output: /realm/data/captures/asciinema/index/asciinema_sessions.ndjson
(one record per .cast file).

Run: python scripts/asciinema_index.py
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

CAST_ROOT = Path("/realm/data/captures/asciinema")
INDEX_PATH = CAST_ROOT / "index" / "asciinema_sessions.ndjson"

DIR_RE = re.compile(
    r"^(?P<host>[^-]+)-(?P<user>[^-]+)-(?P<shell>[a-z0-9._]+)_"
    r"pts_(?P<pts>\d+)-(?P<stamp>\d{8}T\d{6}Z)-(?P<pid>\d+)-(?P<seq>\d+)$"
)


def parse_header(cast_path: Path) -> dict | None:
    """Read only the first line (header) of an asciinema cast file."""
    try:
        with cast_path.open("r", encoding="utf-8", errors="replace") as f:
            first_line = f.readline()
    except OSError as exc:
        return {"error": str(exc)}
    if not first_line.strip():
        return None
    try:
        return json.loads(first_line)
    except json.JSONDecodeError as exc:
        return {"error": f"json: {exc}"}


def parse_dir_name(dir_name: str) -> dict | None:
    """Parse the encoded session-dir name."""
    m = DIR_RE.match(dir_name)
    if not m:
        return None
    g = m.groupdict()
    return {
        "host": g["host"],
        "user": g["user"],
        "shell": g["shell"],
        "pts": int(g["pts"]),
        "stamp_iso": g["stamp"],
        "pid": int(g["pid"]),
        "seq": int(g["seq"]),
    }


def iter_sessions():
    """Yield one dict per session.cast found."""
    for cast in sorted(CAST_ROOT.rglob("session.cast")):
        rel = cast.relative_to(CAST_ROOT)
        # rel = YYYY/MM/DD/<dir>/session.cast
        parts = rel.parts
        if len(parts) < 5:
            continue
        year, month, day, dir_name = parts[:4]
        dn = parse_dir_name(dir_name)
        try:
            size_bytes = cast.stat().st_size
            mtime = cast.stat().st_mtime
        except OSError:
            size_bytes = mtime = None
        header = parse_header(cast)
        # Extract timestamp + command + shell from the header
        ts = header.get("timestamp") if isinstance(header, dict) else None
        ts_iso = (
            datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
            if isinstance(ts, (int, float)) else None
        )
        env = header.get("env") if isinstance(header, dict) else {}
        if not isinstance(env, dict):
            env = {}
        shell_env = env.get("SHELL")
        term = header.get("term") if isinstance(header, dict) else None
        term_type = term.get("type") if isinstance(term, dict) else env.get("TERM")
        cmd = header.get("command") if isinstance(header, dict) else None
        # Operator's sinnix-capture custom env tagging (v3-era sessions).
        # These let us join an asciinema session directly to project + git state
        # at the moment the terminal started, no temporal-proximity needed.
        capture = {
            k.replace("SINNIX_CAPTURE_", "").lower(): v
            for k, v in env.items()
            if isinstance(k, str) and k.startswith("SINNIX_CAPTURE_") and v
        }
        rec = {
            "path": str(rel),
            "date": f"{year}-{month}-{day}",
            "size_bytes": size_bytes,
            "mtime": (
                datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
                if mtime else None
            ),
            "session_start_iso": ts_iso,
            "command": cmd,
            "shell_env": shell_env,
            "term_type": term_type,
        }
        # Promote operator capture fields to first-class columns
        for k in ("start_cwd", "start_repo_branch", "start_repo_commit",
                  "start_repo_dirty", "project_root", "session_id", "host",
                  "user", "tty", "terminal"):
            if k in capture:
                rec[f"capture_{k}"] = capture[k]
        if dn:
            rec.update(dn)
        yield rec


def main():
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with INDEX_PATH.open("w", encoding="utf-8") as out:
        for rec in iter_sessions():
            out.write(json.dumps(rec, sort_keys=True) + "\n")
            n += 1
            if n % 500 == 0:
                print(f"  {n} sessions indexed...", file=sys.stderr, flush=True)
    print(f"\nWrote {n} sessions → {INDEX_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
