"""The devshell banner reports the Polylogue archive Lynchpin actually reads."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "tool" / "devshell-motd"


def _sources_line(tmp_path: Path, archive: Path) -> str:
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "LYNCHPIN_REPO_ROOT": str(tmp_path),
        "LYNCHPIN_POLYLOGUE_ARCHIVE_ROOT": str(archive),
    }
    out = subprocess.run(["bash", str(_SCRIPT)], env=env, check=True, capture_output=True, text=True).stdout
    return next(line for line in out.splitlines() if line.startswith("sources"))


def test_polylogue_status_follows_the_split_archive(tmp_path: Path) -> None:
    """Fails if the banner checks a single legacy database file instead of the
    archive directory, or reports an absent archive without saying why."""
    archive = tmp_path / "state" / "polylogue"
    assert f"polylogue: no archive yet ({archive})" in _sources_line(tmp_path, archive)
    archive.mkdir(parents=True)
    (archive / "source.db").write_bytes(b"")
    assert f"polylogue: not indexed yet ({archive})" in _sources_line(tmp_path, archive)
    (archive / "index.db").write_bytes(b"")
    assert "polylogue:ok" in _sources_line(tmp_path, archive)
