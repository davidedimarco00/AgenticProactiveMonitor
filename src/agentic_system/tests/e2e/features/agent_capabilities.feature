Feature: Agent capabilities

  Scenario: Agents expose reasoning and monitoring capabilities
    Given the agentic backend is ready for capability checks
    Then the reasoning provider is Ollama
    And every agent exposes MCP tools
    And every agent exposes knowledge search
