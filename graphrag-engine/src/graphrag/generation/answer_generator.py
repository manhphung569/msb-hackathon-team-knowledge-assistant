from __future__ import annotations

import os

from ..config import PROJECT_ROOT, Settings

_PROMPT_PATH = PROJECT_ROOT / "config" / "prompts" / "answer_synthesis.md"


def _build_prompt(query: str, context: str, subgraph_facts: str) -> str:
    template = _PROMPT_PATH.read_text(encoding="utf-8")
    return (
        template.replace("{{query}}", query)
        .replace("{{chunks}}", context)
        .replace("{{subgraph_facts}}", subgraph_facts)
    )


def _generate_openai(prompt: str, model: str, max_tokens: int) -> str:
    from openai import OpenAI

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY chưa được set trong .env — không thể sinh câu trả lời.")
    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        max_completion_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content or ""


def _generate_claude(prompt: str, model: str, max_tokens: int) -> str:
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY chưa được set trong .env — không thể sinh câu trả lời.")
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def _generate_greennode(prompt: str, model: str, max_tokens: int) -> str:
    """MSB AI Hackathon 2026 co-organizer platform — xem GreenNodeEmbedder trong
    embedding/embedder.py cho bối cảnh đầy đủ (docs/keys pending, viết theo giả định
    OpenAI-compatible, chỉ cần sửa body hàm này nếu thực tế khác)."""
    from openai import OpenAI

    api_key = os.environ.get("GREENNODE_API_KEY")
    base_url = os.environ.get("GREENNODE_BASE_URL")
    if not api_key or not base_url:
        raise RuntimeError("GREENNODE_API_KEY / GREENNODE_BASE_URL chưa được set trong .env")
    client = OpenAI(api_key=api_key, base_url=base_url)
    response = client.chat.completions.create(
        model=model,
        max_completion_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content or ""


_PROVIDERS = {
    "openai": _generate_openai,
    "claude": _generate_claude,
    "greennode": _generate_greennode,
}


def call_llm(prompt: str, provider: str, model: str, max_tokens: int = 1024) -> str:
    """Gọi thẳng 1 provider LLM (openai/claude) với 1 prompt đã dựng sẵn — dùng chung cho
    generate_answer() (trả lời câu hỏi) và pipeline/dashboard.py (tổng hợp dashboard sáng).
    KHÔNG tự lọc sensitivity ở đây (như generate_answer() từ trước tới giờ) — nơi gọi hàm này
    chịu trách nhiệm quyết định context có được phép gửi cloud hay không trước khi gọi."""
    generate = _PROVIDERS.get(provider)
    if generate is None:
        raise ValueError(f"Unknown provider: {provider}")
    return generate(prompt, model, max_tokens)


def generate_answer(
    query: str, context: str, settings: Settings, subgraph_facts: str = "(chưa có graph layer — Giai đoạn 1)"
) -> str:
    prompt = _build_prompt(query, context, subgraph_facts)
    return call_llm(prompt, settings.answer_provider, settings.answer_model)
