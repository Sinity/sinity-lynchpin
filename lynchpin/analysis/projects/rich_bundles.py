"""Richer project bundle generation with structural slices and git history shards."""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
import json
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from ...core.io import write_text_if_changed
from ...core.projects import project_profiles
from .bundles import (
    BUNDLE_ROOT,
    GitState,
    PROJECT_SPECS,
    ProjectSpec,
    LogFn,
    _noop,
    git_state,
    project_ignore_patterns,
    repomix_version,
    require_repomix,
    run_command,
    utc_now,
)

RICH_BUNDLE_ROOT = BUNDLE_ROOT / "rich"
RICH_INDEX_SCHEMA = "project-context-rich-bundle-index-v1"
RICH_MANIFEST_SCHEMA = "project-context-rich-bundle-v1"
DEFAULT_PATCH_WINDOW = 10
DEFAULT_SUMMARY_WINDOW = 100
DEFAULT_PATCH_COMMITS = 200
DEFAULT_RICH_PROJECTS = (
    "sinex",
    "sinnix",
    "sinity-lynchpin",
    "sinex-target-vision",
    "polylogue",
    "scribe-tap",
    "intercept-bounce",
)


@dataclass(frozen=True)
class SliceSpec:
    name: str
    description: str
    include: tuple[str, ...]


@dataclass(frozen=True)
class RichProjectPlan:
    spec: ProjectSpec
    slices: tuple[SliceSpec, ...]


@dataclass(frozen=True)
class SliceArtifact:
    name: str
    path: Path
    description: str
    include: tuple[str, ...]
    size_bytes: int
    command: list[str]


def _plan(name: str, *slices: SliceSpec) -> RichProjectPlan:
    return RichProjectPlan(spec=PROJECT_SPECS[name], slices=tuple(slices))


def _single_repo_plan(spec: ProjectSpec) -> RichProjectPlan:
    return RichProjectPlan(
        spec=spec,
        slices=(
            SliceSpec(
                "repo",
                "Full repository snapshot for smaller satellites.",
                ("**/*",),
            ),
        ),
    )


