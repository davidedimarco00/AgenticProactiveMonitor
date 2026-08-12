from __future__ import annotations

import json
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class OllamaError(RuntimeError):
    pass


class OllamaModelNotFoundError(OllamaError):
    pass


class OllamaStructuredOutputError(OllamaError):
    pass


class OllamaClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        temperature: float = 0.1,
        timeout_seconds: float = 120.0,
        keep_alive: str = "5m",
        max_retries: int = 1,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.keep_alive = keep_alive
        self.max_retries = max_retries
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def ensure_model_available(self) -> None:
        response = await self._client.get("/api/tags")
        response.raise_for_status()
        available = {
            str(value)
            for item in response.json().get("models", [])
            for value in (item.get("name"), item.get("model"))
            if value
        }
        if self.model not in available:
            raise OllamaModelNotFoundError(
                f"Ollama model '{self.model}' is not installed. Available models: {sorted(available)}"
            )

    async def structured(
        self,
        *,
        response_model: type[T],
        system_prompt: str,
        payload: dict,
    ) -> T:
        schema = response_model.model_json_schema()
        user_prompt = (
            "Analyse the following incident data. Use only the supplied evidence.\n\n"
            f"INCIDENT DATA:\n{json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
            f"REQUIRED JSON SCHEMA:\n{json.dumps(schema, ensure_ascii=False)}"
        )
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            response = await self._client.post(
                "/api/chat",
                json={
                    "model": self.model,
                    "messages": messages,
                    "stream": False,
                    "format": schema,
                    "keep_alive": self.keep_alive,
                    "options": {"temperature": self.temperature},
                },
            )
            response.raise_for_status()
            content = response.json().get("message", {}).get("content")
            if not isinstance(content, str) or not content.strip():
                last_error = OllamaStructuredOutputError("Ollama returned an empty structured response")
            else:
                try:
                    return response_model.model_validate_json(content)
                except ValidationError as exc:
                    last_error = exc
                    messages.extend(
                        [
                            {"role": "assistant", "content": content},
                            {
                                "role": "user",
                                "content": (
                                    "The previous response did not match the required schema. "
                                    "Return a corrected JSON object only."
                                ),
                            },
                        ]
                    )

            if attempt >= self.max_retries:
                break

        raise OllamaStructuredOutputError(
            f"Unable to validate Ollama response as {response_model.__name__}: {last_error}"
        )
