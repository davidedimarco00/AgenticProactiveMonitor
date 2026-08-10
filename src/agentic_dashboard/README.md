# Agentic System Operator Dashboard

Flask-based operator interface for the thesis prototype. The dashboard is intentionally separated from the agentic control loop: it observes and presents incident and agent activity state, while the SPADE agents remain responsible for autonomous investigation.

## What the operator can inspect

- incidents taken in charge and their current lifecycle state;
- explicit rationale for starting an investigation;
- OpenSearch Anomaly Detection signal and confidence;
- final diagnosis, supporting evidence and diagnosis confidence;
- remediation recommendation, verification steps and operational risks;
- live health of OpenSearch, Qdrant, MCP Server, Prosody/XMPP and Ollama;
- the specialised agent network and current `WORKING` / `IDLE` state;
- per-agent operational activity: timestamp, action, caller, reason, tool/outcome and incident.

The interface does **not** store or display raw private model chain-of-thought. Agent observability records only expose concise operational rationale and externally observable actions useful for audit and troubleshooting.

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

## OpenSearch persistence

Incident records are stored using monthly indices:

```text
agentic-incidents-YYYY.MM
```

Agent activity records are stored separately:

```text
agentic-agent-events-YYYY.MM
```

The dashboard ensures both index templates at startup. Keeping agent events separate from incident documents makes the observability stream independently searchable and suitable for later experimental analysis.

## REST interface

Incident endpoints:

- `GET /api/incidents`
- `POST /api/incidents`
- `GET /api/incidents/<incident_id>`
- `PATCH /api/incidents/<incident_id>`
- `GET /api/overview`
- `GET /api/system-health`

Agent observability endpoints:

- `GET /api/agent-events?agent_jid=reasoning@xmpp`
- `POST /api/agent-events`
- `GET /api/agents/<agent_jid>/activity`

Each agent can publish a structured event whenever it is invoked, selects a tool, receives a delegated task, completes an operation or hands control to another agent. Example payload:

```json
{
  "agent_jid": "evidence@xmpp",
  "timestamp": "2026-08-10T12:32:12+00:00",
  "action": "Validate runtime CPU saturation",
  "called_by": "coordinator@xmpp",
  "reason": "The OpenSearch anomaly must be confirmed against live telemetry.",
  "incident_id": "INC-001",
  "tool": "get_runtime_stats",
  "status": "COMPLETED",
  "outcome": "CPU saturation confirmed."
}
```

The `reason` field is an operational explanation for the action, not raw hidden reasoning.

## Load the thesis demo incident

`examples/demo-incident.json` includes both the incident and a complete multi-agent activity trace. From `src/infrastructure` in PowerShell:

```powershell
$body = Get-Content "..\agentic_dashboard\examples\demo-incident.json" -Raw
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:5050/api/incidents" -ContentType "application/json; charset=utf-8" -Body $body
```

The incident POST also persists the embedded `agent_events` into the dedicated agent-event OpenSearch index. After loading the demo, select any agent in the network to inspect its activity window.

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

Automatic remediation execution is intentionally not part of this dashboard. A future `ExecutionAgent` can be added later without changing the operator-facing incident or observability model.
