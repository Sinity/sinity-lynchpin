"""Shared filesystem and JSON helpers for analysis modules."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime, timezone
from os import PathLike
from pathlib import Path
from typing import Any, Callable, TypeVar, cast

from .config import get_config

T = TypeVar("T")


class MissingAnalysisArtifact(FileNotFoundError):
    """Required generated analysis artifact is absent."""


class MalformedAnalysisArtifact(ValueError):
    """Required generated analysis artifact is not a JSON object."""


def repo_root() -> str:
    return str(get_config().repo_root)


def analysis_root() -> str:
    return str(get_config().analysis_output_dir)


def _resolve(base: Path, path: str | PathLike[str]) -> str:
    candidate = Path(path)
    if candidate.is_absolute():
        return str(candidate)
    return str(base / candidate)


def resolve_repo_path(path: str | PathLike[str]) -> str:
    return _resolve(get_config().repo_root, path)


def resolve_analysis_path(path: str | PathLike[str]) -> str:
    return _resolve(get_config().analysis_output_dir, path)


def resolve_artifact_path(spec: dict[str, Any], artifact_name: str) -> str:
    return resolve_analysis_path(spec['artifacts'][artifact_name])


def load_json(path: str | PathLike[str]) -> Any:
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_json_if_exists(path: str | PathLike[str]) -> Any | None:
    if not Path(path).exists():
        return None
    return load_json(path)


def load_json_object(path: str | PathLike[str], *, label: str | None = None) -> dict[str, Any]:
    target = Path(path)
    name = label or str(target)
    if not target.exists():
        raise MissingAnalysisArtifact(f"{name} is missing: {target}")
    payload = load_json(target)
    if not isinstance(payload, dict):
        raise MalformedAnalysisArtifact(f"{name} is not a JSON object: {target}")
    return payload


def save_json(path: str | PathLike[str], payload: Any, sort_keys: bool = False) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2, sort_keys=sort_keys)


def save_text(path: str | PathLike[str], text: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('w', encoding='utf-8') as f:
        f.write(text)


def latest_mtime_iso(paths: Iterable[Path]) -> str | None:
    """Return the latest existing path mtime as local ISO text."""
    latest = max((path.stat().st_mtime for path in paths if path.exists()), default=None)
    if latest is None:
        return None
    return datetime.fromtimestamp(latest, timezone.utc).astimezone().isoformat()


def load_analysis_artifact(
    name: str,
    parser: Callable[[dict[str, Any]], T] | None = None,
) -> T | dict[str, Any] | None:
    """Load a named analysis artifact, returning None when absent or shaped wrong.

    Encapsulates the resolve_analysis_path + load_json_if_exists +
    isinstance(dict) + optional-parse pattern repeated across the
    analysis layer. The artifact directory is the configured
    analysis_output_dir; ``name`` is the basename (e.g.
    "current_state_context_pack.json").

    When ``parser`` is provided, returns ``parser(payload)`` after the
    dict check. When omitted, returns the raw dict. Returns None if the
    file is missing or the top-level JSON is not a dict (caller's
    responsibility to handle).
    """
    payload = load_json_if_exists(resolve_analysis_path(name))
    if not isinstance(payload, dict):
        return None
    if parser is None:
        return payload
    return parser(payload)


def load_materialized_analysis_artifact(
    name: str,
    *,
    parser: Callable[[dict[str, Any]], T] | None = None,
    materialization: dict[str, Any] | None = None,
) -> tuple[T | dict[str, Any] | None, dict[str, Any]]:
    """Load an analysis artifact after ensuring artifact products are usable."""

    materialization_json = dict(materialization or materialize_analysis_artifacts())
    target = Path(resolve_analysis_path(name))
    raw = load_json_if_exists(target)
    artifact_status = "ready" if isinstance(raw, dict) else "missing" if not target.exists() else "malformed"
    materialization_json.update({
        "requested_artifact": str(target),
        "requested_artifact_name": name,
        "requested_artifact_status": artifact_status,
    })

    if not isinstance(raw, dict):
        if materialization_json.get("status") in {"ready", "updated"}:
            materialization_json.update({
                "status": artifact_status,
                "reason": f"requested analysis artifact is {artifact_status}: {target}",
            })
        return None, materialization_json

    payload = cast(T | dict[str, Any], raw) if parser is None else parser(raw)
    return payload, materialization_json


def materialize_analysis_artifacts() -> dict[str, Any]:
    """Ensure the analysis artifact inventory once and return its status JSON."""

    from lynchpin.materialization import ensure_materialized

    return ensure_materialized("analysis_artifacts", cfg=get_config()).to_json()


def require_analysis_artifact(name: str) -> dict[str, Any]:
    """Load a required analysis artifact as a JSON object or raise typed errors."""
    return load_json_object(resolve_analysis_path(name), label=name)
