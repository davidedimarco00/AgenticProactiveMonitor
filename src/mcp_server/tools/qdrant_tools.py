import os

import httpx
from mcp.server import MCPServer


QDRANT_URL = os.getenv(
    "QDRANT_URL",
    "http://qdrant:6333",
).rstrip("/")

MONITORED_SYSTEM_COLLECTION = os.getenv(
    "QDRANT_MONITORED_SYSTEM_COLLECTION",
    os.getenv("QDRANT_COLLECTION", "monitored-system"),
)

ROLE_COLLECTIONS = {
    "technical_lead": os.getenv(
        "QDRANT_TECHNICAL_LEAD_COLLECTION",
        "kb-technical-lead",
    ),
    "system_engineer": os.getenv(
        "QDRANT_SYSTEM_ENGINEER_COLLECTION",
        "kb-system-engineer-linux",
    ),
    "network_engineer": os.getenv(
        "QDRANT_NETWORK_ENGINEER_COLLECTION",
        "kb-network-engineer",
    ),
    "application_engineer": os.getenv(
        "QDRANT_APPLICATION_ENGINEER_COLLECTION",
        "kb-application-engineer",
    ),
    "software_developer": os.getenv(
        "QDRANT_SOFTWARE_DEVELOPER_COLLECTION",
        "kb-software-developer",
    ),
}

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
    collection: str,
    vector: list[float],
    limit: int,
) -> list[dict]:
    """Search one configured Qdrant collection."""

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{QDRANT_URL}/collections/{collection}/points/query",
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


def _select_collections(
    role: str | None,
    scope: str,
) -> list[str]:
    normalized_scope = scope.strip().lower()
    if normalized_scope not in {"auto", "shared", "role", "both"}:
        raise ValueError("scope must be one of: auto, shared, role, both")

    normalized_role = role.strip().lower() if role else None
    if normalized_role and normalized_role not in ROLE_COLLECTIONS:
        raise ValueError(
            "role must be one of: " + ", ".join(sorted(ROLE_COLLECTIONS))
        )

    if normalized_scope == "auto":
        normalized_scope = "both" if normalized_role else "shared"

    if normalized_scope == "shared":
        return [MONITORED_SYSTEM_COLLECTION]

    if not normalized_role:
        raise ValueError("role is required when scope is role or both")

    role_collection = ROLE_COLLECTIONS[normalized_role]
    if normalized_scope == "role":
        return [role_collection]

    return [MONITORED_SYSTEM_COLLECTION, role_collection]


def register_qdrant_tools(
    mcp: MCPServer,
) -> None:

    @mcp.tool()
    async def search_knowledge(
        query: str,
        limit: int = 5,
        role: str | None = None,
        scope: str = "auto",
    ) -> dict:
        """
        Search Qdrant knowledge using semantic vector search.

        Knowledge is separated into a shared monitored-system collection and
        one professional collection per specialist role.

        scope="auto" searches only monitored-system when role is omitted, and
        searches monitored-system plus the role-specific collection when a
        valid role is supplied.

        This tool is read-only. Retrieved knowledge provides system/domain
        context; live telemetry and tool observations remain the source of
        truth for incident diagnosis.
        """

        query = query.strip()
        if not query:
            raise ValueError("query must not be empty")

        if len(query) > 2000:
            raise ValueError("query must not exceed 2000 characters")

        if limit < 1 or limit > 10:
            raise ValueError("limit must be between 1 and 10")

        try:
            collections = _select_collections(role, scope)
            query_vector = await _embed_query(query)

            merged_results: list[dict] = []
            for collection in collections:
                points = await _query_qdrant(
                    collection=collection,
                    vector=query_vector,
                    limit=limit,
                )

                for point in points:
                    payload = point.get("payload") or {}
                    merged_results.append(
                        {
                            "score": round(float(point.get("score", 0.0)), 4),
                            "collection": collection,
                            "document_id": payload.get("document_id"),
                            "filename": payload.get("filename"),
                            "file_type": payload.get("file_type"),
                            "chunk_index": payload.get("chunk_index"),
                            "total_chunks": payload.get("total_chunks"),
                            "text": payload.get("text"),
                            "uploaded_at": payload.get("uploaded_at"),
                            "kb_id": payload.get("kb_id"),
                            "document_type": payload.get("document_type"),
                            "roles": payload.get("roles"),
                            "domains": payload.get("domains"),
                            "services": payload.get("services"),
                            "topics": payload.get("topics"),
                            "platform": payload.get("platform"),
                        }
                    )

            merged_results.sort(
                key=lambda item: item.get("score", 0.0),
                reverse=True,
            )
            results = merged_results[:limit]

            return {
                "status": "ok",
                "query": query,
                "role": role,
                "scope": scope,
                "collections": collections,
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
