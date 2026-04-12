"""Shared filesystem and JSON helpers for analysis modules."""

from __future__ import annotations

import json
from os import PathLike
from pathlib import Path
from typing import Any

from ...core.config import get_config


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
