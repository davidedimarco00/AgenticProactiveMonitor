# Agentic backend tests

Test levels:

- unit: project logic without Docker.
- integration: running SPADE/XMPP, SPADE-LLM and MCP integration.
- e2e: Gherkin acceptance tests with pytest-bdd.

Run from src/agentic_system after installing the test extra defined in pyproject.toml.
Integration and end-to-end tests require the Docker stack to be running.
