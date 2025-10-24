"""Change-surface map: deterministic commit clusters linked to touched modules/tests."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import cast

from ..core.canonical import JsonObject
from ..core.commit_stats import collect_commit_stats
from lynchpin.core.io import resolve_analysis_path, save_json, save_text
from .module_keys import Ecosystem, is_test_path, sinex_module_key


def _str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _active_months(commits: list[JsonObject]) -> int:
    return len({str(c['date'])[:7] for c in commits if c.get('date')})


def _primary_root(commit: JsonObject) -> str:
    roots = sorted(_str_list(commit.get('path_roots')))
    return roots[0] if roots else 'unknown'


def _build_change_surface(commits: list[JsonObject], ecosystem: Ecosystem) -> list[JsonObject]:
    module_key_fn = sinex_module_key
    groups: dict[str, JsonObject] = {}

    for c in commits:
        month = str(c.get('date') or '')[:7] or 'unknown-month'
        key = f"{c.get('author') or 'unknown'}:{month}:{_primary_root(c)}"
        files = _str_list(c.get('files'))
        modules = {module_key_fn(rel) for rel in files if rel}
        test_touched = any(is_test_path(rel, ecosystem) for rel in files)

        row = groups.get(key)
        if row is None:
            row = {
                'surface_key': key,
                'ecosystem': ecosystem,
                'author': c.get('author'),
                'month': month,
                'primary_path_root': _primary_root(c),
                'first_commit': c.get('date'),
                'last_commit': c.get('date'),
                'commit_count': 0,
                'modules': set(),
                'test_touched': False,
                'files_touched': 0,
                'additions': 0,
                'lines_changed': 0,
                'commit_shas': [],
            }
            groups[key] = row

        row['commit_count'] += 1
        cast(set[str], row['modules']).update(modules)
        row['test_touched'] = row['test_touched'] or test_touched
        row['files_touched'] += len(files)
        row['additions'] += int(c.get('additions', 0) or 0)
        row['lines_changed'] += int(c.get('lines_changed', 0) or 0)
        row['commit_shas'].append(c['sha'])

        if c.get('date') and c['date'] < row['first_commit']:
            row['first_commit'] = c['date']
        if c.get('date') and c['date'] > row['last_commit']:
            row['last_commit'] = c['date']

    out: list[JsonObject] = []
    for row in groups.values():
        sorted_modules = sorted(cast(set[str], row['modules']))
        row['modules'] = sorted_modules
        row['module_count'] = len(sorted_modules)
        out.append(row)

    out.sort(key=lambda x: (int(x['commit_count']), int(x['module_count']), int(x['lines_changed'])), reverse=True)
    return out


def _summary(rows: list[JsonObject], commits: list[JsonObject]) -> JsonObject:
    modules: dict[str, int] = defaultdict(int)
    for r in rows:
        for m in _str_list(r['modules']):
            modules[m] += 1

    return {
        'change_unit_count': len(rows),
        'active_months': _active_months(commits),
        'change_units_per_active_month': round(len(rows) / max(1, _active_months(commits)), 3),
        'test_touched_rate': round(sum(1 for r in rows if r['test_touched']) / max(1, len(rows)), 3),
        'top_modules_by_change_unit_touches': [
            {'module': k, 'change_unit_count': v}
            for k, v in sorted(modules.items(), key=lambda kv: kv[1], reverse=True)[:20]
        ],
    }


def _render_markdown(payload: JsonObject) -> str:
    lines: list[str] = []
    lines.append('# Change Surface Map\n')
    lines.append('## Scope\n')
    lines.append('- Deterministic change units grouped by `(author, month, primary_path_root)`.\n')
    lines.append('- Built from file-touch transport only (no commit-message semantics).\n')

    for eco in ('sinex',):
        summary = payload['summary'][eco]
        rows = payload['change_surface'][eco]
        lines.append(f'\n## {eco.upper()} Summary\n')
        lines.append(f"- Change units: `{summary['change_unit_count']}`\n")
        lines.append(f"- Change units per active month: `{summary['change_units_per_active_month']}`\n")
        lines.append(f"- Test-touched rate: `{summary['test_touched_rate']}`\n")

        lines.append('\nTop modules by change-unit touches:\n')
        for row in summary['top_modules_by_change_unit_touches'][:10]:
            lines.append(f"- `{row['module']}`: `{row['change_unit_count']}`\n")

        lines.append('\nLargest change units by commit_count:\n')
        for row in rows[:10]:
            lines.append(
                f"- `{row['surface_key']}`: commits `{row['commit_count']}`, modules `{row['module_count']}`, "
                f"test_touched `{row['test_touched']}`\n"
            )

    return ''.join(lines)


def run_change_surface_map(spec: JsonObject, out_file: str, markdown_out: str) -> JsonObject:
    sinex_commits = collect_commit_stats(
        repo_dir=spec['sinex']['repo'],
        branch=spec['sinex']['branch'],
        keep_files=True,
    )

    sinex_rows = _build_change_surface(sinex_commits, ecosystem='sinex')

    payload = {
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'methodology': {
            'grouping': 'author + month + primary_path_root',
            'signal': 'changed files, additions/deletions, path roots',
            'note': 'no commit-message semantic classification is used',
        },
        'summary': {
            'sinex': _summary(sinex_rows, sinex_commits),
        },
        'change_surface': {
            'sinex': sinex_rows,
        },
    }

    out_abs = resolve_analysis_path(out_file)
    md_abs = resolve_analysis_path(markdown_out)
    save_json(out_abs, payload, sort_keys=True)
    save_text(md_abs, _render_markdown(payload))

    return payload
