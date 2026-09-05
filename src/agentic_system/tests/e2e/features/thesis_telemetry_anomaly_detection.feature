Feature: Monitoring and anomaly detection
  The monitoring layer must collect operational telemetry, analyse monitored metrics automatically, and preserve the entity associated with each anomaly.

  @FR-01
  Scenario: Telemetry is collected from monitored services
    Given the monitoring infrastructure is operational
    And monitored services are running
    When the monitored services produce metrics and logs
    Then metrics are collected from the monitored services
    And logs are collected from the monitored services

  @FR-02
  Scenario: Stored telemetry preserves operational context
    Given telemetry has been collected from a monitored service
    When the telemetry is stored centrally
    Then each stored telemetry record preserves its timestamp
    And each stored telemetry record identifies the monitored component that produced it

  @FR-03 @US-01
  Scenario: An anomalous metric is detected automatically
    Given the monitoring infrastructure is operational
    And a monitored entity is producing metrics
    When the metric behaviour becomes anomalous
    Then anomalous behaviour is identified automatically
    And no operator action is required to start anomaly analysis

  @FR-04 @US-01
  Scenario: A detected anomaly is associated with one monitored entity
    Given a single-entity anomaly detector is monitoring a monitored entity
    When the detector reports a relevant anomaly
    Then the anomaly identifies the affected monitored entity
    And relevant anomaly information is available for the diagnostic workflow
    And the anomaly is not associated with multiple monitored entities
