from __future__ import annotations

import csv
import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from lynchpin.sources import chisel
from lynchpin.sources.chisel_metrics import build_metrics


@dataclass(frozen=True)
class File:
    path: str
    role: str
    included: bool
    sha256: str
    size_bytes: int
    excluded_by: tuple[str, ...] = ()


@dataclass(frozen=True)
class Inventory:
    root: Path
    files: tuple[File, ...]
    project: str = "sample"
    generated_at: str = "2026-09-26T00:00:00Z"


def _file(root: Path, path: str, role: str, included: bool = True) -> File:
    content = (root / path).read_bytes() if included else b""
    return File(path, role, included, hashlib.sha256(content).hexdigest(), len(content))


def test_unreadable_source_keeps_complete_total_unknown(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    inventory = Inventory(root, (File("src/lost.py", "implementation", False, "", 0, ("unreadable",)),))
    summary = build_metrics(inventory, tmp_path / "package")
    assert summary["measured_maintained_code_lines"] == 0
    assert summary["maintained_code_lines"] is None
    assert summary["maintained_code_coverage_complete"] is False
    assert summary["populations"]["unknown"] == 1


def test_context_prose_and_fenced_code_never_enter_maintained_loc(
    monkeypatch, tmp_path: Path
) -> None:
    root = tmp_path / "captured" / "source"
    root.mkdir(parents=True)
    files = {
        "src/main.py": "def run():\n    return 1\n",
        "tests/test_main.py": "def test_run():\n    assert True\n",
        "docs/guide.md": "lots of prose\n```python\ndef fake():\n    pass\n```\n",
        ".agent/scratchpad.md": "scratch notes\n```python\ndef invented():\n    pass\n```\n",
        "generated/output.py": "def generated():\n    pass\n",
    }
    for path, content in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    records = (
        _file(root, "src/main.py", "implementation"),
        _file(root, "tests/test_main.py", "tests"),
        _file(root, "docs/guide.md", "documentation"),
        _file(root, ".agent/scratchpad.md", "context"),
        _file(root, "generated/output.py", "unclassified"),
    )
    calls: list[list[str]] = []

    def fake_run(command, *, cwd, capture_output, text, check):
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps(
                {
                    "Python": {
                        "reports": [
                            {"name": str(root / "src/main.py"), "stats": {"code": 2, "comments": 0, "blanks": 0}},
                            {"name": str(root / "tests/test_main.py"), "stats": {"code": 2, "comments": 0, "blanks": 0}},
                        ]
                    }
                }
            ),
            "",
        )

    monkeypatch.setattr("lynchpin.sources.chisel_metrics.shutil.which", lambda _: "/bin/tokei")
    monkeypatch.setattr("lynchpin.sources.chisel_metrics.subprocess.run", fake_run)
    inventory = Inventory(root, records)
    package = tmp_path / "package"
    summary = build_metrics(inventory, package)
    assert calls[0][calls[0].index("--") + 1 :] == ["src/main.py", "tests/test_main.py"]
    assert summary["known_maintained_code_lines"] == 4
    assert summary["maintained_code_lines"] == 4
    assert summary["maintained_code_coverage_complete"] is True
    roles = {row["role"]: row for row in summary["roles"]}
    assert roles["documentation"]["bytes"] == len(files["docs/guide.md"].encode())
    assert roles["context"]["bytes"] == len(files[".agent/scratchpad.md"].encode())
    assert roles["unclassified"]["code"] is None
    csv_rows = list(csv.DictReader((package / "metrics/files.csv").open(encoding="utf-8")))
    assert {row["path"] for row in csv_rows} >= {"docs/guide.md", ".agent/scratchpad.md"}


