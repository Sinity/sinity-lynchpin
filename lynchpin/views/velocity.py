#!/usr/bin/env python3
"""
Generate velocity plots (LoC growth and churn) for all bundled projects.
Generates a rich, interactive HTML dashboard using Apache ECharts.

Each project has bespoke categorization to show meaningful breakdowns.
"""

import sys
import json
import datetime as dt
import subprocess
from pathlib import Path
from typing import Callable, Dict, List, Optional
from dataclasses import dataclass, field

import typer

from ..core.projects import ProjectProfile, project_profiles
from ..core.io import write_text_if_changed

DEFAULT_OUTPUT = Path("artefacts/meta/velocity/velocity.html")
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
            content = line[len("COMMIT:") :]
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


def analyze_projects(project_specs: Dict[str, ProjectProfile]) -> Dict[str, ProjectStats]:
    all_stats = {}
    for name, spec in project_specs.items():
        path = spec.path
        if not path.exists():
            print(f"Path not found: {path}, skipping...", file=sys.stderr)
            continue

        print(f"Analyzing {name}...")
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


def generate_html(
    all_stats: Dict[str, ProjectStats],
    project_specs: Dict[str, ProjectProfile],
    output_path: Path,
):
    # Collect all dates
    all_dates = set()
    for p in all_stats.values():
        all_dates.update(p.daily.keys())
    sorted_dates = sorted(list(all_dates))

    if not sorted_dates:
        print("No data found.")
        return

    # Build datasets per project per category
    js_projects = {}

    for name, stats in all_stats.items():
        spec = project_specs[name]
        categories = list(spec.categories)
        colors = spec.colors

        project_data = {
            "categories": {},
            "categoryList": categories,
            "colors": colors,
            "events": {},
            "activity": {"commits": [], "churn": [], "net": []},
            "files": [],
            "modules": [],
            "owners": [],
            "authors": [],
            "cochange": {"nodes": [], "edges": []},
            "tags": [],
        }

        # Initialize cumulative counters per category
        cumulative = {cat: 0 for cat in categories}

        for cat in categories:
            project_data["categories"][cat] = {"growth": [], "churn": [], "net": []}

        for d in sorted_dates:
            day_stats = stats.daily.get(d)
            day_churn = 0
            day_net = 0

            for cat in categories:
                if day_stats and cat in day_stats.by_category:
                    cat_data = day_stats.by_category[cat]
                    cumulative[cat] += cat_data.net
                    if cumulative[cat] < 0:
                        cumulative[cat] = 0
                    cat_churn = cat_data.added + cat_data.removed
                    project_data["categories"][cat]["churn"].append(cat_churn)
                    project_data["categories"][cat]["net"].append(cat_data.net)
                    day_churn += cat_churn
                    day_net += cat_data.net
                else:
                    project_data["categories"][cat]["churn"].append(0)
                    project_data["categories"][cat]["net"].append(0)

                project_data["categories"][cat]["growth"].append(cumulative[cat])

            project_data["activity"]["commits"].append(
                len(day_stats.commits) if day_stats and day_stats.commits else 0
            )
            project_data["activity"]["churn"].append(day_churn)
            project_data["activity"]["net"].append(day_net)

            # Events for inspector
            if day_stats and day_stats.commits:
                ev_list = []
                for c in day_stats.commits:
                    cat_breakdown = {
                        k: {"a": v.added, "r": v.removed}
                        for k, v in c.by_category.items()
                    }
                    ev_list.append(
                        {
                            "h": c.hash,
                            "a": c.author,
                            "m": c.message,
                            "+": c.added,
                            "-": c.removed,
                            "t": c.timestamp,
                            "p": c.parents,
                            "cats": cat_breakdown,
                            "f": c.top_files,
                            "fc": c.files_count,
                        }
                    )
                ev_list.sort(key=lambda x: x["+"] + x["-"], reverse=True)
                project_data["events"][d] = ev_list

        file_rows = []
        for filename, fstats in stats.file_stats.items():
            churn = fstats.added + fstats.removed
            net = fstats.added - fstats.removed
            loc = max(0, net)
            volatility = churn / max(1, loc)
            file_rows.append(
                {
                    "name": filename,
                    "churn": churn,
                    "net": net,
                    "loc": loc,
                    "volatility": round(volatility, 3),
                }
            )
        file_rows.sort(key=lambda row: row["churn"], reverse=True)
        project_data["files"] = file_rows[:200]

        module_rows = []
        for module, mstats in stats.module_stats.items():
            churn = mstats.added + mstats.removed
            net = mstats.added - mstats.removed
            loc = max(0, net)
            volatility = churn / max(1, loc)
            module_rows.append(
                {
                    "name": module,
                    "churn": churn,
                    "net": net,
                    "loc": loc,
                    "volatility": round(volatility, 3),
                }
            )
        module_rows.sort(key=lambda row: row["churn"], reverse=True)
        project_data["modules"] = module_rows[:200]

        owners = []
        for module, authors in stats.module_authors.items():
            total = sum(authors.values())
            if total == 0:
                continue
            top_author = max(authors.items(), key=lambda item: item[1])
            churn = 0
            if module in stats.module_stats:
                mstats = stats.module_stats[module]
                churn = mstats.added + mstats.removed
            owners.append(
                {
                    "module": module,
                    "author": top_author[0],
                    "share": round(top_author[1] / total, 3),
                    "commits": total,
                    "churn": churn,
                }
            )
        owners.sort(key=lambda row: (row["share"], row["churn"]), reverse=True)
        project_data["owners"] = owners[:200]

        author_rows = []
        author_totals: Dict[str, dict] = {}
        for daily in stats.daily.values():
            for event in daily.commits:
                row = author_totals.setdefault(
                    event.author,
                    {
                        "name": event.author,
                        "commits": 0,
                        "churn": 0,
                        "net": 0,
                        "mergeCommits": 0,
                        "lastSeen": event.timestamp or daily.date,
                    },
                )
                row["commits"] += 1
                row["churn"] += event.added + event.removed
                row["net"] += event.added - event.removed
                row["mergeCommits"] += 1 if event.parents > 1 else 0
                if event.timestamp and event.timestamp > row["lastSeen"]:
                    row["lastSeen"] = event.timestamp
        for row in author_totals.values():
            author_rows.append(row)
        author_rows.sort(key=lambda row: (row["churn"], row["commits"]), reverse=True)
        project_data["authors"] = author_rows[:200]

        top_modules = [row["name"] for row in module_rows[:20]]
        module_weights = {row["name"]: row["churn"] for row in module_rows}
        nodes = []
        for module_name in top_modules:
            weight = module_weights.get(module_name, 0)
            nodes.append(
                {
                    "name": module_name,
                    "value": weight,
                    "symbolSize": max(8, min(40, 8 + (weight**0.5))),
                }
            )
        edges = []
        for (left, right), weight in stats.cochange.items():
            if left in top_modules and right in top_modules:
                edges.append(
                    {
                        "source": left,
                        "target": right,
                        "value": weight,
                        "lineStyle": {"width": max(1, min(6, weight / 2))},
                    }
                )
        edges.sort(key=lambda row: row["value"], reverse=True)
        project_data["cochange"] = {"nodes": nodes, "edges": edges[:60]}
        project_data["tags"] = stats.tags

        js_projects[name] = project_data

    # Compute summary stats for each project
    project_summaries = {}
    for name, stats in all_stats.items():
        spec = project_specs[name]
        categories = list(spec.categories)
        total_loc = (
            sum(
                js_projects[name]["categories"][cat]["growth"][-1] for cat in categories
            )
            if sorted_dates
            else 0
        )
        total_commits = sum(len(day.commits) for day in stats.daily.values())
        active_days = len([d for d in stats.daily.values() if d.commits])

        # Recent 30-day velocity
        recent_net = 0
        recent_churn = 0
        for cat in categories:
            growth = js_projects[name]["categories"][cat]["growth"]
            churn = js_projects[name]["categories"][cat]["churn"]
            if len(growth) >= 30:
                recent_net += growth[-1] - growth[-30]
                recent_churn += sum(churn[-30:])
            elif len(growth) > 0:
                recent_net += growth[-1]
                recent_churn += sum(churn)

        project_summaries[name] = {
            "totalLoc": total_loc,
            "totalCommits": total_commits,
            "activeDays": active_days,
            "recentNet": recent_net,
            "recentChurn": recent_churn,
            "firstDate": min(stats.daily.keys()) if stats.daily else None,
            "lastDate": max(stats.daily.keys()) if stats.daily else None,
            "authorCount": len(author_totals),
            "tagCount": len(stats.tags),
        }

    dashboard_payload = {
        "dates": sorted_dates,
        "projectData": js_projects,
        "projectSummaries": project_summaries,
        "aggregateProject": AGGREGATE_PROJECT,
        "generatedAt": dt.datetime.now().replace(microsecond=0).isoformat(),
    }

    html_template = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Velocity Atlas</title>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=Space+Grotesk:wght@400;500;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --paper: #f6efe3;
            --paper-2: #fcf8f1;
            --surface: rgba(255, 252, 247, 0.84);
            --surface-strong: rgba(255, 255, 255, 0.92);
            --ink: #182230;
            --ink-soft: #4d5a6c;
            --line: rgba(24, 34, 48, 0.12);
            --line-strong: rgba(24, 34, 48, 0.18);
            --accent: #c5523d;
            --accent-2: #1f7a8c;
            --accent-3: #d19a2d;
            --good: #24845d;
            --bad: #b93d2f;
            --shadow: 0 24px 60px rgba(72, 48, 14, 0.12);
            --radius-lg: 28px;
            --radius-md: 18px;
            --radius-sm: 12px;
            --font-ui: "Space Grotesk", sans-serif;
            --font-mono: "IBM Plex Mono", monospace;
        }

        * {
            box-sizing: border-box;
        }

        html, body {
            margin: 0;
            min-height: 100%;
            background:
                radial-gradient(circle at top left, rgba(197, 82, 61, 0.14), transparent 28rem),
                radial-gradient(circle at top right, rgba(31, 122, 140, 0.12), transparent 32rem),
                linear-gradient(180deg, #f8f2e7 0%, #f4ecde 52%, #efe6d7 100%);
            color: var(--ink);
            font-family: var(--font-ui);
        }

        body {
            min-height: 100vh;
            padding: 32px 20px 56px;
        }

        .shell {
            max-width: 1540px;
            margin: 0 auto;
            display: flex;
            flex-direction: column;
            gap: 22px;
        }

        .hero {
            display: grid;
            grid-template-columns: minmax(0, 1.45fr) minmax(320px, 1fr);
            gap: 18px;
            padding: 28px;
            border: 1px solid var(--line);
            border-radius: var(--radius-lg);
            background: linear-gradient(145deg, rgba(255,255,255,0.74), rgba(255,248,238,0.88));
            box-shadow: var(--shadow);
            backdrop-filter: blur(18px);
        }

        .eyebrow {
            margin: 0 0 12px;
            color: var(--accent);
            text-transform: uppercase;
            letter-spacing: 0.16em;
            font-size: 11px;
            font-weight: 700;
        }

        h1 {
            margin: 0;
            font-size: clamp(2.2rem, 4vw, 4.4rem);
            line-height: 0.95;
            letter-spacing: -0.05em;
        }

        .lede {
            max-width: 52rem;
            margin: 16px 0 0;
            color: var(--ink-soft);
            font-size: 1rem;
            line-height: 1.65;
        }

        .hero-meta {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            margin-top: 20px;
        }

        .meta-pill,
        .segmented button,
        .project-pill,
        .mode-toggle button {
            border: 1px solid var(--line);
            background: rgba(255, 255, 255, 0.72);
            color: var(--ink);
            border-radius: 999px;
            font: 600 12px/1 var(--font-ui);
            padding: 10px 14px;
            transition: transform 140ms ease, background 140ms ease, border-color 140ms ease;
        }

        .meta-pill {
            color: var(--ink-soft);
            font-family: var(--font-mono);
            font-weight: 500;
        }

        .project-panel {
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            gap: 16px;
        }

        .project-strip,
        .segmented,
        .mode-toggle {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
        }

        .segmented button,
        .project-pill,
        .mode-toggle button,
        .search-input {
            cursor: pointer;
        }

        .segmented button.active,
        .project-pill.active,
        .mode-toggle button.active {
            background: var(--ink);
            border-color: var(--ink);
            color: white;
            transform: translateY(-1px);
        }

        .project-pill.aggregate {
            border-color: rgba(197, 82, 61, 0.28);
        }

        .project-pill:hover,
        .segmented button:hover,
        .mode-toggle button:hover {
            transform: translateY(-1px);
            border-color: var(--ink-soft);
        }

        .overview-banner {
            display: grid;
            grid-template-columns: minmax(0, 1.3fr) minmax(260px, 0.9fr);
            gap: 18px;
        }

        .feature-card,
        .card {
            border: 1px solid var(--line);
            border-radius: var(--radius-md);
            background: var(--surface);
            box-shadow: 0 18px 36px rgba(83, 61, 25, 0.08);
            backdrop-filter: blur(16px);
        }

        .feature-card {
            padding: 22px 24px;
        }

        .feature-title {
            display: flex;
            justify-content: space-between;
            gap: 16px;
            align-items: flex-start;
        }

        .feature-title h2,
        .section-title {
            margin: 0;
            font-size: 1.2rem;
            line-height: 1.1;
        }

        .feature-title p,
        .section-subtitle {
            margin: 6px 0 0;
            color: var(--ink-soft);
            font-size: 0.95rem;
            line-height: 1.55;
        }

        .banner-grid {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 14px;
            margin-top: 18px;
        }

        .banner-item {
            border: 1px solid var(--line);
            border-radius: var(--radius-sm);
            background: rgba(255,255,255,0.62);
            padding: 14px 16px;
        }

        .banner-item span {
            display: block;
            color: var(--ink-soft);
            font-size: 0.77rem;
            letter-spacing: 0.02em;
            text-transform: uppercase;
        }

        .banner-item strong {
            display: block;
            margin-top: 8px;
            font-size: 1.2rem;
            font-family: var(--font-mono);
        }

        .metric-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
            gap: 14px;
        }

        .metric-card {
            padding: 18px 20px;
        }

        .metric-label {
            color: var(--ink-soft);
            text-transform: uppercase;
            letter-spacing: 0.08em;
            font-size: 0.72rem;
        }

        .metric-value {
            margin-top: 10px;
            font-family: var(--font-mono);
            font-size: clamp(1.3rem, 2.4vw, 2.2rem);
        }

        .metric-value.positive { color: var(--good); }
        .metric-value.negative { color: var(--bad); }

        .views {
            display: flex;
            flex-direction: column;
            gap: 18px;
        }

        .view {
            display: none;
            gap: 18px;
        }

        .view.active {
            display: flex;
            flex-direction: column;
        }

        .grid-2,
        .grid-3 {
            display: grid;
            gap: 18px;
        }

        .grid-2 {
            grid-template-columns: repeat(2, minmax(0, 1fr));
        }

        .grid-3 {
            grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr) minmax(0, 1fr);
        }

        .card {
            padding: 18px 20px 20px;
            min-height: 180px;
        }

        .card-header {
            display: flex;
            justify-content: space-between;
            gap: 14px;
            align-items: flex-start;
            margin-bottom: 14px;
        }

        .card-header h3 {
            margin: 0;
            font-size: 1rem;
        }

        .card-header p {
            margin: 5px 0 0;
            color: var(--ink-soft);
            font-size: 0.88rem;
            line-height: 1.45;
        }

        .chart {
            width: 100%;
            min-height: 360px;
        }

        .chart.tall {
            min-height: 430px;
        }

        .search-row {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            align-items: center;
        }

        .search-input {
            min-width: min(26rem, 100%);
            border: 1px solid var(--line);
            background: rgba(255,255,255,0.72);
            color: var(--ink);
            border-radius: 999px;
            padding: 11px 16px;
            font: 500 13px/1 var(--font-mono);
            outline: none;
        }

        .search-input::placeholder {
            color: rgba(77, 90, 108, 0.76);
        }

        .table-wrap {
            overflow: auto;
            border-top: 1px solid var(--line);
            margin-top: 14px;
            padding-top: 10px;
        }

        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }

        th,
        td {
            text-align: left;
            padding: 11px 10px;
            border-bottom: 1px solid var(--line);
            vertical-align: top;
        }

        th {
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            color: var(--ink-soft);
        }

        td:first-child,
        th:first-child {
            padding-left: 0;
        }

        td:last-child,
        th:last-child {
            padding-right: 0;
        }

        .mono {
            font-family: var(--font-mono);
        }

        .dim {
            color: var(--ink-soft);
        }

        .tag-list,
        .legend-list,
        .commit-list {
            display: flex;
            flex-direction: column;
            gap: 10px;
        }

        .tag-pill {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 8px 10px;
            border-radius: 999px;
            background: rgba(255,255,255,0.72);
            border: 1px solid var(--line);
            font: 500 12px/1 var(--font-mono);
            width: fit-content;
        }

        .tag-dot {
            width: 10px;
            height: 10px;
            border-radius: 999px;
            background: var(--accent-3);
        }

        .commit-entry {
            padding: 14px 15px;
            border-radius: var(--radius-sm);
            border: 1px solid var(--line);
            background: rgba(255,255,255,0.72);
        }

        .commit-top {
            display: flex;
            justify-content: space-between;
            gap: 12px;
            align-items: flex-start;
        }

        .commit-title {
            font-size: 0.95rem;
            font-weight: 600;
            line-height: 1.45;
        }

        .commit-meta,
        .commit-files,
        .empty-state {
            margin-top: 8px;
            color: var(--ink-soft);
            font-size: 0.83rem;
            line-height: 1.55;
        }

        .chips {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-top: 10px;
        }

        .chip {
            padding: 6px 9px;
            border-radius: 999px;
            background: rgba(24,34,48,0.06);
            font: 500 11px/1 var(--font-mono);
        }

        .chip.colorized {
            color: white;
        }

        .side-stack {
            display: flex;
            flex-direction: column;
            gap: 18px;
        }

        .small-note {
            color: var(--ink-soft);
            font-size: 0.82rem;
            line-height: 1.5;
        }

        .empty-state {
            padding: 14px 0 2px;
        }

        @media (max-width: 1180px) {
            .hero,
            .overview-banner,
            .grid-2,
            .grid-3 {
                grid-template-columns: 1fr;
            }
        }

        @media (max-width: 780px) {
            body {
                padding: 20px 14px 40px;
            }

            .hero,
            .feature-card,
            .card {
                padding: 18px;
            }

            .banner-grid,
            .metric-grid {
                grid-template-columns: 1fr 1fr;
            }

            .chart,
            .chart.tall {
                min-height: 300px;
            }
        }
    </style>
