"""Thin client cho graphrag-engine's guest `POST /query` — dùng để rewire chat.py's discover-mode
retrieval sang gọi thẳng 1 index LanceDB thật thay vì tự keyword-grep file .md rời trong
ARTIFACTS_ROOT (xem CLAUDE.md/plan hackathon: "1 index tri thức duy nhất").

Contract xác nhận trực tiếp từ graphrag-engine/src/graphrag/api.py:242-261:
  POST {GRAPHRAG_API_BASE}/query
  headers: X-API-Key: <GUEST_API_KEY của tenant đang chạy>
  body:    {"question": str}
  200:     {"answer": str, "sources": list[str]}   # source dạng "{source_path}#{section}"
  401/500: {"detail": str}

Không silent fallback: mọi lỗi (thiếu key, mất kết nối, non-200) raise GraphragError — caller
(chat.py) tự quyết định trả 502 cho client, KHÔNG được âm thầm quay lại keyword-grep cũ (dễ che
mất lỗi thật lúc demo dưới áp lực thời gian)."""

from __future__ import annotations

import requests

from .config import GRAPHRAG_API_BASE, GRAPHRAG_API_KEY


class GraphragError(RuntimeError):
    pass


def query_graphrag(question: str, timeout: int = 30) -> dict:
    if not GRAPHRAG_API_KEY:
        raise GraphragError("GRAPHRAG_API_KEY chưa được set trong workbench/.env")

    try:
        resp = requests.post(
            f"{GRAPHRAG_API_BASE}/query",
            json={"question": question},
            headers={"X-API-Key": GRAPHRAG_API_KEY},
            timeout=timeout,
        )
    except requests.RequestException as e:
        raise GraphragError(f"graphrag-engine không phản hồi ({GRAPHRAG_API_BASE}): {e}") from e

    if resp.status_code != 200:
        raise GraphragError(f"graphrag-engine trả lỗi {resp.status_code}: {resp.text[:300]}")

    try:
        return resp.json()
    except ValueError as e:
        raise GraphragError(f"graphrag-engine trả JSON không hợp lệ: {e}") from e