RICH_PROJECT_PLANS: dict[str, RichProjectPlan] = {
    "sinex": _plan(
        "sinex",
        SliceSpec(
            "runtime-and-libraries",
            "Top-level runtime, schemas, core crates, library crates, and shared configuration.",
            (
                "Cargo.toml",
                "README.md",
                "AGENTS.md",
                "src/**",
                "config/**",
                "schemas/**",
                "crate/core/**",
                "crate/lib/**",
            ),
        ),
        SliceSpec(
            "nodes-and-operators",
            "Deployable nodes, ingest paths, test harnesses, and operator-facing tooling.",
            (
                "crate/nodes/**",
                "crate/tests/**",
                "tests/**",
                "xtask/**",
                "scripts/**",
                ".github/**",
            ),
        ),
        SliceSpec(
            "nixos-and-deployment",
            "NixOS modules, service wiring, deployment examples, and runbook surfaces.",
            (
                "nixos/**",
                "flake.nix",
                "justfile",
                "xtask/docs/**",
                "config/verify/**",
            ),
        ),
    ),
    "sinnix": _plan(
        "sinnix",
        SliceSpec(
            "hosts-and-modules",
            "Host profiles, modules, flake composition, and shared Nix infrastructure.",
            (
                "flake.nix",
                "flake/**",
                "hosts/**",
                "modules/**",
            ),
        ),
        SliceSpec(
            "agent-and-tooling-dots",
            "Codex/Claude/Gemini control plane, editor/runtime dotfiles, and operator tooling.",
            (
                "CLAUDE.md",
                "README.md",
                "dots/**",
            ),
        ),
        SliceSpec(
            "scripts-and-assets",
            "Scripts, helper assets, and glue surfaces outside the Nix module tree.",
            (
                "assets/**",
                "scripts/**",
                ".github/**",
            ),
        ),
    ),
    "sinity-lynchpin": _plan(
        "sinity-lynchpin",
        SliceSpec(
            "analysis-and-project-surfaces",
            "Analysis core, project tooling, and context/control-plane surfaces.",
            (
                "README.md",
                "docs/reference/**",
                "lynchpin/analysis/**",
                "lynchpin/context/**",
                "lynchpin/core/**",
            ),
        ),
        SliceSpec(
            "ingest-and-evidence-planes",
            "Source adapters, ingest pipelines, evidence planes, and upstream data shaping.",
            (
                "lynchpin/ingest/**",
                "lynchpin/sources/**",
                "lynchpin/signals/**",
                "lynchpin/metrics/**",
            ),
        ),
        SliceSpec(
            "views-system-and-tests",
            "View materializers, system entrypoints, retrospective flows, docs, and tests.",
            (
                "docs/**",
                "lynchpin/retrospective/**",
                "lynchpin/system/**",
                "lynchpin/views/**",
                "scripts/**",
                "tests/**",
                "justfile",
                "pyproject.toml",
            ),
        ),
    ),
    "sinex-target-vision": _plan(
        "sinex-target-vision",
        SliceSpec(
            "canon-and-control-plane",
            "Maintained canon, repo control plane, and top-level routing surfaces.",
            (
                "AGENTS.md",
                "README.md",
                "canon/**",
            ),
        ),
        SliceSpec(
            "analysis-high-traffic",
            "High-traffic collations and synthesis surfaces used to answer current-state questions.",
            (
                "analysis/README.md",
                "analysis/collations/**",
            ),
        ),
        SliceSpec(
            "analysis-deep-design-and-meta",
            "Deep analysis, studies, design docs, and prompt/meta surfaces.",
            (
                "analysis/foundation/**",
                "analysis/domains/**",
                "analysis/studies/**",
                "design/**",
                "meta/**",
            ),
        ),
    ),
    "polylogue": _plan(
        "polylogue",
        SliceSpec(
            "core-library-and-storage",
            "Core archive library, storage backends, schemas, and source abstractions.",
            (
                "polylogue/lib/**",
                "polylogue/storage/**",
                "polylogue/schemas/**",
                "polylogue/sources/**",
                "README.md",
            ),
        ),
        SliceSpec(
            "cli-mcp-and-operations",
            "CLI, MCP, operational interfaces, automation helpers, and UI glue.",
            (
                "polylogue/cli/**",
                "polylogue/mcp/**",
                "polylogue/operations/**",
                "polylogue/ui/**",
                "scripts/**",
                ".github/**",
            ),
        ),
        SliceSpec(
            "rendering-site-and-qa",
            "Rendering/site surfaces, demos, docs, tests, and QA campaigns.",
            (
                "docs/**",
                "demos/**",
                "polylogue/rendering/**",
                "polylogue/site/**",
                "polylogue/showcase/**",
                "polylogue/templates/**",
                "qa/**",
                "tests/**",
            ),
        ),
    ),
}


def _default_rich_projects() -> list[str]:
    return [name for name in DEFAULT_RICH_PROJECTS if name in PROJECT_SPECS]


def _resolve_plan(name: str) -> RichProjectPlan:
    plan = RICH_PROJECT_PLANS.get(name)
    if plan is not None:
        return plan
    spec = PROJECT_SPECS[name]
    return _single_repo_plan(spec)


def select_rich_projects(requested: Sequence[str] | None) -> list[RichProjectPlan]:
    names = list(requested) if requested else _default_rich_projects()
    unknown = [name for name in names if name not in PROJECT_SPECS]
    if unknown:
        available = ", ".join(sorted(PROJECT_SPECS))
        raise ValueError(f"unknown projects: {', '.join(unknown)}; available: {available}")
    return [_resolve_plan(name) for name in names]


def _slice_header(
    *,
    plan: RichProjectPlan,
    git: GitState,
    generated_at: str,
    slice_spec: SliceSpec,
) -> str:
    return "\n".join(
        (
            f"Project: {plan.spec.name}",
            f"Source path: {plan.spec.path}",
            f"Generated at: {generated_at}",
            f"Slice: {slice_spec.name}",
            f"Slice description: {slice_spec.description}",
            f"Git branch: {git.branch}",
            f"Git commit: {git.commit}",
            f"Dirty worktree: {'yes' if git.dirty else 'no'}",
            f"Include patterns: {', '.join(slice_spec.include)}",
            "Generated by lynchpin.analysis.projects.rich_bundles via repomix.",
        )
    )