</head>
<body>
    <div class="shell">
        <section class="hero">
            <div>
                <p class="eyebrow">Repository velocity atlas</p>
                <h1>Velocity</h1>
                <p class="lede">Cross-repo growth, churn, authorship, and co-change rendered from git history. The dashboard is static HTML, but it behaves like an exploratory control panel rather than a screenshot.</p>
                <div class="hero-meta">
                    <span class="meta-pill">Generated __GENERATED_AT__</span>
                    <span class="meta-pill">Static HTML + ECharts</span>
                    <span class="meta-pill">Range-aware commit inspector</span>
                    <span class="meta-pill">Hotspots, authors, topology</span>
                </div>
            </div>
            <div class="project-panel">
                <div>
                    <div class="section-title">Project focus</div>
                    <div class="section-subtitle">Switch repositories instantly. The aggregate view stacks all included repos together, but the other views stay repo-specific.</div>
                </div>
                <div id="project-strip" class="project-strip"></div>
                <div class="segmented" id="view-tabs"></div>
                <div class="segmented" id="range-tabs"></div>
            </div>
        </section>

        <section class="overview-banner">
            <div id="overview-copy" class="feature-card"></div>
            <div id="summary-metrics" class="metric-grid"></div>
        </section>

        <section class="views">
            <div id="view-pulse" class="view active">
                <div class="grid-2">
                    <div class="card">
                        <div class="card-header">
                            <div>
                                <h3>Growth silhouette</h3>
                                <p>Stacked cumulative code growth by category. Release tags are listed separately so the chart stays readable.</p>
                            </div>
                        </div>
                        <div id="growth-chart" class="chart tall"></div>
                    </div>
                    <div class="card">
                        <div class="card-header">
                            <div>
                                <h3>Flow pulse</h3>
                                <p>Daily churn bars and net delta line. Click a day to inspect the underlying commits on the right.</p>
                            </div>
                        </div>
                        <div id="flow-chart" class="chart tall"></div>
                    </div>
                </div>
                <div class="grid-3">
                    <div class="card">
                        <div class="card-header">
                            <div>
                                <h3>Category share</h3>
                                <p>Current footprint and momentum by category over the selected range.</p>
                            </div>
                        </div>
                        <div id="share-chart" class="chart"></div>
                    </div>
                    <div class="card">
                        <div class="card-header">
                            <div>
                                <h3>Category ledger</h3>
                                <p>Dominant categories, footprint, and range net changes.</p>
                            </div>
                        </div>
                        <div class="table-wrap">
                            <table id="category-table"></table>
                        </div>
                    </div>
                    <div class="card">
                        <div class="card-header">
                            <div>
                                <h3>Commit inspector</h3>
                                <p id="commit-heading">Select a day on the flow chart to inspect the commit stack.</p>
                            </div>
                        </div>
                        <div id="commit-list" class="commit-list"></div>
                    </div>
                </div>
            </div>

            <div id="view-hotspots" class="view">
                <div class="card">
                    <div class="card-header">
                        <div>
                            <h3>Hotspot explorer</h3>
                            <p>All-time hotspots by files or modules. Search narrows the ranked list without recomputing the underlying git history.</p>
                        </div>
                        <div class="search-row">
                            <div id="hotspot-mode" class="mode-toggle"></div>
                            <input id="hotspot-search" class="search-input" type="search" placeholder="Filter files or modules by path fragment">
                        </div>
                    </div>
                    <div class="grid-2">
                        <div id="hotspot-chart" class="chart"></div>
                        <div class="table-wrap">
                            <table id="hotspot-table"></table>
                        </div>
                    </div>
                </div>
            </div>

            <div id="view-people" class="view">
                <div class="grid-2">
                    <div class="card">
                        <div class="card-header">
                            <div>
                                <h3>Authors in range</h3>
                                <p>Real author aggregation from commit events in the selected window, not inferred ownership.</p>
                            </div>
                        </div>
                        <div id="author-chart" class="chart"></div>
                    </div>
                    <div class="side-stack">
                        <div class="card">
                            <div class="card-header">
                                <div>
                                    <h3>Author ledger</h3>
                                    <p>Commit counts, churn, and net deltas for the selected range.</p>
                                </div>
                            </div>
                            <div class="table-wrap">
                                <table id="author-table"></table>
                            </div>
                        </div>
                        <div class="card">
                            <div class="card-header">
                                <div>
                                    <h3>Module owners</h3>
                                    <p>Top owners by module share across the whole repository history.</p>
                                </div>
                            </div>
                            <div class="table-wrap">
                                <table id="ownership-table"></table>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <div id="view-topology" class="view">
                <div class="grid-2">
                    <div class="card">
                        <div class="card-header">
                            <div>
                                <h3>Co-change topology</h3>
                                <p>Modules that repeatedly move together across commits. Heavier edges mean more shared change activity.</p>
                            </div>
                        </div>
                        <div id="cochange-chart" class="chart tall"></div>
                    </div>
                    <div class="side-stack">
                        <div class="card">
                            <div class="card-header">
                                <div>
                                    <h3>Release tags</h3>
                                    <p>Latest tags, if the repo exposes them. Useful for correlating velocity bursts with release cadence.</p>
                                </div>
                            </div>
                            <div id="tag-list" class="tag-list"></div>
                        </div>
                        <div class="card">
                            <div class="card-header">
                                <div>
                                    <h3>Project notes</h3>
                                    <p>Interpret the numbers before overfitting them.</p>
                                </div>
                            </div>
                            <div class="legend-list small-note">
                                <div>Growth is clamped at zero after cumulative net changes, so historical delete-heavy windows do not make the chart go negative.</div>
                                <div>Hotspots and module ownership are full-history views. Range selection only affects activity, authors, and category momentum.</div>
                                <div>The aggregate project stacks repositories rather than categories. Its “category” values are really repo names.</div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </section>
    </div>

    <script>
        const dashboard = __PAYLOAD__;
        const dates = dashboard.dates;
        const projectData = dashboard.projectData;
        const projectSummaries = dashboard.projectSummaries;
        const projects = Object.keys(projectData);
        const chartInstances = {};
        const rangeOptions = [
            { id: "30", label: "30D", days: 30 },
            { id: "90", label: "90D", days: 90 },
            { id: "180", label: "180D", days: 180 },
            { id: "all", label: "All", days: null },
        ];
        const viewOptions = [
            { id: "pulse", label: "Pulse" },
            { id: "hotspots", label: "Hotspots" },
            { id: "people", label: "People" },
            { id: "topology", label: "Topology" },
        ];
        let currentProject = projects.includes(dashboard.aggregateProject) ? dashboard.aggregateProject : projects[0];
        let currentView = "pulse";
        let currentRange = "180";
        let currentHotspotMode = "modules";
        let currentHotspotQuery = "";
        let selectedDate = null;

        function formatProjectName(name) {
            if (name === dashboard.aggregateProject) return "All Projects";
            return name;
        }

        function formatNumber(value) {
            return Number(value || 0).toLocaleString();
        }

        function formatSigned(value) {
            const number = Number(value || 0);
            const prefix = number > 0 ? "+" : "";
            return `${prefix}${number.toLocaleString()}`;
        }

        function escapeHtml(value) {
            return String(value)
                .replaceAll("&", "&amp;")
                .replaceAll("<", "&lt;")
                .replaceAll(">", "&gt;")
                .replaceAll('"', "&quot;")
                .replaceAll("'", "&#39;");
        }

        function currentData() {
            return projectData[currentProject];
        }

        function currentSummary() {
            return projectSummaries[currentProject];
        }

        function rangeDays() {
            return rangeOptions.find((option) => option.id === currentRange)?.days ?? null;
        }

        function rangeStartIndex() {
            const days = rangeDays();
            if (!days || dates.length <= days) return 0;
            return Math.max(0, dates.length - days);
        }

        function visibleDates() {
            return dates.slice(rangeStartIndex());
        }

        function sliceSeries(series) {
            return series.slice(rangeStartIndex());
        }

        function sum(values) {
            return values.reduce((acc, value) => acc + value, 0);
        }

        function categoryRows(data) {
            const start = rangeStartIndex();
            const totals = data.categoryList.map((category) => {
                const growth = data.categories[category].growth;
                const net = data.categories[category].net;
                const total = growth.length ? growth[growth.length - 1] : 0;
                const rangeNet = sum(net.slice(start));
                return {
                    category,
                    total,
                    rangeNet,
                    color: data.colors[category],
                };
            });
            const totalLoc = sum(totals.map((row) => row.total));
            return totals
                .map((row) => ({
                    ...row,
                    share: totalLoc ? row.total / totalLoc : 0,
                }))
                .sort((left, right) => right.total - left.total);
        }

        function authorRowsInRange(data) {
            const start = rangeStartIndex();
            const scopedDates = dates.slice(start);
            const authors = new Map();
            scopedDates.forEach((date) => {
                (data.events[date] || []).forEach((event) => {
                    const existing = authors.get(event.a) || {
                        name: event.a,
                        commits: 0,
                        churn: 0,
                        net: 0,
                        mergeCommits: 0,
                        lastSeen: event.t || date,
                    };
                    existing.commits += 1;
                    existing.churn += event["+"] + event["-"];
                    existing.net += event["+"] - event["-"];
                    existing.mergeCommits += event.p > 1 ? 1 : 0;
                    existing.lastSeen = event.t && event.t > existing.lastSeen ? event.t : existing.lastSeen;
                    authors.set(event.a, existing);
                });
            });
            return Array.from(authors.values()).sort((left, right) => {
                if (right.churn !== left.churn) return right.churn - left.churn;
                return right.commits - left.commits;
            });
        }

        function latestActiveDate(data) {
            const scopedDates = visibleDates().filter((date) => (data.events[date] || []).length > 0);
            return scopedDates.length ? scopedDates[scopedDates.length - 1] : visibleDates()[visibleDates().length - 1] || null;
        }

        function ensureSelectedDate(data) {
            const scoped = new Set(visibleDates());
            if (!selectedDate || !scoped.has(selectedDate) || !(data.events[selectedDate] || []).length) {
                selectedDate = latestActiveDate(data);
            }
        }

        function hotspotRows(data) {
            const baseRows = currentHotspotMode === "files" ? data.files : data.modules;
            if (!currentHotspotQuery.trim()) return baseRows;
            const needle = currentHotspotQuery.trim().toLowerCase();
            return baseRows.filter((row) => row.name.toLowerCase().includes(needle));
        }

        function summaryState(data, summary) {
            const rows = categoryRows(data);
            const authors = authorRowsInRange(data);
            const scopedDates = visibleDates();
            const commits = sum(sliceSeries(data.activity.commits));
            const churn = sum(sliceSeries(data.activity.churn));
            const net = sum(sliceSeries(data.activity.net));
            const activeDays = sliceSeries(data.activity.commits).filter((value) => value > 0).length;
            return {
                rows,
                authors,
                commits,
                churn,
                net,
                activeDays,
                firstVisibleDate: scopedDates[0] || summary.firstDate,
                lastVisibleDate: scopedDates[scopedDates.length - 1] || summary.lastDate,
                dominant: rows[0] || null,
                totalLoc: summary.totalLoc,
                tagCount: summary.tagCount,
                fullActiveDays: summary.activeDays,
            };
        }

        function renderProjectStrip() {
            document.getElementById("project-strip").innerHTML = projects
                .map((name) => `
                    <button
                        class="project-pill ${name === currentProject ? "active" : ""} ${name === dashboard.aggregateProject ? "aggregate" : ""}"
                        data-project="${name}"
                    >${escapeHtml(formatProjectName(name))}</button>
                `)
                .join("");
            document.querySelectorAll(".project-pill").forEach((button) => {
                button.addEventListener("click", () => {
                    currentProject = button.dataset.project;
                    selectedDate = null;
                    render();
                });
            });
        }

        function renderViewTabs() {
            document.getElementById("view-tabs").innerHTML = viewOptions
                .map((view) => `<button class="${view.id === currentView ? "active" : ""}" data-view="${view.id}">${view.label}</button>`)
                .join("");
            document.querySelectorAll("#view-tabs button").forEach((button) => {
                button.addEventListener("click", () => {
                    currentView = button.dataset.view;
                    renderViewState();
                    resizeCharts();
                });
            });
        }

        function renderRangeTabs() {
            document.getElementById("range-tabs").innerHTML = rangeOptions
                .map((range) => `<button class="${range.id === currentRange ? "active" : ""}" data-range="${range.id}">${range.label}</button>`)
                .join("");
            document.querySelectorAll("#range-tabs button").forEach((button) => {
                button.addEventListener("click", () => {
                    currentRange = button.dataset.range;
                    selectedDate = null;
                    render();
                });
            });
        }

        function renderViewState() {
            document.querySelectorAll(".view").forEach((view) => {
                view.classList.toggle("active", view.id === `view-${currentView}`);
            });
        }

        function renderOverview(summary, state, data) {
            const tagPreview = data.tags.length ? escapeHtml(data.tags[data.tags.length - 1].name) : "no tags";
            document.getElementById("overview-copy").innerHTML = `
                <div class="feature-title">
                    <div>
                        <h2>${escapeHtml(formatProjectName(currentProject))}</h2>
                        <p>${escapeHtml(state.firstVisibleDate || "n/a")} to ${escapeHtml(state.lastVisibleDate || "n/a")} · ${formatNumber(state.activeDays)} active day(s) in range · ${formatNumber(summary.authorCount)} author(s) observed overall.</p>
                    </div>
                    <span class="meta-pill">${currentRange === "all" ? "Full history" : `${currentRange} day window`}</span>
                </div>
                <div class="banner-grid">
                    <div class="banner-item">
                        <span>Dominant category</span>
                        <strong>${state.dominant ? escapeHtml(state.dominant.category) : "n/a"}</strong>
                    </div>
                    <div class="banner-item">
                        <span>Latest visible tag</span>
                        <strong>${tagPreview}</strong>
                    </div>
                    <div class="banner-item">
                        <span>Tracked tags</span>
                        <strong>${formatNumber(summary.tagCount)}</strong>
                    </div>
                </div>
            `;

            const metrics = [
                { label: "Total LOC", value: formatNumber(state.totalLoc), tone: "" },
                { label: `${currentRange === "all" ? "History" : currentRange + "d"} Net`, value: formatSigned(state.net), tone: state.net >= 0 ? "positive" : "negative" },
                { label: `${currentRange === "all" ? "History" : currentRange + "d"} Churn`, value: formatNumber(state.churn), tone: "" },
                { label: "Commits In Range", value: formatNumber(state.commits), tone: "" },
                { label: "Active Days In Range", value: formatNumber(state.activeDays), tone: "" },
                { label: "Authors In Range", value: formatNumber(state.authors.length), tone: "" },
            ];

            document.getElementById("summary-metrics").innerHTML = metrics
                .map((metric) => `
                    <div class="card metric-card">
                        <div class="metric-label">${metric.label}</div>
                        <div class="metric-value ${metric.tone}">${metric.value}</div>
                    </div>
                `)
                .join("");
        }

        function baseChartOption(title) {
            return {
                backgroundColor: "transparent",
                animationDuration: 260,
                title: {
                    text: title,
                    left: 4,
                    top: 0,
                    textStyle: {
                        color: "#182230",
                        fontFamily: "Space Grotesk",
                        fontWeight: 700,
                        fontSize: 15,
                    },
                },
                tooltip: {
                    trigger: "axis",
                    backgroundColor: "rgba(255,255,255,0.94)",
                    borderColor: "rgba(24,34,48,0.12)",
                    borderWidth: 1,
                    textStyle: {
                        color: "#182230",
                        fontFamily: "IBM Plex Mono",
                        fontSize: 12,
                    },
                },
                legend: {
                    top: 28,
                    left: 6,
                    textStyle: {
                        color: "#4d5a6c",
                        fontFamily: "IBM Plex Mono",
                        fontSize: 11,
                    },
                },
                grid: {
                    left: 56,
                    right: 24,
                    top: 72,
                    bottom: 46,
                },
                xAxis: {
                    type: "category",
                    axisLine: { lineStyle: { color: "rgba(24,34,48,0.16)" } },
                    axisLabel: {
                        color: "#4d5a6c",
                        fontFamily: "IBM Plex Mono",
                        fontSize: 11,
                    },
                },
                yAxis: {
                    type: "value",
                    splitLine: { lineStyle: { color: "rgba(24,34,48,0.08)" } },
                    axisLine: { show: false },
                    axisLabel: {
                        color: "#4d5a6c",
                        fontFamily: "IBM Plex Mono",
                        fontSize: 11,
                    },
                },
            };
        }

        function renderGrowthChart(data) {
            const chart = echarts.init(document.getElementById("growth-chart"), null, { renderer: "canvas" });
            chartInstances.growth = chart;
            const scopedDates = visibleDates();
            const series = data.categoryList.map((category) => ({
                name: category,
                type: "line",
                smooth: true,
                stack: "growth",
                symbol: "none",
                emphasis: { focus: "series" },
                areaStyle: { opacity: 0.2 },
                lineStyle: { width: 2 },
                itemStyle: { color: data.colors[category] },
                data: sliceSeries(data.categories[category].growth),
            }));
            const option = baseChartOption("Growth silhouette");
            option.xAxis.data = scopedDates;
            option.series = series;
            chart.setOption(option);
        }

        function renderFlowChart(data) {
            const chart = echarts.init(document.getElementById("flow-chart"), null, { renderer: "canvas" });
            chartInstances.flow = chart;
            const scopedDates = visibleDates();
            const churn = sliceSeries(data.activity.churn);
            const net = sliceSeries(data.activity.net);
            const commits = sliceSeries(data.activity.commits);
            const option = baseChartOption("Daily churn and net");
            option.legend.data = ["Churn", "Net", "Commits"];
            option.xAxis.data = scopedDates;
            option.series = [
                {
                    name: "Churn",
                    type: "bar",
                    barMaxWidth: 18,
                    itemStyle: { color: "rgba(197, 82, 61, 0.48)", borderRadius: [6, 6, 0, 0] },
                    data: churn,
                },
                {
                    name: "Net",
                    type: "line",
                    smooth: true,
                    symbol: "none",
                    lineStyle: { width: 2, color: "#1f7a8c" },
                    areaStyle: { opacity: 0.08, color: "#1f7a8c" },
                    data: net,
                },
                {
                    name: "Commits",
                    type: "line",
                    smooth: true,
                    symbol: "none",
                    lineStyle: { width: 1.6, type: "dashed", color: "#d19a2d" },
                    data: commits,
                },
            ];
            chart.setOption(option);
            chart.off("click");
            chart.on("click", (params) => {
                const date = scopedDates[params.dataIndex];
                if (date) {
                    selectedDate = date;
                    renderCommitInspector(data);
                }
            });
        }

        function renderShareChart(data, rows) {
            const chart = echarts.init(document.getElementById("share-chart"), null, { renderer: "canvas" });
            chartInstances.share = chart;
            chart.setOption({
                title: {
                    text: "Footprint share",
                    left: "center",
                    top: 6,
                    textStyle: { color: "#182230", fontFamily: "Space Grotesk", fontWeight: 700, fontSize: 15 },
                },
                tooltip: {
                    trigger: "item",
                    formatter: (params) => `${params.name}<br>${formatNumber(params.value)} LOC`,
                    backgroundColor: "rgba(255,255,255,0.94)",
                    borderColor: "rgba(24,34,48,0.12)",
                    borderWidth: 1,
                    textStyle: { color: "#182230", fontFamily: "IBM Plex Mono", fontSize: 12 },
                },
                series: [
                    {
                        type: "pie",
                        radius: ["46%", "74%"],
                        center: ["50%", "56%"],
                        padAngle: 2,
                        label: {
                            color: "#182230",
                            fontFamily: "IBM Plex Mono",
                            formatter: ({ name, percent }) => `${name}\n${percent.toFixed(1)}%`,
                        },
                        labelLine: { length: 14, length2: 10 },
                        data: rows.map((row) => ({
                            name: row.category,
                            value: row.total,
                            itemStyle: { color: row.color },
                        })),
                    },
                ],
            });
        }

        function renderCategoryTable(rows) {
            document.getElementById("category-table").innerHTML = `
                <thead>
                    <tr>
                        <th>Category</th>
                        <th>Footprint</th>
                        <th>Share</th>
                        <th>Range Net</th>
                    </tr>
                </thead>
                <tbody>
                    ${rows.map((row) => `
                        <tr>
                            <td>
                                <span class="chip colorized" style="background:${row.color}">${escapeHtml(row.category)}</span>
                            </td>
                            <td class="mono">${formatNumber(row.total)}</td>
                            <td class="mono">${(row.share * 100).toFixed(1)}%</td>
                            <td class="mono ${row.rangeNet >= 0 ? "positive" : "negative"}">${formatSigned(row.rangeNet)}</td>
                        </tr>
                    `).join("")}
                </tbody>
            `;
        }

        function renderCommitInspector(data) {
            ensureSelectedDate(data);
            const heading = document.getElementById("commit-heading");
            const commits = selectedDate ? (data.events[selectedDate] || []) : [];
            heading.textContent = selectedDate
                ? `${selectedDate} · ${commits.length} commit(s) in the visible range`
                : "No commit data available in the current range.";
            document.getElementById("commit-list").innerHTML = commits.length
                ? commits.map((event) => {
                    const categoryChips = Object.entries(event.cats || {}).map(([name, stats]) => {
                        const tone = projectData[currentProject].colors?.[name] || "#182230";
                        return `<span class="chip colorized" style="background:${tone}">${escapeHtml(name)} ${formatSigned(stats.a - stats.r)}</span>`;
                    }).join("");
                    return `
                        <div class="commit-entry">
                            <div class="commit-top">
                                <div>
                                    <div class="commit-title">${escapeHtml(event.m)}</div>
                                    <div class="commit-meta">${escapeHtml(event.a)} · <span class="mono">${escapeHtml(event.h)}</span> · ${formatNumber(event.fc)} file(s)</div>
                                </div>
                                <div class="mono ${event["+"] - event["-"] >= 0 ? "positive" : "negative"}">${formatSigned(event["+"] - event["-"])}</div>
                            </div>
                            <div class="chips">${categoryChips}</div>
                            <div class="commit-files">${event.f.length ? `Top files: ${event.f.map(escapeHtml).join(", ")}` : "No file highlights recorded."}</div>
                        </div>
                    `;
                }).join("")
                : `<div class="empty-state">No commits recorded for the selected day in the current range.</div>`;
        }

        function renderHotspotControls() {
            document.getElementById("hotspot-mode").innerHTML = ["modules", "files"]
                .map((mode) => `<button class="${mode === currentHotspotMode ? "active" : ""}" data-mode="${mode}">${mode}</button>`)
                .join("");
            document.querySelectorAll("#hotspot-mode button").forEach((button) => {
                button.addEventListener("click", () => {
                    currentHotspotMode = button.dataset.mode;
                    renderHotspots(currentData());
                });
            });
            const input = document.getElementById("hotspot-search");
            input.value = currentHotspotQuery;
            input.oninput = (event) => {
                currentHotspotQuery = event.target.value;
                renderHotspots(currentData());
            };
        }

        function renderHotspots(data) {
            renderHotspotControls();
            const rows = hotspotRows(data).slice(0, 40);
            const chart = echarts.init(document.getElementById("hotspot-chart"), null, { renderer: "canvas" });
            chartInstances.hotspots = chart;
            chart.setOption({
                title: {
                    text: currentHotspotMode === "files" ? "Top files by churn" : "Top modules by churn",
                    left: 4,
                    top: 0,
                    textStyle: { color: "#182230", fontFamily: "Space Grotesk", fontWeight: 700, fontSize: 15 },
                },
                tooltip: {
                    trigger: "axis",
                    axisPointer: { type: "shadow" },
                    backgroundColor: "rgba(255,255,255,0.94)",
                    borderColor: "rgba(24,34,48,0.12)",
                    borderWidth: 1,
                    textStyle: { color: "#182230", fontFamily: "IBM Plex Mono", fontSize: 12 },
                },
                grid: { left: 190, right: 24, top: 58, bottom: 24 },
                xAxis: {
                    type: "value",
                    splitLine: { lineStyle: { color: "rgba(24,34,48,0.08)" } },
                    axisLabel: { color: "#4d5a6c", fontFamily: "IBM Plex Mono", fontSize: 11 },
                },
                yAxis: {
                    type: "category",
                    data: rows.map((row) => row.name).reverse(),
                    axisLabel: {
                        color: "#182230",
                        fontFamily: "IBM Plex Mono",
                        fontSize: 11,
                        width: 180,
                        overflow: "truncate",
                    },
                },
                series: [
                    {
                        type: "bar",
                        data: rows.map((row) => row.churn).reverse(),
                        itemStyle: {
                            color: "rgba(31,122,140,0.78)",
                            borderRadius: [0, 8, 8, 0],
                        },
                    },
                ],
            });

            document.getElementById("hotspot-table").innerHTML = `
                <thead>
                    <tr>
                        <th>${currentHotspotMode === "files" ? "File" : "Module"}</th>
                        <th>Churn</th>
                        <th>Net</th>
                        <th>Live LOC</th>
                        <th>Volatility</th>
                    </tr>
                </thead>
                <tbody>
                    ${rows.map((row) => `
                        <tr>
                            <td class="mono">${escapeHtml(row.name)}</td>
                            <td class="mono">${formatNumber(row.churn)}</td>
                            <td class="mono ${row.net >= 0 ? "positive" : "negative"}">${formatSigned(row.net)}</td>
                            <td class="mono">${formatNumber(row.loc)}</td>
                            <td class="mono">${row.volatility}</td>
                        </tr>
                    `).join("")}
                </tbody>
            `;
        }

        function renderPeople(data, state) {
            const authors = state.authors.slice(0, 16);
            const chart = echarts.init(document.getElementById("author-chart"), null, { renderer: "canvas" });
            chartInstances.authors = chart;
            chart.setOption({
                title: {
                    text: "Author churn in range",
                    left: 4,
                    top: 0,
                    textStyle: { color: "#182230", fontFamily: "Space Grotesk", fontWeight: 700, fontSize: 15 },
                },
                tooltip: {
                    trigger: "axis",
                    axisPointer: { type: "shadow" },
                    backgroundColor: "rgba(255,255,255,0.94)",
                    borderColor: "rgba(24,34,48,0.12)",
                    borderWidth: 1,
                    textStyle: { color: "#182230", fontFamily: "IBM Plex Mono", fontSize: 12 },
                },
                grid: { left: 160, right: 24, top: 58, bottom: 24 },
                xAxis: {
                    type: "value",
                    splitLine: { lineStyle: { color: "rgba(24,34,48,0.08)" } },
                    axisLabel: { color: "#4d5a6c", fontFamily: "IBM Plex Mono", fontSize: 11 },
                },
                yAxis: {
                    type: "category",
                    data: authors.map((author) => author.name).reverse(),
                    axisLabel: {
                        color: "#182230",
                        fontFamily: "IBM Plex Mono",
                        fontSize: 11,
                        width: 150,
                        overflow: "truncate",
                    },
                },
                series: [
                    {
                        type: "bar",
                        data: authors.map((author) => author.churn).reverse(),
                        itemStyle: {
                            color: "rgba(197,82,61,0.76)",
                            borderRadius: [0, 8, 8, 0],
                        },
                    },
                ],
            });

            document.getElementById("author-table").innerHTML = `
                <thead>
                    <tr>
                        <th>Author</th>
                        <th>Commits</th>
                        <th>Churn</th>
                        <th>Net</th>
                        <th>Merges</th>
                    </tr>
                </thead>
                <tbody>
                    ${state.authors.slice(0, 30).map((author) => `
                        <tr>
                            <td class="mono">${escapeHtml(author.name)}</td>
                            <td class="mono">${formatNumber(author.commits)}</td>
                            <td class="mono">${formatNumber(author.churn)}</td>
                            <td class="mono ${author.net >= 0 ? "positive" : "negative"}">${formatSigned(author.net)}</td>
                            <td class="mono">${formatNumber(author.mergeCommits)}</td>
                        </tr>
                    `).join("")}
                </tbody>
            `;

            document.getElementById("ownership-table").innerHTML = `
                <thead>
                    <tr>
                        <th>Module</th>
                        <th>Owner</th>
                        <th>Share</th>
                        <th>Commits</th>
                        <th>Churn</th>
                    </tr>
                </thead>
                <tbody>
                    ${data.owners.slice(0, 24).map((owner) => `
                        <tr>
                            <td class="mono">${escapeHtml(owner.module)}</td>
                            <td>${escapeHtml(owner.author)}</td>
                            <td class="mono">${(owner.share * 100).toFixed(1)}%</td>
                            <td class="mono">${formatNumber(owner.commits)}</td>
                            <td class="mono">${formatNumber(owner.churn)}</td>
                        </tr>
                    `).join("")}
                </tbody>
            `;
        }

        function renderTopology(data) {
            const chart = echarts.init(document.getElementById("cochange-chart"), null, { renderer: "canvas" });
            chartInstances.cochange = chart;
            const hasGraph = data.cochange.nodes.length > 0;
            chart.setOption({
                title: {
                    text: hasGraph ? "Co-change network" : "Co-change network unavailable",
                    left: 4,
                    top: 0,
                    textStyle: { color: "#182230", fontFamily: "Space Grotesk", fontWeight: 700, fontSize: 15 },
                },
                tooltip: {
                    formatter: (params) => {
                        if (params.dataType === "edge") {
                            return `${params.data.source} ↔ ${params.data.target}<br>${params.data.value} shared commit(s)`;
                        }
                        return `${params.data.name}<br>${formatNumber(params.data.value)} churn`;
                    },
                    backgroundColor: "rgba(255,255,255,0.94)",
                    borderColor: "rgba(24,34,48,0.12)",
                    borderWidth: 1,
                    textStyle: { color: "#182230", fontFamily: "IBM Plex Mono", fontSize: 12 },
                },
                series: hasGraph ? [{
                    type: "graph",
                    layout: "force",
                    roam: true,
                    draggable: true,
                    label: {
                        show: true,
                        color: "#182230",
                        fontFamily: "IBM Plex Mono",
                        fontSize: 11,
                    },
                    force: {
                        repulsion: 180,
                        edgeLength: [50, 150],
                    },
                    data: data.cochange.nodes.map((node) => ({
                        ...node,
                        itemStyle: { color: "rgba(31,122,140,0.78)" },
                    })),
                    links: data.cochange.edges,
                    lineStyle: {
                        color: "rgba(24,34,48,0.18)",
                        curveness: 0.16,
                    },
                }] : [],
            });

            const tags = data.tags.slice(-10).reverse();
            document.getElementById("tag-list").innerHTML = tags.length
                ? tags.map((tag) => `
                    <div class="tag-pill">
                        <span class="tag-dot"></span>
                        <span>${escapeHtml(tag.name)}</span>
                        <span class="dim mono">${escapeHtml(tag.date.split("T")[0])}</span>
                    </div>
                `).join("")
                : `<div class="empty-state">No tags found for this repository.</div>`;
        }

        function resizeCharts() {
            Object.values(chartInstances).forEach((chart) => {
                if (chart && !chart.isDisposed()) chart.resize();
            });
        }

        function disposeCharts() {
            Object.keys(chartInstances).forEach((key) => {
                if (chartInstances[key] && !chartInstances[key].isDisposed()) {
                    chartInstances[key].dispose();
                }
                delete chartInstances[key];
            });
        }

        function render() {
            const data = currentData();
            const summary = currentSummary();
            ensureSelectedDate(data);
            const state = summaryState(data, summary);

            renderProjectStrip();
            renderViewTabs();
            renderRangeTabs();
            renderViewState();
            renderOverview(summary, state, data);

            disposeCharts();
            renderGrowthChart(data);
            renderFlowChart(data);
            renderShareChart(data, state.rows);
            renderCategoryTable(state.rows);
            renderCommitInspector(data);
            renderHotspots(data);
            renderPeople(data, state);
            renderTopology(data);
            resizeCharts();
        }

        window.addEventListener("DOMContentLoaded", () => {
            render();
            window.addEventListener("resize", resizeCharts);
        });
    </script>
