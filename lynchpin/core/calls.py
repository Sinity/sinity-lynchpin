"""Shared call adapters for optional source reads.

Lynchpin analyses often combine many independent local data sources. One stale
export or malformed provider file should not abort a broad retrospective when
the caller explicitly chose a soft-failure read.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import TypeVar

T = TypeVar("T")
D = TypeVar("D")


class _Unset:
    pass


_UNSET = _Unset()


def realize_iterable(value: T) -> T | list[object]:
    """Materialize one-shot iterators while leaving ordinary containers intact."""
    if isinstance(value, Iterator):
        return list(value)
    return value


def safe_source_call(
    fn: Callable[..., T],
    *args: object,
    default: D | _Unset = _UNSET,
    on_error: Callable[[Exception], None] | None = None,
    **kwargs: object,
) -> T | D | list[object]:
    """Call a source function and return ``default`` when it fails.

    This is intentionally opt-in. Source modules should still raise meaningful
    errors by default; broad composite views use this helper only when partial
    results are better than aborting the whole report.
    """
    try:
        return realize_iterable(fn(*args, **kwargs))
    except Exception as exc:
        if on_error is not None:
            on_error(exc)
        if not isinstance(default, _Unset):
            return default
        return []