def test_missing_tokei_is_explicit_and_not_zero(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    path = root / "main.py"
    path.write_text("print('x')\n", encoding="utf-8")
    monkeypatch.setattr("lynchpin.sources.chisel_metrics.shutil.which", lambda _: None)
    summary = build_metrics(Inventory(root, (_file(root, "main.py", "implementation"),)), tmp_path / "pkg")
    assert summary["maintained_code_lines"] is None
    assert summary["coverage_gaps"] == ["tokei_not_installed"]
    assert {row["role"]: row for row in summary["roles"]}["implementation"]["loc_measured"] is False


def test_partial_tooling_measurement_keeps_subtotal_in_csv(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    for name in ("verify.py", "unrecognized.py"):
        (root / name).write_text("print(1)\n")
    inventory = Inventory(root, tuple(_file(root, name, "tooling") for name in ("verify.py", "unrecognized.py")))

    def partial_tokei(command, **_kwargs):
        payload = {"Python": {"reports": [{"name": str(root / "verify.py"),
            "stats": {"code": 1, "comments": 0, "blanks": 0}}]}}
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr("lynchpin.sources.chisel_metrics.shutil.which", lambda _: "/bin/tokei")
    monkeypatch.setattr("lynchpin.sources.chisel_metrics.subprocess.run", partial_tokei)
    package = tmp_path / "package"
    summary = build_metrics(inventory, package)
    role = next(row for row in summary["roles"] if row["role"] == "tooling")
    assert role["code"] is None and role["measured_code"] == 1
    assert role["measured_files"] == 1 and role["unknown_files"] == 1
    csv_role = next(row for row in csv.DictReader((package / "metrics/roles.csv").open()) if row["role"] == "tooling")
    assert csv_role["code"] == "" and csv_role["measured_code"] == "1"


def test_loc_ignore_rules_apply_in_order_to_captured_files(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "source"
    (root / "src").mkdir(parents=True)
    for path in ("src/drop.py", "src/keep.py"):
        (root / path).write_text("print(1)\n", encoding="utf-8")
    (root / ".tokeignore").write_text("src/**\n!src/keep.py\n", encoding="utf-8")
    records = tuple(_file(root, path, "implementation") for path in ("src/drop.py", "src/keep.py")) + (
        _file(root, ".tokeignore", "tooling"),
    )
    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        payload = {"Python": {"reports": [{"name": str(root / "src/keep.py"), "stats": {"code": 1, "comments": 0, "blanks": 0}}, {"name": str(root / ".tokeignore"), "stats": {"code": 2, "comments": 0, "blanks": 0}}]}}
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr("lynchpin.sources.chisel_metrics.shutil.which", lambda _: "/bin/tokei")
    monkeypatch.setattr("lynchpin.sources.chisel_metrics.subprocess.run", fake_run)
    package = tmp_path / "pkg"
    build_metrics(Inventory(root, records), package)
    assert commands[0][commands[0].index("--") + 1 :] == ["src/keep.py", ".tokeignore"]
    csv_rows = list(csv.DictReader((package / "metrics/files.csv").open(encoding="utf-8")))
    dropped = next(row for row in csv_rows if row["path"] == "src/drop.py")
    assert dropped["metric_excluded_reason"] == "metric_ignore"


def test_captured_hash_mismatch_fails_instead_of_emitting_metrics(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "main.py").write_text("print('changed')\n", encoding="utf-8")
    inv = Inventory(root, (File("main.py", "implementation", True, "0" * 64, 1),))
    with pytest.raises(ValueError, match="captured inventory hash mismatch"):
        build_metrics(inv, tmp_path / "pkg")
    assert not (tmp_path / "pkg" / "metrics").exists()


def test_real_tokei_smoke_uses_captured_source_paths(tmp_path: Path) -> None:
    if shutil.which("tokei") is None:
        pytest.skip("tokei is unavailable")
    root = tmp_path / "source"
    root.mkdir()
    (root / "main.py").write_text("print('x')\n", encoding="utf-8")
    inv = Inventory(root, (_file(root, "main.py", "implementation"),))
    summary = build_metrics(inv, tmp_path / "pkg")
    assert summary["maintained_code_lines"] == 1
    row = json.loads((tmp_path / "pkg" / "metrics/files.jsonl").read_text().splitlines()[0])
    assert row["path"] == "main.py"


def test_legacy_composition_does_not_classify_context_as_maintained(
    monkeypatch, tmp_path: Path
) -> None:
    out = tmp_path / "sample"
    out.mkdir()
    (out / "sample-tokei-stats.json").write_text(
        json.dumps(
            {
                "buckets": {
                    "implementation": {"code": 10},
                    "tests": {"code": 5},
                    "documentation": {"code": 800},
                    "context": {"code": 900},
                    "unclassified": {"files": 1, "code": None, "loc_measured": False},
                }
            }
        ),
        encoding="utf-8",
    )
    plan = chisel.RepoPlan("sample", tmp_path, ())
    row = chisel._composition_rows((plan,), tmp_path)[0]
    assert row["Known maintained code"] == 15
    assert row["Maintained code"] is None
    assert row["Test share of production+tests"] is None
    assert row["Unclassified"] is None


def test_embedded_report_blobs_do_not_inflate_host_file_stats() -> None:
    bucket = chisel._empty_stats_bucket("docs")
    chisel._add_report_stats(
        bucket,
        "Markdown",
        {
            "code": 1,
            "comments": 2,
            "blanks": 3,
            "blobs": {"Python": {"code": 50, "comments": 0, "blanks": 0}},
        },
    )
    assert bucket["code"] == 1
    assert "Python" not in bucket["languages"]
