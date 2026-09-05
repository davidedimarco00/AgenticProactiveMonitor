Feature: Multi-agent incident investigation
  Incidents must be investigated by specialised agents that can collaborate and collect live operational evidence through controlled diagnostic tools.

  @FR-07
  Scenario: A specialist is selected according to the incident
    Given a diagnostic incident requires investigation
    When the Technical Lead assigns the investigation
    Then a specialist agent is selected
    And the selected specialist has a technical responsibility relevant to the incident

  @FR-08 @NFR-04
  Scenario: Specialists collaborate during an investigation
    Given a specialist is investigating an incident
    And additional cross-domain evidence is required
    When another specialist contributes to the investigation
    Then tasks or observations can be exchanged between agents
    And collected evidence or diagnostic findings can be exchanged between agents
    And the collaboration remains traceable in the investigation activity

  @FR-09 @NFR-06
  Scenario: Live evidence is collected through controlled tools
    Given an agent is investigating an incident
    When the agent requires live operational evidence
    Then the agent can invoke an allowed diagnostic tool
    And the collected observation is associated with the investigation
    And unrestricted shell access is not required
