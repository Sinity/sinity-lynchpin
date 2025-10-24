"""Web-history domain categorization with sensitive-content metadata.

Turns the *set of distinct domains* that appear in browsing history into a typed
classification: a coarse ``category`` from a documented closed vocabulary, a
dedicated ``nsfw`` compatibility flag, and a finer free-ish ``content_type``
label. Sensitive-content classification stays separate from the coarse
category so downstream consumers can filter or summarize it without exposing
the underlying URLs. The finer label can characterize content without
overloading the category or boolean fields.

Strategy
--------
Distinct-domain cardinality in a bounded browsing corpus is usually small
(hundreds to low thousands), so the whole unique set can be classified once
and cached:

1. **Seed mapping** (:data:`SEED`). A bundled table of common high-frequency
   domains — ``github.com`` -> dev, ``reddit.com`` -> social,
   ``youtube.com`` -> media, major news sites -> news, etc. Covers the obvious
   head of the distribution with zero model cost and stable labels. Sensitive
   domains are deliberately not seeded — see the LLM fallback below.
2. **LLM fallback** for the tail. Domains absent from the seed are batch
   classified via the Claude Max subscription backend
   (:mod:`lynchpin.core.claude_sdk`, no API key). The model returns JSON
   ``{domain: {category, nsfw, content_type}}`` per batch. Results are cached
   to disk (see :func:`_default_cache_path`, outside the checkout) so each
   domain is classified exactly once across runs. Domains the model fails to
   classify cache as ``other`` so they are not re-requested every run.

The actual model invocation is isolated in :func:`_llm_classify_batch`, a small
function tests monkeypatch so no real LLM call happens under test.

Graduated API
-------------
    classify_domains(domains) -> dict[str, DomainCategory]
        domain -> DomainCategory, seed first, LLM (cached) for the rest.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from ..core.config import get_config
from .web_urls import _normalize_domain

logger = logging.getLogger(__name__)

__all__ = [
    "WebCategory",
    "CATEGORY_VOCABULARY",
    "DomainCategory",
    "SEED",
    "classify_domains",
]

# ---------------------------------------------------------------------------
# Category vocabulary (documented closed set)
# ---------------------------------------------------------------------------

#: Coarse domain category. Closed vocabulary — the LLM is constrained to it and
#: any out-of-vocabulary label is coerced to ``"other"``.
#:
#:   news         — journalism / current events (nytimes, bbc, hacker news feeds)
#:   dev          — software development (github, stackoverflow, docs, package indexes)
#:   social       — social networks / forums / link aggregators (reddit, twitter, fb)
#:   media        — video / music / streaming entertainment (youtube, netflix, twitch)
#:   reference    — encyclopedic / educational / docs (wikipedia, arxiv, mdn)
#:   shopping     — e-commerce / marketplaces (amazon, allegro, ebay)
#:   productivity — tools / SaaS / mail / calendars / docs apps (gmail, notion, jira)
#:   finance      — banking / brokerage / crypto / accounting (paypal, binance)
#:   adult        — pornography / sexual content (always nsfw=True)
#:   ai           — AI assistants / model UIs (chatgpt, claude.ai, perplexity)
#:   search       — search engines (google, bing, duckduckgo)
#:   other        — anything that fits none of the above / unknown
WebCategory = Literal[
    "news",
    "dev",
    "social",
    "media",
    "reference",
    "shopping",
    "productivity",
    "finance",
    "adult",
    "ai",
    "search",
    "other",
]

CATEGORY_VOCABULARY: tuple[str, ...] = get_args(WebCategory)


@dataclass(frozen=True)
class DomainCategory:
    """Classification of a single domain.

    ``content_type`` is a finer free-ish label refining ``category``: for
    ``media`` it may be ``"video"`` / ``"music"`` / ``"streaming"``; for
    ``dev`` it may be ``"code-host"`` / ``"qa"`` / ``"docs"``. ``"general"``
    is the neutral default when no finer type applies.
    """

    domain: str
    category: WebCategory
    nsfw: bool
    content_type: str


# ---------------------------------------------------------------------------
# Seed mapping: (category, content_type[, nsfw]) for common high-freq domains.
# nsfw defaults to (category == "adult"); only override when it differs.
# ---------------------------------------------------------------------------

_SEED_SPEC: dict[str, tuple[str, str]] = {
    # dev
    "github.com": ("dev", "code-host"),
    "gist.github.com": ("dev", "code-host"),
    "gitlab.com": ("dev", "code-host"),
    "stackoverflow.com": ("dev", "qa"),
    "stackexchange.com": ("dev", "qa"),
    "pypi.org": ("dev", "package-index"),
    "npmjs.com": ("dev", "package-index"),
    "crates.io": ("dev", "package-index"),
    "docs.rs": ("dev", "docs"),
    "readthedocs.io": ("dev", "docs"),
    "developer.mozilla.org": ("dev", "docs"),
    "rust-lang.org": ("dev", "docs"),
    "python.org": ("dev", "docs"),
    "docs.python.org": ("dev", "docs"),
    "kernel.org": ("dev", "docs"),
    "nixos.org": ("dev", "docs"),
    "wiki.nixos.org": ("dev", "docs"),
    # news
    "news.ycombinator.com": ("news", "tech"),
    "nytimes.com": ("news", "general"),
    "bbc.com": ("news", "general"),
    "bbc.co.uk": ("news", "general"),
    "theguardian.com": ("news", "general"),
    "arstechnica.com": ("news", "tech"),
    "theverge.com": ("news", "tech"),
    "wired.com": ("news", "tech"),
    "reuters.com": ("news", "general"),
    "bloomberg.com": ("news", "finance"),
    "wyborcza.pl": ("news", "general"),
    "onet.pl": ("news", "general"),
    "wp.pl": ("news", "general"),
    # social
    "reddit.com": ("social", "forum"),
    "old.reddit.com": ("social", "forum"),
    "new.reddit.com": ("social", "forum"),
    "twitter.com": ("social", "microblog"),
    "x.com": ("social", "microblog"),
    "facebook.com": ("social", "network"),
    "instagram.com": ("social", "network"),
    "linkedin.com": ("social", "professional"),
    "news.ycombinator.com.social": ("social", "forum"),
    "mastodon.social": ("social", "microblog"),
    "bsky.app": ("social", "microblog"),
    "wykop.pl": ("social", "forum"),
    "discord.com": ("social", "chat"),
    "tumblr.com": ("social", "blog"),
    # media
    "youtube.com": ("media", "video"),
    "youtu.be": ("media", "video"),
    "netflix.com": ("media", "streaming"),
    "twitch.tv": ("media", "video"),
    "spotify.com": ("media", "music"),
    "open.spotify.com": ("media", "music"),
    "soundcloud.com": ("media", "music"),
    "vimeo.com": ("media", "video"),
    "music.youtube.com": ("media", "music"),
    # reference
    "wikipedia.org": ("reference", "encyclopedia"),
    "en.wikipedia.org": ("reference", "encyclopedia"),
    "pl.wikipedia.org": ("reference", "encyclopedia"),
    "arxiv.org": ("reference", "papers"),
    "scholar.google.com": ("reference", "papers"),
    "goodreads.com": ("reference", "books"),
    "wolframalpha.com": ("reference", "computation"),
    # shopping
    "amazon.com": ("shopping", "marketplace"),
    "amazon.pl": ("shopping", "marketplace"),
    "amazon.de": ("shopping", "marketplace"),
    "allegro.pl": ("shopping", "marketplace"),
    "ebay.com": ("shopping", "marketplace"),
    "aliexpress.com": ("shopping", "marketplace"),
    "ceneo.pl": ("shopping", "price-compare"),
    # productivity
    "mail.google.com": ("productivity", "email"),
    "gmail.com": ("productivity", "email"),
    "calendar.google.com": ("productivity", "calendar"),
    "drive.google.com": ("productivity", "storage"),
    "docs.google.com": ("productivity", "docs"),
    "notion.so": ("productivity", "notes"),
    "obsidian.md": ("productivity", "notes"),
    "trello.com": ("productivity", "tasks"),
    "atlassian.net": ("productivity", "tasks"),
    "outlook.com": ("productivity", "email"),
    "outlook.office.com": ("productivity", "email"),
    "outlook.office365.com": ("productivity", "email"),
    # finance
    "paypal.com": ("finance", "payments"),
    "binance.com": ("finance", "crypto"),
    "coinbase.com": ("finance", "crypto"),
    "ing.pl": ("finance", "banking"),
    "mbank.pl": ("finance", "banking"),
    "revolut.com": ("finance", "banking"),
    # ai
    "chat.openai.com": ("ai", "assistant"),
    "chatgpt.com": ("ai", "assistant"),
    "claude.ai": ("ai", "assistant"),
    "perplexity.ai": ("ai", "assistant"),
    "gemini.google.com": ("ai", "assistant"),
    "huggingface.co": ("ai", "models"),
    # search
    "google.com": ("search", "engine"),
    "google.pl": ("search", "engine"),
    "bing.com": ("search", "engine"),
    "duckduckgo.com": ("search", "engine"),
    "kagi.com": ("search", "engine"),
    # Adult domains are intentionally not seeded here: the LLM fallback below
    # already classifies them (category="adult", nsfw=True — see _LLM_PROMPT),
    # so a real domain list never needs to live in tracked source. Classified
    # results land in the gitignored on-disk cache (_default_cache_path), not
    # in this file.
}


def _coerce_category(value: object) -> WebCategory:
    if isinstance(value, str) and value.lower() in CATEGORY_VOCABULARY:
        return value.lower()  # type: ignore[return-value]
    return "other"


def _build_seed() -> dict[str, DomainCategory]:
    seed: dict[str, DomainCategory] = {}
    for raw_domain, (category, content_type) in _SEED_SPEC.items():
        domain = _normalize_domain(raw_domain)
        cat = _coerce_category(category)
        seed[domain] = DomainCategory(
            domain=domain,
            category=cat,
            nsfw=(cat == "adult"),
            content_type=content_type,
        )
    return seed


#: Bundled seed mapping: normalized domain -> DomainCategory.
SEED: dict[str, DomainCategory] = _build_seed()


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def _default_cache_path() -> Path:
    # Outside the checkout entirely (not just gitignored inside it) — this
    # cache accumulates real classified domains, including adult ones, so it
    # shouldn't be at risk from repo-local operations either.
    return get_config().derived_root / "cache" / "web_categories.json"


def _load_cache(path: Path) -> dict[str, DomainCategory]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, DomainCategory] = {}
    for domain, rec in data.items():
        if not isinstance(rec, dict):
            continue
        out[str(domain)] = DomainCategory(
            domain=str(domain),
            category=_coerce_category(rec.get("category")),
            nsfw=bool(rec.get("nsfw", False)),
            content_type=str(rec.get("content_type") or "general"),
        )
    return out


def _save_cache(path: Path, cache: dict[str, DomainCategory]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        domain: {
            "category": dc.category,
            "nsfw": dc.nsfw,
            "content_type": dc.content_type,
        }
        for domain, dc in sorted(cache.items())
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------


def _coerce_record(domain: str, rec: object) -> DomainCategory:
    """Coerce one raw model record into a validated DomainCategory.

    ``adult`` always implies ``nsfw=True`` so sensitive-content classification
    remains stable regardless of what the model emits for the boolean.
    """
    category: WebCategory = "other"
    nsfw = False
    content_type = "general"
    if isinstance(rec, dict):
        category = _coerce_category(rec.get("category"))
        nsfw = bool(rec.get("nsfw", False))
        ct = rec.get("content_type")
        if isinstance(ct, str) and ct.strip():
            content_type = ct.strip()
    if category == "adult":
        nsfw = True
    return DomainCategory(
        domain=domain, category=category, nsfw=nsfw, content_type=content_type
    )


_LLM_PROMPT = """\
You are classifying web domains for a personal-data analytics pipeline.

