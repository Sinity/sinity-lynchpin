"""Chisel — XML repomix snapshots with semantic splitting and GitHub issue commentary.

Produces AI-ready codebase snapshots split by concern (code modules, tests, docs,
issues, log) plus one compressed whole-repo XML per project.
Output under /realm/inbox/store/next/<timestamp>/.
"""
from __future__ import annotations
import datetime as dt
import json
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
try:
    from rich.console import Console
    from rich.table import Table
    _console = Console(highlight=False)
    _has_rich = True
except ImportError:
    _console = None
    _has_rich = False

def _print(*args, **kwargs):
    if _has_rich:
        _console.print(*args, **kwargs)
    else:
        import re
        text = ' '.join((str(a) for a in args))
        text = re.sub('\\[/?\\w+\\]', '', text)
        print(text)
OUTPUT_ROOT_DEFAULT = Path('/realm/inbox/store/next')
DEFAULT_MAX_WORKERS = 4
DEFAULT_SLICE_WORKERS = 4
DEFAULT_ISSUE_LIMIT = 10000
LARGE_SLICE_BYTES = 5000000
_CONTROL_CHARS = bytes((b for b in range(32) if b not in (9, 10, 13))) + b'\x7f'
DEFAULT_IGNORE = ('.git/**', '.direnv/**', '.venv/**', 'venv/**', 'node_modules/**', 'target/**', '**/trybuild-target/**', '.sinex/**', 'dist/**', 'build/**', 'coverage/**', '.cache/**', '.lynchpin/**', '.mypy_cache/**', '__pycache__/**', '*.pyc', 'artefacts/**', 'result/**', 'out/**', '.agent/history-summaries/**', '.agent/scratch/**', '*.lock', '*.db', '*.db-journal', '*.db-wal', '*.db-shm')

def _utc_ts() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H%M%SZ')

def _run(cmd: Sequence[str], *, cwd: Path | None=None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.setdefault('NO_COLOR', '1')
    return subprocess.run(list(cmd), cwd=str(cwd) if cwd else None, text=True, capture_output=True, encoding='utf-8', errors='replace', env=env)

def _require_repomix() -> str:
    bin = shutil.which('repomix')
    if bin is None:
        raise RuntimeError('repomix not found on PATH')
    return bin

def _repomix_version(bin: str) -> str:
    result = _run([bin, '--version'])
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else 'unknown'

def _git_state(repo: Path) -> dict[str, str | bool]:
    branch = _run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], cwd=repo)
    commit = _run(['git', 'rev-parse', 'HEAD'], cwd=repo)
    status = _run(['git', 'status', '--short'], cwd=repo)
    return {'branch': branch.stdout.strip(), 'commit': commit.stdout.strip(), 'dirty': bool(status.stdout.strip())}

def _has_github_remote(repo: Path) -> bool:
    result = _run(['git', 'remote', 'get-url', 'origin'], cwd=repo)
    return result.returncode == 0 and 'github.com' in result.stdout

def _sanitize_xml(path: Path) -> int:
    """Strip control characters from an XML file. Returns number of bytes removed."""
    data = path.read_bytes()
    cleaned = bytes((b for b in data if b not in _CONTROL_CHARS))
    diff = len(data) - len(cleaned)
    if diff:
        path.write_bytes(cleaned)
    return diff

def _validate_xml(path: Path) -> str | None:
    """Check XML well-formedness. Returns None if valid, error string if not."""
    try:
        ET.parse(str(path))
        return None
    except ET.ParseError as e:
        return str(e)

def _fmt_bytes(n: int) -> str:
    if n >= 1000000:
        return f'{n / 1000000:.1f} MB'
    if n >= 1000:
        return f'{n / 1000:.1f} KB'
    return f'{n} B'

@dataclass(frozen=True)
class Slice:
    name: str
    description: str
    include: tuple[str, ...]
    extra_ignore: tuple[str, ...] = ()

@dataclass(frozen=True)
class RepoPlan:
    name: str
    path: Path
    slices: tuple[Slice, ...]
    github_slug: str | None = None
    compressed: bool = True
    extra_ignore: tuple[str, ...] = ()
    extra_copy: tuple[tuple[str, str], ...] = ()
