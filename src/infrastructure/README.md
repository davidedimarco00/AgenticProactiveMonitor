# Agentic Proactive Monitor Infrastructure

This directory contains only the infrastructure used by the **agentic monitoring system**.
The software under observation is intentionally separated into `src/monitored_system` and runs as a different Docker Compose project.

## Runtime architecture

Docker Desktop shows two independent projects, while Ollama runs directly on Windows:

```text
Windows host
  - Ollama (native, NVIDIA GPU)

agentic-proactive-monitor-infrastructure
  - OpenSearch
  - OpenSearch Dashboards
  - Qdrant
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

The monitored services are **not defined in the infrastructure Compose file**. Ollama is also intentionally outside Docker so Docker Desktop can use the Hyper-V/LinuxKit backend required by the validated `tc/netem` network-fault scenario while Ollama continues to use the host NVIDIA GPU.

## Integration boundary

The two Docker projects communicate through:

```text
agentic-monitoring-net
```

The agentic infrastructure creates the network. The standalone monitored system attaches to it only to send telemetry to OpenSearch.

Ollama is reached from Docker containers through:

```text
http://host.docker.internal:11434
```

The monitored system owns a separate private network, `monitored-system-net`, for application-to-application communication.

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

## Configure native Ollama on Windows

Install and start Ollama for Windows first. Then, from PowerShell:

```powershell
cd src\infrastructure
.\ollama\init\setup-windows.ps1
```

The script:

- configures `OLLAMA_HOST=0.0.0.0:11434` for the current Windows user;
- configures `OLLAMA_KEEP_ALIVE=5m` and `OLLAMA_NUM_PARALLEL=1`;
- pulls `gemma4:e2b`, `qwen3.5:4b`, `qwen2.5:latest` and `ibm/granite-embedding:30m` by default.

After the script completes, quit Ollama from the Windows tray and start it again so it inherits the new environment variables.

Verify the host API:

```powershell
curl.exe http://localhost:11434/api/tags
```

Verify access from Docker Desktop:

```powershell
docker run --rm curlimages/curl:8.10.1 -fsS http://host.docker.internal:11434/api/tags
```

## Configure the agentic infrastructure

From PowerShell:

```powershell
cd src\infrastructure
Copy-Item .env.example .env
```

The default `.env.example` uses:

```text
OLLAMA_HOST_URL=http://host.docker.internal:11434
```

Validate the Compose configuration:

```powershell
docker compose config
```

## Start the two systems

Start native Ollama first, then the agentic infrastructure because it creates `agentic-monitoring-net`:

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

Ollama must not appear as a Docker container.

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

CPU and RAM detectors are created for all five monitored services. Network-latency detectors cover the three critical application links. Every detector is **SINGLE_ENTITY** and reads only the dedicated source-service metrics index.

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

Native Ollama remains independent from Docker Compose and can continue running while the Docker projects are stopped.
