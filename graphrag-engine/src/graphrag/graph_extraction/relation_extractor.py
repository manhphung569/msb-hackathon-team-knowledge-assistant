from __future__ import annotations

from ..config import PROJECT_ROOT, Settings
from ._llm_util import call_llm, parse_json_array

_PROMPT_PATH = PROJECT_ROOT / "config" / "prompts" / "relation_extraction.md"
_VALID_RELATIONS = {"BELONGS_TO", "PART_OF", "DEPENDS_ON", "OWNED_BY", "ATTENDED", "RELATED_TO"}


def extract_relations(chunk_text: str, entities: list[dict], settings: Settings) -> list[dict]:
    """Trích quan hệ giữa các entity đã tìm được trong cùng 1 chunk. `from`/`to` trả về dùng
    đúng `name` (chưa resolve) — caller tự map sang node id qua bảng name->id đã build khi
    resolve entity."""
    if len(entities) < 2:
        return []

    entity_names = ", ".join(f'{e["name"]} ({e["type"]})' for e in entities)
    prompt = (
        _PROMPT_PATH.read_text(encoding="utf-8")
        .replace("{{entities}}", entity_names)
        .replace("{{chunk_text}}", chunk_text)
    )
    raw = parse_json_array(call_llm(prompt, settings))

    relations = []
    for item in raw:
        relation = item.get("relation")
        from_name = item.get("from")
        to_name = item.get("to")
        if relation not in _VALID_RELATIONS or not from_name or not to_name:
            continue
        relations.append(
            {
                "from": str(from_name).strip(),
                "to": str(to_name).strip(),
                "relation": relation,
                "evidence": str(item.get("evidence", "")).strip(),
            }
        )
    return relations
