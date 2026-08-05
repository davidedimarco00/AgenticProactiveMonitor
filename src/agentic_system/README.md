# Five-Agent Collaborative Troubleshooting System

The active runtime is `src.agentic_system.main`. It starts five SPADE agents:

1. Coordinator
2. Evidence
3. Reasoning
4. Critic
5. Remediation

## Docker startup

From `src/infrastructure` run:

```bash
docker compose up -d --build
```

The Compose stack starts OpenSearch, Ollama, the model initializer, Prosody XMPP, the monitored machines, and finally `agentic-system`.

Follow the MAS startup and investigation with:

```bash
docker compose logs -f agentic-system
```

Check the relevant services with:

```bash
docker compose ps opensearch ollama ollama-init xmpp agentic-system
```

The agent container is healthy when `/tmp/agentic-system.ready` exists. SPADE accounts are created automatically through Prosody in-band registration. This registration setting is intended only for the local thesis lab.

## Runtime sequence

1. Validate that the configured Ollama model is available.
2. Create OpenSearch repositories.
3. Synchronise anomaly detectors when enabled.
4. Load the infrastructure topology.
5. Create and connect the five SPADE agents to XMPP.
6. Mark the runtime as ready.
7. Open the demo incident when `open_demo_incident` is enabled.
8. Execute the Evidence -> Reasoning -> Critic loop.
9. Send an accepted diagnosis to the policy-gated Remediation Agent.

The current remediation executor is intentionally disabled. The active incident source is still the demo event until the Perception component is connected to real OpenSearch anomaly results.
