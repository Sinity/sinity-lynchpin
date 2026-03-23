"""Data analysis functions for project-level git velocity."""

from collections.abc import Callable, Mapping
import sys
import subprocess
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, field

from ...core.projects import ProjectProfile, project_profiles

AGGREGATE_PROJECT = "all-projects"

SKIP_EXTENSIONS = {
    "lock",
    "svg",
    "map",
    "min.js",
    "png",
    "jpg",
    "pdf",
    "gif",
    "ico",
    "woff",
    "woff2",
    "ttf",
    "eot",
}
SKIP_PATHS = {"reports/", "artefacts/", "data/"}

AGGREGATE_PALETTE = [
    "#5470c6",
    "#91cc75",
    "#fac858",
    "#ee6666",
    "#73c0de",
    "#3ba272",
    "#fc8452",
    "#9a60b4",
    "#ea7ccc",
    "#2d91c2",
    "#f5a623",
    "#7ed321",
    "#a17c6b",
    "#b6a2de",
    "#61a0a8",
]


def _skip_common(filename: str) -> bool:
    """Common skip logic for all projects."""
    for ext in SKIP_EXTENSIONS:
        if filename.endswith(f".{ext}"):
            return True
    for path in SKIP_PATHS:
        if filename.startswith(path):
            return True
    return False


def module_from_path(filename: str) -> str:
    parts = filename.split("/")
    if not parts:
        return "(root)"
    if len(parts) == 1:
        return "(root)"
    if parts[0] in {
        "src",
        "crates",
        "modules",
        "module",
        "analyzer",
        "history_cleanup",
        "pipelines",
        "lynchpin",
        "tests",
        "views",
        "sources",
        "system",
        "apps",
        "app",
        "bin",
        "lib",
    }:
        if len(parts) > 1:
            return f"{parts[0]}/{parts[1]}"
    return parts[0]


PROJECT_SPECS: Dict[str, ProjectProfile] = project_profiles()
LogFn = Callable[[str], None]


def _noop(_message: str) -> None:
    pass


@dataclass
class CategoryStats:
    added: int = 0
    removed: int = 0

    @property
    def net(self):
        return self.added - self.removed


@dataclass
class CommitEvent:
    hash: str
    date: str
    author: str
    message: str
    timestamp: str = ""
    parents: int = 1
    by_category: Dict[str, CategoryStats] = field(default_factory=dict)
    files_count: int = 0
    top_files: List[str] = field(default_factory=list)

    @property
    def added(self):
        return sum(c.added for c in self.by_category.values())

    @property
    def removed(self):
        return sum(c.removed for c in self.by_category.values())


@dataclass
class AuthorStats:
    commits: int = 0
    added: int = 0
    removed: int = 0

    @property
    def churn(self):
        return self.added + self.removed

    @property
    def net(self):
        return self.added - self.removed


@dataclass
class DailyStats:
    date: str
    by_category: Dict[str, CategoryStats] = field(default_factory=dict)
    commits: List[CommitEvent] = field(default_factory=list)

    @property
    def added(self):
        return sum(c.added for c in self.by_category.values())

    @property
    def removed(self):
        return sum(c.removed for c in self.by_category.values())

    @property
    def net(self):
        return self.added - self.removed


@dataclass
class ProjectStats:
    name: str
    daily: Dict[str, DailyStats] = field(default_factory=dict)
    file_stats: Dict[str, CategoryStats] = field(default_factory=dict)
    module_stats: Dict[str, CategoryStats] = field(default_factory=dict)
    module_authors: Dict[str, Dict[str, int]] = field(default_factory=dict)
    author_stats: Dict[str, AuthorStats] = field(default_factory=dict)
    cochange: Dict[tuple, int] = field(default_factory=dict)
    tags: List[dict] = field(default_factory=list)


def run_git_log(path: Path) -> List[str]:
    sep = "|||"
    fmt = f"%h{sep}%ad{sep}%an{sep}%s{sep}%P"

    cmd = [
        "git",
        "log",
        "--all",
        "--date=iso-strict-local",
        f"--pretty=format:COMMIT:{fmt}",
        "--numstat",
    ]
    try:
        subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=path,
            check=True,
            capture_output=True,
        )
        result = subprocess.run(
            cmd, cwd=path, capture_output=True, text=True, check=True, errors="replace"
        )
        return result.stdout.splitlines()
    except subprocess.CalledProcessError:
        print(f"Skipping {path} (not a git repo or error)", file=sys.stderr)
        return []


