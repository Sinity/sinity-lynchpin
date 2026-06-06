"""Canonical title/window classification metadata.

The authoritative product is a materialized NDJSON derived from the historical
GPT/rules classification DuckDB. Readers intentionally use the canonical
product only; rebuilding from the old DuckDB belongs to
``lynchpin.ingest.title_metadata_materialize``.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

from ..core.config import get_config

__all__ = [
    "TitleClassification",
    "classification_for",
    "hash_title",
    "iter_title_classifications",
    "load_title_classification_map",
    "normalize_app",
    "normalize_title",
    "title_metadata_manifest_path",
    "title_metadata_path",
]


_SPINNER_CHARS = frozenset(
    "⠁⠂⠃⠄⠅⠆⠇⠈⠉⠊⠋⠌⠍⠎⠏⠐⠑⠒⠓⠔⠕⠖⠗⠘⠙⠚⠛⠜⠝⠞⠟"
    "⠠⠡⠢⠣⠤⠥⠦⠧⠨⠩⠪⠫⠬⠭⠮⠯⠰⠱⠲⠳⠴⠵⠶⠷⠸⠹⠺⠻⠼⠽⠾⠿"
    "✳✦◇◆●○"
)
_GMAIL_COUNT_RE = re.compile(r"^\((\d+)\)\s+")
_BROWSER_COUNT_RE = re.compile(r"^\((\d+)\)\s+")
# Progress counters embedded in titles: "(3/5)", "[60%]", "60% Loading",
# "3/10 done", "[3/12]". These churn from frame to frame and explode the
# unique-title cardinality without changing the underlying activity.
# Patterns leave a single space when matched mid-string so neighbouring
# words don't get glued together ("Building 12/87 done" → "Building done").
_PROGRESS_FRAC_RE = re.compile(r"[\[\(]?\d+/\d+[\]\)]?")
_PROGRESS_PCT_RE = re.compile(r"[\[\(]?\d{1,3}%[\]\)]?")
# Claude Code generates titles that include time-elapsed counters like
# "(esc to interrupt · ctrl+t to ...)" — strip the parenthetical fluff.
_INTERRUPT_HINT_RE = re.compile(r"\s*\((?:esc|ctrl)[^)]*\)\s*$")
_NUM_TOKEN_RE = re.compile(r"\b\d{4,}\b")
_DATE_RE = re.compile(r"\b\d{2,4}[-/]\d{2}[-/]\d{2,4}\b")
_TIME_RE = re.compile(r"\b\d{1,2}:\d{2}(:\d{2})?\b")
_YT_NOISE_PARAMS_RE = re.compile(r"(&list=[a-zA-Z0-9_-]+|&index=\d+|&start_radio=\d+|&pp=[^&\s]+|&t=\d+s?)")
_YT_MUSIC_RADIO_RE = re.compile(r"&list=RD[a-zA-Z0-9_-]+")
_SSH_PID_RE = re.compile(r"^\[(\d+)\]@")
_FILE_LINE_COL_RE = re.compile(r"^(.+\.\w+):(\d+)(:\d+)?$")
_TRAILING_TS_RE = re.compile(r"\s[—|-]\s+\d{1,2}:\d{2}(:\d{2})?\s*$")
_CANONICAL_FORMS = {
    "about:blank": "about:blank",
    "about:blank - Google Chrome": "about:blank",
    "New Tab": "about:blank",
    "New Tab - Google Chrome": "about:blank",
    "New tab - obsidian": "obsidian:new-tab",
    "Extensions - Google Chrome": "chrome:extensions",
    "Extensions": "chrome:extensions",
    "Settings - Google Chrome": "chrome:settings",
    "Downloads - Google Chrome": "chrome:downloads",
    "History - Google Chrome": "chrome:history",
    "Bookmark Manager - Google Chrome": "chrome:bookmarks",
    "rawlog-loop": "system:rawlog-capture",
}


@dataclass(frozen=True)
class TitleClassification:
    title_hash: str
    app: str
    raw_title: str
    normalized_title: str
    activity: str | None = None
    subject: str | None = None
    content_type: str | None = None
    attention_level: str | None = None
    topic_category: str | None = None
    platform: str | None = None
    mode: str | None = None
    app_kind: str | None = None
    tool: str | None = None
    domain: str | None = None
    domain_category: str | None = None
    is_ai_tool: bool | None = None
    is_ai_active: bool | None = None
    productivity_score: float | None = None
    focus_score: float | None = None
    confidence: float | None = None
    classification_source: str | None = None
    model_version: str | None = None
    extra: dict[str, Any] | None = None


def title_metadata_path(root: Path | None = None) -> Path:
    base = root or get_config().derived_root
    return base / "title_metadata/classifications.ndjson"


def title_metadata_manifest_path(root: Path | None = None) -> Path:
    return title_metadata_path(root).with_suffix(".manifest.json")


def normalize_title(app: str, title: str) -> str:
    """Normalize a window title using the historical classifier key algorithm."""
    if not title:
        return ""
    del app
    t = title.strip()
    canonical = _CANONICAL_FORMS.get(t)
    if canonical:
        return canonical
    # Strip ALL leading spinner chars (was: only one). Each frame of the
    # spinner is a distinct character in _SPINNER_CHARS; iterate until no
    # leading spinner remains.
    while t and t[0] in _SPINNER_CHARS:
        t = t[1:].lstrip()
    if not t:
        return "claude-code:idle"
    # Strip progress counters and interrupt-hint suffixes BEFORE other
    # normalization so they don't survive as residual tokens.
    t = _INTERRUPT_HINT_RE.sub("", t)
    t = _PROGRESS_FRAC_RE.sub("", t)
    t = _PROGRESS_PCT_RE.sub("", t)
    t = _GMAIL_COUNT_RE.sub("", t)
    t = _BROWSER_COUNT_RE.sub("", t)
    t = _YT_NOISE_PARAMS_RE.sub("", t)
    t = _YT_MUSIC_RADIO_RE.sub("", t)
    t = _SSH_PID_RE.sub("ssh@", t)
    t = _DATE_RE.sub("<DATE>", t)
    t = _TIME_RE.sub("<TIME>", t)
    t = _NUM_TOKEN_RE.sub("<N>", t)
    t = _FILE_LINE_COL_RE.sub(r"\1:<LINE>", t)
    t = _TRAILING_TS_RE.sub("", t)
    t = re.sub(r"  +", " ", t)
    t = re.sub(r"\s[—-]\s+(Google Chrome|Zen Browser|Ablaze Floorp|Floorp)\s*$", "", t)
    return t.strip() or (title.strip() or "")


def normalize_app(app: str) -> str:
    """Canonicalize an app identifier — lowercase to collapse case variants.

    The Wayland xdg-toplevel app_id convention is lowercase (`firefox`,
    `kitty`, etc.), but real-world data has case variants emerging from
    desktop-file misconfiguration, app version changes, or runtime
    overrides. Concrete pairs seen in the operator's archive:
      Antigravity / antigravity            60,504 events
      google-chrome-beta / Google-chrome-beta  57,619 events
      xdg-desktop-portal-gtk / Xdg-…           7,746 events
      spotify / Spotify                          688 events

    Lowercasing collapses these to the canonical form so aggregations
    by app (focused_seconds per app, top-apps rollups, etc.) don't
    double-count.
    """
    return (app or "").strip().lower()


def hash_title(app: str, normalized_title: str) -> str:
    """Compute the title-classification lookup key.

    Lowercases ``app`` so ``Antigravity`` and ``antigravity`` (and similar
    case variants) hash to the same classification entry. The title is
    NOT lowercased — case carries meaning in titles (e.g., proper nouns,
    code identifiers, command-line flags).
    """
    return hashlib.md5(f"{normalize_app(app)}\0{normalized_title}".encode()).hexdigest()


def iter_title_classifications(
    path: Path | None = None,
    *,
    ensure: bool = True,
) -> Iterator[TitleClassification]:
    if path is None and ensure:
        from ..materialization import ensure_materialized

        ensure_materialized("title_metadata")
    target = path or title_metadata_path()
    if not target.exists():
        raise FileNotFoundError(
            f"canonical title metadata materialization is missing: {target}. "
            "Run python -m lynchpin.cli.materialize --all."
        )
    with target.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                yield _classification_from_payload(payload)


def load_title_classification_map(path: Path | None = None) -> dict[str, TitleClassification]:
    return {row.title_hash: row for row in iter_title_classifications(path)}


def classification_for(app: str, title: str, *, path: Path | None = None) -> TitleClassification | None:
    normalized = normalize_title(app, title)
    key = hash_title(app, normalized)
    if path is not None:
        return load_title_classification_map(path).get(key)
    return _default_classification_map().get(key)


@lru_cache(maxsize=1)
def _default_classification_map() -> dict[str, TitleClassification]:
    return load_title_classification_map()


def _classification_from_payload(payload: dict[str, Any]) -> TitleClassification:
    known = {
        "title_hash",
        "app",
        "raw_title",
        "normalized_title",
        "activity",
        "subject",
        "content_type",
        "attention_level",
        "topic_category",
        "platform",
        "mode",
        "app_kind",
        "tool",
        "domain",
        "domain_category",
        "is_ai_tool",
        "is_ai_active",
        "productivity_score",
        "focus_score",
        "confidence",
        "classification_source",
        "model_version",
    }
    extra = {key: value for key, value in payload.items() if key not in known and value not in (None, "")}
    return TitleClassification(
        title_hash=str(payload.get("title_hash") or ""),
        app=str(payload.get("app") or ""),
        raw_title=str(payload.get("raw_title") or ""),
        normalized_title=str(payload.get("normalized_title") or ""),
        activity=_str_or_none(payload.get("activity")),
        subject=_str_or_none(payload.get("subject")),
        content_type=_str_or_none(payload.get("content_type")),
        attention_level=_str_or_none(payload.get("attention_level")),
        topic_category=_str_or_none(payload.get("topic_category")),
        platform=_str_or_none(payload.get("platform")),
        mode=_str_or_none(payload.get("mode")),
        app_kind=_str_or_none(payload.get("app_kind")),
        tool=_str_or_none(payload.get("tool")),
        domain=_str_or_none(payload.get("domain")),
        domain_category=_str_or_none(payload.get("domain_category")),
        is_ai_tool=_bool_or_none(payload.get("is_ai_tool")),
        is_ai_active=_bool_or_none(payload.get("is_ai_active")),
        productivity_score=_float_or_none(payload.get("productivity_score")),
        focus_score=_float_or_none(payload.get("focus_score")),
        confidence=_float_or_none(payload.get("confidence")),
        classification_source=_str_or_none(payload.get("classification_source")),
        model_version=_str_or_none(payload.get("model_version")),
        extra=extra or None,
    )


def _str_or_none(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _float_or_none(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool_or_none(value: object) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes"}:
        return True
    if text in {"0", "false", "no"}:
        return False
    return None