REPO_PLANS: dict[str, RepoPlan] = {}

def _plan(name: str, path: str, github_slug: str | None, *slices: Slice, compressed: bool=True, extra_ignore: tuple[str, ...]=(), extra_copy: tuple[tuple[str, str], ...]=()) -> RepoPlan:
    plan = RepoPlan(name, Path(path), tuple(slices), github_slug, compressed, extra_ignore, extra_copy)
    REPO_PLANS[name] = plan
    return plan
_plan('sinex', '/realm/project/sinex', 'Sinity/sinex', Slice('core-crates', 'Runtime daemons — gateway and ingest daemon', ('crate/core/**',)), Slice('lib-crates', 'Shared libraries — db, primitives, macros, node-sdk, schema', ('crate/lib/**',)), Slice('root-and-config', 'Build metadata, configuration, schemas, top-level docs', ('Cargo.toml', 'Cargo.lock', 'README.md', 'AGENTS.md', 'config/**', 'schemas/**')), Slice('nodes-and-cli', 'Deployable nodes, CLI, xtask, CI, scripts', ('crate/nodes/**', 'crate/cli/**', 'xtask/**', '.github/**', 'scripts/**', 'justfile')), Slice('nixos-deployment', 'NixOS modules, flake, deployment surfaces', ('nixos/**', 'flake.nix', 'rustfmt.toml')), Slice('docs', 'Documentation', ('docs/**', 'xtask/docs/**', 'CLAUDE.md', 'CONTRIBUTING.md')), Slice('tests', 'Test suites and verification', ('tests/**', 'crate/tests/**')))
_plan('sinnix', '/realm/project/sinnix', 'Sinity/sinnix', Slice('hosts-and-modules', 'Host profiles, Nix modules, flake composition', ('hosts/**', 'modules/**', 'flake/**', 'flake.nix')), Slice('scripts-and-dots', 'Scripts, dotfiles, agent control plane, CI', ('scripts/**', 'dots/**', '.github/**', 'README.md', 'CLAUDE.md')))
_plan('polylogue', '/realm/project/polylogue', 'Sinity/polylogue', Slice('core-and-storage', 'Core library, storage backends, schemas, sources', ('polylogue/lib/**', 'polylogue/storage/**', 'polylogue/schemas/**', 'polylogue/sources/**', 'README.md')), Slice('cli-mcp-and-operations', 'CLI, MCP server, operational automation, UI glue', ('polylogue/cli/**', 'polylogue/mcp/**', 'polylogue/operations/**', 'polylogue/ui/**', 'scripts/**', '.github/**', 'AGENTS.md')), Slice('rendering-and-site', 'Rendering engine, site generation, demos, templates', ('polylogue/rendering/**', 'polylogue/site/**', 'polylogue/showcase/**', 'polylogue/templates/**', 'demos/**')), Slice('docs', 'Documentation', ('docs/**', 'CLAUDE.md', 'CHANGELOG.md')), Slice('tests-and-qa', 'Tests and QA campaigns', ('tests/**', 'qa/**')))
_plan('knowledgebase', '/realm/data/knowledgebase', 'Sinity/knowledgebase', Slice('permanent', 'Authored knowledge: reflections, ideas, concepts, self-analysis, MOCs', ('permanent.*',)), Slice('extrinsic-chatlogs-reports', 'AI chatlogs and analysis reports', ('extrinsic.chatlog.*', 'extrinsic.report.*', 'extrinsic.psychometry.*')), Slice('extrinsic-docs-comms', 'External documents, psychometric tests, communications', ('extrinsic.doc.*', 'extrinsic.comms.*', 'extrinsic.misc.*')), Slice('logs-inbox-archive', 'Journals, raw logs, inbox captures, archived notes', ('logs.*', 'inbox.*', 'archive.*')), Slice('infrastructure', 'Vault machinery: schemas, scripts, templates, projects, config', ('schemas/**', 'scripts/**', 'templates.*', 'projects.*', 'root.md', 'root.schema.yml', 'CLAUDE.md', 'dendron.yml', 'plan.txt', 'README.md')), compressed=False, extra_ignore=('store/**', 'assets/**', '90_special/**', '.gitignore'), extra_copy=(('logs.raw-log.md', 'raw-log-copy.md'),))