def _build_slice_command(
    *,
    repomix_bin: str,
    output_path: Path,
    plan: RichProjectPlan,
    git: GitState,
    generated_at: str,
    slice_spec: SliceSpec,
) -> list[str]:
    return [
        repomix_bin,
        ".",
        "--quiet",
        "--no-security-check",
        "--style",
        "markdown",
        "--parsable-style",
        "--no-git-sort-by-changes",
        "--include-full-directory-structure",
        "--output-show-line-numbers",
        "--header-text",
        _slice_header(
            plan=plan,
            git=git,
            generated_at=generated_at,
            slice_spec=slice_spec,
        ),
        "--ignore",
        project_ignore_patterns(plan.spec),
        "--include",
        ",".join(slice_spec.include),
        "--output",
        str(output_path),
    ]


def _run_repomix_slice(
    *,
    repomix_bin: str,
    output_path: Path,
    plan: RichProjectPlan,
    git: GitState,
    generated_at: str,
    slice_spec: SliceSpec,
) -> SliceArtifact:
    command = _build_slice_command(
        repomix_bin=repomix_bin,
        output_path=output_path,
        plan=plan,
        git=git,
        generated_at=generated_at,
        slice_spec=slice_spec,
    )
    result = run_command(command, cwd=plan.spec.path)
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "repomix failed").strip()
        raise RuntimeError(f"{plan.spec.name}/{slice_spec.name}: {details}")
    if not output_path.exists():
        raise RuntimeError(
            f"{plan.spec.name}/{slice_spec.name}: expected slice output was not written: {output_path}"
        )
    return SliceArtifact(
        name=output_path.name,
        path=output_path,
        description=slice_spec.description,
        include=slice_spec.include,
        size_bytes=output_path.stat().st_size,
        command=command,
    )


def _git_commits(repo: Path) -> list[str]:
    result = run_command(["git", "rev-list", "--reverse", "HEAD"], cwd=repo)
    if result.returncode != 0:
        raise RuntimeError(f"failed to enumerate git history for {repo}")
    return [line for line in result.stdout.splitlines() if line.strip()]


def _select_history_commits(commit_shas: Sequence[str], limit: int | None) -> list[str]:
    if limit is None or limit <= 0 or limit >= len(commit_shas):
        return list(commit_shas)
    return list(commit_shas[-limit:])


def _render_history_markdown(
    *,
    plan: RichProjectPlan,
    kind: str,
    description: str,
    generated_at: str,
    window_index: int,
    total_windows: int,
    commits: Sequence[str],
    git_output: str,
) -> str:
    header = [
        f"# {plan.spec.name} {kind} window {window_index:04d}/{total_windows:04d}",
        "",
        f"- Generated: `{generated_at}`",
        f"- Source: `{plan.spec.path}`",
        f"- Window commits: `{len(commits)}`",
        f"- First commit: `{commits[0]}`",
        f"- Last commit: `{commits[-1]}`",
        f"- Description: {description}",
        "",
        "```text",
        git_output.rstrip(),
        "```",
        "",
    ]
    return "\n".join(header)


def _git_log_for_commits(repo: Path, commits: Sequence[str], *, include_patches: bool) -> str:
    command = [
        "git",
        "log",
        "--reverse",
        "--date=iso-strict",
        "--format=medium",
        "--summary",
        "--stat",
        "--stdin",
    ]
    if include_patches:
        command.insert(-1, "-p")
    result = subprocess.run(
        command,
        cwd=str(repo),
        text=True,
        input="".join(f"{commit}\n" for commit in commits),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "git log failed").strip()
        raise RuntimeError(f"{repo}: {details}")
    return result.stdout


