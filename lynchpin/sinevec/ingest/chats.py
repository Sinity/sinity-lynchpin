from __future__ import annotations

import json
import logging
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..embed_utils import (
    CONTEXT_MODEL,
    DATA_ROOT,
    EMBED_DIM,
    MAX_DOC_TOKENS,
    STATE_DIR,
    count_tokens,
    detect_code,
    ensure_collection,
    get_clients,
    split_long_text,
)

logger = logging.getLogger(__name__)
SAFE_SEGMENT_TOKENS = max(1024, MAX_DOC_TOKENS - 2048)


def format_msg(role: str, content: str) -> str:
    role = (role or "unknown").upper()
    return f"{role}:\n{content}"


class ChatState:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data = self._load()
        self.processed: Dict[str, Dict[str, Dict[str, object]]] = self.data.get("processed", {})
        self.failed: Dict[str, Dict[str, str]] = self.data.get("failed", {})
        self.token_usage = int(self.data.get("token_usage", 0))
        self._dirty = False

    def _load(self) -> Dict[str, object]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text())
            except Exception:
                logger.warning("Unable to load chat state from %s; starting fresh", self.path)
        return {
            "processed": {},
            "failed": {},
            "token_usage": 0,
            "created_at": datetime.now().isoformat(),
            "last_updated": None,
        }

    def should_skip(self, platform: str, conv_id: str, updated: str | None, force: bool) -> bool:
        if force:
            return False
        stored = self.processed.get(platform, {}).get(conv_id)
        if isinstance(stored, dict):
            stored_updated = str(stored.get("updated") or "")
        else:
            stored_updated = str(stored or "")
        return stored_updated == str(updated or "")

    def mark_processed(self, platform: str, conv_id: str, updated: str | None, tokens: int, segments: int):
        platform_map = self.processed.setdefault(platform, {})
        platform_map[conv_id] = {
            "updated": str(updated or ""),
            "segments": segments,
            "token_usage": tokens,
        }
        self.token_usage += max(tokens, 0)
        self._dirty = True

    def mark_failed(self, platform: str, conv_id: str, error: str):
        key = f"{platform}#{conv_id}"
        self.failed[key] = {
            "error": error[:500],
            "timestamp": datetime.now().isoformat(),
        }
        self._dirty = True

    def save(self, force: bool = False):
        if not self._dirty and not force:
            return
        payload = dict(self.data)
        payload["processed"] = self.processed
        payload["failed"] = self.failed
        payload["token_usage"] = self.token_usage
        payload["last_updated"] = datetime.now().isoformat()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.rename(self.path)
        self.data = payload
        self._dirty = False


def _segment_messages(messages: List[Dict]) -> Tuple[List[Dict], List[str]]:
    segments: List[Dict] = []
    texts: List[str] = []
    counts: Dict[int, int] = {}

    for index, msg in enumerate(messages):
        text = format_msg(msg.get("role", "unknown"), str(msg.get("content", "")))
        tokens = count_tokens(text)
        parts = [text]
        if tokens > SAFE_SEGMENT_TOKENS:
            parts = split_long_text(text, max_tokens=SAFE_SEGMENT_TOKENS)
        counts[index] = len(parts)
        for part_idx, part in enumerate(parts):
            segments.append(
                {
                    "message": msg,
                    "index": index,
                    "part": part_idx if counts[index] > 1 else 0,
                    "total_parts": counts[index],
                }
            )
            texts.append(part)
    return segments, texts