def run_git_tags(path: Path) -> List[dict]:
    cmd = [
        "git",
        "for-each-ref",
        "--sort=creatordate",
        "--format=%(refname:short)|||%(creatordate:iso-strict)",
        "refs/tags",
    ]
    try:
        result = subprocess.run(
            cmd, cwd=path, capture_output=True, text=True, check=True, errors="replace"
        )
    except subprocess.CalledProcessError:
        return []

    tags = []
    for line in result.stdout.splitlines():
        if "|||" not in line:
            continue
        name, date_raw = line.split("|||", 1)
        date = date_raw.strip()
        if not name or not date:
            continue
        tags.append({"name": name.strip(), "date": date})
    return tags


def parse_log(
    lines: List[str], project_name: str, classify_fn: Callable[[str], Optional[str]]
) -> ProjectStats:
    stats = ProjectStats(name=project_name)

    current_commit: Optional[CommitEvent] = None
    current_files_buffer = []
    current_file_scores = {}
    current_modules = set()
    current_files_count = 0

    def flush_commit():
        nonlocal \
            current_commit, \
            current_files_buffer, \
            current_file_scores, \
            current_modules, \
            current_files_count
        if current_commit:
            if current_file_scores:
                sorted_files = sorted(
                    current_file_scores.items(), key=lambda item: item[1], reverse=True
                )
                current_commit.top_files = [name for name, _ in sorted_files[:5]]
            current_commit.files_count = current_files_count

            d_str = current_commit.date
            if d_str not in stats.daily:
                stats.daily[d_str] = DailyStats(date=d_str)

            day = stats.daily[d_str]

            # Aggregate category stats
            for cat, cat_stats in current_commit.by_category.items():
                if cat not in day.by_category:
                    day.by_category[cat] = CategoryStats()
                day.by_category[cat].added += cat_stats.added
                day.by_category[cat].removed += cat_stats.removed

            day.commits.append(current_commit)
            author_stats = stats.author_stats.setdefault(
                current_commit.author, AuthorStats()
            )
            author_stats.commits += 1
            author_stats.added += current_commit.added
            author_stats.removed += current_commit.removed

            if current_modules:
                for module in current_modules:
                    stats.module_authors.setdefault(module, {})
                    stats.module_authors[module][current_commit.author] = (
                        stats.module_authors[module].get(current_commit.author, 0) + 1
                    )

                modules = sorted(current_modules)
                for i in range(len(modules)):
                    for j in range(i + 1, len(modules)):
                        pair = (modules[i], modules[j])
                        stats.cochange[pair] = stats.cochange.get(pair, 0) + 1

        current_commit = None
        current_files_buffer = []
        current_file_scores = {}
        current_modules = set()
        current_files_count = 0

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if line.startswith("COMMIT:"):
            flush_commit()
            content = line[len("COMMIT:"):]
            parts = content.split("|||")
            if len(parts) >= 5:
                h, d_raw, auth, msg, parents_raw = (
                    parts[0],
                    parts[1],
                    parts[2],
                    parts[3],
                    parts[4],
                )
                date_str = d_raw.split("T")[0]
                parents_count = len(parents_raw.split()) if parents_raw.strip() else 0
                current_commit = CommitEvent(
                    hash=h,
                    date=date_str,
                    author=auth,
                    message=msg,
                    timestamp=d_raw,
                    parents=parents_count,
                )
            elif len(parts) >= 4:
                h, d_raw, auth, msg = parts[0], parts[1], parts[2], parts[3]
                date_str = d_raw.split("T")[0]
                current_commit = CommitEvent(
                    hash=h, date=date_str, author=auth, message=msg, timestamp=d_raw
                )
            continue

        parts = line.split(maxsplit=2)
        if len(parts) < 3:
            continue

        added_str, removed_str, filename = parts

        if added_str == "-" or removed_str == "-":
            continue

        try:
            add_val = int(added_str)
            rem_val = int(removed_str)
        except ValueError:
            continue

        category = classify_fn(filename)
        if category is None:
            continue

        if current_commit:
            if category not in current_commit.by_category:
                current_commit.by_category[category] = CategoryStats()
            current_commit.by_category[category].added += add_val
            current_commit.by_category[category].removed += rem_val

            current_files_buffer.append(filename)
            current_files_count += 1
            current_file_scores[filename] = (
                current_file_scores.get(filename, 0) + add_val + rem_val
            )

            if filename not in stats.file_stats:
                stats.file_stats[filename] = CategoryStats()
            stats.file_stats[filename].added += add_val
            stats.file_stats[filename].removed += rem_val

            module = module_from_path(filename)
            current_modules.add(module)
            if module not in stats.module_stats:
                stats.module_stats[module] = CategoryStats()
            stats.module_stats[module].added += add_val
            stats.module_stats[module].removed += rem_val

    flush_commit()
    return stats


