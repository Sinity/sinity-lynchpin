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
import re
from pathlib import Path
from typing import Callable, Dict, List, Optional
from dataclasses import dataclass, field

import typer

from ..core.io import write_text_if_changed
DEFAULT_OUTPUT = Path("artefacts/meta/velocity/velocity.html")
AGGREGATE_PROJECT = "all-projects"

SKIP_EXTENSIONS = {"lock", "svg", "map", "min.js", "png", "jpg", "pdf", "gif", "ico", "woff", "woff2", "ttf", "eot"}
SKIP_PATHS = {"reports/", "pipelines/artefacts/", "artefacts/", "data/"}

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
    if parts[0] in {"src", "crates", "modules", "module", "pipelines", "lynchpin", "apps", "app", "bin", "lib"}:
        if len(parts) > 1:
            return f"{parts[0]}/{parts[1]}"
    return parts[0]


# === Per-project classifiers ===

def classify_sinex(filename: str) -> Optional[str]:
    """sinex: Rust project with src/tests/docs/config/generated split."""
    if _skip_common(filename):
        return None

    if filename.startswith(".sqlx/"):
        return "generated"

    basename = Path(filename).name.lower()
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    # Tests
    if "/tests/" in filename or "/test/" in filename:
        return "tests"
    if filename.startswith(("tests/", "test/")):
        return "tests"
    if basename.endswith("_test.rs") or basename.endswith("_tests.rs"):
        return "tests"

    # Docs
    if "/docs/" in filename or filename.startswith("docs/"):
        return "docs"
    if ext in {"md", "mdx", "rst", "txt"}:
        return "docs"

    # Config
    if ext in {"nix", "toml", "yaml", "yml"}:
        return "config"
    if basename in {"justfile", ".gitignore", ".envrc"}:
        return "config"

    return "src"


def classify_sinnix(filename: str) -> Optional[str]:
    """sinnix: NixOS config with module/host/flake/docs split."""
    if _skip_common(filename):
        return None

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    # Docs
    if "/docs/" in filename or filename.startswith("docs/"):
        return "docs"
    if ext in {"md", "mdx", "rst", "txt"}:
        return "docs"

    # Host-specific config
    if filename.startswith("host/") or "/host/" in filename:
        return "host"

    # Flake infrastructure
    if filename.startswith("flake/") or filename in {"flake.nix", "flake.lock"}:
        return "flake"

    # Modules (domain config)
    if filename.startswith("module/") or "/module/" in filename:
        return "module"

    return "other"


def classify_sinity_analysis(filename: str) -> Optional[str]:
    """sinity-lynchpin: Python analysis repo with pipelines/docs/config split."""
    if _skip_common(filename):
        return None

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    basename = Path(filename).name.lower()

    # Docs
    if "/docs/" in filename or filename.startswith("docs/"):
        return "docs"
    if ext in {"md", "mdx", "rst", "txt"}:
        return "docs"

    # Pipelines (the main code)
    if filename.startswith("pipelines/"):
        return "pipelines"

    # Config
    if ext in {"nix", "toml", "yaml", "yml", "json"}:
        return "config"
    if basename in {"justfile", ".gitignore", ".envrc"}:
        return "config"

    return "other"


def classify_knowledgebase(filename: str) -> Optional[str]:
    """knowledgebase: Mostly docs with some config."""
    if _skip_common(filename):
        return None

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    basename = Path(filename).name.lower()

    # Config
    if ext in {"nix", "toml", "yaml", "yml", "json"}:
        return "config"
    if basename in {"justfile", ".gitignore", ".envrc"}:
        return "config"

    # Everything else is docs/content
    return "docs"


def classify_rust_simple(filename: str) -> Optional[str]:
    """Simple Rust project: src/tests/docs/config."""
    if _skip_common(filename):
        return None

    basename = Path(filename).name.lower()
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    # Tests
    if "/tests/" in filename or "/test/" in filename:
        return "tests"
    if filename.startswith(("tests/", "test/")):
        return "tests"
    if basename.endswith("_test.rs") or basename.endswith("_tests.rs"):
        return "tests"
    if re.match(r"(test_.*|.*_test|.*_tests)\.", basename):
        return "tests"

    # Docs
    if "/docs/" in filename or filename.startswith("docs/"):
        return "docs"
    if ext in {"md", "mdx", "rst", "txt"}:
        return "docs"

    # Config
    if ext in {"nix", "toml", "yaml", "yml"}:
        return "config"
    if basename in {"justfile", ".gitignore", ".envrc"}:
        return "config"

    return "src"


# === Project registry ===

PROJECT_SPECS: Dict[str, dict] = {
    "sinex": {
        "path": "/realm/project/sinex",
        "classify": classify_sinex,
        "categories": ["src", "tests", "docs", "config", "generated"],
        "colors": {"src": "#5470c6", "tests": "#91cc75", "docs": "#fac858", "config": "#ee6666", "generated": "#73c0de"},
    },
    "polylogue": {
        "path": "/realm/project/polylogue",
        "classify": classify_rust_simple,
        "categories": ["src", "tests", "docs", "config"],
        "colors": {"src": "#5470c6", "tests": "#91cc75", "docs": "#fac858", "config": "#ee6666"},
    },
    "intercept-bounce": {
        "path": "/realm/project/intercept-bounce",
        "classify": classify_rust_simple,
        "categories": ["src", "tests", "docs", "config"],
        "colors": {"src": "#5470c6", "tests": "#91cc75", "docs": "#fac858", "config": "#ee6666"},
    },
    "scribe-tap": {
        "path": "/realm/project/scribe-tap",
        "classify": classify_rust_simple,
        "categories": ["src", "tests", "docs", "config"],
        "colors": {"src": "#5470c6", "tests": "#91cc75", "docs": "#fac858", "config": "#ee6666"},
    },
    "sinevec": {
        "path": "/realm/project/sinevec",
        "classify": classify_rust_simple,
        "categories": ["src", "tests", "docs", "config"],
        "colors": {"src": "#5470c6", "tests": "#91cc75", "docs": "#fac858", "config": "#ee6666"},
    },
    "pwrank": {
        "path": "/realm/project/pwrank",
        "classify": classify_rust_simple,
        "categories": ["src", "tests", "docs", "config"],
        "colors": {"src": "#5470c6", "tests": "#91cc75", "docs": "#fac858", "config": "#ee6666"},
    },
    "knowledge-extract": {
        "path": "/realm/project/knowledge-extract",
        "classify": classify_rust_simple,
        "categories": ["src", "tests", "docs", "config"],
        "colors": {"src": "#5470c6", "tests": "#91cc75", "docs": "#fac858", "config": "#ee6666"},
    },
    "sinnix": {
        "path": "/realm/project/sinnix",
        "classify": classify_sinnix,
        "categories": ["module", "host", "flake", "docs", "other"],
        "colors": {"module": "#5470c6", "host": "#91cc75", "flake": "#fac858", "docs": "#ee6666", "other": "#73c0de"},
    },
    "sinity-lynchpin": {
        "path": "/realm/project/sinity-lynchpin",
        "classify": classify_sinity_analysis,
        "categories": ["pipelines", "docs", "config", "other"],
        "colors": {"pipelines": "#5470c6", "docs": "#fac858", "config": "#ee6666", "other": "#73c0de"},
    },
}


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
    cochange: Dict[tuple, int] = field(default_factory=dict)
    tags: List[dict] = field(default_factory=list)


def run_git_log(path: Path) -> List[str]:
    sep = "|||"
    fmt = f"%h{sep}%ad{sep}%an{sep}%s{sep}%P"

    cmd = [
        "git", "log",
        "--all",
        "--date=iso-strict-local",
        f"--pretty=format:COMMIT:{fmt}",
        "--numstat"
    ]
    try:
        subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=path, check=True, capture_output=True)
        result = subprocess.run(cmd, cwd=path, capture_output=True, text=True, check=True, errors="replace")
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
        result = subprocess.run(cmd, cwd=path, capture_output=True, text=True, check=True, errors="replace")
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

def parse_log(lines: List[str], project_name: str, classify_fn: Callable[[str], Optional[str]]) -> ProjectStats:
    stats = ProjectStats(name=project_name)

    current_commit: Optional[CommitEvent] = None
    current_files_buffer = []
    current_file_scores = {}
    current_modules = set()
    current_files_count = 0

    def flush_commit():
        nonlocal current_commit, current_files_buffer, current_file_scores, current_modules, current_files_count
        if current_commit:
            if current_file_scores:
                sorted_files = sorted(current_file_scores.items(), key=lambda item: item[1], reverse=True)
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
                h, d_raw, auth, msg, parents_raw = parts[0], parts[1], parts[2], parts[3], parts[4]
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
                current_commit = CommitEvent(hash=h, date=date_str, author=auth, message=msg, timestamp=d_raw)
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
            current_file_scores[filename] = current_file_scores.get(filename, 0) + add_val + rem_val

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


def analyze_projects(project_specs: Dict[str, dict]) -> Dict[str, ProjectStats]:
    all_stats = {}
    for name, spec in project_specs.items():
        path = Path(spec["path"])
        if not path.exists():
            print(f"Path not found: {path}, skipping...", file=sys.stderr)
            continue

        print(f"Analyzing {name}...")
        lines = run_git_log(path)
        classify_fn = spec["classify"]
        stats = parse_log(lines, name, classify_fn)
        stats.tags = run_git_tags(path)
        all_stats[name] = stats
    return all_stats


def _aggregate_spec(project_names: List[str]) -> dict:
    categories = sorted(project_names)
    colors = {
        name: AGGREGATE_PALETTE[i % len(AGGREGATE_PALETTE)]
        for i, name in enumerate(categories)
    }
    return {
        "path": "(aggregate)",
        "classify": None,
        "categories": categories,
        "colors": colors,
    }


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
    return aggregate


