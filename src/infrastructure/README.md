# Complete local infrastructure

This folder contains the complete Docker infrastructure used by the project:

- OpenSearch and OpenSearch Dashboards;
- five monitored Linux containers with Telegraf and Fluent Bit;
- automatic index templates, Discover data views, and CPU/RAM anomaly detectors;
- Ollama for local LLM and embedding inference;
- Open WebUI connected to Ollama;
- Qdrant with an automatically created collection for RAG;
- Prosody as the dedicated XMPP server for SPADE.

The application agents are not started from this compose file yet. The infrastructure is ready for them and exposes the internal service names `opensearch`, `ollama`, `qdrant`, and `xmpp` on the shared Docker network.

## Service map

| Service | Internal address | Host address | Purpose |
|---|---|---|---|
| OpenSearch | `http://opensearch:9200` | `http://localhost:9200` | Metrics, logs, anomaly detection |
| OpenSearch Dashboards | `http://opensearch-dashboards:5601` | `http://localhost:5601` | Discover and detector UI |
| Ollama | `http://ollama:11434` | `http://localhost:11434` | Chat and embedding models |
| Open WebUI | `http://open-webui:8080` | `http://localhost:3000` | Browser UI for Ollama |
| Qdrant | `http://qdrant:6333` | `http://localhost:6333` | Vector database for RAG |
| Qdrant gRPC | `qdrant:6334` | `localhost:6334` | Optional gRPC client access |
| Prosody XMPP | `xmpp:5222` | `localhost:5222` | SPADE agent communication |

All published ports are bound to `127.0.0.1`. They are reachable from the local machine but are not intentionally exposed to the LAN.

## Directory layout

```text
src/infrastructure/
├── docker-compose.yml
├── docker-compose.gpu.yml
├── .env.example
├── monitored-machine/
├── ollama/
│   └── init/pull-models.sh
├── opensearch/
│   └── init/
├── opensearch-dashboards/
│   └── init/
├── qdrant/
│   └── init/create-collection.sh
└── xmpp/
    └── prosody.cfg.lua
```

## Configure the environment

From `src/infrastructure`:

```powershell
Copy-Item .env.example .env
```

Before the first start, edit `.env` and replace:

- `OPEN_WEBUI_SECRET_KEY`;
- all `XMPP_*_PASSWORD` values.

The `.env` file is ignored by Git and must not be committed. The supplied `.env.example` contains version pins and safe local defaults.

The default models are:

- chat/reasoning: `llama3.2:3b`;
- embeddings: `nomic-embed-text`;
- Qdrant vector size: `768`.

When the embedding model is changed, update `QDRANT_VECTOR_SIZE` to match its output dimension. Recreate the Qdrant collection or reset the Qdrant volume after changing the dimension.

## Validate the compose configuration

```powershell
docker compose config
```

This expands `.env` and validates the merged Compose model before containers are created.

## Start without GPU passthrough

```powershell
docker compose up -d --build
```

This works on systems where Docker cannot access a GPU. Ollama uses the available CPU.

## Start with an NVIDIA GPU

On Windows with Docker Desktop/WSL2 or on Linux with NVIDIA Container Toolkit:

```powershell
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

The override only adds `gpus: all` to the Ollama service. The base compose remains portable.

## Automatic initialisation

The first complete startup performs these one-shot operations:

1. `opensearch-init` creates index templates.
2. The five monitored machines start Telegraf and Fluent Bit.
3. `opensearch-dashboards-init` creates the ten per-machine data views.
4. `opensearch-detectors-init` creates and starts CPU and memory detectors.
5. `ollama-models-init` downloads the chat and embedding models.
6. `qdrant-init` creates the configured RAG collection if it does not exist.

Successful one-shot containers remain in the `Exited (0)` state. This is expected.

## Check the stack

```powershell
docker compose ps -a
```

Useful logs:

```powershell
docker compose logs opensearch-init
docker compose logs opensearch-dashboards-init
docker compose logs opensearch-detectors-init
docker compose logs ollama
docker compose logs ollama-models-init
docker compose logs qdrant-init
docker compose logs xmpp
```

Expected state:

- OpenSearch, Dashboards, Ollama, Open WebUI, Qdrant, Prosody, and all five monitored machines are running;
- the five initialiser services have exited with code `0`;
- the monitored machines are healthy.

## OpenSearch indexes

Each monitored machine writes to its own daily metric and log indexes:

```text
metrics-machine-01-YYYY.MM.DD
logs-machine-01-YYYY.MM.DD
...
metrics-machine-05-YYYY.MM.DD
logs-machine-05-YYYY.MM.DD
```

Check them with:

```powershell
curl.exe "http://localhost:9200/_cat/indices/metrics-machine-*,logs-machine-*?v&s=index"
```

Open `http://localhost:5601` and use **Discover** to select one of the ten automatically created data views.

