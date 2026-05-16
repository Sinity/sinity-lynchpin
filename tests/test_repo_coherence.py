from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACTIVE_TEXT_ROOTS = (
    ROOT / "lynchpin",
    ROOT / "tool",
)
ACTIVE_TEXT_FILES = (
    ROOT / "README.md",
    ROOT / "CLAUDE.md",
    ROOT / "pyproject.toml",
    ROOT / "justfile",
)
TEXT_SUFFIXES = {".md", ".py", ".toml", ".yml", ".yaml", ".sh"}
STALE_REFERENCES = (
    "lynchpin.scripts",
    "lynchpin/scripts",
    "lynchpin.composite",
    "lynchpin/composite",
    "from lynchpin.graph.evidence import",
    "lynchpin/graph/evidence.py",
    "compatibility facade",
    "compatibility shim",
    "retrospective/scaffold",
    "generate_scaffold",
)


def _active_text_files() -> list[Path]:
    files = [path for path in ACTIVE_TEXT_FILES if path.exists()]
    for root in ACTIVE_TEXT_ROOTS:
        if root.is_file():
            files.append(root)
            continue
        files.extend(
            path
            for path in root.rglob("*")
            if path.is_file() and (path.suffix in TEXT_SUFFIXES or path.parent == ROOT / "tool")
        )
    return sorted(set(files))


def test_active_repo_surfaces_do_not_reference_retired_scaffold_paths():
    offenders: list[str] = []
    for path in _active_text_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for needle in STALE_REFERENCES:
            if needle in text:
                offenders.append(f"{path.relative_to(ROOT)}: {needle}")

    assert offenders == []
