"""In-Lynchpin rules layer for AW title classification.

The upstream `title_metadata` materialization (sourced from the GPT classifier
SQLite DB) covers only ~27% of recent window titles. The dominant misses are
trivially classifiable kitty terminal titles set by agent sessions: project
names, Claude Code session-prefix sentinels, and topic slugs.

This module supplies a *fallback* classifier that fires when the upstream
classmap has no entry for `hash_title(app, normalize_title(...))`. It emits
``TitleClassification`` with ``classification_source="rules-local"`` so
downstream consumers can distinguish it from the upstream rules/gpt sources.

Vocabulary is chosen to match values already present in the upstream
classifications, so existing aggregation code keeps working without changes.
"""
from __future__ import annotations

import re
from typing import Iterable

from ..core.projects import (
    _PROJECT_ALIASES,
    _PROJECT_CONTAINS_ALIASES,
    _PROJECT_PREFIX_ALIASES,
    ALL_PROJECTS,
)
from .title_metadata import TitleClassification, hash_title

__all__ = ["classify_title_via_rules"]


# Build the project-slug match set once. Includes canonical names, aliases,
# and prefix/contains stems. Lowercase-comparison.
def _build_project_slug_set() -> frozenset[str]:
    slugs: set[str] = set()
    for name, entry in ALL_PROJECTS.items():
        if entry.active:
            slugs.add(name.lower())
    for alias in _PROJECT_ALIASES:
        slugs.add(alias.lower())
    return frozenset(slugs)


_PROJECT_SLUGS = _build_project_slug_set()

_PROJECT_PREFIX_MAP: tuple[tuple[str, str], ...] = (
    *_PROJECT_PREFIX_ALIASES,
    *((p + "-", p) for p in (e.name for e in ALL_PROJECTS.values() if e.active)),
    # Alias-derived prefixes: "lynchpin-foo" → sinity-lynchpin, etc.
    *(
        (alias + "-", canonical)
        for alias, canonical in _PROJECT_ALIASES.items()
        if "-" not in alias and " " not in alias and alias != canonical
    ),
)

_PROJECT_CONTAINS_MAP: tuple[tuple[str, str], ...] = _PROJECT_CONTAINS_ALIASES

# Claude Code sets the kitty title to this sentinel when /xxx-style commands
# inject preamble text into the conversation. Whole title is sentinel text.
_CLAUDE_CAVEAT_PREFIX = "<local-command-caveat>"

# Agent session lifecycle markers. Each entry maps a normalized title token
# to the canonical (tool, activity) attribution. Keeps codex sessions
# correctly attributed to "codex" rather than blanket-tagged "claude-code".
_AGENT_LIFECYCLE_TOOL = {
    "start coding session": "claude-code",
    "claude-code:idle": "claude-code",
    "claude code": "claude-code",
    "codex resume --last": "codex",
    "codex": "codex",
}

# Claude Code topic slugs are kebab-case all-lowercase
# strings with multiple tokens. Threshold of >=2 dashes (3 tokens) catches the
# common "phased-issue-closure", "temporal-intelligence-activation" patterns
# without grabbing random hyphenated text.
_TOPIC_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+){2,}$")

# Claude Code "topic" titles: sentence-like, first-word capitalized, no dashes
# or slashes, length 8-100, contains a space. Set by Claude Code based on the
# conversation topic. Common pattern: "Create schema inventory document",
# "Analyze current I/O spike with observability".
_TOPIC_SENTENCE_RE = re.compile(
    r"^[A-Z][A-Za-z0-9]*(?:\s+[A-Za-z0-9/.\-]+){1,15}$"
)


def _project_for_title(normalized_title: str) -> str | None:
    """Best-effort project lookup for a normalized title string."""
    t = normalized_title.strip().lower()
    if not t:
        return None
    if t in _PROJECT_SLUGS:
        # Resolve alias to canonical via the alias dict (else identity).
        return _PROJECT_ALIASES.get(t, t)
    for prefix, canonical in _PROJECT_PREFIX_MAP:
        if t.startswith(prefix):
            return canonical
    for substring, canonical in _PROJECT_CONTAINS_MAP:
        if substring in t:
            return canonical
    return None


