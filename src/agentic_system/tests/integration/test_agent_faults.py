from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Callable
from urllib.error import URLError
from urllib.request import urlopen

import pytest
from websockets.sync.client import connect


PROSODY_CONTAINER = os.getenv("AGENTIC_XMPP_CONTAINER", "agentic-xmpp")
BACKEND_CONTAINER = os.getenv("AGENTIC_BACKEND_CONTAINER", "agentic-system-backend")
TARGET_ROLE = "network_engineer"
TARGET_JID = "network-engineer@xmpp"
TARGET_PORT = 8103
TECHNICAL_LEAD_PORT = 8101
UNAFFECTED_SPECIALIST_PORTS = (8102, 8104, 8105)


def _get_health(port: int) -> dict[str, object]:
    with urlopen(  # noqa: S310 - localhost integration endpoint
        f"http://127.0.0.1:{port}/health",
        timeout=3,
    ) as response:
        payload = json.loads(response.read().decode("utf-8"))

    if not isinstance(payload, dict):
        raise AssertionError(f"Expected JSON object from agent health port {port}")
    return payload


def _wait_for_health(
    port: int,
    predicate: Callable[[dict[str, object]], bool],
    *,
    timeout: float = 15.0,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    last_payload: dict[str, object] | None = None
    last_error: BaseException | None = None

    while time.monotonic() < deadline:
        try:
            last_payload = _get_health(port)
            if predicate(last_payload):
                return last_payload
        except (URLError, TimeoutError, OSError) as exc:
            last_error = exc
        time.sleep(0.5)

    raise AssertionError(
        f"Health condition was not reached on port {port}; "
        f"last_payload={last_payload!r}, last_error={last_error!r}"
    )


def _prosody_shell(section: str, command: str, jid: str) -> None:
    completed = subprocess.run(
        [
            "docker",
            "exec",
            PROSODY_CONTAINER,
            "prosodyctl",
            "shell",
            section,
            command,
            jid,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "Prosody shell command failed: "
            f"{section} {command} {jid}\n"
            f"stdout={completed.stdout}\n"
            f"stderr={completed.stderr}"
        )


def _restart_backend_for_cleanup() -> None:
    completed = subprocess.run(
        ["docker", "restart", BACKEND_CONTAINER],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "Could not restart agentic backend during fault-test cleanup\n"
            f"stdout={completed.stdout}\n"
            f"stderr={completed.stderr}"
        )


@pytest.mark.fault
def test_single_agent_xmpp_disconnect_is_detected_in_real_time_and_isolated() -> None:
    """Break one real XMPP session and verify per-agent failure observability.

    This opt-in fault test intentionally changes Prosody runtime state. It
    disables only the Network Engineer account, closes its current c2s session
    and verifies that the same agent's WebSocket reports OFFLINE while the other
    specialist agents remain ONLINE. The Technical Lead becomes DEGRADED because
    its periodic team communication probe can no longer reach one specialist.

    Cleanup is deterministic: the account is re-enabled and the backend is
    restarted after the assertions. Automatic XMPP reconnection is deliberately
    left for a separate recovery test when that behaviour is implemented.
    """

    initial = _get_health(TARGET_PORT)
    assert initial["role"] == TARGET_ROLE
    assert initial["status"] == "ONLINE"
    assert initial["xmpp_connected"] is True
    assert initial["communication_ok"] is True

    try:
        with connect(
            f"ws://127.0.0.1:{TARGET_PORT}/ws/health",
            open_timeout=3,
            close_timeout=2,
        ) as websocket:
            first_payload = json.loads(websocket.recv(timeout=3))
            assert first_payload["status"] == "ONLINE"

            # Prevent immediate re-authentication, then terminate exactly the
            # target user's current Prosody client-to-server session.
            _prosody_shell("user", "disable", TARGET_JID)
            _prosody_shell("c2s", "close", TARGET_JID)

            offline_payload: dict[str, object] | None = None
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                payload = json.loads(websocket.recv(timeout=3))
                if payload.get("status") == "OFFLINE":
                    offline_payload = payload
                    break

            assert offline_payload is not None, (
                "The per-agent WebSocket never reported OFFLINE after Prosody "
                "closed the Network Engineer XMPP session"
            )
            assert offline_payload["spade_alive"] is True
            assert offline_payload["xmpp_connected"] is False
            assert offline_payload["communication_ok"] is False
            assert offline_payload["last_xmpp_disconnected_at"]

            # Other specialist sessions are independent and must stay healthy.
            for port in UNAFFECTED_SPECIALIST_PORTS:
                healthy = _wait_for_health(
                    port,
                    lambda item: item.get("status") == "ONLINE",
                    timeout=8,
                )
                assert healthy["xmpp_connected"] is True
                assert healthy["communication_ok"] is True

            # The Technical Lead remains connected to XMPP but correctly reports
            # degraded team communication because one specialist is unreachable.
            degraded_lead = _wait_for_health(
                TECHNICAL_LEAD_PORT,
                lambda item: item.get("status") == "DEGRADED",
                timeout=12,
            )
            assert degraded_lead["xmpp_connected"] is True
            assert degraded_lead["communication_ok"] is False

    finally:
        # Never leave the local thesis environment with a disabled agent account.
        _prosody_shell("user", "enable", TARGET_JID)
        _restart_backend_for_cleanup()
        restored = _wait_for_health(
            TARGET_PORT,
            lambda item: item.get("status") == "ONLINE",
            timeout=35,
        )
        assert restored["xmpp_connected"] is True
        assert restored["communication_ok"] is True
