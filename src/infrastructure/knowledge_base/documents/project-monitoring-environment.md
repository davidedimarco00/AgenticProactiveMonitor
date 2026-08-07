# AgenticProactiveMonitor - Monitoring Environment

This document describes the monitored infrastructure used in the thesis laboratory.

## Monitored machines

- machine-01: application-server
- machine-02: database-server
- machine-03: api-gateway
- machine-04: worker-node
- machine-05: edge-node

Each monitored machine runs Telegraf and Fluent Bit.

## Metrics

Telegraf sends infrastructure metrics to OpenSearch every 10 seconds. Metrics are stored in one daily index for each monitored machine using the pattern `metrics-machine-XX-YYYY.MM.DD`.

The main measurements include CPU, memory, disk, disk I/O, network, system load, swap, processes and kernel information.

The fields currently used by anomaly detection are:

- `cpu.usage_active` for CPU usage.
- `mem.used_percent` for memory usage.
- `tag.host_id` to identify the monitored machine.
- `@timestamp` as the time field.

## Logs

Fluent Bit reads application and system logs from every monitored machine and sends them to OpenSearch. Logs are stored in one daily index for each machine using the pattern `logs-machine-XX-YYYY.MM.DD`.

Important log metadata includes `host_id`, `machine_role`, `log_source`, `level`, `service`, `component`, `event_type` and `message`.

## Anomaly detection

OpenSearch Anomaly Detection provides two real-time detectors:

- `CPU_ANOMALY`, based on the average value of `cpu.usage_active`.
- `RAM_ANOMALY`, based on the average value of `mem.used_percent`.

Both detectors use `tag.host_id` as the category field so that the five monitored machines are evaluated as separate entities.

## Troubleshooting objective

When an anomaly is detected, the future agentic system must collect evidence before proposing a root cause. Evidence can include recent metrics, relevant logs, process information, service status, previous incidents and troubleshooting documentation.

A diagnostic conclusion should identify the affected machine, the observed symptoms, the most likely root cause, the evidence supporting the conclusion and a possible remediation action.
