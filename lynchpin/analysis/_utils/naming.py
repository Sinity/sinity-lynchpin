"""Shared naming and identifier normalization helpers for analysis artifacts."""

from __future__ import annotations

import re


NON_WORD_RE = re.compile(r'[^a-zA-Z0-9._-]+')


def safe_key(value: str | None) -> str:
    key = NON_WORD_RE.sub('_', (value or '').strip()).strip('_.')
    return key[:80] or 'unknown'
