from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from jsonschema import Draft202012Validator
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from .diagnostic_react import SpecialistReActExecutor as _EvidenceFirstExecutor
from .langchain_agent import ReActEvidence, ReActInvestigationError


LOGGER = logging.getLogger("agentic_system.reasoning.schema_validated_react")


class SpecialistReActExecutor(_EvidenceFirstExecutor):
    """Evidence-first ReAct with pre-execution JSON-Schema tool validation."""

    _PRIMARY_TARGET_ARGUMENTS = (
        "host_id",
        "service",
        "service_name",
        "component",
        "container_name",
    )
    _RELATED_TARGET_ARGUMENTS = (
        "target_host",
        "target_service",
        "dependency",
    )

    @staticmethod
    def _validate_tool_args(tool: Any, args: dict[str, Any]) -> dict[str, Any]:
        """Validate only the declared tool JSON schema.

        Kept static for generic callers and regression tests. Project-specific
        semantic constraints are applied separately through
        ``_validate_tool_semantics`` after schema validation.
        """

        schema = getattr(tool, "args_schema", None)
        if isinstance(schema, dict):
            validator = Draft202012Validator(schema)
            errors = sorted(validator.iter_errors(args), key=lambda item: list(item.path))
            if errors:
                error = errors[0]
                location = ".".join(str(item) for item in error.path) or "arguments"
                raise ValueError(f"{location}: {error.message}")
            return dict(args)

        input_schema = tool.get_input_schema()
        validated = input_schema.model_validate(args)
        return validated.model_dump(exclude_none=True)

    @staticmethod
    def _tool_schema_properties(tool: Any) -> dict[str, Any]:
        schema = getattr(tool, "args_schema", None)
        if isinstance(schema, dict):
            properties = schema.get("properties")
            return dict(properties) if isinstance(properties, dict) else {}

        try:
            input_schema = tool.get_input_schema()
            model_schema = input_schema.model_json_schema()
        except Exception:
            return {}
        properties = model_schema.get("properties")
        return dict(properties) if isinstance(properties, dict) else {}

    def _bind_tool_arguments(
        self,
        tool: Any,
        args: dict[str, Any],
        assignment: dict[str, Any],
    ) -> dict[str, Any]:
        """Bind target identifiers already fixed by the evidence contract.

        Qwen decides which compatible tool to use and may supply non-authoritative
        optional arguments. It must not be responsible for repeating the target
        component already selected by Gemma. If ``assignment.evidence_request``
        is present, the runtime injects its primary and secondary targets into
        the corresponding declared tool arguments before JSON-Schema validation.
        """

        clean_args = dict(args)
        request = assignment.get("evidence_request")
        if not isinstance(request, dict):
            return clean_args

        properties = self._tool_schema_properties(tool)
        target_component = str(request.get("target_component") or "").strip()
        related_component = str(request.get("related_component") or "").strip()
        tool_name = str(getattr(tool, "name", "")).strip()

        if target_component:
            for field in self._PRIMARY_TARGET_ARGUMENTS:
                if field not in properties:
                    continue
                original = str(clean_args.get(field) or "").strip()
                clean_args[field] = target_component
                if original and original != target_component:
                    LOGGER.info(
                        "Rebound Qwen primary target before MCP execution: tool=%s field=%s "
                        "model_value=%s evidence_target=%s",
                        tool_name,
                        field,
                        original,
                        target_component,
                    )
                break

        if related_component:
            for field in self._RELATED_TARGET_ARGUMENTS:
                if field not in properties:
                    continue
                original = str(clean_args.get(field) or "").strip()
                clean_args[field] = related_component
                if original and original != related_component:
                    LOGGER.info(
                        "Rebound Qwen related target before MCP execution: tool=%s field=%s "
                        "model_value=%s evidence_target=%s",
                        tool_name,
                        field,
                        original,
                        related_component,
                    )
                break

        return clean_args

    def _validate_tool_semantics(
        self,
        tool: Any,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """Project extension point for semantic action contracts."""

        return dict(args)

    async def _select_tool(
        self,
        *,
        assignment: dict[str, Any],
        evidence_needed: str,
        evidence: list[ReActEvidence],
    ) -> tuple[str, dict[str, Any]]:
        previous_calls = [
            {"tool": item.tool, "arguments": item.arguments, "success": item.success}
            for item in evidence[-6:]
        ]
        messages = [
            {"role": "system", "content": self.TOOL_SELECTION_POLICY},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "assignment": assignment,
                        "evidence_requested_by_gemma": evidence_needed,
                        "previous_tool_calls": previous_calls,
                    },
                    default=str,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            },
        ]

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self._invoke_with_provider_slot(
                    self.tool_provider,
                    self._tool_selector.ainvoke(messages),
                )
                if not isinstance(response, AIMessage):
                    raise RuntimeError("Qwen tool selector returned a non-AI message")
                calls = list(response.tool_calls or [])
                if not calls:
                    raise RuntimeError("Qwen tool selector did not return a tool call")
                call = calls[0]
                name = str(call.get("name") or "").strip()
                if name not in self._tool_names:
                    raise RuntimeError(f"Qwen selected unavailable tool: {name!r}")
                args = call.get("args") or {}
                if not isinstance(args, dict):
                    raise RuntimeError("Qwen tool arguments are not a JSON object")

                tool = self._langchain_tool_by_name[name]
                bound_args = self._bind_tool_arguments(tool, dict(args), assignment)
                clean_args = self._validate_tool_args(tool, bound_args)
                clean_args = self._validate_tool_semantics(tool, clean_args)
                duplicate = any(
                    item.success and item.tool == name and item.arguments == clean_args
                    for item in evidence
                )
                if duplicate:
                    raise RuntimeError(
                        "The same successful diagnostic call was already executed; select a "
                        "different action that adds discriminating evidence."
                    )
                return name, clean_args
            except asyncio.CancelledError:
                raise
            except (ValidationError, RuntimeError, ValueError) as exc:
                last_error = exc
                if attempt < 2:
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "The proposed tool call was rejected BEFORE MCP execution. Repair "
                                "the tool choice or non-authoritative arguments without changing "
                                "Gemma's evidence goal. The runtime binds target identifiers from "
                                "assignment.evidence_request deterministically, so do not choose a "
                                "different target. Validation feedback: "
                                f"{exc}. Select exactly one bound tool with valid arguments."
                            ),
                        }
                    )

        raise ReActInvestigationError(f"Qwen tool selection failed: {last_error}") from last_error
