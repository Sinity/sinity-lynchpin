#!/usr/bin/env python3
"""Generate combined Markdown bundles and git history slices for multiple repos."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
BUNDLE_ROOT = REPO_ROOT / "reports" / "project-bundles"
DEFAULT_TIMESTAMP = (
    dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
)

EXCLUDE_PATTERNS = [
    ".git/*",
    "target/*",
    "node_modules/*",
    "result/*",
    "dist/*",
    "build/*",
    ".venv/*",
    "venv/*",
    ".sqlx/*",
    "*.lock",
    "nixos/grafana-dashboards/*",
    "docs/test-suite-report/*",
    "docs/historical/*",
    "combined-bundles/*",
]

SKIP_PATHS = {
    "docs/misc-including-high-level-overviews-and-plans/_new_ideas_discussion.md",
    "docs/testing-gap-analysis.md",
    "docs/TEST_PATTERNS.md",
    "docs/TODO.md",
    "nixos/README.md",
    "docs/vision/emergent-insights-and-extensions.md",
    "docs/vision/project-target-state.md",
    "docs/misc-including-high-level-overviews-and-plans/EMERGENT_INSIGHTS_AND_SPECULATIVE_EXTENSIONS.md",
}

LANGUAGE_BY_EXT = {
    "rs": "rust",
    "ts": "typescript",
    "tsx": "tsx",
    "js": "javascript",
    "jsx": "jsx",
    "py": "python",
    "sh": "bash",
    "bash": "bash",
    "nix": "nix",
    "toml": "toml",
    "json": "json",
    "yml": "yaml",
    "yaml": "yaml",
    "md": "markdown",
    "mdx": "markdown",
    "txt": "text",
    "sql": "sql",
    "go": "go",
    "rb": "ruby",
    "java": "java",
    "c": "c",
    "cpp": "cpp",
    "hpp": "cpp",
    "cxx": "cpp",
    "kt": "kotlin",
    "swift": "swift",
}

DOC_EXTS = {"md", "mdx", "rst", "txt", "adoc", "org", "markdown", "rtf"}

PROJECT_SPECS = {
    "sinex": {"path": "/realm/project/sinex", "mode": "split"},
    "polylogue": {"path": "/realm/project/polylogue", "mode": "single"},
    "intercept-bounce": {"path": "/realm/project/intercept-bounce", "mode": "single"},
    "scribe-tap": {"path": "/realm/project/scribe-tap", "mode": "single"},
    "sinevec": {"path": "/realm/project/sinevec", "mode": "single"},
    "pwrank": {"path": "/realm/project/pwrank", "mode": "single"},
    "knowledge-extract": {"path": "/realm/project/knowledge-extract", "mode": "single"},
}

PART_SIZE_LIMITS = {
    "lt3mb": 3 * 1024 * 1024,
    "lt18mb": 18 * 1024 * 1024,
}

@dataclass
class ProjectConfig:
    name: str
    path: Path
    mode: str  # 'split' or 'single'

@dataclass
class FileEntry:
    absolute: Path
    relative: str
    size: int
    tokens: int
    language: str

@dataclass
class CommitRecord:
    index: int
    sha: str
    summary: str
    date: Optional[dt.datetime]
    text: str
    size_bytes: int

@dataclass
class Chunk:
    commits: List[CommitRecord]
    label: str
    part_index: int
    total_parts: int
    variant: str

    @property
    def start_commit(self) -> CommitRecord:
        return self.commits[0]

    @property
    def end_commit(self) -> CommitRecord:
        return self.commits[-1]

    @property
    def metadata(self) -> str:
        start = self.start_commit
        end = self.end_commit
        start_date = start.date.date().isoformat() if start.date else "unknown"
        end_date = end.date.date().isoformat() if end.date else "unknown"
        return (
            f"Part {self.part_index} of {self.total_parts} ({self.variant})\n"
            f"Commits: {start.index}-{end.index}\n"
            f"Range: {start_date} → {end_date}\n"
            f"First: {start.sha[:12]} — {start.summary}\n"
            f"Last:  {end.sha[:12]} — {end.summary}"
        )


def build_project_configs() -> Dict[str, ProjectConfig]:
    configs: Dict[str, ProjectConfig] = {}
    for name, spec in PROJECT_SPECS.items():
        configs[name] = ProjectConfig(
            name=name,
            path=Path(spec["path"]).resolve(),
            mode=spec["mode"],
        )
    return configs


def run_command(
    cmd: Sequence[str],
    cwd: Optional[Path] = None,
    check: bool = True,
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.setdefault("NO_COLOR", "1")
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        check=check,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


def list_files(root: Path) -> List[str]:
    if shutil.which("rg") is None:
        raise RuntimeError("rg is required for bundle generation")
    cmd = ["rg", "--files", "--hidden"]
    for pattern in EXCLUDE_PATTERNS:
        cmd.extend(["-g", f"!{pattern}"])
    result = run_command(cmd, cwd=root, check=False)
    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr.strip() or "rg failed")
    files = result.stdout.strip().splitlines()
    return [f for f in files if f]


def is_text_file(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            chunk = fh.read(4096)
    except (OSError, IOError):
        return False
    if b"\x00" in chunk:
        return False
    try:
        chunk.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def is_doc_file(rel_path: str) -> bool:
    rel_lower = rel_path.lower()
    if rel_lower.startswith(("docs/", "doc/")):
        return True
    if "/docs/" in rel_lower or "/doc/" in rel_lower:
        return True
    if rel_lower.startswith("schemas/") or "/schemas/" in rel_lower:
        return True
    ext = rel_path.rsplit(".", 1)[-1].lower() if "." in rel_path else ""
    return ext in DOC_EXTS


def is_test_file(rel_path: str) -> bool:
    rel_lower = rel_path.lower()
    if rel_lower.startswith(("tests/", "test/")):
        return True
    if "/tests/" in rel_lower or "/test/" in rel_lower:
        return True
    base = Path(rel_path).name
    if re.match(r"(test_|.*_test|.*_tests)", base):
        return True
    return False


def path_priority(rel_path: str) -> int:
    special = {
        "README.md": 10,
        "README": 10,
        "AGENTS.md": 10,
        "CLAUDE.md": 10,
        "TESTING.md": 10,
        "docs/README.md": 12,
        "docs/architecture": 12,
        "Cargo.toml": 15,
        "justfile": 15,
        "flake.nix": 15,
        "flake.lock": 15,
        "deny.toml": 15,
        "clippy.toml": 15,
        ".pre-commit-config.yaml": 15,
        ".editorconfig": 15,
        ".gitignore": 15,
        ".cargo/config.toml": 15,
        ".cargo-machete.toml": 15,
    }
    for prefix, weight in (
        ("docs/architecture/", 12),
        ("docs/", 25),
        ("scripts/", 20),
        ("cli/", 30),
        ("nixos/", 40),
        ("schemas/", 45),
        ("crate/lib/", 50),
        ("crate/core/", 60),
        ("crate/satellites/", 70),
        ("src/", 80),
        ("tests/", 85),
    ):
        if rel_path.startswith(prefix):
            return weight
    return special.get(rel_path, 100)


def classify(rel_path: str) -> str:
    if is_doc_file(rel_path):
        return "docs"
    if is_test_file(rel_path):
        return "tests"
    return "sources"


def gather_files(project: ProjectConfig) -> Dict[str, List[FileEntry]]:
    files_by_category: Dict[str, List[FileEntry]] = {"sources": [], "tests": [], "docs": []}
    files = list_files(project.path)
    for rel_path in files:
        rel_path = rel_path.strip()
        if not rel_path or rel_path in SKIP_PATHS:
            continue
        absolute = project.path / rel_path
        if not absolute.is_file():
            continue
        if not is_text_file(absolute):
            continue
        size = absolute.stat().st_size
        tokens = max(1, math.ceil(size / 4))
        ext = absolute.suffix.lower().lstrip(".")
        language = LANGUAGE_BY_EXT.get(ext, "")
        entry = FileEntry(
            absolute=absolute,
            relative=rel_path,
            size=size,
            tokens=tokens,
            language=language,
        )
        category = classify(rel_path)
        files_by_category[category].append(entry)
    for entries in files_by_category.values():
        entries.sort(key=lambda e: (path_priority(e.relative), e.relative))
    return files_by_category


def write_combined_files(
    project: ProjectConfig,
    files_by_category: Dict[str, List[FileEntry]],
    tokei_overall: Optional[str],
    per_category_tokei: Dict[str, str],
) -> None:
    target_dir = BUNDLE_ROOT / project.name
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = DEFAULT_TIMESTAMP

    if project.mode == "single":
        combined_entries = [
            entry
            for category in ("sources", "tests", "docs")
            for entry in files_by_category[category]
        ]
        write_single_bundle(
            target_dir / "combined.md",
            project,
            "combined",
            combined_entries,
            timestamp,
            tokei_overall,
        )
    else:
        for category in ("sources", "tests", "docs"):
            write_single_bundle(
                target_dir / f"combined-{category}.md",
                project,
                category,
                files_by_category[category],
                timestamp,
                per_category_tokei.get(category),
            )


def write_single_bundle(
    path: Path,
    project: ProjectConfig,
    category: str,
    entries: List[FileEntry],
    timestamp: str,
    tokei_snippet: Optional[str],
) -> None:
    if not entries:
        return
    with path.open("w", encoding="utf-8") as fh:
        fh.write("---\n")
        fh.write(f"generated: {timestamp}\n")
        fh.write(f"project: {project.name}\n")
        fh.write(f"category: {category}\n")
        fh.write(f"base_directory: {project.path}\n")
        fh.write(f"file_count: {len(entries)}\n")
        fh.write("---\n\n")
        if tokei_snippet:
            fh.write("## Code Statistics (tokei)\n\n")
            fh.write("```text\n")
            fh.write(tokei_snippet.rstrip())
            fh.write("\n```\n\n")
        fh.write("## Table of Contents\n\n")
        for idx, entry in enumerate(entries, start=1):
            anchor = f"file-{idx}"
            fh.write(f"{idx}. [{entry.relative}](#{anchor})\n")
        fh.write("\n")
        for idx, entry in enumerate(entries, start=1):
            anchor = f"file-{idx}"
            fh.write(f"<a id=\"{anchor}\"></a>\n")
            fh.write(f"## File: {entry.relative}\n\n")
            fh.write(f"- Size: {entry.size} bytes\n")
            fh.write(f"- Tokens (est): {entry.tokens}\n\n")
            lang = entry.language or ""
            fh.write(f"```{lang}\n")
            try:
                content = entry.absolute.read_text(encoding="utf-8", errors="replace")
            except Exception as exc:  # pragma: no cover - safety
                content = f"<unable to read file: {exc}>"
            fh.write(content.rstrip())
            fh.write("\n```\n\n")


def run_tokei(path: Path) -> Optional[str]:
    if shutil.which("tokei") is None:
        return None
    try:
        result = run_command(["tokei", "--sort", "lines", "."], cwd=path)
    except subprocess.CalledProcessError as exc:
        print(f"tokei failed for {path}: {exc.stderr}", file=sys.stderr)
        return None
    return result.stdout.strip()


def run_tokei_for_subset(project: ProjectConfig, entries: List[FileEntry]) -> Optional[str]:
    if not entries:
        return None
    if shutil.which("tokei") is None:
        return None
    with tempfile.TemporaryDirectory() as tmpdir:
        staging = Path(tmpdir)
        for entry in entries:
            dest = staging / entry.relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                if dest.exists():
                    dest.unlink()
                shutil.copy2(entry.absolute, dest, follow_symlinks=True)
            except OSError as exc:
                print(
                    f"Failed to stage {entry.relative} for tokei subset: {exc}",
                    file=sys.stderr,
                )
        try:
            result = run_command(["tokei", "--sort", "lines", "."], cwd=staging)
        except subprocess.CalledProcessError as exc:
            print(f"tokei subset failed for {project.name}: {exc.stderr}", file=sys.stderr)
            return None
        return result.stdout.strip()


def ensure_git_repo(path: Path) -> bool:
    try:
        run_command(["git", "rev-parse", "--is-inside-work-tree"], cwd=path)
        return True
    except subprocess.CalledProcessError:
        return False


def has_commits(path: Path) -> bool:
    try:
        result = run_command(["git", "rev-list", "--count", "HEAD"], cwd=path)
        return int(result.stdout.strip() or 0) > 0
    except subprocess.CalledProcessError:
        return False


def write_tokei_gitlog(
    project: ProjectConfig,
    tokei_overall: Optional[str],
    git_log_summary: Optional[str],
) -> None:
    target = BUNDLE_ROOT / project.name
    target.mkdir(parents=True, exist_ok=True)
    path = target / "tokei_gitlog.md"
    with path.open("w", encoding="utf-8") as fh:
        fh.write(f"# Tokei report — {project.name}\n\n")
        if tokei_overall:
            fh.write("```text\n")
            fh.write(tokei_overall.rstrip())
            fh.write("\n```\n\n")
        else:
            fh.write("_tokei not available_\n\n")
        fh.write("# git log --reverse --summary --stat\n\n")
        if git_log_summary:
            fh.write("```text\n")
            fh.write(git_log_summary.rstrip())
            fh.write("\n```\n")
        else:
            fh.write("_no git history_\n")


def capture_git_log(path: Path, with_diff: bool) -> Optional[str]:
    if not ensure_git_repo(path) or not has_commits(path):
        return None
    cmd = [
        "git",
        "log",
        "--reverse",
        "--summary",
        "--stat",
        "--date=iso8601-strict",
    ]
    if with_diff:
        cmd.append("-p")
    result = run_command(cmd, cwd=path)
    return result.stdout


def parse_commits(log_text: str) -> List[CommitRecord]:
    commits: List[CommitRecord] = []
    if not log_text.strip():
        return commits
    current_lines: List[str] = []
    count = 0
    for line in log_text.splitlines():
        if line.startswith("commit "):
            if current_lines:
                commits.append(build_commit_record(current_lines, count))
            current_lines = [line]
            count += 1
        else:
            current_lines.append(line)
    if current_lines:
        commits.append(build_commit_record(current_lines, count))
    return commits


def build_commit_record(lines: List[str], index: int) -> CommitRecord:
    sha = lines[0].split()[1] if lines and lines[0].startswith("commit ") else f"chunk-{index}"
    date_line = next((ln for ln in lines if ln.startswith("Date:")), None)
    parsed_date: Optional[dt.datetime] = None
    if date_line:
        raw = date_line.split("Date:", 1)[1].strip()
        try:
            parsed_date = dt.datetime.fromisoformat(raw)
        except ValueError:
            pass
    summary = ""
    saw_blank = False
    for ln in lines:
        if not ln.strip():
            saw_blank = True
            continue
        if saw_blank and ln.startswith("    "):
            summary = ln.strip()
            break
    text = "\n".join(lines).rstrip() + "\n"
    size_bytes = len(text.encode("utf-8"))
    return CommitRecord(
        index=index if index > 0 else 1,
        sha=sha,
        summary=summary,
        date=parsed_date,
        text=text,
        size_bytes=size_bytes,
    )


def chunk_by_size(commits: List[CommitRecord], limit: int) -> List[List[CommitRecord]]:
    if not commits:
        return []
    chunks: List[List[CommitRecord]] = []
    current: List[CommitRecord] = []
    current_size = 0
    for commit in commits:
        commit_size = commit.size_bytes
        if current and current_size + commit_size > limit:
            chunks.append(current)
            current = []
            current_size = 0
        current.append(commit)
        current_size += commit_size
    if current:
        chunks.append(current)
    return chunks


def chunk_by_count(commits: List[CommitRecord], size: int) -> List[List[CommitRecord]]:
    return [commits[i : i + size] for i in range(0, len(commits), size)]


def chunk_label(chunks: List[List[CommitRecord]], part_index: int, variant: str) -> str:
    total_parts = len(chunks)
    chunk = chunks[part_index - 1]
    start = chunk[0]
    end = chunk[-1]
    start_date = start.date.date().isoformat() if start.date else "unknown"
    end_date = end.date.date().isoformat() if end.date else "unknown"
    return (
        f"part{part_index:04d}-of{total_parts:04d}__"
        f"commit{start.index:04d}-{start.sha[:10]}__"
        f"{start_date}_to_{end_date}__{variant}"
    )


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_gitlog_outputs(
    project: ProjectConfig,
    git_log_diffs: Optional[str],
) -> None:
    target = BUNDLE_ROOT / project.name
    ensure_dir(target)
    if git_log_diffs:
        diff_path = target / "gitlog_diffs.md"
        with diff_path.open("w", encoding="utf-8") as fh:
            fh.write(f"# git log --reverse --summary --stat -p\n")
            fh.write(f"generated: {DEFAULT_TIMESTAMP}\n\n")
            fh.write("```text\n")
            fh.write(git_log_diffs.rstrip())
            fh.write("\n```\n")
    splits_dir = target / "gitlog_splits"
    ensure_dir(splits_dir)
    if not git_log_diffs:
        return
    commits = parse_commits(git_log_diffs)
    if not commits:
        return
    build_and_write_chunks(project, splits_dir, commits)


def build_and_write_chunks(project: ProjectConfig, splits_dir: Path, commits: List[CommitRecord]) -> None:
    # Size-based chunks
    for variant, limit in PART_SIZE_LIMITS.items():
        dir_path = splits_dir / f"max-{variant}"
        if dir_path.exists():
            shutil.rmtree(dir_path)
        dir_path.mkdir(parents=True, exist_ok=True)
        chunks = chunk_by_size(commits, limit)
        total_parts = len(chunks)
        for idx, chunk in enumerate(chunks, start=1):
            label = chunk_label(chunks, idx, variant)
            file_path = dir_path / f"{label}.md"
            write_chunk_file(project, file_path, chunk, idx, total_parts, variant)

    # 100-commit chunks with per-commit directories
    hundred_dir = splits_dir / "by-100-commits"
    if hundred_dir.exists():
        shutil.rmtree(hundred_dir)
    hundred_dir.mkdir(parents=True, exist_ok=True)
    hundred_chunks = chunk_by_count(commits, 100)
    total_hundred = len(hundred_chunks)
    for idx, chunk in enumerate(hundred_chunks, start=1):
        label = chunk_label(hundred_chunks, idx, "100-commits")
        chunk_path = hundred_dir / f"{label}.md"
        write_chunk_file(project, chunk_path, chunk, idx, total_hundred, "100-commits")
        per_commit_dir = hundred_dir / f"{label}__per-commit"
        per_commit_dir.mkdir(parents=True, exist_ok=True)
        for commit in chunk:
            commit_file = per_commit_dir / f"commit{commit.index:04d}-{commit.sha[:12]}.md"
            write_single_commit_file(project, commit_file, commit)

    # 10-commit chunks
    ten_dir = splits_dir / "by-10-commits"
    if ten_dir.exists():
        shutil.rmtree(ten_dir)
    ten_dir.mkdir(parents=True, exist_ok=True)
    ten_chunks = chunk_by_count(commits, 10)
    total_ten = len(ten_chunks)
    for idx, chunk in enumerate(ten_chunks, start=1):
        label = chunk_label(ten_chunks, idx, "10-commits")
        chunk_path = ten_dir / f"{label}.md"
        write_chunk_file(project, chunk_path, chunk, idx, total_ten, "10-commits")


def write_chunk_file(
    project: ProjectConfig,
    path: Path,
    chunk: List[CommitRecord],
    part_index: int,
    total_parts: int,
    variant: str,
) -> None:
    ensure_dir(path.parent)
    start = chunk[0]
    end = chunk[-1]
    start_date = start.date.date().isoformat() if start.date else "unknown"
    end_date = end.date.date().isoformat() if end.date else "unknown"
    with path.open("w", encoding="utf-8") as fh:
        fh.write(f"# git log slice — {project.name}\n")
        fh.write(f"variant: {variant}\n")
        fh.write(f"part: {part_index} / {total_parts}\n")
        fh.write(f"commits: {start.index}-{end.index}\n")
        fh.write(f"range: {start_date} → {end_date}\n")
        fh.write(f"first: {start.sha[:12]} — {start.summary}\n")
        fh.write(f"last:  {end.sha[:12]} — {end.summary}\n")
        fh.write("---\n\n")
        for commit in chunk:
            fh.write(commit.text)
            if not commit.text.endswith("\n"):
                fh.write("\n")


def write_single_commit_file(project: ProjectConfig, path: Path, commit: CommitRecord) -> None:
    ensure_dir(path.parent)
    date_str = commit.date.isoformat() if commit.date else "unknown"
    with path.open("w", encoding="utf-8") as fh:
        fh.write(f"# commit {commit.index} — {commit.sha}\n")
        fh.write(f"project: {project.name}\n")
        fh.write(f"date: {date_str}\n")
        fh.write(f"summary: {commit.summary}\n")
        fh.write("---\n\n")
        fh.write(commit.text)
        if not commit.text.endswith("\n"):
            fh.write("\n")


def process_project(project: ProjectConfig) -> None:
    print(f"→ Processing {project.name} ({project.path})")
    if not project.path.exists():
        print(f"  Skipping — path not found", file=sys.stderr)
        return
    project_output = BUNDLE_ROOT / project.name
    if project_output.exists():
        shutil.rmtree(project_output)
    project_output.mkdir(parents=True, exist_ok=True)
    files_by_category = gather_files(project)
    tokei_overall = run_tokei(project.path)
    per_category_tokei: Dict[str, str] = {}
    if project.mode == "split":
        for category in ("sources", "tests", "docs"):
            per_category_tokei[category] = run_tokei_for_subset(project, files_by_category[category]) or ""
    write_combined_files(project, files_by_category, tokei_overall, per_category_tokei)
    git_log_summary = capture_git_log(project.path, with_diff=False)
    write_tokei_gitlog(project, tokei_overall, git_log_summary)
    git_log_diffs = capture_git_log(project.path, with_diff=True)
    write_gitlog_outputs(project, git_log_diffs)
    print(f"  Done: outputs in {BUNDLE_ROOT / project.name}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate combined bundles for multiple repos")
    parser.add_argument(
        "--projects",
        nargs="*",
        help="Subset of project names to process",
        choices=sorted(PROJECT_SPECS.keys()),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configs = build_project_configs()
    selected = args.projects or list(configs.keys())
    BUNDLE_ROOT.mkdir(parents=True, exist_ok=True)
    for name in selected:
        process_project(configs[name])


if __name__ == "__main__":
    main()
