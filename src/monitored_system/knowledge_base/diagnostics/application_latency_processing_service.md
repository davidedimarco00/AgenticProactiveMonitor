---
kb_id: monitored-system.diagnostic.application-latency-processing-service
version: 2
domain: monitored_system
document_type: diagnostic-guide
roles:
  [application_engineer, software_developer, system_engineer, technical_lead]
domains: [application, latency, dependencies, logs, cpu]
services: [traffic-generator, api-gateway, processing-service, data-service]
incident_types: [application-latency, performance]
source_files:
  [
    src/monitored_system/src/api-gateway/app.py,
    src/monitored_system/src/processing-service/app.py,
    src/monitored_system/src/data-service/app.py,
    src/monitored_system/src/traffic-generator/generator.py,
    src/monitored_system/infrastructure/telegraf.conf,
  ]
---

# Application Latency on processing-service

## Diagnostic pattern

`processing-service` sits between `api-gateway` and `data-service`. Slow user-visible operations can therefore originate inside processing-service or be propagated from its downstream dependency.

A local application-latency hypothesis becomes stronger when request timing increases while independent network probes remain near their normal baselines.

## Evidence that supports local application delay

Relevant evidence includes:

- increased api-gateway downstream `latency_ms` for calls to processing-service;
- increased traffic-generator action latency for correlated operations;
- processing-service logs showing that requests continue to be received and handled;
- normal `api-gateway -> processing-service` ICMP RTT;
- normal `api-gateway -> processing-service` `network_service_latency.response_time`;
- no stronger evidence that `data-service` is unavailable or degraded.

## Evidence that weakens the hypothesis

A local processing-service explanation is weaker when:

- network-service latency and raw ICMP RTT rise together;
- processing-service reports downstream failures to `data-service`;
- data-service telemetry disappears or its health fails;
- the main anomaly is high container CPU and the timing increase follows the resource anomaly.

## Alternative explanations

Slow requests can result from:

- network degradation between api-gateway and processing-service;
- CPU pressure inside processing-service;
- slow or unavailable data-service;
- a wider runtime or infrastructure problem.

Use request correlation to determine where time or failure first appears in the chain.

## Useful diagnostic checks

- compare traffic-generator action latency with api-gateway downstream latency;
- correlate api-gateway and processing-service logs using `request_id`;
- compare application timing with `network_service_latency.response_time` and ping RTT;
- inspect processing-service CPU metrics;
- inspect processing-service logs for `data_service_unavailable` or other downstream errors;
- verify that fresh data-service telemetry is present.

## Diagnosis rule

Do not classify slow user requests as a processing-service problem only because processing-service is in the request path. Prefer the component whose live evidence first explains the additional latency, and reject the local application hypothesis when network or downstream evidence provides a stronger causal explanation.
