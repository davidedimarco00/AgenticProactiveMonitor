from __future__ import annotations

import time

import requests

from model import MANAGED_BY


class OllamaEmbeddingClient:
    def __init__(self, base_url: str, model: str, expected_size: int):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.expected_size = expected_size
        self.session = requests.Session()

    def wait_until_ready(self, wait_seconds: int) -> None:
        deadline = time.monotonic() + wait_seconds
        last_error: Exception | None = None

        while time.monotonic() < deadline:
            try:
                response = self.session.get(f"{self.base_url}/api/tags", timeout=5)
                response.raise_for_status()
                return
            except requests.RequestException as exc:
                last_error = exc
                time.sleep(2)

        raise RuntimeError(
            f"Ollama did not become reachable within {wait_seconds}s: {last_error}"
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        response = self.session.post(
            f"{self.base_url}/api/embed",
            json={"model": self.model, "input": texts},
            timeout=120,
        )
        response.raise_for_status()
        embeddings = response.json().get("embeddings", [])

        if len(embeddings) != len(texts):
            raise RuntimeError(
                "Ollama returned a different number of embeddings than inputs"
            )

        for vector in embeddings:
            if not isinstance(vector, list) or len(vector) != self.expected_size:
                raise RuntimeError(
                    "Embedding dimension does not match QDRANT_VECTOR_SIZE: "
                    f"expected {self.expected_size}"
                )

        return embeddings


class QdrantIngestClient:
    def __init__(self, base_url: str, upsert_batch_size: int = 64):
        self.base_url = base_url.rstrip("/")
        self.upsert_batch_size = upsert_batch_size
        self.session = requests.Session()

    def wait_until_ready(self, wait_seconds: int) -> None:
        deadline = time.monotonic() + wait_seconds
        last_error: Exception | None = None

        while time.monotonic() < deadline:
            try:
                response = self.session.get(f"{self.base_url}/readyz", timeout=5)
                response.raise_for_status()
                return
            except requests.RequestException as exc:
                last_error = exc
                time.sleep(2)

        raise RuntimeError(
            f"Qdrant did not become reachable within {wait_seconds}s: {last_error}"
        )

    def ensure_collection_exists(self, collection: str) -> None:
        response = self.session.get(
            f"{self.base_url}/collections/{collection}", timeout=15
        )
        response.raise_for_status()

    def delete_managed_points(self, collection: str) -> None:
        response = self.session.post(
            f"{self.base_url}/collections/{collection}/points/delete",
            params={"wait": "true"},
            json={
                "filter": {
                    "must": [
                        {
                            "key": "managed_by",
                            "match": {"value": MANAGED_BY},
                        }
                    ]
                }
            },
            timeout=60,
        )
        response.raise_for_status()

    def upsert_points(self, collection: str, points: list[dict]) -> None:
        for start in range(0, len(points), self.upsert_batch_size):
            batch = points[start : start + self.upsert_batch_size]
            response = self.session.put(
                f"{self.base_url}/collections/{collection}/points",
                params={"wait": "true"},
                json={"points": batch},
                timeout=120,
            )
            response.raise_for_status()
