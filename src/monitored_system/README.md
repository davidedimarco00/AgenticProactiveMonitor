# Monitored System - Notes Platform

This directory contains the standalone Notes Platform observed by AgenticProactiveMonitor. It runs as a separate Docker Compose project and shares only the observability network with the monitoring infrastructure.

## Application

The monitored workload is a small distributed personal notes platform. A user can view, create, open, edit and delete notes. Notes are persisted in SQLite on a Docker volume.

## Architecture

```text
traffic-generator
       |
       v
api-gateway (Flask)
       |
       v
processing-service (FastAPI)
       |
       v
data-service (FastAPI + SQLite)

worker-service (independent synthetic background node)
```

`traffic-generator` behaves as a synthetic user and performs real HTTP requests against the Notes Platform. `worker-service` is intentionally kept as an independent background node with a controlled synthetic workload. It is not part of the HTTP request path; it provides a fifth monitored entity and a reproducible target for resource faults such as memory exhaustion.

## Project structure

```text
src/monitored_system/
├── docker-compose.yml
├── .env.example
├── README.md
├── infrastructure/
│   ├── Dockerfile
│   ├── entrypoint.sh
│   ├── telegraf.conf
│   ├── fluent-bit.conf
│   ├── parsers.conf
│   └── scenarios/
│       ├── README.md
│       ├── reset-to-base.ps1
│       ├── cpu-spike/
│       ├── memory-leak/
│       ├── high-latency/
│       └── data-service-down/
└── src/
    ├── common/
    ├── api-gateway/
    ├── processing-service/
    ├── data-service/
    └── traffic-generator/
```

`infrastructure/` contains Docker runtime, telemetry configuration and controlled test scenarios. `src/` contains application source code.

## Ports

- `8080`: Flask Notes Platform dashboard
- `8002`: FastAPI Notes API, exposed for development/debugging
- `8003`: FastAPI Data Service, exposed for development/debugging

## Normal workload

The default traffic generator performs real actions every 2-5 seconds:

- dashboard browsing;
- note creation;
- note reads;
- note updates;
- occasional note deletion.

Every generated operation carries an `X-Request-ID`, propagated through the API Gateway, Processing Service and Data Service for distributed log correlation.

## Observability

Every monitored container runs Telegraf and Fluent Bit.

- Telegraf writes metrics to `metrics-<service>-YYYY.MM.DD`.
- Fluent Bit writes logs to `logs-<service>-YYYY.MM.DD`.
- Container CPU is collected as `docker_container_cpu.usage_percent`.
- Container memory is collected as `docker_container_mem.usage_percent`.
- Application JSON Lines are written to `/var/log/machine/app.log`.
- System heartbeat records are written to `/var/log/machine/system.log`.

The current OpenSearch Anomaly Detection configuration uses ten SINGLE_ENTITY detectors: one CPU detector and one RAM detector for each of the five monitored services.

## Controlled scenarios

Restore the normal base state at any time:

```powershell
.\infrastructure\scenarios\reset-to-base.ps1
```

CPU spike on `processing-service`:

```powershell
.\infrastructure\scenarios\cpu-spike\start.ps1 -Workers 4
.\infrastructure\scenarios\cpu-spike\stop.ps1
```

Memory leak on `worker-service`:

```powershell
.\infrastructure\scenarios\memory-leak\start.ps1
.\infrastructure\scenarios\memory-leak\stop.ps1
```

High processing latency:

```powershell
.\infrastructure\scenarios\high-latency\start.ps1 -DelayMs 2500
.\infrastructure\scenarios\high-latency\stop.ps1
```

Data Service unavailable:

```powershell
.\infrastructure\scenarios\data-service-down\start.ps1
.\infrastructure\scenarios\data-service-down\stop.ps1
```

CPU and RAM scenarios map directly to the current metric-based OpenSearch detectors. High latency and service unavailability are retained as controlled ground-truth incidents for diagnostic reasoning using telemetry and logs and for future detector extensions.

## Start

From `src/monitored_system`:

```powershell
docker compose build
docker compose up -d
```

Then open `http://localhost:8080`.
