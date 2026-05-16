from __future__ import annotations

from typing import Any


def string_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, list | tuple):
        return tuple(str(item) for item in value)
    return ()


def dict_or_empty(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def list_or_empty(value: object) -> list[Any]:
    return value if isinstance(value, list) else []
