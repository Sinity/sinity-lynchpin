"""Lynchpin configuration and canonical input roots.

`LynchpinConfig` is the single place where the live source modules resolve
their default filesystem roots. The important boundaries are:

- local app state under `~/.local/share/...` for ActivityWatch and Atuin,
- canonical raw and processed exports under `/realm/data/...`,
- local repos under `/realm/project/...`,
- personal registries, generated datasets, and archives under
  `/realm/project/knowledgebase/lynchpin/...`.

If a stable source root changes, update this module and the consuming source
module together rather than reviving a parallel reference doc.
"""

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
    knowledgebase_root: Path
    knowledge_archive_root: Path
    repo_artefacts_root: Path
    session_registry_dir: Path
    artefact_catalog: Path
    analysis_output_dir: Path
    session_ledger_output: Path
    artefact_ledger_output: Path
    session_summary_dir: Path
    session_summary_log: Path
    velocity_output: Path
    webhistory_report_dir: Path
    # Source paths
    activitywatch_db: Path
    atuin_db: Path
    baseline_dir: Path
    webhistory_raw_dir: Path
    webhistory_dir: Path
    webhistory_ndjson: Optional[Path]
    sleep_jsonl: Path
    codex_sessions_root: Path
    reddit_export_dir: Optional[Path]
    spotify_root: Path
    polylogue_root: Path
    polylogue_archive_root: Path
    fbmessenger_gdpr_root: Path
    fbmessenger_db: Path
    asciinema_root: Path
    audio_root: Path
    screenshot_root: Path
    keylog_root: Path
    cache_dir: Path
    dendron_root: Path
    raindrop_dir: Path
    raindrop_csv: Optional[Path]
    goodreads_library: Path
    wykop_root: Path
    wykop_username: str
    samsung_gdpr_cloud_dir: Path

    def available_sources(self) -> dict[str, bool]:
        """Check which data sources actually have data on disk."""
        return {
            "activitywatch": self.activitywatch_db.exists(),
            "atuin": self.atuin_db.exists(),
            "git_baseline": (self.baseline_dir / "git_numstat.jsonl").exists() if self.baseline_dir.exists() else False,
            "webhistory": self.webhistory_dir.exists() or (self.webhistory_ndjson is not None),
            "sleep": self.sleep_jsonl.exists(),
            "codex": self.codex_sessions_root.exists(),
            "reddit": self.reddit_export_dir is not None and self.reddit_export_dir.exists(),
            "spotify": self.spotify_root.exists(),
            "polylogue": self.polylogue_root.exists(),
            "fbmessenger": self.fbmessenger_gdpr_root.exists() or Path(self.fbmessenger_db).exists(),
            "asciinema": self.asciinema_root.exists(),
            "goodreads": self.goodreads_library.exists(),
            "raindrop": self.raindrop_csv is not None and self.raindrop_csv.exists(),
            "wykop": self.wykop_root.exists(),
            "dendron": self.dendron_root.exists(),
            "samsung_gdpr_cloud": self.samsung_gdpr_cloud_dir.exists(),
        }

    @classmethod
    def from_env(cls) -> LynchpinConfig:
        repo_root = Path(os.environ.get("LYNCHPIN_REPO_ROOT", Path(__file__).resolve().parents[2]))
        data_root = Path(os.environ.get("LYNCHPIN_DATA_ROOT", "/realm/data"))
        captures_root = Path(os.environ.get("LYNCHPIN_CAPTURES_ROOT", data_root / "captures"))
        exports_root = Path(os.environ.get("LYNCHPIN_EXPORTS_ROOT", data_root / "exports"))
        libraries_root = Path(os.environ.get("LYNCHPIN_LIBRARIES_ROOT", data_root / "libraries"))
        sinnix_root = Path(os.environ.get("LYNCHPIN_SINNIX_ROOT", "/realm/project/sinnix"))
        knowledgebase_root = Path(
            os.environ.get("LYNCHPIN_KNOWLEDGEBASE_ROOT", "/realm/project/knowledgebase/lynchpin")
        )
        knowledge_archive_root = Path(
            os.environ.get("LYNCHPIN_KNOWLEDGE_ARCHIVE_ROOT", knowledgebase_root / "archive")
        )
        repo_artefacts_root = Path(
            os.environ.get("LYNCHPIN_REPO_ARTEFACTS_ROOT", knowledgebase_root / "repo-artefacts")
        )
        registry_root = Path(os.environ.get("LYNCHPIN_REGISTRY_ROOT", knowledgebase_root / "registry"))
        session_registry_dir = Path(
            os.environ.get("LYNCHPIN_SESSION_REGISTRY_DIR", registry_root / "sessions")
        )
        artefact_catalog = Path(
            os.environ.get("LYNCHPIN_ARTEFACT_CATALOG", registry_root / "artefact_catalog.json")
        )
        analysis_output_dir = Path(
            os.environ.get("LYNCHPIN_ANALYSIS_OUTPUT_DIR", repo_artefacts_root / "analysis/derived")
        )
        session_ledger_output = Path(
            os.environ.get(
                "LYNCHPIN_SESSION_LEDGER_OUTPUT",
                repo_artefacts_root / "knowledge/ledgers/session_index.csv",
            )
        )
        artefact_ledger_output = Path(
            os.environ.get(
                "LYNCHPIN_ARTEFACT_LEDGER_OUTPUT",
                repo_artefacts_root / "knowledge/ledgers/artefact_index.csv",
            )
        )
        session_summary_dir = Path(
            os.environ.get(
                "LYNCHPIN_SESSION_SUMMARY_DIR",
                repo_artefacts_root / "knowledge/sessions/summaries",
            )
        )
        session_summary_log = Path(
            os.environ.get(
                "LYNCHPIN_SESSION_SUMMARY_LOG",
                repo_artefacts_root / "knowledge/sessions/logs/session_summaries.jsonl",
            )
        )
        velocity_output = Path(
            os.environ.get("LYNCHPIN_VELOCITY_OUTPUT", repo_artefacts_root / "meta/velocity/velocity.html")
        )
        webhistory_report_dir = Path(
            os.environ.get("LYNCHPIN_WEBHISTORY_REPORT_DIR", repo_artefacts_root / "webhistory")
        )

        aw_db = Path(os.environ.get(
            "LYNCHPIN_ACTIVITYWATCH_DB", "~/.local/share/activitywatch/aw-server-rust/sqlite.db"
        )).expanduser()
        atuin_db = Path(os.environ.get("LYNCHPIN_ATUIN_DB", "~/.local/share/atuin/history.db")).expanduser()
        baseline_dir = Path(
            os.environ.get("LYNCHPIN_BASELINE_DIR", repo_artefacts_root / "core/baseline/latest")
        )

        webhistory_raw_dir = Path(os.environ.get("LYNCHPIN_WEBHISTORY_RAW_DIR", captures_root / "webhistory/gestalt/raw"))
        webhistory_dir = Path(os.environ.get("LYNCHPIN_WEBHISTORY_DIR", captures_root / "webhistory/gestalt/data"))
        webhistory_ndjson_path = Path(os.environ.get(
            "LYNCHPIN_WEBHISTORY_NDJSON", captures_root / "webhistory/gestalt/derived/full_history.ndjson"
        ))
        webhistory_ndjson = webhistory_ndjson_path if webhistory_ndjson_path.exists() else None

        sleep_jsonl = Path(os.environ.get("LYNCHPIN_SLEEP_JSONL", exports_root / "health/processed/sleep_merged.jsonl"))
        codex_sessions_root = Path(os.environ.get("LYNCHPIN_CODEX_ROOT", "~/.codex/sessions")).expanduser()

        reddit_export_dir = _resolve_reddit_export(
            os.environ.get("LYNCHPIN_REDDIT_EXPORT_DIR"), exports_root / "reddit/processed"
        )
        spotify_root = _resolve_spotify_export(Path(os.environ.get(
            "LYNCHPIN_SPOTIFY_ROOT", exports_root / "spotify/processed"
        )))
        polylogue_root = Path(os.environ.get("LYNCHPIN_POLYLOGUE_ROOT", exports_root / "chatlog/processed/markdown"))
        polylogue_archive_root = Path(os.environ.get("LYNCHPIN_POLYLOGUE_ARCHIVE_ROOT", exports_root / "chatlog/archive"))

        fbmessenger_gdpr_root = Path(os.environ.get(
            "LYNCHPIN_FBMESSENGER_GDPR", exports_root / "comms/facebook-messenger/processed/gdpr"
        ))
        fbmessenger_db = Path(os.environ.get("LYNCHPIN_FBMESSENGER_DB", _resolve_fbmessenger_db(
            exports_root / "comms/facebook-messenger/processed/fbmessengerexport.sqlite",
            exports_root / "comms/facebook-messenger/fbmessengerexport.sqlite",
            exports_root / "comms/fbmessengerexport.sqlite",
        )))

        asciinema_root = Path(os.environ.get("LYNCHPIN_ASCIINEMA_ROOT", captures_root / "asciinema"))
        audio_root = Path(os.environ.get("LYNCHPIN_AUDIO_ROOT", captures_root / "audio/raw"))
        screenshot_root = Path(os.environ.get("LYNCHPIN_SCREENSHOT_ROOT", captures_root / "screenshot"))
        keylog_root = Path(os.environ.get("LYNCHPIN_KEYLOG_ROOT", captures_root / "keylog"))

        cache_dir = Path(os.environ.get("LYNCHPIN_CACHE_DIR", repo_artefacts_root / "lynchpin/cache"))
        dendron_root = Path(os.environ.get("LYNCHPIN_DENDRON_ROOT", "/realm/project/knowledgebase"))

        raindrop_dir = Path(os.environ.get("LYNCHPIN_RAINDROP_DIR", exports_root / "raindrop/raw"))
        raindrop_csv = _resolve_raindrop_csv(os.environ.get("LYNCHPIN_RAINDROP_CSV"), raindrop_dir)
        goodreads_library = Path(os.environ.get(
            "LYNCHPIN_GOODREADS_LIBRARY", exports_root / "goodreads/raw/library_export.csv"
        ))
        wykop_root = Path(os.environ.get("LYNCHPIN_WYKOP_ROOT", exports_root / "wykop/raw"))
        wykop_username = os.environ.get("LYNCHPIN_WYKOP_USER", "Sinity")
        samsung_gdpr_cloud_dir = Path(os.environ.get(
            "LYNCHPIN_SAMSUNG_GDPR_CLOUD", exports_root / "health/raw/samsung-gdpr-cloud"
        ))

        cache_dir.mkdir(parents=True, exist_ok=True)

        return cls(
            repo_root=repo_root, sinnix_root=sinnix_root, data_root=data_root,
            captures_root=captures_root, exports_root=exports_root, libraries_root=libraries_root,
            knowledgebase_root=knowledgebase_root,
            knowledge_archive_root=knowledge_archive_root,
            repo_artefacts_root=repo_artefacts_root,
            session_registry_dir=session_registry_dir,
            artefact_catalog=artefact_catalog,
            analysis_output_dir=analysis_output_dir,
            session_ledger_output=session_ledger_output,
            artefact_ledger_output=artefact_ledger_output,
            session_summary_dir=session_summary_dir,
            session_summary_log=session_summary_log,
            velocity_output=velocity_output,
            webhistory_report_dir=webhistory_report_dir,
            activitywatch_db=aw_db, atuin_db=atuin_db, baseline_dir=baseline_dir,
            webhistory_raw_dir=webhistory_raw_dir, webhistory_dir=webhistory_dir,
            webhistory_ndjson=webhistory_ndjson, sleep_jsonl=sleep_jsonl,
            codex_sessions_root=codex_sessions_root, reddit_export_dir=reddit_export_dir,
            spotify_root=spotify_root, polylogue_root=polylogue_root,
            polylogue_archive_root=polylogue_archive_root,
            fbmessenger_gdpr_root=fbmessenger_gdpr_root, fbmessenger_db=fbmessenger_db,
            asciinema_root=asciinema_root, audio_root=audio_root,
            screenshot_root=screenshot_root, keylog_root=keylog_root,
            cache_dir=cache_dir, dendron_root=dendron_root,
            raindrop_dir=raindrop_dir, raindrop_csv=raindrop_csv,
            goodreads_library=goodreads_library, wykop_root=wykop_root,
            wykop_username=wykop_username,
            samsung_gdpr_cloud_dir=samsung_gdpr_cloud_dir,
        )


