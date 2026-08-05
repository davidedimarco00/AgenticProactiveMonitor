from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable


DiagnosticHandler = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class DiagnosticRequest:
    action: str
    target: str
    parameters: dict[str, Any]


class DiagnosticExecutor:
    def __init__(self) -> None:
        self._handlers: dict[str, DiagnosticHandler] = {}

    def register(self, action: str, handler: DiagnosticHandler) -> None:
        self._handlers[action] = handler

    async def execute(self, request: DiagnosticRequest) -> dict[str, Any]:
        if request.action not in self._handlers:
            raise ValueError(f'Diagnostic action not allowed: {request.action}')
        return await self._handlers[request.action](request.target, request.parameters)
