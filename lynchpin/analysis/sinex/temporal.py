"""Sinex temporal analysis and growth metrics."""
from __future__ import annotations

import os
import re
from collections import Counter, defaultdict
from datetime import datetime
import subprocess
from typing import cast

from ..core.canonical import JsonObject
from ..core import git


_COMMIT_TYPE_RE = re.compile(
    r"^(fix|refactor|feat|test|docs|chore|ci|style|build|perf)(?:\([^)]+\))?(?::|\b)",
    re.IGNORECASE,
)


def _classify_commit_type(subject: str) -> str:
    match = _COMMIT_TYPE_RE.match(subject.strip())
    if match:
        return match.group(1).lower()
    lowered = subject.lower()
    if lowered.startswith('merge '):
        return 'merge'
    return 'other'


def _top_area(path: str) -> str:
    rel = path.replace('\\', '/')
    if '/' not in rel:
        return '(root)'
    return rel.split('/', 1)[0]


def _resolve_default_branch(repo_dir: str) -> str:
    for candidate in ('master', 'main'):
        try:
            subprocess.check_output(['git', 'rev-parse', '--verify', candidate], cwd=repo_dir, stderr=subprocess.DEVNULL)
            return candidate
        except (OSError, subprocess.CalledProcessError):
            continue
    return 'HEAD'


