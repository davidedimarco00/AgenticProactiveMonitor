# Agentic System Operator Dashboard

Flask-based operator interface for the thesis prototype. The dashboard is intentionally separated from the agentic control loop: it observes and presents incident and agent activity state, while the SPADE agents remain responsible for autonomous investigation.

## Virtual operations team

The operator-facing dashboard presents the multi-agent system as a realistic IT technical team instead of exposing implementation-oriented names such as `EvidenceAgent` or `ReasoningAgent`.

The current dashboard roles are:

- **Technical Lead** — incident triage, coordination and critical review;
- **System Engineer** — Linux, containers, host resources and runtime diagnostics;
- **Network Engineer** — connectivity, latency, network paths and traffic analysis;
- **Application Engineer** — service health, application logs and dependency diagnosis;
- **Software Developer** — code behaviour, defects and application-level corrective guidance.

The visual model is deliberately role-oriented because this is the abstraction the operator should understand. The current prototype runtime still uses the legacy XMPP identities `coordinator@xmpp`, `evidence@xmpp`, `critic@xmpp`, `reasoning@xmpp` and `remediation@xmpp`. The dashboard contains a temporary compatibility mapping so that the UI can already use the target enterprise roles without changing the agent runtime in this branch. When the agentic system is refactored to the new roles, this compatibility layer can be removed.

## What the operator can inspect

- incidents taken in charge and their current lifecycle state;
- explicit rationale for starting an investigation;
- OpenSearch Anomaly Detection signal and confidence;
- final diagnosis, supporting evidence and diagnosis confidence;
- remediation recommendation, verification steps and operational risks;
- live health of OpenSearch, Qdrant, MCP Server, Prosody/XMPP and Ollama;
- the virtual technical team and current `WORKING` / `IDLE` state;
- per-role operational activity: timestamp, action, caller, reason, tool/outcome and incident.

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

- `GET /api/agent-events?agent_jid=<runtime-jid>`
- `POST /api/agent-events`
- `GET /api/agents/<agent_jid>/activity`

Each agent can publish a structured event whenever it is invoked, selects a tool, receives a delegated task, completes an operation or hands control to another agent. Example payload using the current compatibility identity for the System Engineer:

```json
{
  "agent_jid": "evidence@xmpp",
  "agent_name": "System Engineer",
  "timestamp": "2026-08-10T12:32:12+00:00",
  "action": "Validate runtime CPU saturation",
  "called_by": "coordinator@xmpp",
  "reason": "The Technical Lead requires host and container evidence before involving application specialists.",
  "incident_id": "INC-001",
  "tool": "get_runtime_stats",
  "status": "COMPLETED",
  "outcome": "CPU saturation confirmed."
}
```

The `reason` field is an operational explanation for the action, not raw hidden reasoning.

## Load the thesis demo incident

`examples/demo-incident.json` includes both the incident and a complete cross-role activity trace. From `src/infrastructure` in PowerShell:

```powershell
$body = Get-Content "..\agentic_dashboard\examples\demo-incident.json" -Raw
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:5050/api/incidents" -ContentType "application/json; charset=utf-8" -Body $body
```

The incident POST also persists the embedded `agent_events` into the dedicated agent-event OpenSearch index. After loading the demo, select Technical Lead, System Engineer, Network Engineer, Application Engineer or Software Developer in the team graph to inspect the corresponding activity window.

## Current autonomy boundary

The dashboard reflects a human-in-the-loop operating model:

```text
OpenSearch anomaly
      -> Technical Lead triage
      -> specialist investigation / consultation
      -> cross-role evidence collection
      -> Technical Lead critical review
      -> explainable diagnosis
      -> remediation recommendation
      -> operator decision
```

The exact specialist path is not meant to be a fixed pipeline. In the final agentic runtime, specialists can be involved dynamically according to the anomaly and the evidence collected during the investigation.
