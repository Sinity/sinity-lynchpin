"""Git source: live git log + baseline JSONL → daily activity, commit facts, commit sessions, repo introspection.

Primary data source is live `git log` subprocess against active repo default
history refs. Callers can opt into all local refs for branch archaeology.
Baseline JSONL provides historical data before repos existed locally.

Graduated API:
  commits_in_range(start, end) → daily_activity(), commit_sessions()
  commit_facts(), file_change_facts(), patch_excerpt()
  repos(), repo_files(), recent_commits(), repo_tokei()
  iter_numstat() — threaded multi-repo shortstat
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from collections import Counter, defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, TypedDict

from ..core.cache import file_signature, persistent_cache
from ..core.config import get_config
from ..core.parse import in_date_range, parse_date_from_any
from ..core.primitives import logical_date
from ..core.source import read_jsonl_with
from ..core.projects import ALL_PROJECTS
from .github import GitHubItem, extract_commit_refs, fetch_issue, fetch_pr, repo_slug
from .git_models import (
    CommitSession,
    GitCommit,
    GitCommitActivity,
    GitCommitFact,
    GitDayActivity,
    GitFileChangeFact,
    GitPatchExcerpt,
    RepoCommitSummary,
    RepoFile,
    RepoInfo,
    TokeiLanguageStat,
    TokeiReport,
    _RepoCommitRecord,
)

log = logging.getLogger(__name__)


class _MutableRepoCommit(TypedDict):
    commit: str
    authored_at: str
    author: str
    subject: str
    path_changes: list[tuple[str, int, int]]


@dataclass(frozen=True)
class _ProjectSpec:
    path: Path
    classify: Callable[[str], str | None]


__all__ = [
    "GitCommit",
    "GitCommitActivity",
    "GitCommitFact",
    "GitFileChangeFact",
    "GitPatchExcerpt",
    "GitDayActivity",
    "CommitSession",
    "RepoInfo",
    "RepoFile",
    "RepoCommitSummary",
    "TokeiLanguageStat",
    "TokeiReport",
    "commits",
    "commits_in_range",
    "active_repo_paths",
    "commit_facts",
    "file_change_facts",
    "patch_excerpt",
    "daily_activity",
    "commit_sessions",
    "repos",
    "repo_files",
    "recent_commits",
    "repo_tokei",
    "github_context_for_commits",
    "iter_numstat",
    "iter_commit_activity",
    "summarize_commit_activity",
]

_PROJECT_ROOT = Path("/realm/project")
_KNOWN_PREFIXES = frozenset(
    {"feat", "fix", "refactor", "test", "docs", "chore", "perf", "ci", "build", "style"}
)
_GIT_SHORTSTAT_RE = re.compile(r"(\d+)\s+files?\s+changed")
_GIT_INSERT_RE = re.compile(r"(\d+)\s+insertions?\(\+\)")
_GIT_DELETE_RE = re.compile(r"(\d+)\s+deletions?\(-\)")
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


# ══════════════════════════════════════════════════════════════════════════════
# Data types
# ══════════════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════════════
# Raw access: commits from baseline JSONL
# ══════════════════════════════════════════════════════════════════════════════


def commits() -> Iterator[GitCommit]:
    """Yield baseline commits, surfacing baseline coverage gaps on every call.

    Git is a capture (live ``git log`` + baseline JSONL), so a stale baseline is
    a real coverage gap, not a cosmetic staleness alarm. The coverage check runs
    in this thin, uncached wrapper so it fires on every call — the underlying
    hydration is memoized by ``_commits_cached`` and would otherwise suppress the
    signal exactly when a long-lived process keeps serving cached data.
    """
    path = get_config().baseline_dir / "git_numstat.jsonl"
    if path.exists():
        import time as _time

        age_days = (_time.time() - path.stat().st_mtime) / 86400
        if age_days > 7:
            log.info(
                "git baseline coverage gap: git_numstat.jsonl last refreshed %d days "
                "ago — pre-repo history may be incomplete; run baseline to refresh",
                int(age_days),
            )
    yield from _commits_cached()


@persistent_cache(
    "git_commits",
    depends_on=lambda: file_signature(get_config().baseline_dir / "git_numstat.jsonl"),
)
def _commits_cached() -> Iterator[GitCommit]:
    cfg = get_config()
    path = cfg.baseline_dir / "git_numstat.jsonl"
    if not path.exists():
        return iter(())

    def _hydrate(rec: dict[str, Any]) -> GitCommit | None:
        dt = _parse_date(rec.get("date"))
        if dt is None:
            return None
        return GitCommit(
            date=dt,
            repo=rec.get("repo", ""),
            commit=rec.get("commit", ""),
            lines_added=int(rec.get("lines_added", 0)),
            lines_deleted=int(rec.get("lines_deleted", 0)),
            subject=rec.get("subject", ""),
        )

    return read_jsonl_with(path, _hydrate, source_name="git_numstat")


def commits_in_range(start: date, end: date) -> Iterator[GitCommit]:
    """Yield commits in date range from live git log (primary) + baseline JSONL (historical).

    Live git log covers all active repos. Baseline JSONL provides history for
    dates before repos existed locally. Deduplicates by commit hash.
    """
    seen: set[str] = set()
    # Primary: live git log from active repos
    for repo_path in active_repo_paths():
        for rec in _iter_repo_commit_records(repo_path, start=start, end=end):
            if rec.commit in seen:
                continue
            seen.add(rec.commit)
            yield GitCommit(
                date=logical_date(rec.authored_at),
                repo=rec.repo,
                commit=rec.commit,
                lines_added=sum(a for _, a, _ in rec.path_changes),
                lines_deleted=sum(d for _, _, d in rec.path_changes),
                subject=rec.subject,
            )
    # Fallback: baseline JSONL for historical data not covered by live repos
    for c in commits():
        if start <= c.date <= end and c.commit not in seen:
            seen.add(c.commit)
            yield c


def active_repo_paths(names: Optional[Sequence[str]] = None) -> List[Path]:
    return [
        r.path for r in repos(names=names) if r.exists and (r.path / ".git").is_dir()
    ]


# ══════════════════════════════════════════════════════════════════════════════
# Commit facts (per-commit + per-file detail from live git log --numstat)
# ══════════════════════════════════════════════════════════════════════════════


def commit_facts(
    *,
    start: date,
    end: date,
    repo_paths: Sequence[Path] | None = None,
    all_refs: bool = False,
    include_paths: bool = True,
) -> Iterator[GitCommitFact]:
    paths = list(repo_paths) if repo_paths else active_repo_paths()
    for repo_path in sorted(paths, key=lambda p: p.name):
        for record in _iter_repo_commit_records(
            repo_path,
            start=start,
            end=end,
            all_refs=all_refs,
            include_paths=include_paths,
        ):
            yield _commit_fact_from_record(record)


def file_change_facts(
    *,
    start: date,
    end: date,
    repo_paths: Sequence[Path] | None = None,
    all_refs: bool = False,
) -> Iterator[GitFileChangeFact]:
    paths = list(repo_paths) if repo_paths else active_repo_paths()
    for repo_path in sorted(paths, key=lambda p: p.name):
        for record in _iter_repo_commit_records(
            repo_path, start=start, end=end, all_refs=all_refs
        ):
            for path, added, deleted in record.path_changes:
                yield GitFileChangeFact(
                    repo=record.repo,
                    commit=record.commit,
                    authored_at=record.authored_at,
                    path=path,
                    path_root=_path_root(path),
                    lines_added=added,
                    lines_deleted=deleted,
                    lines_changed=added + deleted,
                )


def patch_excerpt(
    *, repo_path: Path, commit: str, max_lines: int = 120
) -> GitPatchExcerpt:
    cmd = [
        "git",
        "-C",
        str(repo_path),
        "show",
        "--no-color",
        "--format=",
        "--unified=3",
        commit,
    ]
    try:
        raw = subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
    except Exception:
        return GitPatchExcerpt(line_count=0, truncated=False, patch_excerpt="")
    output = raw.decode("utf-8", errors="replace")
    lines = output.splitlines()
    truncated = len(lines) > max_lines
    return GitPatchExcerpt(
        line_count=len(lines),
        truncated=truncated,
        patch_excerpt="\n".join(lines[:max_lines]),
    )


# ══════════════════════════════════════════════════════════════════════════════
# Daily activity aggregation
# ══════════════════════════════════════════════════════════════════════════════


def daily_activity(*, start: date, end: date) -> list[GitDayActivity]:
    """Daily git activity from live git log. Uses commit_facts for rich per-commit data."""
    facts = list(commit_facts(start=start, end=end))
    if not facts:
        return []

    # Co-author detection from commit message trailers
    repos_set = {f.repo for f in facts}
    coauthor_cache = {
        repo: _fetch_coauthor_info(repo, start, end) for repo in repos_set
    }

    grouped: dict[tuple[date, str], list[GitCommitFact]] = defaultdict(list)
    for f in facts:
        grouped[(logical_date(f.authored_at), f.repo)].append(f)

    result: list[GitDayActivity] = []
    for (d, repo), day_facts in sorted(grouped.items()):
        added = sum(f.lines_added for f in day_facts)
        deleted = sum(f.lines_deleted for f in day_facts)
        coauthors = coauthor_cache.get(repo, {})
        ai_count = 0
        all_authors: set[str] = set()
        prefixes: list[str] = []
        timestamps: list[datetime] = []
        for f in day_facts:
            if f.commit in coauthors:
                ai_count += 1
                all_authors.update(coauthors[f.commit])
            prefixes.append(_parse_prefix(f.subject))
            timestamps.append(f.authored_at)
        prefix_counts = Counter(prefixes)
        total = len(day_facts)
        result.append(
            GitDayActivity(
                date=d,
                repo=repo,
                commit_count=total,
                lines_added=added,
                lines_deleted=deleted,
                churn=added + deleted,
                net_loc=added - deleted,
                ai_coauthored=ai_count,
                ai_ratio=ai_count / total if total else 0,
                human_only=total - ai_count,
                dominant_prefix=prefix_counts.most_common(1)[0][0]
                if prefix_counts
                else "other",
                commit_burst_count=_count_bursts(timestamps),
                authors=tuple(sorted(all_authors)),
            )
        )
    return result


# ══════════════════════════════════════════════════════════════════════════════
# Commit sessions (temporal grouping across repos)
# ══════════════════════════════════════════════════════════════════════════════


def commit_sessions(
    *, start: date, end: date, gap_minutes: float = 30
) -> list[CommitSession]:
    """Group commits into temporal sessions with max gap. Uses live git log."""
    facts = list(commit_facts(start=start, end=end))
    if not facts:
        return []

    repos_set = {f.repo for f in facts}
    coauthor_cache = {
        repo: _fetch_coauthor_info(repo, start, end) for repo in repos_set
    }

    # Sort by authored_at
    timed = sorted(facts, key=lambda f: f.authored_at)
    gap = timedelta(minutes=gap_minutes)
    sessions: list[CommitSession] = []
    current: list[GitCommitFact] = [timed[0]]
    for f in timed[1:]:
        if f.authored_at - current[-1].authored_at <= gap:
            current.append(f)
        else:
            sessions.append(_build_commit_session(current, coauthor_cache))
            current = [f]
    if current:
        sessions.append(_build_commit_session(current, coauthor_cache))
    return sessions


def _build_commit_session(
    facts: list[GitCommitFact], coauthor_cache: dict[str, dict[str, list[str]]]
) -> CommitSession:
    total = len(facts)
    ai_count = sum(1 for f in facts if f.commit in coauthor_cache.get(f.repo, {}))
    lines = sum(f.lines_changed for f in facts)
    repo_counts: Counter[str] = Counter(f.repo for f in facts)
    duration = facts[-1].authored_at - facts[0].authored_at
    return CommitSession(
        repo=repo_counts.most_common(1)[0][0],
        start=facts[0].authored_at,
        end=facts[-1].authored_at,
        commit_count=total,
        duration_min=round(duration.total_seconds() / 60, 1),
        is_burst=total >= 3 and duration < timedelta(minutes=5),
        ai_fraction=ai_count / total if total else 0,
        lines_changed=lines,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Repository introspection
# ══════════════════════════════════════════════════════════════════════════════

PROJECT_SPECS: Dict[str, _ProjectSpec] = {
    name: _ProjectSpec(path=Path(p.path).expanduser(), classify=p.classify)
    for name, p in ALL_PROJECTS.items()
    if p.active and p.classify
}


def repos(names: Optional[Sequence[str]] = None) -> list[RepoInfo]:
    selected = set(names) if names else None
    result: list[RepoInfo] = []
    for name, spec in PROJECT_SPECS.items():
        if selected and name not in selected:
            continue
        path = spec.path
        exists = path.exists()
        branch = head = None
        last_commit_at = None
        if exists and (path / ".git").is_dir():
            branch = _git_output(path, ["rev-parse", "--abbrev-ref", "HEAD"])
            head_output = _git_output(path, ["rev-parse", "HEAD"])
            head = head_output[:12] if head_output else None
            iso = _git_output(path, ["log", "-1", "--format=%aI"])
            if iso:
                try:
                    last_commit_at = datetime.fromisoformat(iso.replace("Z", "+00:00"))
                except ValueError:
                    pass
        result.append(
            RepoInfo(
                name=name,
                path=path,
                exists=exists,
                branch=branch,
                head=head,
                last_commit_at=last_commit_at,
            )
        )
    return result


def repo_files(repo_name: str, tracked_only: bool = True) -> Iterator[RepoFile]:
    spec = PROJECT_SPECS.get(repo_name)
    if not spec or not spec.path.exists():
        return
    path, classifier = spec.path, spec.classify
    if tracked_only:
        output = _git_output(path, ["ls-files"])
        files = output.splitlines() if output else []
    else:
        files = [str(p.relative_to(path)) for p in path.rglob("*") if p.is_file()]
    for rel in files:
        yield RepoFile(
            repo=repo_name, relative=rel, absolute=path / rel, category=classifier(rel)
        )


def recent_commits(repo_name: str, limit: int = 20) -> list[RepoCommitSummary]:
    spec = PROJECT_SPECS.get(repo_name)
    if not spec or not spec.path.exists():
        return []
    path = spec.path
    output = _git_output(
        path, ["--no-pager", "log", f"-n{limit}", "--pretty=%H%x1f%an%x1f%aI%x1f%s"]
    )
    if not output:
        return []
    result: list[RepoCommitSummary] = []
    for line in output.splitlines():
        parts = (line.split("\x1f", 3) + ["", "", "", ""])[:4]
        dt = None
        try:
            dt = datetime.fromisoformat(parts[2])
        except ValueError:
            pass
        result.append(
            RepoCommitSummary(
                repo=repo_name,
                sha=parts[0],
                author=parts[1],
                authored_at=dt,
                subject=parts[3],
            )
        )
    return result


def github_context_for_commits(
    facts: Sequence[GitCommitFact], *, max_refs: int = 24
) -> dict[str, object]:
    """Fetch GitHub PR/issue context referenced by commit subjects when available.

    This is intentionally best-effort: local git remains the primary source and
    GitHub enriches only referenced commits. Missing `gh`, missing auth, private
    repos, or network failures produce explicit unavailable metadata rather than
    failing scaffold generation.
    """
    if shutil.which("gh") is None:
        return {"status": "unavailable", "reason": "gh_not_found", "items": []}

    refs_by_repo: dict[str, dict[str, set[int]]] = defaultdict(
        lambda: {"prs": set(), "issues": set()}
    )
    for fact in facts:
        refs = extract_commit_refs(fact.subject)
        refs_by_repo[fact.repo]["prs"].update(refs["prs"])
        refs_by_repo[fact.repo]["issues"].update(refs["issues"])

    items: list[dict[str, object]] = []
    attempted = 0
    for repo, refs in sorted(refs_by_repo.items()):
        repo_path = _repo_path(repo)
        slug = repo_slug(repo_path)
        if slug is None:
            continue
        for number in sorted(refs["prs"]):
            if attempted >= max_refs:
                break
            attempted += 1
            item = fetch_pr(repo_path, number)
            items.append(_github_item(repo, "pr", number, slug, item))
        for number in sorted(refs["issues"] - refs["prs"]):
            if attempted >= max_refs:
                break
            attempted += 1
            item = fetch_issue(repo_path, number)
            items.append(_github_item(repo, "issue", number, slug, item))

    status = "ok" if items else "no_refs"
    if attempted >= max_refs:
        status = "truncated"
    return {"status": status, "max_refs": max_refs, "items": items}


def repo_tokei(repo_name: str) -> Optional[TokeiReport]:
    spec = PROJECT_SPECS.get(repo_name)
    if not spec or not spec.path.exists() or shutil.which("tokei") is None:
        return None
    try:
        result = subprocess.run(
            ["tokei", "-o", "json"],
            cwd=spec.path,
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
    languages = [
        TokeiLanguageStat(
            language=k,
            code=int(v.get("code", 0)),
            comments=int(v.get("comments", 0)),
            blanks=int(v.get("blanks", 0)),
        )
        for k, v in payload.items()
        if k != "Totals" and isinstance(v, dict)
    ]
    totals = payload.get("Totals", {})
    return TokeiReport(
        repo=repo_name,
        total_code=int(totals.get("code", 0)),
        total_lines=int(totals.get("lines", 0)),
        languages=languages,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Numstat: threaded multi-repo shortstat
# ══════════════════════════════════════════════════════════════════════════════


def iter_numstat(
    repos_seq: Sequence[Path],
    *,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
) -> Iterator[Dict[str, object]]:
    valid = [p.expanduser() for p in repos_seq if (p.expanduser() / ".git").exists()]
    if not valid:
        return
    with ThreadPoolExecutor(max_workers=min(len(valid), 8)) as pool:
        futures = {pool.submit(_numstat_one_repo, r, since, until): r for r in valid}
        for fut in as_completed(futures):
            yield from fut.result()


def iter_commit_activity(
    repos_seq: Sequence[Path],
    *,
    start_month: Optional[str] = None,
    end_month: Optional[str] = None,
) -> Iterator[GitCommitActivity]:
    since = f"{start_month}-01" if start_month else None
    until_str = f"{_month_after(end_month)}-01" if end_month else None
    for repo in repos_seq:
        repo = repo.expanduser()
        if not (repo / ".git").is_dir():
            continue
        cmd = ["git", "-C", str(repo), "log", "--all", "--format=%cI"]
        if since:
            cmd.append(f"--since={since}")
        if until_str:
            cmd.append(f"--until={until_str}")
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
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
        proc.communicate()


def summarize_commit_activity(
    *, start_month: str, end_month: str, repos_seq: Optional[Sequence[Path]] = None
) -> tuple[Dict[str, int], Dict[str, Counter[str]]]:
    counts: Dict[str, int] = defaultdict(int)
    per_month_repos: Dict[str, Counter[str]] = defaultdict(Counter)
    paths = list(repos_seq) if repos_seq else [r.path for r in repos() if r.exists]
    for event in iter_commit_activity(
        paths, start_month=start_month, end_month=end_month
    ):
        m = f"{event.timestamp.year:04d}-{event.timestamp.month:02d}"
        if start_month <= m <= end_month:
            counts[m] += 1
            per_month_repos[m][event.repo] += 1
    return dict(counts), dict(per_month_repos)


# ══════════════════════════════════════════════════════════════════════════════
# Internal helpers
# ══════════════════════════════════════════════════════════════════════════════


def _iter_repo_commit_records(
    repo_path: Path,
    *,
    start: date,
    end: date,
    all_refs: bool = False,
    include_paths: bool = True,
) -> Iterator[_RepoCommitRecord]:
    if not (repo_path / ".git").is_dir():
        return
    cmd = [
        "git",
        "-C",
        str(repo_path),
        "log",
        "--date=iso-strict",
        "--pretty=format:COMMIT|%H|%aI|%aN|%s",
        f"--after={(start - timedelta(days=1)).isoformat()}",
        f"--before={(end + timedelta(days=1)).isoformat()}",
    ]
    if include_paths:
        cmd.append("--numstat")
    if all_refs:
        cmd.append("--all")
    else:
        ref = _default_history_ref(repo_path)
        if ref is None:
            return
        cmd.append(ref)
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
    )
    assert proc.stdout is not None
    current: _MutableRepoCommit | None = None
    for raw in proc.stdout:
        line = raw.rstrip("\n")
        if not line:
            continue
        if line.startswith("COMMIT|"):
            if current is not None:
                rec = _finalize_record(repo_path.name, current)
                if rec and in_date_range(logical_date(rec.authored_at), start, end):
                    yield rec
            parts = line.split("|", 4)
            current = {
                "commit": parts[1],
                "authored_at": parts[2],
                "author": parts[3],
                "subject": parts[4] if len(parts) > 4 else "",
                "path_changes": [],
            }
            continue
        if current is None or "\t" not in line:
            continue
        cols = (line.split("\t", 2) + ["", "", ""])[:3]
        path = cols[2].strip()
        if path:
            current["path_changes"].append(
                (
                    path,
                    int(cols[0]) if cols[0].isdigit() else 0,
                    int(cols[1]) if cols[1].isdigit() else 0,
                )
            )
    if current:
        rec = _finalize_record(repo_path.name, current)
        if rec and in_date_range(logical_date(rec.authored_at), start, end):
            yield rec
    proc.communicate()


def _default_history_ref(repo_path: Path) -> str | None:
    remote_head = _git_output(
        repo_path, ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"]
    )
    if remote_head:
        return remote_head
    for candidate in ("master", "main"):
        if _git_output(repo_path, ["rev-parse", "--verify", candidate]):
            return candidate
    return _git_output(repo_path, ["branch", "--show-current"]) or "HEAD"


def _finalize_record(
    repo: str, current: _MutableRepoCommit
) -> _RepoCommitRecord | None:
    try:
        authored_at = datetime.fromisoformat(
            str(current["authored_at"]).replace("Z", "+00:00")
        )
    except ValueError:
        return None
    return _RepoCommitRecord(
        repo=repo,
        commit=str(current["commit"]),
        authored_at=authored_at,
        author=str(current.get("author", "")),
        subject=str(current.get("subject", "")),
        path_changes=tuple(sorted(current.get("path_changes", ()), key=lambda x: x[0])),
    )


def _commit_fact_from_record(record: _RepoCommitRecord) -> GitCommitFact:
    paths = tuple(sorted({p for p, _, _ in record.path_changes}))
    path_roots = tuple(sorted({_path_root(p) for p in paths} - {""}))
    added = sum(a for _, a, _ in record.path_changes)
    deleted = sum(d for _, _, d in record.path_changes)
    return GitCommitFact(
        repo=record.repo,
        commit=record.commit,
        authored_at=record.authored_at,
        author=record.author,
        subject=record.subject,
        lines_added=added,
        lines_deleted=deleted,
        lines_changed=added + deleted,
        files_changed=len(paths),
        paths=paths,
        path_roots=path_roots,
    )


def _numstat_one_repo(
    repo_path: Path, since: Optional[datetime], until: Optional[datetime]
) -> List[Dict[str, object]]:
    cmd = [
        "git",
        "-C",
        str(repo_path),
        "log",
        "--date=iso-strict",
        "--pretty=format:%H%x09%ad%x09%an%x09%s",
        "--shortstat",
    ]
    if until:
        cmd.append(f"--until={until.isoformat()}")
    if since:
        cmd.append(f"--since={since.isoformat()}")
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
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
            current = {
                "repo": str(repo_path),
                "commit": parts[0],
                "date": parts[1],
                "author": parts[2],
                "subject": "\t".join(parts[3:]),
                "files_changed": 0,
                "lines_added": 0,
                "lines_deleted": 0,
            }
        elif current and ("file changed" in line or "files changed" in line):
            current.update(_parse_git_shortstat(line))
    if current:
        records.append(current)
    proc.communicate()
    return records


def _parse_git_shortstat(line: str) -> Dict[str, int]:
    files = int(m.group(1)) if (m := _GIT_SHORTSTAT_RE.search(line)) else 0
    added = int(m.group(1)) if (m := _GIT_INSERT_RE.search(line)) else 0
    deleted = int(m.group(1)) if (m := _GIT_DELETE_RE.search(line)) else 0
    return {"files_changed": files, "lines_added": added, "lines_deleted": deleted}


def _fetch_coauthor_info(repo: str, after: date, before: date) -> dict[str, list[str]]:
    repo_path = _repo_path(repo)
    if not (repo_path / ".git").is_dir():
        return {}
    cmd = [
        "git",
        "-C",
        str(repo_path),
        "log",
        "--format=%H%n%b%n---END---",
        f"--after={(after - timedelta(days=1)).isoformat()}",
        f"--before={(before + timedelta(days=1)).isoformat()}",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return {}
    if result.returncode != 0:
        return {}
    coauthors: dict[str, list[str]] = {}
    current_sha: str | None = None
    body: list[str] = []
    for line in result.stdout.splitlines():
        if line == "---END---":
            if current_sha:
                names = [
                    name
                    for name in (
                        _extract_coauthor(body_line)
                        for body_line in body
                        if "co-authored-by" in body_line.lower()
                    )
                    if name
                ]
                if names:
                    coauthors[current_sha] = names
            current_sha = None
            body = []
        elif current_sha is None:
            sha = line.strip()
            if len(sha) == 40:
                current_sha = sha
        else:
            body.append(line)
    return coauthors


def _fetch_commit_timestamps(repo: str, hashes: set[str]) -> dict[str, datetime]:
    if not hashes:
        return {}
    repo_path = _repo_path(repo)
    if not (repo_path / ".git").is_dir():
        return {}
    cmd = ["git", "-C", str(repo_path), "log", "--format=%H %aI", "--all"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return {}
    if result.returncode != 0:
        return {}
    timestamps: dict[str, datetime] = {}
    for line in result.stdout.splitlines():
        parts = line.strip().split(" ", 1)
        if len(parts) == 2 and parts[0] in hashes:
            try:
                timestamps[parts[0]] = datetime.fromisoformat(
                    parts[1].replace("Z", "+00:00")
                )
            except ValueError:
                pass
    return timestamps


def _extract_coauthor(line: str) -> str | None:
    match = re.search(r"Co-Authored-By:\s*(.+?)(?:\s*<|$)", line, re.IGNORECASE)
    return match.group(1).strip() if match else None


def _repo_path(repo: str) -> Path:
    p = Path(repo)
    return p if p.is_absolute() else _PROJECT_ROOT / repo


def _parse_prefix(subject: str) -> str:
    for sep in (":", "("):
        idx = subject.find(sep)
        if idx > 0:
            c = subject[:idx].strip().lower()
            if c in _KNOWN_PREFIXES:
                return c
    return "other"


def _github_item(
    repo: str, kind: str, number: int, slug: str, item: GitHubItem | None
) -> dict[str, object]:
    if item is None:
        return {
            "repo": repo,
            "slug": slug,
            "kind": kind,
            "number": number,
            "status": "unavailable",
        }
    return {
        "repo": repo,
        "slug": slug,
        "kind": kind,
        "number": number,
        "status": "ok",
        "title": item.title,
        "state": item.state,
        "author": item.author.login,
        "url": item.url,
        "merged_at": item.merged_at.isoformat() if item.merged_at else None,
        "closed_at": item.closed_at.isoformat() if item.closed_at else None,
        "body": item.body,
        "comment_count": len(item.comments),
        "review_count": None,
        "labels": [label.name for label in item.labels],
        "comments": [
            {
                "author": {"login": comment.author.login},
                "body": comment.body,
                "createdAt": comment.created_at.isoformat()
                if comment.created_at
                else None,
                "url": comment.url,
            }
            for comment in item.comments
        ],
        "reviews": None,
    }


def _count_bursts(timestamps: list[datetime]) -> int:
    if len(timestamps) < 3:
        return 0
    timestamps = sorted(timestamps)
    bursts = i = 0
    while i < len(timestamps):
        j = i + 1
        while j < len(timestamps) and (timestamps[j] - timestamps[i]) <= timedelta(
            minutes=5
        ):
            j += 1
        if j - i >= 3:
            bursts += 1
            i = j
        else:
            i += 1
    return bursts


def _path_root(path: str) -> str:
    parts = [p for p in path.strip().replace("\\", "/").split("/") if p]
    if not parts:
        return "unknown"
    if parts[0] == "crate" and len(parts) >= 3:
        return parts[2]
    if parts[0] in {"src", "tests"} and len(parts) >= 2:
        return parts[1]
    if parts[0] == "Source" and len(parts) >= 2:
        return parts[1]
    return parts[0]


_parse_date = parse_date_from_any  # from core.parse


def _month_after(month: str) -> str:
    year, m = (int(p) for p in month.split("-", 1))
    m += 1
    if m == 13:
        m = 1
        year += 1
    return f"{year:04d}-{m:02d}"


def _git_output(path: Path, args: List[str]) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", *args], cwd=path, check=True, capture_output=True, text=True
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return result.stdout.strip() or None
