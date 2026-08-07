import argparse
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pypdf import PdfReader

QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333").rstrip("/")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "thesis-knowledge-base")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434").rstrip("/")
EMBEDDING_MODEL = os.getenv("OLLAMA_EMBEDDING_MODEL", "ibm/granite-embedding:30m")
CHUNK_SIZE = int(os.getenv("KNOWLEDGE_BASE_CHUNK_SIZE", "1200"))
CHUNK_OVERLAP = int(os.getenv("KNOWLEDGE_BASE_CHUNK_OVERLAP", "200"))
EMBED_BATCH_SIZE = int(os.getenv("KNOWLEDGE_BASE_EMBED_BATCH_SIZE", "16"))
REQUEST_TIMEOUT = int(os.getenv("KNOWLEDGE_BASE_REQUEST_TIMEOUT", "120"))
DEFAULT_TOP_K = int(os.getenv("RAG_TOP_K", "5"))
SUPPORTED_SUFFIXES = {".txt", ".md", ".pdf"}


def request_json(method: str, url: str, payload=None):
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} calling {url}: {body}") from exc
    except URLError as exc:
        raise RuntimeError(f"Unable to reach {url}: {exc}") from exc


def collection_vector_size() -> int:
    response = request_json("GET", f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}")
    vectors = response["result"]["config"]["params"]["vectors"]
    if isinstance(vectors, dict) and "size" in vectors:
        return int(vectors["size"])
    raise RuntimeError("This ingestion utility expects a Qdrant collection with one unnamed vector.")


def embed(texts: list[str]) -> list[list[float]]:
    response = request_json(
        "POST",
        f"{OLLAMA_URL}/api/embed",
        {"model": EMBEDDING_MODEL, "input": texts},
    )
    embeddings = response.get("embeddings") or []
    if len(embeddings) != len(texts):
        raise RuntimeError(
            f"Ollama returned {len(embeddings)} embeddings for {len(texts)} inputs."
        )
    return embeddings


