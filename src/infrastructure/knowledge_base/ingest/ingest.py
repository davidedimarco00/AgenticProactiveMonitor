from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import uuid

from clients import OllamaEmbeddingClient, QdrantIngestClient
from model import load_yaml, prepare_collection_documents


QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333").rstrip("/")
OLLAMA_URL = os.getenv(
    "OLLAMA_URL",
    "http://host.docker.internal:11434",
).rstrip("/")
OLLAMA_EMBEDDING_MODEL = os.getenv(
    "OLLAMA_EMBEDDING_MODEL",
    "ibm/granite-embedding:30m",
)
REGISTRY_PATH = Path(
    os.getenv("KNOWLEDGE_COLLECTIONS_CONFIG", "/config/collections.yaml")
)
REPO_ROOT = Path(os.getenv("KNOWLEDGE_REPO_ROOT", "/repo")).resolve()
CHUNK_SIZE_WORDS = int(os.getenv("KNOWLEDGE_BASE_CHUNK_SIZE_WORDS", "220"))
CHUNK_OVERLAP_WORDS = int(os.getenv("KNOWLEDGE_BASE_CHUNK_OVERLAP_WORDS", "40"))
EMBED_BATCH_SIZE = int(os.getenv("KNOWLEDGE_INGEST_EMBED_BATCH_SIZE", "16"))
UPSERT_BATCH_SIZE = int(os.getenv("KNOWLEDGE_INGEST_UPSERT_BATCH_SIZE", "64"))
EXPECTED_VECTOR_SIZE = int(os.getenv("QDRANT_VECTOR_SIZE", "384"))
WAIT_SECONDS = int(os.getenv("KNOWLEDGE_INGEST_WAIT_SECONDS", "120"))


def embed_documents(
    collection: str,
    documents: list[dict],
    embedder: OllamaEmbeddingClient,
) -> list[dict]:
    points: list[dict] = []
    uploaded_at = datetime.now(timezone.utc).isoformat()

    for document in documents:
        chunks: list[str] = document["chunks"]
        vectors: list[list[float]] = []
        for start in range(0, len(chunks), EMBED_BATCH_SIZE):
            vectors.extend(embedder.embed(chunks[start : start + EMBED_BATCH_SIZE]))

        total_chunks = len(chunks)
        metadata = document["metadata"]
        kb_id = str(metadata["kb_id"])

        for index, (chunk, vector) in enumerate(zip(chunks, vectors)):
            point_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"qdrant-kb:{collection}:{kb_id}:chunk:{index}",
                )
            )
            payload = dict(metadata)
            payload.update(
                {
                    "document_id": document["document_id"],
                    "filename": document["filename"],
                    "file_type": document["file_type"],
                    "chunk_index": index,
                    "total_chunks": total_chunks,
                    "text": chunk,
                    "uploaded_at": uploaded_at,
                }
            )
            points.append({"id": point_id, "vector": vector, "payload": payload})

    return points


def selected_collection_names(registry: dict) -> set[str] | None:
    raw = os.getenv("KNOWLEDGE_INGEST_COLLECTIONS", "").strip()
    if not raw:
        return None

    selected = {item.strip() for item in raw.split(",") if item.strip()}
    known = set((registry.get("collections") or {}).keys())
    unknown = selected - known
    if unknown:
        raise ValueError(
            "Unknown KNOWLEDGE_INGEST_COLLECTIONS values: "
            + ", ".join(sorted(unknown))
        )
    return selected


def resolve_source_root(source_path: str) -> Path:
    source_root = (REPO_ROOT / source_path).resolve()
    try:
        source_root.relative_to(REPO_ROOT)
    except ValueError as exc:
        raise ValueError(f"source_path escapes repository root: {source_path}") from exc
    return source_root


def ingest_registry() -> dict:
    registry = load_yaml(REGISTRY_PATH)
    collections = registry.get("collections")
    if not isinstance(collections, dict) or not collections:
        raise ValueError("collections.yaml must define a non-empty collections mapping")

    selected = selected_collection_names(registry)
    qdrant = QdrantIngestClient(QDRANT_URL, UPSERT_BATCH_SIZE)
    embedder = OllamaEmbeddingClient(
        OLLAMA_URL,
        OLLAMA_EMBEDDING_MODEL,
        EXPECTED_VECTOR_SIZE,
    )

    qdrant.wait_until_ready(WAIT_SECONDS)
    embedder.wait_until_ready(WAIT_SECONDS)

    summary: dict[str, dict] = {}
    for collection, config in collections.items():
        if selected is not None and collection not in selected:
            continue
        if not isinstance(config, dict):
            raise ValueError(f"Collection config must be a mapping: {collection}")

        source_path = str(config.get("source_path") or "")
        if not source_path:
            raise ValueError(f"Collection is missing source_path: {collection}")

        source_root = resolve_source_root(source_path)
        manifest_path = source_root / "manifest.yaml"
        if not manifest_path.is_file():
            raise FileNotFoundError(
                f"Collection manifest not found for {collection}: {manifest_path}"
            )

        qdrant.ensure_collection_exists(collection)
        manifest = load_yaml(manifest_path)
        declared_collection = manifest.get("collection")
        if declared_collection and declared_collection != collection:
            raise ValueError(
                f"Manifest collection mismatch: registry={collection}, "
                f"manifest={declared_collection}"
            )

        documents = prepare_collection_documents(
            collection,
            source_root,
            manifest,
            CHUNK_SIZE_WORDS,
            CHUNK_OVERLAP_WORDS,
        )

        # Prepare embeddings before replacing the last valid managed ingestion.
        points = embed_documents(collection, documents, embedder)
        qdrant.delete_managed_points(collection)
        if points:
            qdrant.upsert_points(collection, points)

        summary[collection] = {
            "documents": len(documents),
            "chunks": len(points),
            "source_path": source_path,
        }
        print(
            f"Ingested {collection}: {len(documents)} documents, {len(points)} chunks"
        )

    return summary


def main() -> None:
    print("Starting manifest-driven knowledge-base ingestion")
    print(f"Registry: {REGISTRY_PATH}")
    print(f"Repository root: {REPO_ROOT}")
    print(f"Embedding model: {OLLAMA_EMBEDDING_MODEL}")

    summary = ingest_registry()
    total_documents = sum(item["documents"] for item in summary.values())
    total_chunks = sum(item["chunks"] for item in summary.values())
    print(
        f"Knowledge ingestion completed: {len(summary)} collections, "
        f"{total_documents} documents, {total_chunks} chunks"
    )


if __name__ == "__main__":
    main()
