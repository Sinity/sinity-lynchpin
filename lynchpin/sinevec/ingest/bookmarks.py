from __future__ import annotations

import csv
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..embed_utils import (
    CONTEXT_MODEL,
    DATA_ROOT,
    EMBED_DIM,
    STATE_DIR,
    contextual_windows,
    domain_of,
    get_clients,
    ensure_collection,
    split_long_text,
)

logger = logging.getLogger(__name__)


class BookmarkState:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data = self._load()
        self.processed_ids = set(self.data.get("processed_ids", []))
        self.failed: Dict[str, Dict[str, str]] = self.data.get("failed", {})
        self.token_usage = int(self.data.get("token_usage", 0))
        self.last_saved_count = 0

    def _load(self) -> Dict[str, object]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text())
            except Exception:
                logger.warning("Unable to load bookmark state from %s; starting fresh", self.path)
        return {
            "processed_ids": [],
            "failed": {},
            "token_usage": 0,
            "created_at": datetime.now().isoformat(),
            "last_updated": None,
        }

    def mark_processed(self, bookmark_id: str, tokens: int):
        self.processed_ids.add(bookmark_id)
        self.token_usage += max(tokens, 0)

    def mark_failed(self, bookmark_id: str, error: str):
        self.failed[bookmark_id] = {
            "error": error[:500],
            "timestamp": datetime.now().isoformat(),
        }

    def save(self, force: bool = False):
        if not force and self.last_saved_count == len(self.processed_ids):
            return
        payload = dict(self.data)
        payload["processed_ids"] = sorted(self.processed_ids)
        payload["failed"] = self.failed
        payload["token_usage"] = self.token_usage
        payload["last_updated"] = datetime.now().isoformat()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.rename(self.path)
        self.last_saved_count = len(self.processed_ids)
        self.data = payload


def parse_highlights(raw: str) -> List[str]:
    s = (raw or '').strip()
    if not s:
        return []
    import re
    chunks = re.split(r"(?:^|\n)\s*Highlight:\s*", s)
    return [c.strip() for c in chunks if c.strip()]


def build_summary_text(row: Dict[str, str]) -> str:
    title = row.get('title') or ''
    url = row.get('url') or ''
    folder = row.get('folder') or ''
    tags = (row.get('tags') or '').strip()
    created = row.get('created') or ''
    excerpt = (row.get('excerpt') or '').strip()
    note = (row.get('note') or '').strip()
    lines = [f"Title: {title}", f"URL: {url}", f"Folder: {folder}", f"Tags: {tags}", f"Created: {created}"]
    if excerpt:
        lines += ["", "Excerpt:", excerpt]
    if note:
        lines += ["", "Note:", note]
    return "\n".join(lines)


def embed_bookmarks_csv(
    csv_path: Path,
    limit: int = 0,
    force: bool = False,
    *,
    voyage_client=None,
    vector_collection=None,
    state_path: Optional[Path] = None,
) -> Tuple[int, int, int]:
    vo = voyage_client
    collection = vector_collection
    if vo is None or collection is None:
        vo, vector_client = get_clients()
        collection = ensure_collection(vector_client)
    state = BookmarkState(state_path or (STATE_DIR / "raindrop_embed_state.json"))

    processed = 0
    embedded = 0
    total_tokens = 0

    if not csv_path.exists():
        raise FileNotFoundError(f"Bookmark CSV not found: {csv_path}")

    with csv_path.open('r', encoding='utf-8', errors='ignore') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if limit and processed >= limit:
                break
            bid = str(row.get('id') or '').strip()
            if not bid:
                continue
            if not force and bid in state.processed_ids:
                continue
            title = row.get('title') or ''
            url = row.get('url') or ''
            tags = [t.strip() for t in (row.get('tags') or '').split(',') if t.strip()]
            folder = row.get('folder') or ''
            created = row.get('created') or ''
            favorite = str(row.get('favorite') or '').lower() == 'true'
            cover = row.get('cover') or ''
            dom = domain_of(url)

            summary_text = build_summary_text(row)
            highlights = parse_highlights(row.get('highlights') or '')

            chunk_texts: List[str] = [summary_text]
            chunk_ids: List[str] = [f"raindrop#{bid}#summary"]
            chunk_metas: List[Dict] = [{
                'category': 'bookmarks',
                'subcategory': folder or dom or 'general',
                'source': f'raindrop://{bid}',
                'file_type': 'bookmark_summary',
                'title': title[:500],
                'url': url,
                'domain': dom,
                'tags': ', '.join(tags) if tags else '',
                'created': created,
                'favorite': favorite,
                'cover': cover,
            }]

            for hi, h in enumerate(highlights):
                parts = split_long_text(h, max_tokens=8000)
                for pi, part in enumerate(parts):
                    hid = f"raindrop#{bid}#hl{hi}"
                    if len(parts) > 1:
                        hid += f"_part{pi}"
                    chunk_texts.append(part)
                    chunk_ids.append(hid)
                    chunk_metas.append({
                        'category': 'bookmarks',
                        'subcategory': folder or dom or 'general',
                        'source': f'raindrop://{bid}',
                        'file_type': 'bookmark_highlight',
                        'title': title[:500],
                        'url': url,
                        'domain': dom,
                        'tags': ', '.join(tags) if tags else '',
                        'created': created,
                        'favorite': favorite,
                        'cover': cover,
                        'highlight_index': hi,
                        'part_index': pi if len(parts) > 1 else 0,
                    })

            try:
                collection.delete(ids=chunk_ids)
            except Exception:
                pass

            windows = contextual_windows(chunk_texts, always_include_first=True)
            seen = set()
            bookmark_tokens = 0
            bookmark_embedded = False
            for s, e in windows:
                inputs = [chunk_texts[s:e]]
                try:
                    embeds = vo.contextualized_embed(inputs, model=CONTEXT_MODEL, input_type='document', output_dimension=EMBED_DIM)
                    vectors = embeds.results[0].embeddings
                    window_tokens = int(getattr(embeds, 'total_tokens', 0) or 0)
                    total_tokens += window_tokens
                    bookmark_tokens += window_tokens
                except Exception as exc:
                    logger.warning("Failed to embed bookmark %s window %s-%s: %s", bid, s, e, exc)
                    continue
                add_ids: List[str] = []
                add_vecs: List[List[float]] = []
                add_docs: List[str] = []
                add_meta: List[Dict] = []
                for i, vec in enumerate(vectors):
                    gid = chunk_ids[s+i]
                    if gid in seen:
                        continue
                    seen.add(gid)
                    add_ids.append(gid)
                    add_vecs.append(vec)
                    add_docs.append(inputs[0][i][:65536])
                    meta = dict(chunk_metas[s+i])
                    meta['embedding_model'] = CONTEXT_MODEL
                    add_meta.append(meta)
                if add_ids:
                    collection.add(ids=add_ids, embeddings=add_vecs, documents=add_docs, metadatas=add_meta)
                    embedded += len(add_ids)
                    bookmark_embedded = True

            if bookmark_embedded:
                state.mark_processed(bid, bookmark_tokens)
                processed += 1
            else:
                state.mark_failed(bid, "No embeddings generated")
            state.save()

    state.save(force=True)
    return processed, embedded, total_tokens
