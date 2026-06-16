"""Chisel — XML repomix snapshots with semantic splitting and GitHub issue commentary.

Produces AI-ready codebase snapshots split by concern (code modules, tests, docs,
issues, log) plus one compressed whole-repo XML per project.
Output under /realm/inbox/store/next/<timestamp>/.
"""
from __future__ import annotations
import datetime as dt
import os
import signal
import shutil
import subprocess
import sys
import threading
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from ..core.errors import MaterializationError, SourceUnavailableError
try:
    from rich.console import Console
    from rich.table import Table
    _console = Console(highlight=False)
    _has_rich = True
except ImportError:
    _console = None
    _has_rich = False

def _print(*args: Any, **kwargs: Any) -> None:
    if _console is not None:
        _console.print(*args, **kwargs)
    else:
        import re
        text = ' '.join((str(a) for a in args))
        text = re.sub('\\[/?\\w+\\]', '', text)
        print(text)
_print_lock = threading.Lock()
_process_lock = threading.Lock()
_active_processes: set[subprocess.Popen[str]] = set()
_abort_event = threading.Event()

def _print_live(*args: Any, **kwargs: Any) -> None:
    with _print_lock:
        _print(*args, **kwargs)

def _emit(log: list[str] | None, message: str) -> None:
    if log is None:
        _print_live(message)
    else:
        log.append(message)
OUTPUT_ROOT_DEFAULT = Path('/realm/inbox/store/next')

def _default_output_root() -> Path:
    """Return the stable canonical output root for materialized code snapshots."""
    from .code_snapshots import code_snapshots_path
    return code_snapshots_path()
DEFAULT_MAX_WORKERS = 4
DEFAULT_SLICE_WORKERS = 2
DEFAULT_REPOMIX_WORKERS = 4
DEFAULT_ISSUE_LIMIT = 10000
LARGE_SLICE_BYTES = 5000000
_repomix_semaphore = threading.Semaphore(DEFAULT_REPOMIX_WORKERS)
_CONTROL_CHARS = bytes((b for b in range(32) if b not in (9, 10, 13))) + b'\x7f'
_WORKTREE_TAR_EXCLUDES: tuple[str, ...] = ('--exclude=.git', '--exclude=.direnv', '--exclude=.venv', '--exclude=venv', '--exclude=node_modules', '--exclude=target', '--exclude=trybuild-target', '--exclude=.sinex', '--exclude=dist', '--exclude=build', '--exclude=coverage', '--exclude=.cache', '--exclude=.lynchpin', '--exclude=.mypy_cache', '--exclude=__pycache__', '--exclude=*.pyc', '--exclude=artefacts', '--exclude=result', '--exclude=out', '--exclude=.agent', '--exclude=*.lock', '--exclude=*.db', '--exclude=*.db-journal', '--exclude=*.db-wal', '--exclude=*.db-shm')
DEFAULT_IGNORE = ('.git/**', '.direnv/**', '.venv/**', 'venv/**', 'node_modules/**', 'target/**', '**/trybuild-target/**', '.sinex/**', 'dist/**', 'build/**', 'coverage/**', '.cache/**', '.lynchpin/**', '.mypy_cache/**', '__pycache__/**', '*.pyc', 'artefacts/**', 'result/**', 'out/**', '.agent/history-summaries/**', '.agent/scratch/**', '*.lock', '*.db', '*.db-journal', '*.db-wal', '*.db-shm')

def _utc_ts() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H%M%SZ')

def _terminate_active_processes() -> None:
    with _process_lock:
        processes = list(_active_processes)
    for proc in processes:
        if proc.poll() is not None:
            continue
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        except OSError:
            proc.terminate()
    for proc in processes:
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                continue
            except OSError:
                proc.kill()

