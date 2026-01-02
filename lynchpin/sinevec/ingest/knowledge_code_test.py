import sys
import tempfile
import types
import unittest
from pathlib import Path


if "voyageai" not in sys.modules:
    voyageai_stub = types.ModuleType("voyageai")

    class _DummyClient:
        def __init__(self, *args, **kwargs):
            pass

    voyageai_stub.Client = _DummyClient
    sys.modules["voyageai"] = voyageai_stub

if "tiktoken" not in sys.modules:
    tiktoken_stub = types.ModuleType("tiktoken")

    class _DummyEncoder:
        def encode(self, text: str):
            return list(text.encode("utf-8"))

    def _get_encoding(_name: str):
        return _DummyEncoder()

    tiktoken_stub.get_encoding = _get_encoding
    sys.modules["tiktoken"] = tiktoken_stub

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sinevec.ingest.knowledge_code import EmbeddingState, embed_file, scan_files
from sinevec.embed_utils import CONTEXT_MODEL


class StubVoyage:
    def contextualized_embed(self, inputs, model, input_type, output_dimension):
        assert model == CONTEXT_MODEL
        chunk_group = inputs[0]
        embeddings = [[float(i + 1)] for i in range(len(chunk_group))]
        return _Response(embeddings, tokens=len(chunk_group) * 10)


class StubCollection:
    def __init__(self):
        self.add_calls = []
        self.deleted = []

    def delete(self, ids):
        self.deleted.extend(ids)

    def add(self, embeddings, documents, metadatas, ids):
        self.add_calls.append(
            {
                "embeddings": embeddings,
                "documents": documents,
                "metadatas": metadatas,
                "ids": ids,
            }
        )


class _Result:
    def __init__(self, embeddings):
        self.embeddings = embeddings


class _Response:
    def __init__(self, embeddings, tokens):
        self.results = [_Result(embeddings)]
        self.total_tokens = tokens


class KnowledgeCodePipelineTest(unittest.TestCase):
    def test_embed_file_records_enriched_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            file_path = root / "docs" / "topic.md"
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text("Hello knowledge base")

            state_path = root / "state.json"
            state = EmbeddingState(state_file=state_path)

            vo = StubVoyage()
            collection = StubCollection()
            tokens = embed_file(vo, collection, file_path, root, "knowledgebase", state, force=True)

            self.assertGreater(tokens, 0)
            self.assertIn(str(file_path), state.processed_files)
            self.assertEqual(state.token_usage["total"], tokens)
            self.assertEqual(len(collection.add_calls), 1)
            payload = collection.add_calls[0]
            self.assertEqual(len(payload["ids"]), 1)
            meta = payload["metadatas"][0]
            self.assertEqual(meta["category"], "knowledgebase")
            self.assertEqual(meta["subcategory"], "docs")
            self.assertEqual(meta["relative_path"], "docs/topic.md")
            self.assertEqual(meta["embedding_model"], CONTEXT_MODEL)
            self.assertIn("created", meta)
            self.assertIn("updated", meta)

    def test_scan_files_respects_force_flag(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            file_path = root / "code" / "sample.py"
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text("print('hi')\n")

            state_path = root / "state.json"
            state = EmbeddingState(state_file=state_path)
            state.mark_processed(str(file_path))

            without_force = scan_files(root, state, force=False)
            self.assertEqual(without_force, [])

            with_force = scan_files(root, state, force=True)
            self.assertEqual(with_force, [file_path])


if __name__ == "__main__":
    unittest.main()
