"""Signal attribution and topic extraction rules."""

from __future__ import annotations

import functools
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..core.projects import ALL_PROJECTS
from . import ActivitySignal

_TERMINAL_APPS = {"kitty", "foot", "wezterm", "alacritty"}
_EDITOR_APPS = _TERMINAL_APPS | {"code", "codium", "emacs", "nvim", "vim", "zed", "cursor", "pycharm"}
_WRITING_APPS = {"obsidian", "logseq", "typora", "marktext", "zettlr"}
_MEDIA_APPS = {"spotify", "mpv", "vlc"}
_RESEARCH_DOMAINS = {
    "github.com",
    "docs.rs",
    "readthedocs.io",
    "stackoverflow.com",
    "wikipedia.org",
    "developer.mozilla.org",
    "arxiv.org",
    "search.brave.com",
    "google.com",
}
_AI_DOMAINS = {"chat.openai.com", "chatgpt.com", "claude.ai", "anthropic.com", "platform.openai.com"}
_MEDIA_DOMAINS = {"youtube.com", "music.youtube.com", "spotify.com", "twitch.tv", "netflix.com"}
_SOCIAL_DOMAINS = {"reddit.com", "x.com", "twitter.com", "facebook.com", "messenger.com", "wykop.pl"}
_ADMIN_DOMAINS = {"mail.google.com", "calendar.google.com", "bankmillennium.pl", "mbank.pl", "revolut.com"}
_PLANNING_TERMS = {"todo", "plan", "roadmap", "backlog", "agenda"}
_WRITING_TERMS = {"draft", "essay", "note", "journal", "narrative"}
_SHELL_TERMS = {"ls", "cd", "pwd", "mkdir", "cp", "mv", "git status"}
_PROJECT_PATTERNS = [
    (name, re.compile(rf"(?<![a-z0-9]){re.escape(name.lower())}(?![a-z0-9])"))
    for name in sorted(ALL_PROJECTS, key=len, reverse=True)
]
# Pre-resolved project paths — computed once at import, reused for every signal
_PROJECT_RESOLVED_PATHS: list[tuple[str, Path]] = [
    (entry.name, Path(entry.path).expanduser().resolve(strict=False))
    for entry in ALL_PROJECTS.values()
]

_WORK_EVENT_TOPIC_BOOSTS: dict[str, tuple[str, float]] = {
    "implementation": ("coding", 2.5),
    "debugging":      ("testing", 2.0),
    "documentation":  ("writing", 2.0),
    "research":       ("research", 2.5),
    "configuration":  ("infra", 2.0),
    "review":         ("git", 1.5),
    "refactoring":    ("coding", 2.0),
    "data_analysis":  ("data", 2.0),
    "conversation":   ("ai", 1.5),
}
_WORK_EVENT_LANGUAGE_TOPICS: frozenset[str] = frozenset({"nix", "rust", "python", "typescript"})


@dataclass(frozen=True)
class SignalAttribution:
    mode: str
    mode_confidence: float
    project: Optional[str]
    project_confidence: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class AttributedSignal:
    signal: ActivitySignal
    mode: str
    mode_confidence: float
    project: Optional[str]
    project_confidence: float
    reasons: tuple[str, ...]
    topic: Optional[str] = None
    topic_confidence: float = 0.0
    topic_scores: tuple[tuple[str, float], ...] = ()  # cached multi-topic scores

    @property
    def signal_id(self) -> str:
        return self.signal.signal_id

    @property
    def source(self) -> str:
        return self.signal.source

    @property
    def kind(self) -> str:
        return self.signal.kind

    @property
    def start(self):
        return self.signal.start

    @property
    def end(self):
        return self.signal.end

    @property
    def duration_seconds(self) -> float:
        return self.signal.duration_seconds

    @property
    def app(self) -> Optional[str]:
        return self.signal.app

    @property
    def title(self) -> Optional[str]:
        return self.signal.title

    @property
    def url(self) -> Optional[str]:
        return self.signal.url

    @property
    def domain(self) -> Optional[str]:
        return self.signal.domain

    @property
    def cwd(self) -> Optional[str]:
        return self.signal.cwd

    @property
    def detail(self) -> Optional[str]:
        return self.signal.detail

    @property
    def evidence(self) -> dict[str, object]:
        return self.signal.evidence

    def to_dict(self) -> dict[str, object]:
        payload = self.signal.to_dict()
        payload.update(
            {
                "mode": self.mode,
                "mode_confidence": self.mode_confidence,
                "project": self.project,
                "project_confidence": self.project_confidence,
                "reasons": list(self.reasons),
                "topic": self.topic,
                "topic_confidence": self.topic_confidence,
            }
        )
        return payload