def _run(cmd: Sequence[str], *, cwd: Path | None=None) -> subprocess.CompletedProcess[str]:
    if _abort_event.is_set():
        raise KeyboardInterrupt
    env = os.environ.copy()
    env.setdefault('NO_COLOR', '1')
    proc = subprocess.Popen(list(cmd), cwd=str(cwd) if cwd else None, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8', errors='replace', env=env, start_new_session=True)
    with _process_lock:
        _active_processes.add(proc)
    try:
        stdout, stderr = proc.communicate()
        return subprocess.CompletedProcess(list(cmd), proc.returncode, stdout, stderr)
    except KeyboardInterrupt:
        _abort_event.set()
        _terminate_active_processes()
        raise
    finally:
        with _process_lock:
            _active_processes.discard(proc)

def _require_repomix() -> str:
    bin = shutil.which('repomix')
    if bin is None:
        raise SourceUnavailableError('repomix', reason='repomix not found on PATH')
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

def _run_repomix(repomix_bin: str, output_path: Path, plan: RepoPlan, args: list[str], git: dict, generated_at: str, log: list[str] | None=None) -> tuple[str, int]:
    """Run repomix. Returns (key, size_bytes)."""
    with _repomix_semaphore:
        result = _run([repomix_bin, '.', *args], cwd=plan.path)
    if result.returncode != 0:
        details = (result.stderr or result.stdout or 'repomix failed').strip()
        raise MaterializationError(plan.name, reason=details)
    if not output_path.exists():
        raise MaterializationError(plan.name, reason=f'output not written: {output_path}')
    stripped = _sanitize_xml(output_path)
    if stripped:
        _emit(log, f'  [dim]┄ {output_path.name}: {stripped:,} ctrl bytes stripped[/dim]')
    return (output_path.stem, output_path.stat().st_size)

def _run_slice(repomix_bin: str, output_dir: Path, plan: RepoPlan, slice: Slice, git: dict, generated_at: str, log: list[str] | None=None) -> tuple[str, int]:
    output_path = output_dir / f'{plan.name}-{slice.name}.xml'
    args = ['--style', 'xml', '--parsable-style', '--quiet', '--no-security-check', '--include-full-directory-structure', '--output-show-line-numbers', '--header-text', _slice_header(plan, slice, git, generated_at), '--include', ','.join(slice.include), '--ignore', _ignore_str(plan, slice), '--output', str(output_path)]
    name, size = _run_repomix(repomix_bin, output_path, plan, args, git, generated_at, log)
    warn = ' [yellow](large)[/yellow]' if size > LARGE_SLICE_BYTES else ''
    _emit(log, f'  [green]✓[/green] {name}.xml ([dim]{_fmt_bytes(size)}[/dim]){warn}')
    return (name, size)

def _run_compressed(repomix_bin: str, output_dir: Path, plan: RepoPlan, git: dict, generated_at: str, log: list[str] | None=None) -> tuple[str, int]:
    output_path = output_dir / f'{plan.name}-compressed.xml'
    include_patterns = sorted({p for s in plan.slices for p in s.include})
    args = ['--style', 'xml', '--parsable-style', '--quiet', '--no-security-check', '--include-full-directory-structure', '--compress', '--remove-empty-lines', '--header-text', _compressed_header(plan, git, generated_at), '--include', ','.join(include_patterns), '--ignore', _ignore_str(plan), '--output', str(output_path)]
    name, size = _run_repomix(repomix_bin, output_path, plan, args, git, generated_at, log)
    _emit(log, f'  [green]✓[/green] {output_path.name} ([dim]{_fmt_bytes(size)}[/dim])')
    return (name, size)
_SCRATCHPAD_IGNORE = ('.git/**', '*.db', '*.db-journal', '*.db-wal', '*.db-shm', '*.lock', '*.pyc')

def _run_scratchpad(repomix_bin: str, output_dir: Path, plan: RepoPlan, git: dict, generated_at: str, log: list[str] | None=None) -> tuple[str, int] | None:
    scratch_dir = plan.path / '.agent' / 'scratch'
    if not scratch_dir.exists():
        return None
    if not any((path.is_file() for path in scratch_dir.rglob('*'))):
        return None
    output_path = output_dir / f'{plan.name}-scratchpad.xml'
    header = '\n'.join((f'Project: {plan.name}', f'Source: {plan.path}/.agent/scratch/', 'Slice: scratchpad — working notes, debugging analysis, temporary reasoning', f'Generated: {generated_at}', f"Branch: {git['branch']} · Commit: {git['commit']} · Dirty: {git['dirty']}", 'Generated by chisel (lynchpin) via repomix.'))
    args = ['--style', 'xml', '--parsable-style', '--quiet', '--no-security-check', '--no-gitignore', '--include-full-directory-structure', '--output-show-line-numbers', '--header-text', header, '--include', '.agent/scratch/**', '--ignore', ','.join(_SCRATCHPAD_IGNORE), '--output', str(output_path)]
    try:
        name, size = _run_repomix(repomix_bin, output_path, plan, args, git, generated_at, log)
    except MaterializationError as exc:
        if 'output not written' in exc.reason:
            _emit(log, '  [dim]scratchpad: skipped empty optional slice[/dim]')
            return None
        raise
    _emit(log, f'  [green]✓[/green] {output_path.name} ([dim]{_fmt_bytes(size)}[/dim])')
    return (name, size)
_github_context_lock = threading.Lock()
_github_context_ready: bool | None = None
_github_context_index: dict[tuple[str, str, str, str], list[Any]] | None = None

def _ensure_github_context_for_chisel(projects: set[str] | None=None) -> None:
    global _github_context_index, _github_context_ready
    with _github_context_lock:
        if _github_context_ready is True:
            return
        if _github_context_ready is False:
            raise MaterializationError('github_context', reason='GitHub context materialization already failed in this run')
        from ..ingest.github_context_materialize import materialize_github_context
        try:
            materialize_github_context(projects=projects, progress=_print_live)
        except MaterializationError as exc:
            try:
                _github_context_index = _build_github_context_index()
            except Exception as stale_exc:
                _github_context_ready = False
                raise MaterializationError('github_context', reason=f'GitHub context is unavailable for chisel issue rendering: {exc}; existing product could not be read: {stale_exc}') from exc
            _github_context_ready = True
            _print_live(f'[yellow]GitHub context: refresh failed; using existing context product for issue/PR snapshots ({exc})[/yellow]')
            return
        _github_context_index = _build_github_context_index()
        _github_context_ready = True

def _ensure_chisel_prerequisites(plans: Sequence[RepoPlan]) -> None:
    if not any((plan.github_slug for plan in plans)):
        return
    _print_live('GitHub context: ensure materialized for issue/PR snapshots...')
    t0 = dt.datetime.now()
    _ensure_github_context_for_chisel({plan.name for plan in plans})
    elapsed = (dt.datetime.now() - t0).total_seconds()
    _print_live(f'GitHub context: ready ({elapsed:.1f}s)')

def _build_github_context_index() -> dict[tuple[str, str, str, str], list[Any]]:
    from .github_context import iter_github_context
    index: dict[tuple[str, str, str, str], list[Any]] = {}
    for row in iter_github_context(ensure=False):
        item = row.item
        slug = item.slug.lower()
        if not slug:
            continue
        index.setdefault((row.project, slug, item.kind, item.state), []).append(item)
    return index

def _github_context_items(project: str, repo_slug: str, kind: str, state: str, limit: int) -> list[Any]:
    if _github_context_index is None:
        return []
    return list(_github_context_index.get((project, repo_slug.lower(), kind, state), ())[:limit])

def _issues_from_context_product(project: str, repo_slug: str, state: str, limit: int) -> list[dict]:
    if state == 'all':
        items = [*_github_context_items(project, repo_slug, 'issue', 'open', limit), *_github_context_items(project, repo_slug, 'issue', 'closed', limit)][:limit]
    else:
        items = _github_context_items(project, repo_slug, 'issue', state, limit)
    return [_github_issue_to_chisel_dict(item) for item in items]

def _github_issue_to_chisel_dict(item) -> dict:
    return {'number': item.number, 'state': item.state.upper(), 'title': item.title, 'body': item.body, 'labels': [{'name': label.name} for label in item.labels], 'url': item.url or '', 'createdAt': item.created_at.isoformat() if item.created_at else '', 'updatedAt': item.updated_at.isoformat() if item.updated_at else '', 'closedAt': item.closed_at.isoformat() if item.closed_at else '', 'comments': [{'author': {'login': comment.author.login}, 'body': comment.body, 'createdAt': comment.created_at.isoformat() if comment.created_at else ''} for comment in item.comments]}

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

def _generate_issues(plan: RepoPlan, out_dir: Path, generated_at: str, log: list[str] | None=None) -> tuple[int, int]:
    """Fetch and write issues-open.xml + issues-closed.xml. Returns (open_count, closed_count)."""
    if not plan.github_slug or not _has_github_remote(plan.path):
        return (0, 0)
    _ensure_github_context_for_chisel()
    open_issues = _issues_from_context_product(plan.name, plan.github_slug, 'open', DEFAULT_ISSUE_LIMIT)
    _normalize_comments(open_issues)
    closed_issues = _issues_from_context_product(plan.name, plan.github_slug, 'closed', DEFAULT_ISSUE_LIMIT)
    _normalize_comments(closed_issues)
    count = 0
    for state, issues in [('open', open_issues), ('closed', closed_issues)]:
        if issues:
            xml = _build_issues_xml(issues, plan.github_slug, state, generated_at)
            (out_dir / f'{plan.name}-issues-{state}.xml').write_text(xml, encoding='utf-8')
            count += len(issues)
    _emit(log, f'  [dim]issues: {len(open_issues)} open / {len(closed_issues)} closed[/dim]')
    return (len(open_issues), len(closed_issues))

def _github_pr_to_chisel_dict(item) -> dict:
    return {'number': item.number, 'state': item.state.upper(), 'title': item.title, 'body': item.body, 'labels': [{'name': label.name} for label in item.labels], 'url': item.url or '', 'mergeCommit': item.merge_commit or '', 'createdAt': item.created_at.isoformat() if item.created_at else '', 'mergedAt': item.merged_at.isoformat() if item.merged_at else '', 'comments': [{'author': {'login': comment.author.login}, 'body': comment.body, 'createdAt': comment.created_at.isoformat() if comment.created_at else ''} for comment in item.comments], 'reviews': [{'author': {'login': review.author.login}, 'state': review.state, 'body': review.body, 'submittedAt': review.submitted_at.isoformat() if review.submitted_at else ''} for review in item.reviews]}

def _prs_from_context_product(project: str, repo_slug: str, state: str, limit: int=DEFAULT_ISSUE_LIMIT) -> list[dict]:
    if state == 'all':
        items = [*_github_context_items(project, repo_slug, 'pr', 'open', limit), *_github_context_items(project, repo_slug, 'pr', 'merged', limit)][:limit]
    else:
        items = _github_context_items(project, repo_slug, 'pr', state, limit)
    return [_github_pr_to_chisel_dict(item) for item in items]

def _normalize_pr_data(prs: list[dict]) -> None:
    for pr in prs:
        pr['_comments'] = pr.pop('comments', [])
        pr['_reviews'] = pr.pop('reviews', [])

def _build_prs_xml(prs: list[dict], repo_slug: str, state: str, generated_at: str) -> str:
    root = ET.Element('prs', {'repository': repo_slug, 'state': state, 'generated-at': generated_at, 'count': str(len(prs))})
    for pr in prs:
        el = ET.SubElement(root, 'pr', {'number': str(pr.get('number', '')), 'state': pr.get('state', ''), 'created-at': pr.get('createdAt', ''), 'merged-at': pr.get('mergedAt', ''), 'url': pr.get('url', ''), 'merge-commit': pr.get('mergeCommit', '')})
        t = ET.SubElement(el, 'title')
        t.text = pr.get('title', '')
        b = ET.SubElement(el, 'body')
        b.text = pr.get('body', '')
        lb = ET.SubElement(el, 'labels')
        lb.text = ', '.join((label['name'] for label in pr.get('labels', [])))
        comments = ET.SubElement(el, 'comments')
        for c in pr.get('_comments', []):
            ce = ET.SubElement(comments, 'comment', {'author': (c.get('author') or {}).get('login', '?'), 'created-at': c.get('createdAt', '')})
            cb = ET.SubElement(ce, 'body')
            cb.text = c.get('body', '')
        reviews = ET.SubElement(el, 'reviews')
        for rv in pr.get('_reviews', []):
            re_el = ET.SubElement(reviews, 'review', {'author': (rv.get('author') or {}).get('login', '?'), 'state': rv.get('state', ''), 'submitted-at': rv.get('submittedAt', '')})
            rb = ET.SubElement(re_el, 'body')
            rb.text = rv.get('body', '')
    ET.indent(root, space='  ')
    return ET.tostring(root, encoding='unicode', xml_declaration=True)

def _generate_prs(plan: RepoPlan, out_dir: Path, generated_at: str, log: list[str] | None=None) -> tuple[int, int]:
    """Fetch and write prs-open.xml + prs-merged.xml. Returns (open_count, merged_count)."""
    if not plan.github_slug or not _has_github_remote(plan.path):
        return (0, 0)
    _ensure_github_context_for_chisel()
    open_prs = _prs_from_context_product(plan.name, plan.github_slug, 'open', DEFAULT_ISSUE_LIMIT)
    _normalize_pr_data(open_prs)
    merged_prs = _prs_from_context_product(plan.name, plan.github_slug, 'merged', DEFAULT_ISSUE_LIMIT)
    _normalize_pr_data(merged_prs)
    for state, prs in [('open', open_prs), ('merged', merged_prs)]:
        if prs:
            xml = _build_prs_xml(prs, plan.github_slug, state, generated_at)
            (out_dir / f'{plan.name}-prs-{state}.xml').write_text(xml, encoding='utf-8')
    _emit(log, f'  [dim]prs: {len(open_prs)} open / {len(merged_prs)} merged[/dim]')
    return (len(open_prs), len(merged_prs))

def _generate_git_log(plan: RepoPlan, out_dir: Path, generated_at: str, log: list[str] | None=None) -> int:
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
        _emit(log, f'  [yellow]⚠[/yellow] {plan.name}: git log failed: {result.stderr.strip()}')
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
    out_path = out_dir / f'{plan.name}-git-log.xml'
    out_path.write_text(ET.tostring(root, encoding='unicode', xml_declaration=True), encoding='utf-8')
    _emit(log, f'  [dim]git-log: {count} commits[/dim]')
    return count

def _copy_extras(plan: RepoPlan, out_dir: Path, log: list[str] | None=None) -> int:
    total = 0
    for src_rel, dst_name in plan.extra_copy:
        src = plan.path / src_rel
        if src.exists():
            dst = out_dir / f'{plan.name}-{dst_name}'
            shutil.copy2(src, dst)
            total += dst.stat().st_size
            _emit(log, f'  [dim]copy: {src_rel} → {dst_name} ({_fmt_bytes(dst.stat().st_size)})[/dim]')
    return total
_TREE_PRUNE_DIRS = {'.git', '.direnv', '.venv', 'node_modules', 'target', 'result', 'vendor'}

def _generate_portable_sidecars(plan: RepoPlan, out_dir: Path, log: list[str] | None=None) -> tuple[list[str], int]:
    """Write portable GPT-Pro sidecars absent from Chisel's XML surfaces."""
    sidecars: list[str] = []
    total_bytes = 0
    bundle_path = out_dir / f'{plan.name}.bundle'
    bundle_lock = Path(f'{bundle_path}.lock')
    if bundle_lock.exists():
        bundle_lock.unlink()
        _emit(log, f'  [dim]removed stale bundle lock: {bundle_lock.name}[/dim]')
    bundle = _run(['git', 'bundle', 'create', str(bundle_path), '--all'], cwd=plan.path)
    if bundle.returncode == 0 and bundle_path.exists():
        _emit(log, f'  [green]✓[/green] {bundle_path.name} ([dim]{_fmt_bytes(bundle_path.stat().st_size)}[/dim])')
        sidecars.append(bundle_path.name)
        total_bytes += bundle_path.stat().st_size
    else:
        details = (bundle.stderr or bundle.stdout or 'git bundle failed').strip()
        _emit(log, f'  [yellow]⚠[/yellow] {plan.name}: {details}')
    archive_path = out_dir / f'{plan.name}-working-tree.tar.gz'
    plan_excludes = []
    for pat in plan.extra_ignore:
        p = pat.strip('/').lstrip('**/').rstrip('/**').rstrip('/')
        if p:
            plan_excludes.append(f'--exclude={p}')
    archive = _run(['tar', '-czf', str(archive_path), *_WORKTREE_TAR_EXCLUDES, *plan_excludes, '-C', str(plan.path.parent), plan.path.name])
    if archive.returncode == 0 and archive_path.exists():
        _emit(log, f'  [green]✓[/green] {archive_path.name} ([dim]{_fmt_bytes(archive_path.stat().st_size)}[/dim])')
        sidecars.append(archive_path.name)
        total_bytes += archive_path.stat().st_size
    else:
        details = (archive.stderr or archive.stdout or 'tar failed').strip()
        _emit(log, f'  [yellow]⚠[/yellow] {plan.name}: {details}')
    tree_path = out_dir / f'{plan.name}-repo-tree.txt'
    tree_path.write_text(_repo_tree(plan.path, max_depth=3), encoding='utf-8')
    _emit(log, f'  [green]✓[/green] {tree_path.name} ([dim]{_fmt_bytes(tree_path.stat().st_size)}[/dim])')
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

def _make_combined_tar(plan: RepoPlan, out_dir: Path, output_root: Path, log: list[str] | None=None) -> tuple[str, int] | None:
    """Create a single tar of all files chisel generated for this project."""
    combined_path = output_root / f'{plan.name}-all.tar.gz'
    result = _run(['tar', '-czf', str(combined_path), '-C', str(output_root), plan.name])
    if result.returncode == 0 and combined_path.exists():
        size = combined_path.stat().st_size
        _emit(log, f'  [green]✓[/green] {combined_path.name} ([dim]{_fmt_bytes(size)}[/dim])')
        return (combined_path.name, size)
    details = (result.stderr or result.stdout or 'tar failed').strip()
    _emit(log, f'  [yellow]⚠[/yellow] {plan.name}: combined tar: {details}')
    return None

def _build_one(plan: RepoPlan, output_root: Path, repomix_bin: str, generated_at: str, slice_workers: int) -> dict:
    """Build all slices + compressed + issues + git-log for one repo."""
    log: list[str] = []
    if not plan.path.exists():
        return {'project': plan.name, 'status': 'missing', 'log_lines': [f'[bold]{plan.name}[/bold]  [red]missing[/red]  [dim]{plan.path}[/dim]']}
    t0 = dt.datetime.now()
    out_dir = output_root / plan.name
    out_dir.mkdir(parents=True, exist_ok=True)
    git = _git_state(plan.path)
    planned_outputs = len(plan.slices) + int(plan.compressed) + 5
    if plan.extra_copy:
        planned_outputs += len(plan.extra_copy)
    _emit(log, f"[bold]{plan.name}[/bold]  [dim]{plan.path}[/dim]  {git['branch']} @ {git['commit'][:8]}  [dim]{planned_outputs} planned outputs, {slice_workers} slice workers[/dim]")
    _print_live(f"→ {plan.name}: start  {git['branch']} @ {git['commit'][:8]}  ({planned_outputs} outputs, {slice_workers} slice workers)")
    slices_done: list[tuple[str, int]] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=slice_workers) as ex:
        futures: dict = {}

        def submit(kind: str, label: str, fn, *args):

            def run_logged():
                started = dt.datetime.now()
                _print_live(f'  → {plan.name}: {kind} {label}')
                return (fn(*args), started)
            f = ex.submit(run_logged)
            futures[f] = (kind, label)
        for slice in plan.slices:
            submit('slice', slice.name, _run_slice, repomix_bin, out_dir, plan, slice, git, generated_at, log)
        if plan.compressed:
            submit('compressed', plan.name, _run_compressed, repomix_bin, out_dir, plan, git, generated_at, log)
        submit('scratchpad', plan.name, _run_scratchpad, repomix_bin, out_dir, plan, git, generated_at, log)
        submit('git-log', plan.name, _generate_git_log, plan, out_dir, generated_at, log)
        submit('issues', plan.name, _generate_issues, plan, out_dir, generated_at, log)
        submit('prs', plan.name, _generate_prs, plan, out_dir, generated_at, log)
        submit('sidecars', plan.name, _generate_portable_sidecars, plan, out_dir, log)
        gitlog_commits = 0
        issues_open = issues_closed = 0
        prs_open = prs_merged = 0
        sidecars_done: list[str] = []
        sidecars_bytes = 0
        for future in as_completed(futures):
            kind, label = futures[future]
            try:
                result, started = future.result()
                if kind == 'slice':
                    name, size = result
                    slices_done.append((name, size))
                elif kind == 'git-log':
                    gitlog_commits = result
                elif kind == 'issues':
                    issues_open, issues_closed = result
                elif kind == 'prs':
                    prs_open, prs_merged = result
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
                elapsed = (dt.datetime.now() - started).total_seconds()
                _print_live(f'  ✓ {plan.name}: {kind} {label} ({elapsed:.1f}s)')
            except Exception as e:
                msg = str(e)
                errors.append(f'{kind}: {msg}')
                _emit(log, f'  [red]✗[/red] {kind}: {msg}')
                _print_live(f'  ✗ {plan.name}: {kind} {label}: {msg}')
    extra_bytes = _copy_extras(plan, out_dir, log)
    xml_errors: list[str] = []
    for xml_file in sorted(out_dir.glob('*.xml')):
        err = _validate_xml(xml_file)
        if err:
            xml_errors.append(f'{xml_file.name}: {err}')
    if xml_errors:
        for e in xml_errors:
            _emit(log, f'  [red]✗ XML invalid:[/red] {e}')
    combined_tar_result = _make_combined_tar(plan, out_dir, output_root, log)
    combined_tar_name = combined_tar_result[0] if combined_tar_result is not None else None
    combined_tar_bytes = combined_tar_result[1] if combined_tar_result is not None else 0
    elapsed = (dt.datetime.now() - t0).total_seconds()
    total_bytes = sum((s[1] for s in slices_done)) + extra_bytes + sidecars_bytes
    return {'project': plan.name, 'status': 'partial' if errors else 'generated', 'git': git, 'slices': len(slices_done), 'slice_names': [s[0] for s in slices_done], 'sidecars': sidecars_done, 'combined_tar': combined_tar_name, 'combined_tar_bytes': combined_tar_bytes, 'total_bytes': total_bytes, 'issues_open': issues_open, 'issues_closed': issues_closed, 'prs_open': prs_open, 'prs_merged': prs_merged, 'gitlog_commits': gitlog_commits, 'xml_valid': len(xml_errors) == 0, 'xml_errors': xml_errors or None, 'elapsed_s': round(elapsed, 1), 'errors': errors or None, 'log_lines': log}

