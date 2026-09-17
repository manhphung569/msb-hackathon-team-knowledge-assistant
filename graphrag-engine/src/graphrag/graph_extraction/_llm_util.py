from __future__ import annotations

import json
import os

from ..config import Settings


def _call_openai(prompt: str, model: str) -> str:
    from openai import OpenAI

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY chưa được set trong .env — không thể chạy graph extraction.")
    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        max_completion_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content or ""


def _call_claude(prompt: str, model: str) -> str:
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY chưa được set trong .env — không thể chạy graph extraction.")
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def _call_greennode(prompt: str, model: str) -> str:
    """MSB AI Hackathon 2026 co-organizer platform — xem GreenNodeEmbedder trong
    embedding/embedder.py cho bối cảnh đầy đủ (docs/keys pending)."""
    from openai import OpenAI

    api_key = os.environ.get("GREENNODE_API_KEY")
    base_url = os.environ.get("GREENNODE_BASE_URL")
    if not api_key or not base_url:
        raise RuntimeError("GREENNODE_API_KEY / GREENNODE_BASE_URL chưa được set trong .env")
    client = OpenAI(api_key=api_key, base_url=base_url)
    response = client.chat.completions.create(
        model=model,
        max_completion_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content or ""


_PROVIDERS = {"openai": _call_openai, "claude": _call_claude, "greennode": _call_greennode}


def call_llm(prompt: str, settings: Settings) -> str:
    """Provider-agnostic — mirror đúng pattern generation/answer_generator.py (openai | claude,
    chọn qua settings.extraction_provider) để đổi provider chỉ cần sửa config/settings.yaml."""
    fn = _PROVIDERS.get(settings.extraction_provider)
    if fn is None:
        raise ValueError(f"Unknown extraction_provider: {settings.extraction_provider}")
    return fn(prompt, settings.extraction_model)


def parse_json_array(text: str) -> list[dict]:
    """Parse JSON array trả về từ Claude — dọn code fence nếu model lỡ bọc ``` dù prompt đã
    yêu cầu không bọc (model đôi khi vẫn làm vậy, giống `_strip_code_fence` ở normalize-engine
    converters/ocr.py). Trả về [] nếu không parse được thay vì raise — 1 chunk lỗi không nên
    làm dừng cả build."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [d for d in data if isinstance(d, dict)]
