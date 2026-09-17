from __future__ import annotations

from ..graph_store.base import GraphStore
from ..types import GraphEdge


def seed_entities(chunk_ids: list[str], graph_store: GraphStore) -> list[str]:
    """Entity mà các chunk top-k vector đã nhắc tới — dùng k_hop(chunk_ids, hops=1) thay vì
    thêm 1 method riêng vào GraphStore: k_hop đã tổng quát theo node id bất kỳ (không riêng
    entity), nên gọi trên chunk_id sẽ trả về đúng cạnh MENTIONS 1-hop của các chunk đó."""
    edges = graph_store.k_hop(chunk_ids, hops=1)
    chunk_id_set = set(chunk_ids)
    return list({e.to_id for e in edges if e.from_id in chunk_id_set and e.relation == "MENTIONS"})


def expand(chunk_ids: list[str], graph_store: GraphStore, hops: int) -> list[GraphEdge]:
    """Từ chunk top-k vector, tìm entity liên quan rồi k-hop traversal — trả về toàn bộ cạnh
    tìm được, kể cả cạnh nối tới chunk không lọt top-k vector nhưng liên quan qua quan hệ với
    entity trong top-k. Đây là phần "graph bù vector" nêu ở ARCHITECTURE.md §4."""
    entities = seed_entities(chunk_ids, graph_store)
    if not entities:
        return []
    return graph_store.k_hop(entities, hops=hops)
