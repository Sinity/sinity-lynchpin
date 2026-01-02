from __future__ import annotations

import os
import textwrap
from datetime import datetime, timezone
from typing import Any, Iterable

from .embed_utils import (
    CONTEXT_MODEL,
    get_clients,
    ensure_collection,
)


class SearchError(RuntimeError):
    """Raised when an embedding query or vector search fails."""


def _determine_model(explicit: str | None) -> str:
    env_query = os.environ.get("VOYAGE_QUERY_MODEL")
    env_context = os.environ.get("VOYAGE_CONTEXT_MODEL")
    env_embed = os.environ.get("VOYAGE_EMBED_MODEL")
    return explicit or env_query or env_context or env_embed or CONTEXT_MODEL or "voyage-3"


def _build_filter(
    *,
    category: str | None = None,
    subcategory: str | None = None,
    channel: str | None = None,
    has_code: bool = False,
    has_urls: bool = False,
) -> dict[str, Any] | None:
    conditions: list[dict[str, Any]] = []
    if category:
        conditions.append({"category": category})
    if subcategory:
        conditions.append({"subcategory": subcategory})
    if channel:
        conditions.append({"channel": channel})
    if has_code:
        conditions.append({"has_code": True})
    if has_urls:
        conditions.append({"has_urls": True})
    if not conditions:
        return None
    if len(conditions) == 1:
        return conditions[0]
    return {"$and": conditions}


def _shorten(text: str, /, *, width: int = 200) -> str:
    clean = (text or "").replace("\n", " ").strip()
    if not clean:
        return ""
    return textwrap.shorten(clean, width=width, placeholder="…")


def run_search(
    query: str,
    *,
    n: int = 10,
    model: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
    channel: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    has_code: bool = False,
    has_urls: bool = False,
    reverse: bool = False,
) -> list[dict[str, Any]]:
    """Execute a semantic search and return curated result dictionaries."""

    if not query:
        return []

    vo, client = get_clients()
    collection = ensure_collection(client)

    chosen_model = _determine_model(model)

    try:
        if "context" in chosen_model:
            ctx = vo.contextualized_embed(inputs=[[query]], model=chosen_model, input_type="query")
            qv = ctx.results[0].embeddings[0]
        else:
            qv = vo.embed([query], model=chosen_model, input_type="query").embeddings[0]
    except Exception as exc:  # pragma: no cover - network failure path
        raise SearchError(f"Failed to embed query with model '{chosen_model}': {exc}") from exc

    where_filter = _build_filter(
        category=category,
        subcategory=subcategory,
        channel=channel,
        has_code=has_code,
        has_urls=has_urls,
    )

    def _parse_date(value: str | None) -> datetime | None:
        if not value:
            return None
        value = value.strip()
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            try:
                dt = datetime.strptime(value[:10], "%Y-%m-%d")
            except ValueError:
                return None
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt

    lower_bound = _parse_date(date_from)
    upper_bound = _parse_date(date_to)

    def _in_date_range(meta: dict[str, Any]) -> bool:
        candidate = meta.get("created") or meta.get("date")
        dt = _parse_date(candidate)
        if dt is None:
            return True  # if no date recorded, don't filter out
        if lower_bound and dt < lower_bound:
            return False
        if upper_bound and dt > upper_bound:
            return False
        return True

    fetch_multiplier = 8
    base_fetch = min(max(n * fetch_multiplier, n), 400)
    include_fields: Iterable[str] = ("metadatas", "documents", "distances", "embeddings")

    def extend_from_result(result: dict[str, Any], bucket: list[tuple], seen: set[str]) -> None:
        ids = result.get("ids") or []
        if not ids or not ids[0]:
            return
        distances = result.get("distances") or [[]]
        documents = result.get("documents") or [[]]
        metadatas = result.get("metadatas") or [[]]
        embeddings = result.get("embeddings") or [[]]

        for idx, eid in enumerate(ids[0]):
            if eid in seen:
                continue

            doc = ""
            meta: dict[str, Any] = {}
            emb: list[float] = []

            if idx < len(documents[0]):
                doc = documents[0][idx] or ""
            if idx < len(metadatas[0]):
                meta = metadatas[0][idx] or {}
            if idx < len(embeddings[0]):
                emb_val = embeddings[0][idx]
                if emb_val is None:
                    emb = []
                elif hasattr(emb_val, "tolist"):
                    emb = emb_val.tolist()
                else:
                    try:
                        emb = list(emb_val)
                    except TypeError:
                        emb = []

            has_text = bool((doc or "").strip())
            has_vector = any(abs(float(v)) > 1e-8 for v in emb) if emb else False
            if not has_text or not has_vector:
                continue

            dist = None
            if idx < len(distances[0]):
                dist = distances[0][idx]

            if not _in_date_range(meta):
                continue

            bucket.append((eid, dist, meta, doc))
            seen.add(eid)
            if len(bucket) >= n:
                break

    bucket: list[tuple[str, float | None, dict[str, Any], str]] = []
    seen_ids: set[str] = set()
    fetch_sizes = [base_fetch]
    if base_fetch < 400:
        fetch_sizes.append(400)

    for size in fetch_sizes:
        try:
            res = collection.query(
                query_embeddings=[qv],
                n_results=size,
                where=where_filter,
                include=list(include_fields),
            )
        except Exception as exc:  # pragma: no cover - realtime failure
            raise SearchError(f"Vector store query failed: {exc}") from exc

        extend_from_result(res, bucket, seen_ids)
        if len(bucket) >= n:
            break

    if not bucket:
        return []

    if reverse:
        bucket = list(reversed(bucket))

    results: list[dict[str, Any]] = []
    for index, (eid, dist, meta, doc) in enumerate(bucket, start=1):
        title = (meta or {}).get("title") if isinstance(meta, dict) else None
        if not title:
            title = (meta or {}).get("file_name") if isinstance(meta, dict) else None
        if not title:
            title = (meta or {}).get("source") if isinstance(meta, dict) else None
        if not title:
            title = eid

        snippet = _shorten(doc)

        results.append(
            {
                "index": index,
                "id": eid,
                "score": dist,
                "title": title,
                "category": (meta or {}).get("category", ""),
                "subcategory": (meta or {}).get("subcategory", ""),
                "source": (meta or {}).get("source", ""),
                "url": (meta or {}).get("url", ""),
                "meta": meta,
                "snippet": snippet,
                "embedding_model": (meta or {}).get("embedding_model", ""),
            }
        )

    return results
