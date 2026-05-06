"""Join ActivityWatch activity with git commit activity on daily buckets."""

import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone

from ..core.commit_stats import collect_commit_stats, parse_iso_datetime
from ..core.io import save_json
from ..core.canonical import load_analysis_spec

NS_PER_SECOND = 1_000_000_000
NS_PER_DAY = 86_400 * NS_PER_SECOND

CODING_APPS = {
    'code',
    'codium',
    'cursor',
    'zed',
    'nvim',
    'neovim',
    'vim',
    'emacs',
    'helix',
    'rustrover',
    'pycharm',
    'intellij',
}
TERMINAL_APPS = {'kitty', 'wezterm', 'alacritty', 'foot', 'konsole', 'gnome-terminal-server', 'tmux'}
BROWSER_APPS = {'firefox', 'floorp', 'chromium', 'google-chrome', 'chrome'}

CODING_TITLE_HINTS = {
    '/realm/project',
    'sinex',
    'sinity-lynchpin',
    '.rs',
    '.py',
    'cargo',
    'pytest',
    'git ',
}
VALIDATION_TITLE_HINTS = {
    'localhost',
    '127.0.0.1',
    'github',
    'gitlab',
    'pull request',
    'issue',
    'sentry',
    'grafana',
    'kibana',
    'docs',
    'test',
    'ci',
}


def _to_ns(dt):
    return int(dt.timestamp() * NS_PER_SECOND)


def _utc_day_from_ns(ns):
    return datetime.fromtimestamp(ns / NS_PER_SECOND, tz=timezone.utc).date().isoformat()


def _utc_day_from_iso(iso_ts):
    dt = parse_iso_datetime(iso_ts)
    if dt is None:
        return iso_ts[:10]
    return dt.astimezone(timezone.utc).date().isoformat()


def _normalize_app(app):
    return (app or '').strip().lower()


def _normalize_title(title):
    return (title or '').strip().lower()


def _is_coding_event(app, title):
    if app in CODING_APPS:
        return True
    if app in TERMINAL_APPS:
        return any(h in title for h in CODING_TITLE_HINTS)
    if app in BROWSER_APPS:
        return 'github' in title or 'gitlab' in title or 'localhost' in title
    return False


def _is_validation_event(app, title):
    if app in BROWSER_APPS:
        return any(h in title for h in VALIDATION_TITLE_HINTS)
    return False


def _merge_intervals(intervals):
    if not intervals:
        return []
    intervals.sort()
    out = [list(intervals[0])]
    for start, end in intervals[1:]:
        last = out[-1]
        if start <= last[1]:
            last[1] = max(last[1], end)
        else:
            out.append([start, end])
    return [(s, e) for s, e in out]


def _clip_interval(start_ns, end_ns, min_ns, max_ns):
    s = max(start_ns, min_ns)
    e = min(end_ns, max_ns)
    if e <= s:
        return None
    return s, e


def _intersect_with_active(start_ns, end_ns, active_intervals, start_index):
    idx = start_index
    while idx < len(active_intervals) and active_intervals[idx][1] <= start_ns:
        idx += 1
    cur = idx
    overlaps = []
    while cur < len(active_intervals) and active_intervals[cur][0] < end_ns:
        a_start, a_end = active_intervals[cur]
        s = max(start_ns, a_start)
        e = min(end_ns, a_end)
        if e > s:
            overlaps.append((s, e))
        if a_end >= end_ns:
            break
        cur += 1
    return overlaps, idx


def _accumulate_by_day(bucket, start_ns, end_ns):
    cur = start_ns
    while cur < end_ns:
        day_start = (cur // NS_PER_DAY) * NS_PER_DAY
        day_end = day_start + NS_PER_DAY
        seg_end = min(end_ns, day_end)
        day = _utc_day_from_ns(cur)
        bucket[day] += (seg_end - cur) / NS_PER_SECOND
        cur = seg_end


def _pearson(x_values, y_values):
    if len(x_values) != len(y_values) or len(x_values) < 2:
        return None
    n = len(x_values)
    x_mean = sum(x_values) / n
    y_mean = sum(y_values) / n
    cov = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_values, y_values))
    x_var = sum((x - x_mean) ** 2 for x in x_values)
    y_var = sum((y - y_mean) ** 2 for y in y_values)
    if x_var == 0 or y_var == 0:
        return None
    return round(cov / (x_var ** 0.5 * y_var ** 0.5), 4)


def _load_active_intervals(conn, min_ns, max_ns):
    cur = conn.cursor()
    cur.execute("SELECT id FROM buckets WHERE type='afkstatus'")
    bucket_ids = [str(r[0]) for r in cur.fetchall()]
    if not bucket_ids:
        return []

    q = (
        "SELECT starttime, endtime, data FROM events "
        f"WHERE bucketrow IN ({','.join(bucket_ids)}) AND endtime >= ? AND starttime <= ? "
        "ORDER BY starttime ASC"
    )
    cur.execute(q, (min_ns, max_ns))

    intervals = []
    for start_ns, end_ns, payload in cur.fetchall():
        clipped = _clip_interval(start_ns, end_ns, min_ns, max_ns)
        if not clipped:
            continue
        data = json.loads(payload or '{}')
        if data.get('status') != 'not-afk':
            continue
        intervals.append(clipped)
    return _merge_intervals(intervals)


