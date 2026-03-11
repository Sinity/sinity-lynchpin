from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_repo_python(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["nix", "develop", "--command", "python", "-c", script],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )


def test_history_cleanup_module_entrypoint_imports() -> None:
    script = textwrap.dedent(
        """
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-m", "lynchpin.analysis.history_cleanup", "--help"],
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "prepare-wave" in result.stdout
        assert "normalize-wave" in result.stdout
        assert "build-review-bundles" in result.stdout
        """
    )
    result = _run_repo_python(script)
    assert result.returncode == 0, result.stderr


def test_history_cleanup_prepare_wave_writes_manifest() -> None:
    script = textwrap.dedent(
        """
        import json
        import subprocess
        import sys
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "wave"
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "lynchpin.analysis.history_cleanup",
                    "prepare-wave",
                    "--repo",
                    ".",
                    "--output-dir",
                    str(out_dir),
                    "--wave-name",
                    "demo",
                    "--start-index",
                    "1",
                    "--count",
                    "3",
                    "--owned-size",
                    "2",
                    "--overlap",
                    "1",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            assert result.returncode == 0, result.stderr
            manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
            assert manifest["wave"] == "demo"
            assert len(manifest["ranges"]) == 2, manifest
        """
    )
    result = _run_repo_python(script)
    assert result.returncode == 0, result.stderr


def test_history_cleanup_launch_pack_examples_are_valid_json() -> None:
    manifest_example = REPO_ROOT / "lynchpin/analysis/history_cleanup/templates/launch-pack-manifest.example.json"
    progress_example = REPO_ROOT / "lynchpin/analysis/history_cleanup/templates/structural-execution-progress.example.json"
    json.loads(manifest_example.read_text(encoding="utf-8"))
    json.loads(progress_example.read_text(encoding="utf-8"))