def _ignore_str(plan: RepoPlan, slice: Slice | None=None) -> str:
    patterns = list(DEFAULT_IGNORE) + list(plan.extra_ignore)
    if slice is not None:
        patterns.extend(slice.extra_ignore)
    return ','.join(patterns)

def _slice_header(plan: RepoPlan, slice: Slice, git: dict, generated_at: str) -> str:
    return '\n'.join((f'Project: {plan.name}', f'Source: {plan.path}', f'Slice: {slice.name} — {slice.description}', f'Generated: {generated_at}', f"Branch: {git['branch']} · Commit: {git['commit']} · Dirty: {git['dirty']}", f"Include: {', '.join(slice.include)}", 'Generated by chisel (lynchpin) via repomix.'))

def _compressed_header(plan: RepoPlan, git: dict, generated_at: str) -> str:
    return '\n'.join((f'Project: {plan.name}', f'Source: {plan.path}', 'Slice: compressed (full repo, Tree-sitter structure extraction)', f'Generated: {generated_at}', f"Branch: {git['branch']} · Commit: {git['commit']} · Dirty: {git['dirty']}", f"Slices this summarises: {', '.join((s.name for s in plan.slices))}", 'Generated by chisel (lynchpin) via repomix.'))

def _run_repomix(repomix_bin: str, output_path: Path, plan: RepoPlan, args: list[str], git: dict, generated_at: str) -> tuple[str, int]:
    """Run repomix. Returns (key, size_bytes)."""
    result = _run([repomix_bin, '.', *args], cwd=plan.path)
    if result.returncode != 0:
        details = (result.stderr or result.stdout or 'repomix failed').strip()
        raise RuntimeError(f'{plan.name}: {details}')
    if not output_path.exists():
        raise RuntimeError(f'{plan.name}: output not written: {output_path}')
    stripped = _sanitize_xml(output_path)
    if stripped:
        _print(f'  [dim]┄ {output_path.name}: {stripped:,} ctrl bytes stripped[/dim]')
    return (output_path.stem, output_path.stat().st_size)

def _run_slice(repomix_bin: str, output_dir: Path, plan: RepoPlan, slice: Slice, git: dict, generated_at: str) -> tuple[str, int, str]:
    output_path = output_dir / f'{slice.name}.xml'
    args = ['--style', 'xml', '--parsable-style', '--quiet', '--no-security-check', '--include-full-directory-structure', '--output-show-line-numbers', '--header-text', _slice_header(plan, slice, git, generated_at), '--include', ','.join(slice.include), '--ignore', _ignore_str(plan, slice), '--output', str(output_path)]
    name, size = _run_repomix(repomix_bin, output_path, plan, args, git, generated_at)
    warn = ' [yellow](large)[/yellow]' if size > LARGE_SLICE_BYTES else ''
    _print(f'  [green]✓[/green] {name}.xml ([dim]{_fmt_bytes(size)}[/dim]){warn}')
    return (name, size)

def _run_compressed(repomix_bin: str, output_dir: Path, plan: RepoPlan, git: dict, generated_at: str) -> tuple[str, int, str]:
    output_path = output_dir / 'compressed.xml'
    include_patterns = sorted({p for s in plan.slices for p in s.include})
    args = ['--style', 'xml', '--parsable-style', '--quiet', '--no-security-check', '--include-full-directory-structure', '--compress', '--remove-empty-lines', '--header-text', _compressed_header(plan, git, generated_at), '--include', ','.join(include_patterns), '--ignore', _ignore_str(plan), '--output', str(output_path)]
    name, size = _run_repomix(repomix_bin, output_path, plan, args, git, generated_at)
    _print(f'  [green]✓[/green] compressed.xml ([dim]{_fmt_bytes(size)}[/dim])')
    return (name, size)
