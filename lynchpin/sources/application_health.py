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
    sinex = ["sinexctl", "--timeout", str(max(1, math.ceil(timeout))), "--format", "json"]
    if cfg.sinex_runtime_target is not None:
        sinex += ["--runtime-target", str(cfg.sinex_runtime_target)]
    if cfg.sinex_health_ca_cert is not None:
        sinex += ["--ca-cert", str(cfg.sinex_health_ca_cert)]
    sinex += ["runtime", "health"]
    observations = []
    for name, argv in (("sinex", sinex), ("polylogue", ["polylogue", "status", "--json"])):
        observation = _probe(name, argv, timeout)
        observation["target"] = str(cfg.sinex_runtime_target) if name == "sinex" and cfg.sinex_runtime_target is not None else None
        observations.append(observation)
    return tuple(observations)



def _probe(application: str, argv: list[str], timeout: float) -> dict[str, Any]:
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
        payload = json.loads(completed.stdout)
        if not isinstance(payload, dict):
            raise ValueError("health response is not an object")
        if application == "sinex":
            view = payload.get("payload", {})
            if view.get("schema_version") != "sinex.runtime-health/v1":
                raise ValueError("unsupported Sinex health schema")
            health = view.get("health", {})
            if not isinstance(health.get("healthy"), bool):
                raise ValueError("Sinex healthy flag is missing or invalid")
            state = "healthy" if health["healthy"] else "degraded"
            details = {key: health.get(key) for key in ("healthy", "serving", "status")}
            details.update({key: payload.get(key) for key in ("runtime_target", "source_surface", "generated_at")})
        else:
            mode = payload.get("mode")
            store = payload.get("store")
            pid = payload.get("pid")
            if (not isinstance(payload.get("archive"), str)
                    or not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0
                    or not isinstance(payload.get("busy"), bool)
                    or not isinstance(mode, dict)
                    or mode.get("state") not in {"standalone", "primary", "blocked"}
                    or (store is not None and not isinstance(store, dict))):
                raise ValueError("Polylogue status fields are missing or invalid")
            pending = store.get("pending_inputs") if store is not None else None
            if store is not None and not isinstance(pending, list):
                raise ValueError("Polylogue pending inputs are missing or invalid")
            state = "degraded" if mode["state"] == "blocked" or store is None or pending else "healthy"
            details = {key: payload[key] for key in ("archive", "pid", "busy", "mode")}
            details["daemon_available"] = completed.returncode == 0
            details["store_available"] = store is not None
            details["pending_inputs"] = pending
        if completed.returncode != 0 and state == "healthy":
            raise ValueError("failed health command reported healthy")
    except (ValueError, AttributeError, TypeError) as exc:
        error = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", completed.stderr).strip()[:400]
        return {**result, "reason": f"invalid or unavailable health response: {exc}" + (f"; {error}" if error else "")}
    return {**result, "probe_status": "available", "health": state,
            "reason": "application reports " + state, "details": details}
