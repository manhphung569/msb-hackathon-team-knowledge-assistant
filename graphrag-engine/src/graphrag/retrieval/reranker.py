from __future__ import annotations

import re
import unicodedata

from ..processes import has_explicit_process_code, identify_process_codes
from ..types import Chunk
from .exact_retriever import extract_exact_terms
from .keyword_retriever import extract_keyword_terms

_PEOPLE_HINTS = (
    "ai ",
    "phu trach",
    "dau moi",
    "spoc",
    "tham gia",
)
_TIMELINE_HINTS = (
    "timeline",
    "tien do",
    "ke hoach",
    "moc",
    "deadline",
    "bao gio",
    "khi nao",
    "thoi diem",
)
_OPS_HINTS = (
    "golive",
    "go live",
    "ho so",
    "thu tuc",
    "review",
    "handover",
    "van hanh",
    "blocker",
)
_GENERIC_PROCESS_TERMS = {
    "quy",
    "trinh",
    "golive",
    "go",
    "live",
    "production",
    "uat",
    "release",
    "test",
    "ho",
    "so",
    "thu",
    "tuc",
    "review",
    "van",
    "hanh",
    "handover",
    "blocker",
    "project",
    "du",
    "an",
    "tiep",
    "nhan",
    "yeu",
    "cau",
    "phat",
    "trien",
    "cong",
    "nghe",
    "fast",
    "lane",
    "demand",
    "intake",
    "backlog",
    "sprint",
    "pending",
    "recent",
    "change",
    "timeline",
}
_SHORT_ANCHOR_ALLOWLIST = {"ocr", "sdk", "tch", "cmp", "cdp", "api"}


def _strip_accents(text: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(ch) != "Mn"
    )


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", _strip_accents(text)).strip()


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", _normalize(text))


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in text for phrase in phrases)


_PROCESS_DOC_RE = re.compile(r"qt[.\s]*it[.\s]*\d{3}")


def _is_process_doc(path: str) -> bool:
    # Truoc day check prefix "02_work/msb/qt" (dung khi con 1 cay normalized/ chung) - kiem
    # chung qua git history cho thay file QT.IT.005/009/019 gio nam PHANG trong
    # tenants/msb/_dept/normalized/ (khong con thu muc con nao), nen ca "/qt" lan prefix cu deu
    # KHONG con khop - reranker mat hoan toan tin hieu process-doc, phat hien khi review Phase 5
    # (2026-08-25). Doi sang nhan dien qua chinh ma quy trinh trong ten file, khong phu thuoc vi
    # tri thu muc - khop moi kieu viet "QT IT 005"/"QT.IT.009"/"QTIT019".
    return bool(_PROCESS_DOC_RE.search(path))


def _anchor_terms(keyword_terms: list[str]) -> list[str]:
    return [
        term
        for term in keyword_terms
        if term not in _GENERIC_PROCESS_TERMS and (len(term) >= 4 or term in _SHORT_ANCHOR_ALLOWLIST)
    ]


def _count_anchor_matches(term: str, path: str, section: str, project: str, text: str) -> int:
    matches = 0
    if term in project:
        matches += 3
    if term in path:
        matches += 3
    if term in section:
        matches += 2
    if term in text:
        matches += 1
    return matches


def _query_context(question_norm: str, keyword_terms: list[str], process_codes: list[str]) -> dict:
    return {
        "explicit_process_code": has_explicit_process_code(question_norm),
        "people_query": _contains_any(question_norm, _PEOPLE_HINTS),
        "timeline_query": _contains_any(question_norm, _TIMELINE_HINTS),
        "ops_query": _contains_any(question_norm, _OPS_HINTS),
        "anchor_terms": _anchor_terms(keyword_terms),
        "process_codes": process_codes,
    }


def rerank_chunks(question: str, chunks: list[Chunk], top_n: int) -> list[Chunk]:
    if len(chunks) <= 1:
        return chunks

    exact_terms = [_normalize(term) for term in extract_exact_terms(question)]
    keyword_terms = [_normalize(term) for term in extract_keyword_terms(question)]
    process_codes = identify_process_codes(question)
    question_norm = _normalize(question)
    query_ctx = _query_context(question_norm, keyword_terms, process_codes)

    scored: list[tuple[int, int, Chunk]] = []
    for index, chunk in enumerate(chunks):
        score = _score_chunk(chunk, exact_terms, keyword_terms, question_norm, query_ctx)
        scored.append((score, index, chunk))

    scored.sort(key=lambda item: (-item[0], item[1]))
    reranked = [chunk for _, _, chunk in scored]
    if top_n > 0:
        head = reranked[:top_n]
        tail = reranked[top_n:]
        return head + tail
    return reranked