_SCRATCHPAD_IGNORE = ('.git/**', '*.db', '*.db-journal', '*.db-wal', '*.db-shm', '*.lock', '*.pyc')

def _run_scratchpad(repomix_bin: str, output_dir: Path, plan: RepoPlan, git: dict, generated_at: str) -> tuple[str, int] | None:
    scratch_dir = plan.path / '.agent' / 'scratch'
    if not scratch_dir.exists():
        return None
    output_path = output_dir / 'scratchpad.xml'
    header = '\n'.join((f'Project: {plan.name}', f'Source: {plan.path}/.agent/scratch/', 'Slice: scratchpad — working notes, debugging analysis, temporary reasoning', f'Generated: {generated_at}', f"Branch: {git['branch']} · Commit: {git['commit']} · Dirty: {git['dirty']}", 'Generated by chisel (lynchpin) via repomix.'))
    args = ['--style', 'xml', '--parsable-style', '--quiet', '--no-security-check', '--no-gitignore', '--include-full-directory-structure', '--output-show-line-numbers', '--header-text', header, '--include', '.agent/scratch/**', '--ignore', ','.join(_SCRATCHPAD_IGNORE), '--output', str(output_path)]
    name, size = _run_repomix(repomix_bin, output_path, plan, args, git, generated_at)
    _print(f'  [green]✓[/green] scratchpad.xml ([dim]{_fmt_bytes(size)}[/dim])')
    return (name, size)

def _fetch_issues(repo_slug: str, state: str, limit: int, repo_path: Path) -> list[dict]:
    result = _run(['gh', 'issue', 'list', '--repo', repo_slug, '--state', state, '--limit', str(limit), '--json', 'number,title,state,labels,body,url,author,createdAt,updatedAt,closedAt,comments'], cwd=repo_path)
    if result.returncode != 0:
        _print(f'  [yellow]⚠[/yellow] {repo_slug}: gh issue list --state {state} failed: {result.stderr.strip()}')
        return []
    return json.loads(result.stdout)

def _normalize_comments(issues: list[dict]) -> None:
    for iss in issues:
        iss['_comments'] = iss.pop('comments', [])

def _build_issues_xml(issues: list[dict], repo_slug: str, state: str, generated_at: str) -> str:
    root = ET.Element('issues', {'repository': repo_slug, 'state': state, 'generated-at': generated_at, 'count': str(len(issues))})
    for iss in issues:
        el = ET.SubElement(root, 'issue', {'number': str(iss.get('number', '')), 'state': iss.get('state', ''), 'created-at': iss.get('createdAt', ''), 'updated-at': iss.get('updatedAt', ''), 'url': iss.get('url', '')})
        t = ET.SubElement(el, 'title')
        t.text = iss.get('title', '')
        b = ET.SubElement(el, 'body')
        b.text = iss.get('body', '')
        lb = ET.SubElement(el, 'labels')
        lb.text = ', '.join((label['name'] for label in iss.get('labels', [])))
        comments = ET.SubElement(el, 'comments')
        for c in iss.get('_comments', []):
            ce = ET.SubElement(comments, 'comment', {'author': (c.get('author') or {}).get('login', '?'), 'created-at': c.get('createdAt', '')})
            cb = ET.SubElement(ce, 'body')
            cb.text = c.get('body', '')
    ET.indent(root, space='  ')
    return ET.tostring(root, encoding='unicode', xml_declaration=True)

def _generate_issues(plan: RepoPlan, out_dir: Path, generated_at: str) -> tuple[int, int]:
    """Fetch and write issues-open.xml + issues-closed.xml. Returns (open_count, closed_count)."""
    if not plan.github_slug or not _has_github_remote(plan.path):
        return (0, 0)
    open_issues = _fetch_issues(plan.github_slug, 'open', DEFAULT_ISSUE_LIMIT, plan.path)
    _normalize_comments(open_issues)
    closed_issues = _fetch_issues(plan.github_slug, 'closed', DEFAULT_ISSUE_LIMIT, plan.path)
    _normalize_comments(closed_issues)
    count = 0
    for state, issues in [('open', open_issues), ('closed', closed_issues)]:
        if issues:
            xml = _build_issues_xml(issues, plan.github_slug, state, generated_at)
            (out_dir / f'issues-{state}.xml').write_text(xml, encoding='utf-8')
            count += len(issues)
    _print(f'  [dim]issues: {len(open_issues)} open / {len(closed_issues)} closed[/dim]')
    return (len(open_issues), len(closed_issues))

