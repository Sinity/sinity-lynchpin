import csv
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


from sinevec.ingest.bookmarks import embed_bookmarks_csv, BookmarkState, CONTEXT_MODEL  # type: ignore


class StubVoyage:
    def contextualized_embed(self, inputs, model, input_type, output_dimension):
        assert model == CONTEXT_MODEL
        rows = len(inputs[0])
        embeddings = [[float(i + 1)] for i in range(rows)]
        return _Response(embeddings, tokens=rows * 11)


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


class BookmarkPipelineTest(unittest.TestCase):
    def _write_csv(self, path: Path):
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=[
                    "id",
                    "title",
                    "url",
                    "tags",
                    "folder",
                    "created",
                    "excerpt",
                    "note",
                    "highlights",
                ],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "id": "bookmark-1",
                    "title": "Test Bookmark",
                    "url": "https://example.com",
                    "tags": "alpha,beta",
                    "folder": "inbox",
                    "created": "2025-01-01",
                    "excerpt": "A sample excerpt",
                    "note": "A personal note",
                    "highlights": "Highlight: First highlight\nHighlight: Second highlight",
                }
            )

    def test_state_skips_processed_entries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            csv_path = tmp / "bookmarks.csv"
            state_path = tmp / "state.json"
            self._write_csv(csv_path)

            vo = StubVoyage()
            collection = StubCollection()

            processed, embedded, tokens = embed_bookmarks_csv(
                csv_path=csv_path,
                voyage_client=vo,
                vector_collection=collection,
                state_path=state_path,
            )
            self.assertEqual(processed, 1)
            self.assertGreater(embedded, 0)
            self.assertGreater(tokens, 0)

            # Repeat without force – should skip because state remembers the ID.
            processed2, embedded2, tokens2 = embed_bookmarks_csv(
                csv_path=csv_path,
                voyage_client=vo,
                vector_collection=collection,
                state_path=state_path,
            )
            self.assertEqual(processed2, 0)
            self.assertEqual(embedded2, 0)
            self.assertEqual(tokens2, 0)

            # Force should re-embed.
            processed3, embedded3, tokens3 = embed_bookmarks_csv(
                csv_path=csv_path,
                voyage_client=vo,
                vector_collection=collection,
                state_path=state_path,
                force=True,
            )
            self.assertEqual(processed3, 1)
            self.assertGreater(embedded3, 0)
            self.assertGreater(tokens3, 0)

            state = BookmarkState(state_path)
            self.assertIn("bookmark-1", state.processed_ids)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
