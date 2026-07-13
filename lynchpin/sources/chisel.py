"""Chisel — XML repomix snapshots with semantic splitting and GitHub issue commentary.

Produces AI-ready codebase snapshots split by concern (code modules, tests, docs,
issues, log) plus one compressed whole-repo XML per project.
By default outputs are written to the stable derived-data root returned by
``code_snapshots_path()``:

    /realm/data/derived/lynchpin/code-snapshots

Re-running chisel keeps the stable snapshot set current and moves previous
combined ``*-all.tar.gz`` packages into ``archive/<timestamp>/`` before
overwriting them. Pass ``--output-root`` only for explicit one-off exports.
"""

from __future__ import annotations

import datetime as dt
import csv
import fnmatch
import hashlib
import html
import json
import math
import os
import re
import signal
import shutil
import statistics
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

# ═══════════════════════════════════════════════════════════════════════════════
# Rich output (optional)
# ═══════════════════════════════════════════════════════════════════════════════

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

        text = " ".join(str(a) for a in args)
        text = re.sub(r"\[/?\w+\]", "", text)
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


# ═══════════════════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════════════════


def _default_output_root() -> Path:
    """Return the stable canonical output root for materialized code snapshots."""
    from .code_snapshots import code_snapshots_path

    return code_snapshots_path()


DEFAULT_MAX_WORKERS = 4
DEFAULT_SLICE_WORKERS = 2
DEFAULT_REPOMIX_WORKERS = 4
DEFAULT_ISSUE_LIMIT = 10_000
LARGE_SLICE_BYTES = 5_000_000  # warn if a slice exceeds this
_repomix_semaphore = threading.Semaphore(DEFAULT_REPOMIX_WORKERS)

# ANSI escape + control characters to strip from repomix XML output.
# Keep tab (0x09), LF (0x0a), CR (0x0d).
_CONTROL_CHARS = bytes(b for b in range(0x20) if b not in (0x09, 0x0A, 0x0D)) + b"\x7f"

# Tar exclude args derived from DEFAULT_IGNORE for working-tree snapshots.
# Each entry is a GNU tar --exclude argument; order does not matter.
_WORKTREE_TAR_EXCLUDES: tuple[str, ...] = (
    "--exclude=.git",
    "--exclude=.direnv",
    "--exclude=.venv",
    "--exclude=venv",
    "--exclude=node_modules",
    "--exclude=target",
    "--exclude=trybuild-target",
    "--exclude=.sinex",
    "--exclude=dist",
    "--exclude=build",
    "--exclude=coverage",
    "--exclude=.cache",
    "--exclude=.local",
    "--exclude=.lynchpin",
    "--exclude=.claude",
    "--exclude=.serena",
    "--exclude=.env",
    "--exclude=.env.*",
    "--exclude=.mcp.json",
    "--exclude=.cclsp.json",
    "--exclude=token.json",
    "--exclude=credentials.json",
    "--exclude=.mypy_cache",
    "--exclude=.pytest_cache",
    "--exclude=.ruff_cache",
    "--exclude=.playwright-mcp",
    "--exclude=playwright-report",
    "--exclude=test-results",
    "--exclude=__pycache__",
    "--exclude=*.pyc",
    "--exclude=artefacts",
    "--exclude=result",
    "--exclude=out",
    "--exclude=.agent",
    "--exclude=.beads",
    "--exclude=*.lock",
    "--exclude=*.db",
    "--exclude=*.db-journal",
    "--exclude=*.db-wal",
    "--exclude=*.db-shm",
)

DEFAULT_IGNORE = (
    ".git/**",
    ".direnv/**",
    ".venv/**",
    "**/.venv/**",
    "venv/**",
    "node_modules/**",
    "**/node_modules/**",
    "target/**",
    "**/target/**",
    "**/trybuild-target/**",
    ".sinex/**",
    "dist/**",
    "**/dist/**",
    "build/**",
    "**/build/**",
    "coverage/**",
    "**/coverage/**",
    ".cache/**",
    "**/.cache/**",
    ".local/**",
    "**/.local/**",
    ".lynchpin/**",
    "**/.lynchpin/**",
    ".claude/**",
    "**/.claude/**",
    ".serena/**",
    "**/.serena/**",
    ".env",
    ".env.*",
    ".mcp.json",
    ".cclsp.json",
    "token.json",
    "credentials.json",
    ".mypy_cache/**",
    ".pytest_cache/**",
    ".ruff_cache/**",
    "**/.ruff_cache/**",
    ".playwright-mcp/**",
    "**/.playwright-mcp/**",
    "playwright-report/**",
    "**/playwright-report/**",
    "test-results/**",
    "**/test-results/**",
    "__pycache__/**",
    "**/__pycache__/**",
    "*.pyc",
    "artefacts/**",
    "result/**",
    "out/**",
    ".agent/history-summaries/**",
    ".agent/scratch/**",
    ".beads/**",
    "*.lock",
    "*.db",
    "*.db-journal",
    "*.db-wal",
    "*.db-shm",
)


def _utc_ts() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")


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


def _run(
    cmd: Sequence[str], *, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    if _abort_event.is_set():
        raise KeyboardInterrupt
    env = os.environ.copy()
    env.setdefault("NO_COLOR", "1")
    proc = subprocess.Popen(
        list(cmd),
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
        env=env,
        start_new_session=True,
    )
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
    bin = shutil.which("repomix")
    if bin is None:
        raise SourceUnavailableError("repomix", reason="repomix not found on PATH")
    return bin


def _repomix_version(bin: str) -> str:
    result = _run([bin, "--version"])
    return (
        result.stdout.strip()
        if result.returncode == 0 and result.stdout.strip()
        else "unknown"
    )


def _git_state(repo: Path) -> dict[str, str | bool]:
    branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo)
    commit = _run(["git", "rev-parse", "HEAD"], cwd=repo)
    status = _run(["git", "status", "--short"], cwd=repo)
    return {
        "branch": branch.stdout.strip(),
        "commit": commit.stdout.strip(),
        "dirty": bool(status.stdout.strip()),
    }


def _has_github_remote(repo: Path) -> bool:
    from .github import repo_slug

    return repo_slug(repo) is not None


def _sanitize_xml(path: Path) -> int:
    """Strip control characters from an XML file. Returns number of bytes removed."""
    data = path.read_bytes()
    cleaned = bytes(b for b in data if b not in _CONTROL_CHARS)
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
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} MB"
    if n >= 1_000:
        return f"{n / 1_000:.1f} KB"
    return f"{n} B"


def _planned_output_count(plan: RepoPlan) -> int:
    return len(plan.slices) + int(plan.compressed) + 24 + len(plan.extra_copy)


def _print_scope(plans: Sequence[RepoPlan], output_root: Path) -> None:
    if _console is not None:
        table = Table(title="Planned outputs", title_style="bold")
        table.add_column("#", justify="right")
        table.add_column("Project", style="bold")
        table.add_column("Configured slices", justify="right")
        table.add_column("XML snapshots", justify="right")
        table.add_column("Sidecars", justify="right")
        table.add_column("Output")
        for idx, plan in enumerate(plans, start=1):
            xml_snapshots = len(plan.slices) + int(plan.compressed) + 3
            sidecars = _planned_output_count(plan) - xml_snapshots
            table.add_row(
                str(idx),
                plan.name,
                str(len(plan.slices)),
                str(xml_snapshots),
                str(sidecars),
                str(output_root / plan.name),
            )
        _console.print(table)  # type: ignore[possibly-undefined]
        return

    _print("[dim]Scope:[/dim]")
    for idx, plan in enumerate(plans, start=1):
        xml_snapshots = len(plan.slices) + int(plan.compressed) + 3
        sidecars = _planned_output_count(plan) - xml_snapshots
        _print(
            f"  [{idx}/{len(plans)}] {plan.name}: {len(plan.slices)} configured slices, "
            f"{xml_snapshots} XML snapshots, {sidecars} sidecars -> {output_root / plan.name}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Slice definitions
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Slice:
    name: str
    description: str
    include: tuple[str, ...]
    extra_ignore: tuple[str, ...] = ()


@dataclass(frozen=True)
class StatsBucket:
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
    compressed: bool = True  # produce a compressed whole-repo XML
    extra_ignore: tuple[str, ...] = ()
    extra_copy: tuple[tuple[str, str], ...] = ()
    stats_buckets: tuple[StatsBucket, ...] = ()


REPO_PLANS: dict[str, RepoPlan] = {}

SINEX_RUST_SPLIT_TEST_PATTERNS: tuple[str, ...] = (
    "crate/*/src/**/*_test.rs",
    "crate/*/src/**/*_tests.rs",
    "crate/*/src/**/tests.rs",
    "crate/*/src/**/tests/**",
    "xtask/src/**/*_test.rs",
    "xtask/src/**/*_tests.rs",
    "xtask/src/**/tests.rs",
    "xtask/src/**/tests/**",
    "xtask/macros/src/**/*_test.rs",
    "xtask/macros/src/**/*_tests.rs",
    "xtask/macros/src/**/tests.rs",
    "xtask/macros/src/**/tests/**",
)


def _plan(
    name: str,
    path: str,
    github_slug: str | None,
    *slices: Slice,
    compressed: bool = True,
    extra_ignore: tuple[str, ...] = (),
    extra_copy: tuple[tuple[str, str], ...] = (),
    stats_buckets: tuple[StatsBucket, ...] = (),
) -> RepoPlan:
    plan = RepoPlan(
        name,
        Path(path),
        tuple(slices),
        github_slug,
        compressed,
        extra_ignore,
        extra_copy,
        stats_buckets,
    )
    REPO_PLANS[name] = plan
    return plan


# ── sinex ─────────────────────────────────────────────────────────────────────

_plan(
    "sinex",
    "/realm/project/sinex",
    "Sinity/sinex",
    Slice(
        "code-proper",
        "Production Rust crates, CLI, daemon, schemas, and developer tooling source",
        (
            "crate/*/src/**",
            "xtask/src/**",
            "xtask/macros/src/**",
            "Cargo.toml",
            "crate/*/Cargo.toml",
            "xtask/Cargo.toml",
            "xtask/macros/Cargo.toml",
        ),
        extra_ignore=SINEX_RUST_SPLIT_TEST_PATTERNS,
    ),
    Slice(
        "test-suite",
        "Workspace, per-crate, xtask, fuzz, fixture, and VM test surfaces",
        (
            "tests/**",
            "crate/*/tests/**",
            *SINEX_RUST_SPLIT_TEST_PATTERNS,
            "crate/*/fuzz/**",
            "xtask/tests/**",
        ),
    ),
    Slice(
        "docs",
        "Root, architecture, design, per-crate, xtask, NixOS, schema, and test documentation",
        (
            "README.md",
            "TESTING.md",
            "CONTRIBUTING.md",
            "CLAUDE.md",
            "AGENTS.md",
            "docs/**",
            "design/**",
            "crate/*/docs/**",
            "crate/*/README.md",
            "crate/*/DESIGN.md",
            "crate/*/CHANGELOG.md",
            "xtask/docs/**",
            "xtask/README.md",
            "tests/*/README.md",
            "nixos/**/*.md",
            "schemas/README.md",
            "demo/**/README.md",
        ),
    ),
    Slice(
        "agent-instructions",
        "Agent-facing instructions, includes, scripts, and GitHub coordination context",
        (
            ".agent/CONVENTIONS.md",
            ".agent/README.md",
            ".agent/scripts/**",
            ".agent/dev/**",
            ".agent/tools/**",
            ".github/**",
        ),
    ),
    Slice(
        "agent-archive",
        "Archived devloop corpus (retired 2026-07 conductor packet) and external-analysis inbox",
        (
            ".agent/archive/**",
            ".agent/inbox/**",
        ),
        extra_ignore=(".agent/artifacts/**",),
    ),
    Slice(
        "agent-demos",
        "Agent demos and local generated evidence summaries",
        (".agent/demos/**",),
    ),
    Slice(
        "other-project-surface",
        "Build, deployment, schemas, fixtures, configs, examples, and generated contracts",
        (
            ".cargo/**",
            ".config/**",
            ".coderabbit.yaml",
            ".gitguardian.yml",
            ".githooks/**",
            "flake.nix",
            "rust-toolchain.toml",
            "rustfmt.toml",
            "rust-analyzer.toml",
            "nixos/**",
            "schemas/**",
            "demo/**",
            "xtask/cloud/**",
            "xtask/config/**",
            "tests/fixtures/**",
        ),
        extra_ignore=("nixos/**/*.md", "schemas/README.md", "demo/**/README.md"),
    ),
    stats_buckets=(
        StatsBucket(
            "agent-instructions",
            "Agent README, includes, scripts, dev bindings, and GitHub coordination metadata",
            (
                ".agent/CONVENTIONS.md",
                ".agent/README.md",
                ".agent/scripts/**",
                ".agent/dev/**",
                ".agent/tools/**",
                ".github/**",
            ),
        ),
        StatsBucket(
            "agent-archive",
            "Archived devloop corpus (retired 2026-07) and external-analysis inbox",
            (
                ".agent/archive/**",
                ".agent/inbox/**",
            ),
        ),
        StatsBucket(
            "agent-demos",
            "Agent demos and generated demo evidence",
            (".agent/demos/**",),
        ),
        StatsBucket(
            "agent-artifacts",
            "Large local agent artifact imports and downloads, separated from instructions",
            (".agent/artifacts/**",),
        ),
        StatsBucket(
            "test-suite",
            "Workspace, per-crate, xtask, fuzz, fixture, and VM test surfaces",
            (
                "tests/**",
                "crate/*/tests/**",
                *SINEX_RUST_SPLIT_TEST_PATTERNS,
                "crate/*/fuzz/**",
                "xtask/tests/**",
            ),
        ),
        StatsBucket(
            "docs",
            "Root, architecture, design, per-crate, xtask, NixOS, schema, and test documentation",
            (
                "README.md",
                "TESTING.md",
                "CONTRIBUTING.md",
                "CLAUDE.md",
                "AGENTS.md",
                "docs/**",
                "design/**",
                "crate/*/docs/**",
                "crate/*/README.md",
                "crate/*/DESIGN.md",
                "crate/*/CHANGELOG.md",
                "xtask/docs/**",
                "xtask/README.md",
                "tests/*/README.md",
                "nixos/**/*.md",
                "schemas/README.md",
                "demo/**/README.md",
            ),
        ),
        StatsBucket(
            "other-project-surface",
            "Build, deployment, schemas, demos, repo config, fixtures, and generated contracts",
            (
                ".cargo/**",
                ".config/**",
                ".coderabbit.yaml",
                ".gitguardian.yml",
                ".githooks/**",
                "flake.nix",
                "rust-toolchain.toml",
                "rustfmt.toml",
                "rust-analyzer.toml",
                "nixos/**",
                "schemas/**",
                "demo/**",
                "xtask/cloud/**",
                "xtask/config/**",
                "tests/fixtures/**",
            ),
            extra_ignore=("nixos/**/*.md", "schemas/README.md", "demo/**/README.md"),
        ),
        StatsBucket(
            "code-sinexd-runtime",
            "sinexd runtime, parser, source driver, stream, and service internals",
            ("crate/sinexd/src/runtime/**", "crate/sinexd/src/sources/**"),
        ),
        StatsBucket(
            "code-sinexd-api",
            "sinexd API handlers, RPC, SSE, gateway, and surface DTOs",
            ("crate/sinexd/src/api/**",),
        ),
        StatsBucket(
            "code-sinexd-event-engine",
            "sinexd event engine, material assembly, policy, and automata",
            ("crate/sinexd/src/event_engine/**", "crate/sinexd/src/automata/**"),
        ),
        StatsBucket(
            "code-sinexd-other",
            "remaining sinexd production source and manifest",
            ("crate/sinexd/src/**", "crate/sinexd/Cargo.toml"),
        ),
        StatsBucket(
            "code-db",
            "database crate source and manifest",
            (
                "crate/sinex-db/src/**",
                "crate/sinex-db/sql/**",
                "crate/sinex-db/Cargo.toml",
            ),
        ),
        StatsBucket(
            "code-primitives",
            "domain primitives crate source and manifest",
            ("crate/sinex-primitives/src/**", "crate/sinex-primitives/Cargo.toml"),
        ),
        StatsBucket(
            "code-cli",
            "sinexctl CLI source and manifest",
            (
                "crate/sinexctl/src/**",
                "crate/sinexctl/config.example.toml",
                "crate/sinexctl/Cargo.toml",
            ),
        ),
        StatsBucket(
            "code-xtask",
            "xtask command, sandbox, graph, and developer tooling source",
            ("xtask/src/**", "xtask/build.rs", "xtask/Cargo.toml"),
        ),
        StatsBucket(
            "code-schema-macros",
            "schema and macro crates plus xtask macros",
            (
                "crate/sinex-schema/src/**",
                "crate/sinex-schema/Cargo.toml",
                "crate/sinex-macros/src/**",
                "crate/sinex-macros/Cargo.toml",
                "xtask/macros/src/**",
                "xtask/macros/Cargo.toml",
            ),
        ),
        StatsBucket(
            "code-workspace",
            "workspace-level Rust manifests and configuration",
            ("Cargo.toml",),
        ),
    ),
)

# ── sinnix ────────────────────────────────────────────────────────────────────

_plan(
    "sinnix",
    "/realm/project/sinnix",
    "Sinity/sinnix",
    Slice(
        "hosts-and-modules",
        "Host profiles, Nix modules, flake composition",
        ("hosts/**", "modules/**", "flake/**", "flake.nix"),
    ),
    Slice(
        "scripts-and-dots",
        "Scripts, dotfiles, agent control plane, CI",
        ("scripts/**", "dots/**", ".github/**", "README.md", "CLAUDE.md"),
    ),
    stats_buckets=(
        StatsBucket(
            "tests",
            "Nix evaluation, VM, package, and agent-tool verification",
            (
                "flake/test-lib.nix",
                "flake/tests.nix",
                "flake/tests/**",
                "pkgs/*/tests/**",
                "pkgs/*/test_*.py",
                "dots/_ai/skills/*/tests/**",
            ),
        ),
        StatsBucket("hosts", "Host profiles", ("hosts/**",)),
        StatsBucket("modules", "NixOS and Home Manager modules", ("modules/**",)),
        StatsBucket(
            "flake",
            "Flake parts, package data, overlays, and npm metadata",
            ("flake/**", "flake.nix"),
        ),
        StatsBucket(
            "dots", "Home-manager dotfiles and agent configuration", ("dots/**",)
        ),
        StatsBucket("scripts", "Operational scripts", ("scripts/**",)),
        StatsBucket("pkgs", "Local package sources", ("pkgs/**",)),
        StatsBucket(
            "docs",
            "Repository documentation and incident notes",
            ("docs/**", "README.md", "CLAUDE.md"),
        ),
        StatsBucket(
            "agent-context",
            "Agent instructions and GitHub metadata",
            (".agent/**", ".github/**", "agent/**"),
        ),
        StatsBucket(
            "assets-and-eval", "Assets and evaluations", ("assets/**", "eval/**")
        ),
    ),
)

# ── polylogue ─────────────────────────────────────────────────────────────────

_plan(
    "polylogue",
    "/realm/project/polylogue",
    "Sinity/polylogue",
    Slice(
        "core-and-storage",
        "Core library, package roots, storage backends, schemas, sources",
        (
            "polylogue/*.py",
            "polylogue/lib/**",
            "polylogue/storage/**",
            "polylogue/schemas/**",
            "polylogue/sources/**",
            "README.md",
        ),
    ),
    Slice(
        "cli-mcp-and-operations",
        "CLI, MCP server, operational automation, UI glue",
        (
            "polylogue/cli/**",
            "polylogue/mcp/**",
            "polylogue/operations/**",
            "polylogue/ui/**",
            "scripts/**",
            ".github/**",
            "AGENTS.md",
        ),
    ),
    Slice(
        "agent-workspace",
        "Agent conventions, scripts, task ledgers, reports, and tools",
        (
            ".agent/CONVENTIONS.md",
            ".agent/README.md",
            ".agent/scripts/**",
            ".agent/task-history/**",
            ".agent/xtask/**",
            ".agent/tools/**",
            ".agent/reports/**",
            ".agent/learnings.local.md",
            ".github/**",
        ),
        extra_ignore=(".agent/task-history/*.jsonl", ".agent/xtask/*.jsonl"),
    ),
    Slice(
        "agent-demos-and-prompts",
        "Agent demos, cloud prompts, and proposed issue packets",
        (".agent/demos/**", ".agent/cloud-prompts/**", ".agent/proposed_issue_set/**"),
        extra_ignore=(".agent/demos/chatlog-exports/**/full-chatlog/**",),
    ),
    Slice(
        "rendering-and-site",
        "Rendering engine, site generation, demos, templates",
        (
            "polylogue/rendering/**",
            "polylogue/site/**",
            "polylogue/showcase/**",
            "polylogue/templates/**",
            "demos/**",
        ),
    ),
    Slice("docs", "Documentation", ("docs/**", "CLAUDE.md", "CHANGELOG.md")),
    Slice("tests-and-qa", "Tests and QA campaigns", ("tests/**", "qa/**")),
    stats_buckets=(
        StatsBucket(
            "agent-workspace",
            "Agent conventions, scripts, task ledgers, reports, tools, and GitHub metadata",
            (
                ".agent/CONVENTIONS.md",
                ".agent/README.md",
                ".agent/scripts/**",
                ".agent/task-history/**",
                ".agent/xtask/**",
                ".agent/tools/**",
                ".agent/reports/**",
                ".agent/learnings.local.md",
                ".github/**",
            ),
        ),
        StatsBucket(
            "agent-demo-raw-exports",
            "Large raw demo payloads kept out of the default demo context slice",
            (".agent/demos/chatlog-exports/**/full-chatlog/**",),
        ),
        StatsBucket(
            "agent-demos-prompts",
            "Agent demos, cloud prompts, and proposed issue packets",
            (
                ".agent/demos/**",
                ".agent/cloud-prompts/**",
                ".agent/proposed_issue_set/**",
            ),
        ),
        StatsBucket(
            "agent-archive",
            "Archived or retired agent workspace material, separated from active devloop state",
            (".agent/archive/**",),
        ),
        StatsBucket(
            "tests-and-qa",
            "Tests, QA, fixtures, visual and benchmark suites",
            ("tests/**", "qa/**"),
        ),
        StatsBucket(
            "docs",
            "Documentation, plans, product notes, and markdown surfaces",
            (
                "docs/**",
                "README.md",
                "AGENTS.md",
                "CLAUDE.md",
                "CHANGELOG.md",
                "CONTRIBUTING.md",
                "TESTING.md",
            ),
        ),
        StatsBucket(
            "archive-query",
            "Archive query and expression code",
            ("polylogue/archive/query/**",),
        ),
        StatsBucket(
            "archive-data",
            "Archive data, semantic artifacts, and stored products",
            ("polylogue/archive/**", "polylogue/artifacts/**"),
        ),
        StatsBucket(
            "daemon",
            "Daemon runtime, status, HTTP, metrics, and service code",
            ("polylogue/daemon/**",),
        ),
        StatsBucket(
            "api-and-surfaces",
            "API, surfaces, browser capture, telemetry, and public payloads",
            (
                "polylogue/api/**",
                "polylogue/surfaces/**",
                "polylogue/browser_capture/**",
                "polylogue/telemetry/**",
            ),
        ),
        StatsBucket(
            "core-and-storage",
            "Core library, package roots, storage, schemas, sources, paths, and cost modules",
            (
                "polylogue/*.py",
                "polylogue/core/**",
                "polylogue/lib/**",
                "polylogue/storage/**",
                "polylogue/schemas/**",
                "polylogue/sources/**",
                "polylogue/paths/**",
                "polylogue/cost/**",
                "polylogue/publication/**",
            ),
        ),
        StatsBucket(
            "pipeline-product-readiness",
            "Pipeline, product, readiness, insight, and verification code",
            (
                "polylogue/pipeline/**",
                "polylogue/product/**",
                "polylogue/readiness/**",
                "polylogue/insights/**",
                "polylogue/verification/**",
            ),
        ),
        StatsBucket(
            "cli-mcp-operations",
            "CLI, MCP, operations, maintenance, context, and scripts",
            (
                "polylogue/cli/**",
                "polylogue/mcp/**",
                "polylogue/operations/**",
                "polylogue/maintenance/**",
                "polylogue/context/**",
                "scripts/**",
                "systemd/**",
            ),
        ),
        StatsBucket(
            "rendering-and-site",
            "Rendering, UI, site, showcase, templates, scenarios, demos, and browser extension",
            (
                "polylogue/rendering/**",
                "polylogue/ui/**",
                "polylogue/site/**",
                "polylogue/showcase/**",
                "polylogue/templates/**",
                "polylogue/scenarios/**",
                "polylogue/demo/**",
                "demos/**",
                "browser-extension/**",
            ),
        ),
        StatsBucket(
            "devtools-packaging-nix",
            "Developer tools, packaging, contrib, Nix, hooks, and release automation",
            (
                "devtools/**",
                "packaging/**",
                "contrib/**",
                "nix/**",
                "pyproject.toml",
                "flake.nix",
                ".githooks/**",
                ".coderabbit.yaml",
                ".release-please-manifest.json",
                "release-please-config.json",
            ),
        ),
    ),
)

# ── sinity-lynchpin ───────────────────────────────────────────────────────────

_plan(
    "sinity-lynchpin",
    "/realm/project/sinity-lynchpin",
    None,
    Slice(
        "analysis-and-core",
        "Analysis modules, core primitives, config, control plane",
        (
            "lynchpin/analysis/**",
            "lynchpin/core/**",
            "config/**",
            "README.md",
            "CLAUDE.md",
            "pyproject.toml",
        ),
    ),
    Slice("sources", "Read-only data source adapters", ("lynchpin/sources/**",)),
    Slice(
        "composite-graph-spine",
        "Evidence graph, context packs, semantic products",
        ("lynchpin/graph/**",),
    ),
    Slice(
        "cli-and-tooling",
        "CLI entrypoints and tooling",
        ("lynchpin/cli/**", "tool/**", "justfile"),
    ),
    Slice("tests", "Test suites", ("tests/**",)),
    Slice("docs", "Documentation", ("docs/**",)),
    stats_buckets=(
        StatsBucket(
            "analysis", "Analysis modules and reports", ("lynchpin/analysis/**",)
        ),
        StatsBucket(
            "core",
            "Core primitives, parsing, config, cache, and errors",
            ("lynchpin/core/**",),
        ),
        StatsBucket("sources", "Source adapters", ("lynchpin/sources/**",)),
        StatsBucket(
            "graph", "Evidence graph and context-pack spine", ("lynchpin/graph/**",)
        ),
        StatsBucket("mcp", "MCP server tools and read surfaces", ("lynchpin/mcp/**",)),
        StatsBucket(
            "substrate",
            "DuckDB substrate schema, promoters, readers, and snapshots",
            ("lynchpin/substrate/**",),
        ),
        StatsBucket(
            "ingest",
            "Ingest and materialization tools",
            ("lynchpin/ingest/**", "lynchpin/materialization.py"),
        ),
        StatsBucket(
            "cli-tooling",
            "CLI entrypoints, local tools, and justfile",
            ("lynchpin/cli/**", "tool/**", "justfile"),
        ),
        StatsBucket("tests", "Test suite", ("tests/**",)),
        StatsBucket("docs", "Documentation", ("docs/**", "README.md", "CLAUDE.md")),
        StatsBucket(
            "config",
            "Project configuration and generated static surfaces",
            ("pyproject.toml", "config/**", "lynchpin/web/**", "lynchpin/static/**"),
        ),
    ),
    extra_ignore=(
        "retrospective/**",
        ".agent/**",
    ),
)

# ── knowledgebase ─────────────────────────────────────────────────────────────

_plan(
    "knowledgebase",
    "/realm/data/knowledgebase",
    "Sinity/knowledgebase",
    Slice(
        "permanent",
        "Authored knowledge: reflections, ideas, concepts, self-analysis, MOCs",
        ("permanent.*",),
    ),
    Slice(
        "extrinsic-chatlogs-reports",
        "AI chatlogs and analysis reports",
        ("extrinsic.chatlog.*", "extrinsic.report.*", "extrinsic.psychometry.*"),
    ),
    Slice(
        "extrinsic-docs-comms",
        "External documents, psychometric tests, communications",
        ("extrinsic.doc.*", "extrinsic.comms.*", "extrinsic.misc.*"),
    ),
    Slice(
        "logs-inbox-archive",
        "Journals, raw logs, inbox captures, archived notes",
        ("logs.*", "inbox.*", "archive.*"),
    ),
    Slice(
        "infrastructure",
        "Vault machinery: schemas, scripts, templates, projects, config",
        (
            "schemas/**",
            "scripts/**",
            "templates.*",
            "projects.*",
            "root.md",
            "root.schema.yml",
            "CLAUDE.md",
            "dendron.yml",
            "plan.txt",
            "README.md",
        ),
    ),
    compressed=False,  # chatlog noise makes compressed variant less useful
    extra_ignore=(
        "store/**",
        "assets/**",
        "90_special/**",
        ".gitignore",
    ),
    extra_copy=(("logs.raw-log.md", "raw-log-copy.md"),),
)


# ═══════════════════════════════════════════════════════════════════════════════
# Repomix runners
# ═══════════════════════════════════════════════════════════════════════════════


def _ignore_str(plan: RepoPlan, slice: Slice | None = None) -> str:
    patterns = list(DEFAULT_IGNORE) + list(plan.extra_ignore)
    if slice is not None:
        patterns.extend(slice.extra_ignore)
    return ",".join(patterns)


def _slice_header(plan: RepoPlan, slice: Slice, git: dict, generated_at: str) -> str:
    return "\n".join(
        (
            f"Project: {plan.name}",
            f"Source: {plan.path}",
            f"Slice: {slice.name} — {slice.description}",
            f"Generated: {generated_at}",
            f"Branch: {git['branch']} · Commit: {git['commit']} · Dirty: {git['dirty']}",
            f"Include: {', '.join(slice.include)}",
            "Generated by chisel (lynchpin) via repomix.",
        )
    )


def _compressed_header(plan: RepoPlan, git: dict, generated_at: str) -> str:
    return "\n".join(
        (
            f"Project: {plan.name}",
            f"Source: {plan.path}",
            "Slice: compressed (full repo, Tree-sitter structure extraction)",
            f"Generated: {generated_at}",
            f"Branch: {git['branch']} · Commit: {git['commit']} · Dirty: {git['dirty']}",
            f"Slices this summarises: {', '.join(s.name for s in plan.slices)}",
            "Generated by chisel (lynchpin) via repomix.",
        )
    )


def _run_repomix(
    repomix_bin: str,
    output_path: Path,
    plan: RepoPlan,
    args: list[str],
    git: dict,
    generated_at: str,
    log: list[str] | None = None,
) -> tuple[str, int]:
    """Run repomix. Returns (key, size_bytes)."""
    with _repomix_semaphore:
        result = _run([repomix_bin, ".", *args], cwd=plan.path)
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "repomix failed").strip()
        raise MaterializationError(
            plan.name,
            reason=details,
        )
    if not output_path.exists():
        raise MaterializationError(
            plan.name,
            reason=f"output not written: {output_path}",
        )
    stripped = _sanitize_xml(output_path)
    if stripped:
        _emit(
            log, f"  [dim]┄ {output_path.name}: {stripped:,} ctrl bytes stripped[/dim]"
        )
    return output_path.stem, output_path.stat().st_size


