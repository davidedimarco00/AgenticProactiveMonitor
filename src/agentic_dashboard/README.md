# Agentic System Operator Dashboard

Flask-based operator interface for the thesis prototype. The dashboard is intentionally separated from the agentic control loop: it observes incidents and structured agent activity, while the SPADE/SPADE-LLM backend remains responsible for autonomous investigation.

## Autonomy boundary

The operator does not start, steer or invoke an investigation from the dashboard.

```text
OpenSearch single-entity anomaly
        -> autonomous agentic backend
        -> diagnosis / remediation / validation
        -> MongoDB incident history
        -> FastAPI read API
        -> operator dashboard
```

Raw metrics and logs are intentionally not copied into the dashboard. Expert operators can inspect them directly in OpenSearch Dashboards when deeper observability analysis is required.

## Virtual operations team

The operator-facing roles are:

- **Technical Lead** — incident triage, coordination and critical review;
- **System Engineer** — Linux, containers, host resources and runtime diagnostics;
- **Network Engineer** — connectivity, latency, network paths and traffic analysis;
- **Application Engineer** — service health, application behaviour and dependency diagnosis;
- **Software Developer** — code behaviour, defects and application-level corrective guidance.

The runtime XMPP identities are `technical-lead@xmpp`, `system-engineer@xmpp`, `network-engineer@xmpp`, `application-engineer@xmpp` and `software-developer@xmpp`.

## What the operator can inspect

- incidents and their lifecycle state;
- affected component and anomaly type;
- diagnosis, possible root cause and diagnosis confidence;
- remediation recommendation, verification guidance and operational risks;
- validation and final incident outcome;
- structured agent activity timeline;
- live health of the backend API, MongoDB, OpenSearch, Qdrant, MCP Server, Prosody/XMPP and Ollama;
- per-role `WORKING` / `IDLE` activity.

The interface never stores or displays private model chain-of-thought. It exposes only concise operational events and conclusions useful for audit and troubleshooting.

## Data source

The dashboard does not persist incidents itself. It consumes the FastAPI backend:

```text
http://agentic-backend:8082/api/v1/...
```

MongoDB is the dedicated persistence layer for incidents and history. OpenSearch remains dedicated to metrics, logs and anomaly detection; Qdrant remains dedicated to RAG knowledge.

## PDF reports

Each incident detail page includes **Download PDF report**. The dashboard proxies the backend endpoint:

```text
GET /api/v1/incidents/{incident_id}/report
```

The generated report contains incident metadata, anomaly summary, diagnosis, root cause, remediation, validation and the structured agent activity timeline. It does not duplicate raw OpenSearch metrics or logs.

## Run with Docker Compose

From Windows PowerShell:

```powershell
cd .\src\infrastructure
Copy-Item .env.example .env -ErrorAction SilentlyContinue
docker compose up -d --build mongodb agentic-backend agentic-system-dashboard
```

Open the operator dashboard:

```text
http://127.0.0.1:5050
```

Open the backend Swagger UI:

```text
http://127.0.0.1:8082/docs
```

Check the services:

```powershell
docker compose ps mongodb agentic-backend agentic-system-dashboard
docker compose logs -f agentic-backend
docker compose logs -f agentic-system-dashboard
```

## Public API contract

The Swagger contract exposed to the operator is read-only:

- `GET /health`
- `GET /ready`
- `GET /api/v1/system/status`
- `GET /api/v1/overview`
- `GET /api/v1/incidents`
- `GET /api/v1/incidents/{incident_id}`
- `GET /api/v1/incidents/{incident_id}/timeline`
- `GET /api/v1/incidents/{incident_id}/report`
- `GET /api/v1/agents`
- `GET /api/v1/agent-events`

Internal persistence writes used by the autonomous backend are deliberately excluded from Swagger and are not used by the dashboard.