def embed_conversation_messages(
    conv: Dict,
    collection,
    *,
    voyage_client=None,
) -> Tuple[int, int, int]:
    vo = voyage_client or get_clients()[0]
    total_tokens = 0
    embedded = 0
    platform = str(conv.get("source", "unknown"))
    conv_id = str(conv.get("id", ""))
    messages = conv.get("messages", [])
    if not messages:
        return 0, 0, 0

    segments, segment_texts = _segment_messages(messages)
    if not segment_texts:
        return 0, 0, 0

    tokens = [count_tokens(t) for t in segment_texts]
    windows: List[Tuple[int, int]] = []
    start = 0
    while start < len(segment_texts):
        total = 0
        end = start
        while end < len(segment_texts) and total + tokens[end] <= MAX_DOC_TOKENS:
            total += tokens[end]
            end += 1
        if end == start:
            end += 1
        windows.append((start, end))
        start = end

    title = str(conv.get("title", "Untitled"))
    created = str(conv.get("created", ""))
    updated = str(conv.get("updated", ""))
    num_messages = len(messages)

    segments_seen = len(segment_texts)

    def _build_payload(seg_idx: int, vector) -> Tuple[str, str, Dict[str, object]]:
        info = segments[seg_idx]
        message_index = info["index"]
        part_index = info["part"]
        total_parts = info["total_parts"]
        base_id = f"message#{platform}#{conv_id}#msg{message_index}"
        if total_parts > 1:
            msg_id = f"{base_id}_part{part_index}"
        else:
            msg_id = base_id
        doc = segment_texts[seg_idx][:65536]
        meta = {
            "granularity": "message",
            "contextualized": True,
            "embedding_model": CONTEXT_MODEL,
            "category": "conversations",
            "subcategory": platform,
            "source": f"chatlog/{platform}",
            "file_type": "ai_conversation",
            "conversation_id": conv_id,
            "conversation_title": title[:500],
            "message_index": message_index,
            "num_messages": num_messages,
            "segment_index": part_index,
            "segment_count": total_parts,
            "role": str(info["message"].get("role", "unknown")),
            "has_code": detect_code(doc),
            "created": created,
            "updated": updated,
        }
        return msg_id, doc, meta

    for w_start, w_end in windows:
        indices = list(range(w_start, w_end))
        window_texts = segment_texts[w_start:w_end]
        try:
            ctx = vo.contextualized_embed(
                inputs=[window_texts],
                model=CONTEXT_MODEL,
                input_type="document",
                output_dimension=EMBED_DIM,
            )
            vectors = ctx.results[0].embeddings if ctx.results else []
            window_tokens = int(getattr(ctx, "total_tokens", 0) or 0)
            total_tokens += window_tokens
            to_add_ids: List[str] = []
            to_add_embs: List[List[float]] = []
            to_add_docs: List[str] = []
            to_add_meta: List[Dict] = []
            for seg_idx, vector in zip(indices, vectors):
                msg_id, doc, meta = _build_payload(seg_idx, vector)
                to_add_ids.append(msg_id)
                to_add_embs.append(vector)
                to_add_docs.append(doc)
                to_add_meta.append(meta)
            if to_add_ids:
                collection.delete(ids=to_add_ids)
                collection.add(ids=to_add_ids, embeddings=to_add_embs, documents=to_add_docs, metadatas=to_add_meta)
                embedded += len(to_add_ids)
            continue
        except Exception as exc:
            logger.warning("Batch embed failed for %s window %s-%s: %s", conv_id, w_start, w_end, exc)

        # Fallback: embed each segment individually
        fallback_ids: List[str] = []
        fallback_embs: List[List[float]] = []
        fallback_docs: List[str] = []
        fallback_meta: List[Dict] = []
        for seg_idx in indices:
            try:
                single = vo.contextualized_embed(
                    inputs=[[segment_texts[seg_idx]]],
                    model=CONTEXT_MODEL,
                    input_type="document",
                    output_dimension=EMBED_DIM,
                )
            except Exception as single_exc:
                logger.error(
                    "Failed to embed conversation %s segment %s: %s",
                    conv_id,
                    seg_idx,
                    single_exc,
                )
                continue
            vectors = single.results[0].embeddings if single.results else []
            if not vectors:
                continue
            total_tokens += int(getattr(single, "total_tokens", 0) or 0)
            msg_id, doc, meta = _build_payload(seg_idx, vectors[0])
            fallback_ids.append(msg_id)
            fallback_embs.append(vectors[0])
            fallback_docs.append(doc)
            fallback_meta.append(meta)
        if fallback_ids:
            collection.delete(ids=fallback_ids)
            collection.add(ids=fallback_ids, embeddings=fallback_embs, documents=fallback_docs, metadatas=fallback_meta)
            embedded += len(fallback_ids)

    return embedded, total_tokens, segments_seen
    platform = conv.get("source", "unknown")
    conv_id = str(conv.get("id", ""))
    messages = conv.get("messages", [])
    if not messages:
        return 0, 0

    msg_texts: List[str] = [format_msg(m.get("role", "unknown"), str(m.get("content", ""))) for m in messages]
    msg_tokens: List[int] = [count_tokens(t) for t in msg_texts]

    windows: List[Tuple[int, int]] = []
    start = 0
    while start < len(msg_texts):
        total = 0
        end = start
        while end < len(msg_texts) and total + msg_tokens[end] <= MAX_DOC_TOKENS:
            total += msg_tokens[end]
            end += 1
        if end == start:
            end = start + 1
        windows.append((start, end))
        start = end

    title = str(conv.get("title", "Untitled"))
    n = len(messages)

    for (w_start, w_end) in windows:
        window_texts = msg_texts[w_start:w_end]
        try:
            ctx = vo.contextualized_embed(
                inputs=[window_texts], model=CONTEXT_MODEL, input_type="document", output_dimension=EMBED_DIM
            )
        except Exception:
            continue
        vectors = ctx.results[0].embeddings if ctx.results else []
        total_tokens += int(getattr(ctx, "total_tokens", 0) or 0)

        to_add_ids: List[str] = []
        to_add_embs: List[List[float]] = []
        to_add_docs: List[str] = []
        to_add_meta: List[Dict] = []

        for offset, (msg, vec) in enumerate(zip(messages[w_start:w_end], vectors)):
            i = w_start + offset
            msg_id = f"message#{platform}#{conv_id}#msg{i}"
            try:
                existing = collection.get(ids=[msg_id])
                if existing.get("ids"):
                    continue
            except Exception:
                pass
            doc = msg_texts[i][:65536]
            metadata = {
                "granularity": "message",
                "contextualized": True,
                "embedding_model": CONTEXT_MODEL,
                "category": "conversations",
                "subcategory": platform,
                "source": f"chatlog/{platform}",
                "file_type": "ai_conversation",
                "conversation_id": conv_id,
                "conversation_title": title[:500],
                "message_index": i,
                "num_messages": n,
                "role": str(msg.get("role", "unknown")),
                "has_code": detect_code(doc),
                "created": str(conv.get("created", "")),
                "updated": str(conv.get("updated", "")),
            }
            to_add_ids.append(msg_id)
            to_add_embs.append(vec)
            to_add_docs.append(doc)
            to_add_meta.append(metadata)

        if to_add_ids:
            collection.add(ids=to_add_ids, embeddings=to_add_embs, documents=to_add_docs, metadatas=to_add_meta)
            embedded += len(to_add_ids)

    return embedded, total_tokens


