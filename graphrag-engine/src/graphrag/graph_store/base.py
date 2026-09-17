from __future__ import annotations

from abc import ABC, abstractmethod

from ..types import GraphEdge, GraphNode


class GraphStore(ABC):
    @abstractmethod
    def upsert_nodes(self, nodes: list[GraphNode]) -> None: ...

    @abstractmethod
    def upsert_edges(self, edges: list[GraphEdge]) -> None: ...

    @abstractmethod
    def delete_by_doc_id(self, doc_id: str) -> None: ...

    @abstractmethod
    def k_hop(self, entity_ids: list[str], hops: int) -> list[GraphEdge]: ...

    @abstractmethod
    def reset(self) -> None: ...
