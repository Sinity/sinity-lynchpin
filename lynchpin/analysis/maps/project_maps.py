"""Module and hotspot map generation."""

from __future__ import annotations

import os
from collections import defaultdict
from collections.abc import Callable
from typing import cast

from ..core.canonical import JsonObject
from ..core.commit_stats import collect_commit_stats
from lynchpin.core.io import resolve_analysis_path, save_json, save_text
from .module_keys import sinex_module_key


SKIP_DIRS = {'.git', 'target', 'node_modules', '.direnv', '__pycache__'}
PathRoleFn = Callable[[str], str]


def _walk_files(root: str, suffixes: list[str]) -> list[str]:
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if any(name.endswith(s) for s in suffixes):
                out.append(os.path.relpath(os.path.join(dirpath, name), root))
    return out


def _sinex_role(rel: str) -> str:
    path = rel.replace('\\', '/')
    lower = path.lower()
    name = os.path.basename(lower)
    if '/tests/' in lower or lower.startswith('tests/') or name.endswith('_test.rs'):
        return 'test'
    if lower.startswith('docs/') or name.endswith('.md'):
        return 'docs'
    if lower.startswith('.github/') or lower.startswith('nixos/') or lower.startswith('scripts/'):
        return 'infra'
    if name in {'cargo.toml', 'cargo.lock', 'flake.nix', 'justfile'}:
        return 'infra'
    if lower.startswith('crate/'):
        return 'code'
    return 'other'


def _counter_row() -> JsonObject:
    return {'file_count': 0, 'roles': defaultdict(int), 'sample_files': []}


def _sample_files(row: JsonObject) -> list[str]:
    return cast(list[str], row['sample_files'])


def _int_map(value: object) -> dict[str, int]:
    return cast(dict[str, int], value)


def _build_module_map_sinex(sinex_repo: str) -> JsonObject:
    files = _walk_files(sinex_repo, suffixes=['.rs'])
    by_module: dict[str, JsonObject] = defaultdict(_counter_row)
    for rel in files:
        key = sinex_module_key(rel)
        by_module[key]['file_count'] += 1
        _int_map(by_module[key]['roles'])[_sinex_role(rel)] += 1
        if len(_sample_files(by_module[key])) < 5:
            _sample_files(by_module[key]).append(rel)

    modules: list[JsonObject] = []
    for key, data in sorted(by_module.items(), key=lambda kv: int(kv[1]['file_count']), reverse=True):
        modules.append(
            {
                'module': key,
                'file_count': data['file_count'],
                'role_breakdown': dict(sorted(_int_map(data['roles']).items())),
                'sample_files': data['sample_files'],
            }
        )
    return {
        'ecosystem': 'sinex',
        'module_count': len(modules),
        'modules': modules,
    }


def _metric_row() -> dict[str, float]:
    return {'commits': 0.0, 'additions': 0.0, 'lines_changed': 0.0, 'files_touched': 0.0}


def _str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _hotspots_from_commits(
    commits: list[JsonObject],
    module_key_fn: PathRoleFn,
    role_fn: PathRoleFn,
    top_n: int = 100,
) -> JsonObject:
    by_module: dict[str, dict[str, float]] = defaultdict(_metric_row)
    by_role: dict[str, dict[str, float]] = defaultdict(_metric_row)
    for c in commits:
        files = _str_list(c.get('files'))
        if not files:
            continue
        modules = [module_key_fn(rel) for rel in files]
        roles = [role_fn(rel) for rel in files]
        unique_modules = sorted(set(modules))
        unique_roles = sorted(set(roles))
        module_count = max(1, len(unique_modules))
        role_count = max(1, len(unique_roles))
        additions = float(c.get('additions', 0) or 0)
        lines_changed = float(c.get('lines_changed', 0) or 0)
        additions_share = additions / module_count
        lines_share = lines_changed / module_count
        additions_share_role = additions / role_count
        lines_share_role = lines_changed / role_count

        file_touches_per_module: dict[str, int] = defaultdict(int)
        file_touches_per_role: dict[str, int] = defaultdict(int)
        for module in modules:
            file_touches_per_module[module] += 1
        for role in roles:
            file_touches_per_role[role] += 1

        for module in unique_modules:
            row = by_module[module]
            row['commits'] += 1
            row['additions'] += additions_share
            row['lines_changed'] += lines_share
            row['files_touched'] += file_touches_per_module[module]
        for role in unique_roles:
            row = by_role[role]
            row['commits'] += 1
            row['additions'] += additions_share_role
            row['lines_changed'] += lines_share_role
            row['files_touched'] += file_touches_per_role[role]

    rows: list[JsonObject] = []
    for module, data in by_module.items():
        score = data['lines_changed'] * 0.7 + data['commits'] * 25 + data['files_touched'] * 5
        rows.append(
            {
                'module': module,
                'score': round(score, 2),
                'commits': data['commits'],
                'additions': round(data['additions'], 2),
                'lines_changed': round(data['lines_changed'], 2),
                'files_touched': data['files_touched'],
            }
        )
    rows.sort(key=lambda r: r['score'], reverse=True)
    by_role_rows: list[JsonObject] = []
    for role, data in by_role.items():
        score = data['lines_changed'] * 0.7 + data['commits'] * 25 + data['files_touched'] * 5
        by_role_rows.append(
            {
                'role': role,
                'score': round(score, 2),
                'commits': data['commits'],
                'additions': round(data['additions'], 2),
                'lines_changed': round(data['lines_changed'], 2),
                'files_touched': data['files_touched'],
            }
        )
    by_role_rows.sort(key=lambda r: r['score'], reverse=True)

    return {
        'top_modules': rows[:top_n],
        'by_role': by_role_rows,
        'top_code_modules': [r for r in rows if not _is_non_code_module_name(r['module'])][:top_n],
    }


