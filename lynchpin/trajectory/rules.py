from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..core.projects import ALL_PROJECTS
from .signal import TrajectorySignal

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


@dataclass(frozen=True)
class SignalAttribution:
    mode: str
    mode_confidence: float
    project: Optional[str]
    project_confidence: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class AttributedSignal:
    signal: TrajectorySignal
    mode: str
    mode_confidence: float
    project: Optional[str]
    project_confidence: float
    reasons: tuple[str, ...]

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
            }
        )
        return payload


def classify_signals(signals: Iterable[TrajectorySignal]) -> list[AttributedSignal]:
    return [classify_signal(signal) for signal in signals]


def classify_signal(signal: TrajectorySignal) -> AttributedSignal:
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

    if signal.kind == "afk":
        mode, mode_confidence = "recovery", 1.0
        reasons.append("afk_status")
    elif signal.source == "git.commit":
        mode, mode_confidence = "coding", 1.0
        reasons.append("git_commit")
    elif _matches_domain(domain, _AI_DOMAINS) or signal.source == "chatlog.transcript":
        mode, mode_confidence = "chat", 0.95
        reasons.append("ai_chat")
    elif _matches_domain(domain, _MEDIA_DOMAINS) or app in _MEDIA_APPS:
        mode, mode_confidence = "media", 0.9
        reasons.append("media_surface")
    elif _matches_domain(domain, _SOCIAL_DOMAINS):
        mode, mode_confidence = "social", 0.85
        reasons.append("social_surface")
    elif _matches_domain(domain, _ADMIN_DOMAINS):
        mode, mode_confidence = "admin", 0.85
        reasons.append("admin_surface")
    elif app in _WRITING_APPS or _contains_any(text, _WRITING_TERMS):
        mode, mode_confidence = "writing", 0.8
        reasons.append("writing_surface")
    elif _contains_any(text, _PLANNING_TERMS):
        mode, mode_confidence = "planning", 0.75
        reasons.append("planning_terms")
    elif _matches_domain(domain, _RESEARCH_DOMAINS) or signal.kind == "web":
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

    return AttributedSignal(
        signal=signal,
        mode=mode,
        mode_confidence=round(mode_confidence, 3),
        project=project,
        project_confidence=round(project_confidence, 3),
        reasons=tuple(dict.fromkeys(reasons)),
    )


def mode_family(mode: str) -> str:
    if mode in {"coding", "shell"}:
        return "coding"
    if mode in {"research", "chat", "writing", "planning"}:
        return "sensemaking"
    return mode


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


def _project_from_path(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if "://" in text and not text.startswith("file://"):
        return None
    if not text.startswith(("/", "~", ".")):
        return None
    normalized = text.replace("\\", "/")
    if normalized.startswith("/realm/project/"):
        project_name = normalized[len("/realm/project/") :].split("/", 1)[0]
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
    for entry in ALL_PROJECTS.values():
        project_path = Path(entry.path).expanduser().resolve(strict=False)
        if path == project_path or project_path in path.parents:
            return entry.name
    return None


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


def _contains_any(text: str, candidates: set[str]) -> bool:
    return any(candidate in text for candidate in candidates)