def classify_title_via_rules(
    app: str,
    raw_title: str,
    normalized_title: str,
) -> TitleClassification | None:
    """Return a TitleClassification if a rule fires, else None.

    Designed as a fallback when the upstream classmap has no entry. Output
    field vocabulary matches the upstream classifications (activity values
    like ``reading_code``, ``chatting_work``, ``planning``; mode values
    ``coding``, ``planning``; topic ``programming``; attention ``deep``).
    """
    if not app or not normalized_title:
        return None
    app_l = app.lower()
    nt = normalized_title.strip()
    nt_l = nt.lower()

    # Claude Code system-prefix marker. The whole title is this sentinel.
    if nt.startswith(_CLAUDE_CAVEAT_PREFIX):
        return _emit(
            app=app, raw_title=raw_title, normalized_title=normalized_title,
            activity="chatting_work", content_type="chat_interface",
            mode="coding", attention_level="deep",
            topic_category="programming",
            tool="claude-code", is_ai_tool=True, is_ai_active=True,
        )

    # Agent-session lifecycle text in the title. Differentiate codex vs
    # claude-code by the literal title; do not blanket-attribute.
    lifecycle_tool = _AGENT_LIFECYCLE_TOOL.get(nt_l)
    if lifecycle_tool is not None:
        return _emit(
            app=app, raw_title=raw_title, normalized_title=normalized_title,
            activity="chatting_work", content_type="chat_interface",
            mode="coding", attention_level="deep",
            topic_category="programming",
            tool=lifecycle_tool, is_ai_tool=True, is_ai_active=True,
        )

    # kitty + title == project slug: the operator's agent sessions set the
    # window title to the project they're working on.
    if app_l in ("kitty", "foot"):
        project = _project_for_title(nt)
        if project is not None:
            return _emit(
                app=app, raw_title=raw_title, normalized_title=normalized_title,
                activity="reading_code", content_type="code",
                mode="coding", attention_level="deep",
                topic_category="programming",
                subject=project,
            )

    # Topic slug: 3+ token kebab-case all-lowercase.
    # Restrict to terminal apps — apps with custom names like
    # "scratchpad-terminal" or "sinnix-captured-shell" emit those strings
    # as their own app name AND title, producing a false-positive "planning"
    # signal for what is just an app self-identifier.
    if app_l in ("kitty", "foot") and _TOPIC_SLUG_RE.match(nt_l):
        return _emit(
            app=app, raw_title=raw_title, normalized_title=normalized_title,
            activity="planning", content_type="documentation",
            mode="planning", attention_level="deep",
            topic_category="programming",
            confidence=0.6,
        )

    # kitty + Claude Code-style topic sentence ("Create schema inventory document").
    # Heuristic only fires for terminal apps and short, capitalized, no-slash text.
    if app_l in ("kitty", "foot") and 8 <= len(nt) <= 100 and _TOPIC_SENTENCE_RE.match(nt):
        return _emit(
            app=app, raw_title=raw_title, normalized_title=normalized_title,
            activity="chatting_work", content_type="chat_interface",
            mode="coding", attention_level="deep",
            topic_category="programming",
            tool="claude-code", is_ai_tool=True, is_ai_active=True,
            confidence=0.5,
        )

    return None


def _emit(
    *,
    app: str,
    raw_title: str,
    normalized_title: str,
    activity: str | None = None,
    subject: str | None = None,
    content_type: str | None = None,
    attention_level: str | None = None,
    topic_category: str | None = None,
    platform: str | None = None,
    mode: str | None = None,
    tool: str | None = None,
    is_ai_tool: bool | None = None,
    is_ai_active: bool | None = None,
    confidence: float = 0.7,
) -> TitleClassification:
    return TitleClassification(
        title_hash=hash_title(app, normalized_title),
        app=app,
        raw_title=raw_title,
        normalized_title=normalized_title,
        activity=activity,
        subject=subject,
        content_type=content_type,
        attention_level=attention_level,
        topic_category=topic_category,
        platform=platform,
        mode=mode,
        tool=tool,
        is_ai_tool=is_ai_tool,
        is_ai_active=is_ai_active,
        confidence=confidence,
        classification_source="rules-local",
    )


def classify_many(
    items: Iterable[tuple[str, str, str]],
) -> dict[str, TitleClassification]:
    """Convenience: classify a batch, keyed by title_hash."""
    out: dict[str, TitleClassification] = {}
    for app, raw_title, normalized_title in items:
        c = classify_title_via_rules(app, raw_title, normalized_title)
        if c is not None:
            out[c.title_hash] = c
    return out
