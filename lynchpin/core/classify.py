"""Mode, project, and topic classification from app/title/url/cwd/domain.

This is the domain knowledge that maps raw window/signal attributes to semantic
categories. The lookup tables are the actual substance; the cascade function
applies them in priority order.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .projects import ALL_PROJECTS

# ── Lookup tables (the real domain knowledge) ─────────────────────────────────

TERMINAL_APPS = {"kitty", "foot", "wezterm", "alacritty"}
EDITOR_APPS = TERMINAL_APPS | {"code", "codium", "emacs", "nvim", "vim", "zed", "cursor", "pycharm"}
WRITING_APPS = {"obsidian", "logseq", "typora", "marktext", "zettlr"}
MEDIA_APPS = {"spotify", "mpv", "vlc"}

RESEARCH_DOMAINS = {
    "github.com", "docs.rs", "readthedocs.io", "stackoverflow.com",
    "wikipedia.org", "developer.mozilla.org", "arxiv.org",
    "search.brave.com", "google.com",
}
AI_DOMAINS = {"chat.openai.com", "chatgpt.com", "claude.ai", "anthropic.com", "platform.openai.com"}
MEDIA_DOMAINS = {"youtube.com", "music.youtube.com", "spotify.com", "twitch.tv", "netflix.com"}
SOCIAL_DOMAINS = {"reddit.com", "x.com", "twitter.com", "facebook.com", "messenger.com", "wykop.pl"}
ADMIN_DOMAINS = {"mail.google.com", "calendar.google.com", "bankmillennium.pl", "mbank.pl", "revolut.com"}

PLANNING_TERMS = {"todo", "plan", "roadmap", "backlog", "agenda"}
WRITING_TERMS = {"draft", "essay", "note", "journal", "narrative"}
SHELL_TERMS = {"ls", "cd", "pwd", "mkdir", "cp", "mv", "git status"}

POLYLOGUE_WORK_EVENT_MODE_MAP = {
    "planning": "planning", "implementation": "coding", "debugging": "coding",
    "review": "coding", "testing": "coding", "research": "research",
    "configuration": "coding", "documentation": "writing",
    "refactoring": "coding", "data_analysis": "research", "conversation": "chat",
}

TOPIC_KEYWORDS: dict[str, list[tuple[str, float]]] = {
    "nix": [("nix", 2.0), ("nixos", 2.5), ("flake", 2.5), ("devshell", 3.0), ("home-manager", 3.0), ("nixpkgs", 3.0), ("nix-shell", 3.0)],
    "rust": [("rust", 1.5), ("cargo", 2.5), ("rustc", 2.5), (".rs", 1.5), ("crate", 2.5), ("tokio", 3.0), ("serde", 3.0), ("rustup", 2.5), ("clippy", 2.5)],
    "python": [("python", 1.0), ("pytest", 2.5), ("pip", 1.5), (".py", 1.5), ("pandas", 2.5), ("polars", 3.0), ("mypy", 2.5), ("ruff", 2.0), ("uv", 1.5)],
    "typescript": [("typescript", 2.0), (".ts", 1.0), (".tsx", 1.5), ("npm", 1.5), ("deno", 2.5), ("node", 1.0), ("bun", 2.0), ("eslint", 2.0)],
    "duckdb": [("duckdb", 3.0), ("parquet", 2.5), ("warehouse", 2.0), ("arrow", 2.0)],
    "docker": [("docker", 2.5), ("container", 1.5), ("dockerfile", 3.0), ("podman", 2.5), ("compose", 1.5)],
    "web": [("html", 1.5), ("css", 1.5), ("browser", 1.0), ("http", 1.0), ("api", 1.0), ("rest", 1.5), ("graphql", 2.5), ("fetch", 1.0)],
    "infra": [("deploy", 2.0), ("ci", 1.5), ("terraform", 3.0), ("ansible", 3.0), ("k8s", 3.0), ("kubernetes", 3.0), ("systemd", 2.0)],
    "ai": [("llm", 2.5), ("claude", 2.0), ("gpt", 2.0), ("openai", 2.0), ("anthropic", 2.5), ("model", 1.0), ("prompt", 1.5), ("embedding", 2.5), ("agent", 1.5)],
    "data": [("analysis", 1.0), ("csv", 1.5), ("sql", 1.5), ("query", 1.0), ("pipeline", 1.0), ("etl", 2.5), ("dataset", 2.0)],
    "testing": [("test", 1.0), ("spec", 1.5), ("assert", 2.0), ("mock", 2.0), ("fixture", 2.5), ("coverage", 1.5)],
    "git": [("git", 1.0), ("commit", 1.5), ("branch", 1.0), ("merge", 1.5), ("rebase", 2.0), ("stash", 1.5)],
    "writing": [("draft", 1.5), ("essay", 2.0), ("note", 1.0), ("journal", 1.5), ("narrative", 2.0), ("doc", 1.0), ("readme", 1.5)],
    "planning": [("todo", 1.5), ("plan", 1.5), ("roadmap", 2.0), ("backlog", 2.0), ("agenda", 1.5), ("design", 1.5)],
    "research": [("arxiv", 3.0), ("paper", 1.5), ("survey", 2.0), ("benchmark", 2.0), ("explore", 1.0), ("investigate", 1.5)],
}

_WORK_EVENT_TOPIC_BOOSTS: dict[str, tuple[str, float]] = {
    "implementation": ("coding", 2.5), "debugging": ("testing", 2.0),
    "documentation": ("writing", 2.0), "research": ("research", 2.5),
    "configuration": ("infra", 2.0), "review": ("git", 1.5),
    "refactoring": ("coding", 2.0), "data_analysis": ("data", 2.0),
    "conversation": ("ai", 1.5),
}
_WORK_EVENT_LANGUAGE_TOPICS = frozenset({"nix", "rust", "python", "typescript"})

_PROJECT_PATTERNS = [
    (name, re.compile(rf"(?<![a-z0-9]){re.escape(name.lower())}(?![a-z0-9])"))
    for name in sorted(ALL_PROJECTS, key=len, reverse=True)
]
_PROJECT_RESOLVED_PATHS: list[tuple[str, Path]] = [
    (entry.name, Path(entry.path).expanduser().resolve(strict=False))
    for entry in ALL_PROJECTS.values()
]


# ── Output type ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Attribution:
    mode: str
    project: str | None
    topic: str | None


# ── Classification ────────────────────────────────────────────────────────────


def classify(
    *,
    app: str = "",
    title: str = "",
    url: str = "",
    cwd: str = "",
    domain: str = "",
    source: str = "",
    kind: str = "",
    mode_hint: str | None = None,
    evidence: dict | None = None,
) -> Attribution:
    """Classify an activity event into (mode, project, topic).

    Priority cascade: source type → domain → app → title content → fallback.
    """
    evidence = evidence or {}
    project = resolve_project(cwd, title, url, app)

    domain_l = domain.lower()
    app_l = app.lower()
    text = " ".join(p for p in [title, cwd, url] if p).lower()

    # Mode classification — priority cascade
    if kind == "afk":
        mode = "recovery"
    elif source == "git.commit":
        mode = "coding"
    elif source == "polylogue.session" and evidence.get("work_event_kind"):
        mode = POLYLOGUE_WORK_EVENT_MODE_MAP.get(str(evidence["work_event_kind"]), "chat")
    elif mode_hint and mode_hint not in {"unknown", ""}:
        mode = mode_hint
    elif _matches_domain_or_text(domain_l, text, AI_DOMAINS) or source in {"chatlog.transcript", "polylogue.session"}:
        mode = "chat"
    elif _matches_domain_or_text(domain_l, text, MEDIA_DOMAINS) or app_l in MEDIA_APPS:
        mode = "media"
    elif _matches_domain_or_text(domain_l, text, SOCIAL_DOMAINS):
        mode = "social"
    elif _matches_domain_or_text(domain_l, text, ADMIN_DOMAINS):
        mode = "admin"
    elif app_l in WRITING_APPS or _contains_any(text, WRITING_TERMS):
        mode = "writing"
    elif _contains_any(text, PLANNING_TERMS):
        mode = "planning"
    elif _matches_domain_or_text(domain_l, text, RESEARCH_DOMAINS) or kind == "web":
        mode = "research"
    elif project and (source.startswith("instrumentation.") or source == "atuin.command"):
        mode = "coding"
    elif app_l in EDITOR_APPS:
        mode = "coding"
    elif source.startswith("instrumentation.") or source == "atuin.command":
        mode = "coding" if project else "shell"
    elif project:
        mode = "coding"
    elif app_l:
        mode = "web"
    else:
        mode = "unknown"

    # Topic classification — weighted keyword scoring
    topic = _extract_topic(text, evidence.get("work_event_kind"))

    return Attribution(mode=mode, project=project, topic=topic)


def resolve_project(*values: str | None) -> str | None:
    """Resolve any string (path, title, URL) to a project name. Single canonical implementation."""
    for value in values:
        if not value:
            continue
        result = _project_from_path(value)
        if result:
            return result
    for value in values:
        if not value:
            continue
        result = _project_from_text(value)
        if result:
            return result
    return None



# ── Topic extraction ──────────────────────────────────────────────────────────


@functools.lru_cache(maxsize=16384)
def _extract_topics_cached(text: str, we_kind: str | None) -> tuple[tuple[str, float], ...]:
    we_boost_target, we_boost_amount = _WORK_EVENT_TOPIC_BOOSTS.get(we_kind or "", (None, 0.0))
    candidates: list[tuple[str, float]] = []
    for topic, keywords in TOPIC_KEYWORDS.items():
        score = sum(weight for kw, weight in keywords if kw in text)
        if we_boost_target == "coding" and topic in _WORK_EVENT_LANGUAGE_TOPICS and score >= 1.0:
            score += 1.0
        elif we_boost_target == topic:
            score += we_boost_amount
        if we_kind == "configuration" and topic == "nix" and "nix" in text:
            score += 1.0
        if score >= 1.0:
            confidence = min(0.3 + score * 0.15, 0.95)
            candidates.append((topic, round(confidence, 2)))
    candidates.sort(key=lambda item: (-item[1], item[0]))
    return tuple(candidates)


def _extract_topic(text: str, we_kind: object = None) -> str | None:
    candidates = _extract_topics_cached(text, we_kind if isinstance(we_kind, str) else None)
    return candidates[0][0] if candidates else None


def extract_topics(text: str, we_kind: str | None = None) -> list[tuple[str, float]]:
    """Public: return all matching topics with scores for a text."""
    return list(_extract_topics_cached(text, we_kind))


# ── Project resolution ────────────────────────────────────────────────────────


@functools.lru_cache(maxsize=4096)
def _project_from_path(text: str) -> str | None:
    text = text.strip()
    if not text:
        return None
    if "://" in text and not text.startswith("file://"):
        return None
    normalized = text.replace("\\", "/")
    if "/realm/project/" in normalized:
        project_name = normalized.split("/realm/project/", 1)[1].split("/", 1)[0]
        if project_name in ALL_PROJECTS:
            return project_name

    if not text.startswith(("/", "~", ".")):
        return None
    if any(ch.isspace() for ch in text):
        return None

    raw_path = Path(text).expanduser()
    for name, project_path in _PROJECT_RESOLVED_PATHS:
        try:
            raw_path.relative_to(project_path)
        except ValueError:
            continue
        else:
            return name
    return None


@functools.lru_cache(maxsize=4096)
def _project_from_text(text: str) -> str | None:
    text = text.strip().lower()
    if not text:
        return None
    for name, pattern in _PROJECT_PATTERNS:
        if pattern.search(text):
            return name
    return None


# ── Helpers ───────────────────────────────────────────────────────────────────


def _matches_domain_or_text(domain: str, text: str, candidates: set[str]) -> bool:
    if domain:
        if any(domain == c or domain.endswith(f".{c}") for c in candidates):
            return True
    return any(c in text for c in candidates)


def _contains_any(text: str, candidates: set[str]) -> bool:
    return any(c in text for c in candidates)
