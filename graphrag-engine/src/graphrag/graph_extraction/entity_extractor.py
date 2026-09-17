from __future__ import annotations

from ..config import PROJECT_ROOT, Settings
from ._llm_util import call_llm, parse_json_array

_PROMPT_PATH = PROJECT_ROOT / "config" / "prompts" / "entity_extraction.md"
_VALID_TYPES = {"Project", "System", "Person", "Organization", "Concept"}


def extract_entities(chunk_text: str, settings: Settings) -> list[dict]:
    """Trích entity thô (chưa resolve id) từ 1 chunk. Mỗi entity: type/name/aliases/evidence."""
    prompt = _PROMPT_PATH.read_text(encoding="utf-8").replace("{{chunk_text}}", chunk_text)
    raw = parse_json_array(call_llm(prompt, settings))

    entities = []
    for item in raw:
        etype = item.get("type")
        name = item.get("name")
        if etype not in _VALID_TYPES or not name:
            continue
        entities.append(
            {
                "type": etype,
                "name": str(name).strip(),
                "aliases": [str(a).strip() for a in (item.get("aliases") or []) if a],
                "evidence": str(item.get("evidence", "")).strip(),
            }
        )
    return entities
