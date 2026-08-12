from __future__ import annotations

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
    deadline = time.monotonic() + timeout_seconds
    last_error: BaseException | None = None

    while time.monotonic() < deadline:
        try:
            payload = get_json("/health")
            if payload.get("status") == "ok" and payload.get("phase") == "agents-running":
                return payload
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc

        time.sleep(0.5)

    detail = f" Last error: {last_error}" if last_error else ""
    pytest.fail(
        "Agentic backend did not reach status=ok and phase=agents-running "
        f"within {timeout_seconds:.1f}s.{detail}"
    )


@pytest.fixture(scope="session")
def backend_health() -> dict[str, Any]:
    return wait_for_backend_ready()


@pytest.fixture
def backend_get_json() -> Callable[[str], dict[str, Any]]:
    return get_json