def _run_slice(
    repomix_bin: str,
    output_dir: Path,
    plan: RepoPlan,
    slice: Slice,
    git: dict,
    generated_at: str,
    log: list[str] | None = None,
) -> tuple[str, int]:
    output_path = output_dir / f"{plan.name}-{slice.name}.xml"
    gitignore_args = (
        ["--no-gitignore"]
        if any(pattern.startswith(".agent/") for pattern in slice.include)
        else []
    )
    args = [
        "--style",
        "xml",
        "--parsable-style",
        "--quiet",
        "--no-security-check",
        *gitignore_args,
        "--include-full-directory-structure",
        "--output-show-line-numbers",
        "--header-text",
        _slice_header(plan, slice, git, generated_at),
        "--include",
        ",".join(slice.include),
        "--ignore",
        _ignore_str(plan, slice),
        "--output",
        str(output_path),
    ]
    name, size = _run_repomix(
        repomix_bin, output_path, plan, args, git, generated_at, log
    )
    warn = " [yellow](large)[/yellow]" if size > LARGE_SLICE_BYTES else ""
    _emit(log, f"  [green]✓[/green] {name}.xml ([dim]{_fmt_bytes(size)}[/dim]){warn}")
    return name, size


def _run_compressed(
    repomix_bin: str,
    output_dir: Path,
    plan: RepoPlan,
    git: dict,
    generated_at: str,
    log: list[str] | None = None,
) -> tuple[str, int]:
    output_path = output_dir / f"{plan.name}-compressed.xml"
    include_patterns = sorted({p for s in plan.slices for p in s.include})
    args = [
        "--style",
        "xml",
        "--parsable-style",
        "--quiet",
        "--no-security-check",
        "--include-full-directory-structure",
        "--compress",
        "--remove-empty-lines",
        "--header-text",
        _compressed_header(plan, git, generated_at),
        "--include",
        ",".join(include_patterns),
        "--ignore",
        _ignore_str(plan),
        "--output",
        str(output_path),
    ]
    name, size = _run_repomix(
        repomix_bin, output_path, plan, args, git, generated_at, log
    )
    _emit(log, f"  [green]✓[/green] {output_path.name} ([dim]{_fmt_bytes(size)}[/dim])")
    return name, size


_SCRATCHPAD_INCLUDE = (
    ".agent/scratch/*.md",
    ".agent/scratch/current/**/*.md",
    ".agent/scratch/research/**/*.md",
    ".agent/scratch/**/README.md",
    ".agent/scratch/**/INDEX.md",
    ".agent/scratch/**/*index*.md",
)

# Accelerant corpora: GPT-Pro planning packs (task packets, release gates,
# triage matrices, conformance reports) escrowed under .agent/scratch/. Bead
# notes reference these paths; shipping them in the bundle makes those refs
# resolvable in the next planning session — the pack loop closes only if the
# outbound snapshot carries the previous inbound pack. Convention: new packs
# land under .agent/scratch/corpus-*/ (new-gpt-pro/ is a grandfathered name).
_ACCELERANT_INCLUDE = (
    ".agent/scratch/corpus-*/**/*.md",
    ".agent/scratch/corpus-*/**/*.yaml",
    ".agent/scratch/corpus-*/**/*.csv",
    ".agent/scratch/new-gpt-pro/**/*.md",
    ".agent/scratch/new-gpt-pro/**/*.csv",
    ".agent/scratch/new/**/*.md",
    ".agent/scratch/new/**/*.csv",
)

_ACCELERANT_IGNORE = (
    "**/prework-v1-superseded/**",
    "**/zips/**",
    # Self-symlink (task_packets -> .) used to repair doubled path segments in
    # bead notes; guard against pattern-expansion recursion through it.
    "**/task_packets/task_packets/**",
    # Captured-session transcript exports (uuid-named) ride the archive lane,
    # not the accelerant lane — the packs distilled FROM them are what ship.
    "**/????????-????-????-????-????????????.md",
)

_ACCELERANT_DIR_GLOBS = ("corpus-*", "new-gpt-pro", "new")


def _run_scratchpad(
    repomix_bin: str,
    output_dir: Path,
    plan: RepoPlan,
    git: dict,
    generated_at: str,
    log: list[str] | None = None,
) -> tuple[str, int] | None:
    scratch_dir = plan.path / ".agent" / "scratch"
    if not scratch_dir.exists():
        return None
    if not any(path.is_file() for path in scratch_dir.rglob("*")):
        return None
    output_path = output_dir / f"{plan.name}-scratchpad.xml"
    header = "\n".join(
        (
            f"Project: {plan.name}",
            f"Source: {plan.path}/.agent/scratch/",
            "Slice: scratchpad — working notes, debugging analysis, temporary reasoning",
            f"Generated: {generated_at}",
            f"Branch: {git['branch']} · Commit: {git['commit']} · Dirty: {git['dirty']}",
            "Generated by chisel (lynchpin) via repomix.",
        )
    )
    args = [
        "--style",
        "xml",
        "--parsable-style",
        "--quiet",
        "--no-security-check",
        "--no-gitignore",
        "--include-full-directory-structure",
        "--output-show-line-numbers",
        "--header-text",
        header,
        "--include",
        ",".join(_SCRATCHPAD_INCLUDE),
        "--output",
        str(output_path),
    ]
    try:
        name, size = _run_repomix(
            repomix_bin, output_path, plan, args, git, generated_at, log
        )
    except MaterializationError as exc:
        if "output not written" in exc.reason:
            _emit(log, "  [dim]scratchpad: skipped empty optional slice[/dim]")
            return None
        raise
    _emit(log, f"  [green]✓[/green] {output_path.name} ([dim]{_fmt_bytes(size)}[/dim])")
    return name, size


def _run_accelerants(
    repomix_bin: str,
    output_dir: Path,
    plan: RepoPlan,
    git: dict,
    generated_at: str,
    log: list[str] | None = None,
) -> tuple[str, int] | None:
    """Optional slice over GPT-Pro accelerant corpora (.agent/scratch/corpus-*)."""
    scratch_dir = plan.path / ".agent" / "scratch"
    if not scratch_dir.exists():
        return None
    corpus_dirs = [
        p
        for glob in _ACCELERANT_DIR_GLOBS
        for p in scratch_dir.glob(glob)
        if p.is_dir()
    ]
    if not corpus_dirs:
        return None
    output_path = output_dir / f"{plan.name}-accelerants.xml"
    header = "\n".join(
        (
            f"Project: {plan.name}",
            f"Source: {plan.path}/.agent/scratch/corpus-*/ (+ new-gpt-pro/)",
            "Slice: accelerants — GPT-Pro planning packs: task packets, release gates,"
            " triage matrices, conformance reports. Bead notes reference these paths.",
            f"Generated: {generated_at}",
            f"Branch: {git['branch']} · Commit: {git['commit']} · Dirty: {git['dirty']}",
            "Generated by chisel (lynchpin) via repomix.",
        )
    )
    args = [
        "--style",
        "xml",
        "--parsable-style",
        "--quiet",
        "--no-security-check",
        "--no-gitignore",
        "--include-full-directory-structure",
        "--output-show-line-numbers",
        "--header-text",
        header,
        "--include",
        ",".join(_ACCELERANT_INCLUDE),
        "--ignore",
        ",".join(_ACCELERANT_IGNORE),
        "--output",
        str(output_path),
    ]
    try:
        name, size = _run_repomix(
            repomix_bin, output_path, plan, args, git, generated_at, log
        )
    except MaterializationError as exc:
        if "output not written" in exc.reason:
            _emit(log, "  [dim]accelerants: skipped empty optional slice[/dim]")
            return None
        raise
    _emit(log, f"  [green]✓[/green] {output_path.name} ([dim]{_fmt_bytes(size)}[/dim])")
    return name, size


# ═══════════════════════════════════════════════════════════════════════════════
# Tokei attribution stats
# ═══════════════════════════════════════════════════════════════════════════════


_STAT_KEYS = ("blanks", "code", "comments")


def _normalize_rel_pattern(value: str) -> str:
    value = value.strip()
    while value.startswith("./"):
        value = value[2:]
    return value


def _glob_matches(rel_path: str, pattern: str) -> bool:
    rel_path = _normalize_rel_pattern(rel_path)
    pattern = _normalize_rel_pattern(pattern)
    if not pattern:
        return False
    if "/" not in pattern:
        return any(fnmatch.fnmatchcase(part, pattern) for part in rel_path.split("/"))
    rel_parts = tuple(part for part in rel_path.split("/") if part)
    pattern_parts = tuple(part for part in pattern.split("/") if part)

    def match_from(path_idx: int, pattern_idx: int) -> bool:
        if pattern_idx == len(pattern_parts):
            return path_idx == len(rel_parts)
        part = pattern_parts[pattern_idx]
        if part == "**":
            return any(
                match_from(next_idx, pattern_idx + 1)
                for next_idx in range(path_idx, len(rel_parts) + 1)
            )
        if path_idx >= len(rel_parts):
            return False
        if not fnmatch.fnmatchcase(rel_parts[path_idx], part):
            return False
        return match_from(path_idx + 1, pattern_idx + 1)

    if match_from(0, 0):
        return True
    if pattern.endswith("/**"):
        prefix = pattern[:-3].rstrip("/")
        if not any(char in prefix for char in "*?["):
            return rel_path == prefix or rel_path.startswith(f"{prefix}/")
    return False


def _glob_any(rel_path: str, patterns: Sequence[str]) -> bool:
    return any(_glob_matches(rel_path, pattern) for pattern in patterns)


def _stats_buckets(plan: RepoPlan) -> tuple[StatsBucket, ...]:
    if plan.stats_buckets:
        return plan.stats_buckets
    return tuple(
        StatsBucket(slice.name, slice.description, slice.include, slice.extra_ignore)
        for slice in plan.slices
    )


def _classify_stats_bucket(plan: RepoPlan, rel_path: str) -> str:
    rel_path = _normalize_rel_pattern(rel_path)
    for bucket in _stats_buckets(plan):
        if _glob_any(rel_path, bucket.extra_ignore):
            continue
        if _glob_any(rel_path, bucket.include):
            return bucket.name
    return "other"


def _empty_stats_bucket(description: str) -> dict[str, Any]:
    return {
        "description": description,
        "files": 0,
        "blanks": 0,
        "code": 0,
        "comments": 0,
        "lines": 0,
        "languages": {},
    }


def _add_stats(target: dict[str, Any], stats: dict[str, Any]) -> None:
    for key in _STAT_KEYS:
        target[key] += int(stats.get(key) or 0)
    target["lines"] += sum(int(stats.get(key) or 0) for key in _STAT_KEYS)


def _add_language_stats(
    bucket: dict[str, Any], language: str, stats: dict[str, Any], *, count_file: bool
) -> None:
    languages = bucket["languages"]
    entry = languages.setdefault(
        language, {"files": 0, "blanks": 0, "code": 0, "comments": 0, "lines": 0}
    )
    if count_file:
        entry["files"] += 1
    _add_stats(entry, stats)


def _add_report_stats(
    bucket: dict[str, Any], language: str, stats: dict[str, Any]
) -> None:
    bucket["files"] += 1
    _add_stats(bucket, stats)
    _add_language_stats(bucket, language, stats, count_file=True)
    for embedded_language, embedded_stats in (stats.get("blobs") or {}).items():
        _add_stats(bucket, embedded_stats)
        _add_language_stats(bucket, embedded_language, embedded_stats, count_file=False)


def _tokei_exclude_args(plan: RepoPlan) -> list[str]:
    args: list[str] = []
    for pattern in (*DEFAULT_IGNORE, *plan.extra_ignore):
        args.extend(["-e", pattern])
    return args


def _read_loc_ignore_rules(repo: Path) -> list[tuple[bool, str]]:
    """Read LOC-specific ignore files using the common gitignore subset.

    Git decides which untracked files are repository-visible. ``.ignore`` and
    ``.tokeignore`` then provide tool-specific exclusions, including for files
    that are tracked. Negations are applied in declaration order.
    """
    rules: list[tuple[bool, str]] = []
    for name in (".ignore", ".tokeignore"):
        path = repo / name
        if not path.is_file():
            continue
        for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            negated = line.startswith("!")
            pattern = line[1:] if negated else line
            pattern = pattern.removeprefix("/")
            if pattern.endswith("/"):
                pattern = f"{pattern.rstrip('/')}/**"
            rules.append((negated, pattern))
    return rules


def _ignore_rule_matches(rel_path: str, pattern: str) -> bool:
    if "/" not in pattern:
        return any(fnmatch.fnmatchcase(part, pattern) for part in rel_path.split("/"))
    return _glob_matches(rel_path, pattern)


def _loc_policy_ignores(rel_path: str, rules: Sequence[tuple[bool, str]]) -> bool:
    ignored = False
    for negated, pattern in rules:
        if _ignore_rule_matches(rel_path, pattern):
            ignored = not negated
    return ignored


def _tokei_input_paths(plan: RepoPlan) -> tuple[list[str], str]:
    """Return the tracked plus non-ignored working-tree files to measure."""
    result = _run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=plan.path,
    )
    if result.returncode != 0:
        return ["."], "filesystem-with-native-ignore-files"

    loc_rules = _read_loc_ignore_rules(plan.path)
    paths = sorted(
        {_normalize_rel_pattern(path) for path in result.stdout.split("\0") if path}
    )
    paths = [
        path
        for path in paths
        if not _glob_any(path, (*DEFAULT_IGNORE, *plan.extra_ignore))
        and not _loc_policy_ignores(path, loc_rules)
        and (plan.path / path).is_file()
    ]
    return paths, "git-tracked-and-nonignored-working-tree"


def _relative_tokei_report_name(plan: RepoPlan, name: str) -> str:
    path = Path(name)
    try:
        return path.resolve().relative_to(plan.path.resolve()).as_posix()
    except (OSError, ValueError):
        return _normalize_rel_pattern(name)


def _collect_tokei_stats(plan: RepoPlan, generated_at: str) -> dict[str, Any]:
    input_paths, input_policy = _tokei_input_paths(plan)
    command = [
        "tokei",
        "--hidden",
        "--files",
        "--output",
        "json",
        *_tokei_exclude_args(plan),
    ]
    if input_policy == "git-tracked-and-nonignored-working-tree":
        # Paths have already passed Git plus repository LOC policy. Disable
        # Tokei's traversal filters so tracked files under selectively ignored
        # roots remain measurable, then pass only the approved file set.
        command.extend(("--no-ignore", "--", *input_paths))
    else:
        command.extend(("--", "."))
    if input_paths:
        result = _run(command, cwd=plan.path)
        if result.returncode != 0:
            details = (result.stderr or result.stdout or "tokei failed").strip()
            raise MaterializationError(plan.name, reason=details)
        raw = json.loads(result.stdout)
    else:
        raw = {}
    bucket_descriptions = {
        bucket.name: bucket.description for bucket in _stats_buckets(plan)
    }
    buckets = {
        name: _empty_stats_bucket(description)
        for name, description in bucket_descriptions.items()
    }
    buckets["other"] = _empty_stats_bucket(
        "Files not matched by the explicit attribution buckets"
    )

    files: list[dict[str, Any]] = []
    for language, language_stats in raw.items():
        if language == "Total":
            continue
        for report in language_stats.get("reports") or []:
            rel_path = _relative_tokei_report_name(plan, str(report.get("name", "")))
            bucket_name = _classify_stats_bucket(plan, rel_path)
            bucket = buckets.setdefault(
                bucket_name,
                _empty_stats_bucket(bucket_descriptions.get(bucket_name, "")),
            )
            stats = report.get("stats") or {}
            _add_report_stats(bucket, language, stats)
            files.append(
                {
                    "path": rel_path,
                    "bucket": bucket_name,
                    "language": language,
                    "blanks": int(stats.get("blanks") or 0),
                    "code": int(stats.get("code") or 0),
                    "comments": int(stats.get("comments") or 0),
                    "lines": sum(int(stats.get(key) or 0) for key in _STAT_KEYS),
                }
            )

    for bucket in buckets.values():
        bucket["languages"] = dict(
            sorted(
                bucket["languages"].items(),
                key=lambda item: (-item[1]["lines"], item[0]),
            )
        )

    return {
        "project": plan.name,
        "source": str(plan.path),
        "generated_at": generated_at,
        "input_policy": input_policy,
        "input_files": len(input_paths),
        "buckets": dict(
            sorted(
                buckets.items(),
                key=lambda item: (
                    999
                    if item[0] == "other"
                    else list(bucket_descriptions).index(item[0])
                    if item[0] in bucket_descriptions
                    else 998,
                    item[0],
                ),
            )
        ),
        "files": sorted(files, key=lambda row: (row["bucket"], row["path"])),
        "rust_inline_tests": _rust_inline_test_stats(plan, set(input_paths)),
        "rust_split_test_files": _rust_split_test_file_stats(plan, set(input_paths)),
    }


def _member_name(rel_path: str) -> str:
    parts = rel_path.split("/")
    if len(parts) >= 2 and parts[0] in {"crate", "tests"}:
        return f"{parts[0]}/{parts[1]}"
    return parts[0] if parts else ""


