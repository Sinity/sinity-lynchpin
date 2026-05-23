"""Canonical typed Google Takeout product rows."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..core.config import get_config

__all__ = [
    "google_takeout_products_dir",
    "iter_assets",
    "iter_contacts",
    "iter_keep_notes",
    "iter_my_activity",
    "iter_play_store",
    "iter_purchases",
    "iter_tasks",
    "iter_youtube",
]


def google_takeout_products_dir() -> Path:
    return get_config().exports_root / "google/processed/takeout-products"


def iter_contacts() -> Iterator[dict[str, Any]]:
    yield from _iter_rows("contacts.ndjson")


def iter_keep_notes() -> Iterator[dict[str, Any]]:
    yield from _iter_rows("keep_notes.ndjson")


def iter_my_activity() -> Iterator[dict[str, Any]]:
    yield from _iter_rows("my_activity.ndjson")


def iter_play_store() -> Iterator[dict[str, Any]]:
    yield from _iter_rows("play_store.ndjson")


def iter_purchases() -> Iterator[dict[str, Any]]:
    yield from _iter_rows("purchases.ndjson")


def iter_tasks() -> Iterator[dict[str, Any]]:
    yield from _iter_rows("tasks.ndjson")


def iter_youtube() -> Iterator[dict[str, Any]]:
    yield from _iter_rows("youtube.ndjson")


def iter_assets() -> Iterator[dict[str, Any]]:
    yield from _iter_rows("assets.ndjson")


def _iter_rows(name: str) -> Iterator[dict[str, Any]]:
    path = google_takeout_products_dir() / name
    if not path.exists():
        raise FileNotFoundError(
            f"canonical Google Takeout product materialization is missing: {path}. "
            "Run `python -m lynchpin.ingest.google_takeout_products`."
        )
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                yield payload
