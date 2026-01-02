from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence

from .config import get_config
from .repos import GitRepository


@dataclass
class GitCommit:
    date: date
    repo: str
    commit: str
    lines_added: int
    lines_deleted: int
    subject: str


def iter_commits() -> Iterator[GitCommit]:
    cfg = get_config()
    path = cfg.baseline_dir / "git_numstat.jsonl"
    if not path.exists():
        return iter(())
    def generator() -> Iterator[GitCommit]:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                try:
                    dt = date.fromisoformat(record.get("date"))
                except Exception:
                    continue
                yield GitCommit(
                    date=dt,
                    repo=record.get("repo", ""),
                    commit=record.get("commit", ""),
                    lines_added=int(record.get("lines_added", 0)),
                    lines_deleted=int(record.get("lines_deleted", 0)),
                    subject=record.get("subject", ""),
                )
    return generator()


def commits_by_date(target: date) -> Iterator[GitCommit]:
    iso = target.isoformat()
    yield from (
        commit for commit in iter_commits() if commit.date.isoformat() == iso
    )


# === Repository coverage ===


@dataclass
class RepoInfo:
    name: str
    path: Path
    exists: bool
    branch: Optional[str]
    head: Optional[str]
    last_commit_at: Optional[datetime]


@dataclass
class RepoFile:
    repo: str
    relative: str
    absolute: Path
    category: Optional[str]


@dataclass
class RepoCommitSummary:
    repo: str
    sha: str
    author: str
    authored_at: Optional[datetime]
    subject: str


@dataclass
class TokeiLanguageStat:
    language: str
    code: int
    comments: int
    blanks: int


@dataclass
class TokeiReport:
    repo: str
    total_code: int
    total_lines: int
    languages: List[TokeiLanguageStat]


def iter_repos(names: Optional[Sequence[str]] = None) -> Iterator[RepoInfo]:
    specs = _project_specs()
    selected = {name for name in names} if names else None
    for name, spec in specs.items():
        if selected and name not in selected:
            continue
        path = Path(spec["path"])
        exists = path.exists()
        branch = None
        head = None
        last_commit_at = None
        if exists:
            repo = GitRepository(path)
            commits = repo.recent_commits(1)
            if commits:
                head = commits[0].sha
                branch = _git_output(path, ["rev-parse", "--abbrev-ref", "HEAD"])
                last_commit_at = commits[0].authored_at
        yield RepoInfo(
            name=name,
            path=path,
            exists=exists,
            branch=branch,
            head=head,
            last_commit_at=last_commit_at,
        )


def iter_repo_files(repo_name: str, tracked_only: bool = True) -> Iterator[RepoFile]:
    spec = _project_specs().get(repo_name)
    if not spec:
        return iter(())
    path = Path(spec["path"])
    classifier = spec["classify"]
    if not path.exists():
        return iter(())

    def generator() -> Iterator[RepoFile]:
        files: List[str]
        if tracked_only:
            output = _git_output(path, ["ls-files"])
            files = output.splitlines() if output else []
        else:
            files = [
                str(p.relative_to(path))
                for p in path.rglob("*")
                if p.is_file()
            ]
        for rel in files:
            category = classifier(rel)
            absolute = path / rel
            yield RepoFile(repo=repo_name, relative=rel, absolute=absolute, category=category)

    return generator()


def iter_recent_commits(repo_name: str, limit: int = 20) -> Iterator[RepoCommitSummary]:
    spec = _project_specs().get(repo_name)
    if not spec:
        return iter(())
    path = Path(spec["path"])
    if not path.exists():
        return iter(())

    format_str = "%H%x1f%an%x1f%aI%x1f%s"
    output = _git_output(path, ["--no-pager", "log", f"-n{limit}", f"--pretty={format_str}"])
    if not output:
        return iter(())

    def generator() -> Iterator[RepoCommitSummary]:
        for line in output.splitlines():
            sha, author, authored_at, subject = (line.split("\x1f", 3) + ["", "", "", ""])[:4]
            dt = None
            try:
                dt = datetime.fromisoformat(authored_at)
            except ValueError:
                pass
            yield RepoCommitSummary(
                repo=repo_name,
                sha=sha,
                author=author,
                authored_at=dt,
                subject=subject,
            )

    return generator()


