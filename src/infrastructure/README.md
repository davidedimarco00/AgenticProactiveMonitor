# Agentic Proactive Monitor Infrastructure

This directory contains only the infrastructure used by the **agentic monitoring system**.
The software under observation is intentionally separated into `src/monitored_system` and runs as a different Docker Compose project.

## Docker architecture

Docker Desktop shows two independent projects:

```text
agentic-proactive-monitor-infrastructure
  - OpenSearch
  - OpenSearch Dashboards
  - Qdrant
  - Ollama
  - Open WebUI
  - Prosody XMPP
  - MCP Server
  - OpenSearch bootstrap services

monitored-system
  - traffic-generator
  - api-gateway
  - processing-service
  - data-service
  - worker-service
```

The monitored services are **not defined in this Compose file**.

## Integration boundary

The two projects communicate through the Docker network:

```text
agentic-monitoring-net
```

The agentic infrastructure creates the network. The standalone monitored system attaches to it only to send telemetry to OpenSearch.

The monitored system also owns a separate private network, `monitored-system-net`, for its future application-to-application communication.

## Telemetry flow

```text
monitored-system
     |
     +-- Telegraf ----> OpenSearch
     |
     +-- Fluent Bit --> OpenSearch
                         |
                         +--> Dashboards
                         +--> Anomaly Detection
                         +--> Agentic System / MCP
```

Metrics and logs are stored using the service name:

```text
metrics-traffic-generator-YYYY.MM.DD
metrics-api-gateway-YYYY.MM.DD
metrics-processing-service-YYYY.MM.DD
metrics-data-service-YYYY.MM.DD
metrics-worker-service-YYYY.MM.DD

logs-traffic-generator-YYYY.MM.DD
logs-api-gateway-YYYY.MM.DD
logs-processing-service-YYYY.MM.DD
logs-data-service-YYYY.MM.DD
logs-worker-service-YYYY.MM.DD
```

## Configure the agentic infrastructure

From PowerShell:

```powershell
cd src\infrastructure
Copy-Item .env.example .env
```

Edit `.env` as required before the first start.

Validate the Compose configuration:

```powershell
docker compose config
```

## Start the two systems

Start the agentic infrastructure first because it creates `agentic-monitoring-net`:

```powershell
cd src\infrastructure
docker compose up -d --build
```

Then start the standalone monitored system:

```powershell
cd ..\monitored_system
docker compose up -d --build
```

Docker Desktop should now show two separate sections:

```text
agentic-proactive-monitor-infrastructure
monitored-system
```

## Verify the separation

Agentic infrastructure:

```powershell
cd src\infrastructure
docker compose ps
```

Monitored system:

```powershell
cd src\monitored_system
docker compose ps
```

Expected monitored containers:

```text
traffic-generator
api-gateway
processing-service
data-service
worker-service
```

## Verify OpenSearch data

```powershell
curl.exe "http://localhost:9200/_cat/indices/metrics-*,logs-*?v&s=index"
```

OpenSearch Dashboards is available at:

```text
http://localhost:5601
```

The bootstrap creates one metric and one log data view for each monitored service.

## Anomaly detectors

CPU and RAM detectors are created for:

- `traffic-generator`
- `api-gateway`
- `processing-service`
- `data-service`
- `worker-service`

If the detector or Dashboards bootstrap finishes before telemetry is available, rerun the one-shot services after the monitored stack is running:

```powershell
cd src\infrastructure
docker compose rm -f opensearch-dashboards-init opensearch-detectors-init
docker compose up opensearch-dashboards-init opensearch-detectors-init
```

## Useful commands

Agentic logs:

```powershell
cd src\infrastructure
docker compose logs -f opensearch
docker compose logs -f mcp-server
docker compose logs -f xmpp
```

Monitored service logs:

```powershell
cd src\monitored_system
docker compose logs -f processing-service
```

Normal stop while preserving volumes:

```powershell
cd src\monitored_system
docker compose down

cd ..\infrastructure
docker compose down
```

The monitored system can be rebuilt and restarted independently from the agentic infrastructure.
