# Agentic System Test Suite

This directory contains the incremental verification suite for the real Agentic System backend.

The integration tests intentionally exercise the running Dockerized system instead of mocking SPADE, Prosody/XMPP, MCP or the LLM runtime. New architectural layers should be added here as they are implemented.

## Current coverage

### TEST D - Multi-Agent SPADE runtime

Implemented in `integration/test_runtime.py`.

The suite verifies that:

- the Agentic System backend reaches `status=ok` and `phase=agents-running`;
- exactly five SPADE agents are configured and running;
- the expected roles are present;
- every role has its own distinct XMPP JID;
- every agent is in the `running` lifecycle state;
- every SPADE lifecycle behaviour continues updating its heartbeat;
- the `/ready` endpoint reports the active runtime;
- the Prosody client-to-server port is reachable.

Because an agent enters the `running` state only from its SPADE `setup()` method after successful XMPP startup, these tests validate the real SPADE/Prosody boundary rather than only checking static configuration.

### TEST E - Inter-agent XMPP communication

Implemented in `integration/test_agent_communication.py`.

On backend startup, the runtime performs a real transport readiness round trip:

```text
Technical Lead Agent
        |
        | REQUEST / runtime_connectivity_probe
        v
System Engineer Agent
        |
        | AGREE / request_acknowledged
        v
Technical Lead Agent
```

The test verifies that:

- the request is sent by `technical-lead@xmpp` to `system-engineer@xmpp`;
- the request uses the Agentic System semantic XMPP protocol;
- the System Engineer receives the real SPADE message and sends an acknowledgement;
- the acknowledgement returns to the Technical Lead;
- REQUEST and AGREE use the same `correlation_id`;
- both agents report bidirectional message activity.

This is intentionally a communication-layer test only. It does not yet perform BDI deliberation, ReAct reasoning, MCP calls or diagnosis.

### Agent health and dashboard presence

Implemented in `integration/test_agent_health.py`.

Every SPADE role exposes an independent observability port:

```text
8101  Technical Lead
8102  System Engineer
8103  Network Engineer
8104  Application Engineer
8105  Software Developer
```

Each port provides:

- `GET /health` for deterministic integration checks;
- `WS /ws/health` for the dashboard real-time presence indicator.

The runtime also performs a repeated XMPP REQUEST/AGREE health probe between the Technical Lead and every specialist. Therefore `communication_ok=true` means that a real inter-agent XMPP round trip has recently succeeded, not only that TCP port 5222 is reachable.

The tests verify that all five health endpoints are distinct, report the expected SPADE/XMPP identity, expose `ONLINE` when communication is verified, and stream the same state through WebSocket.

### Opt-in single-agent XMPP fault test

Implemented in `integration/test_agent_faults.py` and marked `fault`, so it is not included in the normal `-m integration` regression run.

The test deliberately targets only `network-engineer@xmpp` and verifies that:

- the agent starts ONLINE;
- disabling the Prosody account and closing only its c2s session makes its WebSocket report `OFFLINE`;
- `spade_alive` remains true while `xmpp_connected` becomes false, proving that process liveness and XMPP connectivity are different states;
- the other specialist agents remain ONLINE;
- the Technical Lead becomes DEGRADED because one specialist no longer answers the periodic REQUEST/AGREE probe;
- cleanup always re-enables the account, restarts the backend and verifies that the agent returns ONLINE.

Run it explicitly from `src\agentic_system`:

```powershell
python -m pytest -m fault -v
```

## Manual single-agent XMPP fault on Windows

For a deterministic fault that remains visible long enough in the dashboard, use the Network Engineer as an example:

```powershell
docker exec agentic-xmpp prosodyctl shell user disable network-engineer@xmpp
docker exec agentic-xmpp prosodyctl shell c2s close network-engineer@xmpp
```

Inspect its health endpoint:

```powershell
curl.exe http://127.0.0.1:8103/health
```

Restore the environment:

```powershell
docker exec agentic-xmpp prosodyctl shell user enable network-engineer@xmpp
docker restart agentic-system-backend
```

## Planned incremental coverage

The suite will grow together with the backend:

- automatic XMPP reconnection/recovery after a single-agent transport fault;
- TEST F - AgentSpeak(L) / SPADE-BDI integration and isolated BDI state;
- TEST G - ReAct loop and autonomous action/tool selection;
- MCP tool execution and observation feedback;
- autonomous RAG selection versus general LLM knowledge;
- incident delegation from Technical Lead to specialist agents;
- multi-step diagnosis and final evidence-based result.

## Running the normal integration suite on Windows

The Docker infrastructure must already be running and the Agentic System backend must be healthy.

From `src\agentic_system` in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-test.txt
python -m pytest -m integration -v
```

The default test endpoints are:

- Agentic backend: `http://127.0.0.1:8081`
- Prosody/XMPP: `127.0.0.1:5222`
- Per-agent health: `http://127.0.0.1:8101` through `http://127.0.0.1:8105`

They can be overridden when needed:

```powershell
$env:AGENTIC_BACKEND_TEST_URL = "http://127.0.0.1:8081"
$env:AGENTIC_XMPP_TEST_HOST = "127.0.0.1"
$env:AGENTIC_XMPP_TEST_PORT = "5222"
python -m pytest -m integration -v
```

## Test organization

```text
tests/
├── README.md
└── integration/
    ├── conftest.py
    ├── test_runtime.py
    ├── test_agent_communication.py
    ├── test_agent_health.py
    └── test_agent_faults.py
```

Future integration tests should remain grouped by architectural concern instead of creating one large end-to-end test file.