def read_document(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md"}:
        return path.read_text(encoding="utf-8", errors="replace")
    if suffix == ".pdf":
        reader = PdfReader(str(path))
        pages = []
        for page_number, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            if text.strip():
                pages.append(f"\n[Page {page_number}]\n{text}")
        return "\n".join(pages)
    raise ValueError(f"Unsupported document type: {path}")


def chunk_text(text: str) -> list[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []
    if CHUNK_OVERLAP >= CHUNK_SIZE:
        raise ValueError("KNOWLEDGE_BASE_CHUNK_OVERLAP must be smaller than KNOWLEDGE_BASE_CHUNK_SIZE.")

    chunks = []
    start = 0
    while start < len(text):
        hard_end = min(start + CHUNK_SIZE, len(text))
        end = hard_end

        if hard_end < len(text):
            search_start = start + max(CHUNK_SIZE // 2, 1)
            paragraph_break = text.rfind("\n\n", search_start, hard_end)
            line_break = text.rfind("\n", search_start, hard_end)
            sentence_break = text.rfind(". ", search_start, hard_end)
            candidate = max(paragraph_break, line_break, sentence_break)
            if candidate > start:
                end = candidate + (2 if candidate == sentence_break else 1)

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= len(text):
            break
        start = max(end - CHUNK_OVERLAP, start + 1)

    return chunks


def delete_existing_source(source: str) -> None:
    request_json(
        "POST",
        f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}/points/delete?wait=true",
        {
            "filter": {
                "must": [
                    {"key": "source", "match": {"value": source}}
                ]
            }
        },
    )


def upsert_document(source: str, path: Path, chunks: list[str]) -> int:
    document_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    vector_size = collection_vector_size()
    total = 0

    for batch_start in range(0, len(chunks), EMBED_BATCH_SIZE):
        batch = chunks[batch_start : batch_start + EMBED_BATCH_SIZE]
        vectors = embed(batch)

        points = []
        for offset, (text, vector) in enumerate(zip(batch, vectors)):
            if len(vector) != vector_size:
                raise RuntimeError(
                    f"Embedding dimension mismatch: model {EMBEDDING_MODEL} returned "
                    f"{len(vector)} values, but Qdrant collection {QDRANT_COLLECTION} expects {vector_size}."
                )

            chunk_index = batch_start + offset
            point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{source}#{chunk_index}"))
            points.append(
                {
                    "id": point_id,
                    "vector": vector,
                    "payload": {
                        "source": source,
                        "file_name": path.name,
                        "document_type": path.suffix.lower().lstrip("."),
                        "chunk_index": chunk_index,
                        "document_sha256": document_sha256,
                        "text": text,
                    },
                }
            )

        request_json(
            "PUT",
            f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}/points?wait=true",
            {"points": points},
        )
        total += len(points)

    return total


def ingest(directory: Path) -> None:
    directory = directory.resolve()
    if not directory.exists():
        raise RuntimeError(f"Knowledge-base directory does not exist: {directory}")

    documents = sorted(
        path for path in directory.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    )

    if not documents:
        print(f"No supported documents found in {directory}.")
        return

    print(f"Embedding model: {EMBEDDING_MODEL}")
    print(f"Qdrant collection: {QDRANT_COLLECTION}")
    print(f"Chunk size/overlap: {CHUNK_SIZE}/{CHUNK_OVERLAP} characters")

    total_chunks = 0
    for path in documents:
        source = path.relative_to(directory).as_posix()
        text = read_document(path)
        chunks = chunk_text(text)

        delete_existing_source(source)
        if not chunks:
            print(f"SKIP {source}: no extractable text")
            continue

        inserted = upsert_document(source, path, chunks)
        total_chunks += inserted
        print(f"INGESTED {source}: {inserted} chunks")

    print(f"Knowledge-base ingestion complete: {len(documents)} documents, {total_chunks} chunks.")


def search(query: str, top_k: int) -> None:
    vector = embed([query])[0]
    expected = collection_vector_size()
    if len(vector) != expected:
        raise RuntimeError(
            f"Embedding dimension mismatch: got {len(vector)}, expected {expected}."
        )

    response = request_json(
        "POST",
        f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}/points/query",
        {
            "query": vector,
            "limit": top_k,
            "with_payload": True,
            "with_vector": False,
        },
    )

    result = response.get("result", {})
    points = result.get("points", []) if isinstance(result, dict) else result
    if not points:
        print("No matching knowledge-base chunks found.")
        return

    for rank, point in enumerate(points, start=1):
        payload = point.get("payload") or {}
        print(f"[{rank}] score={point.get('score', 0):.4f} source={payload.get('source', 'unknown')} chunk={payload.get('chunk_index', '?')}")
        print(payload.get("text", "").strip())
        print("-" * 80)


def stats() -> None:
    collection = request_json("GET", f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}")
    count = request_json(
        "POST",
        f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}/points/count",
        {"exact": True},
    )
    print(
        json.dumps(
            {
                "collection": QDRANT_COLLECTION,
                "status": collection.get("result", {}).get("status"),
                "vector_size": collection_vector_size(),
                "points": count.get("result", {}).get("count", 0),
                "embedding_model": EMBEDDING_MODEL,
            },
            indent=2,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Qdrant knowledge-base ingestion and retrieval utility")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_parser = subparsers.add_parser("ingest", help="Ingest .txt, .md and .pdf documents")
    ingest_parser.add_argument("directory", nargs="?", default="/documents")

    search_parser = subparsers.add_parser("search", help="Run a semantic search")
    search_parser.add_argument("query")
    search_parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)

    subparsers.add_parser("stats", help="Show Qdrant collection statistics")

    args = parser.parse_args()
    try:
        if args.command == "ingest":
            ingest(Path(args.directory))
        elif args.command == "search":
            search(args.query, args.top_k)
        elif args.command == "stats":
            stats()
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
