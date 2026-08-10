# Agentic System Operator Dashboard

Flask-based operator interface for the thesis prototype. The dashboard is intentionally separated from the agentic control loop: it observes and presents incident state, while the SPADE agents remain responsible for autonomous investigation.

## What the operator can inspect

- incidents taken in charge and their current lifecycle state;
- explicit rationale for starting an investigation;
- OpenSearch Anomaly Detection signal and confidence;
- observable ReAct trajectory: actions/tool calls, observations and concise decision rationales;
- final diagnosis, supporting evidence and diagnosis confidence;
- remediation recommendation, verification steps and operational risks;
- live health of OpenSearch, Qdrant, MCP Server, Prosody/XMPP and Ollama.

The interface does **not** store or display raw private model chain-of-thought. Only operationally useful actions, observations and concise decision rationales are persisted.

## Container

The service is defined in `src/infrastructure/docker-compose.yml` as `agentic-system-dashboard` and is bound to localhost on port `5050` by default.

From Windows PowerShell:

```powershell
cd .\src\infrastructure
Copy-Item .env.example .env -ErrorAction SilentlyContinue
docker compose up -d --build agentic-system-dashboard
```

Open:

```text
http://127.0.0.1:5050
```

Check container status:

```powershell
docker compose ps agentic-system-dashboard
docker compose logs -f agentic-system-dashboard
```

## Incident persistence

Incident records are stored in OpenSearch using monthly indices:

```text
agentic-incidents-YYYY.MM
```

The dashboard automatically ensures the `agentic-dashboard-incidents` index template when it starts.

The agentic runtime can later write directly to the same OpenSearch indices. For integration and manual testing, the dashboard also exposes a small local REST interface:

- `GET /api/incidents`
- `POST /api/incidents`
- `GET /api/incidents/<incident_id>`
- `PATCH /api/incidents/<incident_id>`
- `GET /api/overview`
- `GET /api/system-health`

## Load the thesis demo incident

A complete example is available in `examples/demo-incident.json`. From `src/infrastructure` in PowerShell:

```powershell
$body = Get-Content ..\agentic_dashboard\examples\demo-incident.json -Raw
Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:5050/api/incidents" `
  -ContentType "application/json" `
  -Body $body
```

The generated incident can then be opened from the dashboard and used to verify the final visual layout before real agent integration.

## Current autonomy boundary

The thesis baseline follows a human-in-the-loop model:

```text
OpenSearch anomaly
      -> agent takes incident in charge
      -> ReAct investigation
         Reason -> Act/tool -> Observe -> update -> repeat
      -> explainable diagnosis
      -> remediation recommendation
      -> operator decision
```

Automatic remediation execution is intentionally not part of this dashboard. A future `ExecutionAgent` can be added later without changing the operator-facing incident model.
