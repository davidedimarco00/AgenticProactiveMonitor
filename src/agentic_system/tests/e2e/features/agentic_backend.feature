Feature: Agentic backend

  Scenario: Backend team is ready
    Given the agentic backend is ready
    Then five agents are running
    And all agents use SPADE-LLM
    And all agents are connected to XMPP

  Scenario: Communication is traceable
    Given the agentic backend is ready
    Then the communication probe is successful
    And request and response use the same correlation id