def repo_tokei(repo_name: str) -> Optional[TokeiReport]:
    spec = _project_specs().get(repo_name)
    if not spec:
        return None
    path = Path(spec["path"])
    if not path.exists() or shutil.which("tokei") is None:
        return None
    try:
        result = subprocess.run(
            ["tokei", "-o", "json"],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    languages: List[TokeiLanguageStat] = []
    for key, value in payload.items():
        if key == "Totals":
            continue
        if not isinstance(value, dict):
            continue
        languages.append(
            TokeiLanguageStat(
                language=key,
                code=int(value.get("code", 0)),
                comments=int(value.get("comments", 0)),
                blanks=int(value.get("blanks", 0)),
            )
        )
    totals = payload.get("Totals", {})
    total_code = int(totals.get("code", 0))
    total_lines = int(totals.get("lines", 0))
    return TokeiReport(repo=repo_name, total_code=total_code, total_lines=total_lines, languages=languages)


# === Classification helpers ===


SKIP_EXTENSIONS = {"lock", "svg", "map", "min.js", "png", "jpg", "pdf", "gif", "ico", "woff", "woff2", "ttf", "eot"}
SKIP_PATHS = {"reports/", "pipelines/artefacts/", "artefacts/", "data/"}


def _skip_common(filename: str) -> bool:
    for ext in SKIP_EXTENSIONS:
        if filename.endswith(f".{ext}"):
            return True
    for path in SKIP_PATHS:
        if filename.startswith(path):
            return True
    return False


def _classify_sinex(filename: str) -> Optional[str]:
    if _skip_common(filename):
        return None
    if filename.startswith(".sqlx/"):
        return "generated"
    basename = Path(filename).name.lower()
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if "/tests/" in filename or "/test/" in filename:
        return "tests"
    if filename.startswith(("tests/", "test/")):
        return "tests"
    if basename.endswith("_test.rs") or basename.endswith("_tests.rs"):
        return "tests"
    if "/docs/" in filename or filename.startswith("docs/"):
        return "docs"
    if ext in {"md", "mdx", "rst", "txt"}:
        return "docs"
    if ext in {"nix", "toml", "yaml", "yml"}:
        return "config"
    if basename in {"justfile", ".gitignore", ".envrc"}:
        return "config"
    return "src"


def _classify_sinnix(filename: str) -> Optional[str]:
    if _skip_common(filename):
        return None
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if "/docs/" in filename or filename.startswith("docs/"):
        return "docs"
    if ext in {"md", "mdx", "rst", "txt"}:
        return "docs"
    if filename.startswith("host/") or "/host/" in filename:
        return "host"
    if filename.startswith("flake/") or filename in {"flake.nix", "flake.lock"}:
        return "flake"
    if filename.startswith("module/") or "/module/" in filename:
        return "module"
    return "other"


def _classify_sinity_analysis(filename: str) -> Optional[str]:
    if _skip_common(filename):
        return None
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    basename = Path(filename).name.lower()
    if "/docs/" in filename or filename.startswith("docs/"):
        return "docs"
    if ext in {"md", "mdx", "rst", "txt"}:
        return "docs"
    if filename.startswith("pipelines/"):
        return "pipelines"
    if ext in {"nix", "toml", "yaml", "yml", "json"}:
        return "config"
    if basename in {"justfile", ".gitignore", ".envrc"}:
        return "config"
    return "other"


def _classify_knowledgebase(filename: str) -> Optional[str]:
    if _skip_common(filename):
        return None
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    basename = Path(filename).name.lower()
    if ext in {"nix", "toml", "yaml", "yml", "json"}:
        return "config"
    if basename in {"justfile", ".gitignore", ".envrc"}:
        return "config"
    return "docs"


def _classify_rust_simple(filename: str) -> Optional[str]:
    if _skip_common(filename):
        return None
    basename = Path(filename).name.lower()
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if "/tests/" in filename or "/test/" in filename:
        return "tests"
    if filename.startswith(("tests/", "test/")):
        return "tests"
    if basename.endswith("_test.rs") or basename.endswith("_tests.rs"):
        return "tests"
    if "/docs/" in filename or filename.startswith("docs/"):
        return "docs"
    if ext in {"md", "mdx", "rst", "txt"}:
        return "docs"
    if ext in {"nix", "toml", "yaml", "yml"}:
        return "config"
    if basename in {"justfile", ".gitignore", ".envrc"}:
        return "config"
    return "src"


PROJECT_SPECS: Dict[str, dict] = {
    "sinity-lynchpin": {"path": "/realm/project/sinity-lynchpin", "classify": _classify_sinity_analysis},
    "sinex": {"path": "/realm/project/sinex", "classify": _classify_sinex},
    "polylogue": {"path": "/realm/project/polylogue", "classify": _classify_rust_simple},
    "intercept-bounce": {"path": "/realm/project/intercept-bounce", "classify": _classify_rust_simple},
    "scribe-tap": {"path": "/realm/project/scribe-tap", "classify": _classify_rust_simple},
    "sinevec": {"path": "/realm/project/sinevec", "classify": _classify_rust_simple},
    "pwrank": {"path": "/realm/project/pwrank", "classify": _classify_rust_simple},
    "knowledge-extract": {"path": "/realm/project/knowledge-extract", "classify": _classify_rust_simple},
    "sinnix": {"path": "/realm/project/sinnix", "classify": _classify_sinnix},
    "knowledgebase": {"path": "/realm/project/knowledgebase", "classify": _classify_knowledgebase},
}


def _project_specs() -> Dict[str, dict]:
    return PROJECT_SPECS


def _git_output(path: Path, args: List[str]) -> Optional[str]:
    if not path.exists():
        return None
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    output = result.stdout.strip()
    return output or None
