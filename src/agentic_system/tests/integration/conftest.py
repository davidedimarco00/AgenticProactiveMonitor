from __future__ import annotations

from http.client import RemoteDisconnected
import json
import os
import time
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import pytest


DEFAULT_BACKEND_URL = "http://127.0.0.1:8081"
DEFAULT_STARTUP_TIMEOUT_SECONDS = 30.0


def _backend_url() -> str:
    return os.getenv("AGENTIC_BACKEND_TEST_URL", DEFAULT_BACKEND_URL).rstrip("/")


def get_json(path: str, *, timeout: float = 3.0) -> dict[str, Any]:
    url = f"{_backend_url()}{path}"
    with urlopen(url, timeout=timeout) as response:  # noqa: S310 - local integration endpoint
        payload = json.loads(response.read().decode("utf-8"))

    if not isinstance(payload, dict):
        raise AssertionError(f"Expected JSON object from {url}, got {type(payload)!r}")

    return payload


def wait_for_backend_ready(
    *, timeout_seconds: float = DEFAULT_STARTUP_TIMEOUT_SECONDS
) -> dict[str, Any]:
    """Wait through Docker restart/startup transients until the backend is ready.

    During a container rebuild/recreate the host port can briefly accept a TCP
    connection and close it before an HTTP status line is returned. Treat that
    exactly like connection-refused/503 while the readiness deadline is active;
    otherwise one transient RemoteDisconnected aborts the whole integration
    session and hides the real backend startup result.
    """

    deadline = time.monotonic() + timeout_seconds
    last_error: BaseException | None = None

    while time.monotonic() < deadline:
        try:
            payload = get_json("/health")
            if payload.get("status") == "ok" and payload.get("phase") == "agents-running":
                return payload
        except (
            HTTPError,
            URLError,
            RemoteDisconnected,
            ConnectionResetError,
            ConnectionAbortedError,
            TimeoutError,
            json.JSONDecodeError,
        ) as exc:
            last_error = exc

        time.sleep(0.5)

    detail = f" Last error: {type(last_error).__name__}: {last_error}" if last_error else ""
    pytest.fail(
        "Agentic backend did not reach status=ok and phase=agents-running "
        f"within {timeout_seconds:.1f}s.{detail}"
    )


def _assert_test_e_capabilities(payload: dict[str, Any]) -> None:
    probe = payload.get("communication_probe")
    agents = payload.get("agents")

    missing: list[str] = []
    if not isinstance(probe, dict):
        missing.append("communication_probe")

    if isinstance(agents, list):
        by_role = {
            agent.get("role"): agent
            for agent in agents
            if isinstance(agent, dict)
        }
        for role in ("technical_lead", "system_engineer"):
            agent = by_role.get(role)
            if not isinstance(agent, dict):
                missing.append(f"agents[{role}]")
                continue
            for field in ("messages_sent", "messages_received", "last_message_at"):
                if field not in agent:
                    missing.append(f"agents[{role}].{field}")
    else:
        missing.append("agents")

    if missing:
        pytest.fail(
            "Running backend does not expose TEST E communication capabilities. "
            "The Docker container is probably using an older agentic-backend image. "
            "Rebuild and recreate it with: "
            "docker compose build agentic-backend ; "
            "docker compose up -d --force-recreate agentic-backend. "
            f"Missing fields: {', '.join(missing)}"
        )


@pytest.fixture(scope="session")
def backend_health() -> dict[str, Any]:
    return wait_for_backend_ready()


@pytest.fixture(scope="session")
def backend_communication_health(
    backend_health: dict[str, Any],
) -> dict[str, Any]:
    _assert_test_e_capabilities(backend_health)
    return backend_health


@pytest.fixture
def backend_get_json() -> Callable[[str], dict[str, Any]]:
    return get_json