def generate_html(all_stats: Dict[str, ProjectStats], project_specs: Dict[str, dict], output_path: Path):
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
        categories = spec["categories"]
        colors = spec["colors"]

        project_data = {
            "categories": {},
            "categoryList": categories,
            "colors": colors,
            "events": {},
            "files": [],
            "modules": [],
            "owners": [],
            "cochange": {"nodes": [], "edges": []},
            "tags": []
        }

        # Initialize cumulative counters per category
        cumulative = {cat: 0 for cat in categories}

        for cat in categories:
            project_data["categories"][cat] = {
                "growth": [],
                "churn": [],
                "net": []
            }

        for d in sorted_dates:
            day_stats = stats.daily.get(d)

            for cat in categories:
                if day_stats and cat in day_stats.by_category:
                    cat_data = day_stats.by_category[cat]
                    cumulative[cat] += cat_data.net
                    if cumulative[cat] < 0:
                        cumulative[cat] = 0
                    project_data["categories"][cat]["churn"].append(cat_data.added + cat_data.removed)
                    project_data["categories"][cat]["net"].append(cat_data.net)
                else:
                    project_data["categories"][cat]["churn"].append(0)
                    project_data["categories"][cat]["net"].append(0)

                project_data["categories"][cat]["growth"].append(cumulative[cat])

            # Events for inspector
            if day_stats and day_stats.commits:
                ev_list = []
                for c in day_stats.commits:
                    cat_breakdown = {k: {"a": v.added, "r": v.removed} for k, v in c.by_category.items()}
                    ev_list.append({
                        "h": c.hash,
                        "a": c.author,
                        "m": c.message,
                        "+": c.added,
                        "-": c.removed,
                        "t": c.timestamp,
                        "p": c.parents,
                        "cats": cat_breakdown,
                        "f": c.top_files,
                        "fc": c.files_count
                    })
                ev_list.sort(key=lambda x: x["+"] + x["-"], reverse=True)
                project_data["events"][d] = ev_list

        file_rows = []
        for filename, fstats in stats.file_stats.items():
            churn = fstats.added + fstats.removed
            net = fstats.added - fstats.removed
            loc = max(0, net)
            volatility = churn / max(1, loc)
            file_rows.append({
                "name": filename,
                "churn": churn,
                "net": net,
                "loc": loc,
                "volatility": round(volatility, 3),
            })
        file_rows.sort(key=lambda row: row["churn"], reverse=True)
        project_data["files"] = file_rows[:200]

        module_rows = []
        for module, mstats in stats.module_stats.items():
            churn = mstats.added + mstats.removed
            net = mstats.added - mstats.removed
            loc = max(0, net)
            volatility = churn / max(1, loc)
            module_rows.append({
                "name": module,
                "churn": churn,
                "net": net,
                "loc": loc,
                "volatility": round(volatility, 3),
            })
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
            owners.append({
                "module": module,
                "author": top_author[0],
                "share": round(top_author[1] / total, 3),
                "commits": total,
                "churn": churn,
            })
        owners.sort(key=lambda row: (row["share"], row["churn"]), reverse=True)
        project_data["owners"] = owners[:200]

        top_modules = [row["name"] for row in module_rows[:20]]
        module_weights = {row["name"]: row["churn"] for row in module_rows}
        nodes = []
        for module_name in top_modules:
            weight = module_weights.get(module_name, 0)
            nodes.append({
                "name": module_name,
                "value": weight,
                "symbolSize": max(8, min(40, 8 + (weight ** 0.5))),
            })
        edges = []
        for (left, right), weight in stats.cochange.items():
            if left in top_modules and right in top_modules:
                edges.append({
                    "source": left,
                    "target": right,
                    "value": weight,
                    "lineStyle": {"width": max(1, min(6, weight / 2))}
                })
        edges.sort(key=lambda row: row["value"], reverse=True)
        project_data["cochange"] = {"nodes": nodes, "edges": edges[:60]}
        project_data["tags"] = stats.tags

        js_projects[name] = project_data

    # Compute summary stats for each project
    project_summaries = {}
    for name, stats in all_stats.items():
        spec = project_specs[name]
        categories = spec["categories"]
        total_loc = sum(js_projects[name]["categories"][cat]["growth"][-1] for cat in categories) if sorted_dates else 0
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
        }

    html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Velocity Observatory</title>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/mermaid@10.4.0/dist/mermaid.min.js"></script>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&family=Outfit:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        *, *::before, *::after {{ box-sizing: border-box; }}

        :root {{
            --void: #08090c;
            --abyss: #0d0f14;
            --deep: #12151c;
            --surface: #1a1e28;
            --elevated: #232836;
            --border: #2d3344;
            --border-subtle: #252a38;
            --text: #e4e8f1;
            --text-secondary: #9ba3b8;
            --text-muted: #6b7280;
            --phosphor: #4ade80;
            --phosphor-dim: #22c55e;
            --amber: #fbbf24;
            --amber-dim: #d97706;
            --rose: #fb7185;
            --cyan: #22d3ee;
            --violet: #a78bfa;
            --font-mono: 'JetBrains Mono', 'SF Mono', 'Fira Code', monospace;
            --font-display: 'Outfit', system-ui, sans-serif;
            --ui-scale: 2;
        }}

        @keyframes fadeIn {{
            from {{ opacity: 0; transform: translateY(8px); }}
            to {{ opacity: 1; transform: translateY(0); }}
        }}

        @keyframes pulse {{
            0%, 100% {{ opacity: 1; }}
            50% {{ opacity: 0.5; }}
        }}

        @keyframes slideIn {{
            from {{ opacity: 0; transform: translateX(-12px); }}
            to {{ opacity: 1; transform: translateX(0); }}
        }}

        @keyframes glow {{
            0%, 100% {{ box-shadow: 0 0 20px rgba(74, 222, 128, 0.1); }}
            50% {{ box-shadow: 0 0 30px rgba(74, 222, 128, 0.2); }}
        }}

        html, body {{
            margin: 0;
            padding: 0;
            height: 100%;
            overflow: auto;
        }}

        body {{
            font-family: var(--font-display);
            background: var(--void);
            color: var(--text);
            display: flex;
            flex-direction: column;
            font-size: calc(15px * var(--ui-scale));
            line-height: 1.4;
            text-rendering: geometricPrecision;
            -webkit-font-smoothing: antialiased;
        }}

        body::before {{
            content: '';
            position: fixed;
            inset: 0;
            background: radial-gradient(circle at top right, rgba(34, 211, 238, 0.05), transparent 60%),
                        radial-gradient(circle at 10% 20%, rgba(74, 222, 128, 0.04), transparent 55%);
            pointer-events: none;
            z-index: 0;
        }}

        /* Header */
        .header {{
            background: linear-gradient(180deg, var(--deep) 0%, var(--abyss) 100%);
            border-bottom: 1px solid var(--border-subtle);
            padding: 0 calc(32px * var(--ui-scale));
            height: calc(72px * var(--ui-scale));
            display: flex;
            align-items: center;
            justify-content: space-between;
            flex-shrink: 0;
            position: relative;
        }}

        .header::after {{
            content: '';
            position: absolute;
            bottom: 0;
            left: 0;
            right: 0;
            height: 1px;
            background: linear-gradient(90deg, transparent, var(--phosphor-dim), transparent);
            opacity: 0.3;
        }}

        .logo {{
            display: flex;
            align-items: center;
            gap: calc(12px * var(--ui-scale));
        }}

        .logo-icon {{
            width: calc(40px * var(--ui-scale));
            height: calc(40px * var(--ui-scale));
            background: linear-gradient(135deg, var(--phosphor) 0%, var(--cyan) 100%);
            border-radius: calc(10px * var(--ui-scale));
            display: flex;
            align-items: center;
            justify-content: center;
            font-family: var(--font-mono);
            font-weight: 600;
            font-size: calc(18px * var(--ui-scale));
            color: var(--void);
            animation: glow 3s ease-in-out infinite;
        }}

        .logo-text {{
            font-weight: 600;
            font-size: calc(24px * var(--ui-scale));
            letter-spacing: -0.02em;
            background: linear-gradient(135deg, var(--text) 0%, var(--text-secondary) 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }}

        .logo-sub {{
            font-size: calc(14px * var(--ui-scale));
            font-family: var(--font-mono);
            color: var(--text-muted);
            letter-spacing: 0.05em;
            text-transform: uppercase;
        }}

        .header-meta {{
            display: flex;
            align-items: center;
            gap: calc(24px * var(--ui-scale));
        }}

        .timestamp {{
            font-family: var(--font-mono);
            font-size: calc(15px * var(--ui-scale));
            color: var(--text-muted);
            display: flex;
            align-items: center;
            gap: calc(8px * var(--ui-scale));
        }}

        .timestamp::before {{
            content: '';
            width: calc(6px * var(--ui-scale));
            height: calc(6px * var(--ui-scale));
            background: var(--phosphor);
            border-radius: 50%;
            animation: pulse 2s ease-in-out infinite;
        }}

        /* Project Selector */
        .project-selector {{
            position: relative;
        }}

        .project-selector select {{
            appearance: none;
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: calc(8px * var(--ui-scale));
            padding: calc(12px * var(--ui-scale)) calc(48px * var(--ui-scale)) calc(12px * var(--ui-scale)) calc(20px * var(--ui-scale));
            font-family: var(--font-mono);
            font-size: calc(16px * var(--ui-scale));
            font-weight: 500;
            color: var(--text);
            cursor: pointer;
            transition: all 0.2s ease;
            min-width: calc(220px * var(--ui-scale));
        }}

        .project-selector select:hover {{
            border-color: var(--phosphor-dim);
            background: var(--elevated);
        }}

        .project-selector select:focus {{
            outline: none;
            border-color: var(--phosphor);
            box-shadow: 0 0 0 3px rgba(74, 222, 128, 0.1);
        }}

        .project-selector::after {{
            content: 'v';
            position: absolute;
            right: calc(18px * var(--ui-scale));
            top: 50%;
            transform: translateY(-50%);
            color: var(--text-muted);
            pointer-events: none;
            font-size: calc(16px * var(--ui-scale));
        }}

        .scale-selector {{
            position: relative;
        }}

        .scale-selector select {{
            appearance: none;
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: calc(8px * var(--ui-scale));
            padding: calc(12px * var(--ui-scale)) calc(44px * var(--ui-scale)) calc(12px * var(--ui-scale)) calc(16px * var(--ui-scale));
            font-family: var(--font-mono);
            font-size: calc(14px * var(--ui-scale));
            font-weight: 500;
            color: var(--text);
            cursor: pointer;
            transition: all 0.2s ease;
            min-width: calc(110px * var(--ui-scale));
        }}

        .scale-selector select:hover {{
            border-color: var(--phosphor-dim);
            background: var(--elevated);
        }}

        .scale-selector select:focus {{
            outline: none;
            border-color: var(--phosphor);
            box-shadow: 0 0 0 3px rgba(74, 222, 128, 0.1);
        }}

        .scale-selector::after {{
            content: 'v';
            position: absolute;
            right: calc(16px * var(--ui-scale));
            top: 50%;
            transform: translateY(-50%);
            color: var(--text-muted);
            pointer-events: none;
            font-size: calc(14px * var(--ui-scale));
        }}

        /* Main Layout */
        .main {{
            display: flex;
            flex: 1;
            overflow: hidden;
            background: var(--abyss);
        }}

        .section-nav {{
            display: flex;
            flex-wrap: wrap;
            gap: calc(10px * var(--ui-scale));
            padding: calc(12px * var(--ui-scale)) calc(28px * var(--ui-scale));
            background: var(--deep);
            border-bottom: 1px solid var(--border-subtle);
            position: sticky;
            top: 0;
            z-index: 5;
        }}

        .section-nav a {{
            text-decoration: none;
            font-family: var(--font-mono);
            font-size: calc(12px * var(--ui-scale));
            text-transform: uppercase;
            letter-spacing: 0.08em;
            color: var(--text-muted);
            border: 1px solid transparent;
            padding: calc(6px * var(--ui-scale)) calc(12px * var(--ui-scale));
            border-radius: calc(999px * var(--ui-scale));
            transition: all 0.2s ease;
        }}

        .section-nav a:hover {{
            color: var(--text);
            border-color: var(--border);
            background: var(--surface);
        }}

        .section {{
            padding: calc(28px * var(--ui-scale)) calc(28px * var(--ui-scale)) calc(36px * var(--ui-scale));
            border-bottom: 1px solid var(--border-subtle);
            background: var(--abyss);
            position: relative;
        }}

        .section-header {{
            display: flex;
            flex-direction: column;
            gap: calc(6px * var(--ui-scale));
            margin-bottom: calc(20px * var(--ui-scale));
        }}

        .section-title {{
            font-size: calc(20px * var(--ui-scale));
            font-weight: 600;
            letter-spacing: -0.01em;
            color: var(--text);
        }}

        .section-subtitle {{
            font-size: calc(13px * var(--ui-scale));
            font-family: var(--font-mono);
            color: var(--text-muted);
            letter-spacing: 0.03em;
        }}

        .section-grid {{
            display: grid;
            grid-template-columns: repeat(12, minmax(0, 1fr));
            gap: calc(16px * var(--ui-scale));
        }}

        .panel {{
            background: var(--deep);
            border: 1px solid var(--border-subtle);
            border-radius: calc(16px * var(--ui-scale));
            padding: calc(16px * var(--ui-scale));
            display: flex;
            flex-direction: column;
            gap: calc(12px * var(--ui-scale));
            min-width: 0;
        }}

        .panel-title {{
            font-size: calc(14px * var(--ui-scale));
            font-family: var(--font-mono);
            text-transform: uppercase;
            letter-spacing: 0.08em;
            color: var(--text-muted);
        }}

        .panel-subtitle {{
            font-size: calc(12px * var(--ui-scale));
            font-family: var(--font-mono);
            color: var(--text-secondary);
        }}

        .panel-chart {{
            flex: 1;
            min-height: calc(220px * var(--ui-scale));
        }}

        .panel.span-12 {{ grid-column: span 12; }}
        .panel.span-8 {{ grid-column: span 8; }}
        .panel.span-7 {{ grid-column: span 7; }}
        .panel.span-6 {{ grid-column: span 6; }}
        .panel.span-5 {{ grid-column: span 5; }}
        .panel.span-4 {{ grid-column: span 4; }}

        .insight-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(calc(160px * var(--ui-scale)), 1fr));
            gap: calc(12px * var(--ui-scale));
        }}

        .insight-card {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: calc(12px * var(--ui-scale));
            padding: calc(12px * var(--ui-scale));
            display: flex;
            flex-direction: column;
            gap: calc(6px * var(--ui-scale));
        }}

        .insight-label {{
            font-family: var(--font-mono);
            font-size: calc(11px * var(--ui-scale));
            text-transform: uppercase;
            letter-spacing: 0.08em;
            color: var(--text-muted);
        }}

        .insight-value {{
            font-family: var(--font-mono);
            font-size: calc(22px * var(--ui-scale));
            font-weight: 600;
            color: var(--text);
        }}

        .insight-meta {{
            font-family: var(--font-mono);
            font-size: calc(11px * var(--ui-scale));
            color: var(--text-muted);
        }}

        .data-table {{
            width: 100%;
            border-collapse: collapse;
            font-family: var(--font-mono);
            font-size: calc(12px * var(--ui-scale));
        }}

        .data-table thead th {{
            text-align: left;
            color: var(--text-muted);
            padding: calc(8px * var(--ui-scale)) calc(6px * var(--ui-scale));
            text-transform: uppercase;
            letter-spacing: 0.06em;
            border-bottom: 1px solid var(--border-subtle);
        }}

        .data-table tbody td {{
            padding: calc(8px * var(--ui-scale)) calc(6px * var(--ui-scale));
            border-bottom: 1px solid var(--border-subtle);
            color: var(--text-secondary);
        }}

        .data-table tbody tr:hover td {{
            color: var(--text);
            background: var(--surface);
        }}

        .mermaid-card {{
            background: var(--deep);
            border: 1px solid var(--border-subtle);
            border-radius: calc(16px * var(--ui-scale));
            padding: calc(18px * var(--ui-scale));
            overflow: auto;
        }}

        .mermaid {{
            font-family: var(--font-mono);
            color: var(--text-secondary);
        }}

        /* Stats Bar */
        .stats-bar {{
            display: flex;
            gap: calc(20px * var(--ui-scale));
            padding: calc(20px * var(--ui-scale)) calc(28px * var(--ui-scale));
            background: var(--deep);
            border-bottom: 1px solid var(--border-subtle);
            overflow-x: auto;
            flex-shrink: 0;
        }}

        .filter-bar {{
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: calc(10px * var(--ui-scale));
            padding: calc(12px * var(--ui-scale)) calc(28px * var(--ui-scale));
            background: var(--deep);
            border-bottom: 1px solid var(--border-subtle);
        }}

        .filter-label {{
            font-family: var(--font-mono);
            font-size: calc(11px * var(--ui-scale));
            text-transform: uppercase;
            letter-spacing: 0.08em;
            color: var(--text-muted);
            margin-right: calc(6px * var(--ui-scale));
        }}

        .filter-pill {{
            display: flex;
            align-items: center;
            gap: calc(6px * var(--ui-scale));
            padding: calc(6px * var(--ui-scale)) calc(12px * var(--ui-scale));
            border-radius: calc(999px * var(--ui-scale));
            border: 1px solid var(--border);
            background: var(--surface);
            font-family: var(--font-mono);
            font-size: calc(12px * var(--ui-scale));
            color: var(--text-secondary);
            cursor: pointer;
            transition: all 0.2s ease;
        }}

        .filter-pill.active {{
            color: var(--text);
            border-color: var(--phosphor-dim);
            box-shadow: 0 0 calc(16px * var(--ui-scale)) rgba(74, 222, 128, 0.15);
        }}

        .filter-pill.inactive {{
            opacity: 0.5;
            text-decoration: line-through;
        }}

        .filter-pill::before {{
            content: '';
            width: calc(8px * var(--ui-scale));
            height: calc(8px * var(--ui-scale));
            border-radius: 50%;
            background: var(--pill-color, var(--text-muted));
        }}

        .filter-pill span {{
            font-size: calc(10px * var(--ui-scale));
            color: var(--text-muted);
        }}

        .stat-card {{
            background: linear-gradient(135deg, var(--surface) 0%, var(--elevated) 100%);
            border: 1px solid var(--border);
            border-radius: calc(14px * var(--ui-scale));
            padding: calc(20px * var(--ui-scale)) calc(24px * var(--ui-scale));
            min-width: calc(180px * var(--ui-scale));
            position: relative;
            overflow: hidden;
            transition: all 0.3s ease;
            animation: fadeIn 0.5s ease backwards;
        }}

        .stat-card:nth-child(1) {{ animation-delay: 0.05s; }}
        .stat-card:nth-child(2) {{ animation-delay: 0.1s; }}
        .stat-card:nth-child(3) {{ animation-delay: 0.15s; }}
        .stat-card:nth-child(4) {{ animation-delay: 0.2s; }}
        .stat-card:nth-child(5) {{ animation-delay: 0.25s; }}

        .stat-card:hover {{
            border-color: var(--phosphor-dim);
            transform: translateY(-2px);
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
        }}

        .stat-card::before {{
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 2px;
            background: linear-gradient(90deg, var(--phosphor), var(--cyan));
            opacity: 0;
            transition: opacity 0.3s ease;
        }}

        .stat-card:hover::before {{
            opacity: 1;
        }}

        .stat-label {{
            font-size: calc(13px * var(--ui-scale));
            font-family: var(--font-mono);
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.08em;
            margin-bottom: calc(10px * var(--ui-scale));
        }}

        .stat-value {{
            font-size: calc(36px * var(--ui-scale));
            font-weight: 600;
            font-family: var(--font-mono);
            color: var(--text);
            line-height: 1;
            letter-spacing: -0.02em;
        }}

        .stat-value.positive {{
            color: var(--phosphor);
        }}

        .stat-value.negative {{
            color: var(--rose);
        }}

        .stat-detail {{
            font-size: calc(14px * var(--ui-scale));
            font-family: var(--font-mono);
            color: var(--text-muted);
            margin-top: calc(8px * var(--ui-scale));
        }}

        .stat-sparkline {{
            position: absolute;
            bottom: 0;
            left: 0;
            right: 0;
            height: calc(40px * var(--ui-scale));
            opacity: 0.15;
        }}

        /* Charts Area */
        .charts-area {{
            flex: 1;
            display: flex;
            flex-direction: column;
            padding: calc(20px * var(--ui-scale));
            gap: calc(16px * var(--ui-scale));
            min-width: 0;
            overflow: hidden;
        }}

        .chart-container {{
            background: var(--deep);
            border: 1px solid var(--border-subtle);
            border-radius: calc(16px * var(--ui-scale));
            flex: 1;
            min-height: 0;
            position: relative;
            overflow: hidden;
            transition: all 0.3s ease;
        }}

        .chart-container:hover {{
            border-color: var(--border);
        }}

        .chart-container::before {{
            content: '';
            position: absolute;
            inset: 0;
            background: radial-gradient(ellipse at 50% 0%, rgba(74, 222, 128, 0.03) 0%, transparent 70%);
            pointer-events: none;
        }}

        #churn-chart {{
            flex: 0.5;
        }}

        /* Inspector Panel */
        .inspector {{
            width: calc(420px * var(--ui-scale));
            background: var(--deep);
            border-left: 1px solid var(--border-subtle);
            display: flex;
            flex-direction: column;
            overflow: hidden;
            flex-shrink: 0;
        }}

        .inspector-header {{
            padding: calc(24px * var(--ui-scale));
            background: linear-gradient(180deg, var(--surface) 0%, var(--deep) 100%);
            border-bottom: 1px solid var(--border-subtle);
        }}

        .inspector-title {{
            font-size: calc(14px * var(--ui-scale));
            font-family: var(--font-mono);
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.1em;
            margin-bottom: calc(10px * var(--ui-scale));
        }}

        .inspector-date {{
            font-size: calc(26px * var(--ui-scale));
            font-weight: 600;
            font-family: var(--font-mono);
            color: var(--text);
            letter-spacing: -0.02em;
        }}

        .inspector-legend {{
            display: flex;
            flex-wrap: wrap;
            gap: calc(14px * var(--ui-scale));
            padding: calc(14px * var(--ui-scale)) calc(24px * var(--ui-scale));
            background: var(--abyss);
            border-bottom: 1px solid var(--border-subtle);
        }}

        .legend-controls {{
            display: flex;
            gap: calc(8px * var(--ui-scale));
            width: 100%;
        }}

        .legend-action {{
            background: var(--surface);
            border: 1px solid var(--border);
            color: var(--text-secondary);
            font-family: var(--font-mono);
            font-size: calc(12px * var(--ui-scale));
            border-radius: calc(999px * var(--ui-scale));
            padding: calc(4px * var(--ui-scale)) calc(10px * var(--ui-scale));
            cursor: pointer;
            transition: all 0.2s ease;
        }}

        .legend-action:hover {{
            border-color: var(--phosphor-dim);
            color: var(--text);
        }}

        .legend-item {{
            display: flex;
            align-items: center;
            gap: calc(8px * var(--ui-scale));
            font-size: calc(14px * var(--ui-scale));
            font-family: var(--font-mono);
            color: var(--text-secondary);
            cursor: pointer;
            user-select: none;
        }}

        .legend-text {{
            display: flex;
            flex-direction: column;
            gap: calc(2px * var(--ui-scale));
        }}

        .legend-name {{
            font-weight: 500;
        }}

        .legend-meta {{
            font-size: calc(11px * var(--ui-scale));
            color: var(--text-muted);
            letter-spacing: 0.02em;
        }}

        .legend-item.inactive {{
            opacity: 0.4;
            text-decoration: line-through;
        }}

        .legend-dot {{
            width: calc(10px * var(--ui-scale));
            height: calc(10px * var(--ui-scale));
            border-radius: calc(3px * var(--ui-scale));
        }}

        .event-list {{
            flex: 1;
            overflow-y: auto;
            padding: 0;
            margin: 0;
            list-style: none;
        }}

        .event-list::-webkit-scrollbar {{
            width: calc(6px * var(--ui-scale));
        }}

        .event-list::-webkit-scrollbar-track {{
            background: var(--abyss);
        }}

        .event-list::-webkit-scrollbar-thumb {{
            background: var(--border);
            border-radius: 3px;
        }}

        .event-list::-webkit-scrollbar-thumb:hover {{
            background: var(--text-muted);
        }}

        .event-item {{
            padding: calc(18px * var(--ui-scale)) calc(24px * var(--ui-scale));
            border-bottom: 1px solid var(--border-subtle);
            transition: all 0.2s ease;
            animation: slideIn 0.3s ease backwards;
            cursor: default;
        }}

        .event-item:hover {{
            background: var(--surface);
        }}

        .event-header {{
            display: flex;
            align-items: center;
            gap: calc(12px * var(--ui-scale));
            margin-bottom: calc(10px * var(--ui-scale));
        }}

        .event-hash {{
            font-family: var(--font-mono);
            font-size: calc(14px * var(--ui-scale));
            font-weight: 500;
            color: var(--cyan);
            background: rgba(34, 211, 238, 0.1);
            padding: calc(4px * var(--ui-scale)) calc(10px * var(--ui-scale));
            border-radius: calc(5px * var(--ui-scale));
        }}

        .event-author {{
            font-size: calc(14px * var(--ui-scale));
            color: var(--text-muted);
            flex: 1;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}

        .event-message {{
            font-size: calc(16px * var(--ui-scale));
            color: var(--text);
            line-height: 1.5;
            margin-bottom: calc(12px * var(--ui-scale));
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }}

        .event-stats {{
            display: flex;
            gap: calc(14px * var(--ui-scale));
            margin-bottom: calc(12px * var(--ui-scale));
        }}

        .event-stat {{
            font-family: var(--font-mono);
            font-size: calc(15px * var(--ui-scale));
            font-weight: 500;
        }}

        .event-stat.add {{
            color: var(--phosphor);
        }}

        .event-stat.del {{
            color: var(--rose);
        }}

        .event-categories {{
            display: flex;
            flex-wrap: wrap;
            gap: calc(6px * var(--ui-scale));
        }}

        .cat-badge {{
            font-family: var(--font-mono);
            font-size: calc(13px * var(--ui-scale));
            font-weight: 500;
            padding: calc(5px * var(--ui-scale)) calc(10px * var(--ui-scale));
            border-radius: calc(5px * var(--ui-scale));
            border: 1px solid;
            transition: all 0.2s ease;
        }}

        .cat-badge:hover {{
            transform: scale(1.05);
        }}

        .event-files {{
            margin-top: calc(12px * var(--ui-scale));
            padding: calc(12px * var(--ui-scale));
            background: var(--abyss);
            border-radius: calc(8px * var(--ui-scale));
            font-family: var(--font-mono);
            font-size: calc(13px * var(--ui-scale));
            color: var(--text-muted);
        }}

        .file-entry {{
            display: block;
            padding: calc(2px * var(--ui-scale)) 0;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}

        .file-entry:hover {{
            color: var(--text-secondary);
        }}

        .empty-state {{
            padding: calc(48px * var(--ui-scale)) calc(24px * var(--ui-scale));
            text-align: center;
            color: var(--text-muted);
            font-size: calc(16px * var(--ui-scale));
        }}

        .empty-state-icon {{
            font-size: calc(40px * var(--ui-scale));
            margin-bottom: calc(14px * var(--ui-scale));
            opacity: 0.5;
        }}

        /* Range indicator */
        .range-indicator {{
            display: flex;
            align-items: center;
            gap: calc(10px * var(--ui-scale));
            font-family: var(--font-mono);
            font-size: calc(14px * var(--ui-scale));
            color: var(--text-muted);
            padding: calc(14px * var(--ui-scale)) calc(24px * var(--ui-scale));
            background: var(--abyss);
            border-bottom: 1px solid var(--border-subtle);
        }}

        .range-indicator span {{
            color: var(--text-secondary);
        }}

        /* Loading state */
        .loading {{
            display: flex;
            align-items: center;
            justify-content: center;
            height: 100%;
            color: var(--text-muted);
        }}

        @media (max-width: 1200px) {{
            .inspector {{
                width: calc(360px * var(--ui-scale));
            }}
            .section {{
                padding: calc(22px * var(--ui-scale));
            }}
            .section-grid {{
                grid-template-columns: repeat(6, minmax(0, 1fr));
            }}
            .panel.span-12,
            .panel.span-8,
            .panel.span-7,
            .panel.span-6,
            .panel.span-5,
            .panel.span-4 {{
                grid-column: span 6;
            }}
            .stats-bar {{
                padding: calc(16px * var(--ui-scale)) calc(20px * var(--ui-scale));
                gap: calc(14px * var(--ui-scale));
            }}
            .stat-card {{
                min-width: calc(160px * var(--ui-scale));
                padding: calc(16px * var(--ui-scale)) calc(20px * var(--ui-scale));
            }}
            .stat-value {{
                font-size: calc(30px * var(--ui-scale));
            }}
        }}

        @media (max-width: 900px) {{
            .section-nav {{
                position: static;
            }}
            .section-grid {{
                grid-template-columns: repeat(1, minmax(0, 1fr));
            }}
            .panel.span-12,
            .panel.span-8,
            .panel.span-7,
            .panel.span-6,
            .panel.span-5,
            .panel.span-4 {{
                grid-column: span 1;
            }}
            .main {{
                flex-direction: column;
            }}
            .inspector {{
                width: 100%;
                border-left: none;
                border-top: 1px solid var(--border-subtle);
            }}
        }}
    </style>
</head>
<body>
    <header class="header">
        <div class="logo">
            <div class="logo-icon">V</div>
            <div>
                <div class="logo-text">Velocity Observatory</div>
                <div class="logo-sub">Realm Development Metrics</div>
            </div>
        </div>
        <div class="header-meta">
            <div class="project-selector">
                <select id="project-select"></select>
            </div>
            <div class="scale-selector">
                <select id="scale-select"></select>
            </div>
            <div class="timestamp">
                {dt.datetime.now().strftime('%Y-%m-%d %H:%M')} UTC
            </div>
        </div>
    </header>

    <nav class="section-nav">
        <a href="#overview">Overview</a>
        <a href="#activity">Activity</a>
        <a href="#rhythm">Rhythm</a>
        <a href="#history">History</a>
        <a href="#mix">Mix</a>
        <a href="#people">People</a>
        <a href="#compare">Compare</a>
        <a href="#system">System</a>
    </nav>

    <section id="overview" class="section">
        <div class="section-header">
            <div class="section-title">Overview</div>
            <div class="section-subtitle">Velocity = LoC growth + churn over time, with commit-level inspection.</div>
        </div>
        <div class="filter-bar" id="filter-bar"></div>
        <div class="stats-bar" id="stats-bar"></div>

        <div class="main">
            <div class="charts-area">
                <div id="growth-chart" class="chart-container"></div>
                <div id="churn-chart" class="chart-container"></div>
            </div>

            <div class="inspector">
                <div class="inspector-header">
                    <div class="inspector-title">Activity Inspector</div>
                    <div class="inspector-date" id="inspector-date">Select a date</div>
                </div>
                <div class="range-indicator" id="range-indicator"></div>
                <div class="inspector-legend" id="inspector-legend"></div>
                <ul class="event-list" id="event-list">
                    <li class="empty-state">
                        <div class="empty-state-icon">o</div>
                        Hover over the charts to inspect daily activity
                    </li>
                </ul>
            </div>
        </div>
    </section>

    <section id="activity" class="section">
        <div class="section-header">
            <div class="section-title">Activity Pulse</div>
            <div class="section-subtitle">Daily net flow, cadence, and highlight windows.</div>
        </div>
        <div class="section-grid">
            <div class="panel span-8">
                <div class="panel-title">Net Momentum</div>
                <div class="panel-subtitle">Daily net lines with 7/30-day smoothing.</div>
                <div id="net-chart" class="panel-chart"></div>
            </div>
            <div class="panel span-4">
                <div class="panel-title">Key Signals</div>
                <div class="panel-subtitle">Fast read on intensity and stability.</div>
                <div class="insight-grid" id="insight-cards"></div>
            </div>
            <div class="panel span-8">
                <div class="panel-title">Commit Cadence</div>
                <div class="panel-subtitle">Daily commits and 7-day average.</div>
                <div id="commit-chart" class="panel-chart"></div>
            </div>
            <div class="panel span-4">
                <div class="panel-title">Top Burst Days</div>
                <div class="panel-subtitle">Highest churn days in the window.</div>
                <table class="data-table" id="top-days-table"></table>
            </div>
        </div>
    </section>

    <section id="rhythm" class="section">
        <div class="section-header">
            <div class="section-title">Rhythm & Streaks</div>
            <div class="section-subtitle">Calendar view plus weekly and streak-based signals.</div>
        </div>
        <div class="section-grid">
            <div class="panel span-7">
                <div class="panel-title">Activity Calendar</div>
                <div class="panel-subtitle">Daily churn heatmap.</div>
                <div id="heatmap-chart" class="panel-chart"></div>
            </div>
            <div class="panel span-5">
                <div class="panel-title">Weekday Profile</div>
                <div class="panel-subtitle">Average commits and churn by weekday.</div>
                <div id="weekday-chart" class="panel-chart"></div>
            </div>
            <div class="panel span-12">
                <div class="panel-title">Streak Signals</div>
                <div class="panel-subtitle">Consistency, recency, and stability metrics.</div>
                <div class="insight-grid" id="rhythm-cards"></div>
            </div>
        </div>
    </section>

    <section id="history" class="section">
        <div class="section-header">
            <div class="section-title">History Lab</div>
            <div class="section-subtitle">Commit size, cadence, topology, and hotspots.</div>
        </div>
        <div class="section-grid">
            <div class="panel span-6">
                <div class="panel-title">Commit Size Distribution</div>
                <div class="panel-subtitle">Histogram of lines changed per commit.</div>
                <div id="size-chart" class="panel-chart"></div>
            </div>
            <div class="panel span-6">
                <div class="panel-title">Time-of-Day Heatmap</div>
                <div class="panel-subtitle">Commit density by weekday and hour.</div>
                <div id="timeofday-chart" class="panel-chart"></div>
            </div>
            <div class="panel span-7">
                <div class="panel-title">Merge Topology</div>
                <div class="panel-subtitle">Merge volume, ratio, and fan-in.</div>
                <div id="merge-chart" class="panel-chart"></div>
            </div>
            <div class="panel span-5">
                <div class="panel-title">Tag Cadence</div>
                <div class="panel-subtitle">Release marker frequency over time.</div>
                <div id="tag-chart" class="panel-chart"></div>
            </div>
            <div class="panel span-6">
                <div class="panel-title">File Hotspots</div>
                <div class="panel-subtitle">Highest churn files.</div>
                <table class="data-table" id="file-hotspot-table"></table>
            </div>
            <div class="panel span-6">
                <div class="panel-title">Module Hotspots</div>
                <div class="panel-subtitle">Highest churn modules.</div>
                <table class="data-table" id="module-hotspot-table"></table>
            </div>
            <div class="panel span-7">
                <div class="panel-title">Co-change Network</div>
                <div class="panel-subtitle">Modules that move together.</div>
                <div id="cochange-chart" class="panel-chart"></div>
            </div>
            <div class="panel span-5">
                <div class="panel-title">Ownership / Bus Factor</div>
                <div class="panel-subtitle">Top author share per module.</div>
                <table class="data-table" id="ownership-table"></table>
            </div>
        </div>
    </section>

    <section id="mix" class="section">
        <div class="section-header">
            <div class="section-title">Category Mix</div>
            <div class="section-subtitle">Share and momentum across selected categories/projects.</div>
        </div>
        <div class="section-grid">
            <div class="panel span-6">
                <div class="panel-title">Share Map</div>
                <div class="panel-subtitle">Total LOC vs 30-day churn mix.</div>
                <div id="share-chart" class="panel-chart"></div>
            </div>
            <div class="panel span-6">
                <div class="panel-title">Momentum Matrix</div>
                <div class="panel-subtitle">30-day net and churn per category.</div>
                <div id="momentum-chart" class="panel-chart"></div>
            </div>
            <div class="panel span-12">
                <div class="panel-title">Churn Treemap</div>
                <div class="panel-subtitle">Module churn distribution.</div>
                <div id="treemap-chart" class="panel-chart"></div>
            </div>
            <div class="panel span-12">
                <div class="panel-title">Category Ranking</div>
                <div class="panel-subtitle">Snapshot of totals and recent movement.</div>
                <table class="data-table" id="mix-table"></table>
            </div>
        </div>
    </section>

    <section id="people" class="section">
        <div class="section-header">
            <div class="section-title">People & Authors</div>
            <div class="section-subtitle">Commit share and change impact by author.</div>
        </div>
        <div class="section-grid">
            <div class="panel span-6">
                <div class="panel-title">Top Authors</div>
                <div class="panel-subtitle">Commit volume by author.</div>
                <div id="author-chart" class="panel-chart"></div>
            </div>
            <div class="panel span-6">
                <div class="panel-title">Author Breakdown</div>
                <div class="panel-subtitle">Commits, net, churn, active days.</div>
                <table class="data-table" id="author-table"></table>
            </div>
        </div>
    </section>

    <section id="compare" class="section">
        <div class="section-header">
            <div class="section-title">Compare & Rank</div>
            <div class="section-subtitle">Project-level or category-level ranking.</div>
        </div>
        <div class="section-grid">
            <div class="panel span-12">
                <div class="panel-title">Ranked Overview</div>
                <div class="panel-subtitle">Total scale vs recent acceleration.</div>
                <div id="compare-chart" class="panel-chart"></div>
            </div>
            <div class="panel span-12">
                <div class="panel-title">Rank Table</div>
                <div class="panel-subtitle">Totals, 30-day net, and churn.</div>
                <table class="data-table" id="compare-table"></table>
            </div>
        </div>
    </section>

    <section id="system" class="section">
        <div class="section-header">
            <div class="section-title">System Map</div>
            <div class="section-subtitle">Velocity pipeline and signal flow.</div>
        </div>
        <div class="mermaid-card">
            <pre class="mermaid">
graph LR
    Git[Git History] --> Classifier[Per-Repo Classifier]
    Classifier --> Daily[Daily Aggregates]
    Daily --> Charts[ECharts Dashboards]
    Charts --> Review[Velocity Narratives]
    Review --> Iteration[Next Iteration Decisions]
            </pre>
        </div>
    </section>

    <script>
        const dates = {json.dumps(sorted_dates)};
        const projectData = {json.dumps(js_projects)};
        const projectSummaries = {json.dumps(project_summaries)};
        const projects = Object.keys(projectData);
        const selectionByProject = {{}};
        let lastInspectorIndex = null;
        let renderedInspectorIndex = null;
        let inspectorFrame = null;
        let suppressLegendEvents = false;

        const params = new URLSearchParams(window.location.search);
        const rendererParam = (params.get('renderer') || 'canvas').toLowerCase();
        const renderer = rendererParam === 'svg' ? 'svg' : 'canvas';
        const scaleOptions = [1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0];
        const maxDpr = 2.5;

        function normalizeScale(raw) {{
            const value = Number.parseFloat(raw);
            if (!Number.isFinite(value)) return null;
            if (value < 0.5 || value > 3.0) return null;
            return Math.round(value * 100) / 100;
        }}

        function safeStorageGet(key) {{
            try {{
                return localStorage.getItem(key);
            }} catch (_) {{
                return null;
            }}
        }}

        function safeStorageSet(key, value) {{
            try {{
                localStorage.setItem(key, value);
            }} catch (_) {{
                // ignore storage failures (file:// permissions, etc.)
            }}
        }}

        let uiScale = 2.0;
        const paramScale = normalizeScale(params.get('scale'));
        const storedScale = normalizeScale(safeStorageGet('velocityScale'));
        if (paramScale !== null) {{
            uiScale = paramScale;
            safeStorageSet('velocityScale', String(uiScale));
        }} else if (storedScale !== null) {{
            uiScale = storedScale;
        }}
        document.documentElement.style.setProperty('--ui-scale', String(uiScale));

        const scaled = (value) => Math.round(value * uiScale);
        const scaledArray = (values) => values.map((value) => Math.round(value * uiScale));

        let currentDpr = 0;
        const charts = {{}};
        const chartIds = {{
            growth: 'growth-chart',
            churn: 'churn-chart',
            net: 'net-chart',
            commit: 'commit-chart',
            heatmap: 'heatmap-chart',
            weekday: 'weekday-chart',
            size: 'size-chart',
            timeofday: 'timeofday-chart',
            merge: 'merge-chart',
            tag: 'tag-chart',
            share: 'share-chart',
            momentum: 'momentum-chart',
            treemap: 'treemap-chart',
            author: 'author-chart',
            compare: 'compare-chart',
            cochange: 'cochange-chart'
        }};

        function computeDpr() {{
            const base = window.devicePixelRatio || 1;
            const scaled = base * uiScale;
            const capped = Math.min(maxDpr, scaled);
            return Math.max(1, Math.round(capped * 10) / 10);
        }}

        function initCharts() {{
            const nextDpr = computeDpr();
            currentDpr = nextDpr;
            Object.entries(chartIds).forEach(([key, id]) => {{
                if (charts[key]) {{
                    charts[key].dispose();
                }}
                const el = document.getElementById(id);
                if (!el) {{
                    return;
                }}
                charts[key] = echarts.init(
                    el,
                    null,
                    {{ renderer: renderer, devicePixelRatio: nextDpr }}
                );
            }});
            bindChartEvents();
        }}

        // Populate project selector
        const projectSelect = document.getElementById('project-select');
        const scaleSelect = document.getElementById('scale-select');
        projects.forEach((p, i) => {{
            const opt = document.createElement('option');
            opt.value = p;
            opt.textContent = p;
            projectSelect.appendChild(opt);
        }});

        scaleOptions.forEach((value) => {{
            const opt = document.createElement('option');
            opt.value = String(value);
            opt.textContent = `${{Math.round(value * 100)}}%`;
            scaleSelect.appendChild(opt);
        }});
        scaleSelect.value = String(uiScale);

        let currentProject = projects.includes('{AGGREGATE_PROJECT}')
            ? '{AGGREGATE_PROJECT}'
            : projects[0];

        function setScale(rawValue, persist = true) {{
            const next = normalizeScale(rawValue);
            if (next === null) {{
                return;
            }}
            uiScale = next;
            document.documentElement.style.setProperty('--ui-scale', String(uiScale));
            if (persist) {{
                safeStorageSet('velocityScale', String(uiScale));
            }}
            scaleSelect.value = String(uiScale);
            initCharts();
            updateCharts(currentProject);
            if (lastInspectorIndex !== null) {{
                updateInspector(lastInspectorIndex);
            }}
        }}

        function ensureSelection(projectName) {{
            if (!selectionByProject[projectName]) {{
                selectionByProject[projectName] = new Set(projectData[projectName].categoryList);
            }}
            return selectionByProject[projectName];
        }}

        function selectionMap(projectName, selectedSet) {{
            const map = {{}};
            projectData[projectName].categoryList.forEach((cat) => {{
                map[cat] = selectedSet.has(cat);
            }});
            return map;
        }}

        function formatNumber(n) {{
            const sign = n < 0 ? '-' : '';
            const abs = Math.abs(n);
            if (abs >= 1000000) return sign + (abs / 1000000).toFixed(1) + 'M';
            if (abs >= 1000) return sign + (abs / 1000).toFixed(1) + 'K';
            return sign + abs.toString();
        }}

        function parseDate(value) {{
            if (typeof value === 'string') {{
                const match = value.match(/^(\\d{{4}})-(\\d{{2}})-(\\d{{2}})$/);
                if (match) {{
                    const year = Number(match[1]);
                    const month = Number(match[2]);
                    const day = Number(match[3]);
                    if (Number.isFinite(year) && Number.isFinite(month) && Number.isFinite(day)) {{
                        return new Date(year, month - 1, day);
                    }}
                }}
            }}
            return new Date(value);
        }}

        function formatAxisValue(value) {{
            const abs = Math.abs(value);
            const formatted = formatNumber(abs);
            return value < 0 ? '-' + formatted : formatted;
        }}

        function formatDecimal(value, digits = 1) {{
            if (!Number.isFinite(value)) return 'n/a';
            return value.toFixed(digits);
        }}

        function escapeHtml(value) {{
            return String(value)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
        }}

        function movingAverage(series, window) {{
            const out = [];
            let sum = 0;
            for (let i = 0; i < series.length; i++) {{
                sum += series[i];
                if (i >= window) {{
                    sum -= series[i - window];
                }}
                const denom = Math.min(window, i + 1);
                out.push(sum / denom);
            }}
            return out;
        }}

        function median(series) {{
            if (!series.length) return 0;
            const sorted = [...series].sort((a, b) => a - b);
            const mid = Math.floor(sorted.length / 2);
            if (sorted.length % 2 === 0) {{
                return (sorted[mid - 1] + sorted[mid]) / 2;
            }}
            return sorted[mid];
        }}

        function eventMatchesSelection(event, selectedSet) {{
            const cats = event.cats ? Object.keys(event.cats) : [];
            return cats.some((cat) => selectedSet.has(cat));
        }}

        function computeDailyMetrics(projectName, selectedSet) {{
            const data = projectData[projectName];
            const netSeries = [];
            const churnSeries = [];
            const commitSeries = [];
            const activeSeries = [];

            for (let i = 0; i < dates.length; i++) {{
                let dayNet = 0;
                let dayChurn = 0;
                for (const cat of data.categoryList) {{
                    if (!selectedSet.has(cat)) {{
                        continue;
                    }}
                    const series = data.categories[cat];
                    const net = series.net || [];
                    dayNet += net[i] || 0;
                    dayChurn += series.churn[i] || 0;
                }}

                const events = data.events[dates[i]] || [];
                let commits = 0;
                for (const ev of events) {{
                    if (eventMatchesSelection(ev, selectedSet)) {{
                        commits += 1;
                    }}
                }}
                netSeries.push(dayNet);
                churnSeries.push(dayChurn);
                commitSeries.push(commits);
                activeSeries.push(commits > 0 ? 1 : 0);
            }}

            return {{ netSeries, churnSeries, commitSeries, activeSeries }};
        }}

        function computeMix(projectName, selectedSet) {{
            const data = projectData[projectName];
            const categories = data.categoryList.filter((cat) => selectedSet.has(cat));
            const rows = categories.map((cat) => {{
                const summary = categorySummary(data, cat);
                return {{
                    name: cat,
                    totalLoc: summary.totalLoc,
                    net30: summary.net30,
                    churn30: summary.churn30
                }};
            }});
            rows.sort((a, b) => b.totalLoc - a.totalLoc);
            return {{ categories, rows }};
        }}

        function computeWeekdayProfile(metrics) {{
            const labels = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
            const counts = Array(7).fill(0);
            const commitSum = Array(7).fill(0);
            const churnSum = Array(7).fill(0);
            const netSum = Array(7).fill(0);

            for (let i = 0; i < dates.length; i++) {{
                const dayIndex = (parseDate(dates[i]).getDay() + 6) % 7;
                counts[dayIndex] += 1;
                commitSum[dayIndex] += metrics.commitSeries[i];
                churnSum[dayIndex] += metrics.churnSeries[i];
                netSum[dayIndex] += metrics.netSeries[i];
            }}

            const avgCommits = commitSum.map((value, i) => counts[i] ? value / counts[i] : 0);
            const avgChurn = churnSum.map((value, i) => counts[i] ? value / counts[i] : 0);
            const avgNet = netSum.map((value, i) => counts[i] ? value / counts[i] : 0);
            return {{ labels, avgCommits, avgChurn, avgNet }};
        }}

        function computeAuthorStats(projectName, selectedSet) {{
            const data = projectData[projectName];
            const byAuthor = {{}};

            for (const day of dates) {{
                const events = data.events[day] || [];
                for (const ev of events) {{
                    if (!eventMatchesSelection(ev, selectedSet)) {{
                        continue;
                    }}
                    const author = ev.a || 'unknown';
                    if (!byAuthor[author]) {{
                        byAuthor[author] = {{ commits: 0, net: 0, churn: 0, days: new Set() }};
                    }}
                    byAuthor[author].commits += 1;
                    byAuthor[author].days.add(day);
                    let net = 0;
                    let churn = 0;
                    if (ev.cats) {{
                        for (const [cat, stats] of Object.entries(ev.cats)) {{
                            if (!selectedSet.has(cat)) {{
                                continue;
                            }}
                            net += stats.a - stats.r;
                            churn += stats.a + stats.r;
                        }}
                    }}
                    byAuthor[author].net += net;
                    byAuthor[author].churn += churn;
                }}
            }}

            const list = Object.entries(byAuthor).map(([name, stats]) => ({{
                name,
                commits: stats.commits,
                net: stats.net,
                churn: stats.churn,
                activeDays: stats.days.size
            }}));
            list.sort((a, b) => b.commits - a.commits);
            return list;
        }}

        function computeStreaks(commitSeries) {{
            let longest = 0;
            let current = 0;
            for (const value of commitSeries) {{
                if (value > 0) {{
                    current += 1;
                    longest = Math.max(longest, current);
                }} else {{
                    current = 0;
                }}
            }}

            let tail = 0;
            for (let i = commitSeries.length - 1; i >= 0; i--) {{
                if (commitSeries[i] > 0) {{
                    tail += 1;
                }} else {{
                    break;
                }}
            }}
            return {{ longest, current: tail }};
        }}

        function computeTopDays(metrics, limit = 8) {{
            const rows = dates.map((date, i) => ({{
                date,
                churn: metrics.churnSeries[i],
                net: metrics.netSeries[i],
                commits: metrics.commitSeries[i]
            }}));
            const sorted = rows
                .filter((row) => row.churn > 0 || row.net !== 0 || row.commits > 0)
                .sort((a, b) => b.churn - a.churn);
            return sorted.slice(0, limit);
        }}

        function renderTable(tableEl, headers, rows) {{
            if (!tableEl) {{
                return;
            }}
            const headerHtml = `<thead><tr>${{headers.map((h) => `<th>${{escapeHtml(h)}}</th>`).join('')}}</tr></thead>`;
            if (!rows.length) {{
                tableEl.innerHTML = headerHtml + `<tbody><tr><td colspan="${{headers.length}}">No data</td></tr></tbody>`;
                return;
            }}
            const bodyHtml = rows.map((row) => {{
                const cells = row.map((cell) => `<td>${{escapeHtml(cell)}}</td>`).join('');
                return `<tr>${{cells}}</tr>`;
            }}).join('');
            tableEl.innerHTML = headerHtml + `<tbody>${{bodyHtml}}</tbody>`;
        }}

        function computeSummary(projectName, selectedSet) {{
            const data = projectData[projectName];
            const categories = data.categoryList.filter((cat) => selectedSet.has(cat));
            let totalLoc = 0;
            let recentNet = 0;
            let recentChurn = 0;
            let weekNet = 0;
            let peakChurn = 0;
            let peakDate = null;
            let peakNet = 0;
            let peakNetDate = null;
            let recentCommits = 0;
            let recentActiveDays = 0;
            const categoryTotals = [];
            const recentDates = new Set(dates.slice(-30));
            const recentWindow = Math.min(30, dates.length);

            for (const cat of categories) {{
                const series = data.categories[cat];
                const growth = series.growth;
                const churn = series.churn;
                const net = series.net || [];
                const catTotal = growth.length ? growth[growth.length - 1] : 0;
                categoryTotals.push({{ name: cat, totalLoc: catTotal }});
                if (growth.length) {{
                    totalLoc += catTotal;
                    recentNet += net.slice(-30).reduce((a, b) => a + b, 0);
                    weekNet += net.slice(-7).reduce((a, b) => a + b, 0);
                }}
                if (churn.length) {{
                    recentChurn += churn.slice(-30).reduce((a, b) => a + b, 0);
                    churn.forEach((value, i) => {{
                        if (value > peakChurn) {{
                            peakChurn = value;
                            peakDate = dates[i];
                        }}
                    }});
                }}
            }}

            for (let i = 0; i < dates.length; i++) {{
                let dayNet = 0;
                for (const cat of categories) {{
                    const net = data.categories[cat].net || [];
                    dayNet += net[i] || 0;
                }}
                if (Math.abs(dayNet) > Math.abs(peakNet)) {{
                    peakNet = dayNet;
                    peakNetDate = dates[i];
                }}
            }}

            categoryTotals.sort((a, b) => b.totalLoc - a.totalLoc);
            const topCategories = categoryTotals.slice(0, 3);

            let totalCommits = 0;
            let activeDays = 0;
            let activityStart = null;
            let activityEnd = null;
            for (const day of dates) {{
                const events = data.events[day] || [];
                let dayCommits = 0;
                let dayMatches = false;
                for (const ev of events) {{
                    const cats = ev.cats ? Object.keys(ev.cats) : [];
                    if (cats.some((cat) => selectedSet.has(cat))) {{
                        totalCommits += 1;
                        dayCommits += 1;
                        dayMatches = true;
                        if (recentDates.has(day)) {{
                            recentCommits += 1;
                        }}
                    }}
                }}
                if (dayCommits > 0) {{
                    activeDays += 1;
                    if (recentDates.has(day)) {{
                        recentActiveDays += 1;
                    }}
                }}
                if (dayMatches) {{
                    if (!activityStart) {{
                        activityStart = day;
                    }}
                    activityEnd = day;
                }}
            }}

            const recentDays = Math.max(1, recentWindow);
            return {{
                totalLoc,
                totalCommits,
                activeDays,
                recentNet,
                recentChurn,
                weekNet,
                peakChurn,
                peakDate,
                peakNet,
                peakNetDate,
                recentCommits,
                recentActiveDays,
                recentNetDaily: recentNet / recentDays,
                recentChurnDaily: recentChurn / recentDays,
                recentCommitDaily: recentCommits / recentDays,
                recentDays,
                categoriesCount: categories.length,
                categoriesTotal: data.categoryList.length,
                topCategories,
                activityStart,
                activityEnd,
            }};
        }}

        function updateStatsBar(projectName, selectedSet) {{
            const data = projectData[projectName];
            if (!data) return;
            const summary = computeSummary(projectName, selectedSet);
            const statsBar = document.getElementById('stats-bar');
            const recentSign = summary.recentNet >= 0 ? '+' : '';
            const weekSign = summary.weekNet >= 0 ? '+' : '';
            const peakLabel = summary.peakDate ? summary.peakDate : 'n/a';
            const peakNetSign = summary.peakNet >= 0 ? '+' : '';
            const peakNetLabel = summary.peakNetDate ? summary.peakNetDate : 'n/a';
            const hiddenCount = summary.categoriesTotal - summary.categoriesCount;
            const isAggregate = projectName === '{AGGREGATE_PROJECT}';
            const categoryLabel = isAggregate ? 'Projects' : 'Categories';
            const topLabel = isAggregate ? 'Top Projects' : 'Top Mix';
            const selectedNames = data.categoryList.filter((cat) => selectedSet.has(cat));
            const selectedLabel = selectedNames.length <= 4
                ? selectedNames.join(', ')
                : `${{selectedNames.slice(0, 4).join(', ')}} +${{selectedNames.length - 4}} more`;
            const topDetails = summary.topCategories.length
                ? summary.topCategories.map((item) => `${{item.name}} ${{formatNumber(item.totalLoc)}}`).join(' | ')
                : 'n/a';

            statsBar.innerHTML = `
                <div class="stat-card">
                    <div class="stat-label">Total Lines</div>
                    <div class="stat-value">${{formatNumber(summary.totalLoc)}}</div>
                    <div class="stat-detail">${{summary.categoriesCount}} selected | ${{hiddenCount}} hidden</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">30-Day Net</div>
                    <div class="stat-value ${{summary.recentNet >= 0 ? 'positive' : 'negative'}}">${{recentSign}}${{formatNumber(summary.recentNet)}}</div>
                    <div class="stat-detail">${{formatNumber(summary.recentNetDaily)}}/day | ${{summary.recentDays}}d window</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">30-Day Churn</div>
                    <div class="stat-value">${{formatNumber(summary.recentChurn)}}</div>
                    <div class="stat-detail">${{formatNumber(summary.recentChurnDaily)}}/day | ${{summary.recentDays}}d window</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">7-Day Net</div>
                    <div class="stat-value ${{summary.weekNet >= 0 ? 'positive' : 'negative'}}">${{weekSign}}${{formatNumber(summary.weekNet)}}</div>
                    <div class="stat-detail">${{formatNumber(summary.weekNet / 7)}}/day</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Peak Churn</div>
                    <div class="stat-value">${{formatNumber(summary.peakChurn)}}</div>
                    <div class="stat-detail">${{peakLabel}}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Peak Net Day</div>
                    <div class="stat-value ${{summary.peakNet >= 0 ? 'positive' : 'negative'}}">${{peakNetSign}}${{formatNumber(summary.peakNet)}}</div>
                    <div class="stat-detail">${{peakNetLabel}}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">${{categoryLabel}}</div>
                    <div class="stat-value">${{summary.categoriesCount}} / ${{summary.categoriesTotal}}</div>
                    <div class="stat-detail">${{selectedLabel || 'none selected'}}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">${{topLabel}}</div>
                    <div class="stat-value">${{summary.topCategories[0] ? summary.topCategories[0].name : 'n/a'}}</div>
                    <div class="stat-detail">${{topDetails}}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">30-Day Commits</div>
                    <div class="stat-value">${{formatNumber(summary.recentCommits)}}</div>
                    <div class="stat-detail">${{formatNumber(summary.recentCommitDaily)}}/day | ${{summary.recentActiveDays}} active days</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Total Commits</div>
                    <div class="stat-value">${{formatNumber(summary.totalCommits)}}</div>
                    <div class="stat-detail">${{summary.activeDays}} active days</div>
                </div>
            `;
        }}

        function categorySummary(data, cat) {{
            const series = data.categories[cat];
            const growth = series.growth;
            const churn = series.churn;
            const totalLoc = growth.length ? growth[growth.length - 1] : 0;
            const net = series.net || [];
            const net30 = net.length ? net.slice(-30).reduce((a, b) => a + b, 0) : 0;
            const churn30 = churn.length ? churn.slice(-30).reduce((a, b) => a + b, 0) : 0;
            return {{ totalLoc, net30, churn30 }};
        }}

        function updateLegend(projectName, selectedSet) {{
            const data = projectData[projectName];
            const legend = document.getElementById('inspector-legend');
            const controls = `
                <div class="legend-controls">
                    <button class="legend-action" data-action="all">All</button>
                    <button class="legend-action" data-action="none">None</button>
                </div>
            `;
            const items = data.categoryList.map(cat => {{
                const active = selectedSet.has(cat);
                const metrics = categorySummary(data, cat);
                const netSign = metrics.net30 >= 0 ? '+' : '';
                return `
                <div class="legend-item ${{active ? 'active' : 'inactive'}}" data-cat="${{cat}}">
                    <div class="legend-dot" style="background: ${{data.colors[cat]}}"></div>
                    <div class="legend-text">
                        <span class="legend-name">${{cat}}</span>
                        <span class="legend-meta">${{formatNumber(metrics.totalLoc)}} | ${{netSign}}${{formatNumber(metrics.net30)}} (30d) | ${{formatNumber(metrics.churn30)}} churn</span>
                    </div>
                </div>
                `;
            }}).join('');
            legend.innerHTML = controls + items;

            legend.querySelectorAll('[data-action]').forEach((btn) => {{
                btn.addEventListener('click', () => {{
                    const action = btn.getAttribute('data-action');
                    if (action === 'all') {{
                        selectionByProject[projectName] = new Set(data.categoryList);
                    }} else if (action === 'none') {{
                        selectionByProject[projectName] = new Set();
                    }}
                    applySelection(projectName);
                }});
            }});

            legend.querySelectorAll('[data-cat]').forEach((item) => {{
                item.addEventListener('click', () => {{
                    const cat = item.getAttribute('data-cat');
                    if (!cat) return;
                    const selected = ensureSelection(projectName);
                    if (selected.has(cat)) {{
                        selected.delete(cat);
                    }} else {{
                        selected.add(cat);
                    }}
                    applySelection(projectName);
                }});
            }});
        }}

        function updateFilterBar(projectName, selectedSet) {{
            const data = projectData[projectName];
            const filterBar = document.getElementById('filter-bar');
            if (!data || !filterBar) return;
            const label = projectName === '{AGGREGATE_PROJECT}' ? 'Projects' : 'Categories';

            const controls = `
                <span class="filter-label">${{label}}</span>
                <button class="filter-pill active" data-action="all">All</button>
                <button class="filter-pill" data-action="none">None</button>
            `;

            const items = data.categoryList.map(cat => {{
                const active = selectedSet.has(cat);
                const metrics = categorySummary(data, cat);
                const netSign = metrics.net30 >= 0 ? '+' : '';
                return `
                <button class="filter-pill ${{active ? 'active' : 'inactive'}}" data-cat="${{cat}}" style="--pill-color: ${{data.colors[cat]}}">
                    ${{cat}}
                    <span>${{netSign}}${{formatNumber(metrics.net30)}} | ${{formatNumber(metrics.churn30)}} churn</span>
                </button>
                `;
            }}).join('');

            filterBar.innerHTML = controls + items;

            filterBar.querySelectorAll('[data-action]').forEach((btn) => {{
                btn.addEventListener('click', () => {{
                    const action = btn.getAttribute('data-action');
                    if (action === 'all') {{
                        selectionByProject[projectName] = new Set(data.categoryList);
                    }} else if (action === 'none') {{
                        selectionByProject[projectName] = new Set();
                    }}
                    applySelection(projectName);
                }});
            }});

            filterBar.querySelectorAll('[data-cat]').forEach((item) => {{
                item.addEventListener('click', () => {{
                    const cat = item.getAttribute('data-cat');
                    if (!cat) return;
                    const selected = ensureSelection(projectName);
                    if (selected.has(cat)) {{
                        selected.delete(cat);
                    }} else {{
                        selected.add(cat);
                    }}
                    applySelection(projectName);
                }});
            }});
        }}

        function updateRangeIndicator(projectName, selectedSet) {{
            const summary = computeSummary(projectName, selectedSet);
            const fallback = projectSummaries[projectName];
            const indicator = document.getElementById('range-indicator');
            const start = summary.activityStart || fallback.firstDate;
            const end = summary.activityEnd || fallback.lastDate;
            if (start && end) {{
                const scope = summary.categoriesCount === summary.categoriesTotal ? 'full' : 'selection';
                indicator.innerHTML = `<span>${{start}}</span> -> <span>${{end}}</span> | ${{scope}}`;
            }} else {{
                indicator.textContent = 'No activity range available';
            }}
        }}

        function syncLegendSelection(projectName, selectedSet) {{
            const data = projectData[projectName];
            if (!data) return;
            suppressLegendEvents = true;
            data.categoryList.forEach((cat) => {{
                const action = selectedSet.has(cat) ? 'legendSelect' : 'legendUnSelect';
                if (charts.growth) {{
                    charts.growth.dispatchAction({{ type: action, name: cat }});
                }}
                if (charts.churn) {{
                    charts.churn.dispatchAction({{ type: action, name: cat }});
                }}
            }});
            suppressLegendEvents = false;
        }}

        function updateInsightPanels(projectName, selectedSet, dailyMetrics, summary, weekdayProfile) {{
            const insightEl = document.getElementById('insight-cards');
            const rhythmEl = document.getElementById('rhythm-cards');

            const totalDays = dates.length;
            const activeDays = dailyMetrics.activeSeries.reduce((a, b) => a + b, 0);
            const totalNet = dailyMetrics.netSeries.reduce((a, b) => a + b, 0);
            const totalChurn = dailyMetrics.churnSeries.reduce((a, b) => a + b, 0);
            const streaks = computeStreaks(dailyMetrics.commitSeries);

            let lastActiveIndex = -1;
            for (let i = dailyMetrics.commitSeries.length - 1; i >= 0; i--) {{
                if (dailyMetrics.commitSeries[i] > 0) {{
                    lastActiveIndex = i;
                    break;
                }}
            }}
            let daysSince = null;
            if (lastActiveIndex >= 0) {{
                const lastDate = parseDate(dates[dates.length - 1]);
                const activeDate = parseDate(dates[lastActiveIndex]);
                daysSince = Math.round((lastDate - activeDate) / 86400000);
            }}

            const activeRatio = totalDays ? (activeDays / totalDays) * 100 : 0;
            const avgNetActive = activeDays ? totalNet / activeDays : 0;
            const avgChurnActive = activeDays ? totalChurn / activeDays : 0;
            const churnRatio = summary.recentNet !== 0
                ? summary.recentChurn / Math.abs(summary.recentNet)
                : null;

            if (insightEl) {{
                insightEl.innerHTML = `
                    <div class="insight-card">
                        <div class="insight-label">Current Streak</div>
                        <div class="insight-value">${{streaks.current}}d</div>
                        <div class="insight-meta">Longest: ${{streaks.longest}}d</div>
                    </div>
                    <div class="insight-card">
                        <div class="insight-label">Days Since Active</div>
                        <div class="insight-value">${{daysSince === null ? 'n/a' : daysSince + 'd'}}</div>
                        <div class="insight-meta">Last active: ${{lastActiveIndex >= 0 ? dates[lastActiveIndex] : 'n/a'}}</div>
                    </div>
                    <div class="insight-card">
                        <div class="insight-label">Active Ratio</div>
                        <div class="insight-value">${{formatDecimal(activeRatio, 1)}}%</div>
                        <div class="insight-meta">${{activeDays}} active days</div>
                    </div>
                    <div class="insight-card">
                        <div class="insight-label">Net / Active Day</div>
                        <div class="insight-value">${{formatAxisValue(avgNetActive)}}</div>
                        <div class="insight-meta">All-time average</div>
                    </div>
                    <div class="insight-card">
                        <div class="insight-label">Churn / Active Day</div>
                        <div class="insight-value">${{formatNumber(Math.round(avgChurnActive))}}</div>
                        <div class="insight-meta">All-time average</div>
                    </div>
                    <div class="insight-card">
                        <div class="insight-label">Churn:Net (30d)</div>
                        <div class="insight-value">${{churnRatio === null ? 'n/a' : formatDecimal(churnRatio, 2)}}</div>
                        <div class="insight-meta">${{summary.recentDays}}-day window</div>
                    </div>
                `;
            }}

            if (rhythmEl) {{
                const maxCommits = Math.max(...weekdayProfile.avgCommits, 0);
                const peakIndex = weekdayProfile.avgCommits.indexOf(maxCommits);
                const peakLabel = peakIndex >= 0 ? weekdayProfile.labels[peakIndex] : 'n/a';
                const medianChurn = median(dailyMetrics.churnSeries);
                const medianNet = median(dailyMetrics.netSeries);
                const avgCommits = totalDays ? dailyMetrics.commitSeries.reduce((a, b) => a + b, 0) / totalDays : 0;

                rhythmEl.innerHTML = `
                    <div class="insight-card">
                        <div class="insight-label">Peak Weekday</div>
                        <div class="insight-value">${{peakLabel}}</div>
                        <div class="insight-meta">${{formatDecimal(maxCommits, 2)}} commits avg</div>
                    </div>
                    <div class="insight-card">
                        <div class="insight-label">Avg Commits / Day</div>
                        <div class="insight-value">${{formatDecimal(avgCommits, 2)}}</div>
                        <div class="insight-meta">All days in range</div>
                    </div>
                    <div class="insight-card">
                        <div class="insight-label">Median Daily Net</div>
                        <div class="insight-value">${{formatAxisValue(medianNet)}}</div>
                        <div class="insight-meta">Daily net median</div>
                    </div>
                    <div class="insight-card">
                        <div class="insight-label">Median Daily Churn</div>
                        <div class="insight-value">${{formatNumber(Math.round(medianChurn))}}</div>
                        <div class="insight-meta">Daily churn median</div>
                    </div>
                    <div class="insight-card">
                        <div class="insight-label">Active Days (30d)</div>
                        <div class="insight-value">${{summary.recentActiveDays}}</div>
                        <div class="insight-meta">${{summary.recentDays}}-day window</div>
                    </div>
                    <div class="insight-card">
                        <div class="insight-label">Span</div>
                        <div class="insight-value">${{summary.activityStart || 'n/a'}}</div>
                        <div class="insight-meta">-> ${{summary.activityEnd || 'n/a'}}</div>
                    </div>
                `;
            }}
        }}

        function updateDeepDive(projectName, selectedSet) {{
            const data = projectData[projectName];
            if (!data) return;

            const dailyMetrics = computeDailyMetrics(projectName, selectedSet);
            const netAvg7 = movingAverage(dailyMetrics.netSeries, 7);
            const netAvg30 = movingAverage(dailyMetrics.netSeries, 30);
            const commitAvg7 = movingAverage(dailyMetrics.commitSeries, 7);
            const weekdayProfile = computeWeekdayProfile(dailyMetrics);
            const mix = computeMix(projectName, selectedSet);
            const authorStats = computeAuthorStats(projectName, selectedSet);
            const summary = computeSummary(projectName, selectedSet);

            const selectedEvents = [];
            for (const day of dates) {{
                const events = data.events[day] || [];
                for (const ev of events) {{
                    if (eventMatchesSelection(ev, selectedSet)) {{
                        selectedEvents.push(ev);
                    }}
                }}
            }}

            updateInsightPanels(projectName, selectedSet, dailyMetrics, summary, weekdayProfile);

            const topDays = computeTopDays(dailyMetrics, 8);
            renderTable(
                document.getElementById('top-days-table'),
                ['Date', 'Churn', 'Net', 'Commits'],
                topDays.map((row) => [
                    row.date,
                    formatNumber(row.churn),
                    formatAxisValue(row.net),
                    String(row.commits)
                ])
            );

            renderTable(
                document.getElementById('mix-table'),
                ['Category', 'Total LOC', 'Net (30d)', 'Churn (30d)'],
                mix.rows.map((row) => [
                    row.name,
                    formatNumber(row.totalLoc),
                    formatAxisValue(row.net30),
                    formatNumber(row.churn30)
                ])
            );

            renderTable(
                document.getElementById('author-table'),
                ['Author', 'Commits', 'Net', 'Churn', 'Active Days'],
                authorStats.slice(0, 10).map((row) => [
                    row.name,
                    String(row.commits),
                    formatAxisValue(row.net),
                    formatNumber(row.churn),
                    String(row.activeDays)
                ])
            );

            const rankRows = [...mix.rows].sort((a, b) => b.net30 - a.net30);
            renderTable(
                document.getElementById('compare-table'),
                ['Category', 'Total LOC', 'Net (30d)', 'Churn (30d)'],
                rankRows.map((row) => [
                    row.name,
                    formatNumber(row.totalLoc),
                    formatAxisValue(row.net30),
                    formatNumber(row.churn30)
                ])
            );

            const fileRows = (data.files || []).slice(0, 12);
            renderTable(
                document.getElementById('file-hotspot-table'),
                ['File', 'Churn', 'Net', 'Volatility'],
                fileRows.map((row) => [
                    row.name,
                    formatNumber(row.churn),
                    formatAxisValue(row.net),
                    formatDecimal(row.volatility, 2)
                ])
            );

            const moduleRows = (data.modules || []).slice(0, 12);
            renderTable(
                document.getElementById('module-hotspot-table'),
                ['Module', 'Churn', 'Net', 'Volatility'],
                moduleRows.map((row) => [
                    row.name,
                    formatNumber(row.churn),
                    formatAxisValue(row.net),
                    formatDecimal(row.volatility, 2)
                ])
            );

            const ownerRows = (data.owners || []).slice(0, 12);
            renderTable(
                document.getElementById('ownership-table'),
                ['Module', 'Top Author', 'Share', 'Commits'],
                ownerRows.map((row) => [
                    row.module,
                    row.author,
                    `${{Math.round(row.share * 100)}}%`,
                    String(row.commits)
                ])
            );

            if (charts.net) {{
                charts.net.setOption({{
                    tooltip: {{
                        trigger: 'axis',
                        backgroundColor: 'rgba(13, 15, 20, 0.95)',
                        borderColor: '#2d3344',
                        borderWidth: 1,
                        padding: scaledArray([10, 12]),
                        textStyle: {{
                            color: '#e4e8f1',
                            fontFamily: 'JetBrains Mono',
                            fontSize: scaled(11)
                        }},
                        extraCssText: 'border-radius: 8px;'
                    }},
                    legend: {{
                        data: ['Net', 'Net 7d', 'Net 30d'],
                        top: scaled(6),
                        right: scaled(12),
                        textStyle: {{
                            color: '#9ba3b8',
                            fontFamily: 'JetBrains Mono',
                            fontSize: scaled(10)
                        }}
                    }},
                    grid: {{
                        left: scaled(10),
                        right: scaled(16),
                        top: scaled(40),
                        bottom: scaled(24),
                        containLabel: true
                    }},
                    xAxis: {{
                        type: 'category',
                        data: dates,
                        axisLabel: {{
                            color: '#6b7280',
                            fontFamily: 'JetBrains Mono',
                            fontSize: scaled(9)
                        }},
                        axisLine: {{ lineStyle: {{ color: '#2d3344' }} }},
                        axisTick: {{ show: false }}
                    }},
                    yAxis: {{
                        type: 'value',
                        axisLabel: {{
                            color: '#6b7280',
                            fontFamily: 'JetBrains Mono',
                            fontSize: scaled(9),
                            formatter: formatAxisValue
                        }},
                        splitLine: {{
                            lineStyle: {{
                                color: '#1a1e28',
                                type: 'dashed'
                            }}
                        }}
                    }},
                    series: [
                        {{
                            name: 'Net',
                            type: 'bar',
                            data: dailyMetrics.netSeries,
                            barMaxWidth: scaled(6),
                            itemStyle: {{
                                color: (params) => params.value >= 0 ? '#22c55e' : '#fb7185'
                            }}
                        }},
                        {{
                            name: 'Net 7d',
                            type: 'line',
                            data: netAvg7,
                            smooth: 0.4,
                            symbol: 'none',
                            lineStyle: {{
                                color: '#22d3ee',
                                width: scaled(2)
                            }}
                        }},
                        {{
                            name: 'Net 30d',
                            type: 'line',
                            data: netAvg30,
                            smooth: 0.4,
                            symbol: 'none',
                            lineStyle: {{
                                color: '#fbbf24',
                                width: scaled(2)
                            }}
                        }}
                    ]
                }}, true);
            }}

            if (charts.commit) {{
                charts.commit.setOption({{
                    tooltip: {{
                        trigger: 'axis',
                        backgroundColor: 'rgba(13, 15, 20, 0.95)',
                        borderColor: '#2d3344',
                        borderWidth: 1,
                        padding: scaledArray([10, 12]),
                        textStyle: {{
                            color: '#e4e8f1',
                            fontFamily: 'JetBrains Mono',
                            fontSize: scaled(11)
                        }},
                        extraCssText: 'border-radius: 8px;'
                    }},
                    legend: {{
                        data: ['Commits', 'Commits 7d'],
                        top: scaled(6),
                        right: scaled(12),
                        textStyle: {{
                            color: '#9ba3b8',
                            fontFamily: 'JetBrains Mono',
                            fontSize: scaled(10)
                        }}
                    }},
                    grid: {{
                        left: scaled(10),
                        right: scaled(16),
                        top: scaled(40),
                        bottom: scaled(24),
                        containLabel: true
                    }},
                    xAxis: {{
                        type: 'category',
                        data: dates,
                        axisLabel: {{ show: false }},
                        axisLine: {{ lineStyle: {{ color: '#2d3344' }} }},
                        axisTick: {{ show: false }}
                    }},
                    yAxis: {{
                        type: 'value',
                        axisLabel: {{
                            color: '#6b7280',
                            fontFamily: 'JetBrains Mono',
                            fontSize: scaled(9)
                        }},
                        splitLine: {{
                            lineStyle: {{
                                color: '#1a1e28',
                                type: 'dashed'
                            }}
                        }}
                    }},
                    series: [
                        {{
                            name: 'Commits',
                            type: 'bar',
                            data: dailyMetrics.commitSeries,
                            barMaxWidth: scaled(6),
                            itemStyle: {{
                                color: '#4ade80'
                            }}
                        }},
                        {{
                            name: 'Commits 7d',
                            type: 'line',
                            data: commitAvg7,
                            smooth: 0.35,
                            symbol: 'none',
                            lineStyle: {{
                                color: '#22d3ee',
                                width: scaled(2)
                            }}
                        }}
                    ]
                }}, true);
            }}

            if (charts.heatmap) {{
                const heatData = dates.map((date, i) => [date, dailyMetrics.churnSeries[i]]);
                const maxHeat = Math.max(...dailyMetrics.churnSeries, 0);
                charts.heatmap.setOption({{
                    tooltip: {{
                        position: 'top',
                        formatter: (params) => {{
                            const value = params.value ? params.value[1] : 0;
                            return `${{params.value[0]}}<br/>Churn: ${{formatNumber(value)}}`;
                        }},
                        backgroundColor: 'rgba(13, 15, 20, 0.95)',
                        borderColor: '#2d3344',
                        borderWidth: 1,
                        textStyle: {{
                            color: '#e4e8f1',
                            fontFamily: 'JetBrains Mono',
                            fontSize: scaled(11)
                        }}
                    }},
                    visualMap: {{
                        min: 0,
                        max: maxHeat || 1,
                        orient: 'horizontal',
                        left: 'center',
                        bottom: 0,
                        textStyle: {{
                            color: '#9ba3b8',
                            fontFamily: 'JetBrains Mono',
                            fontSize: scaled(9)
                        }},
                        inRange: {{
                            color: ['#0f172a', '#22c55e']
                        }}
                    }},
                    calendar: {{
                        top: scaled(20),
                        left: scaled(20),
                        right: scaled(20),
                        cellSize: [scaled(14), scaled(14)],
                        range: [dates[0], dates[dates.length - 1]],
                        yearLabel: {{ show: false }},
                        dayLabel: {{
                            color: '#6b7280',
                            fontFamily: 'JetBrains Mono',
                            fontSize: scaled(9)
                        }},
                        monthLabel: {{
                            color: '#9ba3b8',
                            fontFamily: 'JetBrains Mono',
                            fontSize: scaled(9)
                        }},
                        itemStyle: {{
                            color: '#10141c',
                            borderColor: '#1f2937'
                        }}
                    }},
                    series: [
                        {{
                            type: 'heatmap',
                            coordinateSystem: 'calendar',
                            data: heatData
                        }}
                    ]
                }}, true);
            }}

            if (charts.weekday) {{
                charts.weekday.setOption({{
                    tooltip: {{
                        trigger: 'axis',
                        backgroundColor: 'rgba(13, 15, 20, 0.95)',
                        borderColor: '#2d3344',
                        borderWidth: 1,
                        textStyle: {{
                            color: '#e4e8f1',
                            fontFamily: 'JetBrains Mono',
                            fontSize: scaled(11)
                        }}
                    }},
                    legend: {{
                        data: ['Commits', 'Churn'],
                        top: scaled(6),
                        right: scaled(12),
                        textStyle: {{
                            color: '#9ba3b8',
                            fontFamily: 'JetBrains Mono',
                            fontSize: scaled(10)
                        }}
                    }},
                    grid: {{
                        left: scaled(8),
                        right: scaled(10),
                        top: scaled(40),
                        bottom: scaled(24),
                        containLabel: true
                    }},
                    xAxis: {{
                        type: 'category',
                        data: weekdayProfile.labels,
                        axisLabel: {{
                            color: '#6b7280',
                            fontFamily: 'JetBrains Mono',
                            fontSize: scaled(9)
                        }},
                        axisLine: {{ lineStyle: {{ color: '#2d3344' }} }},
                        axisTick: {{ show: false }}
                    }},
                    yAxis: [
                        {{
                            type: 'value',
                            axisLabel: {{
                                color: '#6b7280',
                                fontFamily: 'JetBrains Mono',
                                fontSize: scaled(9)
                            }},
                            splitLine: {{
                                lineStyle: {{
                                    color: '#1a1e28',
                                    type: 'dashed'
                                }}
                            }}
                        }},
                        {{
                            type: 'value',
                            axisLabel: {{
                                color: '#6b7280',
                                fontFamily: 'JetBrains Mono',
                                fontSize: scaled(9)
                            }},
                            splitLine: {{ show: false }}
                        }}
                    ],
                    series: [
                        {{
                            name: 'Commits',
                            type: 'bar',
                            data: weekdayProfile.avgCommits,
                            barMaxWidth: scaled(12),
                            itemStyle: {{ color: '#4ade80' }}
                        }},
                        {{
                            name: 'Churn',
                            type: 'line',
                            yAxisIndex: 1,
                            data: weekdayProfile.avgChurn,
                            smooth: 0.4,
                            symbol: 'none',
                            lineStyle: {{ color: '#f97316', width: scaled(2) }}
                        }}
                    ]
                }}, true);
            }}

            if (charts.share) {{
                const totalLocData = mix.rows.map((row) => ({{
                    name: row.name,
                    value: row.totalLoc,
                    itemStyle: {{ color: data.colors[row.name] }}
                }}));
                const churnData = mix.rows.map((row) => ({{
                    name: row.name,
                    value: row.churn30,
                    itemStyle: {{ color: data.colors[row.name] }}
                }}));
                charts.share.setOption({{
                    tooltip: {{
                        trigger: 'item',
                        backgroundColor: 'rgba(13, 15, 20, 0.95)',
                        borderColor: '#2d3344',
                        borderWidth: 1,
                        textStyle: {{
                            color: '#e4e8f1',
                            fontFamily: 'JetBrains Mono',
                            fontSize: scaled(11)
                        }}
                    }},
                    legend: {{
                        type: 'scroll',
                        bottom: 0,
                        textStyle: {{
                            color: '#9ba3b8',
                            fontFamily: 'JetBrains Mono',
                            fontSize: scaled(9)
                        }}
                    }},
                    series: [
                        {{
                            name: 'Total LOC',
                            type: 'pie',
                            radius: ['40%', '62%'],
                            center: ['30%', '45%'],
                            label: {{ show: false }},
                            data: totalLocData
                        }},
                        {{
                            name: '30d Churn',
                            type: 'pie',
                            radius: ['40%', '62%'],
                            center: ['72%', '45%'],
                            label: {{ show: false }},
                            data: churnData
                        }}
                    ]
                }}, true);
            }}

            if (charts.momentum) {{
                const momentumRows = mix.rows;
                const categories = momentumRows.map((row) => row.name).reverse();
                const netData = momentumRows.map((row) => row.net30).reverse();
                const churnData = momentumRows.map((row) => row.churn30).reverse();
                charts.momentum.setOption({{
                    tooltip: {{
                        trigger: 'axis',
                        axisPointer: {{ type: 'shadow' }},
                        backgroundColor: 'rgba(13, 15, 20, 0.95)',
                        borderColor: '#2d3344',
                        borderWidth: 1,
                        textStyle: {{
                            color: '#e4e8f1',
                            fontFamily: 'JetBrains Mono',
                            fontSize: scaled(11)
                        }}
                    }},
                    legend: {{
                        data: ['Net (30d)', 'Churn (30d)'],
                        top: scaled(6),
                        right: scaled(12),
                        textStyle: {{
                            color: '#9ba3b8',
                            fontFamily: 'JetBrains Mono',
                            fontSize: scaled(9)
                        }}
                    }},
                    grid: {{
                        left: scaled(12),
                        right: scaled(16),
                        top: scaled(40),
                        bottom: scaled(20),
                        containLabel: true
                    }},
                    xAxis: {{
                        type: 'value',
                        axisLabel: {{
                            color: '#6b7280',
                            fontFamily: 'JetBrains Mono',
                            fontSize: scaled(9),
                            formatter: formatAxisValue
                        }},
                        splitLine: {{
                            lineStyle: {{
                                color: '#1a1e28',
                                type: 'dashed'
                            }}
                        }}
                    }},
                    yAxis: {{
                        type: 'category',
                        data: categories,
                        axisLabel: {{
                            color: '#9ba3b8',
                            fontFamily: 'JetBrains Mono',
                            fontSize: scaled(9)
                        }},
                        axisLine: {{ lineStyle: {{ color: '#2d3344' }} }}
                    }},
                    series: [
                        {{
                            name: 'Net (30d)',
                            type: 'bar',
                            data: netData,
                            itemStyle: {{
                                color: (params) => params.value >= 0 ? '#22c55e' : '#fb7185'
                            }}
                        }},
                        {{
                            name: 'Churn (30d)',
                            type: 'bar',
                            data: churnData,
                            itemStyle: {{
                                color: '#38bdf8'
                            }}
                        }}
                    ]
                }}, true);
            }}

            if (charts.author) {{
                const topAuthors = authorStats.slice(0, 8);
                charts.author.setOption({{
                    tooltip: {{
                        trigger: 'axis',
                        axisPointer: {{ type: 'shadow' }},
                        backgroundColor: 'rgba(13, 15, 20, 0.95)',
                        borderColor: '#2d3344',
                        borderWidth: 1,
                        textStyle: {{
                            color: '#e4e8f1',
                            fontFamily: 'JetBrains Mono',
                            fontSize: scaled(11)
                        }}
                    }},
                    grid: {{
                        left: scaled(12),
                        right: scaled(12),
                        top: scaled(20),
                        bottom: scaled(20),
                        containLabel: true
                    }},
                    xAxis: {{
                        type: 'value',
                        axisLabel: {{
                            color: '#6b7280',
                            fontFamily: 'JetBrains Mono',
                            fontSize: scaled(9)
                        }},
                        splitLine: {{
                            lineStyle: {{
                                color: '#1a1e28',
                                type: 'dashed'
                            }}
                        }}
                    }},
                    yAxis: {{
                        type: 'category',
                        data: topAuthors.map((row) => row.name).reverse(),
                        axisLabel: {{
                            color: '#9ba3b8',
                            fontFamily: 'JetBrains Mono',
                            fontSize: scaled(9)
                        }}
                    }},
                    series: [
                        {{
                            name: 'Commits',
                            type: 'bar',
                            data: topAuthors.map((row) => row.commits).reverse(),
                            itemStyle: {{
                                color: '#f97316'
                            }}
                        }}
                    ]
                }}, true);
            }}

            if (charts.compare) {{
                const compareRows = mix.rows.slice(0, 12);
                const categories = compareRows.map((row) => row.name).reverse();
                const totalLocData = compareRows.map((row) => row.totalLoc).reverse();
                const net30Data = compareRows.map((row) => row.net30).reverse();
                charts.compare.setOption({{
                    tooltip: {{
                        trigger: 'axis',
                        axisPointer: {{ type: 'shadow' }},
                        backgroundColor: 'rgba(13, 15, 20, 0.95)',
                        borderColor: '#2d3344',
                        borderWidth: 1,
                        textStyle: {{
                            color: '#e4e8f1',
                            fontFamily: 'JetBrains Mono',
                            fontSize: scaled(11)
                        }},
                        formatter: (params) => {{
                            const total = params.find((p) => p.seriesName === 'Total LOC');
                            const net = params.find((p) => p.seriesName === 'Net (30d)');
                            return `${{params[0].name}}<br/>Total: ${{formatNumber(total.value)}}<br/>Net 30d: ${{formatAxisValue(net.value)}}`;
                        }}
                    }},
                    legend: {{
                        data: ['Total LOC', 'Net (30d)'],
                        top: scaled(6),
                        right: scaled(12),
                        textStyle: {{
                            color: '#9ba3b8',
                            fontFamily: 'JetBrains Mono',
                            fontSize: scaled(9)
                        }}
                    }},
                    grid: {{
                        left: scaled(12),
                        right: scaled(16),
                        top: scaled(40),
                        bottom: scaled(20),
                        containLabel: true
                    }},
                    xAxis: [
                        {{
                            type: 'value',
                            axisLabel: {{
                                color: '#6b7280',
                                fontFamily: 'JetBrains Mono',
                                fontSize: scaled(9),
                                formatter: formatAxisValue
                            }},
                            splitLine: {{
                                lineStyle: {{
                                    color: '#1a1e28',
                                    type: 'dashed'
                                }}
                            }}
                        }},
                        {{
                            type: 'value',
                            axisLabel: {{
                                color: '#6b7280',
                                fontFamily: 'JetBrains Mono',
                                fontSize: scaled(9),
                                formatter: formatAxisValue
                            }},
                            splitLine: {{ show: false }}
                        }}
                    ],
                    yAxis: {{
                        type: 'category',
                        data: categories,
                        axisLabel: {{
                            color: '#9ba3b8',
                            fontFamily: 'JetBrains Mono',
                            fontSize: scaled(9)
                        }}
                    }},
                    series: [
                        {{
                            name: 'Total LOC',
                            type: 'bar',
                            data: totalLocData,
                            itemStyle: {{ color: '#38bdf8' }}
                        }},
                        {{
                            name: 'Net (30d)',
                            type: 'bar',
                            xAxisIndex: 1,
                            data: net30Data,
                            itemStyle: {{
                                color: (params) => params.value >= 0 ? '#22c55e' : '#fb7185'
                            }}
                        }}
                    ]
                }}, true);
            }}

            if (charts.size) {{
                const sizeBuckets = [
                    {{ label: '0-9', min: 0, max: 9 }},
                    {{ label: '10-49', min: 10, max: 49 }},
                    {{ label: '50-199', min: 50, max: 199 }},
                    {{ label: '200-499', min: 200, max: 499 }},
                    {{ label: '500-999', min: 500, max: 999 }},
                    {{ label: '1k-2k', min: 1000, max: 1999 }},
                    {{ label: '2k+', min: 2000, max: Infinity }}
                ];
                const sizeCounts = new Array(sizeBuckets.length).fill(0);
                for (const ev of selectedEvents) {{
                    const size = (ev['+'] || 0) + (ev['-'] || 0);
                    for (let i = 0; i < sizeBuckets.length; i++) {{
                        const bucket = sizeBuckets[i];
                        if (size >= bucket.min && size <= bucket.max) {{
                            sizeCounts[i] += 1;
                            break;
                        }}
                    }}
                }}
                charts.size.setOption({{
                    tooltip: {{
                        trigger: 'axis',
                        backgroundColor: 'rgba(13, 15, 20, 0.95)',
                        borderColor: '#2d3344',
                        borderWidth: 1,
                        textStyle: {{
                            color: '#e4e8f1',
                            fontFamily: 'JetBrains Mono',
                            fontSize: scaled(11)
                        }}
                    }},
                    grid: {{
                        left: scaled(10),
                        right: scaled(12),
                        top: scaled(20),
                        bottom: scaled(30),
                        containLabel: true
                    }},
                    xAxis: {{
                        type: 'category',
                        data: sizeBuckets.map((b) => b.label),
                        axisLabel: {{
                            color: '#6b7280',
                            fontFamily: 'JetBrains Mono',
                            fontSize: scaled(9)
                        }},
                        axisLine: {{ lineStyle: {{ color: '#2d3344' }} }},
                        axisTick: {{ show: false }}
                    }},
                    yAxis: {{
                        type: 'value',
                        axisLabel: {{
                            color: '#6b7280',
                            fontFamily: 'JetBrains Mono',
                            fontSize: scaled(9)
                        }},
                        splitLine: {{
                            lineStyle: {{
                                color: '#1a1e28',
                                type: 'dashed'
                            }}
                        }}
                    }},
                    series: [
                        {{
                            type: 'bar',
                            data: sizeCounts,
                            barMaxWidth: scaled(18),
                            itemStyle: {{
                                color: '#38bdf8'
                            }}
                        }}
                    ]
                }}, true);
            }}

            if (charts.timeofday) {{
                const weekdayLabels = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
                const hourLabels = Array.from({{ length: 24 }}, (_, i) => String(i).padStart(2, '0'));
                const heat = Array.from({{ length: 7 }}, () => Array(24).fill(0));

                for (const ev of selectedEvents) {{
                    if (!ev.t) continue;
                    const d = new Date(ev.t);
                    if (Number.isNaN(d.getTime())) continue;
                    const hour = d.getHours();
                    const dayIndex = (d.getDay() + 6) % 7;
                    heat[dayIndex][hour] += 1;
                }}

                const heatData = [];
                let maxHeat = 0;
                for (let day = 0; day < 7; day++) {{
                    for (let hour = 0; hour < 24; hour++) {{
                        const value = heat[day][hour];
                        maxHeat = Math.max(maxHeat, value);
                        heatData.push([hour, day, value]);
                    }}
                }}

                charts.timeofday.setOption({{
                    tooltip: {{
                        position: 'top',
                        formatter: (params) => {{
                            const hour = hourLabels[params.value[0]];
                            const day = weekdayLabels[params.value[1]];
                            return `${{day}} ${{hour}}:00<br/>Commits: ${{params.value[2]}}`;
                        }},
                        backgroundColor: 'rgba(13, 15, 20, 0.95)',
                        borderColor: '#2d3344',
                        borderWidth: 1,
                        textStyle: {{
                            color: '#e4e8f1',
                            fontFamily: 'JetBrains Mono',
                            fontSize: scaled(11)
                        }}
                    }},
                    grid: {{
                        left: scaled(10),
                        right: scaled(10),
                        top: scaled(20),
                        bottom: scaled(10),
                        containLabel: true
                    }},
                    xAxis: {{
                        type: 'category',
                        data: hourLabels,
                        axisLabel: {{
                            color: '#6b7280',
                            fontFamily: 'JetBrains Mono',
                            fontSize: scaled(8)
                        }},
                        axisLine: {{ lineStyle: {{ color: '#2d3344' }} }},
                        axisTick: {{ show: false }}
                    }},
                    yAxis: {{
                        type: 'category',
                        data: weekdayLabels,
                        axisLabel: {{
                            color: '#6b7280',
                            fontFamily: 'JetBrains Mono',
                            fontSize: scaled(9)
                        }},
                        axisLine: {{ lineStyle: {{ color: '#2d3344' }} }},
                        axisTick: {{ show: false }}
                    }},
                    visualMap: {{
                        min: 0,
                        max: maxHeat || 1,
                        calculable: false,
                        orient: 'horizontal',
                        left: 'center',
                        bottom: 0,
                        textStyle: {{
                            color: '#9ba3b8',
                            fontFamily: 'JetBrains Mono',
                            fontSize: scaled(9)
                        }},
                        inRange: {{
                            color: ['#0f172a', '#22c55e']
                        }}
                    }},
                    series: [
                        {{
                            type: 'heatmap',
                            data: heatData,
                            emphasis: {{
                                itemStyle: {{
                                    borderColor: '#22d3ee',
                                    borderWidth: 1
                                }}
                            }}
                        }}
                    ]
                }}, true);
            }}

            if (charts.merge) {{
                const mergeCounts = [];
                const mergeRatio = [];
                const mergeFanIn = [];
                for (const day of dates) {{
                    const events = data.events[day] || [];
                    let merges = 0;
                    let parentTotal = 0;
                    for (const ev of events) {{
                        const parents = ev.p || 1;
                        if (parents > 1) {{
                            merges += 1;
                            parentTotal += parents;
                        }}
                    }}
                    mergeCounts.push(merges);
                    mergeRatio.push(events.length ? merges / events.length : 0);
                    mergeFanIn.push(merges ? parentTotal / merges : 0);
                }}

                charts.merge.setOption({{
                    tooltip: {{
                        trigger: 'axis',
                        backgroundColor: 'rgba(13, 15, 20, 0.95)',
                        borderColor: '#2d3344',
                        borderWidth: 1,
                        textStyle: {{
                            color: '#e4e8f1',
                            fontFamily: 'JetBrains Mono',
                            fontSize: scaled(11)
                        }}
                    }},
                    legend: {{
                        data: ['Merges', 'Merge Ratio', 'Avg Parents'],
                        top: scaled(6),
                        right: scaled(12),
                        textStyle: {{
                            color: '#9ba3b8',
                            fontFamily: 'JetBrains Mono',
                            fontSize: scaled(9)
                        }}
                    }},
                    grid: {{
                        left: scaled(10),
                        right: scaled(20),
                        top: scaled(40),
                        bottom: scaled(24),
                        containLabel: true
                    }},
                    xAxis: {{
                        type: 'category',
                        data: dates,
                        axisLabel: {{ show: false }},
                        axisLine: {{ lineStyle: {{ color: '#2d3344' }} }},
                        axisTick: {{ show: false }}
                    }},
                    yAxis: [
                        {{
                            type: 'value',
                            axisLabel: {{
                                color: '#6b7280',
                                fontFamily: 'JetBrains Mono',
                                fontSize: scaled(9)
                            }},
                            splitLine: {{
                                lineStyle: {{
                                    color: '#1a1e28',
                                    type: 'dashed'
                                }}
                            }}
                        }},
                        {{
                            type: 'value',
                            axisLabel: {{
                                color: '#6b7280',
                                fontFamily: 'JetBrains Mono',
                                fontSize: scaled(9),
                                formatter: (value) => `${{Math.round(value * 100)}}%`
                            }},
                            splitLine: {{ show: false }}
                        }}
                    ],
                    series: [
                        {{
                            name: 'Merges',
                            type: 'bar',
                            data: mergeCounts,
                            barMaxWidth: scaled(8),
                            itemStyle: {{ color: '#a78bfa' }}
                        }},
                        {{
                            name: 'Merge Ratio',
                            type: 'line',
                            yAxisIndex: 1,
                            data: mergeRatio,
                            smooth: 0.4,
                            symbol: 'none',
                            lineStyle: {{ color: '#fbbf24', width: scaled(2) }}
                        }},
                        {{
                            name: 'Avg Parents',
                            type: 'line',
                            yAxisIndex: 1,
                            data: mergeFanIn,
                            smooth: 0.4,
                            symbol: 'none',
                            lineStyle: {{ color: '#22d3ee', width: scaled(2) }}
                        }}
                    ]
                }}, true);
            }}

            if (charts.tag) {{
                const tags = data.tags || [];
                const tagCounts = {{}};
                for (const tag of tags) {{
                    if (!tag.date) continue;
                    const date = parseDate(tag.date);
                    if (Number.isNaN(date.getTime())) continue;
                    const month = `${{date.getFullYear()}}-${{String(date.getMonth() + 1).padStart(2, '0')}}`;
                    tagCounts[month] = (tagCounts[month] || 0) + 1;
                }}
                const months = Object.keys(tagCounts).sort();
                const values = months.map((m) => tagCounts[m]);

                charts.tag.setOption({{
                    tooltip: {{
                        trigger: 'axis',
                        backgroundColor: 'rgba(13, 15, 20, 0.95)',
                        borderColor: '#2d3344',
                        borderWidth: 1,
                        textStyle: {{
                            color: '#e4e8f1',
                            fontFamily: 'JetBrains Mono',
                            fontSize: scaled(11)
                        }}
                    }},
                    grid: {{
                        left: scaled(10),
                        right: scaled(10),
                        top: scaled(20),
                        bottom: scaled(24),
                        containLabel: true
                    }},
                    xAxis: {{
                        type: 'category',
                        data: months,
                        axisLabel: {{
                            color: '#6b7280',
                            fontFamily: 'JetBrains Mono',
                            fontSize: scaled(8)
                        }},
                        axisLine: {{ lineStyle: {{ color: '#2d3344' }} }},
                        axisTick: {{ show: false }}
                    }},
                    yAxis: {{
                        type: 'value',
                        axisLabel: {{
                            color: '#6b7280',
                            fontFamily: 'JetBrains Mono',
                            fontSize: scaled(9)
                        }},
                        splitLine: {{
                            lineStyle: {{
                                color: '#1a1e28',
                                type: 'dashed'
                            }}
                        }}
                    }},
                    series: [
                        {{
                            type: 'bar',
                            data: values,
                            barMaxWidth: scaled(14),
                            itemStyle: {{ color: '#fbbf24' }}
                        }}
                    ]
                }}, true);
            }}

            if (charts.treemap) {{
                const treemapData = (data.modules || []).slice(0, 60).map((row) => ({{
                    name: row.name,
                    value: row.churn
                }}));
                charts.treemap.setOption({{
                    tooltip: {{
                        formatter: (params) => `${{params.name}}<br/>Churn: ${{formatNumber(params.value)}}`
                    }},
                    series: [
                        {{
                            type: 'treemap',
                            roam: false,
                            breadcrumb: {{ show: false }},
                            label: {{
                                show: true,
                                fontFamily: 'JetBrains Mono',
                                color: '#e4e8f1',
                                fontSize: scaled(10)
                            }},
                            upperLabel: {{
                                show: true,
                                height: scaled(18)
                            }},
                            itemStyle: {{
                                borderColor: '#12151c',
                                borderWidth: 1,
                                gapWidth: 2
                            }},
                            data: treemapData
                        }}
                    ]
                }}, true);
            }}

            if (charts.cochange) {{
                const cochange = data.cochange || {{ nodes: [], edges: [] }};
                charts.cochange.setOption({{
                    tooltip: {{
                        formatter: (params) => {{
                            if (params.dataType === 'edge') {{
                                return `${{params.data.source}} <-> ${{params.data.target}}<br/>Co-change: ${{params.data.value}}`;
                            }}
                            return `${{params.data.name}}<br/>Churn: ${{formatNumber(params.data.value || 0)}}`;
                        }},
                        backgroundColor: 'rgba(13, 15, 20, 0.95)',
                        borderColor: '#2d3344',
                        borderWidth: 1,
                        textStyle: {{
                            color: '#e4e8f1',
                            fontFamily: 'JetBrains Mono',
                            fontSize: scaled(11)
                        }}
                    }},
                    series: [
                        {{
                            type: 'graph',
                            layout: 'force',
                            roam: true,
                            data: cochange.nodes,
                            links: cochange.edges,
                            emphasis: {{
                                focus: 'adjacency'
                            }},
                            label: {{
                                show: true,
                                position: 'right',
                                fontFamily: 'JetBrains Mono',
                                color: '#e4e8f1',
                                fontSize: scaled(9)
                            }},
                            lineStyle: {{
                                color: '#38bdf8',
                                opacity: 0.4
                            }},
                            force: {{
                                repulsion: 120,
                                edgeLength: [30, 140]
                            }}
                        }}
                    ]
                }}, true);
            }}
        }}

        function applySelection(projectName) {{
            const selected = ensureSelection(projectName);
            const selectedMap = selectionMap(projectName, selected);
            if (charts.growth) {{
                charts.growth.setOption({{ legend: {{ selected: selectedMap }} }}, false);
            }}
            if (charts.churn) {{
                charts.churn.setOption({{ legend: {{ selected: selectedMap }} }}, false);
            }}
            syncLegendSelection(projectName, selected);
            updateStatsBar(projectName, selected);
            updateLegend(projectName, selected);
            updateFilterBar(projectName, selected);
            updateRangeIndicator(projectName, selected);
            updateDeepDive(projectName, selected);
            if (lastInspectorIndex !== null) {{
                renderedInspectorIndex = null;
                updateInspector(lastInspectorIndex);
            }}
        }}

        function updateCharts(projectName) {{
            const data = projectData[projectName];
            if (!data) return;

            const categories = data.categoryList;
            const catColors = data.colors;
            const selected = ensureSelection(projectName);
            const selectedMap = selectionMap(projectName, selected);
            renderedInspectorIndex = null;

            updateStatsBar(projectName, selected);
            updateLegend(projectName, selected);
            updateFilterBar(projectName, selected);
            updateRangeIndicator(projectName, selected);

            // Growth series (stacked area by category)
            const growthSeries = categories.map((cat, i) => ({{
                name: cat,
                type: 'line',
                stack: 'Total',
                smooth: 0.4,
                symbol: 'none',
                lineStyle: {{
                    width: 0
                }},
                areaStyle: {{
                    color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                        {{ offset: 0, color: catColors[cat] + 'aa' }},
                        {{ offset: 1, color: catColors[cat] + '22' }}
                    ])
                }},
                emphasis: {{
                    focus: 'series',
                    areaStyle: {{
                        opacity: 0.8
                    }}
                }},
                data: data.categories[cat].growth
            }}));

            const tags = data.tags || [];
            if (tags.length && projectName !== '{AGGREGATE_PROJECT}' && growthSeries.length) {{
                const tagLines = tags.slice(-12).map((tag) => ({{
                    xAxis: tag.date.split('T')[0],
                    label: {{ formatter: tag.name }}
                }}));
                growthSeries[0].markLine = {{
                    symbol: 'none',
                    label: {{
                        color: '#9ba3b8',
                        fontFamily: 'JetBrains Mono',
                        fontSize: scaled(9),
                        formatter: (params) => params?.data?.label?.formatter || ''
                    }},
                    lineStyle: {{
                        color: '#fbbf24',
                        type: 'dashed',
                        width: 1
                    }},
                    data: tagLines
                }};
            }}

            // Churn series (stacked bar by category)
            const churnSeries = categories.map((cat, i) => ({{
                name: cat,
                type: 'bar',
                stack: 'Total',
                barMaxWidth: scaled(8),
                emphasis: {{
                    focus: 'series'
                }},
                itemStyle: {{
                    color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                        {{ offset: 0, color: catColors[cat] }},
                        {{ offset: 1, color: catColors[cat] + '66' }}
                    ]),
                    borderRadius: [2, 2, 0, 0]
                }},
                data: data.categories[cat].churn
            }}));

            const tooltipConf = {{
                trigger: 'axis',
                axisPointer: {{
                    type: 'cross',
                    crossStyle: {{
                        color: '#4ade8044'
                    }},
                    lineStyle: {{
                        color: '#4ade8044'
                    }},
                    label: {{
                        backgroundColor: '#1a1e28',
                        borderColor: '#2d3344',
                        color: '#e4e8f1',
                        fontFamily: 'JetBrains Mono'
                    }}
                }},
                backgroundColor: 'rgba(13, 15, 20, 0.95)',
                borderColor: '#2d3344',
                borderWidth: 1,
                    padding: scaledArray([12, 16]),
                    textStyle: {{
                        color: '#e4e8f1',
                        fontFamily: 'JetBrains Mono',
                        fontSize: scaled(12)
                    }},
                    extraCssText: 'border-radius: 8px; box-shadow: 0 8px 32px rgba(0,0,0,0.4);'
            }};

            charts.growth.setOption({{
                title: {{
                    text: 'Cumulative Growth',
                    subtext: 'Lines of code by category',
                    left: scaled(20),
                    top: scaled(16),
                    textStyle: {{
                        color: '#e4e8f1',
                        fontSize: scaled(16),
                        fontWeight: 600,
                        fontFamily: 'Outfit'
                    }},
                    subtextStyle: {{
                        color: '#6b7280',
                        fontSize: scaled(12),
                        fontFamily: 'JetBrains Mono'
                    }}
                }},
                tooltip: tooltipConf,
                legend: {{
                    type: 'scroll',
                    data: categories,
                    selected: selectedMap,
                    selectedMode: 'multiple',
                    top: scaled(16),
                    right: scaled(20),
                    textStyle: {{
                        color: '#9ba3b8',
                        fontFamily: 'JetBrains Mono',
                        fontSize: scaled(11)
                    }},
                    itemWidth: scaled(12),
                    itemHeight: scaled(8),
                    itemGap: scaled(16)
                }},
                grid: {{
                    left: scaled(20),
                    right: scaled(20),
                    top: scaled(70),
                    bottom: scaled(40),
                    containLabel: true
                }},
                xAxis: {{
                    type: 'category',
                    boundaryGap: false,
                    data: dates,
                    axisLine: {{
                        lineStyle: {{ color: '#2d3344' }}
                    }},
                    axisLabel: {{
                        color: '#6b7280',
                        fontFamily: 'JetBrains Mono',
                        fontSize: scaled(10),
                        formatter: (value) => {{
                            const d = parseDate(value);
                            return d.toLocaleDateString('en-US', {{ month: 'short', day: 'numeric' }});
                        }}
                    }},
                    axisTick: {{ show: false }},
                    splitLine: {{ show: false }}
                }},
                yAxis: {{
                    type: 'value',
                    axisLine: {{ show: false }},
                    axisLabel: {{
                        color: '#6b7280',
                        fontFamily: 'JetBrains Mono',
                        fontSize: scaled(10),
                        formatter: (value) => {{
                            if (value >= 1000) return (value / 1000).toFixed(0) + 'k';
                            return value;
                        }}
                    }},
                    splitLine: {{
                        lineStyle: {{
                            color: '#1a1e28',
                            type: 'dashed'
                        }}
                    }}
                }},
                series: growthSeries,
                backgroundColor: 'transparent',
                dataZoom: [{{
                    type: 'inside',
                    xAxisIndex: 0,
                    filterMode: 'filter'
                }}],
                animationDuration: 800,
                animationEasing: 'cubicOut'
            }}, true);

            charts.churn.setOption({{
                title: {{
                    text: 'Daily Activity',
                    subtext: 'Lines changed per day',
                    left: scaled(20),
                    top: scaled(12),
                    textStyle: {{
                        color: '#e4e8f1',
                        fontSize: scaled(14),
                        fontWeight: 600,
                        fontFamily: 'Outfit'
                    }},
                    subtextStyle: {{
                        color: '#6b7280',
                        fontSize: scaled(11),
                        fontFamily: 'JetBrains Mono'
                    }}
                }},
                tooltip: tooltipConf,
                legend: {{ show: false, selected: selectedMap, selectedMode: 'multiple' }},
                grid: {{
                    left: scaled(20),
                    right: scaled(20),
                    top: scaled(50),
                    bottom: scaled(24),
                    containLabel: true
                }},
                xAxis: {{
                    type: 'category',
                    data: dates,
                    axisLine: {{
                        lineStyle: {{ color: '#2d3344' }}
                    }},
                    axisLabel: {{ show: false }},
                    axisTick: {{ show: false }},
                    splitLine: {{ show: false }}
                }},
                yAxis: {{
                    type: 'value',
                    axisLine: {{ show: false }},
                    axisLabel: {{
                        color: '#6b7280',
                        fontFamily: 'JetBrains Mono',
                        fontSize: scaled(10),
                        formatter: (value) => {{
                            if (value >= 1000) return (value / 1000).toFixed(0) + 'k';
                            return value;
                        }}
                    }},
                    splitLine: {{
                        lineStyle: {{
                            color: '#1a1e28',
                            type: 'dashed'
                        }}
                    }}
                }},
                series: churnSeries,
                backgroundColor: 'transparent',
                dataZoom: [{{
                    type: 'inside',
                    xAxisIndex: 0,
                    filterMode: 'filter'
                }}],
                animationDuration: 600,
                animationEasing: 'cubicOut'
            }}, true);

            echarts.connect([charts.growth, charts.churn].filter(Boolean));
            syncLegendSelection(projectName, selected);
            updateDeepDive(projectName, selected);
        }}

        function updateInspector(dateIndex) {{
            if (dateIndex === renderedInspectorIndex) {{
                return;
            }}
            renderedInspectorIndex = dateIndex;
            const dateStr = dates[dateIndex];
            if (!dateStr) return;

            const list = document.getElementById('event-list');
            const header = document.getElementById('inspector-date');

            const d = parseDate(dateStr);
            const formatted = d.toLocaleDateString('en-US', {{
                weekday: 'short',
                year: 'numeric',
                month: 'short',
                day: 'numeric'
            }});
            header.textContent = formatted;

            const data = projectData[currentProject];
            const catColors = data.colors;
            const selected = ensureSelection(currentProject);
            const evs = (data.events[dateStr] || []).filter((event) => eventMatchesSelection(event, selected));

            if (evs.length === 0) {{
                list.innerHTML = `
                    <li class="empty-state">
                    <div class="empty-state-icon">o</div>
                        No commits on this day
                    </li>
                `;
                return;
            }}

            list.innerHTML = evs.map((e, i) => {{
                const filesHtml = e.f.map(f => `<span class="file-entry">${{f}}</span>`).join('');
                const moreFiles = e.fc > e.f.length
                    ? `<span class="file-entry" style="opacity: 0.6">+${{e.fc - e.f.length}} more files</span>`
                    : '';

                const catsHtml = e.cats
                    ? Object.entries(e.cats).filter(([cat]) => selected.has(cat)).map(([cat, stats]) => {{
                        const color = catColors[cat] || '#888';
                        return `<span class="cat-badge" style="background: ${{color}}15; border-color: ${{color}}40; color: ${{color}}">
                            ${{cat}} +${{stats.a}} -${{stats.r}}
                        </span>`;
                    }}).join('')
                    : '';

                return `
                    <li class="event-item" style="animation-delay: ${{i * 0.05}}s">
                        <div class="event-header">
                            <span class="event-hash">${{e.h}}</span>
                            <span class="event-author">${{e.a}}</span>
                        </div>
                        <div class="event-message">${{e.m}}</div>
                        <div class="event-stats">
                            <span class="event-stat add">+${{e['+']}}</span>
                            <span class="event-stat del">-${{e['-']}}</span>
                        </div>
                        ${{catsHtml ? `<div class="event-categories">${{catsHtml}}</div>` : ''}}
                        ${{e.f.length > 0 ? `<div class="event-files">${{filesHtml}}${{moreFiles}}</div>` : ''}}
                    </li>
                `;
            }}).join('');
        }}

        function bindChartEvents() {{
            if (!charts.growth || !charts.churn) {{
                return;
            }}

            charts.growth.on('legendselectchanged', function (event) {{
                if (suppressLegendEvents) {{
                    return;
                }}
                const selected = Object.entries(event.selected || {{}})
                    .filter(([_, enabled]) => enabled)
                    .map(([name]) => name);
                selectionByProject[currentProject] = new Set(selected);
                applySelection(currentProject);
            }});

            charts.growth.on('updateAxisPointer', function (event) {{
                if (event.dataIndex != null) {{
                    scheduleInspector(event.dataIndex);
                }}
            }});

            charts.churn.on('updateAxisPointer', function (event) {{
                if (event.dataIndex != null) {{
                    scheduleInspector(event.dataIndex);
                }}
            }});
        }}

        function scheduleInspector(index) {{
            lastInspectorIndex = index;
            if (inspectorFrame) {{
                return;
            }}
            inspectorFrame = requestAnimationFrame(() => {{
                inspectorFrame = null;
                if (lastInspectorIndex !== null) {{
                    updateInspector(lastInspectorIndex);
                }}
            }});
        }}

        // Event handlers
        projectSelect.addEventListener('change', (e) => {{
            currentProject = e.target.value;
            ensureSelection(currentProject);
            lastInspectorIndex = null;
            renderedInspectorIndex = null;
            updateCharts(currentProject);
        }});

        scaleSelect.addEventListener('change', (e) => {{
            setScale(e.target.value);
        }});

        window.addEventListener('resize', () => {{
            const nextDpr = computeDpr();
            if (nextDpr !== currentDpr) {{
                initCharts();
                updateCharts(currentProject);
                if (lastInspectorIndex !== null) {{
                    renderedInspectorIndex = null;
                    updateInspector(lastInspectorIndex);
                }}
                return;
            }}
            Object.values(charts).forEach((chart) => {{
                if (chart) {{
                    chart.resize();
                }}
            }});
        }});

        // Initial render
        initCharts();
        projectSelect.value = currentProject;
        updateCharts(currentProject);

        if (window.mermaid) {{
            mermaid.initialize({{
                startOnLoad: true,
                theme: 'base',
                themeVariables: {{
                    primaryColor: '#1a1e28',
                    primaryTextColor: '#e4e8f1',
                    lineColor: '#2d3344',
                    fontFamily: 'JetBrains Mono'
                }}
            }});
            mermaid.init(undefined, document.querySelectorAll('.mermaid'));
        }}
    </script>
