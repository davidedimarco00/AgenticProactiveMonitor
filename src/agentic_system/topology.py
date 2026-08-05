from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ServiceNode:
    host_id: str
    role: str
    dependencies: list[str] = field(default_factory=list)


class TopologyRegistry:
    def __init__(self, nodes: list[ServiceNode] | None = None) -> None:
        self._nodes = {node.host_id: node for node in nodes or []}

    def upsert(self, node: ServiceNode) -> None:
        self._nodes[node.host_id] = node

    def get(self, host_id: str) -> ServiceNode | None:
        return self._nodes.get(host_id)

    def investigation_scope(self, host_id: str) -> list[str]:
        node = self.get(host_id)
        if not node:
            return [host_id]
        return list(dict.fromkeys([host_id, *node.dependencies]))
