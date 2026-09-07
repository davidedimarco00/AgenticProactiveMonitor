# Thesis requirements traceability

This document is based on the current Chapter 3 thesis specification, not on `docs/report/Requirements.md` in the repository.

The thesis defines functional requirements `FR-01` to `FR-18`, non-functional requirements `NFR-01` to `NFR-07`, and operator user stories `US-01` to `US-10`. BDD is used for selected externally observable behaviours. The intended traceability chain is:

`User Story -> Requirement -> Gherkin Scenario -> Executable Verification`

## Functional requirements

| Requirement | Behaviour covered by Gherkin | Feature / scenario | Main verification level |
| --- | --- | --- | --- |
| FR-01 | Metrics and logs are continuously collected from monitored services. | `thesis_telemetry_anomaly_detection.feature` / Telemetry is collected from monitored services | Integration / E2E |
| FR-02 | Stored telemetry keeps timestamp and monitored-component identity. | `thesis_telemetry_anomaly_detection.feature` / Stored telemetry preserves operational context | Integration / E2E |
| FR-03 | Monitored metrics are analysed automatically for anomalous behaviour. | `thesis_telemetry_anomaly_detection.feature` / An anomalous metric is detected automatically | System / E2E |
| FR-04 | A detected anomaly identifies the affected monitored entity and anomaly information. | `thesis_telemetry_anomaly_detection.feature` / A detected anomaly is associated with one monitored entity | System / E2E |
| FR-05 | A relevant anomaly automatically creates and starts an incident. | `thesis_autonomous_incident.feature` / A detected anomaly starts an investigation | E2E |
| FR-06 | Incident lifecycle and history are maintained. | `thesis_autonomous_incident.feature` / Incident progress remains traceable | Integration / E2E |
| FR-07 | Incidents are investigated by specialised autonomous agents. | `thesis_multi_agent_investigation.feature` / A specialist is selected according to the incident | E2E |
| FR-08 | Agents exchange tasks, observations, evidence, and findings. | `thesis_multi_agent_investigation.feature` / Specialists collaborate during an investigation | Integration / E2E |
| FR-09 | Agents obtain live evidence through controlled diagnostic tools. | `thesis_multi_agent_investigation.feature` / Live evidence is collected through controlled tools | Integration / E2E |
| FR-10 | Infrastructure-specific knowledge is available during diagnosis. | `thesis_knowledge_reasoning.feature` / Technical knowledge supports an investigation | Integration / E2E |
| FR-11 | Local language-model reasoning interprets evidence and retrieved knowledge. | `thesis_knowledge_reasoning.feature` / Diagnostic reasoning is performed locally | Integration / E2E |
| FR-12 | Diagnosis contains a plausible root cause, supporting evidence, and uncertainty. | `thesis_diagnostic_result.feature` / An investigation produces an evidence-supported diagnosis | E2E |
| FR-13 | Remediation and verification guidance is provided when information is sufficient. | `thesis_diagnostic_result.feature` / Sufficient evidence produces remediation and verification guidance | E2E |
| FR-14 | Structured incidents, results, and investigation history are persisted. | `thesis_autonomous_incident.feature` / Completed investigation information remains available | Integration / E2E |
| FR-15 | Operator can inspect health, incidents, anomalies, activity, results, and remediation. | `thesis_operator_supervision.feature` / Operator observes an autonomous investigation | E2E |
| FR-16 | Operator can inspect detailed monitoring information and export a report. | `thesis_operator_supervision.feature` / Operator performs deeper inspection and exports a report | E2E |
| FR-17 | Operator can upload technical documents to the knowledge base. | `thesis_knowledge_reasoning.feature` / Operator adds technical knowledge | E2E |
| FR-18 | Findings and guidance are presented in clear natural language. | `thesis_diagnostic_result.feature` / Diagnostic outcome is understandable to the operator | E2E |

## Non-functional requirements

| Requirement | Behaviour covered by Gherkin | Feature / scenario | Main verification level |
| --- | --- | --- | --- |
| NFR-01 | Monitoring and diagnostic data remain inside owner-controlled infrastructure. | `thesis_quality_constraints.feature` / Diagnostic processing remains local | Deployment / Integration |
| NFR-02 | Monitored application and monitoring infrastructure remain logically separated. | `thesis_quality_constraints.feature` / Monitoring is separated from the monitored application | Architecture / Integration |
| NFR-03 | Main components remain modular and independently replaceable where possible. | `thesis_quality_constraints.feature` / System capabilities are exposed through separated components | Architecture / Integration |
| NFR-04 | Diagnostic activities and results are observable and traceable. | `thesis_multi_agent_investigation.feature` / Investigation activity is traceable | Integration / E2E |
| NFR-05 | Operational evidence is distinguished from diagnostic interpretation and uncertainty remains explicit. | `thesis_diagnostic_result.feature` / Evidence and interpretation remain distinguishable | E2E |
| NFR-06 | Agents use controlled diagnostic tools instead of unrestricted system access. | `thesis_multi_agent_investigation.feature` / Live evidence is collected through controlled tools | Integration / E2E |
| NFR-07 | Deployment and configuration are repeatable. | `thesis_quality_constraints.feature` / Monitoring and diagnosis can be deployed from versioned configuration | Deployment / Integration |

## User-story coverage

- `US-01` -> FR-03, FR-04 -> automatic anomaly detection and entity association.
- `US-02` -> FR-05 -> autonomous incident start.
- `US-03` -> FR-15 -> operator health inspection.
- `US-04` -> FR-06, FR-14, FR-15 -> active and historical incidents.
- `US-05` -> FR-15 -> anomaly and agent activity inspection.
- `US-06` -> FR-12, FR-15 -> diagnosis, root cause, evidence, uncertainty.
- `US-07` -> FR-13, FR-15 -> remediation and verification guidance.
- `US-08` -> FR-16 -> detailed monitoring and incident report export.
- `US-09` -> FR-17 -> knowledge-base document upload.
- `US-10` -> FR-18 -> clear natural-language diagnostic output.

The `.feature` files define the acceptance specification. Python step definitions should verify the behaviour through public interfaces or complete system workflows rather than checking private implementation details.