## Anomaly detectors

Open **OpenSearch Dashboards > Anomaly detection > Detectors**. The following detectors are created automatically:

- `infrastructure-cpu-usage`, based on `cpu.usage_active`;
- `infrastructure-memory-usage`, based on `mem.used_percent`.

REST verification:

```powershell
curl.exe -X POST "http://localhost:9200/_plugins/_anomaly_detection/detectors/_search?pretty" `
  -H "Content-Type: application/json" `
  -d '{"size":20,"query":{"match_all":{}}}'
```

## Ollama and Open WebUI

List downloaded models:

```powershell
curl.exe "http://localhost:11434/api/tags"
```

Open WebUI is available at `http://localhost:3000`. With the default settings, the first account created in the UI becomes the administrator.

Open WebUI communicates with Ollama through the internal address `http://ollama:11434`. Qdrant is kept separate for the project RAG pipeline; Open WebUI does not automatically use the project collection.

To change models, edit `.env`, remove the completed model initialiser, and run it again:

```powershell
docker compose rm -f ollama-models-init
docker compose up ollama-models-init
```

## Qdrant RAG collection

The default collection is `thesis-knowledge-base`, configured for 768-dimensional cosine vectors.

Check Qdrant:

```powershell
curl.exe "http://localhost:6333/readyz"
curl.exe "http://localhost:6333/collections/thesis-knowledge-base"
```

To recreate only the collection initialiser:

```powershell
docker compose rm -f qdrant-init
docker compose up qdrant-init
```

Deleting or changing an existing collection should be an explicit operation because it removes indexed knowledge.

## Prosody XMPP for SPADE

SPADE requires an XMPP server. This stack uses the official Prosody image with the Docker-internal domain `xmpp`, matching the JIDs already present in:

```text
src/agentic_system/config/agents.yaml
```

Examples:

```text
coordinator@xmpp
evidence@xmpp
reasoning@xmpp
critic@xmpp
remediation@xmpp
```

Prosody enables in-band registration because SPADE starts agents with `auto_register=True`. Registration throttling is disabled only for this isolated local environment. Port `5222` is bound to localhost and server-to-server federation is disabled.

The future agent container must join `monitoring-net` and receive the XMPP password variables from `.env`. It can then reach Prosody at `xmpp:5222`.

Configuration check:

```powershell
docker compose exec xmpp prosodyctl check config
```

Registered users can be inspected from the Prosody container:

```powershell
docker compose exec xmpp prosodyctl shell
```

## Clean reset

A complete reset deletes indexes, vector data, downloaded models, Open WebUI state, XMPP accounts, and detector state:

```powershell
docker compose down -v --remove-orphans
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

Use the second command without `docker-compose.gpu.yml` when GPU passthrough is not required.

A normal restart that preserves all data is:

```powershell
docker compose down
docker compose up -d
```

## Focused troubleshooting

```powershell
docker compose logs -f opensearch
docker compose logs -f machine-01
docker compose logs -f ollama
docker compose logs -f qdrant
docker compose logs -f xmpp
docker compose logs -f open-webui
```

Check internal connectivity:

```powershell
docker compose exec open-webui python -c "import urllib.request; print(urllib.request.urlopen('http://ollama:11434/api/tags').status)"
docker compose exec xmpp prosodyctl check config
```

For NVIDIA passthrough, validate Docker before debugging Ollama:

```powershell
docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu24.04 nvidia-smi
```
