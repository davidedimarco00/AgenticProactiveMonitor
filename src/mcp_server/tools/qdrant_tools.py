import os

import httpx
from mcp.server import MCPServer


QDRANT_URL = os.getenv(
    "QDRANT_URL",
    "http://qdrant:6333",
).rstrip("/")

QDRANT_COLLECTION = os.getenv(
    "QDRANT_MONITORED_SYSTEM_COLLECTION",
    os.getenv("QDRANT_COLLECTION", "monitored-system"),
)

OLLAMA_URL = os.getenv(
    "OLLAMA_URL",
    "http://ollama:11434",
).rstrip("/")

OLLAMA_EMBEDDING_MODEL = os.getenv(
    "OLLAMA_EMBEDDING_MODEL",
    "ibm/granite-embedding:30m",
)


async def _embed_query(query: str) -> list[float]:
    """Generate the embedding for a search query using Ollama."""

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{OLLAMA_URL}/api/embed",
            json={
                "model": OLLAMA_EMBEDDING_MODEL,
                "input": query,
            },
        )
        response.raise_for_status()
        data = response.json()

    embeddings = data.get("embeddings", [])
    if not embeddings:
        raise RuntimeError("Ollama did not return an embedding.")

    embedding = embeddings[0]
    if not isinstance(embedding, list) or not embedding:
        raise RuntimeError("Ollama returned an invalid embedding.")

    return embedding


async def _query_qdrant(
    vector: list[float],
    limit: int,
) -> list[dict]:
    """Search the monitored-system Qdrant collection."""

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}/points/query",
            json={
                "query": vector,
                "limit": limit,
                "with_payload": True,
                "with_vector": False,
            },
        )
        response.raise_for_status()
        data = response.json()

    return data.get("result", {}).get("points", [])


def _format_point(point: dict) -> dict:
    payload = point.get("payload") or {}
    return {
        "score": round(float(point.get("score", 0.0)), 4),
        "collection": QDRANT_COLLECTION,
        "document_id": payload.get("document_id"),
        "filename": payload.get("filename"),
        "file_type": payload.get("file_type"),
        "chunk_index": payload.get("chunk_index"),
        "total_chunks": payload.get("total_chunks"),
        "text": payload.get("text"),
        "uploaded_at": payload.get("uploaded_at"),
        "kb_id": payload.get("kb_id"),
        "document_type": payload.get("document_type"),
        "domains": payload.get("domains"),
        "services": payload.get("services"),
        "source_path": payload.get("source_path"),
    }


def register_qdrant_tools(mcp: MCPServer) -> None:

    @mcp.tool()
    async def search_knowledge(
        query: str,
        limit: int = 5,
    ) -> dict:
        """
        Search documentation of the monitored system using semantic retrieval.

        The collection contains external knowledge specific to the monitored
        Notes Platform. General Linux, networking and software knowledge is
        expected to come from the LLM itself rather than from role-specific RAG.

        This tool is read-only. Retrieved knowledge provides system context;
        live telemetry and tool observations remain the source of truth for an
        active incident and the agent must generate the diagnosis itself.
        """

        query = query.strip()
        if not query:
            raise ValueError("query must not be empty")
        if len(query) > 2000:
            raise ValueError("query must not exceed 2000 characters")
        if limit < 1 or limit > 10:
            raise ValueError("limit must be between 1 and 10")

        try:
            query_vector = await _embed_query(query)
            points = await _query_qdrant(query_vector, limit)
            results = [_format_point(point) for point in points]

            return {
                "status": "ok",
                "query": query,
                "collection": QDRANT_COLLECTION,
                "embedding_model": OLLAMA_EMBEDDING_MODEL,
                "embedding_dimensions": len(query_vector),
                "returned_results": len(results),
                "results": results,
            }

        except httpx.HTTPStatusError as exc:
            return {
                "status": "error",
                "query": query,
                "error": f"HTTP error while accessing Ollama or Qdrant: {exc}",
            }
        except httpx.RequestError as exc:
            return {
                "status": "error",
                "query": query,
                "error": f"Unable to reach Ollama or Qdrant: {exc}",
            }
        except RuntimeError as exc:
            return {
                "status": "error",
                "query": query,
                "error": str(exc),
            }