def _score_chunk(
    chunk: Chunk, exact_terms: list[str], keyword_terms: list[str], question_norm: str, query_ctx: dict
) -> int:
    path = _normalize(chunk.source_path)
    path_squashed = _squash(chunk.source_path)
    section = _normalize(chunk.section or "")
    project = _normalize(chunk.project)
    doc_type = _normalize(chunk.doc_type)
    text = _normalize(chunk.text)
    channel = _normalize(chunk.source_channel)
    reliability = _normalize(chunk.reliability)
    ticket_ids = [_normalize(ticket) for ticket in chunk.ticket_ids]
    people = [_normalize(name) for name in chunk.people_mentions]
    process_doc = _is_process_doc(path)

    score = 0
    if question_norm and question_norm in text:
        score += 30
    if question_norm and question_norm in path:
        score += 80

    if query_ctx["process_codes"]:
        for code in query_ctx["process_codes"]:
            code_squashed = _squash(code)
            if code_squashed and code_squashed in path_squashed:
                score += 260
            if code_squashed and code_squashed in _squash(section):
                score += 70
        if process_doc and (
            query_ctx["explicit_process_code"]
            or not query_ctx["anchor_terms"]
            or not (query_ctx["people_query"] or query_ctx["timeline_query"])
        ):
            score += 90

    for term in exact_terms:
        score += _field_score(
            term, ticket_ids, people, path, section, project, doc_type, channel, reliability, text, exact=True
        )
    for term in keyword_terms:
        score += _field_score(
            term, ticket_ids, people, path, section, project, doc_type, channel, reliability, text, exact=False
        )

    anchor_match_score = 0
    for term in query_ctx["anchor_terms"]:
        anchor_match_score += _count_anchor_matches(term, path, section, project, text)
    if anchor_match_score:
        score += anchor_match_score * 20
        if not process_doc:
            score += min(anchor_match_score, 8) * 10

    if query_ctx["people_query"]:
        if people:
            score += min(len(people), 4) * 20
        if any(cue in text for cue in ("phu trach", "dau moi", "spoc", "review ho so", "thu tuc golive")):
            score += 90
        if process_doc and not query_ctx["explicit_process_code"] and query_ctx["anchor_terms"]:
            score -= 170

    if query_ctx["timeline_query"]:
        if any(cue in path for cue in ("cap nhat", "tien do", "mom", "ho chieu", "ocr", "golive")):
            score += 70
        if process_doc and not query_ctx["explicit_process_code"] and query_ctx["anchor_terms"]:
            score -= 160

    if query_ctx["ops_query"] and query_ctx["anchor_terms"]:
        if not process_doc:
            score += 50
        if process_doc and not query_ctx["explicit_process_code"] and anchor_match_score == 0:
            score -= 200

    if chunk.ticket_ids:
        score += min(len(chunk.ticket_ids), 3) * 3
    if chunk.people_mentions:
        score += min(len(chunk.people_mentions), 4) * 2
    return score


def _field_score(
    term: str,
    ticket_ids: list[str],
    people: list[str],
    path: str,
    section: str,
    project: str,
    doc_type: str,
    channel: str,
    reliability: str,
    text: str,
    *,
    exact: bool,
) -> int:
    score = 0
    ticket_weight = 140 if exact else 70
    people_weight = 80 if exact else 35
    path_weight = 70 if exact else 30
    section_weight = 40 if exact else 18
    project_weight = 30 if exact else 14
    doc_type_weight = 18 if exact else 8
    text_weight = 16 if exact else 10
    meta_weight = 8 if exact else 4

    if any(term == ticket or term in ticket for ticket in ticket_ids):
        score += ticket_weight
    if any(term == person or term in person for person in people):
        score += people_weight
    if term == path or path.endswith("/" + term) or term in path:
        score += path_weight
    if term and term in section:
        score += section_weight
    if term and term in project:
        score += project_weight
    if term and term in doc_type:
        score += doc_type_weight
    if term and term in channel:
        score += meta_weight
    if term and term in reliability:
        score += meta_weight
    if term and term in text:
        score += text_weight
    return score
