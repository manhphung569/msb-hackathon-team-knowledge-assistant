from __future__ import annotations

from collections import defaultdict, deque

from ..types import Chunk

_CHARS_PER_TOKEN = 4


def _diversify_chunks(chunks: list[Chunk]) -> list[Chunk]:
    """Ưu tiên phủ nhiều source_path trước khi lấy thêm chunk tiếp theo từ cùng 1 file."""
    buckets: dict[str, deque[Chunk]] = defaultdict(deque)
    source_order: list[str] = []
    for chunk in chunks:
        if chunk.source_path not in buckets:
            source_order.append(chunk.source_path)
        buckets[chunk.source_path].append(chunk)

    packed: list[Chunk] = []
    while True:
        advanced = False
        for source_path in source_order:
            queue = buckets[source_path]
            if not queue:
                continue
            packed.append(queue.popleft())
            advanced = True
        if not advanced:
            break
    return packed


def build_context(chunks: list[Chunk], max_tokens: int = 6000) -> tuple[str, list[str]]:
    """Trả về (context, included_chunk_ids) — included_chunk_ids cho phép caller (CLI, API) biết
    chunk nào thực sự lọt vào context (so với danh sách đầy đủ result["chunks"]) khi ngân sách
    token cắt bớt, thay vì để caller đoán/không biết (xem ARCHITECTURE_DIAGRAMS.html view 09)."""
    budget = max_tokens * _CHARS_PER_TOKEN
    parts: list[str] = []
    included_ids: list[str] = []
    used = 0
    for c in _diversify_chunks(chunks):
        # Hiện kênh/ngày để LLM tự cân nhắc recency/độ tin cậy khi 2 chunk mâu thuẫn nhau —
        # không tự rerank/loại bỏ chunk cũ ở code (xem ARCHITECTURE.md, nguyên tắc không tự
        # động hoá phần cần phán đoán).
        block = f"[{c.source_channel} | {c.date} | {c.source_path}#{c.section or 'root'}]\n{c.text}\n"
        if used + len(block) > budget:
            break
        parts.append(block)
        included_ids.append(c.chunk_id)
        used += len(block)
    return "\n---\n".join(parts), included_ids
