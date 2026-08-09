import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any

import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent


DEFAULT_MCP_URL = "http://127.0.0.1:8000/mcp"
DEFAULT_TEST_HOST = "machine-03"


@dataclass(frozen=True)
class ToolResponse:
    is_error: bool
    text: str
    payload: dict[str, Any] | None


class MCPTestClient:
    """Small synchronous wrapper around the official asynchronous MCP client."""

    def __init__(self, url: str):
        self.url = url

    def _run(self, operation):
        async def runner():
            # MCP Python SDK v2 Streamable HTTP yields exactly two streams.
            async with streamable_http_client(
                self.url,
                terminate_on_close=False,
            ) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    return await operation(session)

        return asyncio.run(runner())

    def list_tools(self) -> set[str]:
        async def operation(session: ClientSession) -> set[str]:
            result = await session.list_tools()
            return {tool.name for tool in result.tools}

        return self._run(operation)

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> ToolResponse:
        async def operation(session: ClientSession) -> ToolResponse:
            result = await session.call_tool(
                name,
                arguments=arguments or {},
            )

            text_parts = [
                item.text
                for item in result.content
                if isinstance(item, TextContent)
            ]
            text = "\n".join(text_parts)

            payload = None

            # MCPServer returns dictionaries as JSON text for backward-compatible
            # unstructured content, so try this representation first.
            if text:
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, dict):
                        payload = parsed
                except json.JSONDecodeError:
                    pass

            # MCP Python SDK v2 exposes Python model fields in snake_case.
            if payload is None:
                structured = getattr(result, "structured_content", None)
                if isinstance(structured, dict):
                    payload = structured

            return ToolResponse(
                is_error=bool(getattr(result, "is_error", False)),
                text=text,
                payload=payload,
            )

        return self._run(operation)


@pytest.fixture(scope="session")
def mcp_url() -> str:
    return os.getenv("MCP_TEST_URL", DEFAULT_MCP_URL)


@pytest.fixture(scope="session")
def test_host() -> str:
    return os.getenv("MCP_TEST_HOST", DEFAULT_TEST_HOST)


@pytest.fixture(scope="session")
def mcp_client(mcp_url: str) -> MCPTestClient:
    return MCPTestClient(mcp_url)


def assert_success(response: ToolResponse) -> dict[str, Any]:
    assert response.is_error is False, response.text
    assert response.payload is not None, response.text
    assert response.payload.get("status") == "ok", response.payload
    return response.payload