For EACH domain below, return its classification. Respond with ONLY a JSON object
mapping each domain string to an object with these keys:
  - "category": one of {vocab}
  - "nsfw": boolean (true for adult or explicitly sexual content)
  - "content_type": a short lowercase label refining the category
        (e.g. for media: "video"/"music"/"streaming";
         for dev: "code-host"/"qa"/"docs"/"package-index";
         use "general" when nothing finer applies)

Any explicitly adult or sexual domain MUST have category "adult" and
nsfw true. Use "other" only when no listed category fits.

Domains:
{domains}

Return only the JSON object, no prose.
"""


def _parse_llm_json(text: str) -> dict[str, object]:
    """Extract the JSON object from a model response, tolerating fences/prose."""
    text = text.strip()
    if not text:
        return {}
    # Strip ```json ... ``` fences if present.
    if text.startswith("```"):
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return {}
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _llm_classify_batch(domains: list[str]) -> dict[str, object]:
    """Classify a batch of domains via the Claude Max subscription backend.

    Returns the raw parsed ``{domain: {category, nsfw, content_type}}`` mapping.
    Isolated and small so tests monkeypatch it instead of invoking a real model.
    On any failure returns ``{}`` (callers then default the domains to ``other``).
    """
    if not domains:
        return {}
    from ..core.claude_sdk import run_claude_sdk

    prompt = _LLM_PROMPT.format(
        vocab=", ".join(CATEGORY_VOCABULARY),
        domains="\n".join(f"- {d}" for d in domains),
    )
    try:
        result = asyncio.run(
            run_claude_sdk(
                prompt,
                model="claude-haiku-4-5",
                allowed_tools=[],
                max_turns=1,
            )
        )
    except Exception as exc:  # pragma: no cover - runtime/LLM surface
        logger.warning("web_categories: LLM batch classify failed: %s", exc)
        return {}
    return _parse_llm_json(result.text)


_LLM_BATCH_SIZE = 40


def classify_domains(
    domains: Iterable[str], *, cache_path: Path | None = None
) -> dict[str, DomainCategory]:
    """Classify domains: ``domain -> DomainCategory``.

    Routing per distinct (normalized) domain:
      1. seed mapping (:data:`SEED`) — zero cost, stable;
      2. disk cache (see :func:`_default_cache_path`) — prior LLM results;
      3. LLM fallback (:func:`_llm_classify_batch`, batched), result cached.

    Domains the LLM does not return are cached as ``other`` so they are not
    re-requested on later runs. ``adult`` always implies ``nsfw=True``.
    """
    path = cache_path or _default_cache_path()

    # Normalize + dedupe, preserving the empty-domain skip.
    norm: list[str] = []
    seen: set[str] = set()
    for raw in domains:
        d = _normalize_domain(raw or "")
        if d and d not in seen:
            seen.add(d)
            norm.append(d)

    result: dict[str, DomainCategory] = {}
    cache = _load_cache(path)
    to_classify: list[str] = []

    for d in norm:
        if d in SEED:
            result[d] = SEED[d]
        elif d in cache:
            result[d] = cache[d]
        else:
            to_classify.append(d)

    if to_classify:
        for start in range(0, len(to_classify), _LLM_BATCH_SIZE):
            batch = to_classify[start : start + _LLM_BATCH_SIZE]
            batch_raw = _llm_classify_batch(batch)
            for d in batch:
                dc = _coerce_record(d, batch_raw.get(d))
                result[d] = dc
                cache[d] = dc
        _save_cache(path, cache)

    return result
