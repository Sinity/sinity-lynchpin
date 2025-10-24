"""Project registry — single source of truth for all project metadata."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

ProjectClassifier = Callable[[str], Optional[str]]
PROJECT_ROOT = Path("/realm/project")


@dataclass(frozen=True)
class ProjectEntry:
    name: str
    path: str
    era: str  # "pre-ai" | "mixed" | "ai"
    active: bool
    extensions: tuple[str, ...]
    classify: Optional[ProjectClassifier]


# ── File classifiers ──────────────────────────────────────────────────────────

def _skip_common(path: str) -> bool:
    parts = Path(path).parts
    if not parts:
        return True
    lowered = {p.lower() for p in parts}
    return bool(lowered & {
        ".git", ".direnv", ".claude", "node_modules", "target",
        "dist", "build", "coverage", "artefacts", "__pycache__",
    })


def classify_sinex(path: str) -> str | None:
    if _skip_common(path):
        return None
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if ".sqlx/" in path:
        return None
    if "tests/" in path or path.endswith("_test.rs"):
        return "tests"
    if "docs/" in path or path.endswith(".md"):
        return "docs"
    if ext in {"nix", "toml", "yaml", "yml", "json"}:
        return "config"
    if Path(path).name.lower() in {"justfile", ".gitignore", ".envrc", "cargo.lock"}:
        return "config"
    return "src"


def classify_sinnix(filename: str) -> str | None:
    if _skip_common(filename):
        return None
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if "/docs/" in filename or filename.startswith("docs/"):
        return "docs"
    if ext in {"md", "mdx", "rst", "txt"}:
        return "docs"
    if filename.startswith("host/") or "/host/" in filename:
        return "host"
    if filename.startswith("flake/") or filename in {"flake.nix", "flake.lock"}:
        return "flake"
    if filename.startswith("modules/") or "/modules/" in filename:
        return "module"
    return "other"


def classify_sinity_analysis(filename: str) -> str | None:
    if _skip_common(filename):
        return None
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if "/docs/" in filename or filename.startswith("docs/"):
        return "docs"
    if ext in {"md", "mdx", "rst", "txt"}:
        return "docs"
    if filename.startswith("tests/") or "/tests/" in filename:
        return "tests"
    if filename.startswith("lynchpin/"):
        return "analysis"
    if ext in {"nix", "toml", "yaml", "yml", "json"}:
        return "config"
    if Path(filename).name.lower() in {"justfile", ".gitignore", ".envrc", "pyproject.toml"}:
        return "config"
    return "other"


def classify_knowledgebase(filename: str) -> str | None:
    if _skip_common(filename):
        return None
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in {"nix", "toml", "yaml", "yml", "json"}:
        return "config"
    if Path(filename).name.lower() in {"justfile", ".gitignore", ".envrc"}:
        return "config"
    return "docs"


def classify_rust_simple(filename: str) -> str | None:
    if _skip_common(filename):
        return None
    basename = Path(filename).name.lower()
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if "/tests/" in filename or "/test/" in filename or filename.startswith(("tests/", "test/")):
        return "tests"
    if basename.endswith(("_test.rs", "_tests.rs")) or re.match(r"(test_.*|.*_test|.*_tests)\.", basename):
        return "tests"
    if "/docs/" in filename or filename.startswith("docs/") or ext in {"md", "mdx", "rst", "txt"}:
        return "docs"
    if ext in {"nix", "toml", "yaml", "yml"}:
        return "config"
    if basename in {"justfile", ".gitignore", ".envrc"}:
        return "config"
    return "src"


# ── Project registry ──────────────────────────────────────────────────────────

ALL_PROJECTS: dict[str, ProjectEntry] = {
    # Active
    "sinex": ProjectEntry("sinex", "/realm/project/sinex", "ai", True, (".rs",), classify_sinex),
    "sinex-target-vision": ProjectEntry("sinex-target-vision", "/realm/project/sinex-target-vision", "ai", True, (".rs", ".py", ".md"), classify_rust_simple),
    "sinnix": ProjectEntry("sinnix", "/realm/project/sinnix", "mixed", True, (".nix",), classify_sinnix),
    "sinity-lynchpin": ProjectEntry("sinity-lynchpin", "/realm/project/sinity-lynchpin", "ai", True, (".py",), classify_sinity_analysis),
    "polylogue": ProjectEntry("polylogue", "/realm/project/polylogue", "ai", True, (".py",), classify_rust_simple),
    "intercept-bounce": ProjectEntry("intercept-bounce", "/realm/project/intercept-bounce", "ai", True, (".rs",), classify_rust_simple),
    "scribe-tap": ProjectEntry("scribe-tap", "/realm/project/scribe-tap", "ai", True, (".c", ".h", ".py"), classify_rust_simple),
    "knowledge-extract": ProjectEntry("knowledge-extract", "/realm/project/knowledge-extract", "ai", True, (".py",), classify_rust_simple),
    "pwrank": ProjectEntry("pwrank", "/realm/project/pwrank", "pre-ai", True, (".py", ".vue", ".js"), classify_rust_simple),
    "knowledgebase": ProjectEntry("knowledgebase", "/realm/data/knowledgebase", "ai", True, (), classify_knowledgebase),
    # Inactive
    "WSoC13-SpaceCombat-Game": ProjectEntry("WSoC13-SpaceCombat-Game", "_inactive/WSoC13-SpaceCombat-Game", "pre-ai", False, (".cpp", ".h"), None),
    "UselessOS": ProjectEntry("UselessOS", "_inactive/UselessOS", "pre-ai", False, (".cpp", ".h", ".s"), None),
    "Entity-Component-System": ProjectEntry("Entity-Component-System", "_inactive/Entity-Component-System", "pre-ai", False, (".cpp", ".hpp"), None),
    "TabHistory-ChromeExtension": ProjectEntry("TabHistory-ChromeExtension", "_inactive/TabHistory-ChromeExtension", "pre-ai", False, (".js", ".html"), None),
    "BakeryRecipeManager": ProjectEntry("BakeryRecipeManager", "_inactive/BakeryRecipeManager", "pre-ai", False, (".cpp", ".h"), None),
    "Studia-Picture-Binner-CSharp-Desktop-App": ProjectEntry("Studia-Picture-Binner-CSharp-Desktop-App", "_inactive/Studia-Picture-Binner-CSharp-Desktop-App", "pre-ai", False, (".cs",), None),
    "Technikum-Praktyki-Przebiegi-Django": ProjectEntry("Technikum-Praktyki-Przebiegi-Django", "_inactive/Technikum-Praktyki-Przebiegi-Django", "pre-ai", False, (".py", ".html"), None),
}


_PROJECT_ALIASES = {
    "polylogue_cl": "polylogue",
    "polylogue-cl": "polylogue",
    "__lynchpin_exported": "sinity-lynchpin",
    "lynchpin": "sinity-lynchpin",
    "sinity_lynchpin": "sinity-lynchpin",
    "target-vision": "sinex-target-vision",
    "target vision": "sinex-target-vision",
}

_PROJECT_PREFIX_ALIASES = (
    ("polylogue-", "polylogue"),
    ("sinex-target-vision-", "sinex-target-vision"),
    ("sinity-lynchpin-", "sinity-lynchpin"),
    ("sinnix-", "sinnix"),
)

_PROJECT_CONTAINS_ALIASES = (
    ("polylogue", "polylogue"),
    ("sinex-target-vision", "sinex-target-vision"),
)


def canonical_project_name(value: object, *, include_inactive: bool = False) -> str | None:
    """Return a known project name for a raw repo/path/title fragment.

    This is intentionally conservative: unknown fragments are not projects.
    It prevents analysis rows from treating UUIDs, temporary conversation
    directories, or arbitrary terminal CWD leaves as first-class projects.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.rstrip("/").removesuffix(".git")
    text = text.split("#", 1)[0]

    marker = "/realm/project/"
    if marker in text:
        rel = text.split(marker, 1)[1]
        head = rel.split("/", 1)[0]
        if head == "_inactive":
            return None
        text = head
    elif "/" in text:
        text = text.rsplit("/", 1)[-1]

    cleaned = text.rstrip(";").rstrip("_").strip()
    if not cleaned or cleaned in {".", "..", "~", "_inactive"}:
        return None
    lowered = cleaned.lower()

    if lowered in ALL_PROJECTS and (include_inactive or ALL_PROJECTS[lowered].active):
        return lowered
    alias = _PROJECT_ALIASES.get(lowered)
    if alias:
        return alias
    for prefix, project in _PROJECT_PREFIX_ALIASES:
        if lowered.startswith(prefix):
            return project
    for needle, project in _PROJECT_CONTAINS_ALIASES:
        if needle in lowered:
            return project
    return None


