Feature: Operator supervision and reporting
  The operator must be able to inspect the monitoring and diagnostic state, while autonomous investigations remain system-driven rather than manually steered.

  @FR-15 @US-03 @US-04 @US-05 @US-06 @US-07
  Scenario: Operator observes an autonomous investigation
    Given the monitoring and diagnostic system is operational
    And an incident exists
    When the operator opens the supervision interface
    Then system health is visible
    And incident and anomaly information is visible
    And agent activity is visible
    And diagnostic results are visible when available
    And remediation information is visible when available

  @FR-05 @US-02
  Scenario: Operator access does not start or steer an investigation
    Given the agentic backend is operational
    When the operator accesses incident information
    Then incident information can be inspected
    And the operator is not required to start the investigation
    And the operator cannot directly steer the autonomous investigation through the read-oriented incident interface

  @FR-16 @US-08
  Scenario: Operator performs deeper inspection and exports a report
    Given an incident is available for inspection
    When the operator requests detailed operational information
    Then detailed monitoring information can be accessed
    And an incident report can be exported when required
