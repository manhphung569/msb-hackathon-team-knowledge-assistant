from __future__ import annotations

import re

from ..types import Chunk
from ..vector_store.base import VectorStore

_EXACT_PATTERNS = [
    re.compile(r"\b[A-Z]{2,10}-\d+\b"),
    re.compile(r"\bRE-\d+\b", re.I),
    re.compile(r"\b(?:QT|QĐ|QD)\.IT\.\d+\b", re.I),
    re.compile(r"\bSIDLC[_A-Z0-9,.-]+\b", re.I),
    re.compile(r"\b[\w .()\-]+\.(?:md|pdf|docx|pptx|xlsx|msg|html?)\b", re.I),
]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def extract_exact_terms(question: str) -> list[str]:
    terms: list[str] = []
    for pattern in _EXACT_PATTERNS:
        for match in pattern.findall(question):
            if match not in terms:
                terms.append(match)
    normalized_question = question.strip()
    if not terms and 1 <= len(normalized_question) <= 80 and not normalized_question.endswith("?"):
        words = [part for part in re.split(r"\s+", normalized_question) if part]
        if len(words) <= 3 and re.search(r"[A-Z0-9._\\/-]", normalized_question):
            terms.append(normalized_question)
    return terms


def should_use_exact_first(question: str) -> bool:
    return bool(extract_exact_terms(question))


def retrieve_exact(question: str, store: VectorStore, k: int, filters: dict | None = None) -> list[Chunk]:
    terms = extract_exact_terms(question)
    if not terms:
        return []

    chunks = store.all_chunks(filters=filters)
    scored: list[tuple[int, Chunk]] = []
    normalized_question = _normalize(question)
    for chunk in chunks:
        score = _score_chunk(chunk, terms, normalized_question)
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


def _score_chunk(chunk: Chunk, terms: list[str], normalized_question: str) -> int:
    haystacks = {
        "source_path": _normalize(chunk.source_path),
        "section": _normalize(chunk.section or ""),
        "project": _normalize(chunk.project),
        "doc_type": _normalize(chunk.doc_type),
        "text": _normalize(chunk.text),
    }
    score = 0
    for term in terms:
        needle = _normalize(term)
        if needle == haystacks["source_path"] or haystacks["source_path"].endswith("/" + needle):
            score += 200
        elif needle in haystacks["source_path"]:
            score += 120
        if needle in haystacks["section"]:
            score += 40
        if needle == haystacks["project"] or needle in haystacks["project"]:
            score += 30
        if needle == haystacks["doc_type"] or needle in haystacks["doc_type"]:
            score += 20
        if needle in haystacks["text"]:
            score += 10
    if normalized_question and normalized_question in haystacks["text"]:
        score += 15
    return score
