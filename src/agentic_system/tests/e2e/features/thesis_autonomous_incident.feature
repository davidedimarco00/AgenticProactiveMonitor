Feature: Autonomous incident lifecycle
  A relevant anomaly must start a diagnostic incident without operator intervention, and the incident lifecycle must remain available for later inspection.

  @FR-05 @US-02
  Scenario: A detected anomaly starts an investigation
    Given the monitoring infrastructure is operational
    And a monitored service is producing telemetry
    When a relevant anomaly is detected
    Then a diagnostic incident is created
    And the investigation starts without operator intervention

  @FR-06 @US-04
  Scenario: Incident progress remains traceable
    Given a diagnostic incident has been created
    When the investigation changes the incident state
    Then the current incident state is available
    And the previous investigation activity remains available in the incident history

  @FR-14 @US-04
  Scenario: Completed investigation information remains available
    Given a diagnostic investigation has produced a result
    When the incident is later inspected
    Then the structured incident information is still available
    And the diagnostic result is still available
    And relevant investigation history is still available