def build_chisel_bundles(*, project_names: Sequence[str] | None=None, output_root: Path | None=None, max_workers: int=DEFAULT_MAX_WORKERS) -> dict[str, Any]:
    global _github_context_index, _github_context_ready
    _abort_event.clear()
    _github_context_index = None
    _github_context_ready = None
    repomix_bin = _require_repomix()
    repomix_ver = _repomix_version(repomix_bin)
    generated_at = _utc_ts()
    output_root = (output_root or _default_output_root()).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    if project_names:
        unknown = [n for n in project_names if n not in REPO_PLANS]
        if unknown:
            available = ', '.join(sorted(REPO_PLANS))
            raise ValueError(f"unknown projects: {', '.join(unknown)}; available: {available}")
        plans = [REPO_PLANS[n] for n in project_names]
    else:
        plans = list(REPO_PLANS.values())
    repo_workers = min(max(1, max_workers), max(1, len(plans)))
    slice_workers = DEFAULT_SLICE_WORKERS
    _print(f'[bold]Chisel — XML repomix snapshots[/bold]  ({repomix_ver})')
    _print(f'Output: {output_root}')
    _print(f"Repos:  {len(plans)} selected — {', '.join((p.name for p in plans))}")
    _print(f'Pools:  {repo_workers} across repos × {slice_workers} within each; {DEFAULT_REPOMIX_WORKERS} global repomix slots')
    _print('[dim]Scope:[/dim]')
    for idx, plan in enumerate(plans, start=1):
        planned_outputs = len(plan.slices) + int(plan.compressed) + 5 + len(plan.extra_copy)
        _print(f'  [{idx}/{len(plans)}] {plan.name}: {len(plan.slices)} slices, {planned_outputs} planned outputs -> {output_root / plan.name}')
    _print()
    _ensure_chisel_prerequisites(plans)
    _print()
    results: dict[str, Any] = {}
    t0 = dt.datetime.now()
    ex = ThreadPoolExecutor(max_workers=repo_workers)
    futures = {ex.submit(_build_one, plan, output_root, repomix_bin, generated_at, slice_workers): plan.name for plan in plans}
    try:
        completed = 0
        for future in as_completed(futures):
            name = futures[future]
            completed += 1
            try:
                results[name] = future.result()
                r = results[name]
                status = r.get('status', '?')
                elapsed = r.get('elapsed_s', 0)
                _print(f'\n[bold][{completed}/{len(plans)}] {name} complete[/bold]  {status}  [dim]{elapsed:.1f}s[/dim]')
                for line in r.get('log_lines') or []:
                    _print(line)
            except Exception as e:
                results[name] = {'project': name, 'status': 'failed', 'error': str(e), 'log_lines': [f'  [red]✗[/red] {name}: {e}']}
                _print(f'\n[bold][{completed}/{len(plans)}] {name} failed[/bold]')
                _print(f'  [red]✗[/red] {name}: {e}')
    except KeyboardInterrupt:
        _abort_event.set()
        _terminate_active_processes()
        for future in futures:
            future.cancel()
        ex.shutdown(wait=False, cancel_futures=True)
        _print_live('\n[yellow]Interrupted. Stopped active chisel subprocesses.[/yellow]')
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(130)
    else:
        ex.shutdown(wait=True)
    total_elapsed = round((dt.datetime.now() - t0).total_seconds(), 1)
    if _console is not None:
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
        for xml_err in r.get('xml_errors') or []:
            all_xml_errors.append(f'  {plan.name}/{xml_err}')
    if all_xml_errors:
        _print(f'\n[yellow]XML validation issues ({len(all_xml_errors)}):[/yellow]')
        for xml_err in all_xml_errors:
            _print(xml_err)
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
    ap.add_argument('--output-root', type=_parse_optional_path, default=None, help='Output directory (default: derived_root/code-snapshots — stable, overwrites on re-run).')
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
