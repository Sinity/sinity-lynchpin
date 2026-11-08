"""Bounded, read-only application health observations, separate from dataset coverage."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import re
import subprocess
from typing import Any

from lynchpin.core.config import get_config


def application_health_report(*, cfg: Any = None) -> tuple[dict[str, Any], ...]:
    cfg = cfg if cfg is not None else get_config()
    if not getattr(cfg, "application_health_enabled", False):
        return ()
    timeout = cfg.application_health_timeout_seconds
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("application health timeout must be positive and finite")
    return (_probe("sinex", ["sinexctl", "--json", "status"], timeout),)


def _probe(application: str, argv: list[str], timeout: float) -> dict[str, Any]:
    """Run `sinexctl --json status`: healthy when the daemon answers and its writer runs."""
    result: dict[str, Any] = {
        "application": application, "probe_status": "unavailable", "health": "unknown",
        "observed_at": datetime.now(timezone.utc).isoformat(), "exit_code": None,
        "reason": "", "details": {},
    }
    try:
        completed = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                                   text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return {**result, "reason": f"health command timed out after {timeout:g}s"}
    except (OSError, UnicodeError) as exc:
        return {**result, "reason": f"health command unavailable: {exc}"}
    result["exit_code"] = completed.returncode
    try:
        if completed.returncode != 0:
            raise ValueError(f"status command exited {completed.returncode}")
        status = json.loads(completed.stdout)
        if not isinstance(status, dict) or not isinstance(status.get("commit_seq"), int):
            raise ValueError("status response lacks a commit position")
        if not isinstance(status.get("writer"), bool):
            raise ValueError("status response lacks the writer state")
    except (ValueError, AttributeError, TypeError) as exc:
        error = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", completed.stderr).strip()[:400]
        return {**result, "reason": f"invalid or unavailable status response: {exc}" + (f"; {error}" if error else "")}
    state = "healthy" if status["writer"] else "degraded"
    search = status.get("search_index") or {}
    details = {"commit_seq": status["commit_seq"], "writer": status["writer"],
               "search_index_behind": search.get("behind")}
    return {**result, "probe_status": "available", "health": state,
            "reason": "status probe indicates " + state, "details": details}