def _generate_git_log(plan: RepoPlan, out_dir: Path, generated_at: str) -> int:
    default_branch = 'HEAD'
    branch_result = _run(['git', 'symbolic-ref', '--short', 'refs/remotes/origin/HEAD'], cwd=plan.path)
    if branch_result.returncode == 0:
        default_branch = branch_result.stdout.strip().removeprefix('origin/')
    else:
        br = _run(['git', 'symbolic-ref', '--short', 'HEAD'], cwd=plan.path)
        if br.returncode == 0:
            default_branch = br.stdout.strip()
    result = _run(['git', 'log', '--first-parent', '--reverse', default_branch, '--format=format:%x00%H%x1f%an%x1f%ae%x1f%aI%x1f%D%x1f%s%x1f%B%x1e'], cwd=plan.path)
    if result.returncode != 0:
        _print(f'  [yellow]⚠[/yellow] {plan.name}: git log failed: {result.stderr.strip()}')
        return 0
    root = ET.Element('git-log', {'repository': str(plan.path), 'branch': default_branch, 'style': 'first-parent', 'generated-at': generated_at})
    count = 0
    for block in result.stdout.split('\x1e'):
        block = block.strip()
        if not block:
            continue
        parts = block.split('\x1f')
        if len(parts) < 7:
            continue
        sha, author, email, date, refs, subject, body = (parts[0], parts[1], parts[2], parts[3], parts[4], parts[5], parts[6])
        commit = ET.SubElement(root, 'commit', {'sha': sha.strip('\x00'), 'author': author, 'email': email, 'date': date})
        if refs.strip():
            commit.set('refs', refs.strip())
        s = ET.SubElement(commit, 'subject')
        s.text = subject
        if body.strip():
            b = ET.SubElement(commit, 'body')
            b.text = body.strip()
        count += 1
    root.set('count', str(count))
    ET.indent(root, space='  ')
    out_path = out_dir / 'git-log.xml'
    out_path.write_text(ET.tostring(root, encoding='unicode', xml_declaration=True), encoding='utf-8')
    _print(f'  [dim]git-log: {count} commits[/dim]')
    return count

def _copy_extras(plan: RepoPlan, out_dir: Path) -> int:
    total = 0
    for src_rel, dst_name in plan.extra_copy:
        src = plan.path / src_rel
        if src.exists():
            dst = out_dir / dst_name
            shutil.copy2(src, dst)
            total += dst.stat().st_size
            _print(f'  [dim]copy: {src_rel} → {dst_name} ({_fmt_bytes(dst.stat().st_size)})[/dim]')
    return total
_TREE_PRUNE_DIRS = {'.git', '.direnv', '.venv', 'node_modules', 'target', 'result', 'vendor'}

