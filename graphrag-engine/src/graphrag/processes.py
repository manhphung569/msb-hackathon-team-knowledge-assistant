from __future__ import annotations

import re

from .config import Settings

_PROCESS_RULES = [
    {
        "code": "QT.IT.005",
        "aliases": [
            "tiep nhan yeu cau",
            "quan ly yeu cau phat trien",
            "demand intake",
            "fast lane",
            "luong nhanh",
            "backlog",
            "pending backlog",
            "product backlog",
            "sprint backlog",
        ],
    },
    {
        "code": "QT.IT.009",
        "aliases": [
            "phat trien phan mem",
            "uat",
            "sit",
            "release",
            "kiem thu",
            "uat release",
            "kich ban kiem thu",
            "sau golive",
            "post golive",
        ],
    },
    {
        "code": "QT.IT.019",
        "aliases": [
            "trien khai du an",
            "golive",
            "ho so golive",
            "production",
            "len production",
            "production test",
            "go live",
        ],
    },
]


def _normalize(text: str) -> str:
    text = text.lower()
    table = str.maketrans(
        {
            "á": "a",
            "à": "a",
            "ả": "a",
            "ã": "a",
            "ạ": "a",
            "ă": "a",
            "ắ": "a",
            "ằ": "a",
            "ẳ": "a",
            "ẵ": "a",
            "ặ": "a",
            "â": "a",
            "ấ": "a",
            "ầ": "a",
            "ẩ": "a",
            "ẫ": "a",
            "ậ": "a",
            "é": "e",
            "è": "e",
            "ẻ": "e",
            "ẽ": "e",
            "ẹ": "e",
            "ê": "e",
            "ế": "e",
            "ề": "e",
            "ể": "e",
            "ễ": "e",
            "ệ": "e",
            "í": "i",
            "ì": "i",
            "ỉ": "i",
            "ĩ": "i",
            "ị": "i",
            "ó": "o",
            "ò": "o",
            "ỏ": "o",
            "õ": "o",
            "ọ": "o",
            "ô": "o",
            "ố": "o",
            "ồ": "o",
            "ổ": "o",
            "ỗ": "o",
            "ộ": "o",
            "ơ": "o",
            "ớ": "o",
            "ờ": "o",
            "ở": "o",
            "ỡ": "o",
            "ợ": "o",
            "ú": "u",
            "ù": "u",
            "ủ": "u",
            "ũ": "u",
            "ụ": "u",
            "ư": "u",
            "ứ": "u",
            "ừ": "u",
            "ử": "u",
            "ữ": "u",
            "ự": "u",
            "ý": "y",
            "ỳ": "y",
            "ỷ": "y",
            "ỹ": "y",
            "ỵ": "y",
            "đ": "d",
        }
    )
    return re.sub(r"\s+", " ", text.translate(table)).strip()


def _code_variants(code: str) -> list[str]:
    digits = code.split(".")[-1]
    return [
        code,
        code.replace(".", " "),
        code.replace(".", ""),
        f"QT IT {digits}",
        f"QT.IT {digits}",
        f"QTIT{digits}",
    ]


def identify_process_codes(question: str) -> list[str]:
    normalized = _normalize(question)
    matched: list[str] = []
    for rule in _PROCESS_RULES:
        if any(alias in normalized for alias in rule["aliases"]):
            matched.append(rule["code"])
            continue
        if any(_normalize(variant) in normalized for variant in _code_variants(rule["code"])):
            matched.append(rule["code"])
    return list(dict.fromkeys(matched))


def has_explicit_process_code(question: str) -> bool:
    normalized = _normalize(question)
    return any(_normalize(variant) in normalized for rule in _PROCESS_RULES for variant in _code_variants(rule["code"]))


def expand_query_terms(question: str, settings: Settings) -> list[str]:
    del settings
    variants = {question}
    for code in identify_process_codes(question):
        variants.update(_code_variants(code))
        variants.add(f"{question} {code}")
    return list(variants)
