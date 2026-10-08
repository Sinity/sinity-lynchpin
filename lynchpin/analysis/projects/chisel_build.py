"""Chisel build, derived reports, and generation publication."""

from __future__ import annotations

import datetime as dt
import csv
import hashlib
import html
import json
import math
import os
import re
import shutil
import statistics
import sys
import threading
import time
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from lynchpin.core.errors import MaterializationError, SourceUnavailableError
from lynchpin.sources import chisel_options
from lynchpin.sources.chisel import (
    _print,
    _print_lock,
    _progress_lock,
    _active_stages,
    _build_state_local,
    _set_stage,
    _stage_timing_local,
    _abort_event,
    _print_live,
    _emit,
    _default_output_root,
    DEFAULT_MAX_WORKERS,
    DEFAULT_SLICE_WORKERS,
    DEFAULT_REPOMIX_WORKERS,
    DEFAULT_ISSUE_LIMIT,
    LARGE_SLICE_BYTES,
    _WORKTREE_TAR_EXCLUDES,
    DEFAULT_IGNORE,
    GROWTH_IGNORE,
    _utc_ts,
    _terminate_active_processes,
    _run,
    _require_repomix,
    _repomix_version,
    _git_state,
    _has_github_remote,
    _sanitize_xml,
    _validate_xml,
    _fmt_bytes,
    RepoPlan,
    REPO_PLANS,
    _record_substage_duration,
    _SCRATCHPAD_INCLUDE,
    _ACCELERANT_INCLUDE,
    _ACCELERANT_IGNORE,
    _normalize_rel_pattern,
    _glob_matches,
    _glob_any,
    _stats_buckets,
    _classify_stats_bucket,
    _collect_tokei_stats,
)
from lynchpin.sources.chisel import _console
from lynchpin.sources.chisel_warnings import captured_warnings
from . import chisel_terminal

if _console is not None:
    from rich.table import Table

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

    growth_ignore = tuple(DEFAULT_IGNORE) + GROWTH_IGNORE + tuple(plan.extra_ignore)
    daily_map: dict[str, dict[str, Any]] = {}
    bucket_churn: dict[str, dict[str, int]] = {}
    kind_counts: dict[str, int] = {}
    heatmap = [[0 for _hour in range(24)] for _day in range(7)]
    commit_changes: list[int] = []
    commits: list[dict[str, Any]] = []
    excluded_additions = 0
    excluded_deletions = 0

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
            if _glob_any(path, growth_ignore):
                excluded_additions += added
                excluded_deletions += deleted
                continue
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
        "excluded_data_additions": excluded_additions,
        "excluded_data_deletions": excluded_deletions,
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
            "path_exclusions": (
                "DEFAULT_IGNORE + GROWTH_IGNORE + plan.extra_ignore; agent "
                "coordination payloads, bead ledgers, lockfiles, and databases "
                "do not count toward growth"
            ),
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
            "- Git `numstat` measures tracked text: implementation, tests, documentation, configuration, and schemas all contribute.",
            (
                "- Agent coordination payloads (`.agent/**`), bead ledgers (`.beads/**`), lockfiles, and databases are excluded from growth accounting"
                f" ({summary.get('excluded_data_additions', 0):,} added / {summary.get('excluded_data_deletions', 0):,} deleted lines excluded)."
            ),
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
        if not chisel_options.active_options.refresh:
            try:
                _github_context_index = _build_github_context_index()
                _github_context_manifest = {"refresh_status": "local_only", "remote_freshness": "unknown"}
            except Exception as exc:
                _github_context_index = {}
                _github_context_manifest = {"refresh_status": "unavailable", "reason": str(exc)}
            _github_context_ready = True
            return
        from lynchpin.ingest.github_context_materialize import materialize_github_context

        try:
            _github_context_manifest = materialize_github_context(
                projects=projects, progress=_print_live
            )
            if _github_context_manifest is not None:
                _github_context_manifest.setdefault("refresh_status", "refreshed")
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
            _github_context_manifest = {
                "refresh_status": "stale_fallback",
                "refresh_error": str(exc),
            }
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


def _ensure_chisel_prerequisites(plans: Sequence[RepoPlan]) -> None:
    if not any(plan.github_slug for plan in plans):
        return
    if "trackers" not in chisel_options.active_options.datasets:
        return
    if chisel_options.active_options.refresh:
        _print_live("GitHub: refreshing the mirror from the remote…")
    t0 = time.perf_counter()
    _ensure_github_context_for_chisel({plan.name for plan in plans})
    _print_live(chisel_terminal.github_context_line(_github_context_manifest, time.perf_counter() - t0))


def _build_github_context_index() -> dict[tuple[str, str, str, str], list[Any]]:
    from lynchpin.sources.github_context import iter_github_context

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


