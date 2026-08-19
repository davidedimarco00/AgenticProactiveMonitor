Feature: Operator incident API
  The operator must observe the autonomous agentic system without starting or steering investigations.

  Scenario: The published API contract is read-only for incident operations
    Given the agentic backend is ready for operator API checks
    When the operator inspects the published API contract
    Then Swagger documentation is reachable
    And public incident operations are read only
    And the incident PDF report endpoint is published
