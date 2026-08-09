import os

import pytest

from conftest import assert_success


pytestmark = pytest.mark.integration


DEFAULT_KB_QUERY = "high CPU troubleshooting Linux"
EXPECTED_EMBEDDING_DIMENSIONS = 384


def test_search_knowledge_end_to_end(mcp_client):
    query = os.getenv("MCP_TEST_KB_QUERY", DEFAULT_KB_QUERY)

    response = mcp_client.call_tool(
        "search_knowledge",
        {
            "query": query,
            "limit": 5,
        },
    )
    payload = assert_success(response)

    assert payload["query"] == query
    assert payload["collection"] == "thesis-knowledge-base"
    assert payload["embedding_model"] == "ibm/granite-embedding:30m"
    assert payload["embedding_dimensions"] == EXPECTED_EMBEDDING_DIMENSIONS
    assert payload["returned_results"] > 0
    assert len(payload["results"]) == payload["returned_results"]

    first_result = payload["results"][0]
    assert isinstance(first_result["score"], (int, float))
    assert first_result["document_id"]
    assert first_result["filename"]
    assert first_result["chunk_index"] is not None
    assert first_result["total_chunks"] >= 1
    assert first_result["text"]
    assert "\x00" not in first_result["text"]
