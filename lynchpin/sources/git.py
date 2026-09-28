"""Git source: live git log + baseline JSONL → daily activity, commit facts, commit sessions, repo introspection.

Primary data source is live `git log` subprocess against active repo default
history refs. Callers can opt into all local refs for branch archaeology.
Baseline JSONL provides historical data before repos existed locally.

Graduated API:
  commits_in_range(*, start, end) → daily_activity(), commit_sessions()
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
import tempfile
from collections import Counter, defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, TypedDict

from ..core.cache import file_signature, persistent_cache
from ..core.config import get_config
from ..core.errors import SourceUnavailableError
from ..core.coverage import CoverageBounds
from ..core.parse import in_date_range, parse_date_from_any
from ..core.primitives import logical_date
from ..core.source import read_jsonl_with
from ..core.projects import ALL_PROJECTS
from .github import (
    GitHubItem,
    extract_commit_refs,
)
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
    path_changes: list[tuple[str, int, int, str | None]]


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
    "coverage_bounds",
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
_COMMIT_MARK = b"COMMIT\x1f"
# A co-author trailer is AI attribution only when it names an AI agent. A
# generic human co-author is not evidence of AI contribution.
_AI_COAUTHOR_RE = re.compile(
    r"\b(claude|anthropic|codex|openai|chatgpt|gpt-\d|copilot|gemini|cursor|devin|aider)\b",
    re.IGNORECASE,
)


class GitSourceError(SourceUnavailableError):
    """A git command failed or a declared ref does not resolve.

    Distinct from a genuinely empty history: callers must not read this as
    "no commits".
    """

    def __init__(self, path: Path, reason: str) -> None:
        super().__init__("git", path=str(path), reason=reason)


def _is_git_repo_root(path: Path) -> bool:
    """True when ``path`` is the top level of a git work tree.

    Asks git instead of testing ``.git``'s file type, so linked worktrees
    (whose ``.git`` is a file) are recognised like primary checkouts.
    """
    if not path.is_dir():
        return False
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        if (path / ".git").exists():
            raise GitSourceError(path, f"git rev-parse failed: {exc}") from exc
        return False
    if result.returncode != 0:
        if (path / ".git").exists():
            raise GitSourceError(path, f"git rev-parse exited {result.returncode}: {result.stderr.strip()[:300]}")
        return False
    return Path(result.stdout.strip()).resolve() == path.resolve()


def _run_git_checked(path: Path, args: List[str], *, timeout: int = 60) -> str:
    """Run git and return stdout; any failure is a typed ``GitSourceError``."""
    try:
        result = subprocess.run(
            ["git", "-C", str(path), *args],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise GitSourceError(path, "git executable not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitSourceError(path, f"git {' '.join(args[:2])} timed out after {timeout}s") from exc
    if result.returncode != 0:
        raise GitSourceError(
            path,
            f"git {' '.join(args[:2])} exited {result.returncode}: {result.stderr.strip()[:300]}",
        )
    return result.stdout


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


def commits_in_range(*, start: date, end: date) -> Iterator[GitCommit]:
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
                lines_added=sum(a for _, a, _, _ in rec.path_changes),
                lines_deleted=sum(d for _, _, d, _ in rec.path_changes),
                subject=rec.subject,
            )
    # Fallback: baseline JSONL for historical data not covered by live repos
    for c in commits():
        if start <= c.date <= end and c.commit not in seen:
            seen.add(c.commit)
            yield c


def active_repo_paths(names: Optional[Sequence[str]] = None) -> List[Path]:
    return [
        r.path for r in repos(names=names) if r.exists and _is_git_repo_root(r.path)
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
            for path, added, deleted, old_path in record.path_changes:
                yield GitFileChangeFact(
                    repo=record.repo,
                    commit=record.commit,
                    authored_at=record.authored_at,
                    path=path,
                    old_path=old_path,
                    path_root=_path_root(path),
                    lines_added=added,
                    lines_deleted=deleted,
                    lines_changed=added + deleted,
                )


def patch_excerpt(
    *, repo_path: Path, commit: str, max_lines: int = 120
) -> GitPatchExcerpt:
    # A failed `git show` is a typed failure, never an empty patch.
    output = _run_git_checked(
        repo_path, ["show", "--no-color", "--format=", "--unified=3", commit]
    )
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
                unmarked=total - ai_count,
                dominant_prefix=prefix_counts.most_common(1)[0][0]
                if prefix_counts
                else "other",
                commit_burst_count=_count_bursts(timestamps),
                authors=tuple(sorted(all_authors)),
            )
        )
    return result


def coverage_bounds() -> CoverageBounds | None:
    import json as _json

    path = get_config().baseline_dir / "git_numstat.jsonl"
    if not path.exists():
        return None
    first_dt = last_dt = None
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = _json.loads(line)
                dt = datetime.fromisoformat(obj["authored_at"])
                if first_dt is None:
                    first_dt = dt
                last_dt = dt
            except (KeyError, ValueError, _json.JSONDecodeError):
                continue
    if first_dt is None:
        return None
    return CoverageBounds(
        source="git_baseline",
        first=logical_date(first_dt),
        last=logical_date(last_dt),  # type: ignore[arg-type]
        kind="capture",
    )


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
        if exists and _is_git_repo_root(path):
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
    facts: Sequence[GitCommitFact],
    *,
    max_refs: int = 24,
    cache_only: bool = False,
    max_age_seconds: int | None = None,
) -> dict[str, object]:
    """Read GitHub PR/issue context referenced by commit subjects when available.

    Network refresh belongs to the canonical ``github_context`` materializer.
    This helper is intentionally product-backed so callers can enrich commits
    without making analysis reads perform GitHub API work.
    """
    refs_by_repo: dict[str, dict[str, set[int]]] = defaultdict(
        lambda: {"prs": set(), "issues": set()}
    )
    referenced_commit_dates: list[date] = []
    for fact in facts:
        refs = extract_commit_refs(fact.subject)
        if refs["prs"] or refs["issues"]:
            referenced_commit_dates.append(logical_date(fact.authored_at))
        refs_by_repo[fact.repo]["prs"].update(refs["prs"])
        refs_by_repo[fact.repo]["issues"].update(refs["issues"])

    wanted: set[tuple[str, str, int]] = set()
    attempted = 0
    for repo, refs in sorted(refs_by_repo.items()):
        for number in sorted(refs["prs"]):
            if attempted >= max_refs:
                break
            attempted += 1
            wanted.add((repo, "pr", number))
        for number in sorted(refs["issues"] - refs["prs"]):
            if attempted >= max_refs:
                break
            attempted += 1
            wanted.add((repo, "issue", number))

    if not wanted:
        return {"status": "no_refs", "max_refs": max_refs, "reason": None, "items": []}

    materialization_status = "skipped" if cache_only else "unknown"
    materialization_reason = None
    if not cache_only:
        from ..materialization import ensure_materialized

        window = (
            (
                min(referenced_commit_dates),
                max(referenced_commit_dates) + timedelta(days=1),
            )
            if referenced_commit_dates
            else None
        )
        result = ensure_materialized("github_context", window=window)
        materialization_status = result.status
        materialization_reason = result.reason

    from .github_context import iter_github_context

    product_items: dict[tuple[str, str, int], GitHubItem] = {}
    try:
        for row in iter_github_context(
            projects={repo for repo, _kind, _number in wanted},
            ensure=cache_only,
            window=window if not cache_only else None,
        ):
            key = (row.project, row.item.kind, row.item.number)
            if key in wanted:
                product_items[key] = row.item
    except FileNotFoundError as exc:
        materialization_status = "missing"
        materialization_reason = str(exc)

    items: list[dict[str, object]] = []
    for repo, kind, number in sorted(wanted):
        item = product_items.get((repo, kind, number))
        slug = item.slug if item is not None else ""
        items.append(_github_item(repo, kind, number, slug, item))
    available = [item for item in items if item.get("status") == "ok"]
    status = "ok" if available else "cache_miss"
    if (
        not cache_only
        and materialization_status not in {"ready", "updated"}
        and not available
    ):
        status = "unavailable"
    if attempted >= max_refs:
        status = "truncated"
    return {
        "status": status,
        "max_refs": max_refs,
        "reason": materialization_reason,
        "materialization_status": materialization_status,
        "max_age_seconds": max_age_seconds,
        "items": items,
    }


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
    valid = [p.expanduser() for p in repos_seq if _is_git_repo_root(p.expanduser())]
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
        if not _is_git_repo_root(repo):
            continue
        args = ["log", "--all", "--format=%cI"]
        if since:
            args.append(f"--since={since}")
        if until_str:
            args.append(f"--until={until_str}")
        for raw in _run_git_checked(repo, args, timeout=300).splitlines():
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
    if not _is_git_repo_root(repo_path):
        raise GitSourceError(repo_path, "path is not a Git worktree root")
    # Git date options filter by committer time. Filter author time below,
    # without a committer prefilter that could omit valid author dates.
    cmd = [
        "git",
        "-C",
        str(repo_path),
        "log",
        "-z",
        "--date=iso-strict",
        "--pretty=format:COMMIT%x1f%H%x1f%aI%x1f%aN%x1f%s",
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
    repo_identity = _repo_identity(repo_path)
    with tempfile.TemporaryFile() as stderr_sink:
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=stderr_sink)
        except OSError as exc:
            raise GitSourceError(repo_path, f"git log could not start: {exc}") from exc
        assert proc.stdout is not None
        completed = False
        try:
            for record in _parse_log_z(proc.stdout):
                rec = _finalize_record(repo_identity, record)
                if rec and in_date_range(logical_date(rec.authored_at), start, end):
                    yield rec
            completed = True
        finally:
            proc.stdout.close()
            if proc.poll() is None:
                proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        # Only a fully consumed stream is judged: an early-closed consumer
        # terminates git on purpose.
        if completed and proc.returncode != 0:
            stderr_sink.seek(0)
            detail = stderr_sink.read().decode("utf-8", errors="replace").strip()
            raise GitSourceError(
                repo_path, f"git log exited {proc.returncode}: {detail[:300]}"
            )


def _parse_log_z(stream: Any) -> Iterator[_MutableRepoCommit]:
    """Parse ``git log -z --numstat`` output with exact, unquoted paths.

    Records are NUL-framed: a ``COMMIT`` header line, then numstat entries
    ``added\\tdeleted\\tpath`` or, for a rename, ``added\\tdeleted\\t`` followed
    by the old and new path as two NUL-separated tokens. Paths are kept byte
    exact (surrogate-escaped), never display-quoted or whitespace-stripped.
    """
    current: _MutableRepoCommit | None = None
    rename: list[Any] | None = None  # [added, deleted, old or None]
    carry = b""

    def stat_entry(token: bytes) -> None:
        nonlocal rename
        assert current is not None
        added_s, deleted_s, path = (token.split(b"\t", 2) + [b"", b""])[:3]
        added = int(added_s) if added_s.isdigit() else 0
        deleted = int(deleted_s) if deleted_s.isdigit() else 0
        if path:
            current["path_changes"].append((_decode_path(path), added, deleted, None))
        else:
            rename = [added, deleted, None]

    def token_done(token: bytes) -> Iterator[_MutableRepoCommit]:
        nonlocal current, rename
        if rename is not None:
            if rename[2] is None:
                rename[2] = token
                return
            assert current is not None
            # Identity is the destination path; the source is carried in
            # the rename, not counted as a second file.
            current["path_changes"].append((_decode_path(token), rename[0], rename[1], _decode_path(rename[2])))
            rename = None
            return
        if not token:
            return
        if token.startswith(_COMMIT_MARK):
            if current is not None:
                yield current
            header, _, first = token.partition(b"\n")
            fields = header.decode("utf-8", errors="replace").split("\x1f", 4)
            current = {
                "commit": fields[1] if len(fields) > 1 else "",
                "authored_at": fields[2] if len(fields) > 2 else "",
                "author": fields[3] if len(fields) > 3 else "",
                "subject": fields[4] if len(fields) > 4 else "",
                "path_changes": [],
            }
            if first:
                stat_entry(first)
            return
        if current is not None:
            stat_entry(token[1:] if token.startswith(b"\n") else token)

    while chunk := stream.read(65536):
        tokens = (carry + chunk).split(b"\0")
        carry = tokens.pop()
        for token in tokens:
            yield from token_done(token)
    if carry:
        yield from token_done(carry)
    if current is not None:
        yield current


def _decode_path(raw: bytes) -> str:
    return raw.decode("utf-8", errors="surrogateescape")


def _repo_identity(repo_path: Path) -> str:
    """Identify a repository through its shared Git directory, across worktrees."""
    common = _run_git_checked(repo_path, ["rev-parse", "--path-format=absolute", "--git-common-dir"]).strip()
    common_path = Path(common).resolve()
    for name, spec in PROJECT_SPECS.items():
        if (spec.path.resolve() / ".git") == common_path:
            return name
    return common_path.parent.name


def _git_probe(path: Path, args: list[str]) -> str | None:
    """Probe an optional ref; only Git's missing-ref status means absent."""
    try:
        result = subprocess.run(
            ["git", "-C", str(path), *args],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitSourceError(path, f"git {args[0]} failed: {exc}") from exc
    if result.returncode == 1:
        return None
    if (args[0] == "symbolic-ref" and result.returncode == 128
            and "is not a symbolic ref" in result.stderr):
        return None
    if result.returncode != 0:
        raise GitSourceError(
            path, f"git {args[0]} exited {result.returncode}: {result.stderr.strip()[:300]}"
        )
    return result.stdout.strip() or None


def _default_history_ref(repo_path: Path) -> str | None:
    """The history ref to read, verified to resolve to a commit.

    A declared ``origin/HEAD`` that does not resolve is a typed failure, not a
    silent substitution or an empty history. ``None`` means the repository has
    no commits yet, which is a genuinely empty history.
    """
    remote_head = _git_probe(
        repo_path, ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"]
    )
    if remote_head:
        if not _ref_resolves(repo_path, remote_head):
            raise GitSourceError(
                repo_path,
                f"declared default ref {remote_head} (origin/HEAD) does not resolve",
            )
        return remote_head
    for candidate in ("master", "main"):
        if _ref_resolves(repo_path, candidate):
            return candidate
    current = _run_git_checked(repo_path, ["branch", "--show-current"]).strip()
    if current and _ref_resolves(repo_path, current):
        return current
    return "HEAD" if _ref_resolves(repo_path, "HEAD") else None


def _ref_resolves(repo_path: Path, ref: str) -> bool:
    return _git_probe(repo_path, ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"]) is not None


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
    paths = tuple(sorted({p for p, _, _, _ in record.path_changes}))
    path_roots = tuple(sorted({_path_root(p) for p in paths} - {""}))
    added = sum(a for _, a, _, _ in record.path_changes)
    deleted = sum(d for _, _, d, _ in record.path_changes)
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
    _, stderr = proc.communicate()
    if proc.returncode != 0:
        raise GitSourceError(
            repo_path, f"git log exited {proc.returncode}: {stderr.strip()[:300]}"
        )
    return records


def _parse_git_shortstat(line: str) -> Dict[str, int]:
    files = int(m.group(1)) if (m := _GIT_SHORTSTAT_RE.search(line)) else 0
    added = int(m.group(1)) if (m := _GIT_INSERT_RE.search(line)) else 0
    deleted = int(m.group(1)) if (m := _GIT_DELETE_RE.search(line)) else 0
    return {"files_changed": files, "lines_added": added, "lines_deleted": deleted}


def _fetch_coauthor_info(repo: str, after: date, before: date) -> dict[str, list[str]]:
    """AI co-authors per commit, from explicit AI co-author trailers only."""
    repo_path = _repo_path(repo)
    if not _is_git_repo_root(repo_path):
        return {}
    ref = _default_history_ref(repo_path)
    if ref is None:
        return {}
    # Collect trailers for the selected ref, then match only returned facts.
    stdout = _run_git_checked(
        repo_path,
        [
            "log",
            "--format=%H%n%b%n---END---",
            ref,
        ],
    )
    coauthors: dict[str, list[str]] = {}
    current_sha: str | None = None
    body: list[str] = []
    for line in stdout.splitlines():
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
    if not _is_git_repo_root(repo_path):
        return {}
    stdout = _run_git_checked(repo_path, ["log", "--format=%H %aI", "--all"])
    timestamps: dict[str, datetime] = {}
    for line in stdout.splitlines():
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
    """The co-author's name when the trailer names an AI agent, else None.

    Name and address are both matched: agent trailers often carry a generic
    display name with an agent address (``noreply@anthropic.com``).
    """
    match = re.search(r"Co-Authored-By:\s*(.+?)\s*(?:<([^>]*)>|$)", line, re.IGNORECASE)
    if not match:
        return None
    name = match.group(1).strip()
    address = match.group(2) or ""
    if not (_AI_COAUTHOR_RE.search(name) or _AI_COAUTHOR_RE.search(address)):
        return None
    return name


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
