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
    primary_investigator: str | None = None


class JasonBDIGateway:
    """Async bridge to the real Jason AgentSpeak(L) interpreter.

    Jason is executed outside Python. The subprocess performs AgentSpeak
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

    async def begin_triage(
        self,
        *,
        incident_id: str,
        available_agents: list[str],
    ) -> BDIDeliberation:
        """Let AgentSpeak adopt manage_incident -> triage_incident before LLM reasoning."""

        available = self._validate_available_agents(available_agents)
        deliberation = await self._deliberate(
            mode="begin-triage",
            incident_id=incident_id,
            probable_domain="none",
            recommended_agent="none",
            available_agents=available,
        )
        if deliberation.goal != "manage_incident" or deliberation.intention != "triage_incident":
            raise RuntimeError(
                "Jason BDI did not commit to the expected Technical Lead triage intention"
            )
        if deliberation.primary_investigator is not None:
            raise RuntimeError("Jason BDI selected a specialist before triage was completed")
        return deliberation

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
        available = self._validate_available_agents(available_agents)

        if domain not in _ALLOWED_DOMAINS:
            raise ValueError(f"Unsupported triage domain: {probable_domain!r}")
        if recommendation not in _ALLOWED_ROLES:
            raise ValueError(f"Unsupported specialist role: {recommended_agent!r}")
        if recommendation not in available:
            raise RuntimeError(
                f"Recommended specialist {recommendation!r} is not currently available"
            )

        deliberation = await self._deliberate(
            mode="select-primary",
            incident_id=incident_id,
            probable_domain=domain,
            recommended_agent=recommendation,
            available_agents=available,
        )
        if (
            deliberation.goal != "manage_incident"
            or deliberation.intention != "select_primary_investigator"
        ):
            raise RuntimeError(
                "Jason BDI did not commit to the expected primary-investigator intention"
            )
        if deliberation.primary_investigator != recommendation:
            raise RuntimeError(
                "Jason BDI selected a specialist different from the triage recommendation"
            )
        return deliberation

    def _validate_available_agents(self, available_agents: list[str]) -> list[str]:
        available = [role.strip().lower() for role in available_agents]
        if not available:
            raise RuntimeError("No specialist agent is available for Jason BDI deliberation")
        if any(role not in _ALLOWED_ROLES for role in available):
            raise ValueError("available_agents contains an unsupported specialist role")
        if not Path(self.technical_lead_asl).is_file():
            raise RuntimeError(
                f"Technical Lead AgentSpeak source not found: {self.technical_lead_asl}"
            )
        return available

    async def _deliberate(
        self,
        *,
        mode: str,
        incident_id: str,
        probable_domain: str,
        recommended_agent: str,
        available_agents: list[str],
    ) -> BDIDeliberation:
        async with self._semaphore:
            process = await asyncio.create_subprocess_exec(
                self.command,
                self.technical_lead_asl,
                mode,
                incident_id,
                probable_domain,
                recommended_agent,
                ",".join(available_agents),
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

        lines = stdout.decode("utf-8", errors="replace").strip().splitlines()
        if not lines:
            raise RuntimeError("Jason BDI deliberation returned no decision")

        parts = lines[-1].split("\t")
        if len(parts) != 5 or parts[0] != "OK":
            raise RuntimeError(f"Unexpected Jason BDI response: {lines[-1]!r}")

        _, returned_incident, goal, intention, primary = parts
        if returned_incident != incident_id:
            raise RuntimeError("Jason BDI response incident_id does not match request")

        return BDIDeliberation(
            incident_id=returned_incident,
            goal=goal,
            intention=intention,
            primary_investigator=None if primary == "-" else primary,
        )