def _write_history_shards(
    *,
    plan: RichProjectPlan,
    temp_dir: Path,
    generated_at: str,
    commit_shas: Sequence[str],
    window: int,
    include_patches: bool,
) -> list[dict[str, object]]:
    if window <= 0:
        raise ValueError("history shard window must be positive")
    if not commit_shas:
        return []
    kind = "patches" if include_patches else "summary"
    dirname = f"history-{kind}-{window}"
    history_dir = temp_dir / dirname
    history_dir.mkdir(parents=True, exist_ok=True)
    total_windows = (len(commit_shas) + window - 1) // window
    artefacts: list[dict[str, object]] = []
    description = (
        f"Full patch history shard with {window}-commit windows."
        if include_patches
        else f"Git summary/stat shard with {window}-commit windows."
    )
    for offset in range(0, len(commit_shas), window):
        shard_commits = commit_shas[offset : offset + window]
        window_index = offset // window + 1
        shard_path = history_dir / f"{window_index:04d}.md"
        git_output = _git_log_for_commits(
            plan.spec.path,
            shard_commits,
            include_patches=include_patches,
        )
        write_text_if_changed(
            shard_path,
            _render_history_markdown(
                plan=plan,
                kind=kind,
                description=description,
                generated_at=generated_at,
                window_index=window_index,
                total_windows=total_windows,
                commits=shard_commits,
                git_output=git_output,
            ),
        )
        artefacts.append(
            {
                "directory": dirname,
                "kind": kind,
                "window": window,
                "window_index": window_index,
                "path": str(shard_path),
                "commit_count": len(shard_commits),
                "first_commit": shard_commits[0],
                "last_commit": shard_commits[-1],
                "size_bytes": shard_path.stat().st_size,
            }
        )
    return artefacts


def _tracked_files(spec: ProjectSpec) -> list[str]:
    result = run_command(["git", "ls-files", "-z"], cwd=spec.path)
    if result.returncode != 0:
        raise RuntimeError(f"failed to list tracked files for {spec.path}")
    return [path for path in result.stdout.split("\0") if path]


def _inventory_summary(spec: ProjectSpec) -> dict[str, object]:
    tracked = _tracked_files(spec)
    profiles = project_profiles()
    classifier = profiles.get(spec.name).classify if spec.name in profiles else None
    category_counts: Counter[str] = Counter()
    extension_counts: Counter[str] = Counter()
    top_dirs: Counter[str] = Counter()

    for path in tracked:
        category = classifier(path) if classifier else None
        category_counts[category or "other"] += 1
        suffix = Path(path).suffix.lower() or "<none>"
        extension_counts[suffix] += 1
        first = Path(path).parts[0] if Path(path).parts else "<root>"
        top_dirs[first] += 1

    return {
        "tracked_file_count": len(tracked),
        "categories": dict(sorted(category_counts.items())),
        "top_extensions": extension_counts.most_common(10),
        "top_directories": top_dirs.most_common(10),
    }


def _render_overview(
    *,
    plan: RichProjectPlan,
    git: GitState,
    generated_at: str,
    repomix_build: str,
    inventory: dict[str, object],
    commit_count: int,
    patch_commit_count: int,
    summary_commit_count: int,
    patch_shard_count: int,
    summary_shard_count: int,
) -> str:
    lines = [
        f"# {plan.spec.name} rich bundle overview",
        "",
        f"- Source: `{plan.spec.path}`",
        f"- Generated: `{generated_at}`",
        f"- Branch: `{git.branch}`",
        f"- Commit: `{git.commit}`",
        f"- Dirty worktree: `{git.dirty}`",
        f"- Repomix: `{repomix_build}`",
        f"- Tracked files: `{inventory['tracked_file_count']}`",
        f"- Git commits: `{commit_count}`",
        f"- Patch-history coverage: `{patch_commit_count}` recent commits",
        f"- Summary-history coverage: `{summary_commit_count}` commits",
        f"- Patch history shards: `{patch_shard_count}`",
        f"- Summary history shards: `{summary_shard_count}`",
        "",
        "## Slice Plan",
    ]
    for slice_spec in plan.slices:
        lines.extend(
            [
                f"### {slice_spec.name}",
                f"- Description: {slice_spec.description}",
                f"- Include: `{', '.join(slice_spec.include)}`",
                "",
            ]
        )
    lines.append("## Inventory Categories")
    for category, count in inventory["categories"].items():
        lines.append(f"- `{category}`: `{count}`")
    lines.extend(["", "## Top Extensions"])
    for ext, count in inventory["top_extensions"]:
        lines.append(f"- `{ext}`: `{count}`")
    lines.extend(["", "## Top Directories"])
    for directory, count in inventory["top_directories"]:
        lines.append(f"- `{directory}`: `{count}`")
    lines.append("")
    return "\n".join(lines)


