Feature: Knowledge-supported local reasoning
  Diagnostic agents must be able to use infrastructure-specific knowledge and local language-model reasoning during investigation, and the operator must be able to update the technical knowledge base.

  @FR-10
  Scenario: Technical knowledge supports an investigation
    Given an incident is under investigation
    And the investigation requires infrastructure-specific context
    When the investigating agent requests relevant technical knowledge
    Then relevant knowledge can be retrieved from the diagnostic knowledge base
    And retrieved knowledge is used as supporting context for the investigation

  @FR-11 @NFR-01
  Scenario: Diagnostic reasoning is performed locally
    Given operational evidence or retrieved technical knowledge is available
    When an agent requests language-model-based interpretation
    Then the reasoning request is processed by the locally configured language-model infrastructure
    And operational diagnostic information is not required to be sent to an external cloud model

  @FR-17 @US-09
  Scenario: Operator adds technical knowledge
    Given the diagnostic knowledge base is available
    When the operator uploads a supported technical document
    Then the document becomes available to the diagnostic knowledge workflow
    And the uploaded knowledge can be retrieved during a later investigation
