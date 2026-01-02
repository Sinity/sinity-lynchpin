from __future__ import annotations

import uuid
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import tiktoken
import voyageai
from dotenv import load_dotenv

try:  # Optional during cold start
    from qdrant_client import QdrantClient
    from qdrant_client.http import models as qmodels
except ImportError:  # pragma: no cover
    QdrantClient = None  # type: ignore
    qmodels = None  # type: ignore

load_dotenv()

UNIFIED = os.environ.get("QDRANT_COLLECTION", "unified")
EMBED_DIM = int(os.environ.get("EMBED_OUTPUT_DIMENSION", "1024"))

QDRANT_HOST = os.environ.get("QDRANT_HOST", "127.0.0.1")
QDRANT_HTTP_PORT = int(os.environ.get("QDRANT_HTTP_PORT", os.environ.get("QDRANT_PORT", "6333")))
QDRANT_GRPC_PORT_RAW = os.environ.get("QDRANT_GRPC_PORT")
QDRANT_GRPC_PORT = int(QDRANT_GRPC_PORT_RAW) if QDRANT_GRPC_PORT_RAW else None
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY") or None
QDRANT_HTTPS = os.environ.get("QDRANT_USE_HTTPS", "0").strip().lower() in {"1", "true", "yes"}
QDRANT_VECTOR_SIZE = int(os.environ.get("QDRANT_VECTOR_SIZE", str(EMBED_DIM)))
QDRANT_TIMEOUT = float(os.environ.get("QDRANT_CLIENT_TIMEOUT", "20.0"))
DOC_PAYLOAD_KEY = "_document"
EXTERNAL_ID_KEY = "_external_id"

# Defaults: contextualized uses 'voyage-context-3' unless overridden; standard uses 'voyage-3'
CONTEXT_MODEL = os.environ.get("VOYAGE_CONTEXT_MODEL", "voyage-context-3")
DEFAULT_MODEL = os.environ.get("VOYAGE_EMBED_MODEL", "voyage-3")
MAX_DOC_TOKENS = int(os.environ.get("CONTEXT_DOC_TOKEN_LIMIT", "30000"))
MAX_FILE_BYTES = int(os.environ.get("EMBED_MAX_FILE_BYTES", str(2 * 1024 * 1024)))

def _xdg_base(env_name: str, fallback: Path) -> Path:
    base = os.environ.get(env_name)
    if base:
        return Path(base).expanduser()
    return fallback


def _resolve_path(var_names: Sequence[str], default: Path) -> Path:
    for name in var_names:
        value = os.environ.get(name)
        if value:
            return Path(value).expanduser().resolve()
    return default.expanduser().resolve()


DEFAULT_DATA_ROOT = _xdg_base("XDG_DATA_HOME", Path.home() / ".local" / "share") / "sinevec"
DEFAULT_STATE_DIR = _xdg_base("XDG_STATE_HOME", Path.home() / ".local" / "state") / "sinevec"
DEFAULT_LOG_DIR = _xdg_base("XDG_STATE_HOME", Path.home() / ".local" / "state") / "sinevec" / "log"

DATA_ROOT = _resolve_path(("SINEVEC_DATA_ROOT", "SINEVEC_DATA_DIR"), DEFAULT_DATA_ROOT)
STATE_DIR = _resolve_path(("SINEVEC_STATE_DIR",), DEFAULT_STATE_DIR)
LOG_DIR = _resolve_path(("SINEVEC_LOG_DIR",), DEFAULT_LOG_DIR)


def _normalize_vector(vector: Sequence[float] | Iterable[float]) -> List[float]:
    if isinstance(vector, list):
        return [float(v) for v in vector]
    return [float(v) for v in list(vector)]


def _encode_point_id(external_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, external_id))


def _ensure_qdrant_installed() -> None:
    if QdrantClient is None or qmodels is None:  # pragma: no cover - import guard
        raise RuntimeError(
            "qdrant-client is required but is not installed. "
            "Install qdrant-client to continue."
        )


def _field_condition(key: str, value: Any) -> Optional["qmodels.FieldCondition"]:
    if qmodels is None:
        return None
    if isinstance(value, dict):
        if "$in" in value and isinstance(value["$in"], (list, tuple, set)):
            return qmodels.FieldCondition(key=key, match=qmodels.MatchAny(any=list(value["$in"])))
        if "$eq" in value:
            return qmodels.FieldCondition(key=key, match=qmodels.MatchValue(value=value["$eq"]))
        # fall through for unsupported operators
        value = value.get("$eq") or value.get("value") or value
    return qmodels.FieldCondition(key=key, match=qmodels.MatchValue(value=value))


