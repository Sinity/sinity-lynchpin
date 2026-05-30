"""Sinnix generated runtime inventory source.

Reads ``/etc/sinnix/runtime-inventory.json`` by default. Sinnix owns this
present-tense inventory of runtime classes, managed surfaces, and capture
outputs; Lynchpin treats it as reference context for interpreting machine
telemetry, not as a substrate fact table.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.config import LynchpinConfig
from ..core.errors import SchemaVersionError
from ..core.source import SourceReadiness


SCHEMA = "sinnix-runtime-inventory-v1"


@dataclass(frozen=True)
class SinnixRuntimeInventory:
    hostname: str
    classes: dict[str, Any]
    command_classes: dict[str, Any]
    environment_allow_list: tuple[str, ...]
    slices: dict[str, Any]
    surfaces: dict[str, Any]
    observed_services: tuple[dict[str, Any], ...]
    captures: tuple[dict[str, Any], ...]
    mounts: tuple[dict[str, Any], ...]
    backups: dict[str, Any]
    path: Path


def readiness(path: Path | None = None) -> SourceReadiness:
    inventory_path = path or LynchpinConfig.from_env().sinnix_runtime_inventory_json
    try:
        inventory = read_inventory(inventory_path)
    except FileNotFoundError:
        return SourceReadiness(
            status="missing",
            reason=f"{inventory_path} does not exist",
            path=inventory_path,
            row_count=0,
        )
    except (OSError, ValueError, SchemaVersionError) as exc:
        return SourceReadiness(
            status="error",
            reason=str(exc),
            path=inventory_path,
            row_count=0,
        )
    row_count = len(inventory.observed_services) + len(inventory.captures)
    return SourceReadiness(
        status="ok" if row_count else "empty",
        reason="" if row_count else "inventory has no observed service or capture rows",
        path=inventory_path,
        row_count=row_count,
    )


def read_inventory(path: Path | None = None) -> SinnixRuntimeInventory:
    inventory_path = path or LynchpinConfig.from_env().sinnix_runtime_inventory_json
    with inventory_path.open(encoding="utf-8") as fh:
        payload = json.load(fh)
    if not isinstance(payload, dict):
        raise ValueError(f"{inventory_path} is not a JSON object")
    schema = payload.get("schema")
    if schema != SCHEMA:
        raise SchemaVersionError(found=schema, expected=SCHEMA, source="sinnix_runtime_inventory")
    return SinnixRuntimeInventory(
        hostname=str(payload.get("hostname") or ""),
        classes=_dict_value(payload.get("classes"), "classes"),
        command_classes=_dict_value(payload.get("commandClasses"), "commandClasses"),
        environment_allow_list=tuple(
            str(value) for value in _list_value(payload.get("environmentAllowList"), "environmentAllowList")
        ),
        slices=_dict_value(payload.get("slices"), "slices"),
        surfaces=_dict_value(payload.get("surfaces"), "surfaces"),
        observed_services=tuple(_dict_rows(payload.get("observedServices"), "observedServices")),
        captures=tuple(_dict_rows(payload.get("captures"), "captures")),
        mounts=tuple(_dict_rows(payload.get("mounts"), "mounts")),
        backups=_dict_value(payload.get("backups"), "backups"),
        path=inventory_path,
    )


def _dict_rows(value: object, field: str) -> list[dict[str, Any]]:
    rows = _list_value(value, field)
    dict_rows: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            raise ValueError(f"runtime inventory field {field!r} contains a non-object row")
        dict_rows.append(dict(item))
    return dict_rows


def _list_value(value: object, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"runtime inventory field {field!r} must be a list")
    return list(value)


def _dict_value(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"runtime inventory field {field!r} must be an object")
    return dict(value)


__all__ = [
    "SCHEMA",
    "SinnixRuntimeInventory",
    "read_inventory",
    "readiness",
]
