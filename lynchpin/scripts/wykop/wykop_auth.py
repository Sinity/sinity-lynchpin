from __future__ import annotations

import re
from pathlib import Path

USERKEEP_RE = re.compile(rb"userKeep.{0,200}?([0-9a-f]{64})")


def extract_refresh_token_from_leveldb(leveldb_dir: Path) -> str | None:
    if not leveldb_dir.exists():
        return None
    files = sorted(leveldb_dir.glob("*.ldb")) + sorted(leveldb_dir.glob("*.log"))
    for path in files:
        try:
            blob = path.read_bytes()
        except OSError:
            continue
        if b"userKeep" not in blob:
            continue
        if b"wykop" not in blob and b"Wykop" not in blob:
            continue
        match = USERKEEP_RE.search(blob)
        if not match:
            continue
        try:
            return match.group(1).decode("ascii")
        except UnicodeDecodeError:
            continue
    return None


def candidate_chrome_leveldb_dirs() -> list[Path]:
    home = Path.home()
    candidates: list[Path] = []
    for browser in ("google-chrome", "chromium", "BraveSoftware/Brave-Browser", "vivaldi"):
        root = home / ".config" / browser
        if not root.exists():
            continue
        profiles: list[Path] = []
        default_profile = root / "Default"
        if default_profile.exists():
            profiles.append(default_profile)
        profiles.extend(sorted(path for path in root.glob("Profile *") if path.is_dir()))
        for profile in profiles:
            leveldb = profile / "Local Storage" / "leveldb"
            if leveldb.is_dir():
                candidates.append(leveldb)
    return candidates


def extract_refresh_token_from_chrome(leveldb_dir: Path | None) -> str | None:
    if leveldb_dir is not None:
        return extract_refresh_token_from_leveldb(leveldb_dir)
    for candidate in candidate_chrome_leveldb_dirs():
        token = extract_refresh_token_from_leveldb(candidate)
        if token:
            return token
    return None
