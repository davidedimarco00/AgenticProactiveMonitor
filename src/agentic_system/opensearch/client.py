from __future__ import annotations

from typing import Any

import httpx


class OpenSearchClient:
    def __init__(self, base_url: str, username: str | None = None, password: str | None = None, verify_ssl: bool = False) -> None:
        auth = (username, password) if username and password else None
        self._client = httpx.AsyncClient(base_url=base_url.rstrip('/'), auth=auth, verify=verify_ssl, timeout=30.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def request(self, method: str, path: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await self._client.request(method, path, json=json)
        response.raise_for_status()
        if not response.content:
            return {}
        return response.json()

    async def search(self, index: str, query: dict[str, Any]) -> dict[str, Any]:
        return await self.request('POST', f'/{index}/_search', json=query)

    async def field_caps(self, index: str, fields: str) -> dict[str, Any]:
        return await self.request('GET', f'/{index}/_field_caps?fields={fields}')
