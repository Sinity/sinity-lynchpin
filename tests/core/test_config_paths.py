"""Tests for Lynchpin generated-output path defaults."""

from __future__ import annotations

from pathlib import Path

from lynchpin.core.config import LynchpinConfig, _default_polylogue_db, get_config, resolve_latest_dated_dir


def test_default_pytest_config_isolated_from_operator_data(tmp_path: Path) -> None:
    cfg = get_config()

    assert cfg.data_root.is_relative_to(tmp_path)
    assert cfg.local_root.is_relative_to(tmp_path)
    assert cfg.activitywatch_db.is_relative_to(tmp_path)
    assert cfg.atuin_db.is_relative_to(tmp_path)
    assert cfg.polylogue_db.is_relative_to(tmp_path)
    assert cfg.raw_log_file.is_relative_to(tmp_path)


def test_generated_roots_default_to_repo_local_dotfolder(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    monkeypatch.setenv("LYNCHPIN_REPO_ROOT", str(repo_root))
    for key in (
        "LYNCHPIN_LOCAL_ROOT",
        "LYNCHPIN_GENERATED_ROOT",
        "LYNCHPIN_KNOWLEDGEBASE_ROOT",
        "LYNCHPIN_KNOWLEDGE_ARCHIVE_ROOT",
        "LYNCHPIN_REPO_ARTEFACTS_ROOT",
        "LYNCHPIN_REGISTRY_ROOT",
        "LYNCHPIN_SESSION_REGISTRY_DIR",
        "LYNCHPIN_ARTEFACT_CATALOG",
        "LYNCHPIN_ANALYSIS_OUTPUT_DIR",
        "LYNCHPIN_SESSION_LEDGER_OUTPUT",
        "LYNCHPIN_ARTEFACT_LEDGER_OUTPUT",
        "LYNCHPIN_VELOCITY_OUTPUT",
        "LYNCHPIN_WEBHISTORY_REPORT_DIR",
        "LYNCHPIN_BASELINE_DIR",
        "LYNCHPIN_CACHE_DIR",
    ):
        monkeypatch.delenv(key, raising=False)

    cfg = LynchpinConfig.from_env()

    assert cfg.knowledgebase_root == repo_root / ".lynchpin/generated"
    assert cfg.repo_artefacts_root == repo_root / ".lynchpin/generated"
    assert cfg.analysis_output_dir == repo_root / ".lynchpin/generated/analysis"
    assert cfg.baseline_dir == repo_root / ".lynchpin/generated/baseline/latest"
    assert cfg.velocity_output == repo_root / ".lynchpin/generated/meta/velocity.html"
    assert cfg.cache_dir == repo_root / ".lynchpin/cache/lynchpin"


def test_legacy_exported_roots_are_not_live_write_targets(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    legacy = Path("/realm/project/__lynchpin_exported")
    monkeypatch.setenv("LYNCHPIN_REPO_ROOT", str(repo_root))
    monkeypatch.setenv("LYNCHPIN_KNOWLEDGEBASE_ROOT", str(legacy))
    monkeypatch.setenv("LYNCHPIN_REPO_ARTEFACTS_ROOT", str(legacy / "repo-artefacts"))
    monkeypatch.setenv("LYNCHPIN_REGISTRY_ROOT", str(legacy / "registry"))
    monkeypatch.setenv("LYNCHPIN_ANALYSIS_OUTPUT_DIR", str(legacy / "repo-artefacts/analysis/derived"))
    monkeypatch.setenv("LYNCHPIN_BASELINE_DIR", str(legacy / "repo-artefacts/core/baseline/latest"))

    cfg = LynchpinConfig.from_env()

    assert "__lynchpin_exported" not in cfg.knowledgebase_root.parts
    assert "__lynchpin_exported" not in cfg.repo_artefacts_root.parts
    assert "__lynchpin_exported" not in cfg.session_registry_dir.parts
    assert "__lynchpin_exported" not in cfg.analysis_output_dir.parts
    assert "__lynchpin_exported" not in cfg.baseline_dir.parts


def test_packaged_nix_store_repo_root_defaults_to_checkout_local_root(monkeypatch, tmp_path: Path) -> None:
    checkout = Path("/realm/project/sinity-lynchpin")
    original_exists = Path.exists

    def fake_exists(self: Path) -> bool:
        if self == checkout:
            return True
        return original_exists(self)

    monkeypatch.setenv("LYNCHPIN_REPO_ROOT", "/nix/store/source-lynchpin")
    monkeypatch.delenv("LYNCHPIN_LOCAL_ROOT", raising=False)
    monkeypatch.setattr(Path, "exists", fake_exists)

    cfg = LynchpinConfig.from_env()

    assert cfg.local_root == Path("/realm/project/sinity-lynchpin/.lynchpin")
    assert cfg.cache_dir == Path("/realm/project/sinity-lynchpin/.lynchpin/cache/lynchpin")


def test_polylogue_db_env_override_wins(monkeypatch, tmp_path: Path) -> None:
    configured = tmp_path / "configured.db"
    monkeypatch.setenv("LYNCHPIN_POLYLOGUE_DB", str(configured))
    monkeypatch.setenv("POLYLOGUE_DB_PATH", str(tmp_path / "ignored.db"))

    assert _default_polylogue_db(tmp_path / "xdg") == configured


def test_polylogue_db_prefers_sinnix_archive_index(monkeypatch, tmp_path: Path) -> None:
    sinnix_db = tmp_path / "realm-db" / "polylogue" / "index.db"
    sinnix_db.parent.mkdir(parents=True)
    sinnix_db.touch()
    xdg_db = tmp_path / "xdg" / "polylogue" / "index.db"
    xdg_db.parent.mkdir(parents=True)
    xdg_db.touch()
    monkeypatch.delenv("LYNCHPIN_POLYLOGUE_DB", raising=False)
    monkeypatch.delenv("POLYLOGUE_DB_PATH", raising=False)

    assert _default_polylogue_db(tmp_path / "xdg", sinnix_index_db=sinnix_db) == sinnix_db


def test_polylogue_db_falls_back_to_xdg_index(monkeypatch, tmp_path: Path) -> None:
    xdg_db = tmp_path / "xdg" / "polylogue" / "index.db"
    xdg_db.parent.mkdir(parents=True)
    xdg_db.touch()
    monkeypatch.delenv("LYNCHPIN_POLYLOGUE_DB", raising=False)
    monkeypatch.delenv("POLYLOGUE_DB_PATH", raising=False)

    assert _default_polylogue_db(tmp_path / "xdg", sinnix_index_db=tmp_path / "missing.db") == xdg_db


def test_resolve_latest_dated_dir_ignores_non_dated_directories(tmp_path: Path) -> None:
    (tmp_path / "raw").mkdir()
    (tmp_path / "not-an-export").mkdir()
    older = tmp_path / "2026-01-01"
    newer = tmp_path / "2026-05-01"
    older.mkdir()
    newer.mkdir()

    assert resolve_latest_dated_dir(tmp_path, ignore={"raw"}) == newer


def test_resolve_latest_dated_dir_does_not_pick_mtime_candidate(tmp_path: Path) -> None:
    (tmp_path / "not-an-export").mkdir()

    assert resolve_latest_dated_dir(tmp_path) is None
