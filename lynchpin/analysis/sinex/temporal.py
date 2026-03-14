"""Sinex temporal analysis and growth metrics."""
import os
import subprocess
import re
from collections import defaultdict
from datetime import datetime
from ..core import git


def compute_monthly_velocity(sinex_dir):
    """
    Computes monthly velocity for sinex (lines added to .rs files).
    Returns sorted list of {month, lines, commits, files_touched}.
    """
    print("Computing sinex monthly velocity...")
    months = defaultdict(lambda: {'lines': 0, 'commits': 0, 'files': set()})

    cur_month = None
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
                months[cur_month]['files'].add(fp)

    result = []
    for month, data in sorted(months.items()):
        result.append({
            'month': month,
            'lines': data['lines'],
            'commits': data['commits'],
            'files_touched': len(data['files']),
        })
    return result


def compute_crate_growth(sinex_dir):
    """
    For each crate, computes monthly line additions.
    Returns dict: crate_name -> [{month, lines}].
    """
    print("Computing per-crate growth...")

    # Find crate dirs
    crate_dirs = {}
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
            except:
                crate_dirs[rel] = rel

    # Parse log
    crate_months = {name: defaultdict(int) for name in crate_dirs.values()}
    cur_month = None

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

    result = {}
    for crate_name, months in crate_months.items():
        if not months:
            continue
        result[crate_name] = [
            {'month': m, 'lines': l}
            for m, l in sorted(months.items())
        ]
    return result


def compute_sinex_stats(sinex_dir):
    """
    Aggregated sinex development statistics.
    """
    print("Computing sinex aggregate stats...")

    # Total commits
    total_commits = 0
    first_date = None
    last_date = None
    active_days = set()
    files_per_commit = []
    
    cur_files = 0

    for line in git.get_log(sinex_dir, branch="HEAD",
                            params=['--pretty=format:COMMIT|%aI', '--name-only']):
        line = line.strip()
        if not line:
            continue
        if line.startswith('COMMIT|'):
            d = line.split('|')[1][:10]
            if d:
                total_commits += 1
                active_days.add(d)
                if first_date is None or d < first_date:
                    first_date = d
                if last_date is None or d > last_date:
                    last_date = d
            if cur_files > 0:
                files_per_commit.append(cur_files)
            cur_files = 0
        else:
            if line.endswith('.rs') and 'target/' not in line:
                cur_files += 1

    if cur_files > 0:
        files_per_commit.append(cur_files)

    if first_date and last_date:
        span_days = (datetime.strptime(last_date, '%Y-%m-%d') -
                     datetime.strptime(first_date, '%Y-%m-%d')).days
    else:
        span_days = 0
        
    files_per_commit.sort()
    n = len(files_per_commit)

    return {
        'total_commits': total_commits,
        'first_commit': first_date,
        'last_commit': last_date,
        'span_days': span_days,
        'active_days': len(active_days),
        'active_months': len(set(d[:7] for d in active_days)),
        'files_per_commit_median': files_per_commit[n//2] if n > 0 else 0,
        'files_per_commit_mean': round(sum(files_per_commit)/max(1, n), 1)
    }