def _render_project_readme(manifest: dict[str, object]) -> str:
    lines = [
        f"# {manifest['project']}",
        "",
        f"- Source: `{manifest['source_path']}`",
        f"- Generated: `{manifest['generated_at']}`",
        f"- Branch: `{manifest['git']['branch']}`",
        f"- Commit: `{manifest['git']['commit']}`",
        f"- Dirty worktree: `{manifest['git']['dirty']}`",
        "",
        "## Files",
    ]
    lines.append("- `overview.md`: structural summary and slice plan")
    for output in manifest["slice_outputs"]:
        lines.append(f"- `{output['name']}`: slice bundle for `{output['slice']}`")
    lines.append(
        f"- `history-patches-{manifest['history']['patch_window']}/`: "
        f"{manifest['history']['patch_shard_count']} patch shards "
        f"covering {manifest['history']['patch_commit_count']} recent commits"
    )
    lines.append(
        f"- `history-summary-{manifest['history']['summary_window']}/`: "
        f"{manifest['history']['summary_shard_count']} summary shards "
        f"covering {manifest['history']['summary_commit_count']} commits"
    )
    return "\n".join(lines) + "\n"


def _render_root_readme(index: dict[str, object]) -> str:
    lines = [
        "# Rich Project Bundles",
        "",
        f"- Generated: `{index['generated_at']}`",
        f"- Output root: `{index['output_root']}`",
        f"- Repomix: `{index['repomix_version']}`",
        "",
        "## Projects",
    ]
    for project in index["projects"]:
        lines.append(
            f"- `{project['project']}`: `{project['status']}` "
            f"({len(project.get('slice_outputs', []))} slices, "
            f"{project.get('history', {}).get('commit_count', 0)} commits)"
        )
    return "\n".join(lines) + "\n"


