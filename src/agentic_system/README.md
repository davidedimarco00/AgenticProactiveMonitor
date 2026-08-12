# Five-Agent Collaborative Troubleshooting System

The active runtime is `src.agentic_system.main`. It starts five SPADE agents:

1. Coordinator
2. Evidence
3. Reasoning
4. Critic
5. Remediation

## Architectural boundary

The agentic system and the software under observation are separate environments.

```text
agentic-proactive-monitor-infrastructure
        |
        | metrics / logs / diagnostics
        v
monitored-system
```

The monitored software runs from `src/monitored_system/docker-compose.yml` and contains:

- `traffic-generator`
- `api-gateway`
- `processing-service`
- `data-service`
- `worker-service`

The agentic infrastructure contains OpenSearch, Ollama, Qdrant, XMPP, MCP and the supporting services. It does not own the monitored application containers.

## Startup

Start the agentic infrastructure:

```powershell
cd src\infrastructure
docker compose up -d --build
```

Start the monitored system independently:

```powershell
cd ..\monitored_system
docker compose up -d --build
```

The two Compose projects share the `agentic-monitoring-net` network only for observability and diagnostic integration.

## Runtime sequence

1. Validate that the configured Ollama model is available.
2. Create OpenSearch repositories.
3. Synchronise anomaly detectors when enabled.
4. Load the topology of the external monitored system.
5. Create and connect the five SPADE agents to XMPP.
6. Mark the runtime as ready.
7. Open the demo incident when `open_demo_incident` is enabled.
8. Execute the Evidence -> Reasoning -> Critic loop.
9. Send an accepted diagnosis to the policy-gated Remediation Agent.

The demo incident currently targets `processing-service`.

The current remediation executor remains intentionally disabled in the simplified baseline. The active incident source is still the demo event until the Perception component is connected to real OpenSearch anomaly results.