_POLYLOGUE_WORK_EVENT_MODE_MAP = {
    "planning": "planning",
    "implementation": "coding",
    "debugging": "coding",
    "review": "coding",
    "testing": "coding",
    "research": "research",
    "configuration": "coding",
    "documentation": "writing",
    "refactoring": "coding",
    "data_analysis": "research",
    "conversation": "chat",
}

_TOPIC_DOMAIN: dict[str, list[tuple[str, float]]] = {
    "nix": [("nix", 2.0), ("nixos", 2.5), ("flake", 2.5), ("devshell", 3.0), ("home-manager", 3.0), ("nixpkgs", 3.0), ("nix-shell", 3.0)],
    "rust": [("rust", 1.5), ("cargo", 2.5), ("rustc", 2.5), (".rs", 1.5), ("crate", 2.5), ("tokio", 3.0), ("serde", 3.0), ("rustup", 2.5), ("clippy", 2.5)],
    "python": [("python", 1.0), ("pytest", 2.5), ("pip", 1.5), (".py", 1.5), ("pandas", 2.5), ("polars", 3.0), ("mypy", 2.5), ("ruff", 2.0), ("uv", 1.5)],
    "typescript": [("typescript", 2.0), (".ts", 1.0), (".tsx", 1.5), ("npm", 1.5), ("deno", 2.5), ("node", 1.0), ("bun", 2.0), ("eslint", 2.0)],
    "duckdb": [("duckdb", 3.0), ("parquet", 2.5), ("warehouse", 2.0), ("arrow", 2.0)],
    "docker": [("docker", 2.5), ("container", 1.5), ("dockerfile", 3.0), ("podman", 2.5), ("compose", 1.5)],
    "web": [("html", 1.5), ("css", 1.5), ("browser", 1.0), ("http", 1.0), ("api", 1.0), ("rest", 1.5), ("graphql", 2.5), ("fetch", 1.0)],
    "infra": [("deploy", 2.0), ("ci", 1.5), ("terraform", 3.0), ("ansible", 3.0), ("k8s", 3.0), ("kubernetes", 3.0), ("systemd", 2.0)],
}

_TOPIC_ACTIVITY: dict[str, list[tuple[str, float]]] = {
    "ai": [("llm", 2.5), ("claude", 2.0), ("gpt", 2.0), ("openai", 2.0), ("anthropic", 2.5), ("model", 1.0), ("prompt", 1.5), ("embedding", 2.5), ("agent", 1.5)],
    "data": [("analysis", 1.0), ("csv", 1.5), ("sql", 1.5), ("query", 1.0), ("pipeline", 1.0), ("etl", 2.5), ("dataset", 2.0)],
    "testing": [("test", 1.0), ("spec", 1.5), ("assert", 2.0), ("mock", 2.0), ("fixture", 2.5), ("coverage", 1.5)],
    "git": [("git", 1.0), ("commit", 1.5), ("branch", 1.0), ("merge", 1.5), ("rebase", 2.0), ("stash", 1.5)],
    "writing": [("draft", 1.5), ("essay", 2.0), ("note", 1.0), ("journal", 1.5), ("narrative", 2.0), ("doc", 1.0), ("readme", 1.5)],
    "planning": [("todo", 1.5), ("plan", 1.5), ("roadmap", 2.0), ("backlog", 2.0), ("agenda", 1.5), ("design", 1.5)],
    "research": [("arxiv", 3.0), ("paper", 1.5), ("survey", 2.0), ("benchmark", 2.0), ("explore", 1.0), ("investigate", 1.5)],
}

