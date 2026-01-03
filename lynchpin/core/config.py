from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class LynchpinConfig:
    repo_root: Path
    sinnix_root: Path
    data_root: Path
    activitywatch_db: Path
    atuin_db: Path
    baseline_dir: Path
    webhistory_raw_dir: Path
    webhistory_dir: Path
    webhistory_ndjson: Optional[Path]
    sleep_jsonl: Path
    codex_sessions_root: Path
    sessions_csv: Path
    reddit_export_dir: Optional[Path]
    spotify_root: Path
    finance_journal: Path
    polylogue_root: Path
    sinevec_state_dir: Path
    asciinema_root: Path
    audio_root: Path
    screenshot_root: Path
    cache_dir: Path
    warehouse_db: Path
    dendron_root: Path
    raindrop_dir: Path
    raindrop_csv: Optional[Path]
    goodreads_library: Path
    wykop_root: Path
    wykop_username: str
    substack_root: Path

    @classmethod
    def from_env(cls) -> "LynchpinConfig":
        repo_root = Path(os.environ.get("LYNCHPIN_REPO_ROOT", Path(__file__).resolve().parents[2]))
        data_root = Path(os.environ.get("LYNCHPIN_DATA_ROOT", "/realm/data"))
        sinnix_root = Path(os.environ.get("LYNCHPIN_SINNIX_ROOT", "/realm/project/sinnix"))
        aw_db = Path(
            os.environ.get(
                "LYNCHPIN_ACTIVITYWATCH_DB",
                "~/.local/share/activitywatch/aw-server-rust/sqlite.db",
            )
        ).expanduser()
        atuin_db = Path(
            os.environ.get("LYNCHPIN_ATUIN_DB", "~/.local/share/atuin/history.db")
        ).expanduser()
        baseline_dir = Path(
            os.environ.get("LYNCHPIN_BASELINE_DIR", repo_root / "artefacts/core/baseline/latest")
        )
        webhistory_raw_dir = Path(
            os.environ.get(
                "LYNCHPIN_WEBHISTORY_RAW_DIR",
                data_root / "webhistory/gestalt/raw",
            )
        )
        webhistory_dir = Path(
            os.environ.get(
                "LYNCHPIN_WEBHISTORY_DIR",
                data_root / "webhistory/gestalt/data",
            )
        )
        webhistory_ndjson = Path(
            os.environ.get(
                "LYNCHPIN_WEBHISTORY_NDJSON",
                data_root / "webhistory/gestalt/derived/full_history.ndjson",
            )
        )
        if not webhistory_ndjson.exists():
            webhistory_ndjson = None
        sleep_jsonl = Path(
            os.environ.get(
                "LYNCHPIN_SLEEP_JSONL",
                data_root / "health/processed/sleep_merged.jsonl",
            )
        )
        sessions_csv = Path(
            os.environ.get(
                "LYNCHPIN_SESSIONS_CSV",
                repo_root / "artefacts/knowledge/ledgers/session_index.csv",
            )
        )
        codex_sessions_root = Path(
            os.environ.get(
                "LYNCHPIN_CODEX_ROOT",
                "~/.codex/sessions",
            )
        ).expanduser()
        reddit_export_dir = _resolve_reddit_export(
            os.environ.get("LYNCHPIN_REDDIT_EXPORT_DIR"),
            data_root / "reddit/gdpr",
        )
        spotify_root = Path(
            os.environ.get(
                "LYNCHPIN_SPOTIFY_ROOT",
                data_root / "spotify/gdpr",
            )
        )
        spotify_root = _resolve_spotify_export(spotify_root)
        finance_journal = Path(os.environ.get("LYNCHPIN_FINANCE_JOURNAL", data_root / "finance/journal_clean"))
        polylogue_root = Path(
            os.environ.get("LYNCHPIN_POLYLOGUE_ROOT", data_root / "chatlog/markdown")
        )
        sinevec_state_dir = Path(
            os.environ.get("LYNCHPIN_SINEVEC_STATE", "/realm/project/sinevec/var/state")
        )
        asciinema_root = Path(
            os.environ.get("LYNCHPIN_ASCIINEMA_ROOT", data_root / "asciinema_recording")
        )
        audio_root = Path(os.environ.get("LYNCHPIN_AUDIO_ROOT", data_root / "audio/raw"))
        screenshot_root = Path(
            os.environ.get("LYNCHPIN_SCREENSHOT_ROOT", data_root / "screenshot")
        )
        cache_dir = Path(os.environ.get("LYNCHPIN_CACHE_DIR", repo_root / "artefacts/lynchpin/cache"))
        warehouse_db = Path(
            os.environ.get("LYNCHPIN_WAREHOUSE_DB", repo_root / "artefacts/lynchpin/warehouse.duckdb")
        )
        dendron_root = Path(os.environ.get("LYNCHPIN_DENDRON_ROOT", "/realm/project/knowledgebase"))
        raindrop_dir = Path(os.environ.get("LYNCHPIN_RAINDROP_DIR", data_root / "raindrop"))
        raindrop_csv = _resolve_raindrop_csv(os.environ.get("LYNCHPIN_RAINDROP_CSV"), raindrop_dir)
        goodreads_library = Path(
            os.environ.get(
                "LYNCHPIN_GOODREADS_LIBRARY",
                data_root / "goodreads/library_export.csv",
            )
        )
        wykop_root = Path(os.environ.get("LYNCHPIN_WYKOP_ROOT", data_root / "wykop"))
        wykop_username = os.environ.get("LYNCHPIN_WYKOP_USER", "Sinity")
        substack_root = Path(os.environ.get("LYNCHPIN_SUBSTACK_ROOT", data_root / "doc/substack"))
        cache_dir.mkdir(parents=True, exist_ok=True)
        warehouse_db.parent.mkdir(parents=True, exist_ok=True)
        return cls(
            repo_root=repo_root,
            sinnix_root=sinnix_root,
            data_root=data_root,
            activitywatch_db=aw_db,
            atuin_db=atuin_db,
            baseline_dir=baseline_dir,
            webhistory_raw_dir=webhistory_raw_dir,
            webhistory_dir=webhistory_dir,
            webhistory_ndjson=webhistory_ndjson,
            sleep_jsonl=sleep_jsonl,
            codex_sessions_root=codex_sessions_root,
            sessions_csv=sessions_csv,
            reddit_export_dir=reddit_export_dir,
            spotify_root=spotify_root,
            finance_journal=finance_journal,
            polylogue_root=polylogue_root,
            sinevec_state_dir=sinevec_state_dir,
            asciinema_root=asciinema_root,
            audio_root=audio_root,
            screenshot_root=screenshot_root,
            cache_dir=cache_dir,
            warehouse_db=warehouse_db,
            dendron_root=dendron_root,
            raindrop_dir=raindrop_dir,
            raindrop_csv=raindrop_csv,
            goodreads_library=goodreads_library,
            wykop_root=wykop_root,
            wykop_username=wykop_username,
            substack_root=substack_root,
        )


