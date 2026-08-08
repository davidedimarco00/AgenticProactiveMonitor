# Monitored System

This directory contains the standalone software environment observed by AgenticProactiveMonitor.
It is intentionally separated from the agentic infrastructure and runs as its own Docker Compose project: `monitored-system`.

## Services

- `traffic-generator`: generates requests for the monitored application flow.
- `api-gateway`: receives and forwards requests to the processing layer.
- `processing-service`: FastAPI service that performs the main application processing.
- `data-service`: represents the data access layer.
- `worker-service`: performs background or asynchronous work.

## Communication topology

```text
traffic-generator
       |
       v
  api-gateway
       |
       v
processing-service
    /       \
   v         v
data-service  worker-service
```

## Current implementation status

The monitored system is being converted incrementally from synthetic workloads to real application services.

- `processing-service` is the first real FastAPI application.
- `traffic-generator`, `api-gateway`, `data-service`, and `worker-service` still use the synthetic workload temporarily.
- All containers continue to run Telegraf and Fluent Bit.

### Processing service

The service is exposed on host port `8002` and provides:

- `GET /health`
- `POST /process`

Example request:

```json
{
  "value": 10
}
```

The service multiplies the input value by `PROCESSING_MULTIPLIER` (default `2`) and returns the result together with a request identifier and processing time.
`PROCESSING_DELAY_MS` can be used to configure a small artificial processing delay and will later be useful for controlled fault scenarios.

## Observability

Each monitored service contains Telegraf and Fluent Bit.

- Telegraf collects host/container metrics and writes them to `metrics-<service>-YYYY.MM.DD`.
- Fluent Bit collects application and system logs and writes them to `logs-<service>-YYYY.MM.DD`.
- Real application services write structured JSON events to `/var/log/machine/app.log`.

The monitored stack connects to the Docker network created by the agentic infrastructure only for observability traffic. The application services also have their own private `monitored-system-net`.

## Docker projects

The two environments are intentionally separate:

```text
agentic-proactive-monitor-infrastructure
  -> OpenSearch, Qdrant, Ollama, XMPP, MCP, Dashboards, bootstrap services

monitored-system
  -> traffic-generator, api-gateway, processing-service, data-service, worker-service
```

## Failure scenarios

Examples that can later be introduced intentionally:

1. High CPU
2. Memory leak
3. Service crash
4. Data service unavailable
5. API timeout
6. HTTP 500 errors
7. Disk saturation
8. Network degradation
