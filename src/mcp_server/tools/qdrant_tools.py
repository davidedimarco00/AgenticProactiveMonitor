import os

import httpx
from mcp.server import MCPServer


QDRANT_URL = os.getenv(
    "QDRANT_URL",
    "http://qdrant:6333",
).rstrip("/")

QDRANT_COLLECTION = os.getenv(
    "QDRANT_COLLECTION",
    "thesis-knowledge-base",
)

OLLAMA_URL = os.getenv(
    "OLLAMA_URL",
    "http://ollama:11434",
).rstrip("/")

OLLAMA_EMBEDDING_MODEL = os.getenv(
    "OLLAMA_EMBEDDING_MODEL",
    "ibm/granite-embedding:30m",
)


async def _embed_query(
    query: str,
) -> list[float]:
    """
    Generate the embedding for a search query using Ollama.
    """

    async with httpx.AsyncClient(
        timeout=60.0,
    ) as client:
        response = await client.post(
            f"{OLLAMA_URL}/api/embed",
            json={
                "model": OLLAMA_EMBEDDING_MODEL,
                "input": query,
            },
        )

        response.raise_for_status()

        data = response.json()

    embeddings = data.get(
        "embeddings",
        [],
    )

    if not embeddings:
        raise RuntimeError("Ollama did not return an embedding.")

    embedding = embeddings[0]

    if (
        not isinstance(
            embedding,
            list,
        )
        or not embedding
    ):
        raise RuntimeError("Ollama returned an invalid embedding.")

    return embedding


async def _query_qdrant(
    vector: list[float],
    limit: int,
) -> list[dict]:
    """
    Search the configured Qdrant collection.
    """

    async with httpx.AsyncClient(
        timeout=30.0,
    ) as client:
        response = await client.post(
            (f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}/points/query"),
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


def register_qdrant_tools(
    mcp: MCPServer,
) -> None:

    @mcp.tool()
    async def search_knowledge(
        query: str,
        limit: int = 5,
    ) -> dict:
        """
        Search the thesis knowledge base using semantic vector search.

        The query is embedded with the same Ollama embedding model
        used during document ingestion and searched against Qdrant.

        This tool is read-only.
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

            points = await _query_qdrant(
                query_vector,
                limit,
            )

            results = []

            for point in points:
                payload = point.get("payload") or {}

                results.append(
                    {
                        "score": round(
                            float(
                                point.get(
                                    "score",
                                    0.0,
                                )
                            ),
                            4,
                        ),
                        "document_id": (payload.get("document_id")),
                        "filename": (payload.get("filename")),
                        "file_type": (payload.get("file_type")),
                        "chunk_index": (payload.get("chunk_index")),
                        "total_chunks": (payload.get("total_chunks")),
                        "text": (payload.get("text")),
                        "uploaded_at": (payload.get("uploaded_at")),
                    }
                )

            return {
                "status": "ok",
                "query": query,
                "collection": (QDRANT_COLLECTION),
                "embedding_model": (OLLAMA_EMBEDDING_MODEL),
                "embedding_dimensions": len(query_vector),
                "returned_results": len(results),
                "results": results,
            }

        except httpx.HTTPStatusError as exc:
            return {
                "status": "error",
                "query": query,
                "error": (f"HTTP error while accessing Ollama or Qdrant: {exc}"),
            }

        except httpx.RequestError as exc:
            return {
                "status": "error",
                "query": query,
                "error": (f"Unable to reach Ollama or Qdrant: {exc}"),
            }

        except RuntimeError as exc:
            return {
                "status": "error",
                "query": query,
                "error": str(exc),
            }
