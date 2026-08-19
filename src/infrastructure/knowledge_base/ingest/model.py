from __future__ import annotations

from pathlib import Path
import uuid

import yaml

MANAGED_BY = "manifest-ingest"
PAYLOAD_METADATA_FIELDS = {
    "kb_id",
    "domain",
    "document_type",
    "domains",
    "services",
    "topics",
    "platform",
    "source_files",
    "source_urls",
    "version",
}


def load_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return data


def parse_markdown(path: Path) -> tuple[dict, str]:
    raw = path.read_text(encoding="utf-8")
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    if not raw.startswith("---\n"):
        return {}, raw.strip()

    closing = raw.find("\n---\n", 4)
    if closing < 0:
        raise ValueError(f"Unclosed YAML front matter: {path}")

    metadata = yaml.safe_load(raw[4:closing]) or {}
    if not isinstance(metadata, dict):
        raise ValueError(f"Markdown front matter must be a mapping: {path}")

    return metadata, raw[closing + 5 :].strip()


def chunk_text(text: str, chunk_size_words: int, overlap_words: int) -> list[str]:
    if chunk_size_words <= 0:
        raise ValueError("chunk_size_words must be greater than zero")
    if overlap_words < 0 or overlap_words >= chunk_size_words:
        raise ValueError("overlap_words must be >= 0 and smaller than chunk_size_words")

    words = text.split()
    if not words:
        return []

    step = chunk_size_words - overlap_words
    chunks: list[str] = []
    for start in range(0, len(words), step):
        end = min(start + chunk_size_words, len(words))
        chunks.append(" ".join(words[start:end]))
        if end >= len(words):
            break
    return chunks


def safe_document_path(source_root: Path, relative_path: str) -> Path:
    candidate = (source_root / relative_path).resolve()
    try:
        candidate.relative_to(source_root)
    except ValueError as exc:
        raise ValueError(f"Manifest document escapes source directory: {relative_path}") from exc
    return candidate


def derived_kb_id(collection: str, relative_path: str) -> str:
    path_without_suffix = Path(relative_path).with_suffix("")
    normalized = ".".join(path_without_suffix.parts)
    return f"{collection}.{normalized}".replace("_", "-")


def build_document_metadata(
    collection: str,
    manifest: dict,
    document_entry: dict,
    front_matter: dict,
    relative_path: str,
) -> dict:
    merged: dict = {}
    for source in (manifest, document_entry, front_matter):
        for key in PAYLOAD_METADATA_FIELDS:
            if key in source and source[key] is not None:
                merged[key] = source[key]

    merged["collection"] = collection
    merged["source_path"] = relative_path
    merged["managed_by"] = MANAGED_BY
    merged.setdefault("kb_id", derived_kb_id(collection, relative_path))

    # Role labels and incident labels are deliberately excluded. Retrieval is
    # shared across all agents and diagnosis must come from reasoning over live
    # evidence, not from classification tags.
    merged.pop("roles", None)
    merged.pop("incident_types", None)
    return merged


def prepare_collection_documents(
    collection: str,
    source_root: Path,
    manifest: dict,
    chunk_size_words: int,
    overlap_words: int,
) -> list[dict]:
    documents = manifest.get("documents", [])
    if not isinstance(documents, list):
        raise ValueError("manifest documents must be a list")

    prepared: list[dict] = []
    for entry in documents:
        if not isinstance(entry, dict) or not entry.get("path"):
            raise ValueError("every manifest document requires a path")

        relative_path = str(entry["path"])
        path = safe_document_path(source_root, relative_path)
        if not path.is_file():
            raise FileNotFoundError(f"Knowledge document not found: {path}")
        if path.suffix.lower() not in {".md", ".txt"}:
            raise ValueError(f"Manifest ingestion supports Markdown/TXT only: {path}")

        if path.suffix.lower() == ".md":
            front_matter, body = parse_markdown(path)
        else:
            front_matter = {}
            body = path.read_text(encoding="utf-8").strip()

        chunks = chunk_text(body, chunk_size_words, overlap_words)
        if not chunks:
            raise ValueError(f"Knowledge document produced no chunks: {path}")

        metadata = build_document_metadata(
            collection, manifest, entry, front_matter, relative_path
        )
        kb_id = str(metadata["kb_id"])
        document_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"qdrant-kb:{collection}:{kb_id}")
        )
        prepared.append(
            {
                "document_id": document_id,
                "filename": path.name,
                "file_type": path.suffix.lower().lstrip("."),
                "metadata": metadata,
                "chunks": chunks,
            }
        )

    return prepared