def _load_window_events(conn, min_ns, max_ns):
    cur = conn.cursor()
    cur.execute("SELECT id FROM buckets WHERE type='currentwindow'")
    bucket_ids = [str(r[0]) for r in cur.fetchall()]
    if not bucket_ids:
        return []

    q = (
        "SELECT starttime, endtime, data FROM events "
        f"WHERE bucketrow IN ({','.join(bucket_ids)}) AND endtime >= ? AND starttime <= ? "
        "ORDER BY starttime ASC"
    )
    cur.execute(q, (min_ns, max_ns))

    out = []
    for start_ns, end_ns, payload in cur.fetchall():
        clipped = _clip_interval(start_ns, end_ns, min_ns, max_ns)
        if not clipped:
            continue
        data = json.loads(payload or '{}')
        out.append((clipped[0], clipped[1], _normalize_app(data.get('app')), _normalize_title(data.get('title'))))
    return out


def run_aw_git_join(spec_path, out_file, aw_db_path):
    spec = load_analysis_spec(spec_path)
    commits = collect_commit_stats(
        repo_dir=spec['sinex']['repo'],
        branch=spec['sinex']['branch'],
    )
    if not commits:
        output = {'error': 'no commits found in sinex stream'}
        save_json(out_file, output, sort_keys=True)
        return output

    commit_dates = [parse_iso_datetime(c['date']) for c in commits if parse_iso_datetime(c['date']) is not None]
    min_ns = _to_ns(min(commit_dates))
    max_ns = _to_ns(max(commit_dates))

    conn = sqlite3.connect(aw_db_path)
    try:
        active_intervals = _load_active_intervals(conn, min_ns, max_ns)
        window_events = _load_window_events(conn, min_ns, max_ns)
    finally:
        conn.close()

    coding_seconds = defaultdict(float)
    validation_seconds = defaultdict(float)
    total_active_seconds = defaultdict(float)

    active_idx = 0
    for start_ns, end_ns, app, title in window_events:
        overlaps, active_idx = _intersect_with_active(start_ns, end_ns, active_intervals, active_idx)
        if not overlaps:
            continue
        for s, e in overlaps:
            _accumulate_by_day(total_active_seconds, s, e)
            if _is_coding_event(app, title):
                _accumulate_by_day(coding_seconds, s, e)
            if _is_validation_event(app, title):
                _accumulate_by_day(validation_seconds, s, e)

    commit_daily = defaultdict(lambda: {'commit_count': 0, 'lines_changed': 0, 'files_changed': 0})
    for c in commits:
        day = _utc_day_from_iso(c['date'])
        commit_daily[day]['commit_count'] += 1
        commit_daily[day]['lines_changed'] += c['lines_changed']
        commit_daily[day]['files_changed'] += c['files_changed']

    all_days = sorted(set(commit_daily.keys()) | set(total_active_seconds.keys()))
    daily = []
    x_hours = []
    y_commits = []
    y_lines = []

    for day in all_days:
        coding_h = round(coding_seconds.get(day, 0.0) / 3600.0, 4)
        validation_h = round(validation_seconds.get(day, 0.0) / 3600.0, 4)
        active_h = round(total_active_seconds.get(day, 0.0) / 3600.0, 4)
        c = commit_daily[day]
        row = {
            'day_utc': day,
            'coding_active_hours': coding_h,
            'validation_active_hours': validation_h,
            'total_active_hours': active_h,
            'commit_count': c['commit_count'],
            'lines_changed': c['lines_changed'],
            'files_changed': c['files_changed'],
            'commits_per_coding_hour': (
                round(c['commit_count'] / coding_h, 4) if coding_h > 0 else None
            ),
            'lines_per_coding_hour': (
                round(c['lines_changed'] / coding_h, 4) if coding_h > 0 else None
            ),
        }
        daily.append(row)
        if coding_h > 0:
            x_hours.append(coding_h)
            y_commits.append(c['commit_count'])
            y_lines.append(c['lines_changed'])

    output = {
        'methodology': {
            'join_granularity': 'day_utc',
            'active_filter': 'AFK-trimmed using afkstatus status=not-afk intervals',
            'coding_focus_heuristic': 'editor/terminal/browser app + coding title hints',
            'validation_focus_heuristic': 'browser app + validation title hints',
            'scope': 'sinex commit stream only',
        },
        'inputs': {
            'aw_db_path': aw_db_path,
            'aw_active_interval_count': len(active_intervals),
            'aw_window_event_count': len(window_events),
            'sinex_commit_count': len(commits),
            'sinex_date_utc_min': min(c['date'] for c in commits),
            'sinex_date_utc_max': max(c['date'] for c in commits),
        },
        'summary': {
            'days_total': len(daily),
            'days_with_commits': sum(1 for r in daily if r['commit_count'] > 0),
            'days_with_coding_activity': sum(1 for r in daily if r['coding_active_hours'] > 0),
            'total_coding_hours': round(sum(r['coding_active_hours'] for r in daily), 2),
            'total_validation_hours': round(sum(r['validation_active_hours'] for r in daily), 2),
            'total_commits': sum(r['commit_count'] for r in daily),
            'total_lines_changed': sum(r['lines_changed'] for r in daily),
            'commits_per_coding_hour_global': round(
                sum(r['commit_count'] for r in daily) / max(0.001, sum(r['coding_active_hours'] for r in daily)),
                4,
            ),
            'lines_per_coding_hour_global': round(
                sum(r['lines_changed'] for r in daily) / max(0.001, sum(r['coding_active_hours'] for r in daily)),
                2,
            ),
            'pearson_coding_hours_vs_commits': _pearson(x_hours, y_commits),
            'pearson_coding_hours_vs_lines_changed': _pearson(x_hours, y_lines),
        },
        'daily': daily,
    }

    # Reconstruct coding sessions
    sessions = reconstruct_sessions(
        active_intervals=active_intervals,
        window_events=window_events,
        commits=commits,
    )
    output['coding_sessions'] = sessions
    output['summary']['coding_session_count'] = len(sessions)
    output['summary']['total_coding_session_hours'] = round(
        sum(s['duration_hours'] for s in sessions), 2,
    )

    save_json(out_file, output, sort_keys=True)

    return output


