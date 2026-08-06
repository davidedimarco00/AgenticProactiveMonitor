# Infrastructure-only baseline

This folder contains only the components required to collect telemetry, explore it in OpenSearch Dashboards, and run OpenSearch anomaly detection:

- OpenSearch 3.6.0 and OpenSearch Dashboards 3.6.0;
- five monitored Linux containers;
- Telegraf for system metrics;
- Fluent Bit for application and system logs;
- ten per-machine index patterns for Discover;
- one CPU detector and one memory detector.

Qdrant, Ollama, Open WebUI, XMPP, SPADE agents, remediation services, and duplicate validation scripts are intentionally excluded from this phase.

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

## Automatic Discover index patterns

The `opensearch-dashboards-init` one-shot service creates or updates these patterns automatically:

```text
metrics-machine-01-*
logs-machine-01-*
metrics-machine-02-*
logs-machine-02-*
metrics-machine-03-*
logs-machine-03-*
metrics-machine-04-*
logs-machine-04-*
metrics-machine-05-*
logs-machine-05-*
```

All patterns use `@timestamp` as their time field. Wildcards are used so the same patterns continue to work when new daily indexes are created.

## Clean start

From `src/infrastructure`:

```bash
cp .env.example .env
docker compose down -v --remove-orphans
docker compose build --no-cache
docker compose up -d
```

`docker compose down -v` deletes the OpenSearch volume, including previous indexes and detectors. Use it only when a completely clean environment is required.

The monitored-machine configuration is copied into its Docker image. After changing `telegraf.conf`, `fluent-bit.conf`, `parsers.conf`, or `entrypoint.sh`, rebuild the five machine services before restarting them.

PowerShell:

```powershell
docker compose build --no-cache machine-01 machine-02 machine-03 machine-04 machine-05
docker compose up -d --force-recreate
```

## Check startup

```bash
docker compose ps -a
docker compose logs opensearch-init
docker compose logs opensearch-dashboards-init
docker compose logs opensearch-detectors-init
docker compose logs machine-01
```

The expected final state is:

- `opensearch`, `opensearch-dashboards`, and all five machines are running and healthy;
- `opensearch-init` exited with code `0`;
- `opensearch-dashboards-init` exited with code `0` after creating ten patterns;
- `opensearch-detectors-init` exited with code `0`.

## Check indexes

Bash:

```bash
curl -s "http://localhost:9200/_cat/indices/metrics-machine-*,logs-machine-*?v&s=index"
```

PowerShell:

```powershell
curl.exe "http://localhost:9200/_cat/indices/metrics-machine-*,logs-machine-*?v&s=index"
```

## Check Discover

Open `http://localhost:5601`, then go to **Discover**. The data-view selector should contain all ten per-machine patterns.

To rerun only the pattern initializer:

```powershell
docker compose rm -f opensearch-dashboards-init
docker compose up -d opensearch-dashboards-init
docker compose logs -f opensearch-dashboards-init
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
docker compose logs -f opensearch-dashboards-init
docker compose logs -f opensearch-detectors-init
```

Do not start the agentic system from this folder. It will be reintroduced only after telemetry ingestion and anomaly detection are stable.
