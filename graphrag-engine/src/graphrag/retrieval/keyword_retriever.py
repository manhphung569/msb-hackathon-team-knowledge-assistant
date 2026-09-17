from __future__ import annotations

import re
import unicodedata

from ..types import Chunk
from ..vector_store.base import VectorStore

_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{1,}")
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "bị",
    "by",
    "cho",
    "các",
    "cần",
    "còn",
    "có",
    "của",
    "da",
    "dang",
    "de",
    "den",
    "di",
    "do",
    "for",
    "from",
    "gi",
    "gì",
    "hay",
    "how",
    "in",
    "is",
    "khi",
    "không",
    "ko",
    "là",
    "làm",
    "len",
    "lên",
    "một",
    "nao",
    "nào",
    "những",
    "như",
    "of",
    "on",
    "or",
    "ra",
    "sau",
    "show",
    "sẽ",
    "tai",
    "tại",
    "the",
    "theo",
    "thi",
    "thì",
    "this",
    "to",
    "tren",
    "trên",
    "trong",
    "tu",
    "từ",
    "và",
    "ve",
    "về",
    "với",
    "what",
}


def _strip_accents(text: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFD", text) if unicodedata.category(ch) != "Mn"
    )


def _normalize(text: str) -> str:
    base = _strip_accents(text.lower())
    return re.sub(r"\s+", " ", base).strip()


def extract_keyword_terms(question: str) -> list[str]:
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(_normalize(question)):
        token = raw.strip("._/-")
        if len(token) < 2 or token in _STOPWORDS:
            continue
        if token not in tokens:
            tokens.append(token)
    return tokens


def should_use_keyword(question: str) -> bool:
    return bool(extract_keyword_terms(question))


def retrieve_keyword(question: str, store: VectorStore, k: int, filters: dict | None = None) -> list[Chunk]:
    terms = extract_keyword_terms(question)
    if not terms:
        return []

    scored: list[tuple[int, Chunk]] = []
    for chunk in store.all_chunks(filters=filters):
        score = _score_chunk(chunk, terms)
        if score > 0:
            scored.append((score, chunk))

    scored.sort(key=lambda item: (-item[0], item[1].source_path, item[1].chunk_index))
    seen: set[str] = set()
    result: list[Chunk] = []
    for _, chunk in scored:
        if chunk.chunk_id in seen:
            continue
        seen.add(chunk.chunk_id)
        result.append(chunk)
        if len(result) >= k:
            break
    return result


def _contains_whole_term(haystack: str, term: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", haystack) is not None


def _score_chunk(chunk: Chunk, terms: list[str]) -> int:
    path = _normalize(chunk.source_path)
    section = _normalize(chunk.section or "")
    project = _normalize(chunk.project)
    doc_type = _normalize(chunk.doc_type)
    text = _normalize(chunk.text)
    channel = _normalize(chunk.source_channel)
    reliability = _normalize(chunk.reliability)
    ticket_ids = [_normalize(ticket) for ticket in chunk.ticket_ids]
    people = [_normalize(name) for name in chunk.people_mentions]

    score = 0
    matched_terms = 0
    for term in terms:
        term_score = 0
        if any(term == ticket or term in ticket for ticket in ticket_ids):
            term_score += 120
        if any(_contains_whole_term(person, term) or term in person for person in people):
            term_score += 60
        if _contains_whole_term(path, term):
            term_score += 50
        elif term in path:
            term_score += 30
        if _contains_whole_term(section, term):
            term_score += 30
        if _contains_whole_term(project, term) or term in project:
            term_score += 20
        if _contains_whole_term(doc_type, term) or term in doc_type:
            term_score += 15
        if _contains_whole_term(channel, term):
            term_score += 8
        if _contains_whole_term(reliability, term):
            term_score += 5
        if _contains_whole_term(text, term):
            term_score += 12
        elif term in text:
            term_score += 6
        if term_score > 0:
            matched_terms += 1
            score += term_score

    if matched_terms > 1:
        score += matched_terms * 10
    return score
