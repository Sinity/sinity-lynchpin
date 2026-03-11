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
    captures_root: Path
    exports_root: Path
    libraries_root: Path
    activitywatch_db: Path
    atuin_db: Path
    baseline_dir: Path
    webhistory_raw_dir: Path
    webhistory_dir: Path
    webhistory_ndjson: Optional[Path]
    sleep_jsonl: Path
    codex_sessions_root: Path
    session_docs_dir: Path
    reddit_export_dir: Optional[Path]
    spotify_root: Path
    finance_journal: Path
    polylogue_root: Path
    polylogue_archive_root: Path
    fbmessenger_gdpr_root: Path
    fbmessenger_db: Path
    asciinema_root: Path
    audio_root: Path
    screenshot_root: Path
    cache_dir: Path
    warehouse_root: Path
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
        repo_root = Path(
            os.environ.get("LYNCHPIN_REPO_ROOT", Path(__file__).resolve().parents[2])
        )
        data_root = Path(os.environ.get("LYNCHPIN_DATA_ROOT", "/realm/data"))
        captures_root = Path(
            os.environ.get("LYNCHPIN_CAPTURES_ROOT", data_root / "captures")
        )
        exports_root = Path(
            os.environ.get("LYNCHPIN_EXPORTS_ROOT", data_root / "exports")
        )
        libraries_root = Path(
            os.environ.get("LYNCHPIN_LIBRARIES_ROOT", data_root / "libraries")
        )
        sinnix_root = Path(
            os.environ.get("LYNCHPIN_SINNIX_ROOT", "/realm/project/sinnix")
        )
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
            os.environ.get(
                "LYNCHPIN_BASELINE_DIR", repo_root / "artefacts/core/baseline/latest"
            )
        )
        webhistory_raw_dir = Path(
            os.environ.get(
                "LYNCHPIN_WEBHISTORY_RAW_DIR",
                captures_root / "webhistory/gestalt/raw",
            )
        )
        webhistory_dir = Path(
            os.environ.get(
                "LYNCHPIN_WEBHISTORY_DIR",
                captures_root / "webhistory/gestalt/data",
            )
        )
        webhistory_ndjson = Path(
            os.environ.get(
                "LYNCHPIN_WEBHISTORY_NDJSON",
                captures_root / "webhistory/gestalt/derived/full_history.ndjson",
            )
        )
        if not webhistory_ndjson.exists():
            webhistory_ndjson = None
        sleep_jsonl = Path(
            os.environ.get(
                "LYNCHPIN_SLEEP_JSONL",
                exports_root / "health/processed/sleep_merged.jsonl",
            )
        )
        session_docs_dir = Path(
            os.environ.get(
                "LYNCHPIN_SESSION_DOCS_DIR",
                repo_root / "docs/reference/sessions",
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
            exports_root / "reddit/processed",
        )
        spotify_root = Path(
            os.environ.get(
                "LYNCHPIN_SPOTIFY_ROOT",
                exports_root / "spotify/processed",
            )
        )
        spotify_root = _resolve_spotify_export(spotify_root)
        finance_journal = Path(
            os.environ.get(
                "LYNCHPIN_FINANCE_JOURNAL", libraries_root / "finance/journal_clean"
            )
        )
        polylogue_root = Path(
            os.environ.get(
                "LYNCHPIN_POLYLOGUE_ROOT", exports_root / "chatlog/processed/markdown"
            )
        )
        polylogue_archive_root = Path(
            os.environ.get(
                "LYNCHPIN_POLYLOGUE_ARCHIVE_ROOT", exports_root / "chatlog/archive"
            )
        )
        fbmessenger_gdpr_root = Path(
            os.environ.get(
                "LYNCHPIN_FBMESSENGER_GDPR",
                exports_root / "comms/facebook-messenger/processed/gdpr",
            )
        )
        fbmessenger_db = Path(
            os.environ.get(
                "LYNCHPIN_FBMESSENGER_DB",
                _resolve_fbmessenger_db(
                    exports_root
                    / "comms/facebook-messenger/processed/fbmessengerexport.sqlite",
                    exports_root / "comms/facebook-messenger/fbmessengerexport.sqlite",
                    exports_root / "comms/fbmessengerexport.sqlite",
                ),
            )
        )
        asciinema_root = Path(
            os.environ.get("LYNCHPIN_ASCIINEMA_ROOT", captures_root / "asciinema")
        )
        audio_root = Path(
            os.environ.get("LYNCHPIN_AUDIO_ROOT", captures_root / "audio/raw")
        )
        screenshot_root = Path(
            os.environ.get("LYNCHPIN_SCREENSHOT_ROOT", captures_root / "screenshot")
        )
        cache_dir = Path(
            os.environ.get("LYNCHPIN_CACHE_DIR", repo_root / "artefacts/lynchpin/cache")
        )
        warehouse_root = Path(
            os.environ.get(
                "LYNCHPIN_WAREHOUSE_ROOT", repo_root / "artefacts/lynchpin/warehouse"
            )
        )
        warehouse_db = Path(
            os.environ.get(
                "LYNCHPIN_WAREHOUSE_DB",
                repo_root / "artefacts/lynchpin/warehouse.duckdb",
            )
        )
        dendron_root = Path(
            os.environ.get("LYNCHPIN_DENDRON_ROOT", "/realm/project/knowledgebase")
        )
        raindrop_dir = Path(
            os.environ.get("LYNCHPIN_RAINDROP_DIR", exports_root / "raindrop/raw")
        )
        raindrop_csv = _resolve_raindrop_csv(
            os.environ.get("LYNCHPIN_RAINDROP_CSV"), raindrop_dir
        )
        goodreads_library = Path(
            os.environ.get(
                "LYNCHPIN_GOODREADS_LIBRARY",
                exports_root / "goodreads/raw/library_export.csv",
            )
        )
        wykop_root = Path(
            os.environ.get("LYNCHPIN_WYKOP_ROOT", exports_root / "wykop/raw")
        )
        wykop_username = os.environ.get("LYNCHPIN_WYKOP_USER", "Sinity")
        substack_root = Path(
            os.environ.get("LYNCHPIN_SUBSTACK_ROOT", libraries_root / "substack")
        )
        cache_dir.mkdir(parents=True, exist_ok=True)
        warehouse_root.mkdir(parents=True, exist_ok=True)
        warehouse_db.parent.mkdir(parents=True, exist_ok=True)
        return cls(
            repo_root=repo_root,
            sinnix_root=sinnix_root,
            data_root=data_root,
            captures_root=captures_root,
            exports_root=exports_root,
            libraries_root=libraries_root,
            activitywatch_db=aw_db,
            atuin_db=atuin_db,
            baseline_dir=baseline_dir,
            webhistory_raw_dir=webhistory_raw_dir,
            webhistory_dir=webhistory_dir,
            webhistory_ndjson=webhistory_ndjson,
            sleep_jsonl=sleep_jsonl,
            codex_sessions_root=codex_sessions_root,
            session_docs_dir=session_docs_dir,
            reddit_export_dir=reddit_export_dir,
            spotify_root=spotify_root,
            finance_journal=finance_journal,
            polylogue_root=polylogue_root,
            polylogue_archive_root=polylogue_archive_root,
            fbmessenger_gdpr_root=fbmessenger_gdpr_root,
            fbmessenger_db=fbmessenger_db,
            asciinema_root=asciinema_root,
            audio_root=audio_root,
            screenshot_root=screenshot_root,
            cache_dir=cache_dir,
            warehouse_root=warehouse_root,
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


def _resolve_reddit_export(
    env_value: Optional[str], default_root: Path
) -> Optional[Path]:
    if env_value:
        candidate = Path(env_value)
        return candidate if candidate.exists() else None
    if not default_root.exists():
        return None
    subdirs = [
        child
        for child in default_root.iterdir()
        if child.is_dir() and child.name != "raw"
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


def _resolve_latest_dated_dir(
    root: Path, ignore: Optional[set[str]] = None
) -> Optional[Path]:
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
    if (root / "Spotify Account Data").exists() or (
        root / "Spotify Extended Streaming History"
    ).exists():
        return root
    candidate = _resolve_latest_dated_dir(
        root,
        ignore={"raw", "archive", "legacy", "derived", "derivative"},
    )
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
    candidates = sorted(
        root.glob("raindrop*.csv"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    return candidates[0] if candidates else None


def _resolve_fbmessenger_db(*candidates: Path) -> str:
    for path in candidates:
        if path.exists():
            return str(path)
    return str(candidates[0])
