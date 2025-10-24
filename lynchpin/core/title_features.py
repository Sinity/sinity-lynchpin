"""Extract structured features from AW window titles.

Window titles contain rich structured information that the classify module's
keyword matching misses. This module parses titles into structured features
BEFORE classification, enabling much better project attribution and AI detection.

Key patterns:
- Claude Code spinners: ⠐/✳/◇/✦ + status + optional (project)
- Codex invocations: codex [flags] [resume] [prompt...]
- Terminal prompts: user@host:/realm/project/X/...
- Browser pages: Page Title - https://domain.com/path
- Editor: nvim /path/to/file
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

from .config import get_config
from .projects import canonical_project_name

__all__ = ["TitleFeatures", "extract_title_features"]

# ── Constants ──────────────────────────────────────────────────────────────

SPINNER_CHARS = frozenset(
    "⠁⠂⠃⠄⠅⠆⠇⠈⠉⠊⠋⠌⠍⠎⠏⠐⠑⠒⠓⠔⠕⠖⠗⠘⠙⠚⠛⠜⠝⠞⠟"
    "⠠⠡⠢⠣⠤⠥⠦⠧⠨⠩⠪⠫⠬⠭⠮⠯⠰⠱⠲⠳⠴⠵⠶⠷⠸⠹⠺⠻⠼⠽⠾⠿"
    "✳✦◇◆●○"
)

TERMINAL_APPS = frozenset({"kitty", "foot", "wezterm", "alacritty"})
BROWSER_APPS_PARTIAL = ("chrome", "firefox", "zen", "floorp")

# Known projects under /realm/project/
KNOWN_PROJECTS = frozenset({
    "sinex", "sinnix", "sinity-lynchpin", "polylogue", "knowledgebase",
    "sinex-target-vision", "intercept-bounce", "scribe-tap", "knowledge-extract", "pwrank",
})

# Claude Code status messages that have (project) in parens
_CLAUDE_PARENS_RE = re.compile(r'\(([a-z][\w-]*)\)\s*$')
# Realm project path
_REALM_PROJECT_RE = re.compile(r'/realm/project/([^/\s:;]+)')
# Domain from URL in browser title
_TITLE_URL_RE = re.compile(r' - https?://([^/\s]+)')
_URL_RE = re.compile(r'https?://([^/\s]+)')
# Terminal prompt
_PROMPT_RE = re.compile(r'^\w+@[\w-]+:(.+)$')

# Generic domain categories. Dataset-specific mappings are not tracked source
# data: they live in an optional local override file (see
# _load_domain_categories) under the gitignored local root.
_GENERIC_DOMAIN_CATEGORIES: dict[str, str] = {
    # AI tools
    "chatgpt.com": "ai", "claude.ai": "ai", "openai.com": "ai",
    "aistudio.google.com": "ai", "anthropic.com": "ai",
    "alignment.anthropic.com": "ai", "elevenlabs.io": "ai",
    # Code/docs
    "github.com": "code", "docs.rs": "docs", "crates.io": "docs",
    "stackoverflow.com": "docs", "doc.rust-lang.org": "docs",
    "rust-book.cs.brown.edu": "docs", "nixos.wiki": "docs",
    "nix.dev": "docs",
    # Reference
    "wikipedia.org": "reading",
    # Media
    "youtube.com": "media", "music.youtube.com": "media",
    "spotify.com": "media", "twitch.tv": "media",
}


def _load_domain_categories() -> dict[str, str]:
    """Merge generic categories with an optional local dataset-specific override.

    The override file (JSON, ``{domain: category}``) is entirely optional and
    lives under the configured data root (``derived_root``), outside the
    checkout, so dataset-specific mappings remain local.
    """
    merged = dict(_GENERIC_DOMAIN_CATEGORIES)
    try:
        override_path = get_config().derived_root / "local-config" / "title_domain_categories.json"
        if override_path.exists():
            overrides = json.loads(override_path.read_text(encoding="utf-8"))
            if isinstance(overrides, dict):
                merged.update({str(k): str(v) for k, v in overrides.items()})
    except (OSError, json.JSONDecodeError):
        pass
    return merged


# Domain categories
DOMAIN_CATEGORIES: dict[str, str] = _load_domain_categories()


@dataclass(frozen=True)
class TitleFeatures:
    """Structured features extracted from a window title."""
    app_kind: str              # terminal, browser, editor, media_player, system, other
    tool: Optional[str]        # codex, claude-code, nvim, bat, git, btop, etc.
    project: Optional[str]     # from /realm/project/X or Claude Code (project) parens
    domain: Optional[str]      # from URL in browser title
    domain_category: Optional[str]  # ai, code, docs, reading, social, media, admin
    is_ai_tool: bool           # codex/claude in title
    is_ai_active: bool         # spinner char present (AI is running, not just idle terminal)


def extract_title_features(app: str, title: str) -> TitleFeatures:
    """Extract structured features from an AW window event's app + title."""
    if not title:
        return TitleFeatures(
            app_kind=_app_kind(app), tool=None, project=None,
            domain=None, domain_category=None,
            is_ai_tool=False, is_ai_active=False,
        )

    # Terminal apps — richest structured data
    if app in TERMINAL_APPS:
        return _parse_terminal_title(app, title)

    # Browser apps — URL extraction
    if any(b in app for b in BROWSER_APPS_PARTIAL):
        return _parse_browser_title(app, title)

    # Media players
    if app in ("mpv", "vlc", "spotify"):
        return TitleFeatures(
            app_kind="media_player", tool=app, project=None,
            domain=None, domain_category=None,
            is_ai_tool=False, is_ai_active=False,
        )

    # Chat/comms apps
    if 'weechat' in app or 'discord' in app or 'slack' in app or 'telegram' in app:
        return TitleFeatures(
            app_kind="chat", tool=app.split('-')[-1] if '-' in app else app,
            project=None, domain=None, domain_category=None,
            is_ai_tool=False, is_ai_active=False,
        )

    # System apps
    if any(s in app for s in ('polkit', 'blueman', 'gcr-prompter', 'xdg-desktop-portal',
                               'rawlog-capture', 'clipse', 'antigravity', 'steam')):
        # Antigravity with project names
        project = None
        if 'antigravity' in app.lower():
            for known in KNOWN_PROJECTS:
                if known in title.lower():
                    project = known
                    break
        return TitleFeatures(
            app_kind="system", tool=app, project=project,
            domain=None, domain_category=None,
            is_ai_tool=False, is_ai_active=False,
        )

    # Editors
    if app in ('code', 'obsidian', 'logseq', 'typora', 'notes-scratch'):
        project = None
        m = _REALM_PROJECT_RE.search(title)
        if m:
            project = _normalize_project(m.group(1))
        elif 'knowledgebase' in title.lower():
            project = 'knowledgebase'
        return TitleFeatures(
            app_kind="editor", tool=app, project=project,
            domain=None, domain_category=None,
            is_ai_tool=False, is_ai_active=False,
        )

    # Default
    return TitleFeatures(
        app_kind=_app_kind(app), tool=None, project=None,
        domain=None, domain_category=None,
        is_ai_tool=False, is_ai_active=False,
    )


