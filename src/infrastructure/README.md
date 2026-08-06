# Infrastructure-only baseline

This folder currently contains only the components required to collect telemetry and run OpenSearch anomaly detection:

- OpenSearch 3.7.0 and OpenSearch Dashboards 3.7.0;
- five monitored Linux containers;
- Telegraf for system metrics;
- Fluent Bit for application and system logs;
- one CPU detector and one memory detector.

OpenSearch 3.7.0 is the latest official GA release verified on 2026-08-06. The version is explicitly pinned instead of using the floating `latest` Docker tag so that local runs remain reproducible.

Qdrant, Ollama, Open WebUI, XMPP, SPADE agents, remediation services, duplicate validators, and dashboard bootstrap scripts are intentionally excluded from this phase.

## Index layout

Each monitored machine writes to its own daily metric and log indexes:

```text
metrics-machine-01-YYYY.MM.DD
logs-machine-01-YYYY.MM.DD
...
metrics-machine-05-YYYY.MM.DD
logs-machine-05-YYYY.MM.DD
```

Metrics and logs are kept in separate index families because they have different mappings and retention requirements.

## Clean start

From `src/infrastructure`:

```bash
cp .env.example .env
docker compose down -v --remove-orphans
docker compose build --no-cache
docker compose pull
docker compose up -d
```

`docker compose down -v` deletes the OpenSearch volume, including previous indexes and detectors. Use it only when a completely clean environment is required.

## Check startup

```bash
docker compose ps
docker compose logs opensearch-init
docker compose logs opensearch-detectors-init
docker compose logs machine-01
```

The expected final state is:

- `opensearch`, `opensearch-dashboards`, and all five machines are running and healthy;
- `opensearch-init` exited with code `0`;
- `opensearch-detectors-init` exited with code `0`.

## Check installed version

```powershell
curl.exe "http://localhost:9200"
```

The response must contain:

```json
"number": "3.7.0"
```

## Check indexes

Bash:

```bash
curl -s "http://localhost:9200/_cat/indices/metrics-machine-*,logs-machine-*?v&s=index"
```

PowerShell:

```powershell
curl.exe "http://localhost:9200/_cat/indices/metrics-machine-*,logs-machine-*?v&s=index"
```

## Check real documents

CPU metrics:

```powershell
curl.exe -X POST "http://localhost:9200/metrics-machine-*/_search?pretty" `
  -H "Content-Type: application/json" `
  -d '{"size":1,"query":{"term":{"measurement_name":"cpu"}},"sort":[{"@timestamp":"desc"}]}'
```

Logs:

```powershell
curl.exe -X POST "http://localhost:9200/logs-machine-*/_search?pretty" `
  -H "Content-Type: application/json" `
  -d '{"size":1,"sort":[{"@timestamp":"desc"}]}'
```

## Check detectors

Open `http://localhost:5601`, then go to **Anomaly detection > Detectors**. The following detectors should be present and running:

- `infrastructure-cpu-usage` using `cpu.usage_active`;
- `infrastructure-memory-usage` using `mem.used_percent`.

REST verification:

```powershell
curl.exe -X POST "http://localhost:9200/_plugins/_anomaly_detection/detectors/_search?pretty" `
  -H "Content-Type: application/json" `
  -d '{"size":20,"query":{"match_all":{}}}'
```

## Focused troubleshooting

```bash
docker compose logs -f opensearch
docker compose logs -f machine-01
docker compose logs -f opensearch-detectors-init
```

Do not start the agentic system from this folder. It will be reintroduced only after telemetry ingestion and anomaly detection are stable.