def _rust_inline_test_stats(
    plan: RepoPlan, visible_paths: set[str] | None = None
) -> dict[str, Any]:
    by_member: dict[str, dict[str, Any]] = {}
    largest: list[dict[str, Any]] = []
    total_blocks = 0
    total_lines = 0
    total_files = 0

    for path in sorted(plan.path.rglob("*.rs")):
        try:
            rel_path = path.relative_to(plan.path).as_posix()
        except ValueError:
            continue
        if visible_paths is not None and rel_path not in visible_paths:
            continue
        if _glob_any(rel_path, (*DEFAULT_IGNORE, *plan.extra_ignore)):
            continue
        if _glob_any(rel_path, SINEX_RUST_SPLIT_TEST_PATTERNS):
            continue
        if "/src/" not in rel_path:
            continue
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        blocks = _rust_inline_test_blocks(lines)
        if not blocks:
            continue
        file_lines = sum(block["lines"] for block in blocks)
        total_files += 1
        total_blocks += len(blocks)
        total_lines += file_lines
        member = _member_name(rel_path)
        entry = by_member.setdefault(member, {"files": 0, "blocks": 0, "lines": 0})
        entry["files"] += 1
        entry["blocks"] += len(blocks)
        entry["lines"] += file_lines
        largest.append(
            {
                "path": rel_path,
                "blocks": len(blocks),
                "lines": file_lines,
                "file_lines": len(lines),
            }
        )

    return {
        "files": total_files,
        "blocks": total_blocks,
        "lines": total_lines,
        "by_member": dict(
            sorted(by_member.items(), key=lambda item: (-item[1]["lines"], item[0]))
        ),
        "largest_files": sorted(largest, key=lambda row: (-row["lines"], row["path"]))[
            :25
        ],
        "note": (
            "These lines are inside #[cfg(test)] mod tests blocks in src files. "
            "They are counted by tokei in the owning source file's bucket because "
            "tokei is file-oriented, not Rust item-oriented."
        ),
    }


def _rust_split_test_file_stats(
    plan: RepoPlan, visible_paths: set[str] | None = None
) -> dict[str, Any]:
    by_member: dict[str, dict[str, Any]] = {}
    largest: list[dict[str, Any]] = []
    total_lines = 0
    total_files = 0

    for path in sorted(plan.path.rglob("*.rs")):
        try:
            rel_path = path.relative_to(plan.path).as_posix()
        except ValueError:
            continue
        if visible_paths is not None and rel_path not in visible_paths:
            continue
        if _glob_any(rel_path, (*DEFAULT_IGNORE, *plan.extra_ignore)):
            continue
        if not _glob_any(rel_path, SINEX_RUST_SPLIT_TEST_PATTERNS):
            continue
        line_count = len(path.read_text(encoding="utf-8", errors="ignore").splitlines())
        total_files += 1
        total_lines += line_count
        member = _member_name(rel_path)
        entry = by_member.setdefault(member, {"files": 0, "lines": 0})
        entry["files"] += 1
        entry["lines"] += line_count
        largest.append({"path": rel_path, "lines": line_count})

    return {
        "files": total_files,
        "lines": total_lines,
        "by_member": dict(
            sorted(by_member.items(), key=lambda item: (-item[1]["lines"], item[0]))
        ),
        "largest_files": sorted(largest, key=lambda row: (-row["lines"], row["path"]))[
            :25
        ],
        "note": (
            "These are Rust test-only files colocated under src/ and routed to "
            "the test-suite bucket instead of production code slices."
        ),
    }


def _rust_inline_test_blocks(lines: Sequence[str]) -> list[dict[str, int]]:
    blocks: list[dict[str, int]] = []
    i = 0
    while i < len(lines):
        if not lines[i].strip().startswith("#[cfg(test"):
            i += 1
            continue
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        k = j
        while k < len(lines) and (
            lines[k].strip().startswith("#[") or lines[k].strip().startswith("//")
        ):
            k += 1
        if k >= len(lines) or "mod tests" not in lines[k]:
            i += 1
            continue
        start = i
        if lines[k].strip().endswith(";"):
            end = k
        else:
            depth = 0
            seen_open = False
            end = k
            for n in range(k, len(lines)):
                for char in lines[n]:
                    if char == "{":
                        depth += 1
                        seen_open = True
                    elif char == "}":
                        depth -= 1
                end = n
                if seen_open and depth <= 0:
                    break
        blocks.append(
            {"start_line": start + 1, "end_line": end + 1, "lines": end - start + 1}
        )
        i = end + 1
    return blocks


