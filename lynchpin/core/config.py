"""Lynchpin configuration and canonical input roots.

`LynchpinConfig` is the single place where the live source modules resolve
their default filesystem roots. The important boundaries are:

- local app state under `~/.local/share/...` for ActivityWatch and Atuin,
- canonical raw and processed exports under `/realm/data/...`,
- local repos under `/realm/project/...`,
- repo-local generated registries, datasets, and archives under `.lynchpin/`.

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
    local_root: Path
    sinnix_root: Path
    data_root: Path
    captures_root: Path
    exports_root: Path
    derived_root: Path
    libraries_root: Path
    knowledgebase_root: Path
    knowledge_archive_root: Path
    repo_artefacts_root: Path
    session_registry_dir: Path
    artefact_catalog: Path
    analysis_output_dir: Path
    session_ledger_output: Path
    artefact_ledger_output: Path
    velocity_output: Path
    webhistory_report_dir: Path
    # Source paths
    activitywatch_db: Path
    activitywatch_archive_db_dir: Path
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
    polylogue_db: Path
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
    clipboard_live_file: Path
    clipboard_export_files: tuple[Path, ...]
    irc_root: Path
    raw_log_file: Path
    machine_capture_root: Path
    machine_host_root: Path
    machine_telemetry_db: Path
    sinnix_generations_jsonl: Path
    borg_drill_jsonl: Path
    sinnix_runtime_inventory_json: Path
    browser_bookmarks_root: Path
    arbtt_root: Path
    teams_root: Path

    def available_sources(self) -> dict[str, bool]:
        """Check which data sources actually have data on disk."""
        return {
            "activitywatch": self.activitywatch_db.exists(),
            "atuin": self.atuin_db.exists(),
            "git_baseline": (self.baseline_dir / "git_numstat.jsonl").exists() if self.baseline_dir.exists() else False,
            "webhistory": self.webhistory_ndjson is not None and self.webhistory_ndjson.exists(),
            "sleep": self.sleep_jsonl.exists(),
            "codex": self.codex_sessions_root.exists(),
            "reddit": self.reddit_export_dir is not None and self.reddit_export_dir.exists(),
            "spotify": self.spotify_root.exists(),
            "polylogue": (
                self.polylogue_db.exists()
                or self.polylogue_root.exists()
                or self.polylogue_archive_root.exists()
            ),
            "fbmessenger": self.fbmessenger_gdpr_root.exists() or Path(self.fbmessenger_db).exists(),
            "asciinema": self.asciinema_root.exists(),
            "keylog": (self.keylog_root / "logs").exists(),
            "goodreads": self.goodreads_library.exists(),
            "raindrop": self.raindrop_csv is not None and self.raindrop_csv.exists(),
            "wykop": self.wykop_root.exists(),
            "dendron": self.dendron_root.exists(),
            "samsung_gdpr_cloud": self.samsung_gdpr_cloud_dir.exists(),
            "clipboard": self.clipboard_live_file.exists() or any(path.exists() for path in self.clipboard_export_files),
            "irc": self.irc_root.exists(),
            "irc_raw": (self.irc_root / "_raw").exists(),
            "raw_log": self.raw_log_file.exists(),
            "machine": self.machine_telemetry_db.exists(),
            "gmail_takeout": (self.exports_root / "google/raw/takeout").exists(),
            "raindrop_live": _raindrop_live_available(),
            "sinnix_runtime_inventory": self.sinnix_runtime_inventory_json.exists(),
            "browser_bookmarks": self.browser_bookmarks_root.exists(),
            "arbtt": self.arbtt_root.exists(),
        }

    @classmethod
    def from_env(cls) -> LynchpinConfig:
        repo_root = Path(os.environ.get("LYNCHPIN_REPO_ROOT", Path(__file__).resolve().parents[2]))
        data_root = Path(os.environ.get("LYNCHPIN_DATA_ROOT", "/realm/data"))
        captures_root = Path(os.environ.get("LYNCHPIN_CAPTURES_ROOT", data_root / "captures"))
        exports_root = Path(os.environ.get("LYNCHPIN_EXPORTS_ROOT", data_root / "exports"))
        derived_root = Path(os.environ.get("LYNCHPIN_DERIVED_ROOT", data_root / "derived/lynchpin"))
        libraries_root = Path(os.environ.get("LYNCHPIN_LIBRARIES_ROOT", data_root / "libraries"))
        sinnix_root = Path(os.environ.get("LYNCHPIN_SINNIX_ROOT", "/realm/project/sinnix"))
        local_root = _default_local_root(repo_root, os.environ.get("LYNCHPIN_LOCAL_ROOT"))
        generated_root = _non_legacy_generated_path(
            os.environ.get("LYNCHPIN_GENERATED_ROOT"), local_root / "generated"
        )
        knowledgebase_root = _non_legacy_generated_path(
            os.environ.get("LYNCHPIN_KNOWLEDGEBASE_ROOT"), generated_root
        )
        knowledge_archive_root = _non_legacy_generated_path(
            os.environ.get("LYNCHPIN_KNOWLEDGE_ARCHIVE_ROOT"), knowledgebase_root / "archive"
        )
        repo_artefacts_root = _non_legacy_generated_path(
            os.environ.get("LYNCHPIN_REPO_ARTEFACTS_ROOT"), generated_root
        )
        registry_root = _non_legacy_generated_path(
            os.environ.get("LYNCHPIN_REGISTRY_ROOT"), knowledgebase_root / "registry"
        )
        session_registry_dir = _non_legacy_generated_path(
            os.environ.get("LYNCHPIN_SESSION_REGISTRY_DIR"), registry_root / "sessions"
        )
        artefact_catalog = _non_legacy_generated_path(
            os.environ.get("LYNCHPIN_ARTEFACT_CATALOG"), registry_root / "artefact_catalog.json"
        )
        analysis_output_dir = _non_legacy_generated_path(
            os.environ.get("LYNCHPIN_ANALYSIS_OUTPUT_DIR"), repo_artefacts_root / "analysis"
        )
        session_ledger_output = _non_legacy_generated_path(
            os.environ.get("LYNCHPIN_SESSION_LEDGER_OUTPUT"),
            repo_artefacts_root / "knowledge/ledgers/session_index.csv",
        )
        artefact_ledger_output = _non_legacy_generated_path(
            os.environ.get("LYNCHPIN_ARTEFACT_LEDGER_OUTPUT"),
            repo_artefacts_root / "knowledge/ledgers/artefact_index.csv",
        )
        velocity_output = _non_legacy_generated_path(
            os.environ.get("LYNCHPIN_VELOCITY_OUTPUT"), repo_artefacts_root / "meta/velocity.html"
        )
        webhistory_report_dir = _non_legacy_generated_path(
            os.environ.get("LYNCHPIN_WEBHISTORY_REPORT_DIR"), repo_artefacts_root / "webhistory"
        )

        aw_db = Path(os.environ.get(
            "LYNCHPIN_ACTIVITYWATCH_DB", "~/.local/share/activitywatch/aw-server-rust/sqlite.db"
        )).expanduser()
        aw_archive_db_dir = Path(os.environ.get(
            "LYNCHPIN_ACTIVITYWATCH_ARCHIVE_DB_DIR",
            exports_root / "activitywatch/processed/archive-dbs",
        ))
        atuin_db = Path(os.environ.get("LYNCHPIN_ATUIN_DB", "~/.local/share/atuin/history.db")).expanduser()
        baseline_dir = _non_legacy_generated_path(
            os.environ.get("LYNCHPIN_BASELINE_DIR"), repo_artefacts_root / "baseline/latest"
        )

        webhistory_raw_dir = Path(os.environ.get("LYNCHPIN_WEBHISTORY_RAW_DIR", captures_root / "webhistory/gestalt/raw"))
        webhistory_dir = Path(os.environ.get("LYNCHPIN_WEBHISTORY_DIR", captures_root / "webhistory/gestalt/data"))
        webhistory_ndjson_path = Path(os.environ.get(
            "LYNCHPIN_WEBHISTORY_NDJSON", captures_root / "webhistory/gestalt/derived/full_history.ndjson"
        ))
        webhistory_ndjson = webhistory_ndjson_path

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
        xdg_data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")).expanduser()
        polylogue_db = Path(
            os.environ.get(
                "LYNCHPIN_POLYLOGUE_DB",
                os.environ.get("POLYLOGUE_DB_PATH", xdg_data_home / "polylogue" / "polylogue.db"),
            )
        ).expanduser()

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

        cache_dir = Path(os.environ.get("LYNCHPIN_CACHE_DIR", local_root / "cache/lynchpin"))
        dendron_root = Path(os.environ.get("LYNCHPIN_DENDRON_ROOT", "/realm/data/knowledgebase"))

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
        clipboard_live_file = Path(os.environ.get(
            "LYNCHPIN_CLIPBOARD_LIVE_FILE", "~/.config/clipse/clipboard_history.json"
        )).expanduser()
        clipboard_export_files = tuple(
            Path(item).expanduser()
            for item in os.environ.get(
                "LYNCHPIN_CLIPBOARD_EXPORT_FILES",
                ":".join([
                    str(exports_root / "clipboard/clipse/raw/2026-02-01/clipboard_history.json"),
                    str(exports_root / "clipboard/clipse/raw/2026-01-12/clipboard_history.json"),
                    str(exports_root / "chatlog/raw/legacy-raw/gemini_ai_studio_local_dump_20260115/clipboard_history_selections.md"),
                ]),
            ).split(":")
            if item
        )
        irc_root = Path(os.environ.get("LYNCHPIN_IRC_ROOT", captures_root / "comms/irc"))
        raw_log_file = Path(os.environ.get(
            "LYNCHPIN_RAW_LOG_FILE", "/realm/data/knowledgebase/logs.raw-log.md"
        ))
        machine_capture_root = Path(os.environ.get("LYNCHPIN_MACHINE_CAPTURE_ROOT", captures_root / "machine"))
        machine_host = os.environ.get("LYNCHPIN_MACHINE_HOST", "sinnix-prime")
        default_machine_host_root = (
            machine_capture_root
            if (machine_capture_root / "telemetry.sqlite").exists()
            else machine_capture_root / machine_host
        )
        machine_host_root = Path(os.environ.get("LYNCHPIN_MACHINE_HOST_ROOT", default_machine_host_root))
        machine_telemetry_db = Path(os.environ.get("LYNCHPIN_MACHINE_TELEMETRY_DB", machine_host_root / "telemetry.sqlite"))
        sinnix_generations_jsonl = Path(os.environ.get(
            "LYNCHPIN_SINNIX_GENERATIONS_JSONL",
            machine_capture_root / "generations.jsonl",
        ))
        borg_drill_jsonl = Path(os.environ.get(
            "LYNCHPIN_BORG_DRILL_JSONL",
            machine_capture_root / "borg_drill.jsonl",
        ))
        sinnix_runtime_inventory_json = Path(os.environ.get(
            "LYNCHPIN_SINNIX_RUNTIME_INVENTORY_JSON",
            "/etc/sinnix/runtime-inventory.json",
        ))
        browser_bookmarks_root = Path(os.environ.get(
            "LYNCHPIN_BROWSER_BOOKMARKS_ROOT",
            captures_root / "webhistory/bookmarks",
        ))
        arbtt_root = Path(os.environ.get(
            "LYNCHPIN_ARBTT_ROOT",
            captures_root / "focus/arbtt",
        ))
        teams_root = Path(os.environ.get(
            "LYNCHPIN_TEAMS_ROOT",
            captures_root / "comms/teams",
        ))
        cache_dir.mkdir(parents=True, exist_ok=True)

        return cls(
            repo_root=repo_root, local_root=local_root, sinnix_root=sinnix_root, data_root=data_root,
            captures_root=captures_root, exports_root=exports_root, derived_root=derived_root, libraries_root=libraries_root,
            knowledgebase_root=knowledgebase_root,
            knowledge_archive_root=knowledge_archive_root,
            repo_artefacts_root=repo_artefacts_root,
            session_registry_dir=session_registry_dir,
            artefact_catalog=artefact_catalog,
            analysis_output_dir=analysis_output_dir,
            session_ledger_output=session_ledger_output,
            artefact_ledger_output=artefact_ledger_output,
            velocity_output=velocity_output,
            webhistory_report_dir=webhistory_report_dir,
            activitywatch_db=aw_db, activitywatch_archive_db_dir=aw_archive_db_dir,
            atuin_db=atuin_db, baseline_dir=baseline_dir,
            webhistory_raw_dir=webhistory_raw_dir, webhistory_dir=webhistory_dir,
            webhistory_ndjson=webhistory_ndjson, sleep_jsonl=sleep_jsonl,
            codex_sessions_root=codex_sessions_root, reddit_export_dir=reddit_export_dir,
            spotify_root=spotify_root, polylogue_root=polylogue_root,
            polylogue_archive_root=polylogue_archive_root,
            polylogue_db=polylogue_db,
            fbmessenger_gdpr_root=fbmessenger_gdpr_root, fbmessenger_db=fbmessenger_db,
            asciinema_root=asciinema_root, audio_root=audio_root,
            screenshot_root=screenshot_root, keylog_root=keylog_root,
            cache_dir=cache_dir, dendron_root=dendron_root,
            raindrop_dir=raindrop_dir, raindrop_csv=raindrop_csv,
            goodreads_library=goodreads_library, wykop_root=wykop_root,
            wykop_username=wykop_username,
            samsung_gdpr_cloud_dir=samsung_gdpr_cloud_dir,
            clipboard_live_file=clipboard_live_file,
            clipboard_export_files=clipboard_export_files,
            irc_root=irc_root,
            raw_log_file=raw_log_file,
            machine_capture_root=machine_capture_root,
            machine_host_root=machine_host_root,
            machine_telemetry_db=machine_telemetry_db,
            sinnix_generations_jsonl=sinnix_generations_jsonl,
            borg_drill_jsonl=borg_drill_jsonl,
            sinnix_runtime_inventory_json=sinnix_runtime_inventory_json,
            browser_bookmarks_root=browser_bookmarks_root,
            arbtt_root=arbtt_root,
            teams_root=teams_root,
        )


_CONFIG: Optional[LynchpinConfig] = None


def get_config() -> LynchpinConfig:
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = LynchpinConfig.from_env()
    return _CONFIG


# ── Path resolution helpers ───────────────────────────────────────────────────


def _default_local_root(repo_root: Path, env_value: str | None) -> Path:
    if env_value:
        return Path(env_value)
    checkout_root = Path("/realm/project/sinity-lynchpin")
    if "nix" in repo_root.parts and "store" in repo_root.parts and checkout_root.exists():
        return checkout_root / ".lynchpin"
    return repo_root / ".lynchpin"


def _non_legacy_generated_path(env_value: str | None, default: Path) -> Path:
    """Resolve generated-output roots without reviving retired temp roots."""
    if not env_value:
        return Path(default)
    candidate = Path(env_value)
    if "__lynchpin_exported" in candidate.parts:
        return Path(default)
    return candidate


def resolve_latest_dated_dir(root: Path, ignore: Optional[set[str]] = None) -> Optional[Path]:
    if not root.exists():
        return None
    ignore = ignore or set()
    subdirs = [c for c in root.iterdir() if c.is_dir() and c.name not in ignore]
    if not subdirs:
        return None
    dated: list[tuple[datetime, Path]] = []
    for path in subdirs:
        try:
            dated.append((datetime.strptime(path.name, "%Y-%m-%d"), path))
        except ValueError:
            continue
    if dated:
        dated.sort(key=lambda x: x[0], reverse=True)
        return dated[0][1]
    return None


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


def _raindrop_live_available() -> bool:
    import os
    if os.environ.get("RAINDROP_API_TOKEN", "").strip():
        return True
    return False


def _resolve_fbmessenger_db(*candidates: Path) -> str:
    for path in candidates:
        if path.exists():
            return str(path)
    return str(candidates[0])