def _convert_where(where: Optional[Dict[str, Any]]) -> Optional["qmodels.Filter"]:
    if not where or qmodels is None:
        return None

    def _collect_conditions(node: Dict[str, Any]) -> Tuple[List["qmodels.FieldCondition"], List["qmodels.FieldCondition"], List["qmodels.FieldCondition"]]:
        must: List["qmodels.FieldCondition"] = []
        should: List["qmodels.FieldCondition"] = []
        must_not: List["qmodels.FieldCondition"] = []
        for key, value in node.items():
            if key == "$and" and isinstance(value, list):
                for part in value:
                    if isinstance(part, dict):
                        sub_must, sub_should, sub_must_not = _collect_conditions(part)
                        must.extend(sub_must)
                        should.extend(sub_should)
                        must_not.extend(sub_must_not)
            elif key == "$or" and isinstance(value, list):
                for part in value:
                    if isinstance(part, dict):
                        sub_must, sub_should, sub_must_not = _collect_conditions(part)
                        should.extend(sub_must or sub_should)
                        must_not.extend(sub_must_not)
            elif key == "$not":
                parts = value if isinstance(value, list) else [value]
                for part in parts:
                    if isinstance(part, dict):
                        sub_must, sub_should, sub_must_not = _collect_conditions(part)
                        must_not.extend(sub_must or sub_should or [])
                        must_not.extend(sub_must_not)
            else:
                cond = _field_condition(key, value)
                if cond:
                    must.append(cond)
        return must, should, must_not

    must, should, must_not = _collect_conditions(where)
    if not (must or should or must_not):
        return None
    return qmodels.Filter(
        must=must or None,
        should=should or None,
        must_not=must_not or None,
    )