def _is_non_code_module_name(name: str) -> bool:
    n = name.lower()
    non_code_prefixes = (
        'docs',
        'test',
        'tests',
        '.github',
        'nixos',
        'scripts',
        'cargo.toml',
        'cargo.lock',
        'flake.nix',
        'justfile',
        'readme.md',
        'claude.md',
    )
    return n.startswith(non_code_prefixes)


def _as_rows(value: object) -> list[JsonObject]:
    return cast(list[JsonObject], value) if isinstance(value, list) else []


def _render_markdown(module_map: JsonObject, hotspot_map: JsonObject) -> str:
    lines: list[str] = []
    lines.append('# Project Maps\n')
    lines.append('## Scope\n')
    lines.append('- Module maps are structural groupings by path conventions.\n')
    lines.append('- Hotspot maps are change-intensity proxies from git commits.\n')
    lines.append('- Use as navigation/orientation artifacts, not as final quality verdicts.\n')

    for eco in ('sinex',):
        m = cast(JsonObject, module_map[eco])
        h = cast(JsonObject, hotspot_map[eco])
        lines.append(f'\n## {eco.upper()} Module Map\n')
        lines.append(f"- Module count: `{m['module_count']}`\n")
        lines.append('\nTop modules by file count:\n')
        for row in _as_rows(m['modules'])[:10]:
            lines.append(f"- `{row['module']}`: `{row['file_count']}` files\n")

        lines.append(f'\n## {eco.upper()} Hotspots\n')
        lines.append('Top modules by hotspot score:\n')
        top_overall = _as_rows(h['top_modules'])[:12]
        for row in top_overall:
            lines.append(
                f"- `{row['module']}`: score `{row['score']}`, commits `{row['commits']}`, "
                f"lines_changed `{row['lines_changed']}`\n"
            )
        lines.append('\nAdditional code-focused modules (not already listed above):\n')
        top_overall_names = {row['module'] for row in top_overall}
        code_only = [row for row in _as_rows(h['top_code_modules']) if row['module'] not in top_overall_names][:12]
        if code_only:
            for row in code_only:
                lines.append(
                    f"- `{row['module']}`: score `{row['score']}`, commits `{row['commits']}`, "
                    f"lines_changed `{row['lines_changed']}`\n"
                )
        else:
            lines.append('- no extra code-only modules beyond top overall hotspots\n')
        lines.append('\nRole-level hotspot summary:\n')
        for row in _as_rows(h['by_role'])[:5]:
            lines.append(
                f"- `{row['role']}`: score `{row['score']}`, commits `{row['commits']}`, "
                f"lines_changed `{row['lines_changed']}`\n"
            )
    return ''.join(lines)


def run_project_maps(spec: JsonObject, module_out: str, hotspot_out: str, markdown_out: str) -> JsonObject:
    sinex_repo = spec['sinex']['repo']

    sinex_commits = collect_commit_stats(
        repo_dir=sinex_repo,
        branch=spec['sinex']['branch'],
        keep_files=True,
    )

    module_map = {
        'sinex': _build_module_map_sinex(sinex_repo),
    }
    hotspot_map = {
        'sinex': {
            'ecosystem': 'sinex',
            **_hotspots_from_commits(sinex_commits, sinex_module_key, _sinex_role),
        },
    }

    module_abs = resolve_analysis_path(module_out)
    hotspot_abs = resolve_analysis_path(hotspot_out)
    markdown_abs = resolve_analysis_path(markdown_out)
    save_json(module_abs, module_map, sort_keys=True)
    save_json(hotspot_abs, hotspot_map, sort_keys=True)
    save_text(markdown_abs, _render_markdown(module_map, hotspot_map))

    return {
        'module_map': module_map,
        'hotspot_map': hotspot_map,
        'markdown_path': markdown_out,
    }
