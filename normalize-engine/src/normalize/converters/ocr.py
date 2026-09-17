from __future__ import annotations

import base64
import os
from pathlib import Path

import fitz  # PyMuPDF
from openai import OpenAI

_PROMPT = (
    "Trích xuất toàn bộ văn bản trong ảnh này sang markdown, giữ nguyên nội dung gốc "
    "(kể cả tiếng Việt có dấu). Nếu có bảng, giữ cấu trúc bằng markdown table. "
    "Chỉ transcribe, không tóm tắt, không thêm bình luận. "
    "Trả lời bằng markdown thuần, KHÔNG bọc toàn bộ câu trả lời trong ```."
)


def _render_pages(path: Path, dpi: int = 200) -> list[bytes]:
    doc = fitz.open(str(path))
    zoom = dpi / 72
    matrix = fitz.Matrix(zoom, zoom)
    try:
        return [page.get_pixmap(matrix=matrix).tobytes("png") for page in doc]
    finally:
        doc.close()


def _strip_code_fence(text: str) -> str:
    """Model hay bọc cả câu trả lời trong ```markdown ... ``` — bóc ra để có markdown thuần."""
    lines = text.strip().splitlines()
    if lines and lines[0].startswith("```") and lines[-1].strip() == "```":
        lines = lines[1:-1]
    return "\n".join(lines).strip()


def _transcribe_page(client: OpenAI, model: str, image_bytes: bytes) -> str:
    b64 = base64.b64encode(image_bytes).decode("ascii")
    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ],
            }
        ],
        max_completion_tokens=4096,
    )
    return _strip_code_fence(response.choices[0].message.content or "")


def convert(path: Path, model: str) -> str:
    # Mặc định KHÔNG đổi: vẫn gọi OpenAI thẳng, không base_url — đây là call site duy nhất trong
    # normalize-engine tốn phí thật (vision OCR), nên chỉ bật GreenNode khi chủ động opt-in qua
    # OCR_PROVIDER=greennode (MSB AI Hackathon 2026, docs/keys pending — xem
    # graphrag-engine/src/graphrag/embedding/embedder.py's GreenNodeEmbedder cho bối cảnh đầy đủ).
    use_greennode = os.environ.get("OCR_PROVIDER", "").strip().lower() == "greennode"
    if use_greennode:
        api_key = os.environ.get("GREENNODE_API_KEY")
        base_url = os.environ.get("GREENNODE_BASE_URL")
        if not api_key or not base_url:
            raise RuntimeError("GREENNODE_API_KEY / GREENNODE_BASE_URL chưa được set trong .env")
        client = OpenAI(api_key=api_key, base_url=base_url)
    else:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY chưa được set trong .env")
        client = OpenAI(api_key=api_key)

    parts = []
    for i, image_bytes in enumerate(_render_pages(path), start=1):
        text = _transcribe_page(client, model, image_bytes)
        parts.append(f"## Trang {i}\n\n{text}")
    return "\n\n".join(parts)