_TOPIC_KEYWORDS: dict[str, list[tuple[str, float]]] = {**_TOPIC_DOMAIN, **_TOPIC_ACTIVITY}

_TOPIC_VARIANTS: dict[str, str] = {
    "nixos": "nix", "nixpkgs": "nix", "nix-shell": "nix", "home-manager": "nix",
    "cargo": "rust", "rustc": "rust", "clippy": "rust", "rustup": "rust",
    "pytest": "python", "mypy": "python", "ruff": "python",
    "npm": "typescript", "deno": "typescript", "bun": "typescript", "eslint": "typescript",
    "parquet": "duckdb", "arrow": "duckdb",
    "dockerfile": "docker", "podman": "docker", "compose": "docker",
    "terraform": "infra", "ansible": "infra", "k8s": "infra", "kubernetes": "infra",
    "claude": "ai", "gpt": "ai", "openai": "ai", "anthropic": "ai", "embedding": "ai",
    "sql": "data", "csv": "data", "etl": "data",
    "draft": "writing", "essay": "writing", "narrative": "writing",
    "roadmap": "planning", "backlog": "planning",
}


def normalize_topic(raw: str) -> str:
    """Fold variant names to canonical topic keys."""
    lower = raw.strip().lower()
    return _TOPIC_VARIANTS.get(lower, lower)


@functools.lru_cache(maxsize=16384)
def _extract_topics_for_text(text: str, we_kind: Optional[str]) -> tuple[tuple[str, float], ...]:
    """Pure cached kernel: score all topics for a given (text, we_kind) pair."""
    we_boost_target, we_boost_amount = _WORK_EVENT_TOPIC_BOOSTS.get(we_kind or "", (None, 0.0))
    candidates: list[tuple[str, float]] = []
    for topic, keywords in _TOPIC_KEYWORDS.items():
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


def _extract_topics_multi(signal: ActivitySignal) -> list[tuple[str, float]]:
    """Return all matching topics with scores, ranked by confidence descending."""
    text = " ".join(
        part for part in [signal.title, signal.detail, signal.cwd, signal.url, signal.domain]
        if part
    ).lower()
    if not text:
        return []

    evidence = signal.evidence or {}
    file_paths = evidence.get("file_paths")
    if isinstance(file_paths, (list, tuple)):
        text += " " + " ".join(str(p) for p in file_paths)

    we_kind = evidence.get("work_event_kind")
    return list(_extract_topics_for_text(text, we_kind if isinstance(we_kind, str) else None))


