"""Build pinned source and evidence packages with atomic publication.

The default output is the configured code snapshot root. XML renderings are
optional; the default package includes source, history, trackers, and reports.
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
import time
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from ..core.errors import MaterializationError, SourceUnavailableError
from . import chisel_options

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
_progress_lock = threading.Lock()
_active_stages: dict[str, set[str]] = {}
_build_state_local = threading.local()


def _set_stage(project: str, stage: str, active: bool) -> None:
    with _progress_lock:
        if chisel_options.active_options.events:
            path = Path(chisel_options.active_options.events)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a") as stream:
                stream.write(json.dumps({"project": project, "stage": stage, "active": active, "observed_at": dt.datetime.now(dt.timezone.utc).isoformat()}) + "\n")
        stages = _active_stages.setdefault(project, set())
        if active:
            stages.add(stage)
        else:
            stages.discard(stage)
_stage_timing_local = threading.local()
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

# Paths excluded from git growth accounting, in addition to DEFAULT_IGNORE and
# each plan's extra_ignore. These are tracked in git but are agent-coordination
# payloads and machine-written ledgers, not maintained project text: session
# transcripts, handoff dumps, task-packet corpora, bead JSONL state. Without
# this filter a repo that commits large agent handoff corpora reports absurd
# "code growth" (polylogue: ~1.0M tracked lines under .agent/handoffs alone
# pushed net_tracked_text_lines to 1.75M vs ~0.95M of real project text).
GROWTH_IGNORE: tuple[str, ...] = (
    ".agent/**",
    "**/.agent/**",
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
    env["DO_NOT_TRACK"] = "1"
    env["GIT_NO_LAZY_FETCH"] = "1"
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
    _print(
        "[dim]Planned projects (completion order may differ). Packages contain raw source, evidence tables, an offline index and archives.[/dim]"
    )
    for idx, plan in enumerate(plans, start=1):
        _print(
            f"  {idx}. {plan.name}: {len(plan.slices)} configured slices, "
            f"compressed={plan.compressed} -> {output_root / plan.name}"
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
    Slice(
        "packages-and-tooling",
        "Local package implementations, device tooling, tests, and documentation",
        (
            "pkgs/**",
            "devices/**",
            "browser-extensions/**",
            "tests/**",
            "docs/**",
            "assets/**",
            "eval/**",
            "justfile",
            "pyproject.toml",
        ),
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
        "Core library, archive, daemon, pipeline, storage, and source implementations",
        (
            "polylogue/**",
            "README.md",
        ),
        extra_ignore=(
            "polylogue/cli/**",
            "polylogue/mcp/**",
            "polylogue/operations/**",
            "polylogue/ui/**",
            "polylogue/rendering/**",
            "polylogue/site/**",
            "polylogue/showcase/**",
            "polylogue/templates/**",
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
            "webui/**",
            "browser-extension/**",
        ),
    ),
    Slice(
        "developer-tooling",
        "Developer tools, packaging, service definitions, and build configuration",
        (
            "devtools/**",
            "packaging/**",
            "contrib/**",
            "nix/**",
            "systemd/**",
            "pyproject.toml",
            "flake.nix",
            ".githooks/**",
            "justfile",
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
    "Sinity/sinity-lynchpin",
    Slice(
        "analysis-and-core",
        "Analysis modules, core primitives, config, control plane",
        (
            "lynchpin/analysis/**",
            "lynchpin/core/**",
            "lynchpin/*.py",
            "lynchpin/personal_evidence/**",
            "config/**",
            "README.md",
            "CLAUDE.md",
            "pyproject.toml",
        ),
    ),
    Slice("sources", "Read-only data source adapters", ("lynchpin/sources/**",)),
    Slice(
        "ingest-and-substrate",
        "Materializers, ingestion, and coherent substrate readers and writers",
        ("lynchpin/ingest/**", "lynchpin/materializers/**", "lynchpin/substrate/**"),
    ),
    Slice(
        "composite-graph-spine",
        "Evidence graph, context packs, semantic products",
        ("lynchpin/graph/**",),
    ),
    Slice(
        "cli-and-tooling",
        "CLI and MCP entrypoints, web surfaces, and tooling",
        (
            "lynchpin/cli/**",
            "lynchpin/mcp/**",
            "lynchpin/web/**",
            "lynchpin/static/**",
            "tool/**",
            "scripts/**",
            ".github/**",
            "justfile",
            "flake.nix",
        ),
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
    "/realm/archive/knowledgebase",
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
    wait_started = time.perf_counter()
    _repomix_semaphore.acquire()
    wait_elapsed = time.perf_counter() - wait_started
    timing = getattr(_stage_timing_local, "current", None)
    if timing is not None:
        timing["repomix_wait_s"] = round(wait_elapsed, 3)
    run_started = time.perf_counter()
    try:
        result = _run([repomix_bin, ".", *args], cwd=plan.path)
    finally:
        run_elapsed = time.perf_counter() - run_started
        if timing is not None:
            timing["repomix_run_s"] = round(run_elapsed, 3)
        _repomix_semaphore.release()
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


def _record_substage_duration(name: str, started: float) -> None:
    timing = getattr(_stage_timing_local, "current", None)
    if timing is None:
        return
    timings = timing.setdefault("substage_timings_s", {})
    timings[name] = round(time.perf_counter() - started, 3)


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


@lru_cache(maxsize=65_536)
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
    # Embedded-language blobs (notably fenced code in Markdown) belong to the
    # host document, not maintained source. Do not fold them into LOC totals.


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

    paths = (
        (plan.path / rel for rel in visible_paths if rel.endswith(".rs"))
        if visible_paths is not None
        else plan.path.rglob("*.rs")
    )
    for path in sorted(paths):
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

    paths = (
        (plan.path / rel for rel in visible_paths if rel.endswith(".rs"))
        if visible_paths is not None
        else plan.path.rglob("*.rs")
    )
    for path in sorted(paths):
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