class VectorCollection:
    backend_name = "qdrant"

    def __init__(self, client: "QdrantClient", name: str, vector_size: int):
        self.client = client
        self.name = name
        self.vector_size = vector_size

    def add(self, *, ids: Sequence[str], embeddings: Sequence[Sequence[float]], documents: Sequence[str], metadatas: Sequence[Dict[str, Any]]):
        _ensure_qdrant_installed()
        points: List["qmodels.PointStruct"] = []
        for idx, pid in enumerate(ids):
            try:
                vector = embeddings[idx]
            except IndexError:
                continue
            vector_list = _normalize_vector(vector)
            if len(vector_list) != self.vector_size:
                raise ValueError(f"Vector dimension mismatch for collection '{self.name}': expected {self.vector_size}, got {len(vector_list)}")
            doc = documents[idx] if idx < len(documents) else ""
            meta = metadatas[idx] if idx < len(metadatas) else {}
            payload = dict(meta or {})
            external_id = str(pid)
            payload[EXTERNAL_ID_KEY] = external_id
            payload[DOC_PAYLOAD_KEY] = doc
            points.append(
                qmodels.PointStruct(
                    id=_encode_point_id(external_id),
                    vector=vector_list,
                    payload=payload,
                )
            )
        if points:
            self.client.upsert(collection_name=self.name, points=points, wait=True)

    def delete(self, *, ids: Optional[Sequence[str]] = None, where: Optional[Dict[str, Any]] = None):
        _ensure_qdrant_installed()
        if ids:
            selector = qmodels.PointIdsList(ids=[_encode_point_id(str(i)) for i in ids])
            self.client.delete(collection_name=self.name, points_selector=selector, wait=True)
        elif where:
            q_filter = _convert_where(where)
            if q_filter:
                self.client.delete(collection_name=self.name, filter=q_filter, wait=True)

    def _build_result(self, points: Sequence["qmodels.Record"], *, include: Optional[Sequence[str]]) -> Dict[str, List[Any]]:
        include_set = set(include or [])
        want_docs = not include_set or "documents" in include_set
        want_meta = not include_set or "metadatas" in include_set
        want_embeddings = not include_set or "embeddings" in include_set

        ids: List[str] = []
        documents: List[str] = []
        metadatas: List[Dict[str, Any]] = []
        embeddings: List[List[float]] = []

        for point in points:
            payload = dict(point.payload or {})
            external_id = payload.pop(EXTERNAL_ID_KEY, None)
            ids.append(external_id if external_id is not None else str(point.id))
            document = payload.pop(DOC_PAYLOAD_KEY, "")
            if want_docs:
                documents.append(document)
            if want_meta:
                metadatas.append(payload)
            if want_embeddings:
                vector: Any = getattr(point, "vector", None)
                if vector is None and getattr(point, "vectors", None):
                    stored = point.vectors
                    if isinstance(stored, dict):
                        vector = next(iter(stored.values()))
                if vector is None:
                    embeddings.append([])
                else:
                    embeddings.append(_normalize_vector(vector))

        result: Dict[str, List[Any]] = {"ids": ids}
        if want_docs:
            result["documents"] = documents
        if want_meta:
            result["metadatas"] = metadatas
        if want_embeddings:
            result["embeddings"] = embeddings
        return result

    def get(self, *, ids: Optional[Sequence[str]] = None, where: Optional[Dict[str, Any]] = None, include: Optional[Sequence[str]] = None, limit: Optional[int] = None, offset: Optional[Any] = None):
        _ensure_qdrant_installed()
        with_vectors = not include or "embeddings" in include
        with_payload = not include or "metadatas" in include or "documents" in include

        if ids:
            points = self.client.retrieve(
                collection_name=self.name,
                ids=[_encode_point_id(str(i)) for i in ids],
                with_payload=with_payload,
                with_vectors=with_vectors,
            )
            return self._build_result(points, include=include)

        q_filter = _convert_where(where)
        batch_limit = limit or 1024
        points: List["qmodels.Record"] = []
        next_offset = offset

        while True:
            chunk, next_offset = self.client.scroll(
                collection_name=self.name,
                scroll_filter=q_filter,
                with_payload=with_payload,
                with_vectors=with_vectors,
                limit=batch_limit,
                offset=next_offset,
            )
            points.extend(chunk)
            if not next_offset or (limit and len(points) >= limit):
                break

        if limit and len(points) > limit:
            points = points[:limit]
        return self._build_result(points, include=include)

    def query(self, *, query_embeddings: Sequence[Sequence[float]], n_results: int, where: Optional[Dict[str, Any]], include: Optional[Sequence[str]]):
        _ensure_qdrant_installed()
        q_filter = _convert_where(where)
        include_set = set(include or [])
        want_docs = not include_set or "documents" in include_set
        want_meta = not include_set or "metadatas" in include_set
        want_embeddings = not include_set or "embeddings" in include_set

        all_ids: List[List[str]] = []
        all_docs: List[List[str]] = []
        all_meta: List[List[Dict[str, Any]]] = []
        all_embeddings: List[List[List[float]]] = []
        all_scores: List[List[float]] = []

        for vector in query_embeddings:
            vector_list = _normalize_vector(vector)
            search_res = self.client.search(
                collection_name=self.name,
                limit=n_results,
                query_vector=vector_list,
                query_filter=q_filter,
                with_payload=want_docs or want_meta,
                with_vectors=want_embeddings,
            )
            ids: List[str] = []
            docs: List[str] = []
            metas: List[Dict[str, Any]] = []
            embeds: List[List[float]] = []
            dists: List[float] = []
            for point in search_res:
                payload = dict(point.payload or {})
                external_id = payload.pop(EXTERNAL_ID_KEY, None)
                ids.append(external_id if external_id is not None else str(point.id))
                doc = payload.pop(DOC_PAYLOAD_KEY, "")
                if want_docs:
                    docs.append(doc)
                if want_meta:
                    metas.append(payload)
                if want_embeddings:
                    vector_value: Any = getattr(point, "vector", None)
                    if vector_value is None and getattr(point, "vectors", None):
                        stored = point.vectors
                        if isinstance(stored, dict):
                            vector_value = next(iter(stored.values()))
                    embeds.append(_normalize_vector(vector_value) if vector_value is not None else [])
                score = float(point.score or 0.0)
                dists.append(1.0 - score)  # convert cosine similarity to distance-ish metric
            all_ids.append(ids)
            if want_docs:
                all_docs.append(docs)
            if want_meta:
                all_meta.append(metas)
            if want_embeddings:
                all_embeddings.append(embeds)
            all_scores.append(dists)

        result: Dict[str, Any] = {"ids": all_ids, "distances": all_scores}
        if want_docs:
            result["documents"] = all_docs
        if want_meta:
            result["metadatas"] = all_meta
        if want_embeddings:
            result["embeddings"] = all_embeddings
        return result

    def count(self) -> int:
        _ensure_qdrant_installed()
        info = self.client.count(collection_name=self.name)
        return int(getattr(info, "count", 0))


