"""Canonical multi-home publication preserves native packages and old generations."""

import json
from pathlib import Path

import pytest

from lynchpin.sources import chisel_publication as publication
from lynchpin.sources.code_snapshots import code_snapshots_path, code_snapshot_export_path
from tests.analysis.test_chisel_publication import _project


def prepare(tmp_path, monkeypatch):
    monkeypatch.setenv("LYNCHPIN_PROJECTS_ROOT", str(tmp_path / "projects"))
    output = code_snapshots_path()
    output.mkdir(parents=True)
    (output / "index.md").write_text("old shared index")
    for name in ["alpha", "beta"]:
        old = tmp_path / ("previous-" + name)
        _project(old, name, b"old source", b"old archive")
        current = code_snapshots_path(name)
        current.parent.mkdir(parents=True)
        (old / name).rename(current)
        export = code_snapshot_export_path(name)
        export.parent.mkdir()
        (old / f"{name}-all.tar.gz").rename(export)
    return output


def test_packages_move_without_changing_hashes_and_old_generations_are_retained(tmp_path, monkeypatch):
    output = prepare(tmp_path, monkeypatch)
    candidate = tmp_path / "candidate"
    _project(candidate, "alpha", b"new source", b"new archive")
    before = (candidate / "alpha/source.xml").stat().st_ino
    (candidate / "index.md").write_text("[Alpha](alpha/START_HERE.md)")
    paths = publication.publish_project_homes(candidate, output, ["alpha"])
    current = Path(paths["alpha"])
    assert (current / "source.xml").stat().st_ino == before
    publication._validate_project(current.parent, "alpha", project_dir=current)
    assert code_snapshot_export_path("alpha").read_bytes() == b"new archive"
    retained = list((current.parent / "history").glob("*/current/source.xml"))
    assert len(retained) == 1 and retained[0].read_bytes() == b"old source"
    assert (code_snapshots_path("beta") / "source.xml").read_bytes() == b"old source"
    assert not (output / "alpha").exists()
    assert not (output / "alpha-all.tar.gz").exists()
    assert "../../alpha/snapshot/current/" in (output / "index.md").read_text()
    assert json.loads((output / "locations.json").read_text())["projects"]["alpha"] == str(current)


def test_later_failure_restores_prior_project_and_shared_index(tmp_path, monkeypatch):
    output = prepare(tmp_path, monkeypatch)
    candidate = tmp_path / "candidate"
    _project(candidate, "alpha", b"new alpha", b"new archive")
    _project(candidate, "beta", b"new beta", b"new archive")
    rename = publication.os.rename

    def fail_beta(source, destination):
        if Path(source) == candidate / "beta":
            raise OSError("synthetic failure after alpha publication")
        return rename(source, destination)

    monkeypatch.setattr(publication.os, "rename", fail_beta)
    with pytest.raises(OSError, match="synthetic failure"):
        publication.publish_project_homes(candidate, output, ["alpha", "beta"])
    for name in ["alpha", "beta"]:
        assert (code_snapshots_path(name) / "source.xml").read_bytes() == b"old source"
        assert code_snapshot_export_path(name).read_bytes() == b"old archive"
    assert (output / "index.md").read_text() == "old shared index"
    assert (candidate / "alpha/source.xml").read_bytes() == b"new alpha"
    journal = next(output.parent.glob(".snapshot-publication-*.jsonl"))
    assert json.loads(journal.read_text().splitlines()[-1])["state"] == "rolled-back"


def test_invalid_later_package_refuses_before_any_home_changes(tmp_path, monkeypatch):
    output = prepare(tmp_path, monkeypatch)
    candidate = tmp_path / "candidate"
    _project(candidate, "alpha", b"new alpha", b"new archive")
    _project(candidate, "beta", b"new beta", b"new archive")
    (candidate / "beta/source.xml").write_bytes(b"changed after manifest")
    with pytest.raises(publication.PublicationValidationError):
        publication.publish_project_homes(candidate, output, ["alpha", "beta"])
    assert (code_snapshots_path("alpha") / "source.xml").read_bytes() == b"old source"
    assert (output / "index.md").read_text() == "old shared index"


def test_interrupted_journal_refuses_new_publication(tmp_path, monkeypatch):
    output = prepare(tmp_path, monkeypatch)
    candidate = tmp_path / "candidate"
    _project(candidate, "alpha", b"new source", b"new archive")
    (output.parent / ".snapshot-publication-interrupted.jsonl").write_text(
        json.dumps({"state": "intent"}) + "\n")
    with pytest.raises(publication.PublicationBusyError, match="owner recovery"):
        publication.publish_project_homes(candidate, output, ["alpha"])
    assert (code_snapshots_path("alpha") / "source.xml").read_bytes() == b"old source"
