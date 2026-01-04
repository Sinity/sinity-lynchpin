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
    "knowledgebase": {
        "path": "/realm/project/knowledgebase",
        "classify": classify_knowledgebase,
        "categories": ["docs", "config"],
        "colors": {"docs": "#fac858", "config": "#ee6666"},
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


def run_git_log(path: Path) -> List[str]:
    sep = "|||"
    fmt = f"%h{sep}%ad{sep}%an{sep}%s"

    cmd = [
        "git", "log",
        "--all",
        "--date=iso-strict-local",
        f"--pretty=format:COMMIT:{fmt}",
        "--numstat",
        "--no-merges"
    ]
    try:
        subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=path, check=True, capture_output=True)
        result = subprocess.run(cmd, cwd=path, capture_output=True, text=True, check=True, errors="replace")
        return result.stdout.splitlines()
    except subprocess.CalledProcessError:
        print(f"Skipping {path} (not a git repo or error)", file=sys.stderr)
        return []


def parse_log(lines: List[str], project_name: str, classify_fn: Callable[[str], Optional[str]]) -> ProjectStats:
    stats = ProjectStats(name=project_name)

    current_commit: Optional[CommitEvent] = None
    current_files_buffer = []

    def flush_commit():
        nonlocal current_commit, current_files_buffer
        if current_commit and current_commit.by_category:
            current_commit.top_files = current_files_buffer[:5]
            current_commit.files_count = len(current_files_buffer)

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

        current_commit = None
        current_files_buffer = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if line.startswith("COMMIT:"):
            flush_commit()
            content = line[len("COMMIT:"):]
            parts = content.split("|||")
            if len(parts) >= 4:
                h, d_raw, auth, msg = parts[0], parts[1], parts[2], parts[3]
                date_str = d_raw.split("T")[0]
                current_commit = CommitEvent(hash=h, date=date_str, author=auth, message=msg)
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
            "events": {}
        }

        # Initialize cumulative counters per category
        cumulative = {cat: 0 for cat in categories}

        for cat in categories:
            project_data["categories"][cat] = {
                "growth": [],
                "churn": []
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
                else:
                    project_data["categories"][cat]["churn"].append(0)

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
                        "cats": cat_breakdown,
                        "f": c.top_files,
                        "fc": c.files_count
                    })
                ev_list.sort(key=lambda x: x["+"] + x["-"], reverse=True)
                project_data["events"][d] = ev_list

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

        /* Scanline overlay */
        body::before {{
            content: '';
            position: fixed;
            inset: 0;
            background: repeating-linear-gradient(
                0deg,
                transparent,
                transparent 2px,
                rgba(0, 0, 0, 0.03) 2px,
                rgba(0, 0, 0, 0.03) 4px
            );
            pointer-events: none;
            z-index: 9999;
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

    <script>
        const dates = {json.dumps(sorted_dates)};
        const projectData = {json.dumps(js_projects)};
        const projectSummaries = {json.dumps(project_summaries)};
        const projects = Object.keys(projectData);
        const selectionByProject = {{}};
        let lastInspectorIndex = null;
        let suppressLegendEvents = false;

        const params = new URLSearchParams(window.location.search);
        const rendererParam = (params.get('renderer') || 'canvas').toLowerCase();
        const renderer = rendererParam === 'svg' ? 'svg' : 'canvas';
        const scaleOptions = [1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0];

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
        let growthChart = null;
        let churnChart = null;

        function computeDpr() {{
            const base = window.devicePixelRatio || 1;
            const scaled = base * uiScale;
            return Math.max(1, Math.round(scaled));
        }}

        function initCharts() {{
            const nextDpr = computeDpr();
            currentDpr = nextDpr;
            if (growthChart) {{
                growthChart.dispose();
            }}
            if (churnChart) {{
                churnChart.dispose();
            }}
            growthChart = echarts.init(
                document.getElementById('growth-chart'),
                null,
                {{ renderer: renderer, devicePixelRatio: nextDpr }}
            );
            churnChart = echarts.init(
                document.getElementById('churn-chart'),
                null,
                {{ renderer: renderer, devicePixelRatio: nextDpr }}
            );
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
            if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
            if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
            return n.toString();
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
                const catTotal = growth.length ? growth[growth.length - 1] : 0;
                categoryTotals.push({{ name: cat, totalLoc: catTotal }});
                if (growth.length) {{
                    totalLoc += catTotal;
                    const idx30 = Math.max(0, growth.length - 30);
                    const idx7 = Math.max(0, growth.length - 7);
                    recentNet += growth[growth.length - 1] - growth[idx30];
                    weekNet += growth[growth.length - 1] - growth[idx7];
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
                    const growth = data.categories[cat].growth;
                    if (!growth.length) {{
                        continue;
                    }}
                    const prev = i > 0 ? growth[i - 1] : 0;
                    dayNet += growth[i] - prev;
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
                ? summary.topCategories.map((item) => `${{item.name}} ${{formatNumber(item.totalLoc)}}`).join(' • ')
                : 'n/a';

            statsBar.innerHTML = `
                <div class="stat-card">
                    <div class="stat-label">Total Lines</div>
                    <div class="stat-value">${{formatNumber(summary.totalLoc)}}</div>
                    <div class="stat-detail">${{summary.categoriesCount}} selected • ${{hiddenCount}} hidden</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">30-Day Net</div>
                    <div class="stat-value ${{summary.recentNet >= 0 ? 'positive' : 'negative'}}">${{recentSign}}${{formatNumber(summary.recentNet)}}</div>
                    <div class="stat-detail">${{formatNumber(summary.recentNetDaily)}}/day • ${{summary.recentDays}}d window</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">30-Day Churn</div>
                    <div class="stat-value">${{formatNumber(summary.recentChurn)}}</div>
                    <div class="stat-detail">${{formatNumber(summary.recentChurnDaily)}}/day • ${{summary.recentDays}}d window</div>
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
                    <div class="stat-detail">${{formatNumber(summary.recentCommitDaily)}}/day • ${{summary.recentActiveDays}} active days</div>
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
            const idx30 = Math.max(0, growth.length - 30);
            const net30 = growth.length ? growth[growth.length - 1] - growth[idx30] : 0;
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
                        <span class="legend-meta">${{formatNumber(metrics.totalLoc)}} • ${{netSign}}${{formatNumber(metrics.net30)}} (30d) • ${{formatNumber(metrics.churn30)}} churn</span>
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
                    <span>${{netSign}}${{formatNumber(metrics.net30)}} • ${{formatNumber(metrics.churn30)}} churn</span>
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
                indicator.innerHTML = `<span>${{start}}</span> -> <span>${{end}}</span> · ${{scope}}`;
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
                growthChart.dispatchAction({{ type: action, name: cat }});
                churnChart.dispatchAction({{ type: action, name: cat }});
            }});
            suppressLegendEvents = false;
        }}

        function applySelection(projectName) {{
            const selected = ensureSelection(projectName);
            const selectedMap = selectionMap(projectName, selected);
            growthChart.setOption({{ legend: {{ selected: selectedMap }} }}, false);
            churnChart.setOption({{ legend: {{ selected: selectedMap }} }}, false);
            syncLegendSelection(projectName, selected);
            updateStatsBar(projectName, selected);
            updateLegend(projectName, selected);
            updateFilterBar(projectName, selected);
            updateRangeIndicator(projectName, selected);
            if (lastInspectorIndex !== null) {{
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

            growthChart.setOption({{
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
                            const d = new Date(value);
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

            churnChart.setOption({{
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

            echarts.connect([growthChart, churnChart]);
            syncLegendSelection(projectName, selected);
        }}

        function updateInspector(dateIndex) {{
            const dateStr = dates[dateIndex];
            if (!dateStr) return;

            const list = document.getElementById('event-list');
            const header = document.getElementById('inspector-date');

            const d = new Date(dateStr);
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
            const evs = (data.events[dateStr] || []).filter((event) => {{
                const cats = event.cats ? Object.keys(event.cats) : [];
                return cats.some((cat) => selected.has(cat));
            }});

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
            if (!growthChart || !churnChart) {{
                return;
            }}

            growthChart.on('legendselectchanged', function (event) {{
                if (suppressLegendEvents) {{
                    return;
                }}
                const selected = Object.entries(event.selected || {{}})
                    .filter(([_, enabled]) => enabled)
                    .map(([name]) => name);
                selectionByProject[currentProject] = new Set(selected);
                applySelection(currentProject);
            }});

            growthChart.on('updateAxisPointer', function (event) {{
                if (event.dataIndex != null) {{
                    lastInspectorIndex = event.dataIndex;
                    updateInspector(event.dataIndex);
                }}
            }});

            churnChart.on('updateAxisPointer', function (event) {{
                if (event.dataIndex != null) {{
                    lastInspectorIndex = event.dataIndex;
                    updateInspector(event.dataIndex);
                }}
            }});
        }}

        // Event handlers
        projectSelect.addEventListener('change', (e) => {{
            currentProject = e.target.value;
            ensureSelection(currentProject);
            lastInspectorIndex = null;
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
                    updateInspector(lastInspectorIndex);
                }}
                return;
            }}
            growthChart.resize();
            churnChart.resize();
        }});

        // Initial render
        initCharts();
        projectSelect.value = currentProject;
        updateCharts(currentProject);
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
