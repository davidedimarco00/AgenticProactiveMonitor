import requests


class EmbeddingService:
    def __init__(self, base_url: str, model: str, timeout: int = 180):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.session = requests.Session()

    def embed_texts(self, texts: list[str], batch_size: int = 16) -> list[list[float]]:
        if not texts:
            return []

        embeddings: list[list[float]] = []

        for start in range(0, len(texts), batch_size):
            batch = texts[start:start + batch_size]
            response = self.session.post(
                f"{self.base_url}/api/embed",
                json={
                    "model": self.model,
                    "input": batch,
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
            batch_embeddings = data.get("embeddings", [])

            if len(batch_embeddings) != len(batch):
                raise RuntimeError(
                    "Ollama returned a different number of embeddings than requested."
                )

            embeddings.extend(batch_embeddings)

        return embeddings

    def embed_query(self, query: str) -> list[float]:
        embeddings = self.embed_texts([query], batch_size=1)
        if not embeddings:
            raise RuntimeError("Ollama did not return an embedding for the query.")
        return embeddings[0]

    def health(self) -> bool:
        try:
            response = self.session.get(f"{self.base_url}/api/tags", timeout=10)
            return response.ok
        except requests.RequestException:
            return False