def _app_kind(app: str) -> str:
    if app in TERMINAL_APPS:
        return "terminal"
    if any(b in app for b in BROWSER_APPS_PARTIAL):
        return "browser"
    if app in ("mpv", "vlc", "spotify"):
        return "media_player"
    if app in ("obsidian", "logseq", "typora"):
        return "editor"
    return "other"


def _parse_terminal_title(app: str, title: str) -> TitleFeatures:
    """Parse terminal (kitty/foot) window titles."""
    t = title.strip()

    # Claude Code spinner detection
    if t and t[0] in SPINNER_CHARS:
        project = None
        # Check for (project) at end: "◇ Ready (sinex)"
        m = _CLAUDE_PARENS_RE.search(t)
        if m:
            candidate = m.group(1)
            # Validate it looks like a real project, not "Fork" or "Branch"
            if candidate in KNOWN_PROJECTS or candidate.startswith("sinex") or candidate.startswith("sinnix"):
                project = _normalize_project(candidate)
        return TitleFeatures(
            app_kind="terminal", tool="claude-code", project=project,
            domain=None, domain_category=None,
            is_ai_tool=True, is_ai_active=True,
        )

    # "✳ Claude Code" without spinner
    if "Claude Code" in t:
        return TitleFeatures(
            app_kind="terminal", tool="claude-code", project=None,
            domain=None, domain_category=None,
            is_ai_tool=True, is_ai_active=False,
        )

    # Codex invocations
    if t.startswith("codex"):
        return TitleFeatures(
            app_kind="terminal", tool="codex", project=None,
            domain=None, domain_category=None,
            is_ai_tool=True, is_ai_active="resume" not in t,  # "codex resume" = waiting, bare "codex" = running
        )

    # Realm project path
    m = _REALM_PROJECT_RE.search(t)
    if m:
        project = _normalize_project(m.group(1))
        # Detect specific tools from the command
        tool = None
        if t.startswith("nvim ") or t.startswith("vim "):
            tool = "nvim"
        elif t.startswith("bat "):
            tool = "bat"
        elif t.startswith("git "):
            tool = "git"
        return TitleFeatures(
            app_kind="terminal", tool=tool, project=project,
            domain=None, domain_category=None,
            is_ai_tool=False, is_ai_active=False,
        )

    # SSH prompt with path
    m = _PROMPT_RE.match(t)
    if m:
        path = m.group(1)
        pm = _REALM_PROJECT_RE.search(path)
        project = _normalize_project(pm.group(1)) if pm else None
        return TitleFeatures(
            app_kind="terminal", tool=None, project=project,
            domain=None, domain_category=None,
            is_ai_tool=False, is_ai_active=False,
        )

    # Known tools without project context
    tool = None
    if t.startswith("btop"):
        tool = "btop"
    elif t.startswith("ranger") or t.startswith("Yazi"):
        tool = "file_manager"
    elif t.startswith("pingg") or t.startswith("ping "):
        tool = "network"
    elif "weechat" in t.lower():
        tool = "weechat"
    elif t.startswith("nix "):
        tool = "nix"
    elif t.startswith("just "):
        tool = "just"

    return TitleFeatures(
        app_kind="terminal", tool=tool, project=None,
        domain=None, domain_category=None,
        is_ai_tool=False, is_ai_active=False,
    )


