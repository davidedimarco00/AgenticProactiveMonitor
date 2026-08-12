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

`traffic-generator` behaves as a synthetic user and performs real HTTP requests against the Notes Platform. `worker-service` is intentionally kept as an independent background node with a controlled synthetic workload.

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
│       ├── network-latency/
│       ├── high-latency/
│       └── data-service-down/
└── src/
    ├── common/
    ├── api-gateway/
    ├── processing-service/
    ├── data-service/
    └── traffic-generator/
```

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
- Container CPU: `docker_container_cpu.usage_percent`.
- Container memory: `docker_container_mem.usage_percent`.
- Interface counters: Telegraf `net` measurement.
- Raw ICMP RTT: Telegraf `ping` measurement.
- End-to-end network-service latency: `network_service_latency.response_time`.
- Application logs: `/var/log/machine/app.log`.
- System heartbeat: `/var/log/machine/system.log`.

The three critical links used by network-latency detection are:

```text
traffic-generator   -> api-gateway
api-gateway         -> processing-service
processing-service  -> data-service
```

OpenSearch Anomaly Detection uses only SINGLE_ENTITY detectors:

- 5 CPU detectors;
- 5 RAM detectors;
- 3 network-latency detectors.

## Controlled scenarios

Restore the normal base state:

```powershell
.\infrastructure\scenarios\reset-to-base.ps1
```

CPU spike:

```powershell
.\infrastructure\scenarios\cpu-spike\start.ps1 -Workers 4
.\infrastructure\scenarios\cpu-spike\stop.ps1
```

Memory leak:

```powershell
.\infrastructure\scenarios\memory-leak\start.ps1
.\infrastructure\scenarios\memory-leak\stop.ps1
```

Network latency on `api-gateway -> processing-service`:

```powershell
.\infrastructure\scenarios\network-latency\start.ps1 -DelayMs 250
.\infrastructure\scenarios\network-latency\start.ps1 -DelayMs 250 -JitterMs 25
.\infrastructure\scenarios\network-latency\stop.ps1
```

This scenario uses Linux `tc/netem` on the application-network interface selected from the route to `processing-service`. `api-gateway` receives only the `NET_ADMIN` capability. On Docker Desktop for Windows, the tested environment uses the Hyper-V/LinuxKit backend because the WSL2 kernel used during development did not expose the `netem` qdisc.

Application processing latency:

```powershell
.\infrastructure\scenarios\high-latency\start.ps1 -DelayMs 2500
.\infrastructure\scenarios\high-latency\stop.ps1
```

Data Service unavailable:

```powershell
.\infrastructure\scenarios\data-service-down\start.ps1
.\infrastructure\scenarios\data-service-down\stop.ps1
```

`network-latency` and `high-latency` intentionally represent different root causes. The first delays packets in the Linux traffic-control layer; the second adds delay inside the application. This gives the diagnostic agents independent evidence to distinguish network degradation from application slowdown.

## Start

From `src/monitored_system`:

```powershell
docker compose build
docker compose up -d
```

Then open `http://localhost:8080`.
