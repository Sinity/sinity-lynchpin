"""Project mention detection for free-text evidence surfaces."""

from __future__ import annotations

import re

PROJECT_MENTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?<![\w-])sinex-target-vision(?![\w-])", re.I), "sinex-target-vision"),
    (re.compile(r"(?<![\w-])target\s+vision(?![\w-])", re.I), "sinex-target-vision"),
    (re.compile(r"(?<![\w-])intercept-bounce(?![\w-])", re.I), "intercept-bounce"),
    (re.compile(r"(?<![\w-])scribe-tap(?![\w-])", re.I), "scribe-tap"),
    (re.compile(r"(?<![\w-])raw-log(?![\w-])", re.I), "knowledgebase"),
    (re.compile(r"(?<![\w-])raw\s+log(?![\w-])", re.I), "knowledgebase"),
    (re.compile(r"(?<![\w-])knowledgebase(?![\w-])", re.I), "knowledgebase"),
    (re.compile(r"(?<![\w-])narrativization(?![\w-])", re.I), "sinity-lynchpin"),
    (re.compile(r"(?<![\w-])lynchpin(?![\w-])", re.I), "sinity-lynchpin"),
    (re.compile(r"(?<![\w-])sinity-lynchpin(?![\w-])", re.I), "sinity-lynchpin"),
    (re.compile(r"(?<![\w-])polylogue(?![\w-])", re.I), "polylogue"),
    (re.compile(r"(?<![\w-])sinnix(?![\w-])", re.I), "sinnix"),
    (re.compile(r"(?<![\w-])sinex(?![\w-])", re.I), "sinex"),
)


def projects_mentioned_in_text(text: str) -> tuple[str, ...]:
    """Return canonical project names explicitly mentioned in text."""
    projects = []
    for pattern, project in PROJECT_MENTION_PATTERNS:
        if pattern.search(text):
            projects.append(project)
    return tuple(dict.fromkeys(projects))


__all__ = ["PROJECT_MENTION_PATTERNS", "projects_mentioned_in_text"]
