from __future__ import annotations

import re

import yaml

from .config import PROJECT_ROOT, Settings

_SYSTEMS_PATH = PROJECT_ROOT / "config" / "systems.yaml"


def load_registry(settings: Settings) -> list[dict]:
    if not _SYSTEMS_PATH.exists():
        return []
    raw = yaml.safe_load(_SYSTEMS_PATH.read_text(encoding="utf-8")) or {}
    return raw.get("systems", [])


def save_registry(settings: Settings, systems: list[dict]) -> None:
    _SYSTEMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _SYSTEMS_PATH.write_text(
        yaml.safe_dump({"systems": systems}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def upsert_entity(
    settings: Settings,
    canonical: str,
    type_: str = "system",
    aliases: list[str] | None = None,
    depends_on: list[str] | None = None,
    note: str = "",
) -> list[dict]:
    systems = load_registry(settings)
    for s in systems:
        if s["canonical"] == canonical:
            s["type"] = type_
            s["aliases"] = list(dict.fromkeys([*s.get("aliases", []), *(aliases or [])]))
            s["depends_on"] = list(dict.fromkeys([*s.get("depends_on", []), *(depends_on or [])]))
            if note:
                s["note"] = note
            save_registry(settings, systems)
            return systems
    systems.append(
        {
            "canonical": canonical,
            "type": type_,
            "aliases": list(dict.fromkeys(aliases or [])),
            "depends_on": list(dict.fromkeys(depends_on or [])),
            "note": note,
        }
    )
    save_registry(settings, systems)
    return systems


def delete_entity(settings: Settings, canonical: str) -> list[dict]:
    systems = [s for s in load_registry(settings) if s["canonical"] != canonical]
    save_registry(settings, systems)
    return systems


def expand_query_terms(question: str, settings: Settings) -> list[str]:
    """Như people.expand_query_terms (thay alias khớp bằng từng alias khác) — CỘNG THÊM: nếu
    câu hỏi khớp 1 canonical có `depends_on`, thêm mỗi entity phụ thuộc trực tiếp thành 1 biến
    thể tìm kiếm riêng — đây là phần "hippocampal index" thật sự khác people.py: mở rộng sang
    tài liệu CHỈ nói về entity liên quan (vd tài liệu riêng của Woay/CMP) dù không nhắc tên
    entity gốc trong câu hỏi. Đây là index thủ công (view 11), không phải suy luận multi-hop tự
    động như graph layer đã tắt — chỉ đi đúng 1 bước theo quan hệ đã tự tay xác nhận.

    Không khớp gì thì trả về [question] như cũ, không tốn thêm gì cho câu hỏi không liên quan.
    """
    systems = load_registry(settings)
    variants = {question}
    for s in systems:
        all_names = [s["canonical"], *s.get("aliases", [])]
        matched = next(
            (n for n in all_names if re.search(rf"(?<!\w){re.escape(n)}(?!\w)", question, re.IGNORECASE)),
            None,
        )
        if not matched:
            continue
        pattern = re.compile(rf"(?<!\w){re.escape(matched)}(?!\w)", re.IGNORECASE)
        for other in all_names:
            if other == matched:
                continue
            variants.add(pattern.sub(other, question))
        for dep in s.get("depends_on", []):
            variants.add(dep)
    return list(variants)
