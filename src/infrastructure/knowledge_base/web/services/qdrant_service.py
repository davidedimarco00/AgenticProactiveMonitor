from collections import defaultdict
from datetime import datetime, timezone
import uuid

import requests


class QdrantService:
    def __init__(self, base_url: str, collection: str, timeout: int = 60):
        self.base_url = base_url.rstrip("/")
        self.collection = collection
        self.timeout = timeout
        self.session = requests.Session()

    def upsert_document(
        self,
        document_id: str,
        filename: str,
        file_type: str,
        chunks: list[str],
        embeddings: list[list[float]],
        batch_size: int = 64,
    ) -> None:
        if len(chunks) != len(embeddings):
            raise ValueError("Chunks and embeddings must have the same length.")

        uploaded_at = datetime.now(timezone.utc).isoformat()
        namespace = uuid.UUID(document_id)
        total_chunks = len(chunks)

        points = []
        for index, (chunk, vector) in enumerate(zip(chunks, embeddings)):
            point_id = str(uuid.uuid5(namespace, f"chunk-{index}"))
            points.append(
                {
                    "id": point_id,
                    "vector": vector,
                    "payload": {
                        "document_id": document_id,
                        "filename": filename,
                        "file_type": file_type,
                        "chunk_index": index,
                        "total_chunks": total_chunks,
                        "text": chunk,
                        "uploaded_at": uploaded_at,
                    },
                }
            )

        for start in range(0, len(points), batch_size):
            batch = points[start:start + batch_size]
            response = self.session.put(
                f"{self.base_url}/collections/{self.collection}/points",
                params={"wait": "true"},
                json={"points": batch},
                timeout=self.timeout,
            )
            response.raise_for_status()

    def list_documents(self) -> list[dict]:
        documents: dict[str, dict] = defaultdict(dict)
        offset = None

        while True:
            body = {
                "limit": 256,
                "with_payload": [
                    "document_id",
                    "filename",
                    "file_type",
                    "chunk_index",
                    "total_chunks",
                    "uploaded_at",
                ],
                "with_vector": False,
            }
            if offset is not None:
                body["offset"] = offset

            response = self.session.post(
                f"{self.base_url}/collections/{self.collection}/points/scroll",
                json=body,
                timeout=self.timeout,
            )
            response.raise_for_status()
            result = response.json().get("result", {})

            for point in result.get("points", []):
                payload = point.get("payload") or {}
                document_id = payload.get("document_id")
                if not document_id:
                    continue

                item = documents.setdefault(
                    document_id,
                    {
                        "document_id": document_id,
                        "filename": payload.get("filename", "Unknown"),
                        "file_type": payload.get("file_type", ""),
                        "chunks": 0,
                        "uploaded_at": payload.get("uploaded_at", ""),
                    },
                )
                item["chunks"] += 1

            offset = result.get("next_page_offset")
            if offset is None:
                break

        return sorted(
            documents.values(),
            key=lambda item: item.get("uploaded_at", ""),
            reverse=True,
        )

    def delete_document(self, document_id: str) -> None:
        body = {
            "filter": {
                "must": [
                    {
                        "key": "document_id",
                        "match": {"value": document_id},
                    }
                ]
            }
        }
        response = self.session.post(
            f"{self.base_url}/collections/{self.collection}/points/delete",
            params={"wait": "true"},
            json=body,
            timeout=self.timeout,
        )
        response.raise_for_status()

    def search(self, vector: list[float], limit: int = 5) -> list[dict]:
        body = {
            "query": vector,
            "limit": limit,
            "with_payload": True,
            "with_vector": False,
        }
        response = self.session.post(
            f"{self.base_url}/collections/{self.collection}/points/query",
            json=body,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json().get("result", {}).get("points", [])

    def collection_info(self) -> dict:
        response = self.session.get(
            f"{self.base_url}/collections/{self.collection}",
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json().get("result", {})