_CONFIG: Optional[LynchpinConfig] = None


def get_config() -> LynchpinConfig:
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = LynchpinConfig.from_env()
    return _CONFIG


# ── Path resolution helpers ───────────────────────────────────────────────────


def resolve_latest_dated_dir(root: Path, ignore: Optional[set[str]] = None) -> Optional[Path]:
    if not root.exists():
        return None
    ignore = ignore or set()
    subdirs = [c for c in root.iterdir() if c.is_dir() and c.name not in ignore]
    if not subdirs:
        return None
    dated: list[tuple[datetime, Path]] = []
    fallback: list[Path] = []
    for path in subdirs:
        try:
            dated.append((datetime.strptime(path.name, "%Y-%m-%d"), path))
        except ValueError:
            fallback.append(path)
    if dated:
        dated.sort(key=lambda x: x[0], reverse=True)
        return dated[0][1]
    fallback.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return fallback[0]


def _resolve_reddit_export(env_value: Optional[str], default_root: Path) -> Optional[Path]:
    if env_value:
        c = Path(env_value)
        return c if c.exists() else None
    return resolve_latest_dated_dir(default_root, ignore={"raw"})


def _resolve_spotify_export(root: Path) -> Path:
    if (root / "Spotify Account Data").exists() or (root / "Spotify Extended Streaming History").exists():
        return root
    candidate = resolve_latest_dated_dir(root, ignore={"raw", "archive", "legacy", "derived", "derivative"})
    if candidate and ((candidate / "Spotify Account Data").exists() or (candidate / "Spotify Extended Streaming History").exists()):
        return candidate
    return root


def _resolve_raindrop_csv(env_value: Optional[str], root: Path) -> Optional[Path]:
    if env_value:
        c = Path(env_value)
        return c if c.exists() else None
    if not root.exists():
        return None
    candidates = sorted(root.glob("raindrop*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def _resolve_fbmessenger_db(*candidates: Path) -> str:
    for path in candidates:
        if path.exists():
            return str(path)
    return str(candidates[0])
