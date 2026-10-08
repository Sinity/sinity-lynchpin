from datetime import datetime, timezone
from pathlib import Path

import pytest

from lynchpin.ingest.code_snapshots_materialize import _results_to_rows
from lynchpin.sources import chisel


@pytest.mark.parametrize(
    ("project", "path"),
    [
        ("sinnix", "pkgs/agentctl/agentctl/cli.py"),
        ("sinnix", "browser-extensions/nav-capture/background.js"),
        ("polylogue", "polylogue/archive/query/engine.py"),
        ("polylogue", "polylogue/daemon/runtime.py"),
        ("polylogue", "polylogue/pipeline/runner.py"),
        ("polylogue", "webui/src/App.tsx"),
        ("polylogue", "devtools/main.py"),
        ("sinity-lynchpin", "lynchpin/ingest/code_snapshots_materialize.py"),
        ("sinity-lynchpin", "lynchpin/substrate/connection.py"),
        ("sinity-lynchpin", "lynchpin/mcp/server.py"),
        ("sinity-lynchpin", "lynchpin/materializers/catalog.py"),
        ("sinity-lynchpin", "lynchpin/materialization.py"),
    ],
)
def test_maintained_source_families_reach_repomix(
    project: str, path: str, monkeypatch, tmp_path: Path
) -> None:
    plan = chisel.REPO_PLANS[project]
    calls = []

    def capture(binary, output, plan, args, git, generated, log):
        calls.append(args)
        return output.stem, 0

    monkeypatch.setattr(chisel, "_run_repomix", capture)
    git = {"branch": "main", "commit": "abc", "dirty": False}
    for part in plan.slices:
        chisel._run_slice("repomix", tmp_path, plan, part, git, "now", [])
    assert any(
        chisel._glob_any(path, args[args.index("--include") + 1].split(","))
        and not chisel._glob_any(path, args[args.index("--ignore") + 1].split(","))
        for args in calls
    )
    calls.clear()
    chisel._run_compressed("repomix", tmp_path, plan, git, "now", [])
    args = calls[0]
    assert chisel._glob_any(path, args[args.index("--include") + 1].split(","))
    assert not chisel._glob_any(path, args[args.index("--ignore") + 1].split(","))


def test_generated_artifact_names_retain_their_kind_in_promotion(tmp_path: Path) -> None:
    output = tmp_path / "example"
    output.mkdir()
    for name in ("example-git-log-all-refs.xml", "example-compressed.xml", "example-code.xml"):
        (output / name).write_text("<files/>")
    _, rows = _results_to_rows(
        {"project": {"example": {"status": "generated"}}},
        datetime.now(timezone.utc), tmp_path,
    )
    assert {row["filename"]: row["kind"] for row in rows} == {
        "example-git-log-all-refs.xml": "xml_git_log",
        "example-compressed.xml": "xml_compressed",
        "example-code.xml": "xml_slice",
    }


def test_rust_attribution_uses_approved_files_without_rescanning_checkout(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "crate/example/src/lib.rs"
    source.parent.mkdir(parents=True)
    source.write_text("#[cfg(test)]\nmod tests {\n    fn works() {}\n}\n")
    test_file = source.with_name("lib_tests.rs")
    test_file.write_text("#[test]\nfn works() {}\n")
    plan = chisel.RepoPlan("example", tmp_path, ())
    visible = {p.relative_to(tmp_path).as_posix() for p in (source, test_file)}

    def reject_walk(*args, **kwargs):
        raise AssertionError("approved files must not trigger a checkout traversal")

    monkeypatch.setattr(Path, "rglob", reject_walk)
    assert chisel._rust_inline_test_stats(plan, visible)["files"] == 1
    assert chisel._rust_split_test_file_stats(plan, visible)["files"] == 1
    assert chisel._rust_inline_test_stats(plan, set())["files"] == 0
