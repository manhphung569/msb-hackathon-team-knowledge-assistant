from __future__ import annotations

from ..types import Chunk, GraphEdge
from ..vector_store.base import VectorStore


def fuse_chunks(
    vector_chunks: list[Chunk],
    subgraph_edges: list[GraphEdge],
    vector_store: VectorStore,
    top_n: int,
) -> list[Chunk]:
    """Gộp chunk top-k vector với chunk mới kéo về qua cạnh MENTIONS trong subgraph_edges
    (chunk không lọt top-k vector nhưng nối qua quan hệ với entity liên quan). Không dùng
    cross-encoder rerank — quy mô cá nhân chưa cần: giữ nguyên thứ hạng vector, nhưng dành
    riêng tối đa 1 nửa ngân sách top_n cho chunk mới từ graph.

    Lưu ý: vector_top_k (mặc định 20) > top_n (mặc định 8), nên nếu chỉ nối chunk graph vào
    CUỐI danh sách rồi cắt top_n thì chunk graph không bao giờ lọt qua — luôn bị cắt trước khi
    tới lượt (đã kiểm chứng bằng dữ liệu thật, xem lịch sử build). Dành ngân sách riêng mới
    đảm bảo đúng mục đích hybrid: kéo được chunk không lọt top-k vector nhưng liên quan qua
    quan hệ (ARCHITECTURE.md §4).

    MENTIONS luôn có from=Chunk theo ontology (config/graph_schema.yaml) nên e.from_id ở đây
    chắc chắn là chunk_id, bất kể k_hop trả cạnh theo chiều nào."""
    seen_ids = {c.chunk_id for c in vector_chunks}
    extra_ids = [
        e.from_id for e in subgraph_edges if e.relation == "MENTIONS" and e.from_id not in seen_ids
    ]
    extra_ids = list(dict.fromkeys(extra_ids))

    if not extra_ids:
        return vector_chunks[:top_n]

    extra_chunks = vector_store.get_by_ids(extra_ids)
    graph_budget = min(len(extra_chunks), max(1, top_n // 2))
    vector_budget = top_n - graph_budget
    return vector_chunks[:vector_budget] + extra_chunks[:graph_budget]
