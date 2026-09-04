# Agentic backend tests

The backend test suite is split by responsibility:

- `unit/`: isolated project logic, without Docker services;
- `integration/`: real communication between the backend and the running infrastructure, including XMPP, MCP, MongoDB and the FastAPI incident API;
- `e2e/`: Gherkin acceptance scenarios executed with `pytest-bdd`.

Install the backend and test dependencies from Windows PowerShell:

```powershell
cd src\agentic_system
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
```

Run the three levels separately:

```powershell
python -m pytest tests\unit
python -m pytest tests\integration -m integration
python -m pytest tests\e2e -m e2e
```

Integration and end-to-end tests require the Docker infrastructure and the configured Ollama models to be running.

## Incident API and MongoDB integration

`tests/integration/test_incident_api_mongodb.py` verifies the persistence and read path:

```text
IncidentRepository -> MongoDB -> public FastAPI GET -> PDF report
```

The autonomous backend writes incidents and structured events directly through its MongoDB repository. There are no HTTP write endpoints for the operator and no hidden HTTP endpoint is required for persistence.

The integration test also verifies that raw metric, baseline and log payloads are excluded from MongoDB incident records and cleans up the temporary incident when it finishes.

Defaults:

```text
AGENTIC_API_TEST_URL=http://127.0.0.1:8082
MONGODB_TEST_URI=mongodb://agentic:change-this-local-password@127.0.0.1:27017/agentic_monitor?authSource=admin
MONGODB_TEST_DATABASE=agentic_monitor
```

If local `.env` credentials differ, override `MONGODB_TEST_URI` before running the integration suite.

The Gherkin `operator_api.feature` verifies the acceptance constraint that the operator-facing Swagger contract is read-only and that the PDF report endpoint is published.

## Live Gemma/Qwen/MCP routing test

The normal integration suite verifies the configured model roles and MCP tool discovery without forcing an expensive LLM inference. The live routing test is opt-in. It uses real Ollama inference and real SPADE-LLM MCP tools to verify this path:

`Gemma reasoning -> Qwen tool selection -> SPADE-LLM MCP tool execution`.

From PowerShell:

```powershell
$env:RUN_LIVE_MODEL_ROUTING = "1"
python -m pytest tests\integration\test_live_model_routing.py -v
```

From Command Prompt:

```cmd
set RUN_LIVE_MODEL_ROUTING=1
python -m pytest tests\integration\test_live_model_routing.py -v
```

The defaults expect Ollama on `http://127.0.0.1:11434` and MCP on `http://127.0.0.1:8000/mcp`. They can be overridden with `LIVE_OLLAMA_URL` and `LIVE_MCP_URL`.
