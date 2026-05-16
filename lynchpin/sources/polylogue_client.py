"""Cached Polylogue facade client boundary."""

from __future__ import annotations

import warnings
from functools import lru_cache
from pathlib import Path
from typing import Any

warnings.filterwarnings(
    "ignore",
    message=r'Field "model_name" in .* has conflict with protected namespace "model_"',
    category=UserWarning,
    module=r"pydantic\._internal\._fields",
)


def _default_polylogue_db_path() -> Path:
    from ..core.config import get_config

    return get_config().polylogue_db


@lru_cache(maxsize=1)
def _polylogue_client() -> Any:
    """Return process-singleton SyncPolylogue facade.

    Lynchpin consumes Polylogue's typed Python facade rather than its archive
    database schema.
    """
    from polylogue.api.sync import SyncPolylogue

    return SyncPolylogue(db_path=_default_polylogue_db_path())


def _reset_polylogue_client_for_tests() -> None:
    """Invalidate the process-singleton SyncPolylogue client.

    Tests that point at a fixture database must call this before and after
    swapping ``get_config().polylogue_db``.
    """
    _polylogue_client.cache_clear()
