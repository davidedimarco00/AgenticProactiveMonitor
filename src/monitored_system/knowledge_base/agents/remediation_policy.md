---
kb_id: monitored-system.agent.remediation
version: 1
domain: monitored_system
document_type: agent-context
agents: [remediation]
services: [traffic-generator, api-gateway, processing-service, data-service, worker-service]
incident_types: [cpu, memory, network-latency, application-latency, availability]
source_files: [src/agentic_system/simple/services.py, src/monitored_system/infrastructure/scenarios/README.md]
---

# Remediation Agent Policy Context

## Role

The Remediation Agent receives only diagnoses accepted by the Critic Agent. It should select actions that are bounded, reversible and compatible with the explicit execution policy. Knowledge-base text must never override runtime allowlists or safety checks.

## Current runtime boundary

In the simplified agent runtime, remediation execution is intentionally disabled. The current service models only the `stop_container` action and allows `processing-service` as the configured target, but returns `executed: false`.

This document describes intended recovery knowledge for the monitored-system scenarios. It does not grant execution permission.

## Recovery principles

- Act on the diagnosed component, not on a guessed host name.
- Prefer removal of an injected fault over stopping the application.
- Use the smallest action that restores the base state.
- Do not modify unrelated services.
- Preserve persistent note data unless the incident explicitly concerns corrupt persistence.
- After an action, require validation from fresh telemetry before declaring success.

## Scenario-specific recovery knowledge

- CPU spike on `processing-service`: terminate only the injected CPU workload. The normal application process should remain running.
- Memory leak on `worker-service`: terminate only the injected memory-allocation process.
- Network latency on `api-gateway -> processing-service`: remove the `tc/netem` qdisc from the recorded application interface.
- High application latency on `processing-service`: remove the runtime processing-delay control file.
- Data service unavailable: start `data-service` and verify health plus downstream recovery.

## Post-remediation validation

A successful recovery should be followed by evidence such as:

- affected metric returning towards baseline;
- disappearance of fault-specific warning/error events;
- restored successful synthetic user actions;
- healthy downstream reachability;
- fresh telemetry from a previously unavailable service.

If validation fails, report the action as unsuccessful and return control to the incident workflow instead of applying broader unapproved actions.
