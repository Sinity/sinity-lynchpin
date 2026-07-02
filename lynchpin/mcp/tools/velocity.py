"""Velocity MCP tools: time-series, narratives, symbol churn, temporal rhythm.

NOTE: do NOT add ``from __future__ import annotations`` here.
FastMCP inspects annotations at decoration time and cannot handle postponed
string annotations for tool parameters.
"""

from typing import Any

from lynchpin.mcp.tools._utils import (
    best_materialized_refresh_id,
    ensure_substrate_materialized_for_read,
    half_open_date_window,
    json_safe as _json_safe,
    pinned_materialization_for_read,
)


# ── D.4 Velocity Series ──────────────────────────────────────────────────────


def velocity_series(
    projects: list[str] | None = None,
    refresh_id: str | None = None,
    window_days: int = 7,
) -> list[dict[str, Any]]:
    """Project velocity time-series with rolling windows (Arc D.4).

    SQL window functions over project_day_correlation. Returns daily commit
    counts with rolling average and cumulative count per project.

    Parameters:
        projects:     filter to specific projects; None = all.
        refresh_id:   materialized substrate snapshot to query; default = best current snapshot.
        window_days:  rolling-average window size (default 7).

    Returns:
        [{"project": str, "date": "YYYY-MM-DD", "commit_count": int,
          "rolling_avg": float, "cumulative": int, "source_count": int}]
    """
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.readers_velocity import load_velocity_series

    projs: tuple[str, ...] | None = tuple(projects) if projects else None
    if refresh_id is None:
        ensure_substrate_materialized_for_read(caller="velocity_series")
    path = substrate_path()
    with connect(path, read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_materialized_refresh_id(conn, "project_day_correlation", caller="velocity_series")
            if refresh_id is None:
                return []

        rows = load_velocity_series(
            conn,
            refresh_id=refresh_id,
            window_days=window_days,
            projects=projs,
        )

    cols = ["project", "date", "commit_count", "rolling_avg", "cumulative", "source_count"]
    return [
        {c: _json_safe(v) for c, v in zip(cols, row)}
        for row in rows
    ]


# ── E.3 Gap Draft ────────────────────────────────────────────────────────────



# ── M.6 Velocity Narrative ───────────────────────────────────────────────────


def velocity_narrative(
    projects: list[str] | None = None,
    refresh_id: str | None = None,
) -> dict[str, Any]:
    """Auto-summary of project velocity over the latest materialized window (Arc M.6).

    Aggregates project_day_correlation into a narrative summary: total
    commits, active days, peak day, per-project breakdown, and the
    dominant project. Renders as structured text suitable for inclusion
    in a context pack or seed note.

    Parameters:
        projects:   filter to specific projects; None = top 8 by commits.
        refresh_id: materialized substrate snapshot (default: best current snapshot).

    Returns:
        {
            "window": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
            "total_commits": int,
            "total_active_days": int,
            "peak": {"project": str, "date": "YYYY-MM-DD", "commits": int},
            "projects": [{"project": str, "commits": int, "active_days": int}],
            "summary_text": str,
        }
    """
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.readers_velocity import (
        load_velocity_window,
        load_velocity_project_summary,
        load_velocity_peak,
    )

    projs: tuple[str, ...] | None = tuple(projects) if projects else None
    materialization = (
        ensure_substrate_materialized_for_read(caller="velocity_narrative")
        if refresh_id is None
        else pinned_materialization_for_read(caller="velocity_narrative", refresh_id=refresh_id)
    )
    path = substrate_path()
    with connect(path, read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_materialized_refresh_id(conn, "project_day_correlation", caller="velocity_narrative")
            if refresh_id is None:
                return {
                    "error": "no materialized substrate snapshot",
                    "materialization": materialization,
                }

        # Window bounds
        win = load_velocity_window(conn, refresh_id=refresh_id)
        proj_rows = load_velocity_project_summary(conn, refresh_id=refresh_id, projects=projs)
        peak = load_velocity_peak(conn, refresh_id=refresh_id, projects=projs)

        total_commits = sum(r[1] for r in proj_rows)
        total_days = sum(r[2] for r in proj_rows)

        projects_list = [
            {"project": r[0], "commits": r[1],
             "active_days": r[2], "avg_daily": r[3]}
            for r in proj_rows
        ]

        # Build narrative text
        if proj_rows:
            top = proj_rows[0]
            lines = [
                f"In the window {win[0]} → {win[1]}: "
                f"{total_commits} commits across {len(proj_rows)} projects "
                f"({total_days} active project-days).",
                "",
                f"**{top[0]}** led with {top[1]} commits over "
                f"{top[2]} active days (avg {top[3]}/day).",
            ]
            if peak:
                lines.append(
                    f"Peak day: **{peak[0]}** on {peak[1]} "
                    f"({peak[2]} commits)."
                )
            if len(proj_rows) > 1:
                rest = [f"**{p[0]}** ({p[1]} commits)" for p in proj_rows[1:4]]
                lines.append(
                    f"Also active: {', '.join(rest)}."
                )
            if len(proj_rows) > 4:
                lines.append(
                    f"(+{len(proj_rows) - 4} more projects with lower activity)"
                )
            summary = "\n".join(lines)
        else:
            summary = "No project activity in this window."

    return {
        "materialized_refresh_id": refresh_id,
        "refresh_id": refresh_id,
        "materialization": materialization,
        "window": {"start": _json_safe(win[0]), "end": _json_safe(win[1])},
        "total_commits": total_commits,
        "total_active_days": total_days,
        "peak": {
            "project": peak[0], "date": _json_safe(peak[1]),
            "commits": peak[2],
        } if peak else None,
        "projects": projects_list,
        "summary_text": summary,
    }


# ── A.1 D.3 WorkPackageDurability ────────────────────────────────────────────



# ══════════════════════════════════════════════════════════════════════════════
# Phase B — New capabilities from the complete substrate
# ══════════════════════════════════════════════════════════════════════════════


# ── B.1 Symbol-Level Velocity ────────────────────────────────────────────────


def symbol_velocity(
    projects: list[str] | None = None,
    refresh_id: str | None = None,
) -> list[dict[str, Any]]:
    """Symbol-level churn per project per day (Phase B.1).

    Extends commit-count velocity with symbol-change dimensions: added,
    deleted, modified, renamed per project-day. Joins symbol_change to
    project_day_correlation for a unified velocity surface.

    Parameters:
        projects:   filter to specific projects; None = all.
        refresh_id: materialized substrate snapshot (default: best symbol_change coverage).

    Returns:
        [{"project": str, "date": str, "commit_count": int,
          "symbols_added": int, "symbols_modified": int,
          "symbols_renamed": int, "symbols_total": int}]
    """
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.readers_velocity import load_symbol_velocity_rows

    projs: tuple[str, ...] | None = tuple(projects) if projects else None

    if refresh_id is None:
        ensure_substrate_materialized_for_read(caller="symbol_velocity")
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_materialized_refresh_id(
                conn,
                "symbol_change",
                caller="symbol_velocity",
            )
            if refresh_id is None:
                return []

        rows = load_symbol_velocity_rows(conn, refresh_id=refresh_id, projects=projs)

    return [
        {"project": r[0], "date": _json_safe(r[1]),
         "materialized_refresh_id": refresh_id,
         "commit_count": r[2], "symbols_added": r[3],
         "symbols_modified": r[4], "symbols_renamed": r[5],
         "symbols_total": r[6]}
        for r in rows
    ]


# ── B.2 File Hotspot Detection ───────────────────────────────────────────────



# ── B.3 Temporal Rhythm Analysis ─────────────────────────────────────────────


def temporal_rhythm(
    project: str | None = None,
    refresh_id: str | None = None,
) -> dict[str, Any]:
    """Commit time-of-day × day-of-week patterns per project (Phase B.3).

    Groups commit_fact by hour and weekday to surface work rhythms:
    morning vs night coding, weekend vs weekday sprints.

    Parameters:
        project:    filter to one project; None = all.
        refresh_id: materialized substrate snapshot (default: best current snapshot).

    Returns:
        {
            "hourly": [{"hour": 0-23, "count": int}],
            "weekday": [{"weekday": 0-6, "name": "Mon", "count": int}],
            "peak_hour": int, "peak_weekday": str,
        }
    """
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.readers_velocity import (
        load_commit_hourly_distribution,
        load_commit_weekday_distribution,
    )

    materialization = (
        ensure_substrate_materialized_for_read(caller="temporal_rhythm")
        if refresh_id is None
        else pinned_materialization_for_read(caller="temporal_rhythm", refresh_id=refresh_id)
    )
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_materialized_refresh_id(conn, "commit_fact", caller="temporal_rhythm")
            if refresh_id is None:
                return {
                    "materialized_refresh_id": None,
                    "refresh_id": None,
                    "materialization": materialization,
                    "hourly": [],
                    "weekday": [],
                    "peak_hour": None,
                    "peak_weekday": None,
                }

        hourly = load_commit_hourly_distribution(conn, refresh_id=refresh_id, project=project)
        weekday = load_commit_weekday_distribution(conn, refresh_id=refresh_id, project=project)

    weekday_names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    peak_hour = max(hourly, key=lambda r: r[1])[0] if hourly else None
    peak_dow = max(weekday, key=lambda r: r[1]) if weekday else None

    return {
        "materialized_refresh_id": refresh_id,
        "refresh_id": refresh_id,
        "materialization": materialization,
        "hourly": [{"hour": r[0], "count": r[1]} for r in hourly],
        "weekday": [{"weekday": r[0], "name": weekday_names[r[0]], "count": r[1]}
                     for r in weekday],
        "peak_hour": peak_hour,
        "peak_weekday": weekday_names[peak_dow[0]] if peak_dow else None,
    }


# ── B.4 Evidence Confidence Tiering ──────────────────────────────────────────


# Path patterns that are NOT real-engineering code: lockfiles, snapshots,
# generated artifacts, fixture data, minified bundles. Used to compute
# `lines_added_clean` in engineering_throughput.
_NON_CODE_PATH_PATTERNS = (
    "Cargo.lock",
    "flake.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "uv.lock",
    "poetry.lock",
    "Pipfile.lock",
    ".snap",
    "/fixtures/",
    "/__snapshots__/",
    "/generated/",
    "/.lynchpin/generated/",
    "ai_activity.json",
    "focus_timeline.json",
    "narrative_window.json",
    ".min.js",
    ".min.css",
)


# ── File classification (source / test / config / doc / other) ────────────────
# Each project gets its own rules because codebase layout differs.
# These classify paths at the project-root-relative level; the rules
# MUST handle historical re-organisations by matching broadly on
# well-known directory names rather than exact prefixes.


def _classify_path(project: str, path: str) -> str:
    """Classify a project-relative path as source|test|config|doc|other."""
    if not path:
        return "other"

    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""

    # ── documentation: markdown, .agent/, .claude/ docs ──────────────────
    if ext in ("md", "rst", "txt"):
        return "doc"
    if any(p in path for p in ("/docs/", "/.agent/", "/.claude/", "README", "CHANGELOG", "AGENTS")):
        return "doc"

    # ── per-project rules ────────────────────────────────────────────────
    if project == "sinex":
        return _classify_sinex(path, ext)
    elif project == "polylogue":
        return _classify_polylogue(path, ext)
    elif project == "sinnix":
        return _classify_sinnix(path, ext)
    elif project == "sinity-lynchpin":
        return _classify_lynchpin(path, ext)
    else:
        return _classify_generic(path, ext)


def _classify_sinex(path: str, ext: str) -> str:
    # Rust source (including xtask, the build tooling)
    if any(p in path for p in ("/src/", "xtask/", "crate/")):
        # Test code within source trees
        if any(p in path for p in ("/tests/", "/test/", "test_", "_test.rs")):
            return "test"
        return "source"
    # Standalone test directories
    if any(path.startswith(p) for p in ("integration/", "e2e/", "unit/", "property/",
                                         "adversarial/", "spec/", "common/")):
        return "test"
    # Config
    if ext in ("nix", "toml", "json", "yaml", "yml", "lock", "cfg"):
        return "config"
    if any(p in path for p in ("/nixos/", "/.github/", "/schemas/", "/migrations/",
                                "flake.", "Cargo.toml", "Cargo.lock", "rust-toolchain")):
        return "config"
    return "other"


def _classify_polylogue(path: str, ext: str) -> str:
    # Python source
    if path.startswith("polylogue/"):
        if "test" in path.lower() or path.endswith("_test.py"):
            return "test"
        return "source"
    # Standalone test directories
    if any(path.startswith(p) for p in ("unit/", "integration/", "benchmarks/", "fuzz/")):
        return "test"
    if path.endswith("conftest.py") or "_test" in path:
        return "test"
    # Config
    if ext in ("toml", "lock", "nix", "cfg", "ini"):
        return "config"
    if any(p in path for p in ("/nix/", "/.github/", "/devtools/", "/infra/",
                                "pyproject.toml", "flake.", "uv.lock", "poetry.lock")):
        return "config"
    return "other"


def _classify_sinnix(path: str, ext: str) -> str:
    # Nix modules and dotfiles are the "source" of a config repo
    if any(path.startswith(p) for p in ("modules/", "hosts/", "dots/", "pkgs/", "scripts/", "nixos/")):
        return "source"
    if ext == "nix":
        return "source"
    # Secrets and deployment config
    if any(p in path for p in ("/secret", "/secrets/", "/.github/", "flake.", ".age")):
        return "config"
    if ext in ("json", "yaml", "yml", "toml", "lock", "age"):
        return "config"
    return "other"


def _classify_lynchpin(path: str, ext: str) -> str:
    # Python source (lynchpin package)
    if "lynchpin/" in path:
        if "test" in path.lower():
            return "test"
        return "source"
    # Standalone test directory
    if path.startswith("tests/"):
        return "test"
    # Config
    if ext in ("toml", "lock", "nix", "cfg"):
        return "config"
    if any(p in path for p in ("pyproject.toml", "justfile", "flake.", "/.github/")):
        return "config"
    # External data
    if path.startswith("external/"):
        return "other"
    return "other"


def _classify_generic(path: str, ext: str) -> str:
    if ext in ("rs", "py", "js", "ts", "go", "c", "cpp", "h", "java", "kt", "swift"):
        if "test" in path.lower() or path.endswith("_test." + ext):
            return "test"
        return "source"
    if ext in ("md", "rst", "txt"):
        return "doc"
    if ext in ("toml", "yaml", "yml", "json", "nix", "lock", "cfg", "ini"):
        return "config"
    return "other"


def _crate_from_path(project: str, path: str) -> str | None:
    """Extract Rust crate name from a path, or None for non-Rust/non-crate."""
    if project != "sinex":
        return None
    # crate/<name>/... or xtask/...
    if path.startswith("crate/"):
        parts = path.split("/")
        if len(parts) >= 2:
            return parts[1]
    if path.startswith("xtask/"):
        return "xtask"
    return None


def _is_non_code_path(path: str) -> bool:
    """True iff a path matches any non-code pattern (lockfiles/snapshots/etc.)."""
    if not path:
        return False
    return any(pattern in path for pattern in _NON_CODE_PATH_PATTERNS)


def engineering_throughput(
    project: str,
    start: str | None = None,
    end: str | None = None,
    granularity: str = "week",
    refresh_id: str | None = None,
    grouping: str = "raw",
    category: str | None = None,
) -> dict[str, Any]:
    """Decomposed engineering-throughput estimate for a project window.

    Composes commit_fact + file_change_fact + symbol_change so that the
    "is project X actually accelerating, or just committing more
    granularly?" question can be answered from one tool rather than four
    silo'd ones (velocity_series, symbol_velocity, file_hotspots,
    commit_kind_attribution).

    Parameters:
        project:     canonical project name (required — windowing across
                     all projects is the existing velocity_series).
        start, end:  ISO dates; default = full window of the snapshot.
        granularity: "day" | "week" | "month". Aggregation period.
        refresh_id:  materialized substrate snapshot. Defaults to best current commit_fact build.
        grouping:    "raw" (per-commit, default) or "pr" (group commits
                     by PR number extracted from subject, falling back to
                     sha for commits without ``(#N)``).  "pr" normalises
                     across the March 2026 workflow regime change so that
                     atomic-commit months are comparable with squash-merge
                     months.
        category:    Filter source/test/config/doc/other breakdown to a
                     single category.  None = all paths (default).

    Returns rows shaped:
        {
            "project": str,
            "granularity": str,
            "materialized_refresh_id": str | None,
            "refresh_id": str | None,
            "degraded": bool,
            "reason": str | None,
            "substrate_window": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
            "periods": [
                {
                    "period_start", "commit_count", "files_changed",
                    "lines_added", "lines_deleted",
                    "lines_added_clean", "lines_deleted_clean",
                    "symbols_added", "symbols_modified", "symbols_renamed",
                    "symbols_total",
                    "mean_lines_per_commit_clean", "granularity_index",
                },
                ...
            ],
        }
    """
    from datetime import date as _d

    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.readers_velocity import (
        load_best_coverage_refresh_id,
        load_commit_fact_project_count,
        load_commit_fact_window_bounds,
        load_commit_throughput_by_period,
        load_file_change_by_period,
        load_symbol_change_by_period,
    )

    if granularity not in ("day", "week", "month"):
        return {
            "project": project, "granularity": granularity,
            "materialized_refresh_id": None,
            "refresh_id": None, "degraded": True,
            "reason": f"unsupported granularity {granularity!r} (use day, week, or month)",
            "substrate_window": None, "periods": [],
        }

    if grouping not in ("raw", "pr"):
        return {
            "project": project, "granularity": granularity,
            "materialized_refresh_id": None,
            "refresh_id": None, "degraded": True,
            "reason": f"unsupported grouping {grouping!r} (use raw or pr)",
            "substrate_window": None, "periods": [],
        }

    if category is not None and category not in ("source", "test", "config", "doc", "other"):
        return {
            "project": project, "granularity": granularity,
            "materialized_refresh_id": None,
            "refresh_id": None, "degraded": True,
            "reason": f"unsupported category {category!r} (use source, test, config, doc, or other)",
            "substrate_window": None, "periods": [],
        }

    start_d: _d | None = _d.fromisoformat(start) if start else None
    end_d: _d | None = _d.fromisoformat(end) if end else None
    window = half_open_date_window(start_d, end_d)
    materialization = (
        ensure_substrate_materialized_for_read(
            caller="engineering_throughput",
            window=window,
        )
        if refresh_id is None
        else pinned_materialization_for_read(caller="engineering_throughput", refresh_id=refresh_id)
    )

    path = substrate_path()
    with connect(path, read_only=True) as conn:
        if refresh_id is None:
            # Pick the refresh that has the richest *combined* coverage of
            # commit_fact + file_change_fact for this project. Picking the
            # refresh with most commit rows alone backfires when that
            # refresh's file_change_fact has narrow project scope (e.g.
            # current-state:2024-09-01 only covers intercept-bounce +
            # knowledgebase, not sinex/sinnix/sinity-lynchpin).
            refresh_id = load_best_coverage_refresh_id(conn, project=project)
            if refresh_id is None:
                refresh_id = best_materialized_refresh_id(conn, "commit_fact", caller="project_file_change_profile")

        if refresh_id is None:
            return {
                "project": project, "granularity": granularity,
                "materialized_refresh_id": None,
                "refresh_id": None,
                "materialization": materialization,
                "degraded": True,
                "reason": "no commit_fact promote runs found",
                "substrate_window": None, "periods": [],
            }

        bounds = load_commit_fact_window_bounds(conn, refresh_id=refresh_id)
        substrate_window = {
            "start": _json_safe(bounds[0]),
            "end": _json_safe(bounds[1]),
        }

        proj_count = load_commit_fact_project_count(conn, refresh_id=refresh_id, project=project)
        if proj_count == 0:
            return {
                "project": project, "granularity": granularity,
                "materialized_refresh_id": refresh_id,
                "refresh_id": refresh_id,
                "materialization": materialization,
                "degraded": True,
                "reason": f"no commit_fact rows for project {project!r} in this snapshot",
                "substrate_window": substrate_window, "periods": [],
            }

        commit_rows = {
            r[0]: r
            for r in load_commit_throughput_by_period(
                conn,
                refresh_id=refresh_id,
                project=project,
                granularity=granularity,
                grouping=grouping,
                start=start_d,
                end=end_d,
            )
        }

        # file_change_fact for clean line counts
        file_rows: dict[Any, tuple[int, int]] = {}
        agg_cat: dict[Any, dict[str, tuple[int, int]]] = {}
        agg_crate: dict[Any, dict[str, tuple[int, int]]] = {}
        fcf_present = False
        try:
            agg_clean: dict[Any, tuple[int, int]] = {}
            for period, la, ld, p in load_file_change_by_period(
                conn,
                refresh_id=refresh_id,
                project=project,
                granularity=granularity,
                grouping=grouping,
                start=start_d,
                end=end_d,
            ):
                la = la or 0
                ld = ld or 0
                # Clean lines (non-code excluded)
                if not _is_non_code_path(p or ""):
                    cur = agg_clean.get(period, (0, 0))
                    agg_clean[period] = (cur[0] + la, cur[1] + ld)
                # Per-category breakdown (all paths, including non-code)
                cat = _classify_path(project, p or "")
                cb = agg_cat.setdefault(period, {})
                cat_cur = cb.get(cat, (0, 0))
                cb[cat] = (cat_cur[0] + la, cat_cur[1] + ld)
                # Per-crate breakdown (Rust projects only)
                crate = _crate_from_path(project, p or "")
                if crate:
                    crb = agg_crate.setdefault(period, {})
                    cr_cur = crb.get(crate, (0, 0))
                    crb[crate] = (cr_cur[0] + la, cr_cur[1] + ld)
            file_rows = agg_clean
            fcf_present = True
        except Exception:
            fcf_present = False

        # symbol_change for symbol counts
        symbol_rows: dict[Any, dict[str, int]] = {}
        sc_present = False
        try:
            for period, ct, n in load_symbol_change_by_period(
                conn,
                refresh_id=refresh_id,
                project=project,
                granularity=granularity,
                grouping=grouping,
                start=start_d,
                end=end_d,
            ):
                bucket = symbol_rows.setdefault(period, {})
                bucket[ct] = n
            sc_present = bool(symbol_rows)
        except Exception:
            sc_present = False

    periods = []
    cumulative = 0
    for period_date, row in sorted(commit_rows.items(), key=lambda kv: kv[0]):
        # row = (period, n, la, ld, fc) from the SELECT above
        _, n, la, ld, fc = row
        clean_la, clean_ld = file_rows.get(period_date, (la or 0, ld or 0))
        cumulative += (clean_la - clean_ld)
        # symbol_change.change_type is stored as uppercase words
        # ('ADDED'/'MODIFIED'/'RENAMED'/'DELETED') by the materializer in
        # analysis.code_index.symbol_changes. The single-letter / lowercase
        # fallbacks remain in case a future writer changes encoding.
        sym_bucket = symbol_rows.get(period_date, {})
        sa = sym_bucket.get("ADDED", 0) + sym_bucket.get("A", 0) + sym_bucket.get("added", 0)
        sm = sym_bucket.get("MODIFIED", 0) + sym_bucket.get("M", 0) + sym_bucket.get("modified", 0)
        sr = sym_bucket.get("RENAMED", 0) + sym_bucket.get("R", 0) + sym_bucket.get("renamed", 0)
        mean_lpc = round(clean_la / max(1, n), 1)
        # granularity_index is undefined (None) when clean_la == 0
        # (zero-line weeks have no meaningful granularity)
        granularity_index = (
            round(n / clean_la * 1000, 3) if clean_la > 0 else None
        )
        # commit_regime: human-readable label derived from granularity_index.
        # On this repo ecosystem, < 0.5 commits/1k-lines is squash-merge of
        # full PRs (one merge = thousands of lines). > 1.0 is atomic-commit
        # workflow (each step → commit, claude-code style). The threshold is
        # calibrated against the operator's Feb 2026 → May 2026 transition.
        if granularity_index is None:
            commit_regime = None
        elif granularity_index < 0.5:
            commit_regime = "pr_squash_merge"
        elif granularity_index > 1.0:
            commit_regime = "atomic_commits"
        else:
            commit_regime = "transitional"
        # Per-category line breakdown for this period
        cat_added: dict[str, int] = {}
        cat_deleted: dict[str, int] = {}
        for cat in ("source", "test", "config", "doc", "other"):
            cla, cld = agg_cat.get(period_date, {}).get(cat, (0, 0))
            cat_added[cat] = cla
            cat_deleted[cat] = cld

        # Per-crate breakdown (Rust projects only)
        crate_breakdown: dict[str, dict[str, int]] = {}
        if project == "sinex":
            for crate, (cr_la, cr_ld) in sorted(
                agg_crate.get(period_date, {}).items(),
                key=lambda kv: -(kv[1][0] + kv[1][1]),
            ):
                crate_breakdown[crate] = {"added": cr_la, "deleted": cr_ld}

        net_clean = clean_la - clean_ld
        periods.append({
            "period_start": _json_safe(period_date),
            "commit_count": n,
            "files_changed": fc or 0,
            "lines_added": la or 0,
            "lines_deleted": ld or 0,
            "net_lines": la - ld,
            "lines_added_clean": clean_la,
            "lines_deleted_clean": clean_ld,
            "net_clean": net_clean,
            "cumulative_net": cumulative,
            "source_lines_added": cat_added["source"],
            "source_lines_deleted": cat_deleted["source"],
            "test_lines_added": cat_added["test"],
            "test_lines_deleted": cat_deleted["test"],
            "config_lines_added": cat_added["config"],
            "config_lines_deleted": cat_deleted["config"],
            "doc_lines_added": cat_added["doc"],
            "doc_lines_deleted": cat_deleted["doc"],
            "other_lines_added": cat_added["other"],
            "other_lines_deleted": cat_deleted["other"],
            "crates": crate_breakdown if crate_breakdown else None,
            "symbols_added": sa,
            "symbols_modified": sm,
            "symbols_renamed": sr,
            "symbols_total": sa + sm + sr,
            "mean_lines_per_commit_clean": mean_lpc,
            "granularity_index": granularity_index,
            "commit_regime": commit_regime,
        })

    reasons = []
    if not fcf_present:
        reasons.append("file_change_fact empty for this snapshot — lines_added_clean falls back to raw lines_added")
    if not sc_present:
        reasons.append("symbol_change empty for this snapshot — symbol counts all zero")
    degraded = bool(reasons)

    return {
        "project": project,
        "granularity": granularity,
        "grouping": grouping,
        "materialized_refresh_id": refresh_id,
        "refresh_id": refresh_id,
        "materialization": materialization,
        "degraded": degraded,
        "reason": "; ".join(reasons) if reasons else None,
        "substrate_window": substrate_window,
        "periods": periods,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Parametric Dispatchers
# ══════════════════════════════════════════════════════════════════════════════


def velocity(
    view: str = "series",
    projects: list[str] | None = None,
    refresh_id: str | None = None,
    window_days: int = 7,
) -> Any:
    """Commit velocity data with parametric dispatch.

    Parameters:
        view:       "series" (per-project weekly velocity series, default) or
                    "narrative" (velocity narrative with context over latest snapshot).
        projects:   Filter to specific projects; None = all.
        refresh_id: Materialized substrate snapshot to query; default = best current snapshot.
        window_days: Rolling-average window size (only for view=series; default 7).

    Returns:
        For view="series": [{"project": str, "date": str, "commit_count": int, ...}]
        For view="narrative": {materialized_refresh_id, total_commits, peak, projects, summary_text}
    """
    if view == "series":
        return velocity_series(projects=projects, refresh_id=refresh_id, window_days=window_days)
    if view == "narrative":
        return velocity_narrative(projects=projects, refresh_id=refresh_id)
    return {"error": f"unknown view {view!r}. choices: series, narrative"}


def code_velocity(
    view: str = "throughput",
    project: str | None = None,
    start: str | None = None,
    end: str | None = None,
    refresh_id: str | None = None,
    granularity: str = "week",
    grouping: str = "raw",
    category: str | None = None,
) -> Any:
    """Code velocity and throughput metrics with parametric dispatch.

    Parameters:
        view:       "throughput" (engineering throughput composite, default),
                    "symbols" (symbol-level velocity by path), or
                    "rhythm" (temporal commit rhythm patterns).
        project:    Project name for throughput and rhythm views; None for rhythm = all projects.
        start, end: ISO dates; required for throughput view; used for symbols/rhythm if provided.
        refresh_id: Materialized substrate snapshot (default: best current snapshot).
        granularity: "day" | "week" | "month" (only for view=throughput; default "week").
        grouping:   "raw" | "pr" (only for view=throughput; default "raw").
        category:   Filter to source/test/config/doc/other (only for view=throughput; None = all).

    Returns:
        For view="throughput": {project, periods, granularity, ...}
        For view="symbols": [{"project": str, "date": str, "commit_count": int, ...}]
        For view="rhythm": {hourly, weekday, peak_hour, peak_weekday}
    """
    if view == "throughput":
        return engineering_throughput(
            project=project or "unknown",
            start=start,
            end=end,
            granularity=granularity,
            refresh_id=refresh_id,
            grouping=grouping,
            category=category,
        )
    if view == "symbols":
        projects_list = [project] if project else None
        return symbol_velocity(projects=projects_list, refresh_id=refresh_id)
    if view == "rhythm":
        return temporal_rhythm(project=project, refresh_id=refresh_id)
    return {"error": f"unknown view {view!r}. choices: throughput, symbols, rhythm"}
