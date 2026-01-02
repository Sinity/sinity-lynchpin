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

if "dotenv" not in sys.modules:
    dotenv_stub = types.ModuleType("dotenv")

    def _load_dotenv(*args, **kwargs):
        return None

    dotenv_stub.load_dotenv = _load_dotenv
    sys.modules["dotenv"] = dotenv_stub


from sinevec.ingest.chats import embed_conversation_messages, ChatState, CONTEXT_MODEL  # type: ignore


class StubVoyage:
    def contextualized_embed(self, inputs, model, input_type, output_dimension):
        assert model == CONTEXT_MODEL
        rows = len(inputs[0])
        embeddings = [[float(i + 1)] for i in range(rows)]
        return _Response(embeddings, tokens=rows * 9)


class FailingBatchVoyage(StubVoyage):
    def contextualized_embed(self, inputs, model, input_type, output_dimension):
        if len(inputs[0]) > 1:
            raise RuntimeError("Simulated batch failure")
        return super().contextualized_embed(inputs, model, input_type, output_dimension)


class StubCollection:
    def __init__(self):
        self.deleted = []
        self.add_calls = []

    def delete(self, ids):
        self.deleted.extend(ids)

    def add(self, **kwargs):
        self.add_calls.append(kwargs)


class _Result:
    def __init__(self, embeddings):
        self.embeddings = embeddings


class _Response:
    def __init__(self, embeddings, tokens):
        self.results = [_Result(embeddings)]
        self.total_tokens = tokens


class ChatPipelineTest(unittest.TestCase):
    def test_long_message_is_segmented(self):
        long_text = "A" * 70000  # ensures multiple segments after tokenisation
        conversation = {
            "id": "conv-1",
            "title": "Long conversation",
            "source": "chatgpt",
            "messages": [
                {"role": "user", "content": long_text},
                {"role": "assistant", "content": "Short reply"},
            ],
        }
        collection = StubCollection()
        embedded, tokens, segments = embed_conversation_messages(
            conversation,
            collection,
            voyage_client=StubVoyage(),
        )
        self.assertGreater(segments, 2)
        self.assertGreater(embedded, 2)
        ids = [msg_id for call in collection.add_calls for msg_id in call["ids"]]
        self.assertTrue(any("_part0" in i for i in ids))

    def test_fallback_handles_batch_failure(self):
        conversation = {
            "id": "conv-2",
            "title": "Fallback required",
            "source": "chatgpt",
            "messages": [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there"},
            ],
        }
        collection = StubCollection()
        embedded, tokens, segments = embed_conversation_messages(
            conversation,
            collection,
            voyage_client=FailingBatchVoyage(),
        )
        self.assertEqual(segments, 2)
        self.assertEqual(embedded, 2)
        self.assertGreater(tokens, 0)


class ChatStateTest(unittest.TestCase):
    def test_state_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "chat_state.json"
            state = ChatState(path)
            state.mark_processed("chatgpt", "conversation", "2025-01-01", 42, 10)
            state.mark_failed("chatgpt", "conversation", "error")
            state.save(force=True)

            loaded = ChatState(path)
            self.assertTrue(loaded.should_skip("chatgpt", "conversation", "2025-01-01", force=False))
            self.assertFalse(loaded.should_skip("chatgpt", "conversation", "2025-02-01", force=False))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
