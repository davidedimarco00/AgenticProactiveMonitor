from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path


_ALLOWED_ROLES = {
    "system_engineer",
    "network_engineer",
    "application_engineer",
    "software_developer",
}
_ALLOWED_DOMAINS = {"system", "network", "application", "software"}


@dataclass(frozen=True, slots=True)
class BDIDeliberation:
    incident_id: str
    goal: str
    intention: str
    primary_investigator: str


class JasonBDIGateway:
    """Async bridge to the real Jason AgentSpeak(L) interpreter.

    Jason is executed outside Python. The subprocess performs the AgentSpeak
    reasoning cycles and returns only the commitment selected by the BDI engine.
    """

    def __init__(
        self,
        *,
        command: str,
        technical_lead_asl: str,
        timeout_seconds: float = 10.0,
        max_concurrency: int = 2,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if max_concurrency <= 0:
            raise ValueError("max_concurrency must be greater than zero")

        self.command = command
        self.technical_lead_asl = technical_lead_asl
        self.timeout_seconds = timeout_seconds
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def select_primary_investigator(
        self,
        *,
        incident_id: str,
        probable_domain: str,
        recommended_agent: str,
        available_agents: list[str],
    ) -> BDIDeliberation:
        domain = probable_domain.strip().lower()
        recommendation = recommended_agent.strip().lower()
        available = [role.strip().lower() for role in available_agents]

        if domain not in _ALLOWED_DOMAINS:
            raise ValueError(f"Unsupported triage domain: {probable_domain!r}")
        if recommendation not in _ALLOWED_ROLES:
            raise ValueError(f"Unsupported specialist role: {recommended_agent!r}")
        if recommendation not in available:
            raise RuntimeError(
                f"Recommended specialist {recommendation!r} is not currently available"
            )
        if any(role not in _ALLOWED_ROLES for role in available):
            raise ValueError("available_agents contains an unsupported specialist role")
        if not Path(self.technical_lead_asl).is_file():
            raise RuntimeError(
                f"Technical Lead AgentSpeak source not found: {self.technical_lead_asl}"
            )

        async with self._semaphore:
            process = await asyncio.create_subprocess_exec(
                self.command,
                self.technical_lead_asl,
                incident_id,
                domain,
                recommendation,
                ",".join(available),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.timeout_seconds,
                )
            except TimeoutError:
                process.kill()
                await process.wait()
                raise RuntimeError("Jason BDI deliberation timed out") from None

        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"Jason BDI deliberation failed with code {process.returncode}: {detail}"
            )

        line = stdout.decode("utf-8", errors="replace").strip().splitlines()
        if not line:
            raise RuntimeError("Jason BDI deliberation returned no decision")

        parts = line[-1].split("\t")
        if len(parts) != 5 or parts[0] != "OK":
            raise RuntimeError(f"Unexpected Jason BDI response: {line[-1]!r}")

        _, returned_incident, goal, intention, primary = parts
        if returned_incident != incident_id:
            raise RuntimeError("Jason BDI response incident_id does not match request")
        if primary != recommendation:
            raise RuntimeError(
                "Jason BDI selected a specialist different from the triage recommendation"
            )

        return BDIDeliberation(
            incident_id=returned_incident,
            goal=goal,
            intention=intention,
            primary_investigator=primary,
        )
