import os
import unittest

from tools.qdrant_tools import (
    QDRANT_COLLECTION,
    _embed_query,
    _query_qdrant,
)


@unittest.skipUnless(
    os.getenv("RUN_KB_INTEGRATION") == "1",
    "set RUN_KB_INTEGRATION=1 to run Qdrant/Ollama integration tests",
)
class QdrantKnowledgeRetrievalIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_monitored_system_retrieval(self):
        vector = await _embed_query(
            "processing-service data-service dependency request flow"
        )
        points = await _query_qdrant(vector, 3)

        self.assertTrue(points)
        payload = points[0].get("payload") or {}
        self.assertEqual(QDRANT_COLLECTION, "monitored-system")
        self.assertEqual(payload.get("collection"), "monitored-system")
        self.assertEqual(payload.get("managed_by"), "manifest-ingest")
        self.assertNotIn("roles", payload)
        self.assertNotIn("incident_types", payload)


if __name__ == "__main__":
    unittest.main()