</body>
</html>
    """

    wrote = write_text_if_changed(output_path, html)
    if wrote:
        print(f"Rich report generated at {output_path.resolve()}")
    else:
        print(f"Velocity report unchanged at {output_path.resolve()}")


def build(
    output: Path = typer.Option(DEFAULT_OUTPUT, "--output", "-o", help="Destination HTML path"),
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
    selected_specs: Dict[str, dict] = PROJECT_SPECS
    if project:
        requested = [name.strip() for name in project if name.strip()]
        if not requested:
            raise typer.BadParameter("At least one non-empty --project value is required.")
        missing = [name for name in requested if name not in PROJECT_SPECS]
        if missing:
            raise typer.BadParameter(f"Unknown project(s): {', '.join(sorted(missing))}")
        selected_specs = {name: PROJECT_SPECS[name] for name in requested}

    if exclude:
        excluded = [name.strip() for name in exclude if name.strip()]
        if not excluded:
            raise typer.BadParameter("At least one non-empty --exclude value is required.")
        missing = [name for name in excluded if name not in PROJECT_SPECS]
        if missing:
            raise typer.BadParameter(f"Unknown project(s): {', '.join(sorted(missing))}")
        for name in excluded:
            selected_specs.pop(name, None)

    if not selected_specs:
        raise typer.BadParameter("No projects available to analyse.")

    stats = analyze_projects(selected_specs)
    if not stats:
        typer.secho("No repositories produced git history; nothing to render.", fg=typer.colors.YELLOW)
        return
    if aggregate and len(stats) > 1:
        aggregate_stats = _aggregate_stats(stats)
        aggregate_spec = _aggregate_spec(list(stats.keys()))
        stats = {AGGREGATE_PROJECT: aggregate_stats, **stats}
        selected_specs = {AGGREGATE_PROJECT: aggregate_spec, **selected_specs}
    generate_html(stats, selected_specs, output)


if __name__ == "__main__":
    typer.run(build)
