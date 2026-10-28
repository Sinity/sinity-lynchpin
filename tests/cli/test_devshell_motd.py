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