def _generate_portable_sidecars(plan: RepoPlan, out_dir: Path) -> tuple[list[str], int]:
    """Write portable GPT-Pro sidecars absent from Chisel's XML surfaces."""
    sidecars: list[str] = []
    total_bytes = 0
    bundle_path = out_dir / f'{plan.name}.bundle'
    bundle = _run(['git', 'bundle', 'create', str(bundle_path), '--all'], cwd=plan.path)
    if bundle.returncode == 0 and bundle_path.exists():
        _print(f'  [green]✓[/green] {bundle_path.name} ([dim]{_fmt_bytes(bundle_path.stat().st_size)}[/dim])')
        sidecars.append(bundle_path.name)
        total_bytes += bundle_path.stat().st_size
    else:
        details = (bundle.stderr or bundle.stdout or 'git bundle failed').strip()
        _print(f'  [yellow]⚠[/yellow] {plan.name}: {details}')
    archive_path = out_dir / f'{plan.name}-HEAD.tar.gz'
    archive = _run(['git', 'archive', '--format=tar.gz', f'--prefix={plan.name}/', 'HEAD', '-o', str(archive_path)], cwd=plan.path)
    if archive.returncode == 0 and archive_path.exists():
        _print(f'  [green]✓[/green] {archive_path.name} ([dim]{_fmt_bytes(archive_path.stat().st_size)}[/dim])')
        sidecars.append(archive_path.name)
        total_bytes += archive_path.stat().st_size
    else:
        details = (archive.stderr or archive.stdout or 'git archive failed').strip()
        _print(f'  [yellow]⚠[/yellow] {plan.name}: {details}')
    tree_path = out_dir / f'{plan.name}-repo-tree.txt'
    tree_path.write_text(_repo_tree(plan.path, max_depth=3), encoding='utf-8')
    _print(f'  [green]✓[/green] {tree_path.name} ([dim]{_fmt_bytes(tree_path.stat().st_size)}[/dim])')
    sidecars.append(tree_path.name)
    total_bytes += tree_path.stat().st_size
    return (sidecars, total_bytes)

def _repo_tree(root: Path, *, max_depth: int) -> str:
    rows: list[str] = ['.']

    def walk(path: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            children = sorted(path.iterdir(), key=lambda child: (not child.is_dir(), child.name.lower()))
        except OSError:
            return
        for child in children:
            if child.is_dir() and child.name in _TREE_PRUNE_DIRS:
                continue
            rel = child.relative_to(root)
            rows.append(f'./{rel.as_posix()}' + ('/' if child.is_dir() else ''))
            if child.is_dir():
                walk(child, depth + 1)
    walk(root, 1)
    return '\n'.join(rows) + '\n'

def _build_one(plan: RepoPlan, output_root: Path, repomix_bin: str, generated_at: str, slice_workers: int) -> dict:
    """Build all slices + compressed + issues + git-log for one repo."""
    if not plan.path.exists():
        return {'project': plan.name, 'status': 'missing'}
    t0 = dt.datetime.now()
    out_dir = output_root / plan.name
    out_dir.mkdir(parents=True, exist_ok=True)
    git = _git_state(plan.path)
    _print(f"\n[bold]{plan.name}[/bold]  [dim]{plan.path}[/dim]  {git['branch']} @ {git['commit'][:8]}")
    slices_done: list[tuple[str, int]] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=slice_workers) as ex:
        futures: dict = {}
        for slice in plan.slices:
            f = ex.submit(_run_slice, repomix_bin, out_dir, plan, slice, git, generated_at)
            futures[f] = ('slice', slice.name)
        if plan.compressed:
            f = ex.submit(_run_compressed, repomix_bin, out_dir, plan, git, generated_at)
            futures[f] = ('compressed', plan.name)
        f = ex.submit(_run_scratchpad, repomix_bin, out_dir, plan, git, generated_at)
        futures[f] = ('scratchpad', plan.name)
        f = ex.submit(_generate_git_log, plan, out_dir, generated_at)
        futures[f] = ('git-log', plan.name)
        f = ex.submit(_generate_issues, plan, out_dir, generated_at)
        futures[f] = ('issues', plan.name)
        f = ex.submit(_generate_portable_sidecars, plan, out_dir)
        futures[f] = ('sidecars', plan.name)
        gitlog_commits = 0
        issues_open = issues_closed = 0
        sidecars_done: list[str] = []
        sidecars_bytes = 0
        for future in as_completed(futures):
            kind, label = futures[future]
            try:
                result = future.result()
                if kind == 'slice':
                    name, size = result
                    slices_done.append((name, size))
                elif kind == 'git-log':
                    gitlog_commits = result
                elif kind == 'issues':
                    issues_open, issues_closed = result
                elif kind == 'compressed':
                    name, size = result
                    slices_done.append((name, size))
                elif kind == 'scratchpad':
                    if result is not None:
                        name, size = result
                        slices_done.append((name, size))
                elif kind == 'sidecars':
                    names, size = result
                    sidecars_done.extend(names)
                    sidecars_bytes += size
            except Exception as e:
                msg = str(e)
                errors.append(f'{kind}: {msg}')
                _print(f'  [red]✗[/red] {kind}: {msg}')
    extra_bytes = _copy_extras(plan, out_dir)
    xml_errors: list[str] = []
    for xml_file in sorted(out_dir.glob('*.xml')):
        err = _validate_xml(xml_file)
        if err:
            xml_errors.append(f'{xml_file.name}: {err}')
    if xml_errors:
        for e in xml_errors:
            _print(f'  [red]✗ XML invalid:[/red] {e}')
    elapsed = (dt.datetime.now() - t0).total_seconds()
    total_bytes = sum((s[1] for s in slices_done)) + extra_bytes + sidecars_bytes
    return {'project': plan.name, 'status': 'partial' if errors else 'generated', 'git': git, 'slices': len(slices_done), 'slice_names': [s[0] for s in slices_done], 'sidecars': sidecars_done, 'total_bytes': total_bytes, 'issues_open': issues_open, 'issues_closed': issues_closed, 'gitlog_commits': gitlog_commits, 'xml_valid': len(xml_errors) == 0, 'xml_errors': xml_errors or None, 'elapsed_s': round(elapsed, 1), 'errors': errors or None}

