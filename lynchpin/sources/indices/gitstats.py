from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence

from ...core.cache import file_signature, persistent_cache
from ...core.config import get_config
from ...core.projects import ALL_PROJECTS
from .repos import GitRepository

log = logging.getLogger(__name__)


@dataclass
class GitCommit:
    date: date
    repo: str
    commit: str
    lines_added: int
    lines_deleted: int
    subject: str


@dataclass
class GitCommitActivity:
    repo: str
    timestamp: datetime


@persistent_cache(
    "git_commits",
    depends_on=lambda: file_signature(get_config().baseline_dir / "git_numstat.jsonl"),
)
def iter_commits() -> Iterator[GitCommit]:
    cfg = get_config()
    path = cfg.baseline_dir / "git_numstat.jsonl"
    if not path.exists():
        return iter(())
    import time
    age_days = (time.time() - path.stat().st_mtime) / 86400
    if age_days > 7:
        log.warning(
            "git_numstat.jsonl is %d days stale — run 'python -m lynchpin.system.baseline' to refresh",
            int(age_days),
        )
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
                    dt = _parse_date(record.get("date"))
                except Exception:
                    continue
                if dt is None:
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


def _parse_date(raw: object) -> Optional[date]:
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        if "T" in text:
            return datetime.fromisoformat(text).date()
        return date.fromisoformat(text)
    except ValueError:
        return None


def commits_by_date(target: date) -> Iterator[GitCommit]:
    iso = target.isoformat()
    yield from (
        commit for commit in iter_commits() if commit.date.isoformat() == iso
    )


def iter_commit_activity(
    repos: Sequence[Path],
    *,
    start_month: Optional[str] = None,
    end_month: Optional[str] = None,
) -> Iterator[GitCommitActivity]:
    since: Optional[str] = None
    until: Optional[str] = None
    if start_month:
        since = f"{start_month}-01"
    if end_month:
        until = f"{_month_after(end_month)}-01"

    for repo in repos:
        repo = repo.expanduser()
        if not (repo / ".git").is_dir():
            continue
        cmd = ["git", "-C", str(repo), "log", "--all", "--format=%cI"]
        if since:
            cmd.append(f"--since={since}")
        if until:
            cmd.append(f"--until={until}")
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert proc.stdout is not None
        for raw in proc.stdout:
            stamp = raw.strip()
            if not stamp:
                continue
            if stamp.endswith("Z"):
                stamp = stamp[:-1] + "+00:00"
            try:
                dt = datetime.fromisoformat(stamp)
            except ValueError:
                continue
            yield GitCommitActivity(repo=repo.name, timestamp=dt)

        _, stderr = proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"git log failed for {repo}: {stderr.strip()}")


def active_repo_paths(names: Optional[Sequence[str]] = None) -> List[Path]:
    return [repo.path for repo in iter_repos(names=names) if repo.exists and (repo.path / ".git").is_dir()]


def summarize_commit_activity(
    *,
    start_month: str,
    end_month: str,
    repos: Optional[Sequence[Path]] = None,
) -> tuple[Dict[str, int], Dict[str, Counter[str]]]:
    counts: Dict[str, int] = defaultdict(int)
    per_month_repos: Dict[str, Counter[str]] = defaultdict(Counter)
    repo_paths = list(repos) if repos is not None else active_repo_paths()
    for event in iter_commit_activity(repo_paths, start_month=start_month, end_month=end_month):
        month = f"{event.timestamp.year:04d}-{event.timestamp.month:02d}"
        if start_month <= month <= end_month:
            counts[month] += 1
            per_month_repos[month][event.repo] += 1
    return dict(counts), dict(per_month_repos)


def _month_after(month: str) -> str:
    year, month_i = (int(part) for part in month.split("-", 1))
    month_i += 1
    if month_i == 13:
        month_i = 1
        year += 1
    return f"{year:04d}-{month_i:02d}"


_GIT_SHORTSTAT_RE = re.compile(r"(\d+)\s+files?\s+changed")
_GIT_INSERT_RE = re.compile(r"(\d+)\s+insertions?\(\+\)")
_GIT_DELETE_RE = re.compile(r"(\d+)\s+deletions?\(-\)")
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


def _parse_git_shortstat(line: str) -> Dict[str, int]:
    files_changed = 0
    lines_added = 0
    lines_deleted = 0

    match_files = _GIT_SHORTSTAT_RE.search(line)
    if match_files:
        files_changed = int(match_files.group(1))
    match_insert = _GIT_INSERT_RE.search(line)
    if match_insert:
        lines_added = int(match_insert.group(1))
    match_delete = _GIT_DELETE_RE.search(line)
    if match_delete:
        lines_deleted = int(match_delete.group(1))

    return {
        "files_changed": files_changed,
        "lines_added": lines_added,
        "lines_deleted": lines_deleted,
    }


def _numstat_one_repo(
    repo_path: Path,
    since: Optional[datetime],
    until: Optional[datetime],
) -> List[Dict[str, object]]:
    """Run git log --shortstat for one repo and return all parsed records."""
    cmd = [
        "git", "-C", str(repo_path), "log",
        "--date=iso-strict",
        "--pretty=format:%H%x09%ad%x09%an%x09%s",
        "--shortstat",
    ]
    if until is not None:
        cmd.append(f"--until={until.isoformat()}")
    if since is not None:
        cmd.append(f"--since={since.isoformat()}")

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert proc.stdout is not None

    records: List[Dict[str, object]] = []
    current: Optional[Dict[str, object]] = None
    for raw in proc.stdout:
        line = raw.rstrip("\n")
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) >= 4 and _GIT_SHA_RE.match(parts[0]):
            if current:
                records.append(current)
            sha, date_str, author = parts[0], parts[1], parts[2]
            subject = "\t".join(parts[3:])
            current = {
                "repo": str(repo_path),
                "commit": sha,
                "date": date_str,
                "author": author,
                "subject": subject,
                "files_changed": 0,
                "lines_added": 0,
                "lines_deleted": 0,
            }
            continue
        if current and ("file changed" in line or "files changed" in line):
            current.update(_parse_git_shortstat(line))
    if current:
        records.append(current)

    _, stderr = proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"git log failed for {repo_path}: {stderr.strip()}")
    return records


def iter_numstat(
    repos: Sequence[Path],
    *,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
) -> Iterator[Dict[str, object]]:
    valid_repos = [p.expanduser() for p in repos if (p.expanduser() / ".git").exists()]
    if not valid_repos:
        return
    with ThreadPoolExecutor(max_workers=min(len(valid_repos), 8)) as pool:
        futures = {pool.submit(_numstat_one_repo, r, since, until): r for r in valid_repos}
        for fut in as_completed(futures):
            yield from fut.result()


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


PROJECT_SPECS: Dict[str, dict] = {
    name: {"path": p.path, "classify": p.classify}
    for name, p in ALL_PROJECTS.items()
    if p.active and p.classify
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
