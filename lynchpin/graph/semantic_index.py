"""Persistent brute-force vector index for Lynchpin's semantic-search layer.

A semantic index is a named bundle of ``(vector, metadata)`` pairs persisted to
disk: an ``.npz`` holding the float32 matrix plus a sibling ``.json`` holding the
per-document metadata and the embedding backend used. At Lynchpin's scale (tens
of thousands of short docs) brute-force cosine similarity in numpy is fast enough
that no ANN library (faiss/hnsw) is warranted — keeping the dependency surface to
just numpy.

Public surface:
    build_index(corpus, *, name, backend="auto") -> IndexStats
        Embed every doc's ``text`` and persist vectors + metadata under
        ``<cache_dir>/semantic/<name>.{npz,json}``.
    semantic_search(query, *, name, k=10, since=None, backend="auto")
        -> list[SearchHit]
        Embed the query, cosine-rank against the stored matrix, return top-k.
    index_personal_text(start, end, *, name="personal", ...) -> IndexStats
        Concrete proof-of-concept corpus builder over personal data sources
        (git commit subjects + polylogue session titles), with pluggable
        ``sources`` so web titles / reddit / raw-log can be added later.

Metadata for every document carries at least ``source``, ``date`` (ISO date or
``None``), ``id``, and ``text`` (a snippet), so search hits are self-describing
and date-filterable via the ``since`` parameter.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from ..core.config import get_config
from ..core.embeddings import Backend, embed

FloatArray = npt.NDArray[np.float32]

__all__ = [
    "Document",
    "SearchHit",
    "IndexStats",
    "build_index",
    "semantic_search",
    "index_personal_text",
    "git_commit_corpus",
    "polylogue_corpus",
    "CorpusSource",
    "DEFAULT_PERSONAL_SOURCES",
]


@dataclass(frozen=True)
class Document:
    """A single indexable document.

    ``text`` is what gets embedded. ``metadata`` is persisted verbatim alongside
    the vector and returned with each search hit; it should carry at least
    ``source``, ``date`` (ISO ``YYYY-MM-DD`` or ``None``), and ``id``.
    """

    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchHit:
    """A ranked search result: cosine ``score`` plus the document ``metadata``."""

    score: float
    metadata: dict[str, Any]


@dataclass(frozen=True)
class IndexStats:
    """Summary of a built index."""

    name: str
    documents: int
    dimension: int
    backend: Backend
    path: Path


# A corpus source is any callable yielding Documents for a date range.
CorpusSource = Callable[[date, date], Iterable[Document]]


# ── Storage layout ──────────────────────────────────────────────────────────


def _semantic_dir() -> Path:
    path = get_config().cache_dir / "semantic"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _vectors_path(name: str) -> Path:
    return _semantic_dir() / f"{name}.npz"


def _meta_path(name: str) -> Path:
    return _semantic_dir() / f"{name}.json"


# ── Build ─────────────────────────────────────────────────────────────────────


def build_index(
    corpus: Sequence[Document],
    *,
    name: str,
    backend: Backend = "auto",
) -> IndexStats:
    """Embed ``corpus`` and persist vectors + metadata under ``name``.

    Documents with empty/whitespace text are skipped. Vectors are stored as a
    float32 matrix in an ``.npz``; metadata (one dict per surviving row, same
    order) plus the backend label go to a sibling ``.json``. Overwrites any
    existing index of the same ``name``.
    """
    docs = [d for d in corpus if d.text and d.text.strip()]
    texts = [d.text for d in docs]
    vectors = embed(texts, backend=backend) if texts else []

    matrix = (
        np.asarray(vectors, dtype=np.float32)
        if vectors
        else np.zeros((0, 0), dtype=np.float32)
    )
    metadata = [dict(d.metadata) for d in docs]
    dimension = int(matrix.shape[1]) if matrix.ndim == 2 and matrix.size else 0

    np.savez(_vectors_path(name), vectors=matrix)
    payload = {
        "name": name,
        "backend": backend,
        "dimension": dimension,
        "metadata": metadata,
    }
    _meta_path(name).write_text(json.dumps(payload), encoding="utf-8")

    return IndexStats(
        name=name,
        documents=len(docs),
        dimension=dimension,
        backend=backend,
        path=_vectors_path(name),
    )


def _load_index(name: str) -> tuple[FloatArray, list[dict[str, Any]]]:
    vpath = _vectors_path(name)
    mpath = _meta_path(name)
    if not vpath.exists() or not mpath.exists():
        raise FileNotFoundError(
            f"semantic index {name!r} not found; run build_index/index_personal_text first"
        )
    with np.load(vpath) as data:
        matrix: FloatArray = np.asarray(data["vectors"], dtype=np.float32)
    payload = json.loads(mpath.read_text(encoding="utf-8"))
    metadata = list(payload.get("metadata", []))
    return matrix, metadata


# ── Search ─────────────────────────────────────────────────────────────────────


def _cosine(matrix: FloatArray, query: FloatArray) -> FloatArray:
    """Row-wise cosine similarity between ``matrix`` rows and a query vector."""
    if matrix.size == 0:
        return np.zeros((0,), dtype=np.float32)
    mat_norms = np.linalg.norm(matrix, axis=1)
    q_norm = float(np.linalg.norm(query))
    denom = mat_norms * q_norm
    safe = np.where(denom == 0.0, 1.0, denom)
    sims = (matrix @ query) / safe
    sims = np.where(denom == 0.0, 0.0, sims)
    return sims.astype(np.float32)  # type: ignore[no-any-return]


def semantic_search(
    query: str,
    *,
    name: str,
    k: int = 10,
    since: date | None = None,
    backend: Backend = "auto",
) -> list[SearchHit]:
    """Return the top-``k`` documents in index ``name`` most similar to ``query``.

    ``since`` filters out documents whose metadata ``date`` precedes it (docs with
    no parseable date are dropped when ``since`` is given). Ranking is cosine
    similarity over the persisted matrix; the query is embedded with ``backend``.
    """
    matrix, metadata = _load_index(name)
    if matrix.size == 0 or not metadata:
        return []

    query_vec = np.asarray(embed([query], backend=backend)[0], dtype=np.float32)
    sims = _cosine(matrix, query_vec)

    candidates: list[tuple[float, dict[str, Any]]] = []
    for idx, meta in enumerate(metadata):
        if idx >= sims.shape[0]:
            break
        if since is not None and not _date_at_least(meta.get("date"), since):
            continue
        candidates.append((float(sims[idx]), meta))

    candidates.sort(key=lambda pair: pair[0], reverse=True)
    return [SearchHit(score=score, metadata=meta) for score, meta in candidates[:k]]


def _date_at_least(value: Any, threshold: date) -> bool:
    if not value:
        return False
    try:
        return date.fromisoformat(str(value)[:10]) >= threshold
    except ValueError:
        return False


# ── Corpus sources (pluggable) ──────────────────────────────────────────────


def git_commit_corpus(start: date, end: date) -> Iterable[Document]:
    """Documents from git commit subjects in the range (one per commit)."""
    from ..sources.git import commit_facts

    for fact in commit_facts(start=start, end=end, include_paths=False):
        subject = (fact.subject or "").strip()
        if not subject:
            continue
        commit_date = fact.authored_at.date().isoformat()
        yield Document(
            text=subject,
            metadata={
                "source": "git",
                "date": commit_date,
                "id": f"{fact.repo}:{fact.commit}",
                "repo": fact.repo,
                "text": subject[:280],
            },
        )


def polylogue_corpus(start: date, end: date) -> Iterable[Document]:
    """Documents from polylogue session titles in the range (one per session).

    Degrades gracefully: if the polylogue archive/products are unavailable, yields
    nothing rather than failing the whole index build.
    """
    try:
        from ..sources.polylogue import session_profiles_for_date

        profiles = session_profiles_for_date(start=start, end=end)
    except Exception:  # pragma: no cover - archive availability is environmental
        return
    for profile in profiles:
        title = (profile.title or "").strip()
        if not title:
            continue
        yield Document(
            text=title,
            metadata={
                "source": "polylogue",
                "date": profile.canonical_session_date,
                "id": profile.conversation_id,
                "provider": profile.provider,
                "text": title[:280],
            },
        )


DEFAULT_PERSONAL_SOURCES: tuple[CorpusSource, ...] = (
    git_commit_corpus,
    polylogue_corpus,
)


def index_personal_text(
    start: date,
    end: date,
    *,
    name: str = "personal",
    backend: Backend = "auto",
    sources: Sequence[CorpusSource] = DEFAULT_PERSONAL_SOURCES,
) -> IndexStats:
    """Build a proof-of-concept semantic index over personal data sources.

    Concatenates documents from each callable in ``sources`` (git commit subjects
    and polylogue session titles by default), then embeds and persists them under
    ``name``. ``sources`` is pluggable, so web titles / reddit / raw-log corpus
    builders can be appended without touching this function.
    """
    corpus: list[Document] = []
    for source in sources:
        corpus.extend(source(start, end))
    return build_index(corpus, name=name, backend=backend)
