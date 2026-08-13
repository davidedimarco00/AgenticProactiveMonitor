# Agentic backend tests

The backend test suite is split by responsibility:

- `unit/`: isolated project logic, without Docker services.
- `integration/`: real communication between SPADE agents and the running infrastructure (XMPP, SPADE-LLM and MCP capabilities).
- `e2e/`: Gherkin acceptance scenarios executed with `pytest-bdd`.

Install the backend and test dependencies from PowerShell:

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

## Live Gemma/Qwen/MCP routing test

The normal integration suite verifies the configured model roles and MCP tool discovery without forcing an expensive LLM inference. The live routing test is therefore opt-in. It uses real Ollama inference and real SPADE-LLM MCP tools to verify this path:

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
