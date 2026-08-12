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
    subprocess.run(
        ["docker", "restart", BACKEND_CONTAINER],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.fault
def test_single_agent_xmpp_disconnect_is_detected_in_real_time_and_isolated() -> None:
    """Break one real XMPP session and verify per-agent failure observability.

    This test intentionally changes Prosody runtime state. It disables only the
    Network Engineer account, closes its current c2s session and then verifies
    that the agent's WebSocket reports OFFLINE while the other specialist agents
    remain ONLINE. The Technical Lead is expected to become DEGRADED because its
    periodic team communication probe can no longer reach one specialist.

    The account is always re-enabled in a finally block. If the SPADE client does
    not automatically reconnect after re-enabling, the backend container is
    restarted only as cleanup so the local development environment is not left
    broken after the test.
    """

    initial = _get_health(TARGET_PORT)
    assert initial["role"] == TARGET_ROLE
    assert initial["status"] == "ONLINE"
    assert initial["xmpp_connected"] is True
    assert initial["communication_ok"] is True

    recovered_without_restart = False

    with connect(
        f"ws://127.0.0.1:{TARGET_PORT}/ws/health",
        open_timeout=3,
        close_timeout=2,
    ) as websocket:
        first_payload = json.loads(websocket.recv(timeout=3))
        assert first_payload["status"] == "ONLINE"

        try:
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

            # The coordinator itself is still connected, but its team-level
            # REQUEST/AGREE health cycle can no longer reach all specialists.
            degraded_lead = _wait_for_health(
                TECHNICAL_LEAD_PORT,
                lambda item: item.get("status") == "DEGRADED",
                timeout=12,
            )
            assert degraded_lead["xmpp_connected"] is True
            assert degraded_lead["communication_ok"] is False

        finally:
            _prosody_shell("user", "enable", TARGET_JID)

            try:
                recovered = _wait_for_health(
                    TARGET_PORT,
                    lambda item: item.get("status") == "ONLINE",
                    timeout=20,
                )
                recovered_without_restart = True
                assert recovered["xmpp_connected"] is True
                assert recovered["communication_ok"] is True
            except AssertionError:
                # Keep the developer environment usable even if the client does
                # not reconnect automatically after the deliberately failed auth.
                _restart_backend_for_cleanup()
                _wait_for_health(
                    TARGET_PORT,
                    lambda item: item.get("status") == "ONLINE",
                    timeout=35,
                )

    assert recovered_without_restart, (
        "The failure was detected correctly, but the SPADE client did not recover "
        "its XMPP session automatically after the Prosody account was re-enabled"
    )
