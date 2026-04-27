"""Commit-level transport helpers built from git history."""

import subprocess
from datetime import datetime


def parse_iso_datetime(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except Exception:
        return None


def _path_component(path):
    p = (path or '').strip().replace('\\', '/')
    if not p:
        return 'unknown'
    parts = [x for x in p.split('/') if x]
    if not parts:
        return 'unknown'

    if parts[0] == 'crate' and len(parts) >= 3:
        return parts[2]
    if parts[0] in {'src', 'tests'} and len(parts) >= 2:
        return parts[1]
    if parts[0] == 'Source' and len(parts) >= 2:
        return parts[1]
    return parts[0]


def collect_commit_stats(
    repo_dir,
    branch='HEAD',
    after=None,
    before=None,
    author_allowlist=None,
    keep_files=False,
):
    """Collect commit-level stats from `git log --numstat`."""
    cmd = ['git', 'log', branch, '--pretty=format:COMMIT|%H|%aN|%aI|%s', '--numstat']
    if after:
        cmd.extend(['--after', after])
    if before:
        cmd.extend(['--before', before])

    proc = subprocess.Popen(
        cmd,
        cwd=repo_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )

    commits = []
    cur = None

    def flush():
        nonlocal cur
        if not cur:
            return
        if author_allowlist and cur['author'] not in author_allowlist:
            cur = None
            return
        cur['files_changed'] = len(cur['files'])
        cur['lines_changed'] = cur['additions'] + cur['deletions']
        cur['path_roots'] = sorted({_path_component(p) for p in cur['files']})
        if keep_files:
            cur['files'] = sorted(cur['files'])
        else:
            cur.pop('files', None)
        commits.append(cur)
        cur = None

    for raw in proc.stdout:
        line = raw.rstrip('\n')
        if not line:
            continue

        if line.startswith('COMMIT|'):
            flush()
            parts = line.split('|', 4)
            cur = {
                'sha': parts[1],
                'author': parts[2],
                'date': parts[3],
                'subject': parts[4] if len(parts) > 4 else '',
                'additions': 0,
                'deletions': 0,
                'files': set(),
            }
            continue

        if '\t' not in line or not cur:
            continue

        parts = line.split('\t')
        if len(parts) < 3:
            continue

        add = int(parts[0]) if parts[0].isdigit() else 0
        delete = int(parts[1]) if parts[1].isdigit() else 0
        path = parts[2]

        cur['additions'] += add
        cur['deletions'] += delete
        cur['files'].add(path)

    flush()
    commits.sort(key=lambda c: c['date'])
    return commits