def _parse_browser_title(app: str, title: str) -> TitleFeatures:
    """Parse browser window titles — extract domain and category."""
    # Extract domain from URL in title
    m = _TITLE_URL_RE.search(title) or _URL_RE.search(title)
    domain = None
    domain_category = None

    if m:
        domain = m.group(1).lower()
        if domain.startswith("www."):
            domain = domain[4:]
        # Look up category
        domain_category = DOMAIN_CATEGORIES.get(domain)
        if not domain_category:
            # Check partial matches
            for known, cat in DOMAIN_CATEGORIES.items():
                if domain.endswith(known) or known.endswith(domain):
                    domain_category = cat
                    break

    # Check if this is an AI tool in the browser
    is_ai = domain in ("chatgpt.com", "claude.ai", "aistudio.google.com")

    # Project from GitHub URLs
    project = None
    if domain == "github.com":
        # Try to extract repo name from title like "Pull requests · polylogue · GitHub"
        parts = title.split(" · ")
        if len(parts) >= 2:
            candidate = parts[-2].strip().lower()
            if candidate in KNOWN_PROJECTS:
                project = candidate

    return TitleFeatures(
        app_kind="browser", tool=None, project=project,
        domain=domain, domain_category=domain_category,
        is_ai_tool=is_ai, is_ai_active=False,
    )


def _normalize_project(raw: str) -> str | None:
    """Normalize project name — strip trailing semicolons, underscores, etc."""
    canonical = canonical_project_name(raw)
    if canonical:
        return canonical
    cleaned = raw.rstrip(";").rstrip("_").strip()
    if not cleaned or cleaned in (".", "..", "~"):
        return None
    # Map variants to canonical names
    if cleaned.startswith("sinex") and cleaned not in KNOWN_PROJECTS:
        return "sinex"  # sinex-target-vision, sinex-worktree, sinex__ → sinex
    if cleaned.startswith("sinnix") and cleaned not in KNOWN_PROJECTS:
        return "sinnix"
    if cleaned.startswith("polylogue") and cleaned not in KNOWN_PROJECTS:
        return "polylogue"
    if cleaned.startswith("sinity-") and cleaned not in KNOWN_PROJECTS:
        return "sinity-lynchpin"
    return cleaned
