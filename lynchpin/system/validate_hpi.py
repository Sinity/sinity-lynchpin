from __future__ import annotations

from typing import Callable, Optional, Tuple

import typer

ACTIVE_HPI_MODULES: tuple[str, ...] = (
    "my.coding.commits",
    "my.calendar.holidays",
    "my.fbmessenger",
    "my.smscalls",
    "my.sleep.manual",
    "my.money",
    "my.webhistory",
    "my.browser",
    "my.google.takeout.parser",
    "my.goodreads",
    "my.spotify.gdpr",
    "my.activitywatch",
    "my.activitywatch.active_window",
    "my.atuin",
)


def _select_hpi_modules(
    *,
    modules: list[str],
    registry: dict[str, Callable[[], Tuple[Optional[int], str]]],
) -> list[str]:
    if modules:
        selected = list(dict.fromkeys(modules))
    else:
        selected = list(ACTIVE_HPI_MODULES)

    unknown = [name for name in selected if name not in registry]
    if unknown:
        joined = ", ".join(sorted(unknown))
        raise typer.BadParameter(f"Unknown HPI module(s): {joined}", param_hint="--module")
    return selected
