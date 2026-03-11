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
        assert "compile-structural-plan" in result.stdout
        assert "finalize-message-wave" in result.stdout
        assert "verify-rollback-drill" in result.stdout
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
    template_dir = REPO_ROOT / "lynchpin/analysis/history_cleanup/templates"
    for path in template_dir.glob("*.json"):
        json.loads(path.read_text(encoding="utf-8"))


def test_history_cleanup_finalize_message_wave_merges_batches() -> None:
    script = textwrap.dedent(
        """
        import json
        import subprocess
        import sys
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proposals = root / "proposals"
            proposals.mkdir()

            thin_corpus = {
                "head_sha": "deadbeef",
                "commit_count": 2,
                "thin_count": 2,
                "batch_size": 1,
                "commits": [
                    {"sha": "aaa111", "index": 1},
                    {"sha": "bbb222", "index": 2},
                ],
            }
            (root / "thin.json").write_text(json.dumps(thin_corpus), encoding="utf-8")

            weak = {
                "batch_id": "batch-01",
                "agent": "weak",
                "items": [
                    {
                        "sha": "aaa111",
                        "original_subject": "old one",
                        "full_patch_confirmed": True,
                        "adjacent_context_used": [],
                        "proposed_subject": "better one",
                        "proposed_body": "short body only here",
                        "confidence": "high",
                        "why_basis": "diff",
                    }
                ],
            }
            rich = {
                "batch_id": "batch-01",
                "agent": "rich",
                "items": [
                    {
                        "sha": "aaa111",
                        "original_subject": "old one",
                        "full_patch_confirmed": True,
                        "adjacent_context_used": ["prev", "next"],
                        "proposed_subject": "better one",
                        "proposed_body": "This richer body explains the concrete subsystem change, the resulting effect, and the local rationale with materially more detail.",
                        "confidence": "medium",
                        "why_basis": "diff plus adjacent",
                    }
                ],
            }
            second = {
                "batch_id": "batch-02",
                "agent": "solo",
                "items": [
                    {
                        "sha": "bbb222",
                        "original_subject": "old two",
                        "full_patch_confirmed": True,
                        "adjacent_context_used": [],
                        "proposed_subject": "better two",
                        "proposed_body": "Another commit body with enough detail to clear the minimum quality bar.",
                        "confidence": "high",
                        "why_basis": "diff",
                    }
                ],
            }

            (proposals / "weak-batch-01.json").write_text(json.dumps(weak), encoding="utf-8")
            (proposals / "rich-batch-01.json").write_text(json.dumps(rich), encoding="utf-8")
            (proposals / "solo-batch-02.json").write_text(json.dumps(second), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "lynchpin.analysis.history_cleanup",
                    "finalize-message-wave",
                    "--thin-corpus",
                    str(root / "thin.json"),
                    "--proposals-dir",
                    str(proposals),
                    "--canonical-json",
                    str(root / "canonical.json"),
                    "--duplicate-resolution-json",
                    str(root / "dupes.json"),
                    "--rewrite-map-json",
                    str(root / "rewrite.json"),
                    "--summary-json",
                    str(root / "summary.json"),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            assert result.returncode == 0, result.stderr

            canonical = json.loads((root / "canonical.json").read_text(encoding="utf-8"))
            dupes = json.loads((root / "dupes.json").read_text(encoding="utf-8"))
            rewrite = json.loads((root / "rewrite.json").read_text(encoding="utf-8"))

            assert canonical["canonical_batch_count"] == 2
            assert dupes[0]["chosen_file"] == "rich-batch-01.json"
            assert len(rewrite) == 2
            assert rewrite[0]["sha"] == "aaa111"
            assert "richer body explains the concrete subsystem change" in rewrite[0]["rewritten_message"]
        """
    )
    result = _run_repo_python(script)
    assert result.returncode == 0, result.stderr


def test_history_cleanup_verify_rollback_drill_reports_restored_heads() -> None:
    script = textwrap.dedent(
        """
        import json
        import subprocess
        import sys
        import tempfile
        from pathlib import Path

        def git(cwd, *args):
            return subprocess.run(
                ["git", *args],
                cwd=cwd,
                text=True,
                capture_output=True,
                check=True,
            )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            git(repo, "init")
            git(repo, "config", "user.name", "Tester")
            git(repo, "config", "user.email", "tester@example.com")
            (repo / "demo.txt").write_text("hello\\n", encoding="utf-8")
            git(repo, "add", "demo.txt")
            git(repo, "commit", "-m", "initial")
            git(repo, "branch", "backup/test")

            bundle = root / "repo.bundle"
            git(repo, "bundle", "create", str(bundle), "HEAD")

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "lynchpin.analysis.history_cleanup",
                    "verify-rollback-drill",
                    "--repo",
                    str(repo),
                    "--backup-ref",
                    "backup/test",
                    "--bundle",
                    str(bundle),
                    "--output-json",
                    str(root / "rollback.json"),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            assert result.returncode == 0, result.stderr
            payload = json.loads((root / "rollback.json").read_text(encoding="utf-8"))
            assert payload["ok"] is True
            assert {row["kind"] for row in payload["checks"]} == {"backup_ref", "bundle"}
            assert len({row["restored_head"] for row in payload["checks"]}) == 1
            assert len({row["restored_tree"] for row in payload["checks"]}) == 1
        """
    )
    result = _run_repo_python(script)
    assert result.returncode == 0, result.stderr
