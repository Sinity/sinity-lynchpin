from __future__ import annotations

from pathlib import Path
import subprocess
from types import SimpleNamespace

from lynchpin.analysis.code_index import symbol_index
from lynchpin.core.projects import ProjectProfile


def test_symbol_index_coverage_reports_skips_and_indexes_above_old_limit(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "demo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "large.py").write_text("def present():\n    pass\n" + "# padding\n" * 120_000)
    (repo / "unreadable.py").write_text("def hidden():\n    pass\n")
    (repo / "too_big.py").write_bytes(b"#" * (symbol_index._MAX_FILE_BYTES + 1))
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    original_read_bytes = Path.read_bytes

    def read_bytes(path: Path) -> bytes:
        if path.name == "unreadable.py":
            raise PermissionError("synthetic denial")
        return original_read_bytes(path)

    def extract_symbols(*, source: bytes, project: str, path: str, **_kwargs):
        if b"def present():" in source:
            yield SimpleNamespace(project=project, language="python", path=path,
                                  symbol_kind="function", qualified_name="present",
                                  start_line=1, end_line=2, exported=True, parent=None)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(symbol_index, "_load_parsers", lambda _languages: {"python": object()})
    monkeypatch.setattr(symbol_index, "_extract_symbols", extract_symbols)
    profile = ProjectProfile(name="demo", path=repo, classify=lambda p: "src",
                             categories=("src",), colors={"src": "#000"})
    project = symbol_index.build_active_symbol_index(projects=("demo",), profiles={"demo": profile})["project"][0]
    assert [row["qualified_name"] for row in project["symbols"]] == ["present"]
    assert project["coverage_complete"] is False
    assert {(row["path"], row["reason"]) for row in project["omissions"]} == {
        ("too_big.py", "size_limit"), ("unreadable.py", "read_failed")}
