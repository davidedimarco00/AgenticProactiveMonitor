Feature: Evidence-supported diagnostic outcome
  The diagnostic workflow must produce an understandable result that remains connected to evidence and includes uncertainty, remediation, and verification guidance when appropriate.

  @FR-12 @US-06 @NFR-05
  Scenario: An investigation produces an evidence-supported diagnosis
    Given a diagnostic investigation has collected relevant operational evidence
    When the investigation reaches a diagnostic conclusion
    Then the result contains a plausible root cause
    And the result identifies the main evidence supporting the conclusion
    And diagnostic uncertainty is represented when necessary

  @NFR-05
  Scenario: Evidence and interpretation remain distinguishable
    Given a diagnostic result is available
    When the operator reviews the result
    Then observed operational evidence can be distinguished from generated diagnostic interpretation
    And an uncertain interpretation is not presented as confirmed operational evidence

  @FR-13 @US-07
  Scenario: Sufficient evidence produces remediation and verification guidance
    Given a diagnostic result is supported by sufficient information
    When the diagnostic workflow prepares the final guidance
    Then remediation guidance is available
    And verification guidance is available
    And the guidance remains advisory for the operator

  @FR-18 @US-10
  Scenario: Diagnostic outcome is understandable to the operator
    Given a diagnostic result and guidance are available
    When the operator inspects the diagnostic outcome
    Then the diagnosis is presented in clear natural language
    And remediation recommendations are presented in clear natural language
    And verification guidance is presented in clear natural language
