def test_spade_llm_mcp_transport_import_is_available() -> None:
    from mcp.client.streamable_http import streamablehttp_client

    assert callable(streamablehttp_client)