def _github_inventory_coverage(project: str, kind: str) -> str:
    manifest = _github_context_manifest or {}
    coverage = ((manifest.get("inventory_coverage") or {}).get(project) or {}).get(kind) or {}
    return str(coverage.get("coverage") or "unknown")


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
    issues: list[dict], repo_slug: str, state: str, generated_at: str,
    *, coverage: str = "unknown",
) -> str:
    root = ET.Element(
        "issues",
        {
            "repository": repo_slug,
            "state": state,
            "generated-at": generated_at,
            "count": str(len(issues)),
            "coverage": coverage,
            "total-count": str(len(issues)) if coverage == "complete" else "unknown",
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

    if not chisel_options.active_options.xml:
        return len(open_issues), len(closed_issues)
    count = 0
    for state, issues in [("open", open_issues), ("closed", closed_issues)]:
        xml = _build_issues_xml(
            issues, plan.github_slug, state, generated_at,
            coverage=_github_inventory_coverage(plan.name, "issue"),
        )
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
        "reviewComments": [
            {
                "author": {"login": comment.author.login},
                "body": comment.body,
                "path": comment.path or "",
                "line": comment.line,
                "diffHunk": comment.diff_hunk or "",
                "createdAt": comment.created_at.isoformat()
                if comment.created_at
                else "",
                "url": comment.url or "",
                "reviewId": comment.review_id,
            }
            for comment in item.review_comments
        ],
    }


def _prs_from_context_product(
    project: str, repo_slug: str, state: str, limit: int = DEFAULT_ISSUE_LIMIT
) -> list[dict]:
    if state == "all":
        items = [
            *_github_context_items(project, repo_slug, "pr", "open", limit),
            *_github_context_items(project, repo_slug, "pr", "closed", limit),
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
        pr["_review_comments"] = pr.pop("reviewComments", [])


def _build_prs_xml(
    prs: list[dict], repo_slug: str, state: str, generated_at: str,
    *, coverage: str = "unknown",
) -> str:
    root = ET.Element(
        "prs",
        {
            "repository": repo_slug,
            "state": state,
            "generated-at": generated_at,
            "count": str(len(prs)),
            "coverage": coverage,
            "total-count": str(len(prs)) if coverage == "complete" else "unknown",
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
        review_comments = ET.SubElement(el, "inline-review-comments")
        for comment in pr.get("_review_comments", []):
            attrs = {
                "author": (comment.get("author") or {}).get("login", "?"),
                "path": comment.get("path", ""),
                "created-at": comment.get("createdAt", ""),
                "url": comment.get("url", ""),
            }
            if comment.get("line") is not None:
                attrs["line"] = str(comment["line"])
            if comment.get("reviewId") is not None:
                attrs["review-id"] = str(comment["reviewId"])
            ce = ET.SubElement(review_comments, "comment", attrs)
            diff = ET.SubElement(ce, "diff-hunk")
            diff.text = comment.get("diffHunk", "")
            body = ET.SubElement(ce, "body")
            body.text = comment.get("body", "")
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def _generate_prs(
    plan: RepoPlan, out_dir: Path, generated_at: str, log: list[str] | None = None
) -> tuple[int, int]:
    """Write PR snapshots for open, closed-unmerged, and merged states.

    The historical tuple return remains ``(open_count, merged_count)`` for
    callers that consume the established stage result shape.
    """
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
    closed_prs = _prs_from_context_product(
        plan.name, plan.github_slug, "closed", DEFAULT_ISSUE_LIMIT
    )
    _normalize_pr_data(closed_prs)

    if not chisel_options.active_options.xml:
        return len(open_prs), len(merged_prs)
    for state, prs in [
        ("open", open_prs),
        ("closed", closed_prs),
        ("merged", merged_prs),
    ]:
        xml = _build_prs_xml(
            prs, plan.github_slug, state, generated_at,
            coverage=_github_inventory_coverage(plan.name, "pr"),
        )
        (out_dir / f"{plan.name}-prs-{state}.xml").write_text(xml, encoding="utf-8")

    _emit(
        log,
        f"  [dim]prs: {len(open_prs)} open / {len(closed_prs)} closed-unmerged / {len(merged_prs)} merged[/dim]",
    )
    return len(open_prs), len(merged_prs)


# ═══════════════════════════════════════════════════════════════════════════════
# Beads context
# ═══════════════════════════════════════════════════════════════════════════════


def _bd_json(cmd: Sequence[str], *, cwd: Path) -> Any:
    result = _run(["bd", *cmd, "--json", "--readonly", "--sandbox"], cwd=cwd)
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "bd command failed").strip()
        raise SourceUnavailableError("beads", reason=details)
    text = result.stdout.strip()
    if not text:
        return None
    return json.loads(text)


def _bd_export_rows(repo: Path) -> list[dict[str, Any]]:
    result = _run(["bd", "export", "--include-memories", "--readonly", "--sandbox"], cwd=repo)
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
    terminal_statuses = {"closed", "done", "resolved"}
    try:
        snapshot_day = dt.datetime.strptime(generated_at, "%Y-%m-%dT%H%M%SZ").date()
    except ValueError:
        try:
            snapshot_day = dt.datetime.strptime(generated_at, "%Y%m%dT%H%M%SZ").date()
        except ValueError:
            snapshot_day = dt.datetime.now(dt.timezone.utc).date()
    created_dates = [
        parsed.date()
        for issue in issues
        if (parsed := _parse_beads_timestamp(issue.get("created_at"))) is not None
        and parsed.date() <= snapshot_day
    ]
    closed_pairs = [
        (created, closed)
        for issue in issues
        if (created := _parse_beads_timestamp(issue.get("created_at"))) is not None
        and (closed := _parse_beads_timestamp(issue.get("closed_at"))) is not None
        and closed >= created
        and created.date() <= snapshot_day
        and closed.date() <= snapshot_day
    ]
    closed_current = [
        issue
        for issue in issues
        if str(issue.get("status") or "").lower() in terminal_statuses
    ]
    open_current = len(issues) - len(closed_current)
    unplaced_closed = sum(
        1
        for issue in closed_current
        if (created := _parse_beads_timestamp(issue.get("created_at"))) is None
        or (closed := _parse_beads_timestamp(issue.get("closed_at"))) is None
        or closed < created
        or created.date() > snapshot_day
        or closed.date() > snapshot_day
    )
    if not created_dates:
        return {
            "summary": {
                "first_created_day": None,
                "snapshot_day": snapshot_day.isoformat(),
                "created": 0,
                "closed": 0,
                "estimated_open_from_timestamps": None,
                "open_current_by_status": open_current,
                "closed_current_without_valid_timestamps": unplaced_closed,
                "issues_without_valid_created_at": len(issues),
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
                "estimated_open_from_timestamps": open_count,
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
            "estimated_open_from_timestamps": open_count,
            "open_current_by_status": open_current,
            "closed_current_without_valid_timestamps": unplaced_closed,
            "issues_without_valid_created_at": len(issues) - len(created_dates),
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
        "caveat": (
            "The daily trajectory is an estimate from valid created_at and closed_at "
            "timestamps. Closed issues without valid timestamps are left unplaced; "
            "the report gives the current open count from issue statuses separately. "
            "Reopen cycles and compacted/deleted issues require Dolt history."
        ),
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
const searchableIssues = issues.map(item => ({{item, text: JSON.stringify(item).toLowerCase()}}));
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
  const visible = searchableIssues.filter(({{item, text}}) => (!query || text.includes(query)) && (!status || item.status === status) && (!priority || String(item.priority ?? '') === priority) && (!type || item.type === type)).map(({{item}}) => item);
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
    export_path = out_dir / (f"{plan.name}-beads-export.jsonl" if chisel_options.active_options.xml else "trackers/beads-export.jsonl")
    export_path.parent.mkdir(parents=True, exist_ok=True)

    json_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if chisel_options.active_options.xml:
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
        stripped = _sanitize_xml(xml_path)
        if stripped:
            _emit(
                log,
                f"  [dim]┄ {xml_path.name}: {stripped:,} ctrl bytes stripped[/dim]",
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
        export_path.relative_to(out_dir).as_posix(),
    ]
    names = [name for name in names if (out_dir / name).exists()]
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
    failures: list[str] = []

    bundle_path = out_dir / f"{plan.name}-all-refs.bundle"
    bundle_lock = Path(f"{bundle_path}.lock")
    if bundle_lock.exists():
        bundle_lock.unlink()
        _emit(log, f"  [dim]removed stale bundle lock: {bundle_lock.name}[/dim]")
    started = time.perf_counter()
    bundle = _run(["git", "bundle", "create", str(bundle_path), "--all"], cwd=plan.path)
    _record_substage_duration("git_bundle", started)
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
        failures.append(f"git bundle: {details}")

    # Working-tree tar captures committed files AND uncommitted modifications.
    # This differs from `git archive HEAD` which would miss dirty working-tree changes.
    archive_path = out_dir / f"{plan.name}-working-tree.tar.gz"
    plan_excludes = []
    for pat in plan.extra_ignore:
        # Convert a recursive glob into a tar --exclude name.
        p = pat.strip("/").lstrip("**/").rstrip("/**").rstrip("/")
        if p:
            plan_excludes.append(f"--exclude={p}")
    started = time.perf_counter()
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
    _record_substage_duration("working_tree_tar", started)
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
        failures.append(f"working-tree tar: {details}")

    tree_path = out_dir / f"{plan.name}-repo-tree.txt"
    started = time.perf_counter()
    tree_path.write_text(_repo_tree(plan.path, max_depth=3), encoding="utf-8")
    _record_substage_duration("repo_tree", started)
    _emit(
        log,
        f"  [green]✓[/green] {tree_path.name} ([dim]{_fmt_bytes(tree_path.stat().st_size)}[/dim])",
    )
    sidecars.append(tree_path.name)
    total_bytes += tree_path.stat().st_size

    if failures:
        raise MaterializationError(
            plan.name, reason="portable sidecar failures: " + "; ".join(failures)
        )
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
    if name == "snapshot-differences.jsonl":
        return "captured-snapshots", "Differences between policy-filtered captured source snapshots"
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
    if name.endswith("-scratchpad.xml"):
        return "scratchpad", "Repomix XML over .agent/scratch working notes"
    if name.endswith("-accelerants.xml"):
        return (
            "accelerants",
            "Repomix XML over GPT-Pro accelerant corpora (.agent/scratch/corpus-*)",
        )
    if name.endswith("-issues-open.xml") or name.endswith("-issues-closed.xml"):
        return "github-context", "Rendered GitHub issue context"
    if any(
        name.endswith(f"-prs-{state}.xml") for state in ("open", "closed", "merged")
    ):
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
                "bytes": None if child.is_dir() else child.stat().st_size,
                "ignored": bool(matched_patterns),
                "local_state": bool(local_state),
                "matched_patterns": matched_patterns[:8],
            }
        )

    ignored_local_state = [e for e in entries if e["ignored"] and e["local_state"]]
    tracked_hidden = [e for e in entries if not e["ignored"]]

    def measured_total(rows: list[dict[str, Any]]) -> int | None:
        return None if any(e["bytes"] is None for e in rows) else sum(e["bytes"] for e in rows)

    audit = {
        "project": plan.name,
        "source": str(plan.path),
        "entries": entries,
        "size_method": "top-level regular file sizes only; directories are unmeasured to avoid recursive scans",
        "unmeasured_directories": [e["path"] for e in entries if e["bytes"] is None],
        "ignored_local_state_bytes": measured_total(ignored_local_state),
        "tracked_hidden_bytes": measured_total(tracked_hidden),
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
        "Directory sizes are unmeasured; no recursive scan is performed.",
        "",
        "| Path | Ignored | Local state | Size | Matched patterns |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for entry in sorted(entries, key=lambda e: (-(e["bytes"] or 0), e["path"])):
        patterns = ", ".join(f"`{p}`" for p in entry["matched_patterns"][:4]) or "-"
        lines.append(
            f"| `{entry['path']}` | {str(entry['ignored']).lower()} | "
            f"{str(entry['local_state']).lower()} | "
            f"{_fmt_bytes(entry['bytes']) if entry['bytes'] is not None else 'unmeasured'} | {patterns} |"
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
                "bytes": 0,
                "files": 0,
                "exclusive_bytes": 0,
                "exclusive_files": 0,
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

    # One walk fills recursive drill-down sizes and assigns every file to the
    # deepest represented directory for disjoint class summaries.
    rows_by_path = {row["path"]: row for row in rows}
    for path in agent_dir.rglob("*"):
        if path.is_dir() and not path.is_symlink():
            continue
        if not path.is_file() and not path.is_symlink():
            continue
        rel = path.relative_to(repo_root).as_posix()
        size = path.lstat().st_size if path.is_symlink() else path.stat().st_size
        file_row = rows_by_path.get(rel)
        if file_row is not None:
            file_row["bytes"] = size
            file_row["files"] = 1

        exclusive_owner = file_row
        parent = Path(rel).parent
        while parent.as_posix() != ".":
            ancestor = rows_by_path.get(parent.as_posix())
            if ancestor is not None and ancestor["kind"] == "dir":
                ancestor["bytes"] += size
                ancestor["files"] += 1
                if exclusive_owner is None:
                    exclusive_owner = ancestor
            parent = parent.parent
        if exclusive_owner is not None:
            exclusive_owner["exclusive_bytes"] += size
            exclusive_owner["exclusive_files"] += 1

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
        entry["bytes"] += int(row["exclusive_bytes"])
        entry["files"] += int(row["exclusive_files"])
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
        "Directory entries below retain recursive sizes; summary sizes count each file once by its deepest listed directory.",
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
    for path in sorted(out_dir.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file():
            continue
        scope, purpose = _file_scope_and_purpose(plan, path.name)
        rows.append(
            {
                "name": path.relative_to(out_dir).as_posix(),
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
    pending_artifact_names: Sequence[str] = (),
    log: list[str] | None = None,
) -> tuple[list[str], int]:
    artifacts = _artifact_rows(out_dir, plan)
    stats = _read_json_file(out_dir / f"{plan.name}-tokei-stats.json")
    agent_audit = _read_json_file(out_dir / f"{plan.name}-agent-audit.json")
    ignore_audit = _read_json_file(out_dir / f"{plan.name}-ignore-audit.json")
    github_coverage = _read_json_file(out_dir / "trackers/github-coverage.json")
    materialization = github_coverage.get("materialization") or _github_context_manifest or {}
    github_refresh_status = materialization.get("refresh_status")
    project_inventory_coverage = (
        github_coverage.get("inventory_coverage")
        or (materialization.get("inventory_coverage") or {}).get(plan.name, {})
    )
    issue_coverage = project_inventory_coverage.get("issue") or {}
    pr_coverage = project_inventory_coverage.get("pr") or {}
    issue_coverage_status = str(issue_coverage.get("coverage") or "unavailable")
    pr_coverage_status = str(pr_coverage.get("coverage") or "unavailable")
    issue_limit_reached = issue_coverage_status == "possibly_truncated"
    pr_limit_reached = pr_coverage_status == "possibly_truncated"
    github_limit_reached = issue_limit_reached or pr_limit_reached
    refresh_unknown = github_refresh_status in {"local_only", "unavailable", "stale_fallback"}
    current_issue_unknown = refresh_unknown or issue_coverage_status != "complete"
    current_pr_unknown = refresh_unknown or pr_coverage_status != "complete"
    github_count_coverage = (
        "possibly_truncated" if github_limit_reached
        else "unavailable" if refresh_unknown or not (issue_coverage_status == pr_coverage_status == "complete")
        else "captured_export"
    )

    large_artifacts = [
        row
        for row in sorted(artifacts, key=lambda item: int(item["bytes"]), reverse=True)
        if int(row["bytes"]) >= LARGE_SLICE_BYTES
    ][:12]
    top_buckets = sorted(
        (
            (name, values)
            for name, values in (stats.get("buckets") or {}).items()
            if values.get("loc_measured", True) and values.get("lines") is not None
        ),
        key=lambda item: int(item[1].get("lines") or 0),
        reverse=True,
    )[:8]
    agent_summary = agent_audit.get("summary_by_class") or {}
    review_agent_entries = int((agent_summary.get("review") or {}).get("entries") or 0)
    archive_agent_bytes = int(
        (agent_summary.get("archive-or-generated") or {}).get("bytes") or 0
    )
    ignored_local_state = ignore_audit.get("ignored_local_state_bytes")
    tracked_hidden = ignore_audit.get("tracked_hidden_bytes")
    xml_snapshot_count = sum(1 for path in out_dir.glob("*.xml") if path.is_file())
    from lynchpin.sources.chisel_compact import open_text, resolved_stream

    differences_path = resolved_stream(out_dir / "reports/snapshot-differences.jsonl")
    snapshot_difference_count = 0
    if differences_path.is_file():
        with open_text(differences_path) as differences:
            snapshot_difference_count = sum(1 for line in differences if line.strip())
    artifact_count = len(
        {row["name"] for row in artifacts}.union(pending_artifact_names)
    )
    beads = beads or {}
    beads_counts = beads.get("counts") if beads.get("available") else {}
    beads_counts = beads_counts if isinstance(beads_counts, dict) else {}
    beads_issues = int(beads_counts.get("issues") or 0)
    beads_ready = int(beads_counts.get("ready") or 0)
    beads_blocked = int(beads_counts.get("blocked") or 0)
    beads_dependencies = int(beads_counts.get("dependencies") or 0)
    beads_memories = int(beads_counts.get("memories") or 0)

    issue_open_display = (
        f"observed rows: {issues_open}; current total unknown (inventory limit reached)"
        if issue_limit_reached
        else f"current unavailable; local snapshot {issues_open}"
        if current_issue_unknown
        else str(issues_open)
    )
    pr_open_display = (
        f"observed rows: {prs_open}; current total unknown (inventory limit reached)"
        if pr_limit_reached
        else f"current unavailable; local snapshot {prs_open}"
        if current_pr_unknown
        else str(prs_open)
    )
    pr_merged_display = (
        f"observed rows: {prs_merged}; total unknown (inventory limit reached)"
        if pr_limit_reached
        else f"local snapshot {prs_merged}; current value unavailable"
        if refresh_unknown
        else str(prs_merged)
    )

    open_first = [
        f"{plan.name}-overview.md",
        f"{plan.name}-manifest.json",
        f"{plan.name}-beads.md" if beads.get("available") else None,
        f"{plan.name}-prs-open.xml" if prs_open else None,
        f"{plan.name}-issues-open.xml" if issues_open else None,
        differences_path.relative_to(out_dir).as_posix(),
        f"{plan.name}-growth.md",
        f"{plan.name}-tokei-stats.md",
        f"{plan.name}-agent-audit.md" if agent_audit else None,
    ]
    open_first = [item for item in open_first if item and (
        item == f"{plan.name}-overview.md" or (out_dir / item).is_file() or item in pending_artifact_names
    )]

    overview = {
        "project": plan.name,
        "source": str(plan.path),
        "generated_at": generated_at,
        "git": git,
        "counts": {
            "configured_slices": len(plan.slices),
            "xml_snapshots": xml_snapshot_count,
            "snapshot_differences": snapshot_difference_count,
            "artifacts": artifact_count,
            "issues_open": issues_open,
            "issues_closed": issues_closed,
            "prs_open": prs_open,
            "prs_merged": prs_merged,
            "github_current_count_coverage": github_count_coverage,
            "issues_open_count_coverage": issue_coverage_status,
            "issues_closed_count_coverage": issue_coverage_status,
            "prs_open_count_coverage": pr_coverage_status,
            "prs_merged_count_coverage": pr_coverage_status,
            "issues_open_total": issues_open if not current_issue_unknown else None,
            "issues_closed_total": issues_closed if not current_issue_unknown else None,
            "prs_open_total": prs_open if not current_pr_unknown else None,
            "prs_merged_total": prs_merged if not current_pr_unknown else None,
            "issues_open_count_semantics": "captured_count" if not current_issue_unknown else "observed_rows_total_unknown",
            "issues_closed_count_semantics": "captured_count" if not current_issue_unknown else "observed_rows_total_unknown",
            "prs_open_count_semantics": "captured_count" if not current_pr_unknown else "observed_rows_total_unknown",
            "prs_merged_count_semantics": "captured_count" if not current_pr_unknown else "observed_rows_total_unknown",
            "issues_open_current": None if current_issue_unknown else issues_open,
            "prs_open_current": None if current_pr_unknown else prs_open,
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
        f"| Captured snapshot differences | {snapshot_difference_count} |",
        f"| Artifacts | {artifact_count} |",
        f"| Open issues | {issue_open_display} |",
        f"| Open PRs | {pr_open_display} |",
        f"| Merged PRs | {pr_merged_display} |",
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
    elif ignored_local_state is None and ignore_audit:
        attention_lines.append("- Ignored local runtime state size: unmeasured.")
    if tracked_hidden:
        attention_lines.append(
            f"- Tracked hidden files/directories: {_fmt_bytes(tracked_hidden)}."
        )
    elif tracked_hidden is None and ignore_audit:
        attention_lines.append("- Hidden path size: unmeasured.")
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
    pending_artifact_names: Sequence[str] = (),
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
        or (overview.get("attention") or {}).get("xml_errors")
        or (_github_context_manifest or {}).get("refresh_status") == "stale_fallback"
        else "ok",
        "counts": overview.get("counts") or {},
        "attention": overview.get("attention") or {},
        "size": {
            "total_bytes": sum(int(row["bytes"]) for row in artifacts),
            "artifact_count": len(
                {row["name"] for row in artifacts}.union(pending_artifact_names)
            ),
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
            "ignored_local_state_bytes": ignore_audit.get("ignored_local_state_bytes"),
            "tracked_hidden_bytes": ignore_audit.get("tracked_hidden_bytes"),
            "unmeasured_directories": ignore_audit.get("unmeasured_directories") or [],
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
            "refresh_status": github.get("refresh_status") or "unknown",
            "refresh_error": github.get("refresh_error"),
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
        "## Attention",
        "",
        *(
            [
                f"- XML validation errors: {len(audit['attention'].get('xml_errors') or [])}"
            ]
            if audit["attention"].get("xml_errors")
            else ["- No XML validation errors."]
        ),
        "",
        "## GitHub Context",
        "",
        f"- Refresh status: {audit['github_context']['refresh_status']}",
        *(
            [f"- Refresh error: {audit['github_context']['refresh_error']}"]
            if audit["github_context"].get("refresh_error")
            else []
        ),
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
    for path in sorted(out_dir.rglob("*"), key=lambda p: p.as_posix()):
        if not path.is_file() or path == manifest_path:
            continue
        scope, purpose = _file_scope_and_purpose(plan, path.name)
        artifacts.append(
            {
                "name": path.relative_to(out_dir).as_posix(),
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
    capture = _read_json_file(out_dir / "capture.json")
    if capture:
        manifest["snapshot_id"] = capture.get("snapshot_id")
        manifest["role_policy_version"] = capture.get("policy_version")
    self_row = next(
        row for row in manifest["artifacts"] if row["name"] == manifest_path.name
    )
    serialized = ""
    for _ in range(16):
        serialized = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        size = len(serialized.encode("utf-8"))
        if self_row["bytes"] == size:
            break
        self_row["bytes"] = size
    else:
        raise RuntimeError("manifest self-size did not reach a stable value")
    manifest_path.write_text(serialized, encoding="utf-8")
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
    explicit = {
        "implementation": "Production/tooling",
        "tooling": "Production/tooling",
        "tests": "Tests",
        "documentation": "Docs/context",
        "context": "Docs/context",
        "evidence": "Evidence/demo",
        "unclassified": "Unclassified",
    }
    if bucket in explicit:
        return explicit[bucket]
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
    if lowered in {"code-proper", "production", "tooling", "implementation"}:
        return "Production/tooling"
    return "Unclassified"


def _composition_rows(
    plans: Sequence[RepoPlan], output_root: Path
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for plan in plans:
        stats = _read_json_file(
            output_root / plan.name / f"{plan.name}-tokei-stats.json"
        )
        values_by_role: dict[str, list[int | None]] = {
            role: []
            for role in (
                "Production/tooling",
                "Tests",
                "Docs/context",
                "Evidence/demo",
                "Unclassified",
            )
        }
        for bucket, values in (stats.get("buckets") or {}).items():
            role = _stats_role(bucket)
            if values.get("loc_measured", True) and values.get("code") is not None:
                values_by_role[role].append(int(values["code"]))
            else:
                values_by_role[role].append(None)
        totals: dict[str, int | None] = {}
        for role, values in values_by_role.items():
            totals[role] = (
                sum(values) if values and all(v is not None for v in values) else None
            )
        production_tooling = totals["Production/tooling"]
        tests = totals["Tests"]
        known_maintained = (
            production_tooling + tests
            if production_tooling is not None and tests is not None
            else None
        )
        unclassified_bucket = (stats.get("buckets") or {}).get("unclassified") or {}
        has_unclassified = int(unclassified_bucket.get("files") or 0) > 0
        maintained = None if has_unclassified else known_maintained
        denominator = maintained
        rows.append(
            {
                "project": plan.name,
                **totals,
                "Known maintained code": known_maintained,
                "Maintained code": maintained,
                "Test share of production+tests": tests / denominator
                if tests is not None and denominator
                else None,
                "Evidence payload / maintained code": totals["Evidence/demo"]
                / maintained
                if totals["Evidence/demo"] is not None and maintained
                else None,
            }
        )
    return rows


def _svg_composition(rows: Sequence[dict[str, Any]]) -> str:
    width, height = 1200, 180 + len(rows) * 92
    left, right, top = 185, 55, 105
    plot_w = width - left - right
    roles = ("Production/tooling", "Tests")
    colors = ("#7c3aed", "#0891b2")
    complete_rows = [row for row in rows if row.get("Maintained code") is not None]
    maximum = max(
        1,
        max(
            (sum(int(row[role]) for role in roles) for row in complete_rows), default=0
        ),
    )
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">',
        "<title>Maintained source composition</title>",
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<text x="42" y="38" font-family="system-ui,sans-serif" font-size="25" font-weight="700" fill="#111827">Maintained source composition</text>',
        '<text x="42" y="65" font-family="system-ui,sans-serif" font-size="14" fill="#475569">Measured implementation, tooling, and test code lines from the captured inventory. Context and documentation are reported by bytes.</text>',
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
        if row.get("Maintained code") is None:
            svg.append(
                f'<text x="{left}" y="{y + 22}" font-family="system-ui,sans-serif" font-size="12" fill="#64748b">LOC unavailable; see coverage gaps</text>'
            )
            continue
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
    """Compare explicit role measurements; no size-normalized quality proxies."""
    growth_dir = output_root / "growth"
    if growth_dir.exists():
        shutil.rmtree(growth_dir)
    growth_dir.mkdir(parents=True)
    histories = {}
    composition = []
    trackers = {}
    for plan in plans:
        project_dir = output_root / plan.name
        growth = _read_json_file(project_dir / f"{plan.name}-growth.json")
        if growth:
            histories[plan.name] = growth
        metrics = _read_json_file(project_dir / "metrics" / "summary.json")
        for row in metrics.get("roles", []):
            composition.append({"project": plan.name, **row})
        beads = _read_json_file(project_dir / f"{plan.name}-beads.json")
        if beads.get("available"):
            trackers[plan.name] = {"counts": beads.get("counts"), "history": beads.get("history")}
    payload = {
        "generated_at": generated_at,
        "histories": histories,
        "composition": composition,
        "beads": trackers,
        "method": "Reachable commits across captured refs, counted once per commit. "
        "Merge diffs are first-parent. Git numstat measures changed text, "
        "not executable lines or effort. Historical paths use current role policy. "
        "All repository activity and maintained implementation/tests/tooling "
        "text changes are separate. Current LOC uses captured file roles. "
        "Documentation/context/evidence are reported as files and bytes.",
    }
    (growth_dir / "project-growth-summary.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    _write_csv_rows(growth_dir / "code-composition.csv", composition)
    _write_csv_rows(growth_dir / "beads-history.csv", [
        {"project": project, **row} for project, data in trackers.items()
        for row in (data.get("history") or {}).get("daily", [])
    ])
    for period in ("daily", "weekly", "monthly"):
        rows = [
            {"project": project, **row}
            for project, data in histories.items()
            for row in data.get(period, [])
        ]
        _write_csv_rows(growth_dir / f"{period}-project-growth.csv", rows)
    summaries = [
        {"project": project, **data.get("summary", {})}
        for project, data in histories.items()
    ]
    _write_csv_rows(growth_dir / "project-growth-summary.csv", summaries)
    charts = {
        "maintained-text-net-change.svg": _svg_line_chart(
            "Cumulative maintained-code text changes",
            payload["method"],
            [
                {
                    "name": project,
                    "points": [
                        (r["day"], r["cumulative_net"]) for r in data.get("daily", [])
                    ],
                }
                for project, data in histories.items()
            ],
        ),
        "weekly-activity.svg": _svg_heatmap(
            "All repository commit activity (including context-only commits)",
            {project: data.get("weekly", []) for project, data in histories.items()},
        ),
    }
    if trackers:
        charts["beads-backlog.svg"] = _svg_line_chart(
            "Estimated Beads backlog from retained task dates",
            "Incomplete dates, reopen cycles and deleted tasks limit reconstruction; current statuses are separate counts.",
            [{"name": project, "points": [
                (row["day"], row["estimated_open_from_timestamps"])
                for row in (data.get("history") or {}).get("daily", [])
                if row.get("estimated_open_from_timestamps") is not None
            ]} for project, data in trackers.items()],
        )
    for name, content in charts.items():
        (growth_dir / name).write_text(content, encoding="utf-8")
    lines = [
        "# Portfolio measurements",
        "",
        f"Generated: {generated_at}",
        "",
        payload["method"],
        "",
        "Test source share is not executed test coverage. "
        "Missing parsers and unclassified files are coverage gaps; see each project's metrics/summary.json.",
        "",
        "![Maintained text changes](maintained-text-net-change.svg)",
        "",
        "![Repository activity](weekly-activity.svg)",
        "",
        "## Current file roles",
        "",
        "| Project | Role | Files | Bytes | Code lines |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for row in composition:
        code = row["code"] if row.get("loc_measured") else "unavailable"
        lines.append(
            f"| {row['project']} | {row['role']} | {row['files']} | {row['bytes']} | {code} |"
        )
    lines.extend(
        [
            "",
            "CSV files contain the full measurements. JSON retains per-project methods, "
            "coverage and 30/90-day windows. These are descriptive measurements, not quality scores.",
        ]
    )
    (growth_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "directory": "growth",
        "files": [
            p.relative_to(output_root).as_posix() for p in sorted(growth_dir.iterdir())
        ],
    }


def _write_root_index(
    output_root: Path,
    plans: Sequence[RepoPlan],
    results: dict[str, Any],
    generated_at: str,
    repomix_version: str,
    total_elapsed: float,
    *,
    preflight_elapsed: float | None = None,
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
                "elapsed_s": results.get(plan.name, {}).get("elapsed_s"),
                "stage_timings": results.get(plan.name, {}).get("stage_timings", []),
                "snapshot_id": results.get(plan.name, {}).get("snapshot_id"),
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
        "output_root": ".",
        "total_elapsed_s": total_elapsed,
        "elapsed_scope": "preflight and project generation; excludes portfolio archive and atomic publication",
        "preflight_elapsed_s": preflight_elapsed,
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
        "Output root: this extracted directory (`.`)",
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
            f"{str(git.get('dirty', '?')).lower()} | {_github_open_index_count(counts, 'issues')} | "
            f"{_github_open_index_count(counts, 'prs')} | {counts.get('beads_issues', 0)} | "
            f"{counts.get('beads_ready', 0)} | {counts.get('beads_blocked', 0)} | "
            f"{project['artifact_count']} | "
            f"{_fmt_bytes(project['total_bytes'])} | `{overview_link}` | `{audit_link}` | `{manifest_link}` |"
        )
    lines.extend(
        (
            "",
            "## Attention Summary",
            "",
            "| Project | Large artifacts | Agent review | Agent archive/generated | Beads blocked |",
            "| --- | ---: | ---: | ---: | ---: |",
        )
    )
    for project in projects:
        attention = (project.get("overview") or {}).get("attention") or {}
        lines.append(
            f"| `{project['name']}` | {len(attention.get('large_artifacts') or [])} | "
            f"{attention.get('agent_review_entries', 0)} | "
            f"{_fmt_bytes(int(attention.get('agent_archive_or_generated_bytes') or 0))} | "
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
                f"| `{name}` | {bucket['files']:,} | {bucket.get('lines')} | "
                f"{bucket.get('code')} | {bucket.get('comments')} |"
            )
        inline = project.get("inline_rust_tests") or {}
        if inline.get("blocks"):
            lines.append(
                f"| `inline-rust-tests` | {inline['files']:,} | {inline['lines']:,} | n/a | n/a |"
            )
        lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path.name, md_path.name


def _github_open_index_count(counts: Mapping[str, Any], kind: str) -> str:
    observed = counts.get(f"{kind}_open", 0)
    if counts.get(f"{kind}_open_current") is not None:
        return str(observed)
    coverage = counts.get(f"{kind}_open_count_coverage")
    if coverage == "possibly_truncated":
        return f"unknown (at least {observed} observed)"
    return f"unknown (local {observed})"


# ═══════════════════════════════════════════════════════════════════════════════
# Per-repo builder (parallel slices within repo)
# ═══════════════════════════════════════════════════════════════════════════════


def _print_project_summary(completed: int, total: int, result: dict[str, Any]) -> None:
    name = str(result.get("project", "?"))
    status = str(result.get("status", "?"))
    elapsed = float(result.get("elapsed_s", 0) or 0)
    state = "complete" if status == "generated" else status
    with _print_lock:
        _print(
            f"\n[bold]Completed {completed}/{total}: {name} {state}[/bold]  "
            f"[dim]{elapsed:.1f}s[/dim]"
        )
        for line in (result.get("log_lines") or []) if chisel_options.active_options.xml else (result.get("errors") or []):
            _print(line)


def _build_one_impl(
    plan: RepoPlan,
    output_root: Path,
    repomix_bin: str,
    generated_at: str,
    slice_workers: int,
    report_builder: Callable[..., Any] | None = None,
) -> dict:
    """Build all slices, current-tree sidecars, and all-refs git history for one repo."""
    log: list[str] = []
    _build_state_local.log = log
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
    previous_tasks_path = out_dir / "owners/tasks.json"
    previous_tasks = previous_tasks_path.read_bytes() if previous_tasks_path.is_file() else None
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if previous_tasks is not None:
        (out_dir / "owners").mkdir(exist_ok=True)
        (out_dir / "owners/task-baseline.json").write_bytes(previous_tasks)
    from lynchpin.sources.chisel_snapshots import capture_catalogue, verify_snapshot
    from lynchpin.sources.chisel_package import captured_sidecars, run_view, verify_history_bundle
    from .chisel_package import evidence_outputs
    from lynchpin.sources.chisel_metrics import build_metrics
    from lynchpin.sources.chisel_history import build_history, freeze_refs, delivery_history

    capture_started = time.perf_counter()
    capture_started_at = dt.datetime.now(dt.timezone.utc).isoformat()
    _set_stage(plan.name, "capture source inventory", True)
    inventory = capture_catalogue(
        plan,
        out_dir,
        chisel_options.active_options,
        default_ignore=DEFAULT_IGNORE,
        scratchpad_include=_SCRATCHPAD_INCLUDE,
        accelerant_include=_ACCELERANT_INCLUDE,
        accelerant_ignore=_ACCELERANT_IGNORE,
    )
    capture_elapsed = round(time.perf_counter() - capture_started, 3)
    _set_stage(plan.name, "capture source inventory", False)
    log.append(f"  ✓ capture source inventory ({capture_elapsed:.1f}s)")
    git = _git_state(plan.path)
    git = {**git, "commit": inventory.revision, "dirty": inventory.dirty}
    if (out_dir / "snapshots.json").exists():
        primary_ref = json.loads((out_dir / "snapshots.json").read_text())["snapshots"][0]["ref"]
        git = {**git, "checkout_branch": git.get("branch"), "branch": primary_ref}
    from lynchpin.core.config import get_config
    cfg = get_config()
    cache_dir = cfg.chisel_cache() / plan.name
    scratch = cfg.chisel_scratch()
    scratch.mkdir(mode=0o700, parents=True, exist_ok=True)
    frozen_history = freeze_refs(plan.path, scratch) if "history" in chisel_options.active_options.datasets else None
    history_plan = replace(plan, path=Path(frozen_history.name)) if frozen_history else plan

    def metrics_stage():
        build_metrics(inventory, out_dir)
        paths = list(out_dir.glob(f"{plan.name}-tokei-stats.*"))
        return [p.name for p in paths], sum(p.stat().st_size for p in paths)

    def history_stage():
        build_history(
            history_plan.path,
            out_dir,
            project=plan.name,
            revision="HEAD",
            cache_dir=cache_dir / "history",
            frozen=True,
        )
        if (out_dir / "snapshots.json").exists():
            delivery_history(history_plan.path, out_dir, plan.name)
        paths = list(out_dir.glob(f"{plan.name}-growth*"))
        return [p.name for p in paths], sum(p.stat().st_size for p in paths)

    _emit(
        log,
        f"[bold]{plan.name}[/bold] [dim]{plan.path}[/dim] "
        f"{git['branch']} @ {git['commit'][:8]} "
        f"({len(plan.slices)} configured slices; captured snapshot {inventory.snapshot_id[:12]})",
    )
    log.append(f"  {len(inventory.files)} inventory records; {slice_workers} slice workers")

    slices_done: list[tuple[str, int]] = []
    errors: list[str] = []
    stage_timings: list[dict[str, Any]] = [{
        "stage": "capture", "label": plan.name, "started_at": capture_started_at,
        "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "elapsed_s": capture_elapsed, "queue_wait_s": 0.0,
    }]
    stage_timings_lock = threading.Lock()
    _build_state_local.stage_timings = stage_timings

    # ── Run everything in parallel within the repo ──
    with ThreadPoolExecutor(max_workers=slice_workers) as ex:
        futures: dict = {}

        def submit(kind: str, label: str, fn, *args):
            dataset = {"git-log": "history", "growth-analysis": "history", "sidecars": "history",
                       "issues": "trackers", "prs": "trackers", "beads": "trackers", "tokei-stats": "metrics"}.get(kind, "source")
            if dataset not in chisel_options.active_options.datasets:
                return
            if kind == "git-log" and not chisel_options.active_options.xml:
                return
            queued_at = time.perf_counter()

            def run_logged():
                started_at = dt.datetime.now(dt.timezone.utc)
                started = time.perf_counter()
                timing: dict[str, Any] = {
                    "stage": kind,
                    "label": label,
                    "started_at": started_at.isoformat(),
                    "queue_wait_s": round(started - queued_at, 3),
                }
                _stage_timing_local.current = timing
                stage_label = f"{kind} {label}"
                _set_stage(plan.name, stage_label, True)
                log.append(f"  → {stage_label}")
                try:
                    result = fn(*args)
                    return result
                except Exception as exc:
                    timing["error"] = str(exc)
                    raise
                finally:
                    finished_at = dt.datetime.now(dt.timezone.utc)
                    timing["finished_at"] = finished_at.isoformat()
                    timing["elapsed_s"] = round(time.perf_counter() - started, 3)
                    _stage_timing_local.current = None
                    _set_stage(plan.name, stage_label, False)
                    with stage_timings_lock:
                        stage_timings.append(dict(timing))
                    marker = "✗" if timing.get("error") else "✓"
                    suffix = f": {timing['error']}" if timing.get("error") else ""
                    log.append(f"  {marker} {stage_label} ({timing['elapsed_s']:.1f}s){suffix}")

            f = ex.submit(run_logged)
            futures[f] = (kind, label)

        # Every XML view consumes the exact captured membership.
        for slice in (plan.slices if chisel_options.active_options.xml else ()):
            submit(
                "slice",
                slice.name,
                run_view,
                repomix_bin,
                out_dir,
                plan,
                inventory,
                slice.name,
                git,
                generated_at,
                log,
            )
        if plan.compressed and chisel_options.active_options.xml:
            submit(
                "compressed",
                plan.name,
                lambda: run_view(
                    repomix_bin,
                    out_dir,
                    plan,
                    inventory,
                    "compressed",
                    git,
                    generated_at,
                    log,
                    compressed=True,
                ),
            )
        for special in (("scratchpad", "accelerants") if chisel_options.active_options.xml else ()):
            if any(special in row.included_by for row in inventory.files):
                submit(
                    special,
                    plan.name,
                    run_view,
                    repomix_bin,
                    out_dir,
                    plan,
                    inventory,
                    special,
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
        submit("sidecars", plan.name, captured_sidecars, history_plan, inventory, out_dir, log)

        submit("tokei-stats", plan.name, metrics_stage)
        submit("growth-analysis", plan.name, history_stage)

        # Local-state ignore audit.
        submit("ignore-audit", plan.name, _generate_ignore_audit, plan, out_dir, log)

        # Agent workspace layout and cleanup candidate audit.
        submit("agent-audit", plan.name, _generate_agent_audit, plan, out_dir, log)

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
        beads_files_done: list[str] = []
        beads_bytes = 0
        beads_context: dict[str, Any] = {"available": False}
        snapshot_audit_files_done: list[str] = []

        for future in as_completed(futures):
            kind, label = futures[future]
            try:
                result = future.result()
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
                elif kind == "beads":
                    names, size, beads_context = result
                    beads_files_done.extend(names)
                    beads_bytes += size
            except Exception as e:
                msg = str(e)
                errors.append(f"{kind}: {msg}")
                _emit(log, f"  [red]✗[/red] {kind}: {msg}")

    if errors:
        raise RuntimeError("; ".join(errors))

    # ── Extra copies (after repomix finishes) ──
    _copy_extras(replace(plan, path=inventory.root), out_dir, log)

    # Navigation is generated after owner records (including Beads) exist.
    if not errors and "history" in chisel_options.active_options.datasets:
        verify_history_bundle(plan, inventory, out_dir)
    stage_timings.extend(evidence_outputs(
        plan, inventory, out_dir, cache_dir, log, report_builder=report_builder,
    ))
    verify_snapshot(inventory)
    gitlog_commits = _read_json_file(out_dir / "history/coverage.json").get("commit_count", gitlog_commits)

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
        pending_artifact_names=(
            f"{plan.name}-overview.json",
            f"{plan.name}-overview.md",
            f"{plan.name}-snapshot-audit.json",
            f"{plan.name}-snapshot-audit.md",
            f"{plan.name}-manifest.json",
        ),
        log=log,
    )
    snapshot_audit_files_done, _snapshot_audit_bytes = _generate_snapshot_audit(
        plan,
        out_dir,
        generated_at,
        previous_manifest=previous_manifest,
        pending_artifact_names=(
            f"{plan.name}-snapshot-audit.json",
            f"{plan.name}-snapshot-audit.md",
            f"{plan.name}-manifest.json",
        ),
        log=log,
    )

    # ── Manifest after all per-project artifacts exist ──
    manifest_name, manifest_bytes = _write_project_manifest(
        plan, out_dir, generated_at, git, xml_errors, log
    )

    elapsed = (dt.datetime.now() - t0).total_seconds()
    total_bytes = sum(
        path.stat().st_size for path in out_dir.rglob("*") if path.is_file()
    )

    return {
        "project": plan.name,
        "status": "partial" if errors or xml_errors else "generated",
        "snapshot_id": inventory.snapshot_id,
        "git": git,
        "slices": len(slices_done),
        "slice_names": [s[0] for s in slices_done],
        "sidecars": sidecars_done,
        "stats_files": stats_files_done,
        "growth_files": growth_files_done,
        "audit_files": audit_files_done,
        "agent_audit_files": agent_audit_files_done,
        "beads_files": beads_files_done,
        "beads_bytes": beads_bytes,
        "overview_files": overview_files_done,
        "snapshot_audit_files": snapshot_audit_files_done,
        "manifest": manifest_name,
        "combined_tar": None,
        "combined_tar_bytes": 0,
        "total_bytes": total_bytes,
        "inputs": {"files": len(inventory.files), "included_bytes": sum(row.size_bytes or 0 for row in inventory.files if row.included)},
        "cache": {"history_records_reused": _read_json_file(out_dir / "history/coverage.json").get("immutable_commit_cache_rows_reused"),
                  "structure": _read_json_file(out_dir / "structure/coverage.json").get("cache")},
        "issues_open": issues_open,
        "issues_closed": issues_closed,
        "prs_open": prs_open,
        "prs_merged": prs_merged,
        "gitlog_commits": gitlog_commits,
        "xml_valid": len(xml_errors) == 0,
        "xml_errors": xml_errors or None,
        "elapsed_s": round(elapsed, 1),
        "stage_timings": sorted(
            stage_timings,
            key=lambda row: (row["started_at"], row["stage"], row["label"]),
        ),
        "errors": errors or None,
        "log_lines": log,
    }


def _build_one(
    plan: RepoPlan,
    output_root: Path,
    repomix_bin: str,
    generated_at: str,
    slice_workers: int,
    report_builder: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    _build_state_local.log = []
    _build_state_local.stage_timings = []
    try:
        return _build_one_impl(plan, output_root, repomix_bin, generated_at, slice_workers, report_builder)
    except Exception as exc:
        log = list(_build_state_local.log)
        log.append(f"  [red]✗[/red] {plan.name}: {exc}")
        return {
            "project": plan.name,
            "status": "failed",
            "error": str(exc),
            "elapsed_s": round(time.perf_counter() - started, 1),
            "stage_timings": list(_build_state_local.stage_timings),
            "log_lines": log,
        }
    finally:
        with _progress_lock:
            _active_stages.pop(plan.name, None)
        _build_state_local.log = []
        _build_state_local.stage_timings = []


# ═══════════════════════════════════════════════════════════════════════════════
# Top-level orchestrator
# ═══════════════════════════════════════════════════════════════════════════════


_build_chisel_lock = threading.Lock()


def build_chisel_bundles(
    *,
    project_names: Sequence[str] | None = None,
    output_root: Path | None = None,
    max_workers: int = DEFAULT_MAX_WORKERS,
    options: chisel_options.BuildOptions | None = None,
    report_builder: Callable[..., Any] | None = None,
    verbose: bool = False,
) -> dict[str, Any]:
    """Build and publish packages; ``verbose`` also prints library warnings."""
    # GitHub materialization and subprocess cancellation retain process-wide
    # state. Different output roots must not race on those shared resources.
    if not _build_chisel_lock.acquire(blocking=False):
        raise RuntimeError("another Chisel build is active in this process")
    previous_options = chisel_options.active_options
    try:
        chisel_options.active_options = options or chisel_options.BuildOptions()
        return _publish_chisel_bundles(
            project_names=project_names,
            output_root=output_root,
            max_workers=max_workers,
            report_builder=report_builder,
            verbose=verbose,
        )
    finally:
        chisel_options.active_options = previous_options
        _build_chisel_lock.release()


def _publish_chisel_bundles(
    *,
    project_names: Sequence[str] | None = None,
    output_root: Path | None = None,
    max_workers: int = DEFAULT_MAX_WORKERS,
    report_builder: Callable[..., Any] | None = None,
    verbose: bool = False,
) -> dict[str, Any]:
    """Publish one complete selected generation, preserving the last good one.

    Warnings raised during the build go to a log beside the output root, never
    inside the published packages.
    """
    from .chisel_package import build_portfolio
    from lynchpin.sources.chisel_publication import publish_candidate, publish_project_homes, staged_publication
    from lynchpin.sources.code_snapshots import code_snapshots_path

    started = time.perf_counter()
    root = (output_root or _default_output_root()).resolve()
    canonical = root == code_snapshots_path().resolve()
    names = list(project_names) if project_names is not None else list(chisel_options.DEFAULT_PROJECTS)
    unknown = set(names) - REPO_PLANS.keys()
    if unknown:
        raise ValueError(f"unknown projects: {', '.join(sorted(unknown))}")
    warning_log = root.parent / f".{root.name}.chisel-warnings.log"
    echo = (lambda line: _print_live(f"warning: {line}", markup=False)) if verbose else None
    with captured_warnings(warning_log, echo=echo) as warnings_seen, staged_publication(root) as candidate:
        if canonical:
            from lynchpin.sources.chisel_cache import copy_file
            for name in names:
                previous = code_snapshots_path(name)
                if previous.is_dir():
                    shutil.copytree(previous, candidate / name, copy_function=copy_file)
        for name in names:
            (candidate / f"{name}-all.tar.gz").unlink(missing_ok=True)
        result = _build_chisel_candidate(
            project_names=names,
            output_root=candidate,
            max_workers=max_workers,
            display_root=root,
            report_builder=report_builder,
        )
        successful = all(
            r.get("status") == "generated" for r in result["projects"].values()
        )
        if successful:
            plans = [REPO_PLANS[name] for name in names]
            (candidate / "portfolio-all.tar.gz").unlink(missing_ok=True)
            _print_live(f"Publishing to {root}")
            with chisel_terminal.timed_step("portfolio attachment archive"):
                result["portfolio"] = build_portfolio(
                    candidate,
                    plans,
                    result["projects"],
                    result["generated_at"],
                )
            with chisel_terminal.timed_step("publication validation"):
                if canonical:
                    result["project_paths"] = publish_project_homes(candidate, root, names)
                else:
                    publish_candidate(candidate, root, names)
        result["published"] = successful
        result["output_root"] = str(root)
        result["total_elapsed_s"] = round(time.perf_counter() - started, 1)
        if warnings_seen.count:
            _print_live(
                f"[dim]Library warnings: {warnings_seen.count} written to {warning_log}"
                f"{'' if verbose else ' (--verbose prints them)'}[/dim]"
            )
        if successful:
            _print_live(f"[green]Published[/green] in {result['total_elapsed_s']:.1f}s: {root}")
        else:
            _print_live(
                f"[yellow]Not published[/yellow] ({result['total_elapsed_s']:.1f}s): candidate incomplete; "
                f"previous packages retained at {root}"
            )
        for row in result["projects"].values():
            row["published"] = successful
        return result


def _build_chisel_candidate(
    *,
    project_names: Sequence[str] | None = None,
    output_root: Path | None = None,
    max_workers: int = DEFAULT_MAX_WORKERS,
    display_root: Path | None = None,
    report_builder: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    from lynchpin.sources.chisel_context import reset_context_cache

    reset_context_cache()
    global _github_context_index, _github_context_manifest, _github_context_ready
    _abort_event.clear()
    build_started = time.perf_counter()
    _github_context_index = None
    _github_context_manifest = None
    _github_context_ready = (
        None  # reset per-run so repeated calls in the same process work
    )
    repomix_bin = _require_repomix() if chisel_options.active_options.xml else ""
    repomix_ver = _repomix_version(repomix_bin) if repomix_bin else "not requested"
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
        plans = [REPO_PLANS[name] for name in chisel_options.DEFAULT_PROJECTS]

    repo_workers = min(max(1, max_workers), max(1, len(plans)))
    slice_workers = DEFAULT_SLICE_WORKERS

    for line in chisel_terminal.header_lines(
        plans,
        output_root=display_root or output_root,
        xml_version=repomix_ver,
        repo_workers=repo_workers,
        slice_workers=slice_workers,
        repomix_slots=DEFAULT_REPOMIX_WORKERS,
    ):
        _print(line)
    _print()
    preflight_started = time.perf_counter()
    _ensure_chisel_prerequisites(plans)
    preflight_elapsed = round(time.perf_counter() - preflight_started, 1)
    _print()

    results: dict[str, Any] = {}
    with _progress_lock:
        _active_stages.clear()
    ex = ThreadPoolExecutor(max_workers=repo_workers)
    futures = {
        ex.submit(
            _build_one, plan, output_root, repomix_bin, generated_at, slice_workers, report_builder
        ): plan.name
        for plan in plans
    }
    progress = chisel_terminal.ProgressLine()
    try:
        completed = 0
        pending = set(futures)
        while pending:
            done, pending = wait(pending, timeout=progress.poll_seconds, return_when=FIRST_COMPLETED)
            if not done:
                with _progress_lock:
                    active = {name: set(stages) for name, stages in _active_stages.items()}
                progress.update(chisel_terminal.progress_text(completed, len(plans), active))
                continue
            for future in done:
                name = futures[future]
                completed += 1
                try:
                    results[name] = future.result()
                except Exception as e:
                    results[name] = {
                        "project": name,
                        "status": "failed",
                        "error": str(e),
                        "log_lines": [f"  [red]✗[/red] {name}: {e}"],
                    }
                logs = output_root / "logs"
                logs.mkdir(exist_ok=True)
                (logs / f"{name}.log").write_text("\n".join(results[name].get("log_lines") or []) + "\n")
                results[name]["log"] = f"logs/{name}.log"
                _print_project_summary(completed, len(plans), results[name])
    except KeyboardInterrupt:
        progress.close()
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
    finally:
        progress.close()

    growth_portfolio = _write_growth_portfolio(output_root, plans, generated_at)
    total_elapsed = round(time.perf_counter() - build_started, 1)
    _print(f"Wrote growth/README.md ({len(growth_portfolio['files'])} artifacts)")

    counts_by_project = {
        plan.name: _read_json_file(output_root / plan.name / f"{plan.name}-overview.json").get("counts") or {}
        for plan in plans
        if results.get(plan.name, {}).get("status") in chisel_terminal.MEASURED_STATUSES
    }
    rows, total_row, statuses = chisel_terminal.summary_rows(
        plans, results, counts_by_project,
        xml_requested=chisel_options.active_options.xml, total_elapsed=total_elapsed,
    )
    total_bytes = sum(int(results.get(plan.name, {}).get("total_bytes", 0) or 0) for plan in plans)
    _print()
    if chisel_terminal.interactive():
        table = Table(
            title=f"Chisel {generated_at}", title_style="bold", title_justify="left",
            box=None, padding=(0, 1), pad_edge=False,
        )
        for column in chisel_terminal.SUMMARY_COLUMNS:
            table.add_column(
                column,
                justify="right" if column in chisel_terminal.SUMMARY_RIGHT_ALIGNED else "left",
                # A narrow terminal wraps the count phrases instead of eliding digits.
                no_wrap=column not in {"Issues", "PRs"},
                style="bold" if column == "Project" else None,
            )
        for row, status in zip(rows, statuses):
            style = chisel_terminal.status_style(status)
            table.add_row(row[0], f"[{style}]{row[1]}[/{style}]", *row[2:])
        table.add_section()
        table.add_row(f"[bold]{total_row[0]}[/bold]", *total_row[1:])
        _console.print(table)  # type: ignore[possibly-undefined]  # Table imported with rich
    else:
        # Logs and pipes get fixed-width text: rich would fit the table to 80
        # columns and elide cells.
        _print(f"Chisel {generated_at}", markup=False)
        for line in chisel_terminal.render_plain_table(rows, total_row):
            _print(line, markup=False)
    for line in chisel_terminal.summary_legend(counts_by_project, _github_context_manifest):
        _print(f"[dim]{line}[/dim]")

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
    elif all(results.get(plan.name, {}).get("status") == "generated" for plan in plans):
        _print("\n[green]All XML outputs well-formed.[/green]")
    else:
        _print("\n[yellow]XML validation incomplete because one or more projects failed.[/yellow]")

    index_json, index_md = _write_root_index(
        output_root,
        plans,
        results,
        generated_at,
        repomix_ver,
        total_elapsed,
        preflight_elapsed=preflight_elapsed,
    )
    _print(f"Wrote {index_json} and {index_md}")
    if display_root is None:
        _print(f"[dim]Done: {output_root}[/dim]")

    return {
        "generated_at": generated_at,
        "output_root": str(output_root),
        "repomix_version": repomix_ver,
        "total_elapsed_s": total_elapsed,
        "preflight_elapsed_s": preflight_elapsed,
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
