# Role-Specific Knowledge Bases

This directory contains professional/domain knowledge used by the specialist agents. It is intentionally separate from `src/monitored_system/knowledge_base`, which describes the concrete system being monitored.

The separation is:

```text
monitored-system collection
    = knowledge about this monitored Notes Platform

role-specific collections
    = general professional knowledge used to interpret observations
```

The agent must combine retrieved knowledge with live metrics, logs and tool observations. These knowledge bases must not contain evaluation scenario answers, injected-fault ground truth or symptom-to-diagnosis rules.

## Qdrant collections

```text
technical_lead        -> monitored-system + kb-technical-lead
system_engineer       -> monitored-system + kb-system-engineer-linux
network_engineer      -> monitored-system + kb-network-engineer
application_engineer  -> monitored-system + kb-application-engineer
software_developer    -> monitored-system + kb-software-developer
```

`monitored-system` is shared by all roles. A specialist collection is the primary professional knowledge source for its owner role.

Cross-domain knowledge should normally be obtained through collaboration with the appropriate specialist agent instead of making every agent retrieve every professional collection directly.

## Content policy

Allowed content includes:

- operating-system, network, application and software concepts;
- command semantics and read-only inspection commands;
- protocol and API semantics;
- architecture and observability concepts;
- official technical documentation adapted into concise operational reference material.

Excluded content includes:

- pre-written diagnosis decisions;
- test-suite or scenario descriptions;
- injected-fault parameters;
- known answers to evaluation incidents;
- agent implementation details or hidden reasoning policy.

The Qdrant collection registry is defined in `src/infrastructure/knowledge_base/collections.yaml`.
