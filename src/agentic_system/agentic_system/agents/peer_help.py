"""Specialist self-assessment for autonomous peer collaboration.

A specialist that finishes its ReAct investigation still unable to confirm a
root cause decides, with its own Gemma reasoner, whether a peer in a different
domain should investigate too and which one. This replaces the previous flow
where the Technical Lead's review decided ``request_support`` and picked the
support domain: the requesting specialist now contacts the peer directly.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from spade_llm.context import ContextManager
from spade_llm.providers.base_provider import BaseLLMProvider


ROLE_DOMAIN: dict[str, str] = {
    "system_engineer": "system",
    "network_engineer": "network",
    "application_engineer": "application",
    "software_developer": "software",
}
DOMAIN_ROLE: dict[str, str] = {domain: role for role, domain in ROLE_DOMAIN.items()}
ALLOWED_DOMAINS = frozenset(ROLE_DOMAIN.values())


@dataclass(frozen=True, slots=True)
class PeerHelpDecision:
    needs_help: bool
    target_domain: str | None
    reason: str


class PeerHelpReasoner:
    """Gemma-only, tool-less decision: ask a peer domain for help, or not."""

    _SYSTEM_TEMPLATE = """You are the {role} of an IT monitoring multi-agent team.
You have just completed your own bounded investigation of an incident in your
domain ({domain}) and could not fully confirm the root cause. Decide whether a
single peer specialist from a DIFFERENT domain should investigate the same
incident to add cross-domain evidence, and which domain.

Domains and owners:
- system_engineer -> system: host/container resources, CPU, memory, disk, runtime state.
- network_engineer -> network: latency, connections, communication and network behaviour.
- application_engineer -> application: application health, logs, service errors, runtime behaviour.
- software_developer -> software: implementation/design behaviour and code-level investigation.

Rules:
- Only ONE peer may be consulted for this incident. Choose the single most useful domain.
- The peer domain MUST be different from your own ({domain}).
- If your evidence is already sufficient, or no other domain would plausibly help,
  answer needs_help=false.
- Do NOT diagnose, do NOT claim a root cause, do NOT propose remediation here.

Return only a JSON object with these fields:
needs_help: boolean
target_domain: one of system, network, application, software (different from {domain}),
  or null when needs_help is false
reason: short explanation of what cross-domain evidence the peer should look for
Never include diagnosis, root_cause, remediation, commands or corrective actions."""

    def __init__(self, role: str, provider: BaseLLMProvider) -> None:
        normalized = role.strip().lower()
        if normalized not in ROLE_DOMAIN:
            raise ValueError(f"Unsupported specialist role for peer help: {role!r}")
        self.role = normalized
        self.domain = ROLE_DOMAIN[normalized]
        self.provider = provider
        self.system_prompt = self._SYSTEM_TEMPLATE.format(role=self.role, domain=self.domain)

    async def assess(
        self,
        *,
        incident_id: str,
        result: dict[str, Any],
    ) -> PeerHelpDecision:
        conversation_id = f"{self.role}-peer-help:{incident_id}"
        context = ContextManager(system_prompt=self.system_prompt)
        context.add_message_dict(
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "incident_id": incident_id,
                        "your_domain": self.domain,
                        "your_result": {
                            "diagnosis_status": result.get("diagnosis_status"),
                            "summary": result.get("summary"),
                            "root_cause": result.get("root_cause"),
                            "findings": result.get("findings") or [],
                            "hypotheses": result.get("hypotheses") or [],
                            "assistance_domain_hint": result.get("assistance_domain"),
                        },
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            },
            conversation_id,
        )

        response = await self.provider.get_llm_response(
            context,
            tools=None,
            conversation_id=conversation_id,
        )
        raw_text = str(response.get("text") or "").strip()
        return self._parse_response(raw_text, own_domain=self.domain)

    @staticmethod
    def _parse_response(raw_text: str, *, own_domain: str) -> PeerHelpDecision:
        text = raw_text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Peer-help decision did not return valid JSON: {raw_text[:300]!r}"
            ) from exc

        if not isinstance(payload, dict):
            raise RuntimeError("Peer-help decision must be a JSON object")

        forbidden = {"diagnosis", "root_cause", "remediation"} & set(payload)
        if forbidden:
            raise RuntimeError(
                "Peer-help decision attempted to produce diagnostic content: "
                + ", ".join(sorted(forbidden))
            )

        needs_help = bool(payload.get("needs_help"))
        reason = str(payload.get("reason") or "").strip()
        target_domain = payload.get("target_domain")
        target_domain = (
            str(target_domain).strip().lower() if target_domain is not None else None
        )

        if not needs_help:
            return PeerHelpDecision(needs_help=False, target_domain=None, reason=reason)

        if target_domain not in ALLOWED_DOMAINS:
            raise RuntimeError(
                f"Peer-help decision returned an invalid target domain: {target_domain!r}"
            )
        if target_domain == own_domain:
            raise RuntimeError(
                f"Peer-help decision targeted its own domain {own_domain!r}"
            )
        if not reason:
            raise RuntimeError("Peer-help decision requires a reason when needs_help is true")

        return PeerHelpDecision(
            needs_help=True,
            target_domain=target_domain,
            reason=reason,
        )
