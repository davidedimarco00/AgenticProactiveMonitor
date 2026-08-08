# Monitored System - Notes Platform

This directory contains the standalone Notes Platform observed by AgenticProactiveMonitor. It runs as a separate Docker Compose project and shares only the observability network with the agentic infrastructure.

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
```

`traffic-generator` behaves as a synthetic user and performs real HTTP requests against Flask. `worker-service` still uses the temporary synthetic workload and will be implemented separately.

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
│       ├── data-service-down/
│       └── high-latency/
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

## Traffic generator

The default workload performs real actions every 2-5 seconds:

- dashboard browsing;
- note creation;
- note reads;
- note updates;
- occasional note deletion.

Every generated operation carries an `X-Request-ID`, propagated through the API Gateway, Notes Service and Data Service for distributed log correlation.

## Observability

Every monitored container runs Telegraf and Fluent Bit.

- Telegraf writes metrics to `metrics-<service>-YYYY.MM.DD`.
- Fluent Bit writes logs to `logs-<service>-YYYY.MM.DD`.
- Application JSON Lines are written to `/var/log/machine/app.log`.
- System heartbeat records are written to `/var/log/machine/system.log`.

## Controlled scenarios

Scenario scripts are under `infrastructure/scenarios/`. Each scenario includes PowerShell start/stop scripts and a `scenario.yaml` ground-truth description.

### Data Service unavailable

```powershell
.\infrastructure\scenarios\data-service-down\start.ps1
.\infrastructure\scenarios\data-service-down\stop.ps1
```

### High processing latency

```powershell
.\infrastructure\scenarios\high-latency\start.ps1
.\infrastructure\scenarios\high-latency\start.ps1 -DelayMs 2500
.\infrastructure\scenarios\high-latency\stop.ps1
```

The latency test is enabled at runtime and does not require rebuilding or restarting the processing service.

## Start

From `src/monitored_system`:

```powershell
docker compose build
docker compose up -d
```

Then open `http://localhost:8080`.
