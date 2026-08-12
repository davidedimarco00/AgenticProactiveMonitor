import tempfile
from pathlib import Path
import unittest

from model import (
    build_document_metadata,
    chunk_text,
    parse_markdown,
    prepare_collection_documents,
)


class ManifestModelTests(unittest.TestCase):
    def test_parse_markdown_front_matter(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "doc.md"
            path.write_text(
                "---\nkb_id: test.doc\nroles: [system_engineer]\n---\n# Title\nBody text",
                encoding="utf-8",
            )
            metadata, body = parse_markdown(path)

        self.assertEqual(metadata["kb_id"], "test.doc")
        self.assertEqual(metadata["roles"], ["system_engineer"])
        self.assertEqual(body, "# Title\nBody text")

    def test_parse_markdown_supports_windows_line_endings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "windows.md"
            path.write_bytes(
                b"---\r\nkb_id: test.windows\r\n---\r\n# Title\r\nBody text"
            )
            metadata, body = parse_markdown(path)

        self.assertEqual(metadata["kb_id"], "test.windows")
        self.assertEqual(body, "# Title\nBody text")

    def test_chunk_text_applies_overlap(self):
        text = "one two three four five six seven eight"
        chunks = chunk_text(text, chunk_size_words=4, overlap_words=1)
        self.assertEqual(
            chunks,
            ["one two three four", "four five six seven", "seven eight"],
        )

    def test_metadata_forces_collection_and_drops_routing_labels(self):
        metadata = build_document_metadata(
            collection="monitored-system",
            manifest={"version": 7},
            document_entry={"document_type": "service-reference"},
            front_matter={
                "kb_id": "monitored.service.demo",
                "collection": "wrong-collection",
                "roles": ["system_engineer"],
                "incident_types": ["cpu"],
            },
            relative_path="services/demo.md",
        )

        self.assertEqual(metadata["collection"], "monitored-system")
        self.assertEqual(metadata["kb_id"], "monitored.service.demo")
        self.assertNotIn("roles", metadata)
        self.assertNotIn("incident_types", metadata)
        self.assertEqual(metadata["document_type"], "service-reference")

    def test_prepare_collection_documents_uses_manifest_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "one.md").write_text(
                "---\nkb_id: example.one\n---\nalpha beta gamma delta",
                encoding="utf-8",
            )
            (root / "ignored.md").write_text("not in manifest", encoding="utf-8")
            manifest = {
                "collection": "example",
                "documents": [{"path": "one.md", "document_type": "technical-reference"}],
            }

            documents = prepare_collection_documents(
                "example",
                root,
                manifest,
                chunk_size_words=3,
                overlap_words=1,
            )

        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0]["metadata"]["kb_id"], "example.one")
        self.assertEqual(documents[0]["filename"], "one.md")
        self.assertEqual(len(documents[0]["chunks"]), 2)

    def test_manifest_path_cannot_escape_source_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = {"documents": [{"path": "../outside.md"}]}
            with self.assertRaises(ValueError):
                prepare_collection_documents(
                    "example",
                    root,
                    manifest,
                    chunk_size_words=10,
                    overlap_words=0,
                )


if __name__ == "__main__":
    unittest.main()