class VectorClient:
    backend_name = "qdrant"

    def __init__(self, *, host: str, http_port: int, grpc_port: Optional[int], api_key: Optional[str], https: bool, timeout: float):
        _ensure_qdrant_installed()
        self.client = QdrantClient(
            host=host,
            port=http_port,
            grpc_port=grpc_port,
            api_key=api_key,
            https=https,
            timeout=timeout,
        )
        try:
            # Warm connection; will raise if unreachable.
            self.client.get_collections()
        except Exception as exc:  # pragma: no cover - runtime connectivity
            raise RuntimeError(f"Failed to connect to Qdrant at {host}:{http_port}: {exc}") from exc
        self._known: Dict[str, int] = {}

    def ensure_collection(self, name: str, vector_size: Optional[int]) -> VectorCollection:
        size = vector_size or QDRANT_VECTOR_SIZE
        if name not in self._known:
            try:
                info = self.client.get_collection(name)
                from_config = getattr(getattr(info, "config", None), "params", None)
                current_size = getattr(getattr(from_config, "vectors", None), "size", None)
                if current_size and int(current_size) != int(size):
                    size = int(current_size)
                else:
                    size = int(size)
            except Exception:
                self.client.create_collection(
                    name,
                    vectors_config=qmodels.VectorParams(size=int(size), distance=qmodels.Distance.COSINE),
                )
            self._known[name] = int(size)
        return VectorCollection(self.client, name, self._known[name])

    def list_collections(self):
        return self.client.get_collections()

    def raw(self) -> "QdrantClient":
        return self.client

_SKIP_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".ico",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
    ".rar",
    ".7z",
    ".mp3",
    ".mp4",
    ".mov",
    ".bin",
    ".exe",
    ".dll",
}
_SKIP_DIR_NAMES = {".git", "__pycache__", "node_modules", "build", "dist"}
_SKIP_FILE_NAMES = {"package-lock.json", "yarn.lock"}

_tokenizer = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_tokenizer.encode(text or ""))


def _build_vector_client() -> VectorClient:
    return VectorClient(
        host=QDRANT_HOST,
        http_port=QDRANT_HTTP_PORT,
        grpc_port=QDRANT_GRPC_PORT,
        api_key=QDRANT_API_KEY,
        https=QDRANT_HTTPS,
        timeout=QDRANT_TIMEOUT,
    )


def get_clients() -> tuple[voyageai.Client, VectorClient]:
    api_key = os.environ.get("VOYAGE_API_KEY")
    if not api_key:
        raise RuntimeError("VOYAGE_API_KEY is required in the environment.")
    vo = voyageai.Client(api_key=api_key)
    client = _build_vector_client()
    return vo, client


def ensure_collection(client: VectorClient, name: str = UNIFIED) -> VectorCollection:
    return client.ensure_collection(name, vector_size=QDRANT_VECTOR_SIZE or EMBED_DIM)


def get_qdrant_http_client() -> Optional["QdrantClient"]:
    if QdrantClient is None:
        return None
    return QdrantClient(
        host=QDRANT_HOST,
        port=QDRANT_HTTP_PORT,
        grpc_port=QDRANT_GRPC_PORT,
        api_key=QDRANT_API_KEY,
        https=QDRANT_HTTPS,
        timeout=QDRANT_TIMEOUT,
    )


