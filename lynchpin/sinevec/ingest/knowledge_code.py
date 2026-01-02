from __future__ import annotations

from pathlib import Path
from typing import List, Tuple
from datetime import datetime
import json
import signal

from ..embed_utils import (
    CONTEXT_MODEL,
    EMBED_DIM,
    STATE_DIR,
    detect_code,
    get_clients,
    ensure_collection,
    simple_chunk_document,
    group_chunks_for_voyage,
    should_skip_file,
)


class EmbeddingState:
    def __init__(self, state_file: Path):
        self.state_file = state_file
        self.state = self.load_state()
        if 'created_at' not in self.state:
            self.state['created_at'] = datetime.now().isoformat()
        self.state.setdefault('last_updated', self.state['created_at'])
        self.token_usage = self.state.get('token_usage', {})
        self.processed_files = set(self.state.get('processed_files', []))
        self.failed_files = self.state.get('failed_files', {})
        self.current_file = None
        self.start_time = datetime.now().isoformat()
        signal.signal(signal.SIGINT, self.handle_interrupt)
        signal.signal(signal.SIGTERM, self.handle_interrupt)

    def load_state(self):
        if self.state_file.exists():
            return json.loads(self.state_file.read_text())
        return {'token_usage': {}, 'processed_files': [], 'failed_files': {}}

    def save_state(self):
        self.state['token_usage'] = self.token_usage
        self.state['processed_files'] = list(self.processed_files)
        self.state['failed_files'] = self.failed_files
        self.state['last_updated'] = datetime.now().isoformat()
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.state, indent=2))
        tmp.rename(self.state_file)

    def handle_interrupt(self, signum, frame):
        if self.current_file and self.current_file in self.processed_files:
            self.processed_files.remove(self.current_file)
        self.save_state()
        raise SystemExit(0)

    def mark_processed(self, file_path: str, tokens_used: int = 0):
        self.processed_files.add(file_path)
        if tokens_used:
            self.token_usage['total'] = self.token_usage.get('total', 0) + tokens_used
            self.token_usage[file_path] = tokens_used
        if len(self.processed_files) % 10 == 0:
            self.save_state()

    def mark_failed(self, file_path: str, error: str):
        self.failed_files[file_path] = {'error': error[:500], 'timestamp': datetime.now().isoformat()}


def embed_file(vo, collection, file_path: Path, root_dir: Path, category: str, state: EmbeddingState, force: bool = False) -> int:
    file_str = str(file_path)
    if not force and file_str in state.processed_files:
        return 0
    state.current_file = file_str
    try:
        content = file_path.read_text(encoding='utf-8', errors='ignore')
        if not content.strip():
            return 0
        try:
            relative_path = file_path.relative_to(root_dir)
        except ValueError:
            relative_path = Path(file_path.name)
        subcategory = relative_path.parts[0] if len(relative_path.parts) > 1 else category
        try:
            file_stat = file_path.stat()
        except OSError:
            file_stat = None
        created_iso = datetime.fromtimestamp(file_stat.st_ctime).isoformat() if file_stat else None
        updated_iso = datetime.fromtimestamp(file_stat.st_mtime).isoformat() if file_stat else None
        size_bytes = file_stat.st_size if file_stat else None
        chunks = simple_chunk_document(content)
        groups = group_chunks_for_voyage(chunks)
        if not groups:
            state.mark_processed(file_str, 0)
            return 0
        total_tokens = 0
        embedded_at = datetime.now().isoformat()
        for group_idx, chunk_group in enumerate(groups):
            if not chunk_group or not any(c.strip() for c in chunk_group):
                continue
            embeds = vo.contextualized_embed(
                inputs=[chunk_group],
                model=CONTEXT_MODEL,
                input_type='document',
                output_dimension=EMBED_DIM,
            )
            vectors = embeds.results[0].embeddings if embeds.results else []
            batch_ids: List[str] = []
            batch_embeddings: List[List[float]] = []
            batch_docs: List[str] = []
            batch_meta: List[dict] = []
            for chunk_idx, (chunk, embedding) in enumerate(zip(chunk_group, vectors)):
                chunk_id = f"{file_str}#g{group_idx}#c{chunk_idx}"
                try:
                    collection.delete(ids=[chunk_id])
                except Exception:
                    pass
                batch_ids.append(chunk_id)
                batch_embeddings.append(embedding)
                batch_docs.append(chunk[:65536])
                meta = {
                    'source': str(file_path),
                    'file_name': file_path.name,
                    'category': category,
                    'subcategory': subcategory,
                    'relative_path': str(relative_path),
                    'file_type': 'source_code' if category == 'code' else 'knowledge_document',
                    'group_index': group_idx,
                    'chunk_index': chunk_idx,
                    'total_groups': len(groups),
                    'embedded_at': embedded_at,
                    'has_code': detect_code(chunk),
                    'has_urls': 'http://' in chunk or 'https://' in chunk,
                    'embedding_model': CONTEXT_MODEL,
                }
                if size_bytes is not None:
                    meta['size_bytes'] = int(size_bytes)
                if updated_iso:
                    meta['updated'] = updated_iso
                if created_iso:
                    meta['created'] = created_iso
                batch_meta.append(meta)
            if batch_ids:
                collection.add(
                    embeddings=batch_embeddings,
                    documents=batch_docs,
                    metadatas=batch_meta,
                    ids=batch_ids,
                )
            total_tokens += int(getattr(embeds, 'total_tokens', 0) or 0)
        if total_tokens > 0:
            state.mark_processed(file_str, total_tokens)
        else:
            state.mark_failed(file_str, 'No groups successfully embedded')
        state.current_file = None
        return total_tokens
    except Exception as e:
        state.mark_failed(file_str, str(e))
        state.current_file = None
        return 0


def scan_files(directory: Path, state: EmbeddingState, force: bool) -> List[Path]:
    files: List[Path] = []
    for file_path in directory.rglob('*'):
        if not file_path.is_file():
            continue
        if should_skip_file(file_path):
            continue
        file_str = str(file_path)
        if not force and file_str in state.processed_files:
            continue
        files.append(file_path)
    files.sort(key=lambda f: f.stat().st_size)
    return files


def embed_knowledge_code_pipeline(kb_dir: Path, code_dir: Path, force: bool = False) -> Tuple[int, int]:
    """Embed knowledgebase and code trees; returns (files_processed, tokens_used)."""
    vo, client = get_clients()
    state = EmbeddingState(state_file=STATE_DIR / "knowledge_code_state.json")
    collection = ensure_collection(client)
    sources = []
    if kb_dir and kb_dir.exists():
        sources.append((kb_dir, "knowledgebase"))
    if code_dir and code_dir.exists():
        sources.append((code_dir, "code"))
    all_files: List[Tuple[Path, Path, str]] = []
    for src, cname in sources:
        files = scan_files(src, state, force=force)
        all_files.extend((f, src, cname) for f in files)
    if not all_files:
        return 0, state.token_usage.get('total', 0)
    total_tokens = state.token_usage.get('total', 0)
    processed = 0
    for file_path, root_dir, category in all_files:
        tokens = embed_file(vo, collection, file_path, root_dir, category, state, force=force)
        if tokens > 0:
            total_tokens += tokens
        processed += 1
        if processed % 25 == 0:
            state.save_state()
    state.save_state()
    return processed, total_tokens
