from __future__ import annotations

from collections import Counter

from .config import Settings
from .ingestion.loader import iter_documents


def _norm(value: str) -> str:
    return (value or "").strip().lower()


def _matches_terms(haystacks: list[str], query: str) -> bool:
    terms = [term for term in _norm(query).split() if term]
    if not terms:
        return True
    joined = "\n".join(_norm(item) for item in haystacks if item)
    return all(term in joined for term in terms)


def _build_snippet(text: str, query: str, limit: int = 240) -> str:
    body = " ".join((text or "").split())
    if not body:
        return ""
    if not query.strip():
        return body[:limit]
    lowered = body.lower()
    for term in [item for item in query.lower().split() if item]:
        idx = lowered.find(term)
        if idx >= 0:
            start = max(0, idx - 80)
            end = min(len(body), idx + limit - 80)
            snippet = body[start:end].strip()
            if start > 0:
                snippet = "..." + snippet
            if end < len(body):
                snippet = snippet + "..."
            return snippet
    return body[:limit]


def _top_values(counter: Counter[str], limit: int = 12) -> list[str]:
    return [item for item, _count in counter.most_common(limit) if item]


def search_documents(
    settings: Settings,
    *,
    q: str = "",
    project: str = "",
    source_channel: str = "",
    reliability: str = "",
    ticket: str = "",
    person: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = 40,
) -> dict:
    docs = list(iter_documents(settings))

    project_counter: Counter[str] = Counter()
    channel_counter: Counter[str] = Counter()
    reliability_counter: Counter[str] = Counter()
    people_counter: Counter[str] = Counter()
    ticket_counter: Counter[str] = Counter()

    for doc in docs:
        if doc.project:
            project_counter[doc.project] += 1
        if doc.source_channel:
            channel_counter[doc.source_channel] += 1
        if doc.reliability:
            reliability_counter[doc.reliability] += 1
        people_counter.update(person_name for person_name in doc.people_mentions if person_name)
        ticket_counter.update(ticket_id for ticket_id in doc.ticket_ids if ticket_id)

    filters = {
        "projects": _top_values(project_counter, limit=20),
        "source_channels": _top_values(channel_counter, limit=20),
        "reliability": _top_values(reliability_counter, limit=10),
        "people": _top_values(people_counter, limit=25),
        "tickets": _top_values(ticket_counter, limit=25),
    }

    project_value = _norm(project)
    channel_value = _norm(source_channel)
    reliability_value = _norm(reliability)
    ticket_value = _norm(ticket)
    person_value = _norm(person)

    results: list[dict] = []
    for doc in docs:
        if project_value and _norm(doc.project) != project_value:
            continue
        if channel_value and _norm(doc.source_channel) != channel_value:
            continue
        if reliability_value and _norm(doc.reliability) != reliability_value:
            continue
        if ticket_value and ticket_value not in {_norm(item) for item in doc.ticket_ids}:
            continue
        if person_value and person_value not in {_norm(item) for item in doc.people_mentions}:
            continue
        if date_from and doc.date and doc.date < date_from:
            continue
        if date_to and doc.date and doc.date > date_to:
            continue
        if not _matches_terms(
            [
                doc.rel_path,
                doc.project,
                doc.doc_type,
                doc.source_channel,
                doc.reliability,
                " ".join(doc.ticket_ids),
                " ".join(doc.people_mentions),
                doc.text,
            ],
            q,
        ):
            continue

        results.append(
            {
                "source_path": doc.rel_path,
                "project": doc.project,
                "doc_type": doc.doc_type,
                "date": doc.date,
                "source_channel": doc.source_channel,
                "reliability": doc.reliability,
                "ticket_ids": doc.ticket_ids[:8],
                "people_mentions": doc.people_mentions[:8],
                "line_count": len(doc.text.splitlines()),
                "snippet": _build_snippet(doc.text, q),
            }
        )

    results.sort(key=lambda item: (item["date"], item["source_path"]), reverse=True)
    applied = {
        "q": q,
        "project": project,
        "source_channel": source_channel,
        "reliability": reliability,
        "ticket": ticket,
        "person": person,
        "date_from": date_from,
        "date_to": date_to,
        "limit": limit,
    }
    return {
        "summary": {
            "total_documents": len(docs),
            "result_count": min(len(results), limit),
            "matched_before_limit": len(results),
        },
        "applied_filters": applied,
        "available_filters": filters,
        "results": results[:limit],
    }