def build_chisel_bundles(*, project_names: Sequence[str] | None=None, output_root: Path | None=None, max_workers: int=DEFAULT_MAX_WORKERS) -> dict:
    repomix_bin = _require_repomix()
    repomix_ver = _repomix_version(repomix_bin)
    generated_at = _utc_ts()
    output_root = (output_root or OUTPUT_ROOT_DEFAULT / generated_at).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    if project_names:
        unknown = [n for n in project_names if n not in REPO_PLANS]
        if unknown:
            available = ', '.join(sorted(REPO_PLANS))
            raise ValueError(f"unknown projects: {', '.join(unknown)}; available: {available}")
        plans = [REPO_PLANS[n] for n in project_names]
    else:
        plans = list(REPO_PLANS.values())
    _print(f'[bold]Chisel — XML repomix snapshots[/bold]  ({repomix_ver})')
    _print(f'Output: {output_root}')
    _print(f"Repos:  {', '.join((p.name for p in plans))}")
    _print(f'Pools:  {max_workers} across repos × {DEFAULT_SLICE_WORKERS} within each\n')
    results: dict[str, dict] = {}
    t0 = dt.datetime.now()
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_build_one, plan, output_root, repomix_bin, generated_at, DEFAULT_SLICE_WORKERS): plan.name for plan in plans}
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as e:
                results[name] = {'project': name, 'status': 'failed', 'error': str(e)}
                _print(f'  [red]✗[/red] {name}: {e}')
    total_elapsed = round((dt.datetime.now() - t0).total_seconds(), 1)
    if _has_rich:
        table = Table(title=f'Chisel — {generated_at}', title_style='bold')
        table.add_column('Project', style='bold')
        table.add_column('Status')
        table.add_column('Slices', justify='right')
        table.add_column('Issues', justify='right')
        table.add_column('GitLog', justify='right')
        table.add_column('Size', justify='right')
        table.add_column('Valid', justify='center')
        table.add_column('Time', justify='right')
        total_bytes = 0
        total_valid = 0
        total_invalid = 0
        for plan in plans:
            r = results.get(plan.name, {})
            status = r.get('status', '?')
            color = 'green' if status == 'generated' else 'yellow' if status == 'partial' else 'red'
            slices = r.get('slices', 0)
            issues = f"{r.get('issues_open', 0)}o/{r.get('issues_closed', 0)}c"
            commits = str(r.get('gitlog_commits', 0))
            size = r.get('total_bytes', 0)
            total_bytes += size
            valid = r.get('xml_valid', True)
            if valid:
                total_valid += 1
                valid_s = '[green]✓[/green]'
            else:
                total_invalid += 1
                valid_s = '[red]✗[/red]'
            elapsed = f"{r.get('elapsed_s', 0):.1f}s"
            table.add_row(plan.name, f'[{color}]{status}[/{color}]', str(slices), issues, commits, _fmt_bytes(size), valid_s, elapsed)
        table.add_section()
        valid_summary = f'[green]{total_valid}✓[/green]' if total_invalid == 0 else f'[green]{total_valid}✓[/green] [red]{total_invalid}✗[/red]'
        table.add_row('[bold]TOTAL[/bold]', '', '', '', '', _fmt_bytes(total_bytes), valid_summary, f'{total_elapsed:.1f}s')
        _console.print(table)
    else:
        _print(f"\n{'Project':<22} {'Status':<12} {'Slices':>7} {'Issues':>12} {'GitLog':>8} {'Valid':>6} {'Size':>12} {'Time':>8}")
        _print('-' * 95)
        total_bytes = 0
        for plan in plans:
            r = results.get(plan.name, {})
            status = r.get('status', '?')
            slices = r.get('slices', 0)
            issues = f"{r.get('issues_open', 0)}o/{r.get('issues_closed', 0)}c"
            commits = str(r.get('gitlog_commits', 0))
            size = r.get('total_bytes', 0)
            total_bytes += size
            valid = '✓' if r.get('xml_valid', True) else '✗'
            elapsed = f"{r.get('elapsed_s', 0)}s"
            _print(f'{plan.name:<22} {status:<12} {slices:>7} {issues:>12} {commits:>8} {valid:>6} {_fmt_bytes(size):>12} {elapsed:>8}')
        _print('-' * 95)
    all_xml_errors: list[str] = []
    for plan in plans:
        r = results.get(plan.name, {})
        for e in r.get('xml_errors') or []:
            all_xml_errors.append(f'  {plan.name}/{e}')
    if all_xml_errors:
        _print(f'\n[yellow]XML validation issues ({len(all_xml_errors)}):[/yellow]')
        for e in all_xml_errors:
            _print(e)
    else:
        _print('\n[green]All XML outputs well-formed.[/green]')
    _print(f'[dim]Done. {output_root}[/dim]')
    return {'generated_at': generated_at, 'output_root': str(output_root), 'repomix_version': repomix_ver, 'total_elapsed_s': total_elapsed, 'total_bytes': total_bytes, 'projects': results}