def _stats_markdown(plan: RepoPlan, stats: dict[str, Any]) -> str:
    lines = [
        f"# {plan.name} tokei attribution stats",
        "",
        f"Generated: {stats['generated_at']}",
        f"Source: `{stats['source']}`",
        f"Input policy: `{stats.get('input_policy', 'unknown')}` ({stats.get('input_files', 0):,} files)",
        "",
        "## Buckets",
        "",
        "| Bucket | Files | Lines | Code | Comments | Blanks | Top languages |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for name, bucket in stats["buckets"].items():
        top_languages = ", ".join(
            f"{language} {values['lines']:,}"
            for language, values in list(bucket["languages"].items())[:4]
        )
        lines.append(
            f"| `{name}` | {bucket['files']:,} | {bucket['lines']:,} | "
            f"{bucket['code']:,} | {bucket['comments']:,} | {bucket['blanks']:,} | "
            f"{top_languages or '-'} |"
        )
    lines.extend(
        (
            "",
            "## Inline Rust Tests",
            "",
        )
    )
    inline = stats.get("rust_inline_tests") or {}
    if inline.get("blocks"):
        lines.extend(
            (
                f"- Files with inline test modules: {inline['files']:,}",
                f"- Inline `#[cfg(test)] mod tests` blocks: {inline['blocks']:,}",
                f"- Approximate inline test lines: {inline['lines']:,}",
                "",
                "| Member | Files | Blocks | Lines |",
                "| --- | ---: | ---: | ---: |",
            )
        )
        for member, values in list((inline.get("by_member") or {}).items())[:12]:
            lines.append(
                f"| `{member}` | {values['files']:,} | {values['blocks']:,} | {values['lines']:,} |"
            )
        lines.extend(
            (
                "",
                "Largest inline-test source files:",
                "",
                "| File | Blocks | Inline lines | File lines |",
                "| --- | ---: | ---: | ---: |",
            )
        )
        for row in list(inline.get("largest_files") or [])[:12]:
            lines.append(
                f"| `{row['path']}` | {row['blocks']:,} | {row['lines']:,} | {row['file_lines']:,} |"
            )
        lines.append("")
    else:
        lines.extend(("- No inline Rust test modules detected under `src/`.", ""))
    split = stats.get("rust_split_test_files") or {}
    lines.extend(
        (
            "",
            "## Split Rust Test Files",
            "",
        )
    )
    if split.get("files"):
        lines.extend(
            (
                f"- Split test files under `src/`: {split['files']:,}",
                f"- Split test file lines: {split['lines']:,}",
                "",
                "| Member | Files | Lines |",
                "| --- | ---: | ---: |",
            )
        )
        for member, values in list((split.get("by_member") or {}).items())[:12]:
            lines.append(f"| `{member}` | {values['files']:,} | {values['lines']:,} |")
        lines.extend(
            (
                "",
                "Largest split test files:",
                "",
                "| File | Lines |",
                "| --- | ---: |",
            )
        )
        for row in list(split.get("largest_files") or [])[:12]:
            lines.append(f"| `{row['path']}` | {row['lines']:,} |")
        lines.append("")
    else:
        lines.extend(("- No split Rust test files detected under `src/`.", ""))
    lines.extend(
        (
            "",
            "## Notes",
            "",
            "- LOC input is the union of tracked files and untracked files accepted by Git; `.ignore` and `.tokeignore` add repository-owned reporting exclusions.",
            "- Ignored local runtime state, private demo exports, dependency trees, and caches are never traversed merely because they exist in the checkout.",
            "- Buckets are assigned by the first matching project-relative glob.",
            "- Embedded languages reported by tokei, such as fenced code in Markdown, are counted in the owning file's bucket.",
            "- The `other` bucket is intentionally explicit: it catches files outside the project-specific attribution model.",
            "- Inline Rust test modules are reported separately because tokei cannot split Rust source files by item.",
            "- Split Rust test files under `src/` are routed to the `test-suite` bucket even though they live next to production modules.",
            "",
        )
    )
    return "\n".join(lines)


def _generate_tokei_stats(
    plan: RepoPlan, out_dir: Path, generated_at: str, log: list[str] | None = None
) -> tuple[list[str], int]:
    if shutil.which("tokei") is None:
        _emit(log, "  [yellow]⚠[/yellow] tokei stats skipped: tokei not found on PATH")
        return [], 0
    stats = _collect_tokei_stats(plan, generated_at)
    json_path = out_dir / f"{plan.name}-tokei-stats.json"
    md_path = out_dir / f"{plan.name}-tokei-stats.md"
    json_path.write_text(
        json.dumps(stats, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    md_path.write_text(_stats_markdown(plan, stats), encoding="utf-8")
    size = json_path.stat().st_size + md_path.stat().st_size
    _emit(log, f"  [green]✓[/green] tokei-stats ({_fmt_bytes(size)})")
    return [json_path.name, md_path.name], size


# ═══════════════════════════════════════════════════════════════════════════════
# Git growth and change-shape analysis
# ═══════════════════════════════════════════════════════════════════════════════


def _growth_ref(repo: Path) -> str:
    remote_head = _run(
        ["git", "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"],
        cwd=repo,
    )
    candidates = [
        remote_head.stdout.strip(),
        "master",
        "main",
        "origin/master",
        "origin/main",
        "HEAD",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        resolved = _run(
            ["git", "rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}"],
            cwd=repo,
        )
        if resolved.returncode == 0:
            return candidate
    raise MaterializationError(
        repo.name, reason="no commit-bearing default branch found"
    )


def _percentile(values: Sequence[int], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _gini(values: Sequence[int]) -> float:
    nonnegative = sorted(max(0, value) for value in values)
    total = sum(nonnegative)
    if not nonnegative or total == 0:
        return 0.0
    weighted = sum(index * value for index, value in enumerate(nonnegative, start=1))
    count = len(nonnegative)
    return (2 * weighted) / (count * total) - (count + 1) / count


def _commit_kind(subject: str) -> str:
    match = re.match(r"^([A-Za-z][A-Za-z0-9-]*)(?:\([^)]*\))?!?:\s", subject)
    return match.group(1).lower() if match else "unclassified"


def _numstat_destination_path(path: str) -> str:
    """Resolve Git's human-readable rename notation to the destination path."""
    if " => " not in path:
        return path
    if "{" in path and "}" in path:
        prefix, remainder = path.split("{", 1)
        replacement, suffix = remainder.split("}", 1)
        destination = replacement.split(" => ", 1)[-1]
        return f"{prefix}{destination}{suffix}"
    return path.split(" => ", 1)[-1]


def _aggregate_growth_period(
    rows: Sequence[dict[str, Any]], key: str
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        period = str(row[key])
        target = grouped.setdefault(
            period,
            {
                key: period,
                "commits": 0,
                "active_days": set(),
                "additions": 0,
                "deletions": 0,
                "net": 0,
                "gross": 0,
            },
        )
        target["commits"] += int(row["commits"])
        target["active_days"].add(row["day"])
        for metric in ("additions", "deletions", "net", "gross"):
            target[metric] += int(row[metric])
    result: list[dict[str, Any]] = []
    for period in sorted(grouped):
        row = grouped[period]
        row["active_days"] = len(row["active_days"])
        result.append(row)
    return result


def _collect_git_growth(plan: RepoPlan, generated_at: str) -> dict[str, Any]:
    ref = _growth_ref(plan.path)
    result = _run(
        [
            "git",
            "log",
            ref,
            "--reverse",
            "--find-renames",
            "--date=iso-strict",
            "--format=%x1e%H%x1f%aI%x1f%s",
            "--numstat",
        ],
        cwd=plan.path,
    )
    if result.returncode != 0:
        raise MaterializationError(
            plan.name, reason=(result.stderr or "git log failed").strip()
        )

    daily_map: dict[str, dict[str, Any]] = {}
    bucket_churn: dict[str, dict[str, int]] = {}
    kind_counts: dict[str, int] = {}
    heatmap = [[0 for _hour in range(24)] for _day in range(7)]
    commit_changes: list[int] = []
    commits: list[dict[str, Any]] = []

    for raw_record in result.stdout.split("\x1e"):
        record = raw_record.strip("\n")
        if not record:
            continue
        lines = record.splitlines()
        metadata = lines[0].split("\x1f", 2)
        if len(metadata) != 3:
            continue
        sha, authored_at, subject = metadata
        try:
            authored = dt.datetime.fromisoformat(authored_at)
        except ValueError:
            continue
        additions = deletions = files = 0
        per_bucket: dict[str, dict[str, int]] = {}
        for line in lines[1:]:
            parts = line.split("\t", 2)
            if len(parts) != 3 or parts[0] == "-" or parts[1] == "-":
                continue
            try:
                added = int(parts[0])
                deleted = int(parts[1])
            except ValueError:
                continue
            path = _normalize_rel_pattern(_numstat_destination_path(parts[2]))
            bucket = _classify_stats_bucket(plan, path)
            target = per_bucket.setdefault(
                bucket, {"files": 0, "additions": 0, "deletions": 0}
            )
            target["files"] += 1
            target["additions"] += added
            target["deletions"] += deleted
            additions += added
            deletions += deleted
            files += 1

        day = authored.date().isoformat()
        week = (authored.date() - dt.timedelta(days=authored.weekday())).isoformat()
        month = authored.date().replace(day=1).isoformat()
        gross = additions + deletions
        net = additions - deletions
        kind = _commit_kind(subject)
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
        heatmap[authored.weekday()][authored.hour] += 1
        commit_changes.append(gross)
        commits.append(
            {
                "sha": sha,
                "day": day,
                "week": week,
                "month": month,
                "additions": additions,
                "deletions": deletions,
                "net": net,
                "gross": gross,
                "files": files,
                "kind": kind,
            }
        )
        daily = daily_map.setdefault(
            day,
            {
                "day": day,
                "week": week,
                "month": month,
                "commits": 0,
                "additions": 0,
                "deletions": 0,
                "net": 0,
                "gross": 0,
            },
        )
        daily["commits"] += 1
        for metric, value in (
            ("additions", additions),
            ("deletions", deletions),
            ("net", net),
            ("gross", gross),
        ):
            daily[metric] += value
        for bucket, values in per_bucket.items():
            aggregate = bucket_churn.setdefault(
                bucket,
                {
                    "commits": 0,
                    "files_changed": 0,
                    "additions": 0,
                    "deletions": 0,
                    "net": 0,
                    "gross": 0,
                },
            )
            aggregate["commits"] += 1
            aggregate["files_changed"] += values["files"]
            aggregate["additions"] += values["additions"]
            aggregate["deletions"] += values["deletions"]
            aggregate["net"] += values["additions"] - values["deletions"]
            aggregate["gross"] += values["additions"] + values["deletions"]

    if not commits:
        raise MaterializationError(plan.name, reason=f"no commits found on {ref}")

    first_day = dt.date.fromisoformat(commits[0]["day"])
    last_day = dt.date.fromisoformat(commits[-1]["day"])
    daily: list[dict[str, Any]] = []
    cursor = first_day
    cumulative_net = 0
    while cursor <= last_day:
        day = cursor.isoformat()
        source = daily_map.get(day)
        if source is None:
            week = (cursor - dt.timedelta(days=cursor.weekday())).isoformat()
            month = cursor.replace(day=1).isoformat()
            source = {
                "day": day,
                "week": week,
                "month": month,
                "commits": 0,
                "additions": 0,
                "deletions": 0,
                "net": 0,
                "gross": 0,
            }
        row = dict(source)
        cumulative_net += int(row["net"])
        row["cumulative_net"] = cumulative_net
        daily.append(row)
        cursor += dt.timedelta(days=1)

    final_net = cumulative_net
    for index, row in enumerate(daily):
        window = daily[max(0, index - 27) : index + 1]
        rolling_gross = sum(int(item["gross"]) for item in window)
        row["rolling_28d_gross"] = rolling_gross
        row["rolling_28d_commits"] = sum(int(item["commits"]) for item in window)
        row["rolling_28d_relative_to_final_net"] = (
            rolling_gross / abs(final_net) if final_net else None
        )

    active_daily = [row for row in daily if row["commits"]]
    weekly = _aggregate_growth_period(active_daily, "week")
    monthly = _aggregate_growth_period(active_daily, "month")
    threshold = final_net * 0.5
    half_size_day = next(
        (
            row["day"]
            for row in daily
            if final_net > 0 and row["cumulative_net"] >= threshold
        ),
        None,
    )
    peak_rolling = max(
        daily,
        key=lambda row: int(row["rolling_28d_gross"]),
    )
    cutoff_30 = last_day - dt.timedelta(days=29)
    cutoff_90 = last_day - dt.timedelta(days=89)

    def window_summary(cutoff: dt.date) -> dict[str, int]:
        rows = [row for row in daily if dt.date.fromisoformat(row["day"]) >= cutoff]
        return {
            "commits": sum(int(row["commits"]) for row in rows),
            "additions": sum(int(row["additions"]) for row in rows),
            "deletions": sum(int(row["deletions"]) for row in rows),
            "net": sum(int(row["net"]) for row in rows),
            "gross": sum(int(row["gross"]) for row in rows),
            "active_days": sum(1 for row in rows if row["commits"]),
        }

    all_refs = _run(["git", "rev-list", "--all", "--count"], cwd=plan.path)
    unique_all_refs = int(all_refs.stdout.strip()) if all_refs.returncode == 0 else None
    summary = {
        "first_commit_day": commits[0]["day"],
        "last_commit_day": commits[-1]["day"],
        "default_branch_ref": ref,
        "default_branch_commits": len(commits),
        "unique_commits_all_refs": unique_all_refs,
        "active_days": len(active_daily),
        "calendar_span_days": (last_day - first_day).days + 1,
        "additions": sum(commit["additions"] for commit in commits),
        "deletions": sum(commit["deletions"] for commit in commits),
        "net_tracked_text_lines": final_net,
        "gross_line_churn": sum(commit_changes),
        "net_retention_of_additions": (
            final_net / sum(commit["additions"] for commit in commits)
            if sum(commit["additions"] for commit in commits)
            else None
        ),
        "gross_to_net_ratio": sum(commit_changes) / abs(final_net)
        if final_net
        else None,
        "median_changed_lines_per_commit": statistics.median(commit_changes),
        "p90_changed_lines_per_commit": _percentile(commit_changes, 0.90),
        "p99_changed_lines_per_commit": _percentile(commit_changes, 0.99),
        "date_reached_50pct_current_size": half_size_day,
        "peak_28d_churn": int(peak_rolling["rolling_28d_gross"]),
        "peak_28d_churn_day": peak_rolling["day"],
        "peak_28d_churn_relative_to_final_net": peak_rolling[
            "rolling_28d_relative_to_final_net"
        ],
        "weekly_gross_churn_gini": _gini([int(row["gross"]) for row in weekly]),
        "last_30_days": window_summary(cutoff_30),
        "last_90_days": window_summary(cutoff_90),
    }
    return {
        "project": plan.name,
        "source": str(plan.path),
        "generated_at": generated_at,
        "method": {
            "history_scope": ref,
            "history_command": "git log <default-ref> --reverse --find-renames --numstat",
            "binary_numstat_rows": "excluded",
            "date_basis": "author date",
            "bucket_policy": "first matching current Chisel attribution glob",
        },
        "summary": summary,
        "daily": daily,
        "weekly": weekly,
        "monthly": monthly,
        "bucket_churn": [
            {"bucket": bucket, **values}
            for bucket, values in sorted(
                bucket_churn.items(), key=lambda item: (-item[1]["gross"], item[0])
            )
        ],
        "commit_kinds": [
            {"kind": kind, "commits": count, "share": count / len(commits)}
            for kind, count in sorted(
                kind_counts.items(), key=lambda item: (-item[1], item[0])
            )
        ],
        "commit_heatmap": {
            "weekdays": [
                "Monday",
                "Tuesday",
                "Wednesday",
                "Thursday",
                "Friday",
                "Saturday",
                "Sunday",
            ],
            "hours": list(range(24)),
            "counts": heatmap,
        },
    }


def _write_csv_rows(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _growth_markdown(growth: dict[str, Any]) -> str:
    summary = growth["summary"]
    ratio = summary.get("gross_to_net_ratio")
    ratio_text = f"{ratio:.2f}×" if isinstance(ratio, (int, float)) else "n/a"
    retention = summary.get("net_retention_of_additions")
    retention_text = (
        f"{retention:.1%}" if isinstance(retention, (int, float)) else "n/a"
    )
    lines = [
        f"# {growth['project']} growth and change shape",
        "",
        f"Generated: {growth['generated_at']}",
        f"History: `{summary['default_branch_ref']}` ({summary['first_commit_day']} to {summary['last_commit_day']})",
        "",
        "## Summary",
        "",
        "| Signal | Value |",
        "| --- | ---: |",
        f"| Default-branch commits | {summary['default_branch_commits']:,} |",
        f"| Active days | {summary['active_days']:,} |",
        f"| Additions | {summary['additions']:,} |",
        f"| Deletions | {summary['deletions']:,} |",
        f"| Net tracked-text growth | {summary['net_tracked_text_lines']:,} |",
        f"| Gross changed lines | {summary['gross_line_churn']:,} |",
        f"| Net / additions | {retention_text} |",
        f"| Gross / final net | {ratio_text} |",
        f"| Median changed lines / commit | {summary['median_changed_lines_per_commit']:,.1f} |",
        f"| P90 changed lines / commit | {summary['p90_changed_lines_per_commit']:,.1f} |",
        f"| Reached 50% of final net size | {summary.get('date_reached_50pct_current_size') or 'n/a'} |",
        f"| Peak rolling 28-day churn | {summary['peak_28d_churn']:,} ({summary['peak_28d_churn_day']}) |",
        f"| Weekly churn concentration (Gini) | {summary['weekly_gross_churn_gini']:.3f} |",
        "",
        "## Recent velocity",
        "",
        "| Window | Commits | Active days | Additions | Deletions | Net | Gross |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for label, key in (("30 days", "last_30_days"), ("90 days", "last_90_days")):
        row = summary[key]
        lines.append(
            f"| {label} | {row['commits']:,} | {row['active_days']:,} | {row['additions']:,} | "
            f"{row['deletions']:,} | {row['net']:,} | {row['gross']:,} |"
        )
    lines.extend(
        (
            "",
            "## Historical churn by current attribution bucket",
            "",
            "| Bucket | Commits touching | File changes | Additions | Deletions | Net | Gross |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        )
    )
    for row in growth["bucket_churn"]:
        lines.append(
            f"| `{row['bucket']}` | {row['commits']:,} | {row['files_changed']:,} | "
            f"{row['additions']:,} | {row['deletions']:,} | {row['net']:,} | {row['gross']:,} |"
        )
    lines.extend(
        (
            "",
            "## Commit subject mix",
            "",
            "| Conventional kind | Commits | Share |",
            "| --- | ---: | ---: |",
        )
    )
    for row in growth["commit_kinds"]:
        lines.append(f"| `{row['kind']}` | {row['commits']:,} | {row['share']:.1%} |")
    lines.extend(
        (
            "",
            "## Interpretation limits",
            "",
            "- Git `numstat` measures tracked text: implementation, tests, documentation, configuration, schemas, and data all contribute.",
            "- Gross churn captures replacement and refactoring as well as expansion; it is not a waste metric.",
            "- Historical files are assigned using today's Chisel bucket model. Renames across conceptual boundaries can therefore land in `other`.",
            "- Commit counts are integration events, not estimates of human effort or independent review.",
            "",
        )
    )
    return "\n".join(lines)


def _generate_growth_analysis(
    plan: RepoPlan, out_dir: Path, generated_at: str, log: list[str] | None = None
) -> tuple[list[str], int]:
    growth = _collect_git_growth(plan, generated_at)
    prefix = out_dir / f"{plan.name}-growth"
    json_path = prefix.with_suffix(".json")
    md_path = prefix.with_suffix(".md")
    daily_path = out_dir / f"{plan.name}-growth-daily.csv"
    weekly_path = out_dir / f"{plan.name}-growth-weekly.csv"
    monthly_path = out_dir / f"{plan.name}-growth-monthly.csv"
    buckets_path = out_dir / f"{plan.name}-growth-buckets.csv"
    json_path.write_text(
        json.dumps(growth, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    md_path.write_text(_growth_markdown(growth), encoding="utf-8")
    _write_csv_rows(daily_path, growth["daily"])
    _write_csv_rows(weekly_path, growth["weekly"])
    _write_csv_rows(monthly_path, growth["monthly"])
    _write_csv_rows(buckets_path, growth["bucket_churn"])
    paths = [json_path, md_path, daily_path, weekly_path, monthly_path, buckets_path]
    size = sum(path.stat().st_size for path in paths)
    _emit(log, f"  [green]✓[/green] growth-analysis ({_fmt_bytes(size)})")
    return [path.name for path in paths], size


# ═══════════════════════════════════════════════════════════════════════════════
# GitHub issues
# ═══════════════════════════════════════════════════════════════════════════════


# Serialise concurrent github_context materialization across threads.
_github_context_lock = threading.Lock()
_github_context_ready: bool | None = None
_github_context_index: dict[tuple[str, str, str, str], list[Any]] | None = None
_github_context_manifest: dict[str, Any] | None = None


def _ensure_github_context_for_chisel(projects: set[str] | None = None) -> None:
    global _github_context_index, _github_context_manifest, _github_context_ready

    with _github_context_lock:
        if _github_context_ready is True:
            return
        if _github_context_ready is False:
            raise MaterializationError(
                "github_context",
                reason="GitHub context materialization already failed in this run",
            )
        from ..ingest.github_context_materialize import materialize_github_context

        try:
            _github_context_manifest = materialize_github_context(
                projects=projects, progress=_print_live
            )
        except MaterializationError as exc:
            try:
                _github_context_index = _build_github_context_index()
            except Exception as stale_exc:
                _github_context_ready = False
                raise MaterializationError(
                    "github_context",
                    reason=(
                        "GitHub context is unavailable for chisel issue rendering: "
                        f"{exc}; existing product could not be read: {stale_exc}"
                    ),
                ) from exc
            _github_context_ready = True
            _print_live(
                "[yellow]GitHub context: refresh failed; using existing context product "
                f"for issue/PR snapshots ({exc})[/yellow]"
            )
            return
        if (_github_context_manifest or {}).get("substrate_status") == "degraded":
            _github_context_ready = False
            raise MaterializationError(
                "github_context",
                reason=(
                    "GitHub context substrate promotion remained degraded after recovery: "
                    f"{(_github_context_manifest or {}).get('substrate_error') or 'unknown error'}"
                ),
            )
        _github_context_index = _build_github_context_index()
        _github_context_ready = True


def _github_context_summary() -> str:
    manifest = _github_context_manifest or {}
    if not manifest:
        return "existing product"
    inventory = int(manifest.get("inventory_items_seen") or 0)
    refreshed = int(manifest.get("detail_refreshes") or 0)
    reused = int(manifest.get("detail_reuses") or 0)
    missed = int(manifest.get("detail_misses") or 0)
    stale_open = sum(
        int(value or 0)
        for value in (manifest.get("project_stale_open_removed") or {}).values()
    )
    fetched_refs = int(manifest.get("missing_commit_refs_fetched") or 0)
    deferred_refs = int(manifest.get("missing_commit_refs_deferred") or 0)
    parts = [
        f"{inventory} inventory",
        f"{refreshed} detail refresh",
        f"{reused} reused",
    ]
    if missed:
        parts.append(f"{missed} missed")
    if stale_open:
        parts.append(f"{stale_open} stale open removed")
    if fetched_refs or deferred_refs:
        parts.append(f"{fetched_refs} commit refs fetched")
    if deferred_refs:
        parts.append(f"{deferred_refs} deferred")
    reasons = manifest.get("detail_decision_reasons") or {}
    noisy_reasons = {
        str(key): int(value)
        for key, value in reasons.items()
        if key != "unchanged_inventory" and int(value or 0)
    }
    if noisy_reasons:
        parts.append(
            "hydrate reasons "
            + ", ".join(
                f"{key}={value}" for key, value in sorted(noisy_reasons.items())
            )
        )
    substrate_status = str(manifest.get("substrate_status") or "unknown")
    if substrate_status == "degraded":
        attempts = int(manifest.get("substrate_attempts") or 1)
        parts.append(f"substrate promotion degraded after {attempts} attempt(s)")
    return "; ".join(parts)


def _ensure_chisel_prerequisites(plans: Sequence[RepoPlan]) -> None:
    if not any(plan.github_slug for plan in plans):
        return
    _print_live("GitHub context: ensure materialized for issue/PR snapshots...")
    t0 = dt.datetime.now()
    _ensure_github_context_for_chisel({plan.name for plan in plans})
    elapsed = (dt.datetime.now() - t0).total_seconds()
    _print_live(f"GitHub context: ready ({elapsed:.1f}s; {_github_context_summary()})")


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


def _github_context_items(
    project: str, repo_slug: str, kind: str, state: str, limit: int
) -> list[Any]:
    if _github_context_index is None:
        return []
    return list(
        _github_context_index.get((project, repo_slug.lower(), kind, state), ())[:limit]
    )


def _issues_from_context_product(
    project: str, repo_slug: str, state: str, limit: int
) -> list[dict]:
    if state == "all":
        items = [
            *_github_context_items(project, repo_slug, "issue", "open", limit),
            *_github_context_items(project, repo_slug, "issue", "closed", limit),
        ][:limit]
    else:
        items = _github_context_items(project, repo_slug, "issue", state, limit)
    return [_github_issue_to_chisel_dict(item) for item in items]


def _github_issue_to_chisel_dict(item) -> dict:
    return {
        "number": item.number,
        "state": item.state.upper(),
        "title": item.title,
        "body": item.body,
        "labels": [{"name": label.name} for label in item.labels],
        "url": item.url or "",
        "createdAt": item.created_at.isoformat() if item.created_at else "",
        "updatedAt": item.updated_at.isoformat() if item.updated_at else "",
        "closedAt": item.closed_at.isoformat() if item.closed_at else "",
        "comments": [
            {
                "author": {"login": comment.author.login},
                "body": comment.body,
                "createdAt": comment.created_at.isoformat()
                if comment.created_at
                else "",
            }
            for comment in item.comments
        ],
    }


def _normalize_comments(issues: list[dict]) -> None:
    for iss in issues:
        iss["_comments"] = iss.pop("comments", [])


def _build_issues_xml(
    issues: list[dict], repo_slug: str, state: str, generated_at: str
) -> str:
    root = ET.Element(
        "issues",
        {
            "repository": repo_slug,
            "state": state,
            "generated-at": generated_at,
            "count": str(len(issues)),
        },
    )
    for iss in issues:
        el = ET.SubElement(
            root,
            "issue",
            {
                "number": str(iss.get("number", "")),
                "state": iss.get("state", ""),
                "created-at": iss.get("createdAt", ""),
                "updated-at": iss.get("updatedAt", ""),
                "url": iss.get("url", ""),
            },
        )
        t = ET.SubElement(el, "title")
        t.text = iss.get("title", "")
        b = ET.SubElement(el, "body")
        b.text = iss.get("body", "")
        lb = ET.SubElement(el, "labels")
        lb.text = ", ".join(label["name"] for label in iss.get("labels", []))
        comments = ET.SubElement(el, "comments")
        for c in iss.get("_comments", []):
            ce = ET.SubElement(
                comments,
                "comment",
                {
                    "author": (c.get("author") or {}).get("login", "?"),
                    "created-at": c.get("createdAt", ""),
                },
            )
            cb = ET.SubElement(ce, "body")
            cb.text = c.get("body", "")
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def _generate_issues(
    plan: RepoPlan, out_dir: Path, generated_at: str, log: list[str] | None = None
) -> tuple[int, int]:
    """Fetch and write issues-open.xml + issues-closed.xml. Returns (open_count, closed_count)."""
    if not plan.github_slug or not _has_github_remote(plan.path):
        return 0, 0

    _ensure_github_context_for_chisel()
    open_issues = _issues_from_context_product(
        plan.name, plan.github_slug, "open", DEFAULT_ISSUE_LIMIT
    )
    _normalize_comments(open_issues)
    closed_issues = _issues_from_context_product(
        plan.name, plan.github_slug, "closed", DEFAULT_ISSUE_LIMIT
    )
    _normalize_comments(closed_issues)

    count = 0
    for state, issues in [("open", open_issues), ("closed", closed_issues)]:
        xml = _build_issues_xml(issues, plan.github_slug, state, generated_at)
        (out_dir / f"{plan.name}-issues-{state}.xml").write_text(xml, encoding="utf-8")
        count += len(issues)

    _emit(
        log,
        f"  [dim]issues: {len(open_issues)} open / {len(closed_issues)} closed[/dim]",
    )
    return len(open_issues), len(closed_issues)


def _github_pr_to_chisel_dict(item) -> dict:
    return {
        "number": item.number,
        "state": item.state.upper(),
        "title": item.title,
        "body": item.body,
        "labels": [{"name": label.name} for label in item.labels],
        "url": item.url or "",
        "mergeCommit": item.merge_commit or "",
        "createdAt": item.created_at.isoformat() if item.created_at else "",
        "mergedAt": item.merged_at.isoformat() if item.merged_at else "",
        "comments": [
            {
                "author": {"login": comment.author.login},
                "body": comment.body,
                "createdAt": comment.created_at.isoformat()
                if comment.created_at
                else "",
            }
            for comment in item.comments
        ],
        "reviews": [
            {
                "author": {"login": review.author.login},
                "state": review.state,
                "body": review.body,
                "submittedAt": review.submitted_at.isoformat()
                if review.submitted_at
                else "",
            }
            for review in item.reviews
        ],
    }


def _prs_from_context_product(
    project: str, repo_slug: str, state: str, limit: int = DEFAULT_ISSUE_LIMIT
) -> list[dict]:
    if state == "all":
        items = [
            *_github_context_items(project, repo_slug, "pr", "open", limit),
            *_github_context_items(project, repo_slug, "pr", "merged", limit),
        ][:limit]
    else:
        items = _github_context_items(project, repo_slug, "pr", state, limit)
        items = [item for item in items if item.state == state]
    return [_github_pr_to_chisel_dict(item) for item in items]


def _normalize_pr_data(prs: list[dict]) -> None:
    for pr in prs:
        pr["_comments"] = pr.pop("comments", [])
        pr["_reviews"] = pr.pop("reviews", [])


def _build_prs_xml(
    prs: list[dict], repo_slug: str, state: str, generated_at: str
) -> str:
    root = ET.Element(
        "prs",
        {
            "repository": repo_slug,
            "state": state,
            "generated-at": generated_at,
            "count": str(len(prs)),
        },
    )
    for pr in prs:
        el = ET.SubElement(
            root,
            "pr",
            {
                "number": str(pr.get("number", "")),
                "state": pr.get("state", ""),
                "created-at": pr.get("createdAt", ""),
                "merged-at": pr.get("mergedAt", ""),
                "url": pr.get("url", ""),
                "merge-commit": pr.get("mergeCommit", ""),
            },
        )
        t = ET.SubElement(el, "title")
        t.text = pr.get("title", "")
        b = ET.SubElement(el, "body")
        b.text = pr.get("body", "")
        lb = ET.SubElement(el, "labels")
        lb.text = ", ".join(label["name"] for label in pr.get("labels", []))
        comments = ET.SubElement(el, "comments")
        for c in pr.get("_comments", []):
            ce = ET.SubElement(
                comments,
                "comment",
                {
                    "author": (c.get("author") or {}).get("login", "?"),
                    "created-at": c.get("createdAt", ""),
                },
            )
            cb = ET.SubElement(ce, "body")
            cb.text = c.get("body", "")
        reviews = ET.SubElement(el, "reviews")
        for rv in pr.get("_reviews", []):
            re_el = ET.SubElement(
                reviews,
                "review",
                {
                    "author": (rv.get("author") or {}).get("login", "?"),
                    "state": rv.get("state", ""),
                    "submitted-at": rv.get("submittedAt", ""),
                },
            )
            rb = ET.SubElement(re_el, "body")
            rb.text = rv.get("body", "")
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def _generate_prs(
    plan: RepoPlan, out_dir: Path, generated_at: str, log: list[str] | None = None
) -> tuple[int, int]:
    """Fetch and write prs-open.xml + prs-merged.xml. Returns (open_count, merged_count)."""
    if not plan.github_slug or not _has_github_remote(plan.path):
        return 0, 0

    _ensure_github_context_for_chisel()
    open_prs = _prs_from_context_product(
        plan.name, plan.github_slug, "open", DEFAULT_ISSUE_LIMIT
    )
    _normalize_pr_data(open_prs)
    merged_prs = _prs_from_context_product(
        plan.name, plan.github_slug, "merged", DEFAULT_ISSUE_LIMIT
    )
    _normalize_pr_data(merged_prs)

    for state, prs in [("open", open_prs), ("merged", merged_prs)]:
        xml = _build_prs_xml(prs, plan.github_slug, state, generated_at)
        (out_dir / f"{plan.name}-prs-{state}.xml").write_text(xml, encoding="utf-8")

    _emit(log, f"  [dim]prs: {len(open_prs)} open / {len(merged_prs)} merged[/dim]")
    return len(open_prs), len(merged_prs)


# ═══════════════════════════════════════════════════════════════════════════════
# Beads context
# ═══════════════════════════════════════════════════════════════════════════════


def _bd_json(cmd: Sequence[str], *, cwd: Path) -> Any:
    result = _run(["bd", *cmd, "--json"], cwd=cwd)
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "bd command failed").strip()
        raise SourceUnavailableError("beads", reason=details)
    text = result.stdout.strip()
    if not text:
        return None
    return json.loads(text)


def _bd_export_rows(repo: Path) -> list[dict[str, Any]]:
    result = _run(["bd", "export", "--include-memories"], cwd=repo)
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "bd export failed").strip()
        raise SourceUnavailableError("beads", reason=details)
    rows: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        value = json.loads(line)
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _beads_issue_rows(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("_type", "issue") == "issue"]


def _beads_memory_rows(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("_type") == "memory"]


def _beads_status_counts(issues: Sequence[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for issue in issues:
        status = str(issue.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _beads_type_counts(issues: Sequence[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for issue in issues:
        issue_type = str(issue.get("issue_type") or issue.get("type") or "unknown")
        counts[issue_type] = counts.get(issue_type, 0) + 1
    return counts


def _beads_dependency_edges(issues: Sequence[dict[str, Any]]) -> list[dict[str, str]]:
    edges: list[dict[str, str]] = []
    for issue in issues:
        issue_id = str(issue.get("id") or "")
        if not issue_id:
            continue
        for key in ("dependencies", "depends_on", "blocked_by"):
            values = issue.get(key) or ()
            if isinstance(values, str):
                values = [values]
            for value in values:
                if isinstance(value, dict):
                    target = (
                        value.get("id")
                        or value.get("depends_on_id")
                        or value.get("issue_id")
                    )
                    relation = value.get("type") or key
                else:
                    target = value
                    relation = key
                if target:
                    edges.append(
                        {
                            "issue": issue_id,
                            "depends_on": str(target),
                            "type": str(relation),
                        }
                    )
        for key in ("dependents", "blocks", "blocking"):
            values = issue.get(key) or ()
            if isinstance(values, str):
                values = [values]
            for value in values:
                if isinstance(value, dict):
                    target = (
                        value.get("id")
                        or value.get("issue_id")
                        or value.get("dependent_id")
                    )
                    relation = value.get("type") or key
                else:
                    target = value
                    relation = key
                if target:
                    edges.append(
                        {
                            "issue": str(target),
                            "depends_on": issue_id,
                            "type": str(relation),
                        }
                    )
    return edges


def _beads_list_ids(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {
        str(item.get("id"))
        for item in value
        if isinstance(item, dict) and item.get("id")
    }


def _parse_beads_timestamp(value: Any) -> dt.datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError:
        return None


def _beads_history(
    issues: Sequence[dict[str, Any]], generated_at: str
) -> dict[str, Any]:
    created_dates = [
        parsed.date()
        for issue in issues
        if (parsed := _parse_beads_timestamp(issue.get("created_at"))) is not None
    ]
    closed_pairs = [
        (created, closed)
        for issue in issues
        if (created := _parse_beads_timestamp(issue.get("created_at"))) is not None
        and (closed := _parse_beads_timestamp(issue.get("closed_at"))) is not None
        and closed >= created
    ]
    try:
        snapshot_day = dt.datetime.strptime(generated_at, "%Y%m%dT%H%M%SZ").date()
    except ValueError:
        snapshot_day = dt.datetime.now(dt.timezone.utc).date()
    if not created_dates:
        return {
            "summary": {
                "first_created_day": None,
                "snapshot_day": snapshot_day.isoformat(),
                "created": 0,
                "closed": 0,
                "open_snapshot": 0,
                "median_lead_days": None,
                "p90_lead_days": None,
                "closed_last_30_days": 0,
                "closed_last_90_days": 0,
            },
            "daily": [],
        }

    first_day = min(created_dates)
    last_observed = max(
        [snapshot_day, *created_dates, *(closed.date() for _, closed in closed_pairs)]
    )
    created_counts: dict[dt.date, int] = {}
    closed_counts: dict[dt.date, int] = {}
    for day in created_dates:
        created_counts[day] = created_counts.get(day, 0) + 1
    for _, closed in closed_pairs:
        closed_counts[closed.date()] = closed_counts.get(closed.date(), 0) + 1
    daily: list[dict[str, Any]] = []
    open_count = 0
    cursor = first_day
    while cursor <= last_observed:
        created = created_counts.get(cursor, 0)
        closed = closed_counts.get(cursor, 0)
        open_count += created - closed
        daily.append(
            {
                "day": cursor.isoformat(),
                "created": created,
                "closed": closed,
                "net": created - closed,
                "open_snapshot": open_count,
            }
        )
        cursor += dt.timedelta(days=1)
    lead_days = [
        (closed - created).total_seconds() / 86400 for created, closed in closed_pairs
    ]
    cutoff_30 = snapshot_day - dt.timedelta(days=29)
    cutoff_90 = snapshot_day - dt.timedelta(days=89)
    return {
        "summary": {
            "first_created_day": first_day.isoformat(),
            "snapshot_day": snapshot_day.isoformat(),
            "created": len(created_dates),
            "closed": len(closed_pairs),
            "open_snapshot": open_count,
            "median_lead_days": statistics.median(lead_days) if lead_days else None,
            "p90_lead_days": _percentile(
                [round(value * 1000) for value in lead_days], 0.90
            )
            / 1000
            if lead_days
            else None,
            "closed_last_30_days": sum(
                1 for _, closed in closed_pairs if closed.date() >= cutoff_30
            ),
            "closed_last_90_days": sum(
                1 for _, closed in closed_pairs if closed.date() >= cutoff_90
            ),
        },
        "daily": daily,
        "caveat": "Created/closed timestamps reconstruct the current issue set; reopen cycles and compacted/deleted issues require Dolt history.",
    }


def _beads_board_rows(
    issues: Sequence[dict[str, Any]],
    *,
    ready_ids: set[str],
    blocked_ids: set[str],
    dependencies: Sequence[dict[str, str]],
) -> list[dict[str, Any]]:
    dep_map: dict[str, list[str]] = {}
    for edge in dependencies:
        dep_map.setdefault(edge["issue"], []).append(edge["depends_on"])
    rows: list[dict[str, Any]] = []
    for issue in issues:
        issue_id = str(issue.get("id") or "")
        labels = issue.get("labels") or []
        row = dict(issue)
        row.update(
            {
                "id": issue_id,
                "title": str(issue.get("title") or ""),
                "status": str(issue.get("status") or "unknown"),
                "type": str(issue.get("issue_type") or issue.get("type") or "unknown"),
                "priority": issue.get("priority"),
                "labels": [
                    str(label.get("name") if isinstance(label, dict) else label)
                    for label in labels
                ]
                if isinstance(labels, list)
                else [],
                "ready": issue_id in ready_ids,
                "blocked": issue_id in blocked_ids,
                "depends_on": sorted(set(dep_map.get(issue_id, ()))),
            }
        )
        rows.append(row)
    return rows


def _beads_html(
    plan: RepoPlan,
    generated_at: str,
    rows: Sequence[dict[str, Any]],
    memories: Sequence[dict[str, Any]],
) -> str:
    data = json.dumps(
        {"issues": list(rows), "memories": list(memories)}, ensure_ascii=False
    ).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(plan.name)} Beads board</title>
  <style>
    :root {{ color-scheme: light dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }}
    body {{ margin: 0; background: #0b1020; color: #e5e7eb; }}
    main {{ max-width: 1500px; margin: auto; padding: 32px 24px 64px; }}
    h1 {{ margin: 0 0 8px; font-size: 2rem; }}
    .lede {{ color: #9ca3af; max-width: 1000px; }}
    .controls {{ display: grid; grid-template-columns: minmax(240px, 1fr) repeat(3, minmax(130px, 220px)); gap: 12px; margin: 28px 0 18px; }}
    input, select {{ border: 1px solid #374151; border-radius: 10px; padding: 11px 13px; background: #111827; color: inherit; }}
    .stats {{ display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 18px; }}
    .stat {{ background: #111827; border: 1px solid #253047; border-radius: 12px; padding: 10px 14px; }}
    table {{ width: 100%; border-collapse: collapse; background: #111827; border-radius: 14px; overflow: hidden; }}
    th, td {{ padding: 10px 12px; border-bottom: 1px solid #253047; text-align: left; vertical-align: top; }}
    th {{ position: sticky; top: 0; background: #172033; color: #cbd5e1; }}
    tr:hover {{ background: #151f32; }}
    code, .pill {{ font-family: ui-monospace, monospace; font-size: .83rem; }}
    .pill {{ display: inline-block; border-radius: 999px; padding: 3px 8px; background: #253047; margin: 1px 3px 1px 0; }}
    .ready {{ color: #86efac; }} .blocked {{ color: #fca5a5; }}
    details {{ margin-top: 7px; }} summary {{ cursor: pointer; color: #93c5fd; }}
    .detail-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 10px; margin-top: 10px; }}
    .detail {{ min-width: 0; border: 1px solid #253047; border-radius: 10px; padding: 10px; background: #0b1020; }}
    .detail h3 {{ margin: 0 0 6px; color: #9ca3af; font-size: .75rem; letter-spacing: .08em; text-transform: uppercase; }}
    pre {{ margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; font: .82rem/1.45 ui-monospace, monospace; }}
    .memory-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 12px; margin-top: 14px; }}
    .memory {{ background: #111827; border: 1px solid #253047; border-radius: 12px; padding: 14px; }}
    @media (max-width: 900px) {{ .controls {{ grid-template-columns: 1fr 1fr; }} .optional {{ display: none; }} }}
  </style>
</head>
<body><main>
  <h1>{html.escape(plan.name)} Beads board</h1>
  <p class="lede">Searchable private analysis view generated {html.escape(generated_at)}. It carries complete exported issue and memory records, including descriptions, notes, comments, ownership, dependencies, and tracker-specific fields.</p>
  <section class="controls">
    <input id="query" type="search" placeholder="Search any issue field">
    <select id="status"><option value="">All statuses</option></select>
    <select id="priority"><option value="">All priorities</option></select>
    <select id="type"><option value="">All types</option></select>
  </section>
  <div id="stats" class="stats"></div>
  <table><thead><tr><th>ID</th><th>P</th><th>Status</th><th>Type</th><th>Title and context</th><th class="optional">Labels</th><th class="optional">Dependencies</th><th>State</th></tr></thead><tbody id="rows"></tbody></table>
  <section><h2>Durable memories</h2><p class="lede">Complete memory records from <code>bd export --include-memories</code>.</p><div id="memories" class="memory-grid"></div></section>
</main><script>
const payload = {data};
const issues = payload.issues;
const memories = payload.memories;
const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[ch]));
const present = value => value !== null && value !== undefined && value !== '' && (!Array.isArray(value) || value.length > 0) && (typeof value !== 'object' || Array.isArray(value) || Object.keys(value).length > 0);
const detail = (label, value) => present(value) ? `<section class="detail"><h3>${{esc(label)}}</h3><pre>${{esc(typeof value === 'string' ? value : JSON.stringify(value, null, 2))}}</pre></section>` : '';
const primaryFields = new Set(['_type','id','title','status','type','issue_type','priority','labels','ready','blocked','depends_on','description','design','acceptance_criteria','notes','comments','owner','assignee','created_by','created_at','updated_at','closed_at']);
const controls = ['query','status','priority','type'].map(id => document.getElementById(id));
for (const key of ['status','priority','type']) {{
  const select = document.getElementById(key);
  [...new Set(issues.map(item => String(item[key] ?? '')).filter(Boolean))].sort().forEach(value => select.insertAdjacentHTML('beforeend', `<option>${{esc(value)}}</option>`));
}}
function render() {{
  const query = document.getElementById('query').value.toLowerCase();
  const status = document.getElementById('status').value;
  const priority = document.getElementById('priority').value;
  const type = document.getElementById('type').value;
  const visible = issues.filter(item => (!query || JSON.stringify(item).toLowerCase().includes(query)) && (!status || item.status === status) && (!priority || String(item.priority ?? '') === priority) && (!type || item.type === type));
  document.getElementById('stats').innerHTML = `<span class="stat">${{visible.length}} shown</span><span class="stat">${{visible.filter(x => x.ready).length}} ready</span><span class="stat">${{visible.filter(x => x.blocked).length}} blocked</span><span class="stat">${{visible.filter(x => ['closed','done','resolved'].includes(x.status)).length}} closed</span>`;
  document.getElementById('rows').innerHTML = visible.map(item => {{
    const extra = Object.fromEntries(Object.entries(item).filter(([key]) => !primaryFields.has(key)));
    const context = [detail('Description', item.description), detail('Design', item.design), detail('Acceptance criteria', item.acceptance_criteria), detail('Notes', item.notes), detail('Comments', item.comments), detail('Ownership', {{owner:item.owner, assignee:item.assignee, created_by:item.created_by}}), detail('Timestamps', {{created_at:item.created_at, updated_at:item.updated_at, closed_at:item.closed_at}}), detail('Other exported fields', extra)].join('');
    return `<tr><td><code>${{esc(item.id)}}</code></td><td>${{esc(item.priority ?? '')}}</td><td><span class="pill">${{esc(item.status)}}</span></td><td>${{esc(item.type)}}</td><td>${{esc(item.title)}}<details><summary>full record</summary><div class="detail-grid">${{context}}</div></details></td><td class="optional">${{item.labels.map(x => `<span class="pill">${{esc(x)}}</span>`).join('')}}</td><td class="optional">${{item.depends_on.map(x => `<code>${{esc(x)}}</code>`).join('<br>')}}</td><td>${{item.ready ? '<span class="ready">ready</span>' : ''}} ${{item.blocked ? '<span class="blocked">blocked</span>' : ''}}</td></tr>`;
  }}).join('');
}}
document.getElementById('memories').innerHTML = memories.length ? memories.map(item => `<article class="memory">${{detail(item.title || item.id || 'memory', item)}}</article>`).join('') : '<p class="lede">No memory records exported.</p>';
controls.forEach(control => control.addEventListener('input', render)); render();
</script></body></html>"""


def _build_beads_xml(
    issues: Sequence[dict[str, Any]],
    repo_path: Path,
    generated_at: str,
    *,
    ready_ids: set[str],
    blocked_ids: set[str],
    dependencies: Sequence[dict[str, str]],
) -> str:
    root = ET.Element(
        "beads",
        {
            "repository": str(repo_path),
            "generated-at": generated_at,
            "count": str(len(issues)),
            "ready-count": str(len(ready_ids)),
            "blocked-count": str(len(blocked_ids)),
        },
    )
    dep_map: dict[str, list[dict[str, str]]] = {}
    for edge in dependencies:
        dep_map.setdefault(edge["issue"], []).append(edge)
    for issue in issues:
        issue_id = str(issue.get("id") or "")
        priority = issue.get("priority")
        el = ET.SubElement(
            root,
            "issue",
            {
                "id": issue_id,
                "status": str(issue.get("status") or ""),
                "type": str(issue.get("issue_type") or issue.get("type") or ""),
                "priority": "" if priority is None else str(priority),
                "assignee": str(issue.get("assignee") or ""),
                "owner": str(issue.get("owner") or ""),
                "ready": str(issue_id in ready_ids).lower(),
                "blocked": str(issue_id in blocked_ids).lower(),
                "created-at": str(issue.get("created_at") or ""),
                "updated-at": str(issue.get("updated_at") or ""),
                "closed-at": str(issue.get("closed_at") or ""),
            },
        )
        title = ET.SubElement(el, "title")
        title.text = str(issue.get("title") or "")
        description = ET.SubElement(el, "description")
        description.text = str(issue.get("description") or "")
        labels = issue.get("labels") or ()
        labels_el = ET.SubElement(el, "labels")
        if isinstance(labels, list):
            labels_el.text = ", ".join(
                str(label.get("name") if isinstance(label, dict) else label)
                for label in labels
            )
        deps_el = ET.SubElement(el, "dependencies")
        for edge in dep_map.get(issue_id, ()):
            ET.SubElement(
                deps_el,
                "dependency",
                {
                    "depends-on": edge["depends_on"],
                    "type": edge["type"],
                },
            )
        comments_el = ET.SubElement(el, "comments")
        comments = issue.get("comments") or ()
        if isinstance(comments, list):
            for comment in comments:
                if not isinstance(comment, dict):
                    continue
                comment_el = ET.SubElement(
                    comments_el,
                    "comment",
                    {
                        "author": str(
                            comment.get("author") or comment.get("created_by") or ""
                        ),
                        "created-at": str(comment.get("created_at") or ""),
                    },
                )
                body = ET.SubElement(comment_el, "body")
                body.text = str(comment.get("body") or comment.get("text") or "")
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def _beads_markdown(
    plan: RepoPlan,
    generated_at: str,
    summary: dict[str, Any],
    issues: Sequence[dict[str, Any]],
    *,
    ready_ids: set[str],
    blocked_ids: set[str],
) -> str:
    openish = [
        issue
        for issue in issues
        if str(issue.get("status") or "") not in {"closed", "done", "resolved"}
    ]
    priority_rows = sorted(
        openish,
        key=lambda issue: (
            int(issue.get("priority") if issue.get("priority") is not None else 99),
            str(issue.get("updated_at") or ""),
        ),
    )[:25]
    lines = [
        f"# {plan.name} Beads context",
        "",
        f"Generated: {generated_at}",
        f"Repository: `{plan.path}`",
        "",
        "## Summary",
        "",
        "| Signal | Count |",
        "| --- | ---: |",
    ]
    for key in (
        "total_issues",
        "open_issues",
        "in_progress_issues",
        "blocked_issues",
        "deferred_issues",
        "closed_issues",
        "ready_issues",
    ):
        if key in summary:
            lines.append(
                f"| {key.replace('_', ' ').title()} | {int(summary.get(key) or 0)} |"
            )
    lines.extend(
        (
            f"| Exported issues | {len(issues)} |",
            f"| Ready IDs | {len(ready_ids)} |",
            f"| Blocked IDs | {len(blocked_ids)} |",
            "",
            "## Active Work",
            "",
            "| ID | P | Status | Type | Ready | Blocked | Title |",
            "| --- | ---: | --- | --- | --- | --- | --- |",
        )
    )
    for issue in priority_rows:
        issue_id = str(issue.get("id") or "")
        title = str(issue.get("title") or "").replace("|", "\\|")
        lines.append(
            f"| `{issue_id}` | {issue.get('priority', '')} | `{issue.get('status', '')}` | "
            f"`{issue.get('issue_type') or issue.get('type') or ''}` | "
            f"{str(issue_id in ready_ids).lower()} | {str(issue_id in blocked_ids).lower()} | {title} |"
        )
    lines.extend(
        (
            "",
            "## Raw Artifacts",
            "",
            f"- `{plan.name}-beads.xml` renders issue descriptions, comments, readiness, and dependencies.",
            f"- `{plan.name}-beads.json` carries summary counts, dependency edges, and command metadata.",
            f"- `{plan.name}-beads.html` is a searchable private analysis board over complete exported issue and memory records.",
            f"- `{plan.name}-beads-history.csv` reconstructs created, closed, and open counts from current issue timestamps.",
            f"- `{plan.name}-beads-export.jsonl` is `bd export --include-memories` for durable task and memory context.",
        )
    )
    return "\n".join(lines) + "\n"


def _generate_beads(
    plan: RepoPlan, out_dir: Path, generated_at: str, log: list[str] | None = None
) -> tuple[list[str], int, dict[str, Any]]:
    try:
        workspace = _bd_json(["where"], cwd=plan.path)
        stats = _bd_json(["stats"], cwd=plan.path) or {}
        ready = _bd_json(["ready"], cwd=plan.path) or []
        blocked = _bd_json(["blocked"], cwd=plan.path) or []
        rows = _bd_export_rows(plan.path)
    except (FileNotFoundError, json.JSONDecodeError, SourceUnavailableError) as exc:
        _emit(log, f"  [dim]beads: unavailable ({exc})[/dim]")
        return [], 0, {"available": False, "reason": str(exc)}

    issues = _beads_issue_rows(rows)
    memories = _beads_memory_rows(rows)
    ready_ids = _beads_list_ids(ready)
    blocked_ids = _beads_list_ids(blocked)
    dependencies = _beads_dependency_edges(issues)
    history = _beads_history(issues, generated_at)
    board_rows = _beads_board_rows(
        issues,
        ready_ids=ready_ids,
        blocked_ids=blocked_ids,
        dependencies=dependencies,
    )
    summary = stats.get("summary") if isinstance(stats, dict) else {}
    summary = summary if isinstance(summary, dict) else {}

    payload = {
        "available": True,
        "project": plan.name,
        "source": str(plan.path),
        "generated_at": generated_at,
        "workspace": workspace,
        "stats": stats,
        "summary": summary,
        "counts": {
            "issues": len(issues),
            "memories": len(memories),
            "ready": len(ready_ids),
            "blocked": len(blocked_ids),
            "dependencies": len(dependencies),
            "by_status": _beads_status_counts(issues),
            "by_type": _beads_type_counts(issues),
        },
        "ready_ids": sorted(ready_ids),
        "blocked_ids": sorted(blocked_ids),
        "dependencies": dependencies,
        "history": history,
        "board_issue_fields": sorted(board_rows[0]) if board_rows else [],
    }

    json_path = out_dir / f"{plan.name}-beads.json"
    xml_path = out_dir / f"{plan.name}-beads.xml"
    md_path = out_dir / f"{plan.name}-beads.md"
    html_path = out_dir / f"{plan.name}-beads.html"
    history_path = out_dir / f"{plan.name}-beads-history.csv"
    export_path = out_dir / f"{plan.name}-beads-export.jsonl"

    json_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    xml_path.write_text(
        _build_beads_xml(
            issues,
            plan.path,
            generated_at,
            ready_ids=ready_ids,
            blocked_ids=blocked_ids,
            dependencies=dependencies,
        ),
        encoding="utf-8",
    )
    md_path.write_text(
        _beads_markdown(
            plan,
            generated_at,
            summary,
            issues,
            ready_ids=ready_ids,
            blocked_ids=blocked_ids,
        ),
        encoding="utf-8",
    )
    html_path.write_text(
        _beads_html(plan, generated_at, board_rows, memories), encoding="utf-8"
    )
    _write_csv_rows(history_path, history["daily"])
    export_path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )

    names = [
        json_path.name,
        xml_path.name,
        md_path.name,
        html_path.name,
        history_path.name,
        export_path.name,
    ]
    size = sum((out_dir / name).stat().st_size for name in names)
    _emit(
        log,
        f"  [green]✓[/green] beads: {len(issues)} issues / {len(ready_ids)} ready / "
        f"{len(blocked_ids)} blocked ({_fmt_bytes(size)})",
    )
    return names, size, payload


# ═══════════════════════════════════════════════════════════════════════════════
# Git log
# ═══════════════════════════════════════════════════════════════════════════════


def _generate_git_log(
    plan: RepoPlan, out_dir: Path, generated_at: str, log: list[str] | None = None
) -> int:
    result = _run(
        [
            "git",
            "log",
            "--all",
            "--reverse",
            "--format=format:%x00%H%x1f%an%x1f%ae%x1f%aI%x1f%D%x1f%s%x1f%B%x1e",
        ],
        cwd=plan.path,
    )
    if result.returncode != 0:
        _emit(
            log,
            f"  [yellow]⚠[/yellow] {plan.name}: git log failed: {result.stderr.strip()}",
        )
        return 0

    root = ET.Element(
        "git-log",
        {
            "repository": str(plan.path),
            "refs": "all",
            "style": "all-refs",
            "generated-at": generated_at,
        },
    )

    count = 0
    for block in result.stdout.split("\x1e"):
        block = block.strip()
        if not block:
            continue
        parts = block.split("\x1f")
        if len(parts) < 7:
            continue
        sha, author, email, date, refs, subject, body = (
            parts[0],
            parts[1],
            parts[2],
            parts[3],
            parts[4],
            parts[5],
            parts[6],
        )
        commit = ET.SubElement(
            root,
            "commit",
            {
                "sha": sha.strip("\x00"),
                "author": author,
                "email": email,
                "date": date,
            },
        )
        if refs.strip():
            commit.set("refs", refs.strip())
        s = ET.SubElement(commit, "subject")
        s.text = subject
        if body.strip():
            b = ET.SubElement(commit, "body")
            b.text = body.strip()
        count += 1

    root.set("count", str(count))
    ET.indent(root, space="  ")
    out_path = out_dir / f"{plan.name}-git-log-all-refs.xml"
    out_path.write_text(
        ET.tostring(root, encoding="unicode", xml_declaration=True), encoding="utf-8"
    )
    _emit(log, f"  [dim]git-log all-refs: {count} commits[/dim]")
    return count


# ═══════════════════════════════════════════════════════════════════════════════
# Extra file copies
# ═══════════════════════════════════════════════════════════════════════════════


def _copy_extras(plan: RepoPlan, out_dir: Path, log: list[str] | None = None) -> int:
    total = 0
    for src_rel, dst_name in plan.extra_copy:
        src = plan.path / src_rel
        if src.exists():
            dst = out_dir / f"{plan.name}-{dst_name}"
            shutil.copy2(src, dst)
            total += dst.stat().st_size
            _emit(
                log,
                f"  [dim]copy: {src_rel} → {dst_name} ({_fmt_bytes(dst.stat().st_size)})[/dim]",
            )
    return total


# ═══════════════════════════════════════════════════════════════════════════════
# GPT-Pro portable sidecars not otherwise represented by Chisel outputs
# ═══════════════════════════════════════════════════════════════════════════════

_TREE_PRUNE_DIRS = {
    ".git",
    ".direnv",
    ".venv",
    "node_modules",
    "target",
    "result",
    "vendor",
}


def _generate_portable_sidecars(
    plan: RepoPlan, out_dir: Path, log: list[str] | None = None
) -> tuple[list[str], int]:
    """Write portable GPT-Pro sidecars absent from Chisel's XML surfaces."""
    sidecars: list[str] = []
    total_bytes = 0

    bundle_path = out_dir / f"{plan.name}-all-refs.bundle"
    bundle_lock = Path(f"{bundle_path}.lock")
    if bundle_lock.exists():
        bundle_lock.unlink()
        _emit(log, f"  [dim]removed stale bundle lock: {bundle_lock.name}[/dim]")
    bundle = _run(["git", "bundle", "create", str(bundle_path), "--all"], cwd=plan.path)
    if bundle.returncode == 0 and bundle_path.exists():
        _emit(
            log,
            f"  [green]✓[/green] {bundle_path.name} ([dim]{_fmt_bytes(bundle_path.stat().st_size)}[/dim])",
        )
        sidecars.append(bundle_path.name)
        total_bytes += bundle_path.stat().st_size
    else:
        details = (bundle.stderr or bundle.stdout or "git bundle failed").strip()
        _emit(log, f"  [yellow]⚠[/yellow] {plan.name}: {details}")

    # Working-tree tar captures committed files AND uncommitted modifications.
    # This differs from `git archive HEAD` which would miss dirty working-tree changes.
    archive_path = out_dir / f"{plan.name}-working-tree.tar.gz"
    plan_excludes = []
    for pat in plan.extra_ignore:
        # Convert a recursive glob into a tar --exclude name.
        p = pat.strip("/").lstrip("**/").rstrip("/**").rstrip("/")
        if p:
            plan_excludes.append(f"--exclude={p}")
    archive = _run(
        [
            "tar",
            "-czf",
            str(archive_path),
            *_WORKTREE_TAR_EXCLUDES,
            *plan_excludes,
            "-C",
            str(plan.path.parent),
            plan.path.name,
        ],
    )
    if archive.returncode == 0 and archive_path.exists():
        _emit(
            log,
            f"  [green]✓[/green] {archive_path.name} ([dim]{_fmt_bytes(archive_path.stat().st_size)}[/dim])",
        )
        sidecars.append(archive_path.name)
        total_bytes += archive_path.stat().st_size
    else:
        details = (archive.stderr or archive.stdout or "tar failed").strip()
        _emit(log, f"  [yellow]⚠[/yellow] {plan.name}: {details}")

    tree_path = out_dir / f"{plan.name}-repo-tree.txt"
    tree_path.write_text(_repo_tree(plan.path, max_depth=3), encoding="utf-8")
    _emit(
        log,
        f"  [green]✓[/green] {tree_path.name} ([dim]{_fmt_bytes(tree_path.stat().st_size)}[/dim])",
    )
    sidecars.append(tree_path.name)
    total_bytes += tree_path.stat().st_size

    return sidecars, total_bytes


def _repo_tree(root: Path, *, max_depth: int) -> str:
    rows: list[str] = ["."]

    def walk(path: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            children = sorted(
                path.iterdir(),
                key=lambda child: (not child.is_dir(), child.name.lower()),
            )
        except OSError:
            return
        for child in children:
            if child.is_dir() and child.name in _TREE_PRUNE_DIRS:
                continue
            rel = child.relative_to(root)
            rows.append(f"./{rel.as_posix()}" + ("/" if child.is_dir() else ""))
            if child.is_dir():
                walk(child, depth + 1)

    walk(root, 1)
    return "\n".join(rows) + "\n"


# ═══════════════════════════════════════════════════════════════════════════════
# Audit, delta, and manifest sidecars
# ═══════════════════════════════════════════════════════════════════════════════


_LOCAL_STATE_PATTERNS = (
    ".local/**",
    ".cache/**",
    ".lynchpin/**",
    ".claude/**",
    ".serena/**",
    ".playwright-mcp/**",
    ".pytest_cache/**",
    ".ruff_cache/**",
    ".mypy_cache/**",
    ".sinex/**",
    ".venv/**",
    "venv/**",
    "node_modules/**",
    "target/**",
    "test-results/**",
    "playwright-report/**",
)

_AGENT_ARCHIVE_PATTERNS = (
    ".agent/archive/**",
    ".agent/scratch/archive/**",
    ".agent/scratch/artifacts/**",
    ".agent/artifacts/**",
)

_AGENT_TRANSIENT_PATTERNS = (
    ".agent/scratch/live-baselines/**",
    ".agent/scratch/live-dogfood-*",
    ".agent/scratch/inbox-imports/**",
    ".agent/scratch/logs/**",
    ".agent/xtask/*.jsonl",
    ".agent/task-history/*.jsonl",
)

_AGENT_ACTIVE_CONTEXT_PATTERNS = (
    ".agent/CONVENTIONS.md",
    ".agent/README.md",
    ".agent/scripts/**",
    ".agent/dev/**",
    ".agent/task-history/**",
    ".agent/cloud-prompts/**",
    ".agent/proposed_issue_set/**",
    ".agent/tools/**",
    ".agent/reports/**",
    ".agent/learnings.local.md",
)

_AGENT_DEMO_PATTERNS = (".agent/demos/**",)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_scope_and_purpose(plan: RepoPlan, name: str) -> tuple[str, str]:
    if name.endswith("-overview.json") or name.endswith("-overview.md"):
        return "overview", "Human-oriented snapshot guide and triage summary"
    if (
        name.endswith("-beads.xml")
        or name.endswith("-beads.json")
        or name.endswith("-beads.md")
        or name.endswith("-beads.html")
        or name.endswith("-beads-history.csv")
        or name.endswith("-beads-export.jsonl")
    ):
        return (
            "beads-context",
            "Rendered local Beads issue, dependency, readiness, and memory context",
        )
    if name.endswith("-all-refs.bundle"):
        return "all-refs", "Git bundle containing all refs"
    if name.endswith("-git-log-all-refs.xml"):
        return "all-refs", "XML git log over all refs"
    if name.endswith("-working-tree.tar.gz"):
        return (
            "current-working-tree",
            "Working-tree archive with uncommitted changes and local-state ignores",
        )
    if name.endswith("-branch-delta.patch"):
        return (
            "current-branch",
            "Patch for current HEAD against the remote default branch merge-base",
        )
    if name.endswith("-branch-delta-log.txt"):
        return (
            "current-branch",
            "Commit log for current HEAD against the remote default branch",
        )
    if name.endswith("-branch-delta-files.txt"):
        return (
            "current-branch",
            "Changed file list for current HEAD against the remote default branch",
        )
    if name.endswith("-branch-delta.md"):
        return "current-branch", "Human-readable branch delta summary"
    if name.endswith("-scratchpad.xml"):
        return "scratchpad", "Repomix XML over .agent/scratch working notes"
    if name.endswith("-accelerants.xml"):
        return (
            "accelerants",
            "Repomix XML over GPT-Pro accelerant corpora (.agent/scratch/corpus-*)",
        )
    if name.endswith("-issues-open.xml") or name.endswith("-issues-closed.xml"):
        return "github-context", "Rendered GitHub issue context"
    if name.endswith("-prs-open.xml") or name.endswith("-prs-merged.xml"):
        return "github-context", "Rendered GitHub pull request context"
    if name.endswith("-tokei-stats.json") or name.endswith("-tokei-stats.md"):
        return "current-working-tree", "Tokei attribution stats by Chisel bucket"
    if "-growth" in name and name.endswith((".json", ".md", ".csv")):
        return (
            "default-branch-history",
            "Git growth, churn, velocity, and attribution analysis",
        )
    if name.endswith("-ignore-audit.json") or name.endswith("-ignore-audit.md"):
        return "audit", "Local-state ignore audit"
    if name.endswith("-agent-audit.json") or name.endswith("-agent-audit.md"):
        return "audit", "Agent workspace layout and prune-candidate audit"
    if name.endswith("-repo-tree.txt"):
        return "current-working-tree", "Shallow repository tree"
    if name.endswith("-compressed.xml"):
        return "current-working-tree", "Compressed repomix XML over configured slices"
    if name.endswith(".xml") and name.startswith(f"{plan.name}-"):
        slice_name = name.removeprefix(f"{plan.name}-").removesuffix(".xml")
        return "current-working-tree", f"Repomix XML slice: {slice_name}"
    if name == f"{plan.name}-manifest.json":
        return "manifest", "Per-project artifact manifest"
    return "sidecar", "Generated Chisel sidecar"


def _path_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    result = _run(["du", "-sb", str(path)])
    if result.returncode == 0 and result.stdout.strip():
        return int(result.stdout.split()[0])
    return 0


def _generate_ignore_audit(
    plan: RepoPlan, out_dir: Path, log: list[str] | None = None
) -> tuple[list[str], int]:
    entries: list[dict[str, Any]] = []
    for child in sorted(plan.path.iterdir(), key=lambda p: p.name):
        if not child.name.startswith(".") and child.name not in {
            "node_modules",
            "target",
            "test-results",
        }:
            continue
        rel = child.relative_to(plan.path).as_posix()
        rel_probe = f"{rel}/" if child.is_dir() else rel
        matched_patterns = [
            pattern
            for pattern in (*DEFAULT_IGNORE, *plan.extra_ignore)
            if _glob_matches(rel_probe, pattern) or _glob_matches(f"{rel}/x", pattern)
        ]
        local_state = [
            pattern
            for pattern in _LOCAL_STATE_PATTERNS
            if _glob_matches(rel_probe, pattern) or _glob_matches(f"{rel}/x", pattern)
        ]
        entries.append(
            {
                "path": rel,
                "kind": "dir" if child.is_dir() else "file",
                "bytes": _path_size(child),
                "ignored": bool(matched_patterns),
                "local_state": bool(local_state),
                "matched_patterns": matched_patterns[:8],
            }
        )

    audit = {
        "project": plan.name,
        "source": str(plan.path),
        "entries": entries,
        "ignored_local_state_bytes": sum(
            e["bytes"] for e in entries if e["ignored"] and e["local_state"]
        ),
        "tracked_hidden_bytes": sum(e["bytes"] for e in entries if not e["ignored"]),
    }
    json_path = out_dir / f"{plan.name}-ignore-audit.json"
    md_path = out_dir / f"{plan.name}-ignore-audit.md"
    json_path.write_text(
        json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    lines = [
        f"# {plan.name} ignore audit",
        "",
        f"Source: `{plan.path}`",
        "",
        "| Path | Ignored | Local state | Size | Matched patterns |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for entry in sorted(entries, key=lambda e: (-e["bytes"], e["path"])):
        patterns = ", ".join(f"`{p}`" for p in entry["matched_patterns"][:4]) or "-"
        lines.append(
            f"| `{entry['path']}` | {str(entry['ignored']).lower()} | "
            f"{str(entry['local_state']).lower()} | {_fmt_bytes(entry['bytes'])} | {patterns} |"
        )
    lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    size = json_path.stat().st_size + md_path.stat().st_size
    _emit(log, f"  [green]✓[/green] ignore-audit ({_fmt_bytes(size)})")
    return [json_path.name, md_path.name], size


def _agent_audit_class(rel: str) -> tuple[str, str]:
    if _glob_any(rel, _AGENT_ARCHIVE_PATTERNS):
        return (
            "archive-or-generated",
            "Review for relocation outside .agent or leave excluded from Chisel",
        )
    if _glob_any(rel, _AGENT_TRANSIENT_PATTERNS):
        return (
            "transient-heavy",
            "Keep out of main context; summarize through manifests or generated reports",
        )
    if rel.startswith(".agent/scratch/"):
        return "scratchpad-managed", "Covered by the scratchpad snapshot"
    if _glob_any(rel, _AGENT_ACTIVE_CONTEXT_PATTERNS):
        return "active-context", "Keep visible in agent/devloop Chisel slices"
    if _glob_any(rel, _AGENT_DEMO_PATTERNS):
        return (
            "demo-or-devloop",
            "Keep segmented from instructions; prune bulky generated payloads case by case",
        )
    return "review", "Unclassified .agent surface; inspect before including broadly"


def _agent_audit_rows(agent_dir: Path, repo_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        try:
            rel = path.relative_to(repo_root).as_posix()
        except ValueError:
            return
        if rel in seen:
            return
        seen.add(rel)
        rel_probe = f"{rel}/" if path.is_dir() else rel
        cls, recommendation = _agent_audit_class(rel_probe)
        rows.append(
            {
                "path": rel,
                "kind": "dir" if path.is_dir() else "file",
                "bytes": _path_size(path),
                "files": sum(1 for child in path.rglob("*") if child.is_file())
                if path.is_dir()
                else 1,
                "class": cls,
                "recommendation": recommendation,
            }
        )

    for child in sorted(agent_dir.iterdir(), key=lambda p: p.name):
        add(child)
        if child.is_dir():
            for grandchild in sorted(child.iterdir(), key=lambda p: p.name):
                if grandchild.is_dir():
                    add(grandchild)

    return sorted(rows, key=lambda row: (-int(row["bytes"]), row["path"]))


def _generate_agent_audit(
    plan: RepoPlan, out_dir: Path, log: list[str] | None = None
) -> tuple[list[str], int]:
    agent_dir = plan.path / ".agent"
    if not agent_dir.exists():
        return [], 0

    rows = _agent_audit_rows(agent_dir, plan.path)
    by_class: dict[str, dict[str, int]] = {}
    for row in rows:
        entry = by_class.setdefault(
            row["class"], {"bytes": 0, "files": 0, "entries": 0}
        )
        entry["bytes"] += int(row["bytes"])
        entry["files"] += int(row["files"])
        entry["entries"] += 1

    audit = {
        "project": plan.name,
        "source": str(agent_dir),
        "summary_by_class": dict(sorted(by_class.items())),
        "entries": rows,
    }
    json_path = out_dir / f"{plan.name}-agent-audit.json"
    md_path = out_dir / f"{plan.name}-agent-audit.md"
    json_path.write_text(
        json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    lines = [
        f"# {plan.name} agent workspace audit",
        "",
        f"Source: `{agent_dir}`",
        "",
        "This is a read-only audit. Chisel does not delete or move these files.",
        "",
        "## Summary",
        "",
        "| Class | Entries | Files | Size |",
        "| --- | ---: | ---: | ---: |",
    ]
    for cls, entry in sorted(
        by_class.items(), key=lambda item: (-item[1]["bytes"], item[0])
    ):
        lines.append(
            f"| `{cls}` | {entry['entries']} | {entry['files']} | {_fmt_bytes(entry['bytes'])} |"
        )
    lines.extend(
        (
            "",
            "## Largest Entries",
            "",
            "| Path | Class | Files | Size | Recommendation |",
            "| --- | --- | ---: | ---: | --- |",
        )
    )
    for row in rows[:40]:
        lines.append(
            f"| `{row['path']}` | `{row['class']}` | {row['files']} | "
            f"{_fmt_bytes(row['bytes'])} | {row['recommendation']} |"
        )
    lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")

    size = json_path.stat().st_size + md_path.stat().st_size
    _emit(log, f"  [green]✓[/green] agent-audit ({_fmt_bytes(size)})")
    return [json_path.name, md_path.name], size


def _remote_default_ref(repo: Path) -> str:
    symbolic = _run(
        ["git", "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"],
        cwd=repo,
    )
    if symbolic.returncode == 0 and symbolic.stdout.strip():
        return symbolic.stdout.strip()
    for candidate in ("origin/master", "origin/main"):
        exists = _run(["git", "rev-parse", "--verify", "--quiet", candidate], cwd=repo)
        if exists.returncode == 0:
            return candidate
    return "HEAD"


def _generate_branch_delta(
    plan: RepoPlan, out_dir: Path, log: list[str] | None = None
) -> tuple[list[str], int]:
    base_ref = _remote_default_ref(plan.path)
    if base_ref == "HEAD":
        md_path = out_dir / f"{plan.name}-branch-delta.md"
        md_path.write_text(
            f"# {plan.name} branch delta\n\nNo remote default branch is configured for this checkout.\n",
            encoding="utf-8",
        )
        _emit(log, "  [dim]branch-delta: no remote default branch configured[/dim]")
        return [md_path.name], md_path.stat().st_size

    merge_base = _run(["git", "merge-base", "HEAD", base_ref], cwd=plan.path)
    files: list[str] = []
    if merge_base.returncode != 0 or not merge_base.stdout.strip():
        md_path = out_dir / f"{plan.name}-branch-delta.md"
        md_path.write_text(
            f"# {plan.name} branch delta\n\nUnable to determine merge-base for `{base_ref}`.\n",
            encoding="utf-8",
        )
        _emit(
            log,
            f"  [yellow]⚠[/yellow] branch-delta: merge-base unavailable for {base_ref}",
        )
        return [md_path.name], md_path.stat().st_size

    base = merge_base.stdout.strip()
    stat = _run(["git", "diff", "--stat", f"{base}...HEAD"], cwd=plan.path)
    diff = _run(["git", "diff", "--binary", f"{base}...HEAD"], cwd=plan.path)
    changed = _run(["git", "diff", "--name-status", f"{base}...HEAD"], cwd=plan.path)
    commits = _run(
        ["git", "log", "--oneline", "--decorate", f"{base}..HEAD"], cwd=plan.path
    )

    outputs = {
        f"{plan.name}-branch-delta.patch": diff.stdout,
        f"{plan.name}-branch-delta-files.txt": changed.stdout,
        f"{plan.name}-branch-delta-log.txt": commits.stdout,
    }
    for name, content in outputs.items():
        path = out_dir / name
        path.write_text(content, encoding="utf-8")
        files.append(path.name)

    md_path = out_dir / f"{plan.name}-branch-delta.md"
    md_path.write_text(
        "\n".join(
            (
                f"# {plan.name} branch delta",
                "",
                f"Base ref: `{base_ref}`",
                f"Merge base: `{base}`",
                "",
                "## Diff Stat",
                "",
                "```text",
                stat.stdout.strip(),
                "```",
                "",
                "## Commits",
                "",
                "```text",
                commits.stdout.strip(),
                "```",
                "",
            )
        ),
        encoding="utf-8",
    )
    files.append(md_path.name)
    size = sum((out_dir / name).stat().st_size for name in files)
    _emit(log, f"  [green]✓[/green] branch-delta vs {base_ref} ({_fmt_bytes(size)})")
    return files, size


def _read_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _xml_declared_count(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return None
    try:
        return int(root.attrib.get("count") or len(list(root)))
    except ValueError:
        return len(list(root))


def _artifact_rows(out_dir: Path, plan: RepoPlan) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(out_dir.iterdir(), key=lambda item: item.name):
        if not path.is_file():
            continue
        scope, purpose = _file_scope_and_purpose(plan, path.name)
        rows.append(
            {
                "name": path.name,
                "bytes": path.stat().st_size,
                "scope": scope,
                "purpose": purpose,
            }
        )
    return rows


def _generate_snapshot_overview(
    plan: RepoPlan,
    out_dir: Path,
    generated_at: str,
    git: dict[str, str | bool],
    *,
    issues_open: int,
    issues_closed: int,
    prs_open: int,
    prs_merged: int,
    gitlog_commits: int,
    xml_errors: list[str],
    beads: dict[str, Any] | None = None,
    log: list[str] | None = None,
) -> tuple[list[str], int]:
    artifacts = _artifact_rows(out_dir, plan)
    stats = _read_json_file(out_dir / f"{plan.name}-tokei-stats.json")
    agent_audit = _read_json_file(out_dir / f"{plan.name}-agent-audit.json")
    ignore_audit = _read_json_file(out_dir / f"{plan.name}-ignore-audit.json")

    large_artifacts = [
        row
        for row in sorted(artifacts, key=lambda item: int(item["bytes"]), reverse=True)
        if int(row["bytes"]) >= LARGE_SLICE_BYTES
    ][:12]
    top_buckets = sorted(
        (stats.get("buckets") or {}).items(),
        key=lambda item: int(item[1].get("lines") or 0),
        reverse=True,
    )[:8]
    agent_summary = agent_audit.get("summary_by_class") or {}
    review_agent_entries = int((agent_summary.get("review") or {}).get("entries") or 0)
    archive_agent_bytes = int(
        (agent_summary.get("archive-or-generated") or {}).get("bytes") or 0
    )
    ignored_local_state = int(ignore_audit.get("ignored_local_state_bytes") or 0)
    tracked_hidden = int(ignore_audit.get("tracked_hidden_bytes") or 0)
    branch_delta_patch = out_dir / f"{plan.name}-branch-delta.patch"
    branch_delta_size = (
        branch_delta_patch.stat().st_size if branch_delta_patch.exists() else 0
    )
    xml_snapshot_count = len(plan.slices) + int(plan.compressed) + 3
    beads = beads or {}
    beads_counts = beads.get("counts") if beads.get("available") else {}
    beads_counts = beads_counts if isinstance(beads_counts, dict) else {}
    beads_issues = int(beads_counts.get("issues") or 0)
    beads_ready = int(beads_counts.get("ready") or 0)
    beads_blocked = int(beads_counts.get("blocked") or 0)
    beads_dependencies = int(beads_counts.get("dependencies") or 0)
    beads_memories = int(beads_counts.get("memories") or 0)

    open_first = [
        f"{plan.name}-overview.md",
        f"{plan.name}-manifest.json",
        f"{plan.name}-beads.md" if beads.get("available") else None,
        f"{plan.name}-prs-open.xml" if prs_open else None,
        f"{plan.name}-issues-open.xml" if issues_open else None,
        f"{plan.name}-branch-delta.md",
        f"{plan.name}-growth.md",
        f"{plan.name}-tokei-stats.md",
        f"{plan.name}-agent-audit.md" if agent_audit else None,
    ]
    open_first = [item for item in open_first if item]

    overview = {
        "project": plan.name,
        "source": str(plan.path),
        "generated_at": generated_at,
        "git": git,
        "counts": {
            "configured_slices": len(plan.slices),
            "xml_snapshots": xml_snapshot_count,
            "artifacts": len(artifacts) + 3,
            "issues_open": issues_open,
            "issues_closed": issues_closed,
            "prs_open": prs_open,
            "prs_merged": prs_merged,
            "gitlog_commits": gitlog_commits,
            "beads_available": bool(beads.get("available")),
            "beads_issues": beads_issues,
            "beads_ready": beads_ready,
            "beads_blocked": beads_blocked,
            "beads_dependencies": beads_dependencies,
            "beads_memories": beads_memories,
            "open_issue_xml_count": _xml_declared_count(
                out_dir / f"{plan.name}-issues-open.xml"
            ),
            "open_pr_xml_count": _xml_declared_count(
                out_dir / f"{plan.name}-prs-open.xml"
            ),
        },
        "attention": {
            "xml_errors": xml_errors,
            "large_artifacts": large_artifacts,
            "agent_review_entries": review_agent_entries,
            "agent_archive_or_generated_bytes": archive_agent_bytes,
            "ignored_local_state_bytes": ignored_local_state,
            "tracked_hidden_bytes": tracked_hidden,
            "branch_delta_patch_bytes": branch_delta_size,
            "beads_blocked": beads_blocked,
        },
        "top_buckets": [
            {
                "name": name,
                "files": bucket.get("files"),
                "lines": bucket.get("lines"),
                "code": bucket.get("code"),
                "comments": bucket.get("comments"),
            }
            for name, bucket in top_buckets
        ],
        "open_first": open_first,
    }

    json_path = out_dir / f"{plan.name}-overview.json"
    md_path = out_dir / f"{plan.name}-overview.md"
    json_path.write_text(
        json.dumps(overview, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    lines = [
        f"# {plan.name} Chisel overview",
        "",
        f"Generated: {generated_at}",
        f"Source: `{plan.path}`",
        f"Git: `{git.get('branch', '?')}` @ `{str(git.get('commit', ''))[:8]}` dirty={str(git.get('dirty', '?')).lower()}",
        "",
        "## Counts",
        "",
        "| Signal | Count |",
        "| --- | ---: |",
        f"| Configured slices | {len(plan.slices)} |",
        f"| XML snapshots | {xml_snapshot_count} |",
        f"| Artifacts | {len(artifacts) + 3} |",
        f"| Open issues | {issues_open} |",
        f"| Open PRs | {prs_open} |",
        f"| Merged PRs | {prs_merged} |",
        f"| Beads issues | {beads_issues} |",
        f"| Beads ready | {beads_ready} |",
        f"| Beads blocked | {beads_blocked} |",
        f"| All-ref git commits | {gitlog_commits} |",
        "",
        "## Open First",
        "",
    ]
    lines.extend(f"- `{item}`" for item in open_first)

    attention_lines: list[str] = []
    if xml_errors:
        attention_lines.append(f"- XML validation errors: {len(xml_errors)}")
    if large_artifacts:
        attention_lines.append(
            f"- Large artifacts >= {_fmt_bytes(LARGE_SLICE_BYTES)}: {len(large_artifacts)}"
        )
    if review_agent_entries:
        attention_lines.append(
            f"- Agent audit has {review_agent_entries} unclassified review entr{'y' if review_agent_entries == 1 else 'ies'}."
        )
    if archive_agent_bytes:
        attention_lines.append(
            f"- Agent archive/generated surface: {_fmt_bytes(archive_agent_bytes)}."
        )
    if ignored_local_state:
        attention_lines.append(
            f"- Ignored local runtime state: {_fmt_bytes(ignored_local_state)}."
        )
    if tracked_hidden:
        attention_lines.append(
            f"- Tracked hidden files/directories: {_fmt_bytes(tracked_hidden)}."
        )
    if branch_delta_size:
        attention_lines.append(
            f"- Current branch delta patch: {_fmt_bytes(branch_delta_size)}."
        )
    if beads_blocked:
        attention_lines.append(
            f"- Beads has {beads_blocked} blocked issue{'s' if beads_blocked != 1 else ''}."
        )
    lines.extend(
        ("", "## Attention", "", *(attention_lines or ["- No attention flags."]))
    )

    lines.extend(
        (
            "",
            "## Largest Artifacts",
            "",
            "| Artifact | Scope | Size |",
            "| --- | --- | ---: |",
        )
    )
    for row in sorted(artifacts, key=lambda item: int(item["bytes"]), reverse=True)[
        :12
    ]:
        lines.append(
            f"| `{row['name']}` | `{row['scope']}` | {_fmt_bytes(int(row['bytes']))} |"
        )

    lines.extend(
        (
            "",
            "## Top Attribution Buckets",
            "",
            "| Bucket | Files | Lines | Code | Comments |",
            "| --- | ---: | ---: | ---: | ---: |",
        )
    )
    for name, bucket in top_buckets:
        lines.append(
            f"| `{name}` | {int(bucket.get('files') or 0):,} | "
            f"{int(bucket.get('lines') or 0):,} | {int(bucket.get('code') or 0):,} | "
            f"{int(bucket.get('comments') or 0):,} |"
        )
    lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")

    size = json_path.stat().st_size + md_path.stat().st_size
    _emit(log, f"  [green]✓[/green] overview ({_fmt_bytes(size)})")
    return [json_path.name, md_path.name], size


def _generate_snapshot_audit(
    plan: RepoPlan,
    out_dir: Path,
    generated_at: str,
    *,
    previous_manifest: dict[str, Any] | None = None,
    log: list[str] | None = None,
) -> tuple[list[str], int]:
    artifacts = _artifact_rows(out_dir, plan)
    agent_audit = _read_json_file(out_dir / f"{plan.name}-agent-audit.json")
    ignore_audit = _read_json_file(out_dir / f"{plan.name}-ignore-audit.json")
    overview = _read_json_file(out_dir / f"{plan.name}-overview.json")
    previous_artifacts = {
        str(row.get("name")): int(row.get("bytes") or 0)
        for row in (previous_manifest or {}).get("artifacts", [])
        if row.get("name")
    }
    size_delta = [
        {
            "name": row["name"],
            "bytes": row["bytes"],
            "previous_bytes": previous_artifacts.get(str(row["name"])),
            "delta_bytes": None
            if str(row["name"]) not in previous_artifacts
            else int(row["bytes"]) - previous_artifacts[str(row["name"])],
        }
        for row in artifacts
    ]
    size_delta = sorted(
        size_delta,
        key=lambda item: abs(int(item["delta_bytes"] or 0)),
        reverse=True,
    )[:12]
    agent_summary = agent_audit.get("summary_by_class") or {}
    github = _github_context_manifest or {}
    beads = overview.get("counts") or {}
    audit = {
        "project": plan.name,
        "generated_at": generated_at,
        "status": "attention"
        if (overview.get("attention") or {}).get("large_artifacts")
        else "ok",
        "counts": overview.get("counts") or {},
        "size": {
            "total_bytes": sum(int(row["bytes"]) for row in artifacts),
            "largest_artifacts": sorted(
                artifacts, key=lambda item: int(item["bytes"]), reverse=True
            )[:12],
            "largest_deltas": size_delta,
        },
        "agent_workspace": {
            "summary_by_class": agent_summary,
            "active_context_entries": int(
                (agent_summary.get("active-context") or {}).get("entries") or 0
            ),
            "devloop_entries": int(
                (agent_summary.get("transient-heavy") or {}).get("entries") or 0
            ),
            "archive_or_generated_bytes": int(
                (agent_summary.get("archive-or-generated") or {}).get("bytes") or 0
            ),
        },
        "local_state": {
            "ignored_local_state_bytes": int(
                ignore_audit.get("ignored_local_state_bytes") or 0
            ),
            "tracked_hidden_bytes": int(ignore_audit.get("tracked_hidden_bytes") or 0),
        },
        "branch_delta": {
            "patch_bytes": int(
                ((overview.get("attention") or {}).get("branch_delta_patch_bytes")) or 0
            ),
        },
        "beads": {
            "available": bool(beads.get("beads_available")),
            "issues": int(beads.get("beads_issues") or 0),
            "ready": int(beads.get("beads_ready") or 0),
            "blocked": int(beads.get("beads_blocked") or 0),
            "dependencies": int(beads.get("beads_dependencies") or 0),
            "memories": int(beads.get("beads_memories") or 0),
        },
        "github_context": {
            "inventory_items_seen": int(github.get("inventory_items_seen") or 0),
            "detail_refreshes": int(github.get("detail_refreshes") or 0),
            "detail_reuses": int(github.get("detail_reuses") or 0),
            "detail_misses": int(github.get("detail_misses") or 0),
            "detail_decision_reasons": github.get("detail_decision_reasons") or {},
            "project_detail_refreshes": github.get("project_detail_refreshes") or {},
            "project_detail_reuses": github.get("project_detail_reuses") or {},
            "project_stale_open_removed": github.get("project_stale_open_removed")
            or {},
        },
        "open_first": overview.get("open_first") or [],
    }
    json_path = out_dir / f"{plan.name}-snapshot-audit.json"
    md_path = out_dir / f"{plan.name}-snapshot-audit.md"
    json_path.write_text(
        json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    lines = [
        f"# {plan.name} snapshot audit",
        "",
        f"Generated: {generated_at}",
        f"Status: `{audit['status']}`",
        "",
        "## Open First",
        "",
        *(f"- `{item}`" for item in audit["open_first"]),
        "",
        "## GitHub Context",
        "",
        f"- Inventory items: {audit['github_context']['inventory_items_seen']}",
        f"- Detail refreshes/reuses: {audit['github_context']['detail_refreshes']} / {audit['github_context']['detail_reuses']}",
        f"- Stale open rows removed: {sum(int(v or 0) for v in audit['github_context']['project_stale_open_removed'].values())}",
        "",
        "## Beads Context",
        "",
        f"- Available: {str(audit['beads']['available']).lower()}",
        f"- Issues / ready / blocked: {audit['beads']['issues']} / {audit['beads']['ready']} / {audit['beads']['blocked']}",
        f"- Dependencies / memories: {audit['beads']['dependencies']} / {audit['beads']['memories']}",
        "",
        "## Largest Artifacts",
        "",
        "| Artifact | Scope | Size |",
        "| --- | --- | ---: |",
    ]
    for row in audit["size"]["largest_artifacts"]:
        lines.append(
            f"| `{row['name']}` | `{row['scope']}` | {_fmt_bytes(int(row['bytes']))} |"
        )
    lines.extend(
        (
            "",
            "## Largest Size Deltas",
            "",
            "| Artifact | Current | Delta |",
            "| --- | ---: | ---: |",
        )
    )
    for row in audit["size"]["largest_deltas"]:
        delta = row["delta_bytes"]
        delta_text = "new" if delta is None else _fmt_bytes(int(delta))
        lines.append(
            f"| `{row['name']}` | {_fmt_bytes(int(row['bytes']))} | {delta_text} |"
        )
    md_path.write_text("\n".join(lines), encoding="utf-8")
    size = json_path.stat().st_size + md_path.stat().st_size
    _emit(log, f"  [green]✓[/green] snapshot-audit ({_fmt_bytes(size)})")
    return [json_path.name, md_path.name], size


def _write_project_manifest(
    plan: RepoPlan,
    out_dir: Path,
    generated_at: str,
    git: dict[str, str | bool],
    xml_errors: list[str],
    log: list[str] | None = None,
) -> tuple[str, int]:
    manifest_path = out_dir / f"{plan.name}-manifest.json"
    artifacts = []
    for path in sorted(out_dir.iterdir(), key=lambda p: p.name):
        if not path.is_file():
            continue
        scope, purpose = _file_scope_and_purpose(plan, path.name)
        artifacts.append(
            {
                "name": path.name,
                "bytes": path.stat().st_size,
                "sha256": None if path == manifest_path else _sha256_file(path),
                "scope": scope,
                "purpose": purpose,
            }
        )
    artifacts.append(
        {
            "name": manifest_path.name,
            "bytes": 0,
            "sha256": None,
            "scope": "manifest",
            "purpose": "Per-project artifact manifest",
        }
    )
    manifest = {
        "project": plan.name,
        "source": str(plan.path),
        "generated_at": generated_at,
        "git": git,
        "slices": [s.__dict__ for s in plan.slices],
        "stats_buckets": [b.__dict__ for b in _stats_buckets(plan)],
        "xml_valid": len(xml_errors) == 0,
        "xml_errors": xml_errors,
        "artifacts": artifacts,
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    # Update manifest entry size after writing; sha256 remains null by design.
    manifest["artifacts"][-1]["bytes"] = manifest_path.stat().st_size
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    size = manifest_path.stat().st_size
    _emit(log, f"  [green]✓[/green] {manifest_path.name} ({_fmt_bytes(size)})")
    return manifest_path.name, size


_CHART_COLORS = ("#7c3aed", "#0891b2", "#ea580c", "#16a34a", "#db2777", "#4f46e5")


def _svg_number(value: float, *, percent: bool = False) -> str:
    if percent:
        return f"{value:.0f}%"
    absolute = abs(value)
    if absolute >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if absolute >= 1_000:
        return f"{value / 1_000:.0f}k"
    return f"{value:.0f}"


def _svg_line_chart(
    title: str,
    subtitle: str,
    series: Sequence[dict[str, Any]],
    *,
    percent: bool = False,
) -> str:
    width, height = 1200, 680
    left, right, top, bottom = 92, 32, 92, 82
    plot_w, plot_h = width - left - right, height - top - bottom
    points = [point for item in series for point in item["points"]]
    if not points:
        points = [(dt.date.today().isoformat(), 0.0)]
    dates = [dt.date.fromisoformat(str(point[0])) for point in points]
    start, end = min(dates), max(dates)
    span = max(1, (end - start).days)
    values = [float(point[1]) for point in points]
    low = min(0.0, min(values))
    high = max(1.0, max(values))
    if math.isclose(low, high):
        high = low + 1.0

    def x_pos(day: str) -> float:
        return left + ((dt.date.fromisoformat(day) - start).days / span) * plot_w

    def y_pos(value: float) -> float:
        return top + (high - value) / (high - low) * plot_h

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">',
        f"<title>{html.escape(title)}</title>",
        f"<desc>{html.escape(subtitle)}</desc>",
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{left}" y="38" font-family="system-ui,sans-serif" font-size="25" font-weight="700" fill="#111827">{html.escape(title)}</text>',
        f'<text x="{left}" y="65" font-family="system-ui,sans-serif" font-size="14" fill="#475569">{html.escape(subtitle)}</text>',
    ]
    for index in range(6):
        value = low + (high - low) * index / 5
        y = y_pos(value)
        svg.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" stroke="#e2e8f0"/>'
        )
        svg.append(
            f'<text x="{left - 12}" y="{y + 5:.1f}" text-anchor="end" font-family="system-ui,sans-serif" font-size="12" fill="#64748b">{html.escape(_svg_number(value, percent=percent))}</text>'
        )
    for index in range(6):
        day = start + dt.timedelta(days=round(span * index / 5))
        x = left + plot_w * index / 5
        svg.append(
            f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{top + plot_h}" stroke="#f1f5f9"/>'
        )
        svg.append(
            f'<text x="{x:.1f}" y="{top + plot_h + 28}" text-anchor="middle" font-family="system-ui,sans-serif" font-size="12" fill="#64748b">{day.isoformat()}</text>'
        )
    for index, item in enumerate(series):
        color = _CHART_COLORS[index % len(_CHART_COLORS)]
        coords = " ".join(
            f"{x_pos(str(day)):.1f},{y_pos(float(value)):.1f}"
            for day, value in item["points"]
        )
        svg.append(
            f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>'
        )
        legend_x = left + index * 185
        svg.append(
            f'<line x1="{legend_x}" y1="{height - 25}" x2="{legend_x + 26}" y2="{height - 25}" stroke="{color}" stroke-width="4"/>'
        )
        svg.append(
            f'<text x="{legend_x + 34}" y="{height - 20}" font-family="system-ui,sans-serif" font-size="13" fill="#334155">{html.escape(str(item["name"]))}</text>'
        )
    svg.append("</svg>")
    return "\n".join(svg)


def _svg_heatmap(title: str, weekly_by_project: dict[str, list[dict[str, Any]]]) -> str:
    weeks = sorted({row["week"] for rows in weekly_by_project.values() for row in rows})
    cell = max(8, min(18, 1000 // max(1, len(weeks))))
    left, top = 150, 105
    width = max(900, left + len(weeks) * cell + 55)
    height = top + len(weekly_by_project) * 54 + 90
    values = [
        int(row["commits"]) for rows in weekly_by_project.values() for row in rows
    ]
    maximum = max(values, default=1)
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">',
        f"<title>{html.escape(title)}</title>",
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="40" y="38" font-family="system-ui,sans-serif" font-size="25" font-weight="700" fill="#111827">{html.escape(title)}</text>',
        '<text x="40" y="65" font-family="system-ui,sans-serif" font-size="14" fill="#475569">Author-date commits per calendar week; color uses a logarithmic scale.</text>',
    ]
    for row_index, (project, rows) in enumerate(weekly_by_project.items()):
        y = top + row_index * 54
        counts = {row["week"]: int(row["commits"]) for row in rows}
        svg.append(
            f'<text x="{left - 14}" y="{y + cell - 2}" text-anchor="end" font-family="system-ui,sans-serif" font-size="13" fill="#334155">{html.escape(project)}</text>'
        )
        for week_index, week in enumerate(weeks):
            count = counts.get(week, 0)
            intensity = math.log1p(count) / math.log1p(maximum) if maximum else 0
            red = round(241 - 117 * intensity)
            green = round(245 - 194 * intensity)
            blue = round(249 - 11 * intensity)
            fill = f"rgb({red},{green},{blue})"
            x = left + week_index * cell
            svg.append(
                f'<rect x="{x}" y="{y}" width="{cell - 1}" height="{cell - 1}" rx="2" fill="{fill}"><title>{html.escape(project)} · {week}: {count} commits</title></rect>'
            )
    tick_every = max(1, len(weeks) // 8)
    for index in range(0, len(weeks), tick_every):
        x = left + index * cell
        svg.append(
            f'<text x="{x}" y="{top - 14}" transform="rotate(-35 {x} {top - 14})" font-family="system-ui,sans-serif" font-size="11" fill="#64748b">{weeks[index]}</text>'
        )
    svg.append("</svg>")
    return "\n".join(svg)


def _stats_role(bucket: str) -> str:
    lowered = bucket.lower()
    if "demo" in lowered or "artifact" in lowered:
        return "Evidence/demo"
    if "test" in lowered or lowered in {"qa"}:
        return "Tests"
    if (
        "doc" in lowered
        or lowered.startswith("agent-")
        or lowered in {"agent-context", "agent-workspace"}
    ):
        return "Docs/context"
    return "Production/tooling"


def _composition_rows(
    plans: Sequence[RepoPlan], output_root: Path
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for plan in plans:
        stats = _read_json_file(
            output_root / plan.name / f"{plan.name}-tokei-stats.json"
        )
        totals = {
            role: 0
            for role in ("Production/tooling", "Tests", "Docs/context", "Evidence/demo")
        }
        for bucket, values in (stats.get("buckets") or {}).items():
            totals[_stats_role(bucket)] += int(values.get("code") or 0)
        maintained = (
            totals["Production/tooling"] + totals["Tests"] + totals["Docs/context"]
        )
        denominator = totals["Production/tooling"] + totals["Tests"]
        rows.append(
            {
                "project": plan.name,
                **totals,
                "Maintained code": maintained,
                "Test share of production+tests": totals["Tests"] / denominator
                if denominator
                else None,
                "Evidence payload / maintained code": totals["Evidence/demo"]
                / maintained
                if maintained
                else None,
            }
        )
    return rows


def _svg_composition(rows: Sequence[dict[str, Any]]) -> str:
    width, height = 1200, 180 + len(rows) * 92
    left, right, top = 185, 55, 105
    plot_w = width - left - right
    roles = ("Production/tooling", "Tests", "Docs/context", "Evidence/demo")
    colors = ("#7c3aed", "#0891b2", "#94a3b8", "#ea580c")
    maximum = max(
        1,
        max((sum(int(row[role]) for role in roles) for row in rows), default=0),
    )
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">',
        "<title>Current repository composition</title>",
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<text x="42" y="38" font-family="system-ui,sans-serif" font-size="25" font-weight="700" fill="#111827">Current repository composition</text>',
        '<text x="42" y="65" font-family="system-ui,sans-serif" font-size="14" fill="#475569">Tokei code lines from tracked and non-ignored working-tree files; ignored private/local corpora are excluded.</text>',
    ]
    for index, role in enumerate(roles):
        x = 42 + index * 210
        svg.append(
            f'<rect x="{x}" y="82" width="14" height="14" rx="2" fill="{colors[index]}"/>'
        )
        svg.append(
            f'<text x="{x + 21}" y="94" font-family="system-ui,sans-serif" font-size="12" fill="#334155">{html.escape(role)}</text>'
        )
    for row_index, row in enumerate(rows):
        y = top + 45 + row_index * 92
        svg.append(
            f'<text x="{left - 18}" y="{y + 23}" text-anchor="end" font-family="system-ui,sans-serif" font-size="14" font-weight="600" fill="#334155">{html.escape(str(row["project"]))}</text>'
        )
        cursor = left
        for role, color in zip(roles, colors, strict=True):
            value = int(row[role])
            bar_w = value / maximum * plot_w
            if bar_w > 0:
                svg.append(
                    f'<rect x="{cursor:.1f}" y="{y}" width="{bar_w:.1f}" height="32" fill="{color}"><title>{html.escape(role)}: {value:,}</title></rect>'
                )
            cursor += bar_w
        total = sum(int(row[role]) for role in roles)
        svg.append(
            f'<text x="{cursor + 10:.1f}" y="{y + 22}" font-family="system-ui,sans-serif" font-size="12" fill="#64748b">{total:,}</text>'
        )
    svg.append("</svg>")
    return "\n".join(svg)


def _svg_monthly_net(monthly_by_project: dict[str, list[dict[str, Any]]]) -> str:
    months = sorted(
        {row["month"] for rows in monthly_by_project.values() for row in rows}
    )
    projects = list(monthly_by_project)
    width, height = max(1200, 150 + len(months) * 52), 680
    left, right, top, bottom = 88, 40, 95, 115
    plot_w, plot_h = width - left - right, height - top - bottom
    values = [int(row["net"]) for rows in monthly_by_project.values() for row in rows]
    low, high = min([0, *values]), max([1, *values])
    scale = plot_h / (high - low)
    zero_y = top + high * scale
    group_w = plot_w / max(1, len(months))
    bar_w = max(2, group_w * 0.78 / max(1, len(projects)))
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">',
        "<title>Monthly net tracked-text growth</title>",
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{left}" y="38" font-family="system-ui,sans-serif" font-size="25" font-weight="700" fill="#111827">Monthly net tracked-text growth</text>',
        f'<text x="{left}" y="65" font-family="system-ui,sans-serif" font-size="14" fill="#475569">Additions minus deletions on each repository default branch.</text>',
        f'<line x1="{left}" y1="{zero_y:.1f}" x2="{left + plot_w}" y2="{zero_y:.1f}" stroke="#64748b"/>',
    ]
    by_project = {
        project: {row["month"]: int(row["net"]) for row in rows}
        for project, rows in monthly_by_project.items()
    }
    for month_index, month in enumerate(months):
        group_x = left + month_index * group_w + group_w * 0.11
        for project_index, project in enumerate(projects):
            value = by_project[project].get(month, 0)
            x = group_x + project_index * bar_w
            y = zero_y - max(0, value) * scale
            height_value = abs(value) * scale
            if value < 0:
                y = zero_y
            color = _CHART_COLORS[project_index % len(_CHART_COLORS)]
            svg.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(1, bar_w - 1):.1f}" height="{height_value:.1f}" fill="{color}"><title>{html.escape(project)} · {month}: {value:,}</title></rect>'
            )
        x_label = left + (month_index + 0.5) * group_w
        svg.append(
            f'<text x="{x_label:.1f}" y="{top + plot_h + 28}" transform="rotate(-45 {x_label:.1f} {top + plot_h + 28})" text-anchor="end" font-family="system-ui,sans-serif" font-size="11" fill="#64748b">{month[:7]}</text>'
        )
    for index, project in enumerate(projects):
        x = left + index * 180
        color = _CHART_COLORS[index % len(_CHART_COLORS)]
        svg.append(
            f'<rect x="{x}" y="{height - 25}" width="14" height="14" fill="{color}"/>'
        )
        svg.append(
            f'<text x="{x + 21}" y="{height - 13}" font-family="system-ui,sans-serif" font-size="12" fill="#334155">{html.escape(project)}</text>'
        )
    svg.append("</svg>")
    return "\n".join(svg)


def _write_growth_portfolio(
    output_root: Path,
    plans: Sequence[RepoPlan],
    generated_at: str,
) -> dict[str, Any]:
    growth_dir = output_root / "growth"
    if growth_dir.exists():
        shutil.rmtree(growth_dir)
    growth_dir.mkdir(parents=True)
    growth_by_project: dict[str, dict[str, Any]] = {}
    for plan in plans:
        path = output_root / plan.name / f"{plan.name}-growth.json"
        if path.exists():
            growth_by_project[plan.name] = _read_json_file(path)
    composition = _composition_rows(plans, output_root)
    beads_by_project: dict[str, dict[str, Any]] = {}
    for plan in plans:
        path = output_root / plan.name / f"{plan.name}-beads.json"
        payload = _read_json_file(path)
        if payload.get("available") and (payload.get("history") or {}).get("daily"):
            beads_by_project[plan.name] = payload

    summary_rows = [
        {"project": project, **growth["summary"]}
        for project, growth in growth_by_project.items()
    ]
    daily_rows = [
        {"project": project, **row}
        for project, growth in growth_by_project.items()
        for row in growth["daily"]
    ]
    weekly_rows = [
        {"project": project, **row}
        for project, growth in growth_by_project.items()
        for row in growth["weekly"]
    ]
    monthly_rows = [
        {"project": project, **row}
        for project, growth in growth_by_project.items()
        for row in growth["monthly"]
    ]
    beads_history_rows = [
        {"project": project, **row}
        for project, payload in beads_by_project.items()
        for row in payload["history"]["daily"]
    ]
    payload = {
        "generated_at": generated_at,
        "projects": list(growth_by_project),
        "summaries": summary_rows,
        "composition": composition,
        "beads": {
            project: {
                "counts": payload.get("counts"),
                "history_summary": payload["history"]["summary"],
            }
            for project, payload in beads_by_project.items()
        },
        "privacy_boundary": (
            "LOC uses tracked plus Git-visible untracked files after .ignore/.tokeignore; "
            "ignored local demo exports and runtime state are excluded."
        ),
    }
    (growth_dir / "project-growth-summary.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    _write_csv_rows(growth_dir / "project-growth-summary.csv", summary_rows)
    _write_csv_rows(growth_dir / "daily-project-growth.csv", daily_rows)
    _write_csv_rows(growth_dir / "weekly-project-growth.csv", weekly_rows)
    _write_csv_rows(growth_dir / "monthly-project-growth.csv", monthly_rows)
    _write_csv_rows(growth_dir / "code-composition.csv", composition)
    _write_csv_rows(growth_dir / "beads-history.csv", beads_history_rows)

    cumulative_series = [
        {
            "name": project,
            "points": [(row["day"], row["cumulative_net"]) for row in growth["daily"]],
        }
        for project, growth in growth_by_project.items()
    ]
    normalized_series = []
    churn_series = []
    for project, growth in growth_by_project.items():
        final_net = float(growth["summary"]["net_tracked_text_lines"] or 0)
        if final_net:
            normalized_series.append(
                {
                    "name": project,
                    "points": [
                        (row["day"], float(row["cumulative_net"]) / final_net * 100)
                        for row in growth["daily"]
                    ],
                }
            )
        churn_series.append(
            {
                "name": project,
                "points": [
                    (
                        row["day"],
                        float(row["rolling_28d_relative_to_final_net"] or 0) * 100,
                    )
                    for row in growth["daily"]
                ],
            }
        )
    charts = {
        "01-cumulative-net-tracked-text-growth.svg": _svg_line_chart(
            "Cumulative net tracked-text growth",
            "Additions minus deletions on each default branch; binary rows excluded.",
            cumulative_series,
        ),
        "02-normalized-growth-trajectory.svg": _svg_line_chart(
            "Normalized growth trajectory",
            "Each repository's cumulative net tracked text as a percentage of its current final net.",
            normalized_series,
            percent=True,
        ),
        "03-rolling-28d-relative-churn.svg": _svg_line_chart(
            "Rolling 28-day relative churn",
            "Gross additions plus deletions over 28 days, divided by current final net tracked text.",
            churn_series,
            percent=True,
        ),
        "04-weekly-commit-activity-heatmap.svg": _svg_heatmap(
            "Weekly commit activity",
            {
                project: growth["weekly"]
                for project, growth in growth_by_project.items()
            },
        ),
        "05-maintained-code-composition.svg": _svg_composition(composition),
        "06-monthly-net-growth.svg": _svg_monthly_net(
            {
                project: growth["monthly"]
                for project, growth in growth_by_project.items()
            }
        ),
        "07-beads-backlog-trajectory.svg": _svg_line_chart(
            "Beads backlog trajectory",
            "Current issue-set reconstruction from created and closed timestamps; reopen cycles and deleted/compacted issues are not recovered.",
            [
                {
                    "name": project,
                    "points": [
                        (row["day"], row["open_snapshot"])
                        for row in payload["history"]["daily"]
                    ],
                }
                for project, payload in beads_by_project.items()
            ],
        ),
    }
    for name, content in charts.items():
        (growth_dir / name).write_text(content, encoding="utf-8")

    lines = [
        "# Project growth and change shape",
        "",
        f"Generated: {generated_at}",
        "",
        "This report distinguishes default-branch tracked-text history from current maintained-code composition. Git growth includes implementation, tests, documentation, configuration, schemas, and other tracked text. Tokei composition uses tracked files plus non-ignored working-tree files, then applies `.ignore` and `.tokeignore`; ignored local evidence exports, dependency trees, caches, and private runtime state are not counted or packaged.",
        "",
        "![Cumulative tracked-text growth](01-cumulative-net-tracked-text-growth.svg)",
        "",
        "![Normalized trajectory](02-normalized-growth-trajectory.svg)",
        "",
        "![Rolling churn](03-rolling-28d-relative-churn.svg)",
        "",
        "![Weekly activity](04-weekly-commit-activity-heatmap.svg)",
        "",
        "![Code composition](05-maintained-code-composition.svg)",
        "",
        "![Monthly net growth](06-monthly-net-growth.svg)",
        "",
        "![Beads backlog trajectory](07-beads-backlog-trajectory.svg)",
        "",
        "## Growth summary",
        "",
        "| Project | Net tracked text | Gross churn | Gross/net | Commits | Active days | Last 30d net | Last 90d net | 50% size date |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in summary_rows:
        ratio = row.get("gross_to_net_ratio")
        ratio_text = f"{ratio:.2f}×" if isinstance(ratio, (int, float)) else "n/a"
        lines.append(
            f"| `{row['project']}` | {row['net_tracked_text_lines']:,} | {row['gross_line_churn']:,} | "
            f"{ratio_text} | {row['default_branch_commits']:,} | {row['active_days']:,} | "
            f"{row['last_30_days']['net']:,} | {row['last_90_days']['net']:,} | "
            f"{row.get('date_reached_50pct_current_size') or 'n/a'} |"
        )
    lines.extend(
        (
            "",
            "## Current composition",
            "",
            "| Project | Production/tooling | Tests | Docs/context | Evidence/demo | Maintained | Test share |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        )
    )
    for row in composition:
        share = row.get("Test share of production+tests")
        lines.append(
            f"| `{row['project']}` | {row['Production/tooling']:,} | {row['Tests']:,} | "
            f"{row['Docs/context']:,} | {row['Evidence/demo']:,} | {row['Maintained code']:,} | "
            f"{share:.1%} |"
            if isinstance(share, (int, float))
            else f"| `{row['project']}` | {row['Production/tooling']:,} | {row['Tests']:,} | "
            f"{row['Docs/context']:,} | {row['Evidence/demo']:,} | {row['Maintained code']:,} | n/a |"
        )
    if beads_by_project:
        lines.extend(
            (
                "",
                "## Beads delivery history",
                "",
                "| Project | Issues | Ready | Blocked | Closed | Median lead days | P90 lead days | Closed 30d | Closed 90d | Board |",
                "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
            )
        )
        for project, payload in beads_by_project.items():
            counts = payload.get("counts") or {}
            history_summary = payload["history"]["summary"]
            status_counts = counts.get("by_status") or {}
            closed = sum(
                int(status_counts.get(status) or 0)
                for status in ("closed", "done", "resolved")
            )
            median_lead = history_summary.get("median_lead_days")
            p90_lead = history_summary.get("p90_lead_days")
            board = f"../{project}/{project}-beads.html"
            lines.append(
                f"| `{project}` | {int(counts.get('issues') or 0):,} | {int(counts.get('ready') or 0):,} | "
                f"{int(counts.get('blocked') or 0):,} | {closed:,} | "
                f"{median_lead:.2f} | {p90_lead:.2f} | "
                f"{history_summary['closed_last_30_days']:,} | {history_summary['closed_last_90_days']:,} | "
                f"[browse]({board}) |"
                if isinstance(median_lead, (int, float))
                and isinstance(p90_lead, (int, float))
                else f"| `{project}` | {int(counts.get('issues') or 0):,} | {int(counts.get('ready') or 0):,} | "
                f"{int(counts.get('blocked') or 0):,} | {closed:,} | n/a | n/a | "
                f"{history_summary['closed_last_30_days']:,} | {history_summary['closed_last_90_days']:,} | "
                f"[browse]({board}) |"
            )
    lines.extend(
        (
            "",
            "## Extended analysis",
            "",
            "Each project directory also contains a growth report with recent 30/90-day velocity, peak rolling churn, weekly churn concentration, historical change volume by today's attribution buckets, and conventional commit-kind mix. CSV and JSON files retain the underlying daily, weekly, monthly, and composition data.",
            "",
            "## Interpretation limits",
            "",
            "- Net tracked-text growth is not a source-code line count.",
            "- High churn can reflect refactoring, replacement, generated-surface renewal, or history structure; it is not a quality judgment.",
            "- Current Tokei composition and historical Git growth answer different questions and should not be added together.",
            "- Commit counts describe integration cadence, not human effort or independent review.",
            "",
        )
    )
    (growth_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")
    return {
        "directory": str(growth_dir),
        "projects": list(growth_by_project),
        "files": sorted(path.name for path in growth_dir.iterdir() if path.is_file()),
    }


def _write_root_index(
    output_root: Path,
    plans: Sequence[RepoPlan],
    results: dict[str, Any],
    generated_at: str,
    repomix_version: str,
    total_elapsed: float,
) -> tuple[str, str]:
    projects: list[dict[str, Any]] = []
    for plan in plans:
        manifest_path = output_root / plan.name / f"{plan.name}-manifest.json"
        stats_path = output_root / plan.name / f"{plan.name}-tokei-stats.json"
        overview_path = output_root / plan.name / f"{plan.name}-overview.json"
        audit_path = output_root / plan.name / f"{plan.name}-snapshot-audit.json"
        manifest = (
            json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest_path.exists()
            else {}
        )
        stats = (
            json.loads(stats_path.read_text(encoding="utf-8"))
            if stats_path.exists()
            else {}
        )
        overview = (
            json.loads(overview_path.read_text(encoding="utf-8"))
            if overview_path.exists()
            else {}
        )
        audit = (
            json.loads(audit_path.read_text(encoding="utf-8"))
            if audit_path.exists()
            else {}
        )
        artifacts = manifest.get("artifacts") or []
        buckets = stats.get("buckets") or {}
        projects.append(
            {
                "name": plan.name,
                "status": results.get(plan.name, {}).get("status", "missing"),
                "source": str(plan.path),
                "git": manifest.get("git", results.get(plan.name, {}).get("git")),
                "total_bytes": sum(int(a.get("bytes") or 0) for a in artifacts),
                "artifact_count": len(artifacts),
                "largest_artifacts": sorted(
                    artifacts,
                    key=lambda artifact: int(artifact.get("bytes") or 0),
                    reverse=True,
                )[:10],
                "buckets": buckets,
                "inline_rust_tests": stats.get("rust_inline_tests"),
                "overview": overview,
                "snapshot_audit": audit,
                "manifest": str(manifest_path.relative_to(output_root))
                if manifest_path.exists()
                else None,
                "overview_markdown": f"{plan.name}/{plan.name}-overview.md"
                if (output_root / plan.name / f"{plan.name}-overview.md").exists()
                else None,
                "snapshot_audit_markdown": f"{plan.name}/{plan.name}-snapshot-audit.md"
                if (output_root / plan.name / f"{plan.name}-snapshot-audit.md").exists()
                else None,
            }
        )

    index = {
        "generated_at": generated_at,
        "repomix_version": repomix_version,
        "output_root": str(output_root),
        "total_elapsed_s": total_elapsed,
        "growth_analysis": "growth/README.md"
        if (output_root / "growth" / "README.md").exists()
        else None,
        "projects": projects,
    }
    json_path = output_root / "index.json"
    md_path = output_root / "index.md"
    json_path.write_text(
        json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    lines = [
        "# Chisel Snapshot Index",
        "",
        f"Generated: {generated_at}",
        f"Repomix: `{repomix_version}`",
        f"Output root: `{output_root}`",
        "",
        "Growth and change-shape analysis: `growth/README.md`",
        "",
        "## Projects",
        "",
        "| Project | Status | Branch | Dirty | GitHub issues | Open PRs | Beads issues | Beads ready | Beads blocked | Artifacts | Size | Overview | Audit | Manifest |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for project in projects:
        git = project.get("git") or {}
        manifest_link = project["manifest"] or "-"
        overview_link = project["overview_markdown"] or "-"
        audit_link = project["snapshot_audit_markdown"] or "-"
        counts = (project.get("overview") or {}).get("counts") or {}
        lines.append(
            f"| `{project['name']}` | {project['status']} | `{git.get('branch', '?')}` | "
            f"{str(git.get('dirty', '?')).lower()} | {counts.get('issues_open', 0)} | "
            f"{counts.get('prs_open', 0)} | {counts.get('beads_issues', 0)} | "
            f"{counts.get('beads_ready', 0)} | {counts.get('beads_blocked', 0)} | "
            f"{project['artifact_count']} | "
            f"{_fmt_bytes(project['total_bytes'])} | `{overview_link}` | `{audit_link}` | `{manifest_link}` |"
        )
    lines.extend(
        (
            "",
            "## Attention Summary",
            "",
            "| Project | Large artifacts | Agent review | Agent archive/generated | Branch delta | Beads blocked |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        )
    )
    for project in projects:
        attention = (project.get("overview") or {}).get("attention") or {}
        lines.append(
            f"| `{project['name']}` | {len(attention.get('large_artifacts') or [])} | "
            f"{attention.get('agent_review_entries', 0)} | "
            f"{_fmt_bytes(int(attention.get('agent_archive_or_generated_bytes') or 0))} | "
            f"{_fmt_bytes(int(attention.get('branch_delta_patch_bytes') or 0))} | "
            f"{attention.get('beads_blocked', 0)} |"
        )
    lines.extend(("", "## Largest Artifacts", ""))
    for project in projects:
        lines.extend(
            (
                f"### {project['name']}",
                "",
                "| Artifact | Scope | Size |",
                "| --- | --- | ---: |",
            )
        )
        for artifact in project["largest_artifacts"][:8]:
            lines.append(
                f"| `{artifact['name']}` | `{artifact['scope']}` | {_fmt_bytes(int(artifact['bytes']))} |"
            )
        lines.append("")
    lines.extend(("## Attribution Buckets", ""))
    for project in projects:
        lines.extend(
            (
                f"### {project['name']}",
                "",
                "| Bucket | Files | Lines | Code | Comments |",
                "| --- | ---: | ---: | ---: | ---: |",
            )
        )
        for name, bucket in (project.get("buckets") or {}).items():
            lines.append(
                f"| `{name}` | {bucket['files']:,} | {bucket['lines']:,} | "
                f"{bucket['code']:,} | {bucket['comments']:,} |"
            )
        inline = project.get("inline_rust_tests") or {}
        if inline.get("blocks"):
            lines.append(
                f"| `inline-rust-tests` | {inline['files']:,} | {inline['lines']:,} | n/a | n/a |"
            )
        lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path.name, md_path.name


# ═══════════════════════════════════════════════════════════════════════════════
# Combined-output tar
# ═══════════════════════════════════════════════════════════════════════════════


def _make_combined_tar(
    plan: RepoPlan, out_dir: Path, output_root: Path, log: list[str] | None = None
) -> tuple[str, int] | None:
    """Create a single tar of all files chisel generated for this project."""
    combined_path = output_root / f"{plan.name}-all.tar.gz"
    result = _run(
        ["tar", "-czf", str(combined_path), "-C", str(output_root), plan.name]
    )
    if result.returncode == 0 and combined_path.exists():
        size = combined_path.stat().st_size
        _emit(
            log,
            f"  [green]✓[/green] {combined_path.name} ([dim]{_fmt_bytes(size)}[/dim])",
        )
        return combined_path.name, size
    details = (result.stderr or result.stdout or "tar failed").strip()
    _emit(log, f"  [yellow]⚠[/yellow] {plan.name}: combined tar: {details}")
    return None


def _archive_timestamp_from_index(output_root: Path) -> str | None:
    index_path = output_root / "index.json"
    if not index_path.exists():
        return None
    payload = _read_json_file(index_path)
    if not isinstance(payload, dict):
        return None
    generated_at = payload.get("generated_at")
    return generated_at if isinstance(generated_at, str) and generated_at else None


def _combined_tar_archive_timestamp(paths: Sequence[Path], output_root: Path) -> str:
    indexed = _archive_timestamp_from_index(output_root)
    if indexed is not None:
        return indexed
    newest = max(path.stat().st_mtime for path in paths)
    return dt.datetime.fromtimestamp(newest, dt.timezone.utc).strftime(
        "%Y-%m-%dT%H%M%SZ"
    )


def _archive_dir_for_combined_tars(
    output_root: Path, timestamp: str, filenames: Sequence[str]
) -> Path:
    archive_root = output_root / "archive"
    candidate = archive_root / timestamp
    suffix = 1
    while any((candidate / filename).exists() for filename in filenames):
        suffix += 1
        candidate = archive_root / f"{timestamp}-{suffix:02d}"
    return candidate


def _archive_existing_combined_tars(
    plans: Sequence[RepoPlan], output_root: Path, log: list[str] | None = None
) -> list[str]:
    """Move previous root combined packages aside before this run overwrites them."""
    existing = [output_root / f"{plan.name}-all.tar.gz" for plan in plans]
    existing = [path for path in existing if path.exists()]
    if not existing:
        return []

    timestamp = _combined_tar_archive_timestamp(existing, output_root)
    archive_dir = _archive_dir_for_combined_tars(
        output_root, timestamp, [path.name for path in existing]
    )
    archive_dir.mkdir(parents=True, exist_ok=True)

    archived: list[str] = []
    for path in existing:
        target = archive_dir / path.name
        shutil.move(str(path), str(target))
        archived.append(str(target.relative_to(output_root)))

    _emit(
        log,
        f"[dim]Archived previous combined packages:[/dim] {archive_dir.relative_to(output_root)}",
    )
    return archived


# ═══════════════════════════════════════════════════════════════════════════════
# Per-repo builder (parallel slices within repo)
# ═══════════════════════════════════════════════════════════════════════════════


def _build_one(
    plan: RepoPlan,
    output_root: Path,
    repomix_bin: str,
    generated_at: str,
    slice_workers: int,
) -> dict:
    """Build all slices, current-tree sidecars, and all-refs git history for one repo."""
    log: list[str] = []
    if not plan.path.exists():
        return {
            "project": plan.name,
            "status": "missing",
            "log_lines": [
                f"[bold]{plan.name}[/bold]  [red]missing[/red]  [dim]{plan.path}[/dim]"
            ],
        }

    t0 = dt.datetime.now()
    out_dir = output_root / plan.name
    previous_manifest_path = out_dir / f"{plan.name}-manifest.json"
    previous_manifest = (
        _read_json_file(previous_manifest_path)
        if previous_manifest_path.exists()
        else None
    )
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    git = _git_state(plan.path)

    planned_outputs = _planned_output_count(plan)
    xml_snapshots = len(plan.slices) + int(plan.compressed) + 3
    sidecars = planned_outputs - xml_snapshots
    _emit(
        log,
        f"[bold]{plan.name}[/bold]  [dim]{plan.path}[/dim]  "
        f"{git['branch']} @ {git['commit'][:8]}  "
        f"[dim]{len(plan.slices)} configured slices, {xml_snapshots} XML snapshots, "
        f"{sidecars} sidecars, {slice_workers} slice workers[/dim]",
    )
    _print_live(
        f"→ {plan.name}: start  {git['branch']} @ {git['commit'][:8]}  "
        f"({xml_snapshots} XML + {sidecars} sidecars, {slice_workers} slice workers)"
    )

    slices_done: list[tuple[str, int]] = []
    errors: list[str] = []

    # ── Run everything in parallel within the repo ──
    with ThreadPoolExecutor(max_workers=slice_workers) as ex:
        futures: dict = {}

        def submit(kind: str, label: str, fn, *args):
            def run_logged():
                started = dt.datetime.now()
                _print_live(f"  → {plan.name}: {kind} {label}")
                return fn(*args), started

            f = ex.submit(run_logged)
            futures[f] = (kind, label)

        # Per-slice repomix
        for slice in plan.slices:
            submit(
                "slice",
                slice.name,
                _run_slice,
                repomix_bin,
                out_dir,
                plan,
                slice,
                git,
                generated_at,
                log,
            )

        # Compressed whole-repo snapshot (code repos only)
        if plan.compressed:
            submit(
                "compressed",
                plan.name,
                _run_compressed,
                repomix_bin,
                out_dir,
                plan,
                git,
                generated_at,
                log,
            )

        # Scratchpad (.agent/scratch/ working notes)
        submit(
            "scratchpad",
            plan.name,
            _run_scratchpad,
            repomix_bin,
            out_dir,
            plan,
            git,
            generated_at,
            log,
        )

        # Accelerant corpora (.agent/scratch/corpus-* GPT-Pro packs)
        submit(
            "accelerants",
            plan.name,
            _run_accelerants,
            repomix_bin,
            out_dir,
            plan,
            git,
            generated_at,
            log,
        )

        # Git log
        submit(
            "git-log", plan.name, _generate_git_log, plan, out_dir, generated_at, log
        )

        # Issues
        submit("issues", plan.name, _generate_issues, plan, out_dir, generated_at, log)

        # PRs
        submit("prs", plan.name, _generate_prs, plan, out_dir, generated_at, log)

        # Portable upload sidecars not otherwise represented by XML snapshots.
        submit("sidecars", plan.name, _generate_portable_sidecars, plan, out_dir, log)

        # Tokei-based attribution stats by project-specific category.
        submit(
            "tokei-stats",
            plan.name,
            _generate_tokei_stats,
            plan,
            out_dir,
            generated_at,
            log,
        )

        # Default-branch growth, churn, velocity, and historical bucket shape.
        submit(
            "growth-analysis",
            plan.name,
            _generate_growth_analysis,
            plan,
            out_dir,
            generated_at,
            log,
        )

        # Local-state ignore audit.
        submit("ignore-audit", plan.name, _generate_ignore_audit, plan, out_dir, log)

        # Agent workspace layout and cleanup candidate audit.
        submit("agent-audit", plan.name, _generate_agent_audit, plan, out_dir, log)

        # Current branch delta against the remote default branch.
        submit("branch-delta", plan.name, _generate_branch_delta, plan, out_dir, log)

        # Local Beads issue tracker context.
        submit("beads", plan.name, _generate_beads, plan, out_dir, generated_at, log)

        gitlog_commits = 0
        issues_open = issues_closed = 0
        prs_open = prs_merged = 0
        sidecars_done: list[str] = []
        sidecars_bytes = 0
        stats_files_done: list[str] = []
        stats_bytes = 0
        growth_files_done: list[str] = []
        growth_bytes = 0
        audit_files_done: list[str] = []
        audit_bytes = 0
        agent_audit_files_done: list[str] = []
        agent_audit_bytes = 0
        delta_files_done: list[str] = []
        delta_bytes = 0
        beads_files_done: list[str] = []
        beads_bytes = 0
        beads_context: dict[str, Any] = {"available": False}
        snapshot_audit_files_done: list[str] = []

        for future in as_completed(futures):
            kind, label = futures[future]
            try:
                result, started = future.result()
                if kind == "slice":
                    name, size = result
                    slices_done.append((name, size))
                elif kind == "git-log":
                    gitlog_commits = result
                elif kind == "issues":
                    issues_open, issues_closed = result
                elif kind == "prs":
                    prs_open, prs_merged = result
                elif kind == "compressed":
                    name, size = result
                    slices_done.append((name, size))
                elif kind == "scratchpad":
                    if result is not None:
                        name, size = result
                        slices_done.append((name, size))
                elif kind == "accelerants":
                    if result is not None:
                        name, size = result
                        slices_done.append((name, size))
                elif kind == "sidecars":
                    names, size = result
                    sidecars_done.extend(names)
                    sidecars_bytes += size
                elif kind == "tokei-stats":
                    names, size = result
                    stats_files_done.extend(names)
                    stats_bytes += size
                elif kind == "growth-analysis":
                    names, size = result
                    growth_files_done.extend(names)
                    growth_bytes += size
                elif kind == "ignore-audit":
                    names, size = result
                    audit_files_done.extend(names)
                    audit_bytes += size
                elif kind == "agent-audit":
                    names, size = result
                    agent_audit_files_done.extend(names)
                    agent_audit_bytes += size
                elif kind == "branch-delta":
                    names, size = result
                    delta_files_done.extend(names)
                    delta_bytes += size
                elif kind == "beads":
                    names, size, beads_context = result
                    beads_files_done.extend(names)
                    beads_bytes += size
                elapsed = (dt.datetime.now() - started).total_seconds()
                _print_live(f"  ✓ {plan.name}: {kind} {label} ({elapsed:.1f}s)")
            except Exception as e:
                msg = str(e)
                errors.append(f"{kind}: {msg}")
                _emit(log, f"  [red]✗[/red] {kind}: {msg}")
                _print_live(f"  ✗ {plan.name}: {kind} {label}: {msg}")

    # ── Extra copies (after repomix finishes) ──
    _copy_extras(plan, out_dir, log)

    # ── Validate all XML outputs ──
    xml_errors: list[str] = []
    for xml_file in sorted(out_dir.glob("*.xml")):
        err = _validate_xml(xml_file)
        if err:
            xml_errors.append(f"{xml_file.name}: {err}")

    if xml_errors:
        for e in xml_errors:
            _emit(log, f"  [red]✗ XML invalid:[/red] {e}")

    # ── Human-oriented guide after all generated facts exist ──
    overview_files_done, overview_bytes = _generate_snapshot_overview(
        plan,
        out_dir,
        generated_at,
        git,
        issues_open=issues_open,
        issues_closed=issues_closed,
        prs_open=prs_open,
        prs_merged=prs_merged,
        gitlog_commits=gitlog_commits,
        xml_errors=xml_errors,
        beads=beads_context,
        log=log,
    )
    snapshot_audit_files_done, _snapshot_audit_bytes = _generate_snapshot_audit(
        plan,
        out_dir,
        generated_at,
        previous_manifest=previous_manifest,
        log=log,
    )

    # ── Manifest after all per-project artifacts exist, before combined tar ──
    manifest_name, manifest_bytes = _write_project_manifest(
        plan, out_dir, generated_at, git, xml_errors, log
    )

    # ── Combined tar of everything chisel generated for this project ──
    combined_tar_result = _make_combined_tar(plan, out_dir, output_root, log)
    combined_tar_name = (
        combined_tar_result[0] if combined_tar_result is not None else None
    )
    combined_tar_bytes = (
        combined_tar_result[1] if combined_tar_result is not None else 0
    )

    elapsed = (dt.datetime.now() - t0).total_seconds()
    total_bytes = sum(
        path.stat().st_size for path in out_dir.iterdir() if path.is_file()
    )

    return {
        "project": plan.name,
        "status": "partial" if errors else "generated",
        "git": git,
        "slices": len(slices_done),
        "slice_names": [s[0] for s in slices_done],
        "sidecars": sidecars_done,
        "stats_files": stats_files_done,
        "growth_files": growth_files_done,
        "audit_files": audit_files_done,
        "agent_audit_files": agent_audit_files_done,
        "delta_files": delta_files_done,
        "beads_files": beads_files_done,
        "beads_bytes": beads_bytes,
        "overview_files": overview_files_done,
        "snapshot_audit_files": snapshot_audit_files_done,
        "manifest": manifest_name,
        "combined_tar": combined_tar_name,
        "combined_tar_bytes": combined_tar_bytes,
        "total_bytes": total_bytes,
        "issues_open": issues_open,
        "issues_closed": issues_closed,
        "prs_open": prs_open,
        "prs_merged": prs_merged,
        "gitlog_commits": gitlog_commits,
        "xml_valid": len(xml_errors) == 0,
        "xml_errors": xml_errors or None,
        "elapsed_s": round(elapsed, 1),
        "errors": errors or None,
        "log_lines": log,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Top-level orchestrator
# ═══════════════════════════════════════════════════════════════════════════════


def build_chisel_bundles(
    *,
    project_names: Sequence[str] | None = None,
    output_root: Path | None = None,
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> dict[str, Any]:
    global _github_context_index, _github_context_manifest, _github_context_ready
    _abort_event.clear()
    _github_context_index = None
    _github_context_manifest = None
    _github_context_ready = (
        None  # reset per-run so repeated calls in the same process work
    )
    repomix_bin = _require_repomix()
    repomix_ver = _repomix_version(repomix_bin)
    generated_at = _utc_ts()
    output_root = (output_root or _default_output_root()).resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    if project_names:
        unknown = [n for n in project_names if n not in REPO_PLANS]
        if unknown:
            available = ", ".join(sorted(REPO_PLANS))
            raise ValueError(
                f"unknown projects: {', '.join(unknown)}; available: {available}"
            )
        plans = [REPO_PLANS[n] for n in project_names]
    else:
        plans = list(REPO_PLANS.values())

    repo_workers = min(max(1, max_workers), max(1, len(plans)))
    slice_workers = DEFAULT_SLICE_WORKERS

    _print(f"[bold]Chisel — XML repomix snapshots[/bold]  ({repomix_ver})")
    _print(f"Output: {output_root}")
    _print(f"Repos:  {len(plans)} selected — {', '.join(p.name for p in plans)}")
    _print(
        f"Pools:  {repo_workers} across repos × {slice_workers} within each; "
        f"{DEFAULT_REPOMIX_WORKERS} global repomix slots"
    )
    _print_scope(plans, output_root)
    _print()
    _ensure_chisel_prerequisites(plans)
    _archive_existing_combined_tars(plans, output_root)
    _print()

    results: dict[str, Any] = {}
    t0 = dt.datetime.now()

    ex = ThreadPoolExecutor(max_workers=repo_workers)
    futures = {
        ex.submit(
            _build_one, plan, output_root, repomix_bin, generated_at, slice_workers
        ): plan.name
        for plan in plans
    }
    try:
        completed = 0
        for future in as_completed(futures):
            name = futures[future]
            completed += 1
            try:
                results[name] = future.result()
                r = results[name]
                status = r.get("status", "?")
                elapsed = r.get("elapsed_s", 0)
                _print(
                    f"\n[bold][{completed}/{len(plans)}] {name} complete[/bold]  {status}  [dim]{elapsed:.1f}s[/dim]"
                )
                for line in r.get("log_lines") or []:
                    _print(line)
            except Exception as e:
                results[name] = {
                    "project": name,
                    "status": "failed",
                    "error": str(e),
                    "log_lines": [f"  [red]✗[/red] {name}: {e}"],
                }
                _print(f"\n[bold][{completed}/{len(plans)}] {name} failed[/bold]")
                _print(f"  [red]✗[/red] {name}: {e}")
    except KeyboardInterrupt:
        _abort_event.set()
        _terminate_active_processes()
        for future in futures:
            future.cancel()
        ex.shutdown(wait=False, cancel_futures=True)
        _print_live(
            "\n[yellow]Interrupted. Stopped active chisel subprocesses.[/yellow]"
        )
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(130)
    else:
        ex.shutdown(wait=True)

    total_elapsed = round((dt.datetime.now() - t0).total_seconds(), 1)
    growth_portfolio = _write_growth_portfolio(output_root, plans, generated_at)
    _print(
        f"[green]Wrote growth portfolio:[/green] growth/README.md "
        f"({len(growth_portfolio['files'])} artifacts)"
    )

    # ── Summary table ──
    if _console is not None:
        table = Table(title=f"Chisel — {generated_at}", title_style="bold")
        table.add_column("Repo", style="bold", no_wrap=True)
        table.add_column("St", no_wrap=True)
        table.add_column("Snap", justify="right", no_wrap=True)
        table.add_column("Issues", justify="right", no_wrap=True)
        table.add_column("PRs", justify="right", no_wrap=True)
        table.add_column("Git", justify="right", no_wrap=True)
        table.add_column("Size", justify="right", no_wrap=True)
        table.add_column("Time", justify="right", no_wrap=True)

        total_bytes = 0
        for plan in plans:
            r = results.get(plan.name, {})
            status = r.get("status", "?")
            color = (
                "green"
                if status == "generated"
                else "yellow"
                if status == "partial"
                else "red"
            )
            status_label = (
                "OK"
                if status == "generated"
                else "PART"
                if status == "partial"
                else "FAIL"
            )
            configured_slices = len(plan.slices)
            xml_snapshots = r.get("slices", 0)
            snapshots = f"{configured_slices}/{xml_snapshots}"
            issues = f"{r.get('issues_open', 0)}o/{r.get('issues_closed', 0)}c"
            prs = f"{r.get('prs_open', 0)}o/{r.get('prs_merged', 0)}m"
            commits = str(r.get("gitlog_commits", 0))
            size = r.get("total_bytes", 0)
            total_bytes += size
            elapsed = f"{r.get('elapsed_s', 0):.1f}s"
            table.add_row(
                plan.name,
                f"[{color}]{status_label}[/{color}]",
                snapshots,
                issues,
                prs,
                commits,
                _fmt_bytes(size),
                elapsed,
            )

        table.add_section()
        table.add_row(
            "[bold]TOTAL[/bold]",
            "",
            "",
            "",
            "",
            "",
            _fmt_bytes(total_bytes),
            f"{total_elapsed:.1f}s",
        )
        _console.print(table)  # type: ignore[possibly-undefined]  # Table imported with rich
    else:
        _print(
            f"\n{'Repo':<22} {'St':<5} {'Snap':>7} {'Issues':>12} {'PRs':>12} {'Git':>8} {'Size':>12} {'Time':>8}"
        )
        _print("-" * 100)
        total_bytes = 0
        for plan in plans:
            r = results.get(plan.name, {})
            status = r.get("status", "?")
            status_label = (
                "OK"
                if status == "generated"
                else "PART"
                if status == "partial"
                else "FAIL"
            )
            configured_slices = len(plan.slices)
            xml_snapshots = r.get("slices", 0)
            snapshots = f"{configured_slices}/{xml_snapshots}"
            issues = f"{r.get('issues_open', 0)}o/{r.get('issues_closed', 0)}c"
            prs = f"{r.get('prs_open', 0)}o/{r.get('prs_merged', 0)}m"
            commits = str(r.get("gitlog_commits", 0))
            size = r.get("total_bytes", 0)
            total_bytes += size
            elapsed = f"{r.get('elapsed_s', 0)}s"
            _print(
                f"{plan.name:<22} {status_label:<5} {snapshots:>7} {issues:>12} {prs:>12} {commits:>8} {_fmt_bytes(size):>12} {elapsed:>8}"
            )
        _print("-" * 100)

    # ── Validation summary ──
    all_xml_errors: list[str] = []
    for plan in plans:
        r = results.get(plan.name, {})
        for xml_err in r.get("xml_errors") or []:
            all_xml_errors.append(f"  {plan.name}/{xml_err}")
    if all_xml_errors:
        _print(f"\n[yellow]XML validation issues ({len(all_xml_errors)}):[/yellow]")
        for xml_err in all_xml_errors:
            _print(xml_err)
    else:
        _print("\n[green]All XML outputs well-formed.[/green]")

    index_json, index_md = _write_root_index(
        output_root, plans, results, generated_at, repomix_ver, total_elapsed
    )
    _print(f"[green]Wrote root index:[/green] {index_json}, {index_md}")
    _print(f"[dim]Done. {output_root}[/dim]")

    return {
        "generated_at": generated_at,
        "output_root": str(output_root),
        "repomix_version": repomix_ver,
        "total_elapsed_s": total_elapsed,
        "total_bytes": total_bytes,
        "index": {"json": index_json, "markdown": index_md},
        "growth": growth_portfolio,
        "projects": results,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# CLI entry (called from projects/cli.py)
# ═══════════════════════════════════════════════════════════════════════════════


def _split_names(value: str) -> list[str] | None:
    names = [item for item in value.split() if item]
    return names or None


def _parse_optional_path(value: str) -> Path | None:
    stripped = value.strip()
    return Path(stripped) if stripped else None


def run_from_cli(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="Chisel — XML repomix snapshots with semantic splitting and GitHub issue commentary.",
    )
    ap.add_argument(
        "--projects",
        default="",
        help="Whitespace-separated project names (default: all registered).",
    )
    ap.add_argument(
        "--output-root",
        type=_parse_optional_path,
        default=None,
        help="Output directory (default: derived_root/code-snapshots — stable, overwrites on re-run).",
    )
    ap.add_argument(
        "--max-workers",
        type=int,
        default=DEFAULT_MAX_WORKERS,
        help=f"Max parallel repos (default: {DEFAULT_MAX_WORKERS}).",
    )
    ap.add_argument(
        "--list", action="store_true", help="List available project plans and exit."
    )
    args = ap.parse_args(argv)

    if args.list:
        _print("Available chisel projects:\n")
        for name, plan in sorted(REPO_PLANS.items()):
            slices_str = ", ".join(s.name for s in plan.slices)
            _print(f"  [bold]{name}[/bold]")
            _print(f"    path:       {plan.path}")
            _print(f"    github:     {plan.github_slug or '—'}")
            _print(f"    compressed: {'yes' if plan.compressed else 'no'}")
            _print(f"    slices:     {slices_str}")
            if plan.extra_copy:
                copies = ", ".join(f"{s}→{d}" for s, d in plan.extra_copy)
                _print(f"    copies:     {copies}")
            _print()
        return 0

    build_chisel_bundles(
        project_names=_split_names(args.projects),
        output_root=args.output_root,
        max_workers=args.max_workers,
    )
    return 0