def classify_chain_topics(
    signals: list["AttributedSignal"],
) -> tuple[Optional[str], float, list[tuple[str, float]]]:
    """Aggregate topic attribution across chain signals with source-diversity boost.

    Returns (dominant_topic, topic_confidence, ranked_topics).
    ranked_topics is a list of (topic, seconds) for all detected topics.
    """
    from collections import Counter as _Counter

    topic_seconds: _Counter[str] = _Counter()
    topic_source_sets: dict[str, set[str]] = {}

    for signal in signals:
        weight = max(signal.duration_seconds, 1.0)
        # All signals go through classify_signal, so topic_scores is always set.
        # Empty tuple () means "no topics" — do not fall back to _extract_topics_multi.
        topics = signal.topic_scores
        if not topics:
            if signal.topic:
                topic_seconds[signal.topic] += weight
                topic_source_sets.setdefault(signal.topic, set()).add(signal.source)
            continue
        for topic, _conf in topics:
            topic_seconds[topic] += weight
            topic_source_sets.setdefault(topic, set()).add(signal.source)

    if not topic_seconds:
        return None, 0.0, []

    # Source-diversity confidence boost: topics seen from 2+ sources get 1.2x weight
    for topic in list(topic_seconds):
        if len(topic_source_sets.get(topic, set())) >= 2:
            topic_seconds[topic] *= 1.2

    ranked = sorted(topic_seconds.items(), key=lambda item: (-item[1], item[0]))
    dominant = ranked[0][0]
    total_weight = sum(topic_seconds.values())
    confidence = min(ranked[0][1] / total_weight + 0.3, 0.95) if total_weight > 0 else 0.0
    return dominant, round(confidence, 2), [(t, round(s, 3)) for t, s in ranked]


def _classify_topic(signal: ActivitySignal) -> tuple[Optional[str], float]:
    """Derive a topic from signal text content via weighted keyword scoring."""
    candidates = _extract_topics_multi(signal)
    if candidates:
        return candidates[0]
    return None, 0.0


def classify_signals(signals: Iterable[ActivitySignal]) -> list[AttributedSignal]:
    return [classify_signal(signal) for signal in signals]


def classify_signal(signal: ActivitySignal) -> AttributedSignal:
    reasons: list[str] = []
    project = None
    project_confidence = 0.0

    project_match = _project_from_values(
        signal.project_hint,
        signal.cwd,
        signal.url,
        signal.title,
        signal.detail,
        signal.app,
    )
    if project_match:
        project, project_confidence, project_reason = project_match
        reasons.append(project_reason)

    mode = "unknown"
    mode_confidence = 0.3
    domain = (signal.domain or "").lower()
    app = (signal.app or "").lower()
    text = " ".join(part for part in [signal.title, signal.detail, signal.cwd, signal.url] if part).lower()

    # Definitive source-based classification
    if signal.kind == "afk":
        mode, mode_confidence = "recovery", 1.0
        reasons.append("afk_status")
    elif signal.source == "git.commit":
        mode, mode_confidence = "coding", 1.0
        reasons.append("git_commit")
    # Polylogue work event evidence — highest-confidence semantic extraction
    elif signal.source == "polylogue.session" and signal.evidence.get("work_event_kind"):
        we_kind = str(signal.evidence["work_event_kind"])
        mode = _POLYLOGUE_WORK_EVENT_MODE_MAP.get(we_kind, "chat")
        mode_confidence = 0.9
        reasons.append(f"work_event_{we_kind}")
    # mode_hint from signal source (polylogue sessions without work events, or other hints)
    elif signal.mode_hint and signal.mode_hint not in {"unknown", None}:
        mode = signal.mode_hint
        mode_confidence = 0.85
        reasons.append("signal_mode_hint")
    elif _matches_domain_or_text(domain, text, _AI_DOMAINS) or signal.source in {"chatlog.transcript", "polylogue.session"}:
        mode, mode_confidence = "chat", 0.95
        reasons.append("ai_chat")
    elif _matches_domain_or_text(domain, text, _MEDIA_DOMAINS) or app in _MEDIA_APPS:
        mode, mode_confidence = "media", 0.9
        reasons.append("media_surface")
    elif _matches_domain_or_text(domain, text, _SOCIAL_DOMAINS):
        mode, mode_confidence = "social", 0.85
        reasons.append("social_surface")
    elif _matches_domain_or_text(domain, text, _ADMIN_DOMAINS):
        mode, mode_confidence = "admin", 0.85
        reasons.append("admin_surface")
    elif app in _WRITING_APPS or _contains_any(text, _WRITING_TERMS):
        mode, mode_confidence = "writing", 0.8
        reasons.append("writing_surface")
    elif _contains_any(text, _PLANNING_TERMS):
        mode, mode_confidence = "planning", 0.75
        reasons.append("planning_terms")
    elif _matches_domain_or_text(domain, text, _RESEARCH_DOMAINS) or signal.kind == "web":
        mode, mode_confidence = "research", 0.75
        reasons.append("research_surface")
    elif project and (signal.source.startswith("instrumentation.") or signal.source == "atuin.command"):
        mode, mode_confidence = "coding", 0.9
        reasons.append("project_terminal")
    elif app in _EDITOR_APPS:
        mode, mode_confidence = "coding", 0.8 if project else 0.65
        reasons.append("editor_surface")
    elif signal.source.startswith("instrumentation.") or signal.source == "atuin.command":
        if _contains_any(text, _SHELL_TERMS):
            mode, mode_confidence = "shell", 0.7
            reasons.append("shell_terms")
        else:
            mode, mode_confidence = ("coding", 0.65) if project else ("shell", 0.55)
            reasons.append("terminal_default")
    elif project:
        mode, mode_confidence = "coding", 0.6
        reasons.append("project_hint")
    elif app:
        mode, mode_confidence = "web", 0.4
        reasons.append("surface_fallback")

    topic_scores = tuple(_extract_topics_multi(signal))
    topic, topic_confidence = (topic_scores[0] if topic_scores else (None, 0.0))

    return AttributedSignal(
        signal=signal,
        mode=mode,
        mode_confidence=round(mode_confidence, 3),
        project=project,
        project_confidence=round(project_confidence, 3),
        reasons=tuple(dict.fromkeys(reasons)),
        topic=topic,
        topic_confidence=topic_confidence,
        topic_scores=topic_scores,
    )