_CONFIG: Optional[LynchpinConfig] = None


def get_config() -> LynchpinConfig:
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = LynchpinConfig.from_env()
    return _CONFIG


def _resolve_reddit_export(env_value: Optional[str], default_root: Path) -> Optional[Path]:
    if env_value:
        candidate = Path(env_value)
        return candidate if candidate.exists() else None
    if not default_root.exists():
        return None
    subdirs = [
        child for child in default_root.iterdir() if child.is_dir() and child.name != "raw"
    ]
    if not subdirs:
        return None
    dated: list[tuple[datetime, Path]] = []
    fallback: list[Path] = []
    for path in subdirs:
        try:
            parsed = datetime.strptime(path.name, "%Y-%m-%d")
        except ValueError:
            fallback.append(path)
            continue
        dated.append((parsed, path))
    if dated:
        dated.sort(key=lambda item: item[0], reverse=True)
        return dated[0][1]
    fallback.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return fallback[0]


def _resolve_latest_dated_dir(root: Path, ignore: Optional[set[str]] = None) -> Optional[Path]:
    if not root.exists():
        return None
    ignore = ignore or set()
    subdirs = [
        child for child in root.iterdir() if child.is_dir() and child.name not in ignore
    ]
    if not subdirs:
        return None
    dated: list[tuple[datetime, Path]] = []
    fallback: list[Path] = []
    for path in subdirs:
        try:
            parsed = datetime.strptime(path.name, "%Y-%m-%d")
        except ValueError:
            fallback.append(path)
            continue
        dated.append((parsed, path))
    if dated:
        dated.sort(key=lambda item: item[0], reverse=True)
        return dated[0][1]
    fallback.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return fallback[0]


def _resolve_spotify_export(root: Path) -> Path:
    if (root / "Spotify Account Data").exists() or (root / "Spotify Extended Streaming History").exists():
        return root
    candidate = _resolve_latest_dated_dir(root, ignore={"raw", "archive", "legacy", "derivative"})
    if candidate and (
        (candidate / "Spotify Account Data").exists()
        or (candidate / "Spotify Extended Streaming History").exists()
    ):
        return candidate
    return root


def _resolve_raindrop_csv(env_value: Optional[str], root: Path) -> Optional[Path]:
    if env_value:
        candidate = Path(env_value)
        return candidate if candidate.exists() else None
    if not root.exists():
        return None
    candidates = sorted(root.glob("raindrop_bookmarks_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    if candidates:
        return candidates[0]
    generic = sorted(root.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    return generic[0] if generic else None
