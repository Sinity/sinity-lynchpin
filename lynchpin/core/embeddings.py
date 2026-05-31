"""Pluggable text-embedding backend for Lynchpin's semantic layer.

This module is the single embedding seam for the semantic index and any future
semantic analysis. It exposes one public function, :func:`embed`, which routes a
batch of texts through a selectable backend:

- ``"voyage"`` — Voyage AI hosted embeddings (``voyage-3`` family). Near-free,
  high quality. Requires ``VOYAGE_API_KEY`` in the environment. The HTTP call
  goes through :func:`_voyage_post`, which is monkeypatch-friendly so tests and
  offline runs never touch the network.
- ``"local"`` — ``sentence-transformers`` running on the local GPU/CPU. ``torch``
  and the model are imported lazily *inside* the backend so importing this module
  never drags in heavy ML dependencies. If the package or a model is unavailable
  a :class:`SourceUnavailableError` is raised pointing at the install command.
- ``"auto"`` — Voyage when ``VOYAGE_API_KEY`` is present, else local. If neither
  is available, :class:`SourceUnavailableError` is raised.

Design mirrors ``sources/spotify_genres.py`` (mockable HTTP + typed unavailable
error) and ``core/claude_sdk.py`` (subscription/external-call style with lazy
optional imports). No embedding vectors are persisted here — that is the
responsibility of ``graph/semantic_index.py``.
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Literal

from .errors import SourceUnavailableError

Backend = Literal["auto", "voyage", "local"]

_VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
_VOYAGE_MODEL = os.environ.get("LYNCHPIN_VOYAGE_MODEL", "voyage-3")
_VOYAGE_BATCH = 128  # Voyage accepts large batches; keep requests bounded.

_LOCAL_MODEL = os.environ.get(
    "LYNCHPIN_LOCAL_EMBED_MODEL", "BAAI/bge-small-en-v1.5"
)

# Module-level cache for the (expensive) local model handle.
_local_model: object | None = None

__all__ = ["embed", "Backend", "resolve_backend", "embedding_dimension"]


def resolve_backend(backend: Backend = "auto") -> Literal["voyage", "local"]:
    """Resolve ``"auto"`` to a concrete backend without doing any embedding.

    Voyage wins when ``VOYAGE_API_KEY`` is set; otherwise local is selected.
    Explicit ``"voyage"``/``"local"`` are returned unchanged. Raises
    :class:`SourceUnavailableError` only for ``"auto"`` when neither a Voyage key
    nor a local model is available.
    """
    if backend == "voyage":
        return "voyage"
    if backend == "local":
        return "local"
    if backend == "auto":
        if os.environ.get("VOYAGE_API_KEY", "").strip():
            return "voyage"
        if _local_available():
            return "local"
        raise SourceUnavailableError(
            "embeddings",
            reason=(
                "no embedding backend available: set VOYAGE_API_KEY for the "
                "Voyage backend, or `pip install sentence-transformers` for the "
                "local backend"
            ),
        )
    raise ValueError(f"unknown embedding backend: {backend!r}")


def embed(texts: list[str], *, backend: Backend = "auto") -> list[list[float]]:
    """Embed ``texts`` into dense vectors using the selected backend.

    Returns one vector (``list[float]``) per input text, in input order. An empty
    input returns an empty list without touching any backend.

    Backends:
        ``"voyage"`` — hosted Voyage embeddings; requires ``VOYAGE_API_KEY``.
        ``"local"``  — local ``sentence-transformers`` model (lazy import).
        ``"auto"``   — Voyage if key present, else local.

    Raises:
        SourceUnavailableError: backend prerequisites (key / package / model) are
            missing.
    """
    if not texts:
        return []
    resolved = resolve_backend(backend)
    if resolved == "voyage":
        return _embed_voyage(texts)
    return _embed_local(texts)


def embedding_dimension(*, backend: Backend = "auto") -> int:
    """Return the embedding dimension for the resolved backend.

    Cheap for the local backend (model metadata); for Voyage it embeds a single
    probe string. Useful for pre-allocating storage.
    """
    resolved = resolve_backend(backend)
    if resolved == "local":
        model = _load_local_model()
        dim = model.get_sentence_embedding_dimension()  # type: ignore[attr-defined]
        return int(dim)
    return len(embed(["probe"], backend="voyage")[0])


# ── Voyage backend ──────────────────────────────────────────────────────────


def _voyage_post(payload: dict[str, object], *, api_key: str) -> dict[str, object]:
    """POST a payload to the Voyage embeddings endpoint and return parsed JSON.

    Isolated for monkeypatching in tests — replace this function to avoid the
    network entirely.
    """
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        _VOYAGE_URL,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 (fixed https host)
        parsed = json.load(resp)
    return parsed if isinstance(parsed, dict) else {}


def _embed_voyage(texts: list[str]) -> list[list[float]]:
    api_key = os.environ.get("VOYAGE_API_KEY", "").strip()
    if not api_key:
        raise SourceUnavailableError(
            "embeddings",
            reason="VOYAGE_API_KEY not set — required for the Voyage backend",
        )
    vectors: list[list[float]] = []
    for start in range(0, len(texts), _VOYAGE_BATCH):
        batch = texts[start : start + _VOYAGE_BATCH]
        payload: dict[str, object] = {"model": _VOYAGE_MODEL, "input": batch}
        response = _voyage_post(payload, api_key=api_key)
        records = response.get("data")
        if not isinstance(records, list) or len(records) != len(batch):
            raise SourceUnavailableError(
                "embeddings",
                reason=(
                    "Voyage response missing/!= expected embeddings "
                    f"(got {len(records) if isinstance(records, list) else 'none'} "
                    f"for {len(batch)} inputs)"
                ),
            )
        # Voyage returns records carrying an "index"; sort to preserve order.
        ordered = sorted(
            (r for r in records if isinstance(r, dict)),
            key=lambda r: int(r.get("index", 0)),
        )
        for record in ordered:
            emb = record.get("embedding")
            if not isinstance(emb, list):
                raise SourceUnavailableError(
                    "embeddings", reason="Voyage record missing 'embedding' list"
                )
            vectors.append([float(x) for x in emb])
    return vectors


# ── Local backend ─────────────────────────────────────────────────────────────


def _local_available() -> bool:
    """True when ``sentence-transformers`` is importable (model load deferred)."""
    import importlib.util

    return importlib.util.find_spec("sentence_transformers") is not None


def _load_local_model() -> object:
    global _local_model
    if _local_model is not None:
        return _local_model
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]
    except Exception as exc:  # pragma: no cover - environment-specific
        raise SourceUnavailableError(
            "embeddings",
            reason=(
                "sentence-transformers not installed — "
                "`pip install sentence-transformers` to enable the local backend"
            ),
        ) from exc
    try:
        _local_model = SentenceTransformer(_LOCAL_MODEL)
    except Exception as exc:  # pragma: no cover - network/model availability
        raise SourceUnavailableError(
            "embeddings",
            reason=(
                f"could not load local embedding model {_LOCAL_MODEL!r}: {exc}; "
                "set LYNCHPIN_LOCAL_EMBED_MODEL or pre-download the model"
            ),
        ) from exc
    return _local_model


def _embed_local(texts: list[str]) -> list[list[float]]:
    model = _load_local_model()
    # encode returns a numpy array; normalize handling without hard numpy import.
    vectors = model.encode(  # type: ignore[attr-defined]
        texts, normalize_embeddings=False, convert_to_numpy=True
    )
    return [[float(x) for x in row] for row in vectors]
