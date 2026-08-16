"""Read the durable cross-worktree Polylogue verification-run history.

Polylogue's ``devtools verify`` already appends one record per invocation to a
checkout-independent JSONL under its XDG state dir, using ``O_APPEND`` plus an
advisory lock so concurrent worktrees cannot interleave. Detailed per-run
artifacts stay checkout-local under ``.cache/verify/runs/`` and are pruned;
this compact index is the part that survives, which makes it the right thing
to promote into the substrate.

Reading it here is what turns "did the test suite regress?" from an argument
into a query: the checkout-local receipts for a run from three days ago are
already gone, but its history row is not.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = ["iter_verify_runs", "verify_history_path"]

_ENV_OVERRIDE = "POLYLOGUE_VERIFY_HISTORY_PATH"
_DEFAULT_RELATIVE = Path("polylogue/devtools/verify-history.jsonl")


def verify_history_path() -> Path:
    """Locate the durable history, honouring an explicit override then XDG."""
    override = os.environ.get(_ENV_OVERRIDE, "").strip()
    if override:
        return Path(override).expanduser()
    state_home = os.environ.get("XDG_STATE_HOME", "").strip()
    root = Path(state_home).expanduser() if state_home else Path.home() / ".local/state"
    return root / _DEFAULT_RELATIVE


def _timestamp(entry: dict[str, Any]) -> datetime | None:
    for field in ("finished_at", "timestamp", "started_at"):
        raw = entry.get(field)
        if not isinstance(raw, str) or not raw:
            continue
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _number(entry: dict[str, Any], *fields: str) -> float | None:
    for field in fields:
        value = entry.get(field)
        if isinstance(value, bool):
            continue
        if isinstance(value, int | float):
            return float(value)
    return None


def _pytest_counts(entry: dict[str, Any]) -> dict[str, Any]:
    """Project the pytest aggregate, which only newer records carry."""
    aggregate = entry.get("pytest_aggregate")
    if not isinstance(aggregate, dict):
        return {}
    outcomes = aggregate.get("outcomes")
    outcomes = outcomes if isinstance(outcomes, dict) else {}
    return {
        "tests_passed": outcomes.get("passed"),
        "tests_failed": outcomes.get("failed"),
        "selected_count": aggregate.get("selected_union_count"),
        "terminal_count": aggregate.get("terminal_union_count"),
        "terminal_green": aggregate.get("terminal_green"),
        "complete_corpus_covered": aggregate.get("complete_corpus_covered"),
        "pytest_wall_s": _number(aggregate, "wall_s"),
    }


def _step_summary(entry: dict[str, Any]) -> dict[str, Any]:
    steps = entry.get("steps")
    steps = steps if isinstance(steps, list) else []
    slowest_name, slowest_s = None, 0.0
    failed = 0
    for step in steps:
        if not isinstance(step, dict):
            continue
        if isinstance(step.get("exit"), int) and step["exit"] != 0:
            failed += 1
        duration = step.get("duration_s")
        if isinstance(duration, int | float) and float(duration) > slowest_s:
            slowest_s, slowest_name = float(duration), step.get("name")
    return {
        "step_count": len(steps),
        "failed_step_count": failed,
        "slowest_step": slowest_name,
        "slowest_step_s": slowest_s or None,
    }


def iter_verify_runs(path: Path | None = None) -> Iterator[dict[str, Any]]:
    """Yield one normalized row per recorded verification invocation."""
    history = path or verify_history_path()
    if not history.is_file():
        return
    seen: set[str] = set()
    with history.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue
            run_id = entry.get("run_id")
            if not isinstance(run_id, str) or not run_id or run_id in seen:
                continue
            seen.add(run_id)
            checkout_root = entry.get("checkout_root")
            row: dict[str, Any] = {
                "run_id": run_id,
                "run_at": _timestamp(entry),
                "tier": entry.get("tier"),
                "git_head": entry.get("git_head"),
                "checkout_root": checkout_root,
                # A linked worktree reports its own checkout_root, so this is
                # what distinguishes a worktree lane's runs from the main one's.
                "checkout_name": Path(checkout_root).name
                if isinstance(checkout_root, str) and checkout_root
                else None,
                "worktree_fingerprint": entry.get("worktree_fingerprint"),
                "exit_code": entry.get("exit_code"),
                "status": entry.get("status"),
                "diagnosis": entry.get("diagnosis"),
                "duration_s": _number(entry, "total_duration_s", "duration_s"),
                "verification_scope": entry.get("verification_scope"),
                "release_baseline_allowed": entry.get("release_baseline_allowed"),
                "artifact_dir": entry.get("artifact_dir"),
            }
            row.update(_step_summary(entry))
            row.update(_pytest_counts(entry))
            yield row