def generate_rich_project_bundle(
    *,
    plan: RichProjectPlan,
    output_root: Path,
    repomix_bin: str,
    repomix_build: str,
    patch_window: int,
    summary_window: int,
    patch_commits: int | None,
    summary_commits: int | None,
) -> dict[str, object]:
    if not plan.spec.path.exists():
        return {
            "project": plan.spec.name,
            "source_path": str(plan.spec.path),
            "status": "missing",
        }

    output_root.mkdir(parents=True, exist_ok=True)
    generated_at = utc_now()
    git = git_state(plan.spec.path)
    inventory = _inventory_summary(plan.spec)
    commit_shas = _git_commits(plan.spec.path)
    patch_commit_shas = _select_history_commits(commit_shas, patch_commits)
    summary_commit_shas = _select_history_commits(commit_shas, summary_commits)
    temp_dir = Path(tempfile.mkdtemp(prefix=f".{plan.spec.name}-", dir=str(output_root)))
    target_dir = output_root / plan.spec.name

    try:
        slice_outputs: list[SliceArtifact] = []
        for slice_spec in plan.slices:
            slice_outputs.append(
                _run_repomix_slice(
                    repomix_bin=repomix_bin,
                    output_path=temp_dir / f"slice-{slice_spec.name}.md",
                    plan=plan,
                    git=git,
                    generated_at=generated_at,
                    slice_spec=slice_spec,
                )
            )

        patch_history = _write_history_shards(
            plan=plan,
            temp_dir=temp_dir,
            generated_at=generated_at,
            commit_shas=patch_commit_shas,
            window=patch_window,
            include_patches=True,
        )
        summary_history = _write_history_shards(
            plan=plan,
            temp_dir=temp_dir,
            generated_at=generated_at,
            commit_shas=summary_commit_shas,
            window=summary_window,
            include_patches=False,
        )

        write_text_if_changed(
            temp_dir / "overview.md",
            _render_overview(
                plan=plan,
                git=git,
                generated_at=generated_at,
                repomix_build=repomix_build,
                inventory=inventory,
                commit_count=len(commit_shas),
                patch_commit_count=len(patch_commit_shas),
                summary_commit_count=len(summary_commit_shas),
                patch_shard_count=len(patch_history),
                summary_shard_count=len(summary_history),
            ),
        )

        manifest = {
            "schema_generation": RICH_MANIFEST_SCHEMA,
            "project": plan.spec.name,
            "source_path": str(plan.spec.path),
            "output_root": str(target_dir),
            "bundle_dir": str(target_dir),
            "generated_at": generated_at,
            "status": "generated",
            "git": asdict(git),
            "repomix_version": repomix_build,
            "inventory": inventory,
            "slice_plan": [
                {
                    "name": slice_spec.name,
                    "description": slice_spec.description,
                    "include": list(slice_spec.include),
                }
                for slice_spec in plan.slices
            ],
            "slice_outputs": [
                {
                    "name": output.name,
                    "slice": output.name.removeprefix("slice-").removesuffix(".md"),
                    "description": output.description,
                    "path": str(target_dir / output.name),
                    "size_bytes": output.size_bytes,
                    "include": list(output.include),
                    "command": shlex.join(
                        [
                            str(target_dir / output.name) if part == str(output.path) else part
                            for part in output.command
                        ]
                    ),
                }
                for output in slice_outputs
            ],
            "history": {
                "commit_count": len(commit_shas),
                "patch_commit_count": len(patch_commit_shas),
                "patch_window": patch_window,
                "patch_shard_count": len(patch_history),
                "patch_shards": patch_history,
                "summary_commit_count": len(summary_commit_shas),
                "summary_window": summary_window,
                "summary_shard_count": len(summary_history),
                "summary_shards": summary_history,
            },
        }
        write_text_if_changed(
            temp_dir / "manifest.json",
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        )
        write_text_if_changed(temp_dir / "README.md", _render_project_readme(manifest))

        if target_dir.exists():
            shutil.rmtree(target_dir)
        temp_dir.rename(target_dir)
        return manifest
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)


def build_rich_project_bundles(
    *,
    project_names: Sequence[str] | None = None,
    output_root: Path = RICH_BUNDLE_ROOT,
    patch_window: int = DEFAULT_PATCH_WINDOW,
    summary_window: int = DEFAULT_SUMMARY_WINDOW,
    patch_commits: int | None = DEFAULT_PATCH_COMMITS,
    summary_commits: int | None = None,
    log: LogFn | None = None,
) -> dict[str, object]:
    if log is None:
        log = _noop
    if patch_window <= 0 or summary_window <= 0:
        raise ValueError("history shard windows must be positive")
    repomix_bin = require_repomix()
    repomix_build = repomix_version(repomix_bin)
    selected = select_rich_projects(project_names)
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    manifests: list[dict[str, object]] = []
    for plan in selected:
        manifest = generate_rich_project_bundle(
            plan=plan,
            output_root=output_root,
            repomix_bin=repomix_bin,
            repomix_build=repomix_build,
            patch_window=patch_window,
            summary_window=summary_window,
            patch_commits=patch_commits,
            summary_commits=summary_commits,
        )
        manifests.append(manifest)
        log(f"{plan.spec.name}: {manifest['status']}")

    index = {
        "schema_generation": RICH_INDEX_SCHEMA,
        "generated_at": utc_now(),
        "output_root": str(output_root),
        "repomix_version": repomix_build,
        "projects": manifests,
    }
    write_text_if_changed(output_root / "index.json", json.dumps(index, indent=2, sort_keys=True) + "\n")
    write_text_if_changed(output_root / "README.md", _render_root_readme(index))
    return index