</body>
</html>
"""

    html = (
        html_template
        .replace("__PAYLOAD__", json.dumps(dashboard_payload))
        .replace("__GENERATED_AT__", dashboard_payload["generatedAt"])
    )

    wrote = write_text_if_changed(output_path, html)
    if wrote:
        print(f"Rich report generated at {output_path.resolve()}")
    else:
        print(f"Velocity report unchanged at {output_path.resolve()}")


def build(
    output: Path = typer.Option(
        DEFAULT_OUTPUT, "--output", "-o", help="Destination HTML path"
    ),
    project: Optional[List[str]] = typer.Option(
        None,
        "--project",
        "-p",
        help="Limit to specific project names (default: all registered projects)",
    ),
    exclude: Optional[List[str]] = typer.Option(
        None,
        "--exclude",
        "-x",
        help="Exclude project names from the dashboard (repeatable).",
    ),
    aggregate: bool = typer.Option(
        True,
        "--aggregate/--no-aggregate",
        help="Include an aggregated view that stacks repositories together.",
    ),
) -> None:
    """Render the velocity dashboard for the configured repositories."""
    selected_specs: Dict[str, ProjectProfile] = PROJECT_SPECS
    if project:
        requested = [name.strip() for name in project if name.strip()]
        if not requested:
            raise typer.BadParameter(
                "At least one non-empty --project value is required."
            )
        missing = [name for name in requested if name not in PROJECT_SPECS]
        if missing:
            raise typer.BadParameter(
                f"Unknown project(s): {', '.join(sorted(missing))}"
            )
        selected_specs = {name: PROJECT_SPECS[name] for name in requested}

    if exclude:
        excluded = [name.strip() for name in exclude if name.strip()]
        if not excluded:
            raise typer.BadParameter(
                "At least one non-empty --exclude value is required."
            )
        missing = [name for name in excluded if name not in PROJECT_SPECS]
        if missing:
            raise typer.BadParameter(
                f"Unknown project(s): {', '.join(sorted(missing))}"
            )
        for name in excluded:
            selected_specs.pop(name, None)

    if not selected_specs:
        raise typer.BadParameter("No projects available to analyse.")

    stats = analyze_projects(selected_specs)
    if not stats:
        typer.secho(
            "No repositories produced git history; nothing to render.",
            fg=typer.colors.YELLOW,
        )
        return
    if aggregate and len(stats) > 1:
        aggregate_stats = _aggregate_stats(stats)
        aggregate_spec = _aggregate_spec(list(stats.keys()))
        stats = {AGGREGATE_PROJECT: aggregate_stats, **stats}
        selected_specs = {AGGREGATE_PROJECT: aggregate_spec, **selected_specs}
    generate_html(stats, selected_specs, output)


if __name__ == "__main__":
    typer.run(build)
