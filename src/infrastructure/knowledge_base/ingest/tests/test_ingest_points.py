import unittest

from ingest import embed_documents


class FakeEmbedder:
    def __init__(self):
        self.calls = []

    def embed(self, texts):
        self.calls.append(list(texts))
        return [[0.0] * 384 for _ in texts]


class IngestPointTests(unittest.TestCase):
    def test_embed_documents_builds_qdrant_payload(self):
        documents = [
            {
                "document_id": "doc-1",
                "filename": "cpu.md",
                "file_type": "md",
                "metadata": {
                    "kb_id": "linux.cpu.load",
                    "collection": "kb-system-engineer-linux",
                    "roles": ["system_engineer"],
                    "topics": ["cpu", "load"],
                    "managed_by": "manifest-ingest",
                },
                "chunks": ["first chunk", "second chunk"],
            }
        ]
        embedder = FakeEmbedder()

        points = embed_documents(
            "kb-system-engineer-linux",
            documents,
            embedder,
        )

        self.assertEqual(len(points), 2)
        self.assertEqual(points[0]["payload"]["kb_id"], "linux.cpu.load")
        self.assertEqual(
            points[0]["payload"]["collection"],
            "kb-system-engineer-linux",
        )
        self.assertEqual(points[0]["payload"]["chunk_index"], 0)
        self.assertEqual(points[0]["payload"]["total_chunks"], 2)
        self.assertEqual(points[1]["payload"]["text"], "second chunk")
        self.assertEqual(len(points[0]["vector"]), 384)

    def test_point_ids_are_deterministic(self):
        document = {
            "document_id": "doc-1",
            "filename": "cpu.md",
            "file_type": "md",
            "metadata": {
                "kb_id": "linux.cpu.load",
                "collection": "kb-system-engineer-linux",
                "managed_by": "manifest-ingest",
            },
            "chunks": ["same chunk"],
        }

        first = embed_documents(
            "kb-system-engineer-linux",
            [document],
            FakeEmbedder(),
        )
        second = embed_documents(
            "kb-system-engineer-linux",
            [document],
            FakeEmbedder(),
        )

        self.assertEqual(first[0]["id"], second[0]["id"])


if __name__ == "__main__":
    unittest.main()
