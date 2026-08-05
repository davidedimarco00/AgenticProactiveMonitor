# Collaborative Troubleshooting MAS

This module implements the SPADE-based collaborative troubleshooting layer of AgenticProactiveMonitor.

## Current foundation

- Shared incident workspace and typed evidence model
- SPADE message protocol
- Incident coordinator
- Metrics, logs, hypothesis and critic agents
- Iterative investigation loop
- Deterministic repositories separated from LLM reasoning

## Run

1. Start an XMPP server and create the configured accounts.
2. Copy `config/agents.example.yaml` to `config/agents.yaml`.
3. Install dependencies with `pip install -r requirements.txt`.
4. Run `python -m src.agentic_system.main` from the repository root.

The next implementation step will connect the evidence agents to OpenSearch queries, automatic detector creation, topology discovery and safe diagnostic tools.
