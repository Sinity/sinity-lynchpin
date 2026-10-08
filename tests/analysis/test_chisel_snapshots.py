from pathlib import Path
import json
import subprocess
import tarfile

from lynchpin.sources import chisel
from lynchpin.sources.chisel import RepoPlan
from lynchpin.sources.chisel_options import BuildOptions
from lynchpin.sources.chisel_snapshots import capture_catalogue, verify_snapshot


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def test_default_source_and_dirty_overlay_survive_later_development(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "config", "user.name", "Fixture")
    (repo / "main.py").write_text("value = 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "Initial")
    main = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "candidate")
    (repo / "main.py").write_text("value = 2\n")
    git(repo, "commit", "-am", "Candidate")
    (repo / "main.py").write_text("value = 3\n")
    (repo / "extra.py").write_text("extra = True\n")
    package = tmp_path / "package"
    inventory = capture_catalogue(RepoPlan("sample", repo, ()), package,
        BuildOptions(refs=(("sample", "candidate"),)))
    assert inventory.revision == main
    assert (package / "source/main.py").read_text() == "value = 1\n"
    assert (package / "snapshots/worktree/files/main.py").read_text() == "value = 3\n"
    assert (package / "snapshots/candidate-1/files/main.py").read_text() == "value = 2\n"
    assert json.loads((package / "snapshots/worktree/manifest.json").read_text())["dirty"] is True
    (repo / "main.py").write_text("later = 4\n")
    git(repo, "branch", "-f", "main", "HEAD")
    verify_snapshot(inventory)


def test_pinned_capture_ignores_export_ignore_attributes(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "config", "user.name", "Fixture")
    (repo / ".gitattributes").write_text("main.py export-ignore\n")
    (repo / "main.py").write_text("print(1)\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "Initial")
    capture_catalogue(RepoPlan("sample", repo, ()), tmp_path / "package", BuildOptions())
    assert (tmp_path / "package/source/main.py").read_text() == "print(1)\n"


def test_snapshot_differences_use_filtered_frozen_captures(tmp_path):
    from lynchpin.analysis.projects.chisel_reports import build_reports

    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "config", "user.name", "Fixture")
    (repo / "main.py").write_text("value = 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "Initial")
    git(repo, "checkout", "-b", "feature")
    (repo / "main.py").write_text("value = 2\n")
    sentinel = "SYNTHETIC_EXCLUDED_CREDENTIAL_SENTINEL"
    (repo / "credentials.json").write_text('{"token": "' + sentinel + '"}\n')
    git(repo, "add", ".")
    git(repo, "commit", "-m", "Feature with excluded credential")
    feature = git(repo, "rev-parse", "HEAD")

    root = tmp_path / "root"
    root.mkdir()
    package = root / "sample"
    plan = RepoPlan("sample", repo, ())
    inventory = capture_catalogue(
        plan, package, BuildOptions(refs=(("sample", "feature"),)),
        default_ignore=chisel.DEFAULT_IGNORE,
    )
    candidate = json.loads((package / "snapshots/candidate-1/manifest.json").read_text())
    assert candidate["revision"] == feature
    assert "credentials.json" not in {row["path"] for row in candidate["files"] if row["included"]}

    (repo / "main.py").write_text("value = 3\n")
    git(repo, "commit", "-am", "Later development")
    later = git(repo, "rev-parse", "HEAD")
    assert later != feature
    build_reports(package, project="sample", task_roots=[])

    differences = [json.loads(line) for line in (package / "report/snapshot-differences.jsonl").read_text().splitlines()]
    feature_change = next(row for row in differences if row["snapshot"] == "candidate-1" and row["path"] == "main.py")
    assert feature_change["snapshot_id"] == inventory.snapshot_id
    assert feature_change["other_snapshot_id"] == candidate["snapshot_id"]
    assert (package / "snapshots/candidate-1/files/main.py").read_text() == "value = 2\n"
    (root / "portfolio.json").write_text(json.dumps({"project": [{
        "project": "sample", "snapshot_id": inventory.snapshot_id,
    }]}))
    from lynchpin.sources.chisel_attachments import build_attachments
    build_attachments(root, ["sample"], limit=500_000_000)
    extracted = tmp_path / "extracted"
    with tarfile.open(root / "portfolio-all.tar.gz") as archive:
        archive.extractall(extracted, filter="data")
    assert (extracted / "sample/reports/snapshot-differences.jsonl").is_file()
    assert all(sentinel.encode() not in path.read_bytes() for path in extracted.rglob("*") if path.is_file())
    assert all(b"value = 3" not in path.read_bytes() for path in extracted.rglob("*") if path.is_file())
    assert not list(extracted.rglob("*branch-delta.patch"))


def test_frozen_history_survives_live_ref_moves(tmp_path):
    from lynchpin.sources.chisel_history import build_history, freeze_refs
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "config", "user.name", "Fixture")
    (repo / "main.py").write_text("value = 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "Initial")
    original = git(repo, "rev-parse", "HEAD")
    with freeze_refs(repo, tmp_path) as frozen:
        (repo / "main.py").write_text("value = 2\n")
        git(repo, "commit", "-am", "Later")
        result = build_history(Path(frozen), tmp_path / "package", project="fixture", revision="HEAD", frozen=True)
        assert result["revision"] == original
        assert result["commit_count"] == 1
        git(Path(frozen), "bundle", "create", str(tmp_path / "history.bundle"), "--all")
        assert original in git(repo, "bundle", "list-heads", str(tmp_path / "history.bundle"))