def _summarize_branch_sizes(repo_dir: str) -> tuple[JsonObject, list[JsonObject]]:
    baseline = _resolve_default_branch(repo_dir)
    try:
        raw = subprocess.check_output(
            ['git', 'for-each-ref', '--format=%(refname:short)', 'refs/heads'],
            cwd=repo_dir,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return {'mean': 0, '50%': 0, 'max': 0, 'branch_count': 0}, []

    sizes: list[int] = []
    details: list[JsonObject] = []
    for branch in [line.strip() for line in raw.splitlines() if line.strip()]:
        try:
            merge_base = subprocess.check_output(
                ['git', 'merge-base', baseline, branch],
                cwd=repo_dir,
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
            if not merge_base:
                continue
            unique = int(
                subprocess.check_output(
                    ['git', 'rev-list', '--count', f'{merge_base}..{branch}'],
                    cwd=repo_dir,
                    stderr=subprocess.DEVNULL,
                    text=True,
                ).strip()
            )
        except (OSError, subprocess.CalledProcessError, ValueError):
            continue
        sizes.append(unique)
        details.append({'branch': branch, 'unique_commits': unique})
    if not sizes:
        return {'mean': 0, '50%': 0, 'max': 0, 'branch_count': 0}, []
    ordered = sorted(sizes)
    summary = {
        'mean': round(sum(ordered) / len(ordered), 3),
        '50%': float(ordered[len(ordered) // 2]),
        'max': float(ordered[-1]),
        'branch_count': len(ordered),
    }
    details.sort(key=lambda row: int(row['unique_commits']), reverse=True)
    return summary, details[:20]


def _month_row() -> JsonObject:
    return {'lines': 0, 'commits': 0, 'files': set()}


def compute_monthly_velocity(sinex_dir: str) -> list[JsonObject]:
    """
    Computes monthly velocity for sinex (lines added to .rs files).
    Returns sorted list of {month, lines, commits, files_touched}.
    """
    print("Computing sinex monthly velocity...")
    months: dict[str, JsonObject] = defaultdict(_month_row)

    cur_month: str | None = None
    for line in git.get_log(sinex_dir, branch="HEAD",
                            params=['--pretty=format:COMMIT|%aI', '--numstat']):
        line = line.strip()
        if not line:
            continue
        if line.startswith('COMMIT|'):
            date_str = line.split('|', 1)[1][:7]  # YYYY-MM
            cur_month = date_str
            if cur_month:
                months[cur_month]['commits'] += 1
        elif '\t' in line and cur_month:
            parts = line.split('\t')
            if len(parts) < 3:
                continue
            ins = int(parts[0]) if parts[0] != '-' else 0
            fp = parts[2]
            if fp.endswith('.rs') and 'target/' not in fp:
                months[cur_month]['lines'] += ins
                cast(set[str], months[cur_month]['files']).add(fp)

    result: list[JsonObject] = []
    for month, data in sorted(months.items()):
        result.append({
            'month': month,
            'lines': data['lines'],
            'commits': data['commits'],
            'files_touched': len(cast(set[str], data['files'])),
        })
    return result


def compute_crate_growth(sinex_dir: str) -> dict[str, list[JsonObject]]:
    """
    For each crate, computes monthly line additions.
    Returns dict: crate_name -> [{month, lines}].
    """
    print("Computing per-crate growth...")

    # Find crate dirs
    crate_dirs: dict[str, str] = {}
    for root, dirs, files in os.walk(sinex_dir):
        if '.git' in root or 'target' in root:
            continue
        if 'Cargo.toml' in files and root != sinex_dir:
            rel = os.path.relpath(root, sinex_dir)
            # Get crate name
            try:
                with open(os.path.join(root, 'Cargo.toml')) as f:
                    for line in f:
                        m = re.match(r'name\s*=\s*"([^"]+)"', line.strip())
                        if m:
                            crate_dirs[rel] = m.group(1)
                            break
            except OSError:
                crate_dirs[rel] = rel

    # Parse log
    crate_months: dict[str, dict[str, int]] = {name: defaultdict(int) for name in crate_dirs.values()}
    cur_month: str | None = None

    for line in git.get_log(sinex_dir, branch="HEAD",
                            params=['--pretty=format:COMMIT|%aI', '--numstat']):
        line = line.strip()
        if not line:
            continue
        if line.startswith('COMMIT|'):
            cur_month = line.split('|', 1)[1][:7]
        elif '\t' in line and cur_month:
            parts = line.split('\t')
            if len(parts) < 3:
                continue
            ins = int(parts[0]) if parts[0] != '-' else 0
            fp = parts[2]
            if not fp.endswith('.rs') or 'target/' in fp:
                continue

            # Find which crate this belongs to
            for crate_path, crate_name in crate_dirs.items():
                if fp.startswith(crate_path + '/'):
                    crate_months[crate_name][cur_month] += ins
                    break

    result: dict[str, list[JsonObject]] = {}
    for crate_name, months in crate_months.items():
        if not months:
            continue
        result[crate_name] = [
            {'month': m, 'lines': line_count}
            for m, line_count in sorted(months.items())
        ]
    return result


def compute_sinex_stats(sinex_dir: str) -> JsonObject:
    """
    Aggregated sinex development statistics.
    """
    print("Computing sinex aggregate stats...")

    # Total commits
    total_commits = 0
    first_date: str | None = None
    last_date: str | None = None
    active_days: set[str] = set()
    files_per_commit: list[int] = []
    churn_per_commit: list[int] = []
    commit_type_counts: Counter[str] = Counter()
    area_touches: Counter[str] = Counter()
    cur_files = 0
    cur_churn = 0

    for line in git.get_log(sinex_dir, branch="HEAD", params=['--pretty=format:COMMIT|%aI|%s', '--numstat']):
        line = line.strip()
        if not line:
            continue
        if line.startswith('COMMIT|'):
            parts = line.split('|', 2)
            d = parts[1][:10]
            subject = parts[2] if len(parts) > 2 else ''
            if d:
                total_commits += 1
                active_days.add(d)
                if first_date is None or d < first_date:
                    first_date = d
                if last_date is None or d > last_date:
                    last_date = d
            commit_type_counts[_classify_commit_type(subject)] += 1
            if cur_files > 0:
                files_per_commit.append(cur_files)
                churn_per_commit.append(cur_churn)
            cur_files = 0
            cur_churn = 0
        else:
            parts = line.split('\t')
            if len(parts) < 3:
                continue
            added_raw, deleted_raw, rel = parts
            added = int(added_raw) if added_raw.isdigit() else 0
            deleted = int(deleted_raw) if deleted_raw.isdigit() else 0
            area_touches[_top_area(rel)] += 1
            if rel.endswith('.rs') and 'target/' not in rel:
                cur_files += 1
                cur_churn += added + deleted

    if cur_files > 0:
        files_per_commit.append(cur_files)
        churn_per_commit.append(cur_churn)

    if first_date and last_date:
        span_days = (datetime.strptime(last_date, '%Y-%m-%d') -
                     datetime.strptime(first_date, '%Y-%m-%d')).days
    else:
        span_days = 0
        
    files_per_commit = sorted(files_per_commit)
    churn_per_commit = sorted(churn_per_commit)
    n = len(files_per_commit)
    branch_size_summary, branch_sizes = _summarize_branch_sizes(sinex_dir)
    calendar_days = span_days + 1 if span_days else (1 if total_commits else 0)
    active_day_ratio = round(len(active_days) / max(calendar_days, 1), 6)
    area_total = max(sum(area_touches.values()), 1)
    top_touch_areas = [
        {
            'area': area,
            'touches': touches,
            'share': round(touches / area_total, 6),
        }
        for area, touches in sorted(area_touches.items(), key=lambda item: item[1], reverse=True)
    ]
    top_touch_areas = top_touch_areas[:20]

    return {
        'total_commits': total_commits,
        'first_commit': first_date,
        'last_commit': last_date,
        'span_days': span_days,
        'calendar_days': calendar_days,
        'active_days': len(active_days),
        'active_day_ratio': active_day_ratio,
        'active_months': len(set(d[:7] for d in active_days)),
        'commits_per_calendar_day': round(total_commits / max(calendar_days, 1), 3),
        'commits_per_active_day': round(total_commits / max(len(active_days), 1), 3),
        'files_per_commit_median': files_per_commit[n//2] if n > 0 else 0,
        'files_per_commit_mean': round(sum(files_per_commit)/max(1, n), 1),
        'median_churn_per_commit': churn_per_commit[n//2] if n > 0 else 0,
        'p90_files_per_commit': files_per_commit[min(n - 1, int(n * 0.9))] if n > 0 else 0,
        'p90_churn_per_commit': churn_per_commit[min(n - 1, int(n * 0.9))] if n > 0 else 0,
        'commit_type_counts': dict(sorted(commit_type_counts.items(), key=lambda item: item[1], reverse=True)),
        'branch_size_summary': branch_size_summary,
        'branch_sizes': branch_sizes,
        'top_touch_areas': top_touch_areas,
    }
