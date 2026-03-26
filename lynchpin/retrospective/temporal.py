"""Temporal scale utilities for narrative hierarchy navigation."""

from __future__ import annotations

from .narrative import NarrativeKind
from ..periods import child_keys as period_child_keys
from ..periods import child_scale as period_child_scale
from ..periods import next_key as period_next_key
from ..periods import prior_key as period_prior_key

SCALE_HIERARCHY: list[NarrativeKind] = [
    NarrativeKind.day,
    NarrativeKind.week,
    NarrativeKind.month,
    NarrativeKind.quarter,
    NarrativeKind.half,
    NarrativeKind.year,
]


def child_scale(scale: NarrativeKind) -> NarrativeKind | None:
    child = period_child_scale(scale)
    if child is None:
        return None
    return NarrativeKind(child)


def child_keys(scale: NarrativeKind, key: str) -> list[str]:
    return period_child_keys(scale, key)


def prior_key(scale: NarrativeKind, key: str) -> str | None:
    return period_prior_key(scale, key)


def next_key(scale: NarrativeKind, key: str) -> str | None:
    return period_next_key(scale, key)
