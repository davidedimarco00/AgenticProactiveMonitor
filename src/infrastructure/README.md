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
  - MongoDB
  - Open WebUI
  - Prosody XMPP
  - MCP Server
  - Agentic Backend (SPADE/SPADE-LLM + FastAPI)
  - Agentic Operator Dashboard
  - OpenSearch bootstrap services

monitored-system
  - traffic-generator
  - api-gateway
  - processing-service
  - data-service
  - worker-service
```

The monitored services are **not defined in the infrastructure Compose file**. Ollama is also intentionally outside Docker so Docker Desktop can use the Hyper-V/LinuxKit backend required by the validated `tc/netem` network-fault scenario while Ollama continues to use the host NVIDIA GPU.

## Data responsibility

The infrastructure keeps monitoring data and agentic application data separated:

```text
OpenSearch -> metrics, logs, SINGLE_ENTITY anomaly detection
MongoDB    -> incidents, diagnoses, remediations, validation and incident history
Qdrant     -> monitored-system RAG knowledge base
```

Raw metrics and logs are not copied into MongoDB or the operator dashboard.

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

## Telemetry and incident flow

```text
monitored-system
     |
     +-- Telegraf ----> OpenSearch
     |
     +-- Fluent Bit --> OpenSearch
                         |
                         +--> Dashboards
                         +--> SINGLE_ENTITY Anomaly Detection
                         +--> autonomous Agentic Backend / MCP
                                      |
                                      +--> MongoDB incident history
                                      +--> FastAPI :8082
                                              |
                                              +--> Operator Dashboard :5050
                                              +--> PDF incident report
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

The script configures the host Ollama endpoint and pulls the models configured by the infrastructure.

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

Before a non-local deployment, replace the default MongoDB and XMPP passwords in `.env`.

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

## Main local endpoints

```text
OpenSearch:          http://127.0.0.1:9200
OpenSearch Dashboard http://127.0.0.1:5601
MongoDB:             mongodb://127.0.0.1:27017
Agentic API Swagger: http://127.0.0.1:8082/docs
Operator Dashboard:  http://127.0.0.1:5050
```

The public FastAPI/Swagger contract is read-only for the operator. The dashboard cannot start an investigation; investigations originate from anomaly events handled by the autonomous agentic core.

## Verify the separation

Agentic infrastructure:

```powershell
cd src\infrastructure
docker compose ps
```

MongoDB and backend API:

```powershell
docker compose ps mongodb agentic-backend
docker compose logs -f mongodb
docker compose logs -f agentic-backend
curl.exe http://127.0.0.1:8082/health
```

Monitored system:

```powershell
cd src\monitored_system
docker compose ps
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

## Anomaly detectors

CPU and RAM detectors are created for all five monitored services. Network-latency detectors cover the three critical application links. Every detector is **SINGLE_ENTITY** and reads only the dedicated source-service metrics index.

If the detector or Dashboards bootstrap finishes before telemetry is available, rerun the one-shot services after the monitored stack is running:

```powershell
cd src\infrastructure
docker compose rm -f opensearch-dashboards-init opensearch-detectors-init
docker compose up opensearch-dashboards-init opensearch-detectors-init
```

## Useful commands

```powershell
cd src\infrastructure
docker compose logs -f opensearch
docker compose logs -f mongodb
docker compose logs -f mcp-server
docker compose logs -f xmpp
docker compose logs -f agentic-backend
docker compose logs -f agentic-system-dashboard
```

Normal stop while preserving volumes:

```powershell
cd src\monitored_system
docker compose down

cd ..\infrastructure
docker compose down
```

Native Ollama remains independent from Docker Compose and can continue running while the Docker projects are stopped.