def _split_names(value: str) -> list[str] | None:
    names = [item for item in value.split() if item]
    return names or None

def _parse_optional_path(value: str) -> Path | None:
    stripped = value.strip()
    return Path(stripped) if stripped else None

def run_from_cli(argv: list[str] | None=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description='Chisel — XML repomix snapshots with semantic splitting and GitHub issue commentary.')
    ap.add_argument('--projects', default='', help='Whitespace-separated project names (default: all registered).')
    ap.add_argument('--output-root', type=_parse_optional_path, default=None, help=f'Output directory (default: {OUTPUT_ROOT_DEFAULT}/<timestamp>).')
    ap.add_argument('--max-workers', type=int, default=DEFAULT_MAX_WORKERS, help=f'Max parallel repos (default: {DEFAULT_MAX_WORKERS}).')
    ap.add_argument('--list', action='store_true', help='List available project plans and exit.')
    args = ap.parse_args(argv)
    if args.list:
        _print('Available chisel projects:\n')
        for name, plan in sorted(REPO_PLANS.items()):
            slices_str = ', '.join((s.name for s in plan.slices))
            _print(f'  [bold]{name}[/bold]')
            _print(f'    path:       {plan.path}')
            _print(f"    github:     {plan.github_slug or '—'}")
            _print(f"    compressed: {('yes' if plan.compressed else 'no')}")
            _print(f'    slices:     {slices_str}')
            if plan.extra_copy:
                copies = ', '.join((f'{s}→{d}' for s, d in plan.extra_copy))
                _print(f'    copies:     {copies}')
            _print()
        return 0
    build_chisel_bundles(project_names=_split_names(args.projects), output_root=args.output_root, max_workers=args.max_workers)
    return 0
