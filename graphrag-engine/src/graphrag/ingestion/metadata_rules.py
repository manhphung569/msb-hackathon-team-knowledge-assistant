from __future__ import annotations

import re

_TICKET_PATTERNS = [
    re.compile(r"\bQLYC-\d+\b", re.I),
    re.compile(r"\bRE-\d+\b", re.I),
]

_PEOPLE_LINE_PATTERNS = [
    re.compile(r"^Từ:\s*(.+)$", re.M),
    re.compile(r"^Tới:\s*(.+)$", re.M),
    re.compile(r"^Cc:\s*(.+)$", re.M),
    re.compile(r"^\[\d{4}-\d{2}-\d{2} [^\]]+\]\s*([^:]+):", re.M),
]

_PEOPLE_INLINE_PATTERNS = [
    re.compile(r"\b([A-ZĐ][a-zà-ỹđ]+(?:\s+[A-ZĐ][a-zà-ỹđ]+){1,4})\s*\(", re.U),
]


def extract_ticket_ids(text: str, source_path: str = "") -> list[str]:
    found: list[str] = []
    for haystack in (text, source_path):
        for pattern in _TICKET_PATTERNS:
            for match in pattern.findall(haystack):
                canonical = match.upper()
                if canonical not in found:
                    found.append(canonical)
    return found


def extract_people_mentions(text: str) -> list[str]:
    found: list[str] = []
    for pattern in _PEOPLE_LINE_PATTERNS:
        for match in pattern.findall(text):
            for person in _split_people_field(match):
                if person not in found:
                    found.append(person)
    for pattern in _PEOPLE_INLINE_PATTERNS:
        for match in pattern.findall(text):
            person = _clean_person(match)
            if person and person not in found:
                found.append(person)
    return found[:50]


def _split_people_field(value: str) -> list[str]:
    items: list[str] = []
    for part in re.split(r"\s*,\s*", value):
        cleaned = _clean_person(part)
        if cleaned:
            items.append(cleaned)
    return items


def _clean_person(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value)
    value = re.sub(r"\([^)]*\)", "", value)
    value = re.sub(r"\S+@\S+", "", value)
    value = re.sub(r"\s+", " ", value).strip(" -:\t")
    if len(value) < 3:
        return ""
    if any(ch.isdigit() for ch in value):
        return ""
    return value
