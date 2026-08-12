import unittest

from tools.qdrant_tools import _select_collections


class QdrantCollectionRoutingTests(unittest.TestCase):
    def test_auto_without_role_uses_shared_collection(self):
        self.assertEqual(_select_collections(None, "auto"), ["monitored-system"])

    def test_auto_with_system_engineer_uses_shared_and_linux(self):
        self.assertEqual(
            _select_collections("system_engineer", "auto"),
            ["monitored-system", "kb-system-engineer-linux"],
        )

    def test_role_scope_uses_only_role_collection(self):
        self.assertEqual(
            _select_collections("network_engineer", "role"),
            ["kb-network-engineer"],
        )

    def test_shared_scope_does_not_require_role(self):
        self.assertEqual(_select_collections(None, "shared"), ["monitored-system"])

    def test_invalid_role_is_rejected(self):
        with self.assertRaises(ValueError):
            _select_collections("unknown_role", "auto")

    def test_role_scope_requires_role(self):
        with self.assertRaises(ValueError):
            _select_collections(None, "role")


if __name__ == "__main__":
    unittest.main()
