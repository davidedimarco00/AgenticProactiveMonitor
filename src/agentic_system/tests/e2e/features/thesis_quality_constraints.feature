Feature: Architectural and deployment quality constraints
  The monitoring and diagnostic system must preserve local processing, logical separation, modularity, and repeatable deployment.

  @NFR-01
  Scenario: Diagnostic processing remains local
    Given the monitoring and diagnostic infrastructure is deployed locally
    When operational evidence is processed for diagnosis
    Then the configured diagnostic services operate inside the controlled infrastructure
    And local language-model services can process the diagnostic request

  @NFR-02
  Scenario: Monitoring is separated from the monitored application
    Given the monitored application is running
    And the monitoring infrastructure is running
    When monitoring services collect operational information
    Then the monitored application remains a separate deployable system
    And the monitoring infrastructure remains a separate deployable system

  @NFR-03
  Scenario: System capabilities are exposed through separated components
    Given the complete monitoring and diagnostic system is deployed
    When its main capabilities are inspected
    Then telemetry monitoring is provided by a separated monitoring subsystem
    And diagnostic tools are provided by a separated tool subsystem
    And knowledge retrieval is provided by a separated knowledge subsystem
    And agent execution is provided by a separated agentic subsystem
    And operator supervision is provided by a separated interface subsystem

  @NFR-07
  Scenario: Monitoring and diagnosis can be deployed from versioned configuration
    Given the repository configuration is available
    When the monitoring and diagnostic infrastructure is deployed from the documented configuration
    Then the required infrastructure services can be started reproducibly
    And the deployed system uses the versioned configuration from the repository
