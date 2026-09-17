from __future__ import annotations

from ..types import Chunk


def fuse_ranked_lists(weighted_lists: list[tuple[str, float, list[Chunk]]], top_k: int) -> list[Chunk]:
    """Weighted reciprocal-rank fusion cho nhiều danh sách candidate.

    `weighted_lists`: [(name, weight, chunks), ...]
    - name chỉ để debug/log nếu caller cần
    - weight lớn hơn => tín hiệu đó mạnh hơn
    """
    if top_k <= 0:
        return []

    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    chosen_chunk: dict[str, Chunk] = {}
    global_order = 0
    k_base = 10.0

    for _, weight, chunks in weighted_lists:
        for rank, chunk in enumerate(chunks, start=1):
            if chunk.chunk_id not in first_seen:
                first_seen[chunk.chunk_id] = global_order
                chosen_chunk[chunk.chunk_id] = chunk
                global_order += 1
            scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + (weight / (k_base + rank))

    ranked_ids = sorted(scores, key=lambda chunk_id: (-scores[chunk_id], first_seen[chunk_id]))
    return [chosen_chunk[chunk_id] for chunk_id in ranked_ids[:top_k]]
