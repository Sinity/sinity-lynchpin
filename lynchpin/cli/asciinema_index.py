"""Build a header-only index of configured asciinema captures.

The default output is index/asciinema_sessions.ndjson beneath the configured
capture root. Input placement uses Lynchpin's filesystem registry and explicit
LYNCHPIN_ASCIINEMA_ROOT override. Only headers are read, not event streams.

Run: python -m lynchpin.cli.asciinema_index
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from lynchpin.core.config import get_config
from lynchpin.core.errors import SourceUnavailableError

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


def iter_sessions(root: Path):
    """Yield one dict per session.cast found."""
    def fail(error: OSError) -> None:
        raise error

    for directory, _dirs, files in root.walk(on_error=fail):
        _dirs.sort()
        if "session.cast" not in files:
            continue
        cast = directory / "session.cast"
        rel = cast.relative_to(root)
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="Override the index destination")
    args = parser.parse_args(argv)
    temporary: Path | None = None
    try:
        root = get_config().asciinema_root
        # Probe access before creating any output directory. Path.walk may yield
        # nothing for an absent root; that is unavailable input, not an empty scan.
        with os.scandir(root):
            pass
        output = args.out or root / "index" / "asciinema_sessions.ndjson"
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent,
                                         prefix=".asciinema-index-", delete=False) as out:
            temporary = Path(out.name)
            n = 0
            for rec in iter_sessions(root):
                out.write(json.dumps(rec, sort_keys=True) + "\n")
                n += 1
                if n % 500 == 0:
                    print(f"  {n} sessions indexed...", file=sys.stderr, flush=True)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, output)
        temporary = None
        print(f"\nWrote {n} sessions → {output}", file=sys.stderr)
        return 0
    except (OSError, ValueError, SourceUnavailableError) as error:
        print(f"asciinema index failed: {error}", file=sys.stderr)
        return 2
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