def _parse_chatgpt_payload(data: List[Dict]) -> List[Dict]:
    conversations: List[Dict] = []
    for conv in data or []:
        messages: List[Dict] = []
        mapping = conv.get("mapping", {})
        for _msg_id, msg_data in mapping.items():
            msg = msg_data.get("message")
            if not msg or not msg.get("content"):
                continue
            role = msg.get("author", {}).get("role", "unknown")
            content = msg["content"]
            if content.get("content_type") == "text":
                parts = content.get("parts", [])
                text = None
                if parts and isinstance(parts[0], str):
                    text = parts[0]
                if text:
                    messages.append({
                        "role": role,
                        "content": text,
                        "timestamp": msg.get("create_time", ""),
                    })
        try:
            messages.sort(key=lambda m: (m.get("timestamp") is None, str(m.get("timestamp", ""))))
        except Exception:
            pass
        if messages:
            conversations.append({
                "id": conv.get("id", ""),
                "title": conv.get("title", "Untitled"),
                "messages": messages,
                "created": conv.get("create_time", ""),
                "updated": conv.get("update_time", ""),
                "source": "chatgpt",
            })
    return conversations


def load_chatgpt_conversations(file_path: Path) -> List[Dict]:
    conversations: List[Dict] = []
    if file_path.exists():
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            conversations.extend(_parse_chatgpt_payload(data))
        except Exception:
            pass
    if conversations:
        return conversations

    base = file_path.parent
    archives = sorted(base.glob("chatgpt-data-*.zip"), reverse=True)
    for archive in archives:
        try:
            with zipfile.ZipFile(archive, "r") as z:
                with z.open("conversations.json") as raw:
                    data = json.loads(raw.read().decode("utf-8"))
            conversations.extend(_parse_chatgpt_payload(data))
            if conversations:
                break
        except Exception:
            continue
    return conversations