# --- Session reconstruction ---

SESSION_GAP_SECONDS = 30 * 60  # 30 minutes gap → new session


def reconstruct_sessions(active_intervals, window_events, commits, gap_seconds=SESSION_GAP_SECONDS):
    """Reconstruct coding sessions from AFK-filtered coding activity + commits.

    A session is a contiguous block of coding-active time with < gap_seconds
    between coding events. Commits are attributed to the session they fall within.
    """
    # Build coding intervals (AFK-trimmed window events where _is_coding_event)
    coding_intervals = []
    active_idx = 0
    for start_ns, end_ns, app, title in window_events:
        if not _is_coding_event(app, title):
            continue
        overlaps, active_idx = _intersect_with_active(start_ns, end_ns, active_intervals, active_idx)
        for s, e in overlaps:
            coding_intervals.append((s, e))

    if not coding_intervals:
        return []

    coding_intervals.sort()

    # Merge into sessions with gap threshold
    gap_ns = gap_seconds * NS_PER_SECOND
    sessions_raw = []
    cur_start, cur_end = coding_intervals[0]
    for start_ns, end_ns in coding_intervals[1:]:
        if start_ns - cur_end > gap_ns:
            sessions_raw.append((cur_start, cur_end))
            cur_start, cur_end = start_ns, end_ns
        else:
            cur_end = max(cur_end, end_ns)
    sessions_raw.append((cur_start, cur_end))

    # Attribute commits to sessions
    commit_ts_list = []
    for c in commits:
        dt = parse_iso_datetime(c['date'])
        if dt:
            commit_ts_list.append((_to_ns(dt), c))
    commit_ts_list.sort(key=lambda x: x[0])

    results = []
    commit_idx = 0
    for sess_start, sess_end in sessions_raw:
        session_commits = []
        total_additions = 0
        total_deletions = 0
        repos = set()

        # Find commits within or near this session window (± 5 min)
        margin_ns = 5 * 60 * NS_PER_SECOND
        while commit_idx < len(commit_ts_list) and commit_ts_list[commit_idx][0] < sess_start - margin_ns:
            commit_idx += 1
        scan = commit_idx
        while scan < len(commit_ts_list) and commit_ts_list[scan][0] <= sess_end + margin_ns:
            ts, c = commit_ts_list[scan]
            session_commits.append(c['commit_sha'][:10] if 'commit_sha' in c else c.get('sha', '')[:10])
            total_additions += c.get('lines_added', 0) + c.get('additions', 0)
            total_deletions += c.get('lines_deleted', 0) + c.get('deletions', 0)
            # Try to extract repo name from paths
            for root in c.get('path_roots', []):
                repos.add(root.split('/')[0] if '/' in root else root)
            scan += 1

        duration_seconds = (sess_end - sess_start) / NS_PER_SECOND
        results.append({
            'start': datetime.fromtimestamp(sess_start / NS_PER_SECOND, tz=timezone.utc).isoformat(),
            'end': datetime.fromtimestamp(sess_end / NS_PER_SECOND, tz=timezone.utc).isoformat(),
            'duration_hours': round(duration_seconds / 3600.0, 2),
            'commit_count': len(session_commits),
            'commits': session_commits,
            'additions': total_additions,
            'deletions': total_deletions,
            'repos': sorted(repos),
        })

    return results
