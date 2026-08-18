from __future__ import annotations

from typing import Any, Protocol


class DetectorContextPort(Protocol):
    """Read-only application port for normalized OpenSearch detector metadata."""

    async def get_detector_context(self, detector_id: str) -> dict[str, Any]: ...
