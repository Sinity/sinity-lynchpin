"""Content-keyed memoization for analysis DAG steps.

A :class:`~lynchpin.analysis.core.dag.Step` may declare a ``fingerprint``
callable returning a stable identity of its inputs — e.g. a git ``HEAD`` sha
plus the analysis code version. When the current fingerprint matches the one
recorded after the step's last *successful* run, the step's output is
byte-identical, so the step can be skipped.

This is what lets the bulk materialization run collapse to "only what actually
changed" (the dominant cost is full-recompute of immutable derived data such as
full-repository git-history metrics), which in turn makes a frequent / near-real-time
refresh cadence cheap.

Safety: a step is only ever skipped when it carries a fingerprint AND that
fingerprint matches a prior successful run. Steps without a fingerprint always
run, so adoption is incremental and conservative — a missing or uncomputable
fingerprint degrades to "run", never to a stale skip.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lynchpin.core.config import get_config

_STORE_NAME = ".step_fingerprints.json"


def fingerprint_store_path(local_root: Path | None = None) -> Path:
    """Return the JSON sidecar that records each step's last-successful fingerprint."""

    root = local_root if local_root is not None else get_config().local_root
    return root / "generated" / "analysis" / _STORE_NAME


def load_fingerprints(path: Path | None = None) -> dict[str, str]:
    """Load the recorded step→fingerprint map (empty if absent/corrupt)."""

    store = path or fingerprint_store_path()
    try:
        data = json.loads(store.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): value for key, value in data.items() if isinstance(value, str)}


def record_fingerprints(updates: dict[str, str], path: Path | None = None) -> None:
    """Merge ``updates`` into the recorded fingerprint map (atomic write)."""

    if not updates:
        return
    store = path or fingerprint_store_path()
    merged = load_fingerprints(store)
    merged.update(updates)
    store.parent.mkdir(parents=True, exist_ok=True)
    tmp = store.with_name(store.name + ".tmp")
    tmp.write_text(json.dumps(merged, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(store)


def compute_fingerprints(steps: dict[str, Any]) -> dict[str, str]:
    """Compute current fingerprints for the steps that declare one.

    Steps without a ``fingerprint`` callable, or whose callable raises or returns
    a falsy value, are omitted — they will run unconditionally.
    """

    current: dict[str, str] = {}
    for name, step in steps.items():
        fingerprint = getattr(step, "fingerprint", None)
        if fingerprint is None:
            continue
        try:
            value = fingerprint()
        except Exception:
            continue  # cannot fingerprint → leave out so the step runs
        if value:
            current[name] = str(value)
    return current


def memoized_skips(current: dict[str, str], stored: dict[str, str]) -> set[str]:
    """Return step names whose current fingerprint matches the recorded one."""

    return {name for name, value in current.items() if stored.get(name) == value}
