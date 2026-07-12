"""Repository-wide pytest safety boundary.

Default tests must never discover the operator's live data merely because they
run on the operator's workstation. Tests marked ``slow`` are explicit live or
long-running integrations and retain the caller's environment.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_operator_data(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> Iterator[None]:
    if request.node.get_closest_marker("slow") is not None:
        yield
        return

    root = tmp_path / "operator-data"
    data = root / "data"
    local = root / "local"
    isolated_paths = {
        "LYNCHPIN_DATA_ROOT": data,
        "LYNCHPIN_CAPTURES_ROOT": data / "captures",
        "LYNCHPIN_EXPORTS_ROOT": data / "exports",
        "LYNCHPIN_DERIVED_ROOT": data / "derived/lynchpin",
        "LYNCHPIN_LIBRARIES_ROOT": data / "libraries",
        "LYNCHPIN_LOCAL_ROOT": local,
        "LYNCHPIN_ACTIVITYWATCH_DB": root / "activitywatch.sqlite",
        "LYNCHPIN_ACTIVITYWATCH_ARCHIVE_DB_DIR": root / "activitywatch-archives",
        "LYNCHPIN_ATUIN_DB": root / "atuin.sqlite",
        "LYNCHPIN_CODEX_ROOT": root / "codex-sessions",
        "LYNCHPIN_POLYLOGUE_ROOT": root / "polylogue/markdown",
        "LYNCHPIN_POLYLOGUE_ARCHIVE_ROOT": root / "polylogue/archive",
        "LYNCHPIN_POLYLOGUE_DB": root / "polylogue/index.db",
        "POLYLOGUE_DB_PATH": root / "polylogue/index.db",
        "POLYLOGUE_ROOT": root / "polylogue-project",
        "LYNCHPIN_POLYLOGUE_PROJECT_ROOT": root / "polylogue-project",
        "LYNCHPIN_CLIPBOARD_LIVE_FILE": root / "clipboard.json",
        "LYNCHPIN_RAW_LOG_FILE": root / "raw-log.md",
        "LYNCHPIN_DENDRON_ROOT": root / "knowledgebase",
        "LYNCHPIN_SINNIX_RUNTIME_INVENTORY_JSON": root / "runtime-inventory.json",
        "LYNCHPIN_XTASK_HISTORY_DB": root / "xtask-history.db",
        "XDG_DATA_HOME": root / "xdg-data",
    }
    for name, path in isolated_paths.items():
        monkeypatch.setenv(name, str(path))
    monkeypatch.setenv("LYNCHPIN_CLIPBOARD_EXPORT_FILES", "")
    monkeypatch.setenv("LYNCHPIN_XTASK_HISTORY_ARCHIVE_DBS", "")

    import lynchpin.core.config as config_module

    monkeypatch.setattr(config_module, "_CONFIG", None)
    yield
    config_module._CONFIG = None