def _looks_binary(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            sample = fh.read(2048)
    except OSError:
        return True
    return b"\x00" in sample


_TEXTUAL_SUFFIXES = {
    "",
    ".md",
    ".rst",
    ".txt",
    ".py",
    ".pyi",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".json",
    ".yml",
    ".yaml",
    ".toml",
    ".ini",
    ".cfg",
    ".csv",
    ".tsv",
    ".sql",
    ".sh",
    ".bash",
    ".zsh",
    ".bat",
    ".ps1",
    ".go",
    ".c",
    ".h",
    ".hpp",
    ".cc",
    ".cpp",
    ".rs",
    ".java",
    ".kt",
    ".rb",
    ".php",
    ".swift",
    ".scala",
    ".pl",
    ".pm",
    ".lua",
    ".hs",
    ".cs",
    ".css",
    ".scss",
    ".less",
}


def should_skip_file(path: Path) -> bool:
    """Heuristically skip files that are unlikely to embed well."""
    name = path.name
    if name in _SKIP_FILE_NAMES:
        return True
    if any(part in _SKIP_DIR_NAMES for part in path.parts):
        return True
    suffix = path.suffix.lower()
    if suffix in _SKIP_SUFFIXES:
        return True
    try:
        size = path.stat().st_size
    except OSError:
        return True
    if size > MAX_FILE_BYTES:
        return True
    if suffix not in _TEXTUAL_SUFFIXES and _looks_binary(path):
        return True
    return False


def detect_code(text: str) -> bool:
    indicators = [
        "```",
        "def ", "class ", "function ", "const ", "let ", "var ",
        "import ", "from ", "require(",
        "if __name__", "pub fn", "fn main",
        "#!/usr/bin",
        "SELECT ", "CREATE TABLE", "INSERT INTO",
    ]
    t = text or ""
    return any(i in t for i in indicators)


def domain_of(url: str) -> str:
    m = re.match(r"https?://([^/]+)", url or "")
    return m.group(1).lower() if m else ""


def split_long_text(text: str, max_tokens: int = 8000) -> List[str]:
    if count_tokens(text) <= max_tokens:
        return [text]
    parts: List[str] = []
    paras = re.split(r"\n\n+", text or "")
    cur: List[str] = []
    cur_t = 0
    for p in paras:
        t = count_tokens(p)
        if cur_t + t + 10 > max_tokens:
            if cur:
                parts.append("\n\n".join(cur))
            cur = [p]
            cur_t = t
        else:
            cur.append(p)
            cur_t += t
    if cur:
        parts.append("\n\n".join(cur))
    final: List[str] = []
    for part in parts:
        while count_tokens(part) > max_tokens:
            mid = len(part) // 2
            final.append(part[:mid])
            part = part[mid:]
        final.append(part)
    return final


def contextual_windows(texts: List[str], max_tokens: int = MAX_DOC_TOKENS, always_include_first: bool = True) -> List[Tuple[int, int]]:
    """Return inclusive-exclusive windows for grouping chunks under a token cap.

    When ``always_include_first`` is True the first chunk (usually a summary)
    is present in every window, with subsequent chunks packed while respecting
    ``max_tokens``. Highlights that cannot fit alongside the summary are emitted
    as standalone windows rather than looping forever.
    """
    if not texts:
        return []

    tokens = [count_tokens(t) for t in texts]
    windows: List[Tuple[int, int]] = []

    if always_include_first:
        # Always emit at least the summary.
        windows.append((0, min(len(texts), 1)))

        index = 1
        while index < len(texts):
            total = tokens[0]
            end = index
            while end < len(texts) and total + tokens[end] <= max_tokens:
                total += tokens[end]
                end += 1
            if end == index:
                # Highlight (or chunk) does not fit with the summary; embed it on its own.
                windows.append((index, index + 1))
                index += 1
            else:
                windows.append((0, end))
                index = end
    else:
        start = 0
        while start < len(texts):
            total = 0
            end = start
            while end < len(texts) and total + tokens[end] <= max_tokens:
                total += tokens[end]
                end += 1
            if end == start:
                end += 1
            windows.append((start, end))
            start = end

    return windows


# Chunking helpers
def simple_chunk_document(content: str, max_chunk_size: int = 8000) -> List[str]:
    """Split long content into roughly paragraph/sentence chunks under the limit."""
    if len(content) < max_chunk_size:
        return [content]
    chunks: List[str] = []
    paragraphs = content.split("\n\n")
    current_chunk = ""
    for para in paragraphs:
        if len(para) > max_chunk_size:
            if current_chunk:
                chunks.append(current_chunk)
                current_chunk = ""
            if ". " in para:
                sentences = para.split(". ")
                for sent in sentences:
                    if len(current_chunk) + len(sent) + 2 > max_chunk_size:
                        if current_chunk:
                            chunks.append(current_chunk)
                        current_chunk = sent + ". "
                    else:
                        current_chunk += sent + ". "
            else:
                if current_chunk:
                    chunks.append(current_chunk)
                    current_chunk = ""
                current_chunk = para
        else:
            if current_chunk:
                current_chunk += "\n\n" + para
            else:
                current_chunk = para
    if current_chunk:
        chunks.append(current_chunk)
    return chunks


def group_chunks_for_voyage(chunks: List[str], max_tokens: int = MAX_DOC_TOKENS) -> List[List[str]]:
    """Group chunks into windows under token limit for contextualized embedding."""
    groups: List[List[str]] = []
    current_group: List[str] = []
    current_tokens = 0
    for chunk in chunks:
        chunk_tokens = count_tokens(chunk)
        if chunk_tokens > max_tokens:
            if current_group:
                groups.append(current_group)
                current_group = []
                current_tokens = 0
            for sub in simple_chunk_document(chunk, max_chunk_size=4000):
                if count_tokens(sub) > max_tokens:
                    groups.append([sub[:8000]])
                else:
                    groups.append([sub])
        elif current_tokens + chunk_tokens > max_tokens:
            if current_group:
                groups.append(current_group)
            current_group = [chunk]
            current_tokens = chunk_tokens
        else:
            current_group.append(chunk)
            current_tokens += chunk_tokens
    if current_group:
        groups.append(current_group)
    return groups