def mode_family(mode: str) -> str:
    if mode in {"coding", "shell"}:
        return "coding"
    if mode in {"research", "chat", "writing", "planning"}:
        return "sensemaking"
    return mode


@functools.lru_cache(maxsize=8192)
def _project_from_values(*values: object) -> Optional[tuple[str, float, str]]:
    for value in values:
        project = _project_from_path(value)
        if project:
            return project, 1.0, "project_path"
    for value in values:
        project = _project_from_text(value)
        if project:
            return project, 0.7, "project_text"
    return None


@functools.lru_cache(maxsize=4096)
def _project_from_path_str(text: str) -> Optional[str]:
    if not text:
        return None
    if "://" in text and not text.startswith("file://"):
        return None
    if not text.startswith(("/", "~", ".")):
        return None
    normalized = text.replace("\\", "/")
    if normalized.startswith("/realm/project/"):
        project_name = normalized[len("/realm/project/"):].split("/", 1)[0]
        if project_name in ALL_PROJECTS:
            return project_name
    try:
        path = Path(text).expanduser()
    except RuntimeError:
        path = Path(text)
    try:
        path = path.resolve(strict=False)
    except OSError:
        return None
    # Use pre-resolved project paths — no per-call Path.resolve() overhead
    for name, project_path in _PROJECT_RESOLVED_PATHS:
        if path == project_path or project_path in path.parents:
            return name
    return None


def _project_from_path(value: object) -> Optional[str]:
    if value is None:
        return None
    return _project_from_path_str(str(value).strip())


@functools.lru_cache(maxsize=4096)
def _project_from_text(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    for name, pattern in _PROJECT_PATTERNS:
        if pattern.search(text):
            return name
    return None


def _matches_domain(domain: str, candidates: set[str]) -> bool:
    if not domain:
        return False
    return any(domain == candidate or domain.endswith(f".{candidate}") for candidate in candidates)


def _matches_domain_or_text(domain: str, text: str, candidates: set[str]) -> bool:
    if _matches_domain(domain, candidates):
        return True
    return any(candidate in text for candidate in candidates)


def _contains_any(text: str, candidates: set[str]) -> bool:
    return any(candidate in text for candidate in candidates)