def load_claude_conversations_from_extracted(base_path: Path) -> List[Dict]:
    conversations: List[Dict] = []
    if not base_path.exists():
        return conversations
    for folder in base_path.iterdir():
        if not folder.is_dir() or len(folder.name) != 36:
            continue
        chat_file = folder / "chat.json"
        if not chat_file.exists():
            continue
        try:
            with open(chat_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            messages: List[Dict] = []
            for msg in data.get("messages", []):
                role = msg.get("sender", "unknown")
                content = msg.get("content", "")
                if isinstance(content, list) and content:
                    text_parts = []
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            text_parts.append(part.get("text", ""))
                    content = "\n".join(text_parts)
                messages.append({"role": role, "content": content})
            if messages:
                conversations.append({
                    "id": folder.name,
                    "title": data.get("name", "Untitled"),
                    "messages": messages,
                    "created": data.get("created_at", ""),
                    "updated": data.get("updated_at", ""),
                    "source": "claude",
                })
        except Exception:
            pass
    return conversations


def load_claude_conversations_from_zip(archive_path: Path) -> List[Dict]:
    conversations: List[Dict] = []
    if not archive_path.exists():
        return conversations
    try:
        with zipfile.ZipFile(archive_path, "r") as z:
            with z.open("conversations.json") as f:
                data = json.load(f)
        if isinstance(data, list):
            for conv in data:
                messages: List[Dict] = []
                for msg in conv.get("chat_messages", []):
                    messages.append({
                        "role": msg.get("sender", "unknown"),
                        "content": msg.get("text", ""),
                        "timestamp": msg.get("created_at", ""),
                    })
                try:
                    messages.sort(key=lambda m: str(m.get("timestamp", "")))
                except Exception:
                    pass
                if messages:
                    conversations.append({
                        "id": conv.get("uuid", ""),
                        "title": conv.get("name", "Untitled"),
                        "messages": messages,
                        "created": conv.get("created_at", ""),
                        "updated": conv.get("updated_at", ""),
                        "source": "claude",
                    })
    except Exception:
        pass
    return conversations


def load_cody_conversations(file_path: Path) -> List[Dict]:
    conversations: List[Dict] = []
    if not file_path.exists():
        return conversations
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for conv in data:
            messages: List[Dict] = []
            for inter in conv.get("interactions", []):
                if inter.get("humanMessage"):
                    messages.append({
                        "role": "user",
                        "content": inter["humanMessage"].get("text", ""),
                    })
                if inter.get("assistantMessage"):
                    messages.append({
                        "role": "assistant",
                        "content": inter["assistantMessage"].get("text", ""),
                    })
            if messages:
                conversations.append({
                    "id": str(conv.get("id", "")),
                    "title": messages[0]["content"][:100] if messages else "Untitled",
                    "messages": messages,
                    "created": conv.get("timestamp", ""),
                    "updated": conv.get("timestamp", ""),
                    "source": "cody",
                })
    except Exception:
        pass
    return conversations


def embed_chats_pipeline(
    platform: str = "all",
    limit: int = 0,
    force: bool = False,
    *,
    voyage_client=None,
    vector_collection=None,
    state_path: Optional[Path] = None,
) -> Tuple[int, int, int]:
    """Embed conversations from chatlog sources via contextualized per-message embeddings.
    Returns (conversations_processed, messages_embedded, tokens_used).
    """
    vo = voyage_client
    collection = vector_collection
    if vo is None or collection is None:
        vo, client = get_clients()
        collection = ensure_collection(client, "unified")

    state = ChatState(state_path or (STATE_DIR / "chat_embed_state.json"))

    base = DATA_ROOT / "chatlog"
    conversations: List[Dict] = []

    chatgpt_file = base / "conversations.json"
    chatgpt_convs = load_chatgpt_conversations(chatgpt_file)
    claude_extracted = load_claude_conversations_from_extracted(base)
    claude_zip_convs: List[Dict] = []
    if not claude_extracted:
        for z in sorted(base.glob("claude-ai-data-*.zip")):
            more = load_claude_conversations_from_zip(z)
            if more:
                claude_zip_convs = more
                break
    cody_candidates = sorted(base.glob("cody-chat-history-*.json"))
    cody_convs: List[Dict] = load_cody_conversations(cody_candidates[-1]) if cody_candidates else []

    if platform in ("all", "chatgpt"):
        conversations.extend(chatgpt_convs)
    if platform in ("all", "claude"):
        conversations.extend(claude_extracted if claude_extracted else claude_zip_convs)
    if platform in ("all", "cody"):
        conversations.extend(cody_convs)

    if not conversations:
        return 0, 0, 0
    conversations.sort(key=lambda c: str(c.get("updated", "")), reverse=True)

    filtered: List[Dict] = []
    for conv in conversations:
        conv_platform = str(conv.get("source", "unknown"))
        conv_id = str(conv.get("id", ""))
        updated = str(conv.get("updated") or conv.get("created") or "")
        if state.should_skip(conv_platform, conv_id, updated, force):
            continue
        filtered.append(conv)

    conversations = filtered
    if limit and limit > 0:
        conversations = conversations[: limit]

    if not conversations:
        return 0, 0, 0

    total_embedded = 0
    total_tokens = 0
    processed = 0

    for conv in conversations:
        conv_platform = str(conv.get("source", "unknown"))
        conv_id = str(conv.get("id", ""))
        updated = str(conv.get("updated") or conv.get("created") or "")
        try:
            emb_count, tok, segments_seen = embed_conversation_messages(
                conv,
                collection,
                voyage_client=vo,
            )
        except Exception as exc:
            logger.error("Failed to embed conversation %s/%s: %s", conv_platform, conv_id, exc)
            state.mark_failed(conv_platform, conv_id, str(exc))
            continue

        total_embedded += emb_count
        total_tokens += tok
        if emb_count > 0:
            state.mark_processed(conv_platform, conv_id, updated, tok, segments_seen)
            processed += 1
        else:
            state.mark_failed(conv_platform, conv_id, "No segments embedded")
        state.save()

    state.save(force=True)
    return processed, total_embedded, total_tokens
