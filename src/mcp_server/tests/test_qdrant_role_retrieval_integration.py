import os
import unittest

from tools.qdrant_tools import (
    MONITORED_SYSTEM_COLLECTION,
    ROLE_COLLECTIONS,
    _embed_query,
    _query_qdrant,
)


@unittest.skipUnless(
    os.getenv("RUN_KB_INTEGRATION") == "1",
    "set RUN_KB_INTEGRATION=1 to run Qdrant/Ollama integration tests",
)
class QdrantRoleRetrievalIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_monitored_system_retrieval(self):
        vector = await _embed_query(
            "processing-service data-service dependency request flow"
        )
        points = await _query_qdrant(
            MONITORED_SYSTEM_COLLECTION,
            vector,
            3,
        )

        self.assertTrue(points)
        payload = points[0].get("payload") or {}
        self.assertEqual(payload.get("collection"), "monitored-system")
        self.assertEqual(payload.get("managed_by"), "manifest-ingest")
        self.assertNotIn("incident_types", payload)

    async def test_system_engineer_linux_retrieval(self):
        collection = ROLE_COLLECTIONS["system_engineer"]
        vector = await _embed_query(
            "Linux CPU load process accounting procfs cgroup memory"
        )
        points = await _query_qdrant(collection, vector, 3)

        self.assertTrue(points)
        payload = points[0].get("payload") or {}
        self.assertEqual(payload.get("collection"), "kb-system-engineer-linux")
        self.assertIn("system_engineer", payload.get("roles") or [])
        self.assertEqual(payload.get("managed_by"), "manifest-ingest")
        self.assertNotIn("incident_types", payload)


if __name__ == "__main__":
    unittest.main()