def analyze_projects(
    project_specs: Mapping[str, ProjectProfile],
    *,
    log: LogFn | None = None,
) -> Dict[str, ProjectStats]:
    if log is None:
        log = _noop
    all_stats = {}
    for name, spec in project_specs.items():
        path = spec.path
        if not path.exists():
            log(f"Path not found: {path}, skipping...")
            continue

        log(f"Analyzing {name}...")
        lines = run_git_log(path)
        classify_fn = spec.classify
        stats = parse_log(lines, name, classify_fn)
        stats.tags = run_git_tags(path)
        all_stats[name] = stats
    return all_stats


def _aggregate_spec(project_names: List[str]) -> ProjectProfile:
    categories = sorted(project_names)
    colors = {
        name: AGGREGATE_PALETTE[i % len(AGGREGATE_PALETTE)]
        for i, name in enumerate(categories)
    }
    return ProjectProfile(
        name=AGGREGATE_PROJECT,
        path=Path("(aggregate)"),
        classify=lambda _path: None,
        categories=tuple(categories),
        colors=colors,
    )


def _collapse_commit(event: CommitEvent, project: str) -> CommitEvent:
    stats = CategoryStats(added=event.added, removed=event.removed)
    prefixed_files = [f"{project}:{name}" for name in event.top_files]
    return CommitEvent(
        hash=event.hash,
        date=event.date,
        author=event.author,
        message=f"[{project}] {event.message}",
        timestamp=event.timestamp,
        parents=event.parents,
        by_category={project: stats},
        files_count=event.files_count,
        top_files=prefixed_files,
    )


def _aggregate_stats(all_stats: Dict[str, ProjectStats]) -> ProjectStats:
    aggregate = ProjectStats(name=AGGREGATE_PROJECT)
    for project, stats in all_stats.items():
        for day, daily in stats.daily.items():
            agg_day = aggregate.daily.setdefault(day, DailyStats(date=day))
            cat_stats = agg_day.by_category.setdefault(project, CategoryStats())
            cat_stats.added += daily.added
            cat_stats.removed += daily.removed
            for event in daily.commits:
                agg_day.commits.append(_collapse_commit(event, project))
        for filename, file_stats in stats.file_stats.items():
            prefixed_file = f"{project}:{filename}"
            aggregate.file_stats[prefixed_file] = CategoryStats(
                added=file_stats.added,
                removed=file_stats.removed,
            )
        for module, module_stats in stats.module_stats.items():
            prefixed_module = f"{project}:{module}"
            aggregate.module_stats[prefixed_module] = CategoryStats(
                added=module_stats.added,
                removed=module_stats.removed,
            )
        for module, authors in stats.module_authors.items():
            prefixed_module = f"{project}:{module}"
            aggregate.module_authors[prefixed_module] = dict(authors)
        for author, author_stats in stats.author_stats.items():
            aggregate_author = aggregate.author_stats.setdefault(author, AuthorStats())
            aggregate_author.commits += author_stats.commits
            aggregate_author.added += author_stats.added
            aggregate_author.removed += author_stats.removed
        for (left, right), weight in stats.cochange.items():
            pair = (f"{project}:{left}", f"{project}:{right}")
            aggregate.cochange[pair] = aggregate.cochange.get(pair, 0) + weight
        for tag in stats.tags:
            aggregate.tags.append(
                {"name": f"{project}:{tag['name']}", "date": tag["date"]}
            )
    return aggregate