def project_path(name: str) -> Path:
    """Return the canonical local checkout path for a registered project."""
    entry = ALL_PROJECTS[name]
    path = Path(entry.path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


# ── ProjectProfile (used by analysis/projects/) ──────────────────────────────


@dataclass(frozen=True)
class ProjectProfile:
    name: str
    path: Path
    classify: ProjectClassifier
    categories: tuple[str, ...]
    colors: dict[str, str]
    extra_ignore: tuple[str, ...] = ()


def project_profiles() -> dict[str, ProjectProfile]:
    rust_colors = {"src": "#5470c6", "tests": "#91cc75", "docs": "#fac858", "config": "#ee6666"}
    analysis_colors = {"analysis": "#2f7fff", "tests": "#79c753", "docs": "#ffb000", "config": "#f46d43", "other": "#7a8ca5"}
    docs_colors = {"docs": "#ffb000", "config": "#f46d43", "other": "#7a8ca5"}
    return {
        "sinex": ProjectProfile("sinex", project_path("sinex"), classify_sinex, ("src", "tests", "docs", "config"), rust_colors),
        "sinex-target-vision": ProjectProfile("sinex-target-vision", project_path("sinex-target-vision"), classify_rust_simple, ("src", "tests", "docs", "config"), rust_colors),
        "polylogue": ProjectProfile("polylogue", project_path("polylogue"), classify_rust_simple, ("src", "tests", "docs", "config"), rust_colors),
        "intercept-bounce": ProjectProfile("intercept-bounce", project_path("intercept-bounce"), classify_rust_simple, ("src", "tests", "docs", "config"), rust_colors),
        "scribe-tap": ProjectProfile("scribe-tap", project_path("scribe-tap"), classify_rust_simple, ("src", "tests", "docs", "config"), rust_colors),
        "pwrank": ProjectProfile("pwrank", project_path("pwrank"), classify_rust_simple, ("src", "tests", "docs", "config"), rust_colors),
        "knowledge-extract": ProjectProfile("knowledge-extract", project_path("knowledge-extract"), classify_rust_simple, ("src", "tests", "docs", "config"), rust_colors),
        "sinnix": ProjectProfile("sinnix", project_path("sinnix"), classify_sinnix, ("module", "host", "flake", "docs", "other"), {"module": "#5470c6", "host": "#91cc75", "flake": "#fac858", "docs": "#ee6666", "other": "#73c0de"}),
        "sinity-lynchpin": ProjectProfile("sinity-lynchpin", project_path("sinity-lynchpin"), classify_sinity_analysis, ("analysis", "tests", "docs", "config", "other"), analysis_colors),
        "knowledgebase": ProjectProfile("knowledgebase", project_path("knowledgebase"), classify_knowledgebase, ("docs", "config", "other"), docs_colors, extra_ignore=("archive/**",)),
    }
