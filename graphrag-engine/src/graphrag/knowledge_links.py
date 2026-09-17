from __future__ import annotations

from collections import defaultdict

from .config import Settings
from .ingestion.loader import iter_documents
from .processes import identify_process_codes
from .types import SourceDocument

_EXPECTED_PROCESS_FLOW = ["QT.IT.005", "QT.IT.009", "QT.IT.019"]
_PROCESS_GAP_HINTS = {
    "QT.IT.005": [
        "Kiểm tra evidence tiếp nhận/yêu cầu đầu vào, backlog intake, ưu tiên và phê duyệt phạm vi.",
        "Rà lại tài liệu demand, phiếu yêu cầu, hoặc dấu vết backlog đang thiếu trong kho tri thức.",
    ],
    "QT.IT.009": [
        "Kiểm tra evidence phát triển/kiểm thử như SIT, UAT, release note, test case hoặc biên bản nghiệm thu.",
        "Rà lại tài liệu chứng minh backlog đã đi qua delivery phase trước khi golive.",
    ],
    "QT.IT.019": [
        "Kiểm tra evidence triển khai/golive như hồ sơ golive, production checklist, production test và sau golive.",
        "Rà lại tài liệu production readiness để tránh thiếu chứng từ ở chặng cuối.",
    ],
}


def _safe_latest(current: str, incoming: str) -> str:
    if not current:
        return incoming
    return incoming if incoming > current else current


def _document_process_codes(doc: SourceDocument) -> list[str]:
    haystack = " ".join(
        [
            doc.rel_path,
            doc.project,
            doc.doc_type,
            doc.source_channel,
            " ".join(doc.ticket_ids),
            doc.text[:4000],
        ]
    )
    return identify_process_codes(haystack)


def _unique_sorted(values: set[str]) -> list[str]:
    return sorted(v for v in values if v)


def _process_flow_summary(process_codes: set[str]) -> dict:
    covered = [code for code in _EXPECTED_PROCESS_FLOW if code in process_codes]
    missing = [code for code in _EXPECTED_PROCESS_FLOW if code not in process_codes]
    gap_hints: list[dict] = []
    for code in missing:
        gap_hints.append(
            {
                "code": code,
                "hints": _PROCESS_GAP_HINTS.get(code, []),
            }
        )
    return {
        "expected": list(_EXPECTED_PROCESS_FLOW),
        "covered": covered,
        "missing": missing,
        "gap_hints": gap_hints,
        "coverage_ratio": f"{len(covered)}/{len(_EXPECTED_PROCESS_FLOW)}",
        "status": "complete" if not missing else ("partial" if covered else "missing"),
    }


def _document_card(doc: SourceDocument, process_codes: list[str]) -> dict:
    month_bucket = (doc.date or "")[:7] if doc.date else ""
    return {
        "source_path": doc.rel_path,
        "project": doc.project,
        "doc_type": doc.doc_type,
        "date": doc.date,
        "month_bucket": month_bucket,
        "source_channel": doc.source_channel,
        "reliability": doc.reliability,
        "ticket_ids": doc.ticket_ids,
        "people_mentions": doc.people_mentions[:10],
        "process_codes": process_codes,
    }


def _overlap_count(values: set[str], candidates: list[str]) -> int:
    if not values:
        return 0
    return sum(1 for item in candidates if item and item in values)


def _build_backlink_groups(
    project_hits: dict[str, int],
    ticket_hits: dict[str, int],
    people_hits: dict[str, int],
    channel_hits: dict[str, int],
    month_hits: dict[str, int],
) -> list[dict]:
    def to_items(counter: dict[str, int], limit: int = 8) -> list[dict]:
        ranked = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
        return [{"id": key, "count": count} for key, count in ranked[:limit] if key]

    groups = [
        {"label": "project", "items": to_items(project_hits)},
        {"label": "ticket", "items": to_items(ticket_hits)},
        {"label": "people", "items": to_items(people_hits)},
        {"label": "channel", "items": to_items(channel_hits)},
        {"label": "month", "items": to_items(month_hits)},
    ]
    return [group for group in groups if group["items"]]


def _build_related_documents(
    docs: list[tuple[SourceDocument, list[str]]],
    anchor_type: str,
    anchor_id: str,
    related_projects: set[str],
    related_tickets: set[str],
    related_people: set[str],
    related_processes: set[str],
    related_channels: set[str],
    month_hits: set[str],
) -> list[dict]:
    candidates: list[dict] = []
    for doc, process_codes in docs:
        doc_month = (doc.date or "")[:7] if doc.date else "unknown"
        if anchor_type == "project" and doc.project == anchor_id:
            continue
        if anchor_type == "ticket" and anchor_id in doc.ticket_ids:
            continue
        if anchor_type == "person" and anchor_id in doc.people_mentions:
            continue
        if anchor_type == "channel" and (doc.source_channel or "unknown") == anchor_id:
            continue
        if anchor_type == "month" and doc_month == anchor_id:
            continue

        reasons: list[str] = []
        score = 0

        if doc.project and doc.project in related_projects:
            score += 3
            reasons.append(f"same project: {doc.project}")

        shared_tickets = [ticket for ticket in doc.ticket_ids if ticket in related_tickets]
        if shared_tickets:
            score += 4 * len(shared_tickets)
            reasons.append("shared ticket: " + ", ".join(shared_tickets[:3]))

        shared_people = [person for person in doc.people_mentions if person in related_people]
        if shared_people:
            score += 2 * len(shared_people[:3])
            reasons.append("shared people: " + ", ".join(shared_people[:3]))

        shared_processes = [code for code in process_codes if code in related_processes]
        if shared_processes:
            score += len(shared_processes)
            reasons.append("shared process: " + ", ".join(shared_processes[:3]))

        if (doc.source_channel or "unknown") in related_channels:
            score += 1
            reasons.append("same channel: " + (doc.source_channel or "unknown"))

        if doc_month in month_hits:
            score += 1
            reasons.append("same month: " + doc_month)

        if score <= 0:
            continue

        card = _document_card(doc, process_codes)
        card["related_score"] = score
        card["reasons"] = reasons[:4]
        candidates.append(card)

    candidates.sort(key=lambda item: (-item["related_score"], item["date"], item["source_path"]))
    return candidates[:12]


def _collect_overlap_backlinks(
    docs: list[tuple[SourceDocument, list[str]]],
    projects: set[str],
    tickets: set[str],
    people: set[str],
    channels: set[str],
    months: set[str],
) -> list[dict]:
    project_hits: dict[str, int] = defaultdict(int)
    ticket_hits: dict[str, int] = defaultdict(int)
    people_hits: dict[str, int] = defaultdict(int)
    channel_hits: dict[str, int] = defaultdict(int)
    month_hits: dict[str, int] = defaultdict(int)

    for doc, _process_codes in docs:
        doc_channel = doc.source_channel or "unknown"
        doc_month = (doc.date or "")[:7] if doc.date else "unknown"
        matched = False

        if doc.project and doc.project in projects:
            project_hits[doc.project] += 1
            matched = True
        for ticket in doc.ticket_ids:
            if ticket in tickets:
                ticket_hits[ticket] += 1
                matched = True
        for person in doc.people_mentions:
            if person in people:
                people_hits[person] += 1
                matched = True
        if doc_channel in channels:
            channel_hits[doc_channel] += 1
            matched = True
        if doc_month in months:
            month_hits[doc_month] += 1
            matched = True

        if not matched:
            continue

    return _build_backlink_groups(project_hits, ticket_hits, people_hits, channel_hits, month_hits)


def _collect_documents(settings: Settings) -> list[tuple[SourceDocument, list[str]]]:
    docs: list[tuple[SourceDocument, list[str]]] = []
    for doc in iter_documents(settings):
        docs.append((doc, _document_process_codes(doc)))
    return docs


def build_knowledge_links_report(settings: Settings) -> dict:
    docs = _collect_documents(settings)
    project_map: dict[str, dict] = {}
    ticket_map: dict[str, dict] = {}
    person_map: dict[str, dict] = {}
    channel_map: dict[str, dict] = {}
    month_map: dict[str, dict] = {}
    backlog_docs: list[dict] = []

    for doc, process_codes in docs:
        project = doc.project or "general"
        channel = doc.source_channel or "unknown"
        month_bucket = (doc.date or "")[:7] if doc.date else "unknown"
        project_state = project_map.setdefault(
            project,
            {
                "name": project,
                "doc_count": 0,
                "ticket_ids": set(),
                "people_mentions": set(),
                "latest_date": "",
                "backlog_doc_count": 0,
                "process_codes": set(),
            },
        )
        project_state["doc_count"] += 1
        project_state["ticket_ids"].update(doc.ticket_ids)
        project_state["people_mentions"].update(doc.people_mentions)
        project_state["process_codes"].update(process_codes)
        project_state["latest_date"] = _safe_latest(project_state["latest_date"], doc.date)

        is_backlogish = doc.doc_type == "backlog" or bool(doc.ticket_ids)
        if is_backlogish:
            project_state["backlog_doc_count"] += 1
            backlog_docs.append(_document_card(doc, process_codes))

        channel_state = channel_map.setdefault(
            channel,
            {
                "name": channel,
                "doc_count": 0,
                "projects": set(),
                "ticket_ids": set(),
                "people_mentions": set(),
                "latest_date": "",
                "process_codes": set(),
            },
        )
        channel_state["doc_count"] += 1
        channel_state["projects"].add(project)
        channel_state["ticket_ids"].update(doc.ticket_ids)
        channel_state["people_mentions"].update(doc.people_mentions)
        channel_state["process_codes"].update(process_codes)
        channel_state["latest_date"] = _safe_latest(channel_state["latest_date"], doc.date)

        month_state = month_map.setdefault(
            month_bucket,
            {
                "name": month_bucket,
                "doc_count": 0,
                "projects": set(),
                "ticket_ids": set(),
                "people_mentions": set(),
                "source_channels": set(),
                "latest_date": "",
                "process_codes": set(),
            },
        )
        month_state["doc_count"] += 1
        month_state["projects"].add(project)
        month_state["ticket_ids"].update(doc.ticket_ids)
        month_state["people_mentions"].update(doc.people_mentions)
        month_state["source_channels"].add(channel)
        month_state["process_codes"].update(process_codes)
        month_state["latest_date"] = _safe_latest(month_state["latest_date"], doc.date)

        for ticket_id in doc.ticket_ids:
            state = ticket_map.setdefault(
                ticket_id,
                {
                    "id": ticket_id,
                    "doc_count": 0,
                    "projects": set(),
                    "people_mentions": set(),
                    "doc_types": set(),
                    "source_channels": set(),
                    "latest_date": "",
                    "reliability": set(),
                    "process_codes": set(),
                },
            )
            state["doc_count"] += 1
            state["projects"].add(project)
            state["people_mentions"].update(doc.people_mentions)
            state["doc_types"].add(doc.doc_type)
            state["source_channels"].add(doc.source_channel)
            state["reliability"].add(doc.reliability)
            state["process_codes"].update(process_codes)
            state["latest_date"] = _safe_latest(state["latest_date"], doc.date)

        for person in doc.people_mentions:
            state = person_map.setdefault(
                person,
                {
                    "name": person,
                    "doc_count": 0,
                    "projects": set(),
                    "ticket_ids": set(),
                    "latest_date": "",
                    "source_channels": set(),
                    "process_codes": set(),
                },
            )
            state["doc_count"] += 1
            state["projects"].add(project)
            state["ticket_ids"].update(doc.ticket_ids)
            state["source_channels"].add(doc.source_channel)
            state["process_codes"].update(process_codes)
            state["latest_date"] = _safe_latest(state["latest_date"], doc.date)

    projects = [
        {
            "name": state["name"],
            "doc_count": state["doc_count"],
            "ticket_count": len(state["ticket_ids"]),
            "people_count": len(state["people_mentions"]),
            "latest_date": state["latest_date"],
            "backlog_doc_count": state["backlog_doc_count"],
            "process_codes": _unique_sorted(state["process_codes"]),
        }
        for state in project_map.values()
    ]
    projects.sort(key=lambda item: (-item["backlog_doc_count"], -item["doc_count"], item["name"]))

    tickets = [
        {
            "id": state["id"],
            "doc_count": state["doc_count"],
            "projects": _unique_sorted(state["projects"]),
            "people_count": len(state["people_mentions"]),
            "doc_types": _unique_sorted(state["doc_types"]),
            "source_channels": _unique_sorted(state["source_channels"]),
            "latest_date": state["latest_date"],
            "reliability": _unique_sorted(state["reliability"]),
            "process_codes": _unique_sorted(state["process_codes"]),
        }
        for state in ticket_map.values()
    ]
    tickets.sort(key=lambda item: (-item["doc_count"], item["id"]))

    people = [
        {
            "name": state["name"],
            "doc_count": state["doc_count"],
            "projects": _unique_sorted(state["projects"]),
            "ticket_count": len(state["ticket_ids"]),
            "latest_date": state["latest_date"],
            "source_channels": _unique_sorted(state["source_channels"]),
            "process_codes": _unique_sorted(state["process_codes"]),
        }
        for state in person_map.values()
    ]
    people.sort(key=lambda item: (-item["doc_count"], item["name"]))

    channels = [
        {
            "name": state["name"],
            "doc_count": state["doc_count"],
            "projects": _unique_sorted(state["projects"]),
            "ticket_count": len(state["ticket_ids"]),
            "people_count": len(state["people_mentions"]),
            "latest_date": state["latest_date"],
            "process_codes": _unique_sorted(state["process_codes"]),
        }
        for state in channel_map.values()
    ]
    channels.sort(key=lambda item: (-item["doc_count"], item["name"]))

    months = [
        {
            "name": state["name"],
            "doc_count": state["doc_count"],
            "projects": _unique_sorted(state["projects"]),
            "ticket_count": len(state["ticket_ids"]),
            "people_count": len(state["people_mentions"]),
            "source_channels": _unique_sorted(state["source_channels"]),
            "latest_date": state["latest_date"],
            "process_codes": _unique_sorted(state["process_codes"]),
        }
        for state in month_map.values()
    ]
    months.sort(key=lambda item: item["name"], reverse=True)

    backlog_docs.sort(key=lambda item: (item["date"], item["source_path"]), reverse=True)

    return {
        "summary": {
            "documents": len(docs),
            "projects": len(projects),
            "tickets": len(tickets),
            "people": len(people),
            "channels": len(channels),
            "months": len(months),
            "backlog_documents": len(backlog_docs),
            "expected_process_flow": list(_EXPECTED_PROCESS_FLOW),
        },
        "projects": projects,
        "tickets": tickets,
        "people": people,
        "channels": channels,
        "months": months,
        "backlog_documents": backlog_docs[:60],
    }


def build_knowledge_anchor_detail(settings: Settings, anchor_type: str, anchor_id: str) -> dict:
    docs = _collect_documents(settings)
    matches: list[dict] = []
    related_projects: set[str] = set()
    related_tickets: set[str] = set()
    related_people: set[str] = set()
    related_processes: set[str] = set()
    related_channels: set[str] = set()
    related_months: set[str] = set()
    project_hits: dict[str, int] = defaultdict(int)
    ticket_hits: dict[str, int] = defaultdict(int)
    people_hits: dict[str, int] = defaultdict(int)
    channel_hits: dict[str, int] = defaultdict(int)
    month_hits: dict[str, int] = defaultdict(int)
    latest_date = ""

    for doc, process_codes in docs:
        matched = False
        doc_channel = doc.source_channel or "unknown"
        doc_month = (doc.date or "")[:7] if doc.date else "unknown"
        if anchor_type == "project":
            matched = doc.project == anchor_id
        elif anchor_type == "ticket":
            matched = anchor_id in doc.ticket_ids
        elif anchor_type == "person":
            matched = anchor_id in doc.people_mentions
        elif anchor_type == "channel":
            matched = doc_channel == anchor_id
        elif anchor_type == "month":
            matched = doc_month == anchor_id
        if not matched:
            continue

        matches.append(_document_card(doc, process_codes))
        related_projects.add(doc.project)
        related_tickets.update(doc.ticket_ids)
        related_people.update(doc.people_mentions)
        related_processes.update(process_codes)
        related_channels.add(doc_channel)
        related_months.add(doc_month)
        if doc.project:
            project_hits[doc.project] += 1
        channel_hits[doc_channel] += 1
        month_hits[doc_month] += 1
        for ticket in doc.ticket_ids:
            ticket_hits[ticket] += 1
        for person in doc.people_mentions:
            people_hits[person] += 1
        latest_date = _safe_latest(latest_date, doc.date)

    matches.sort(key=lambda item: (item["date"], item["source_path"]), reverse=True)
    flow = _process_flow_summary(related_processes)
    backlinks = _build_backlink_groups(project_hits, ticket_hits, people_hits, channel_hits, month_hits)
    related_documents = _build_related_documents(
        docs=docs,
        anchor_type=anchor_type,
        anchor_id=anchor_id,
        related_projects=related_projects,
        related_tickets=related_tickets,
        related_people=related_people,
        related_processes=related_processes,
        related_channels=related_channels,
        month_hits=related_months,
    )
    return {
        "anchor_type": anchor_type,
        "anchor_id": anchor_id,
        "doc_count": len(matches),
        "latest_date": latest_date,
        "related_projects": _unique_sorted(related_projects),
        "related_tickets": _unique_sorted(related_tickets),
        "related_people": _unique_sorted(related_people)[:40],
        "related_process_codes": _unique_sorted(related_processes),
        "related_source_channels": _unique_sorted(related_channels),
        "related_months": _unique_sorted(related_months),
        "process_flow": flow,
        "backlink_groups": backlinks,
        "related_documents": related_documents,
        "documents": matches,
    }


def build_document_detail(settings: Settings, source_path: str) -> dict:
    docs = _collect_documents(settings)
    target_doc: SourceDocument | None = None
    target_process_codes: list[str] = []

    for doc, process_codes in docs:
        if doc.rel_path == source_path:
            target_doc = doc
            target_process_codes = process_codes
            break

    if target_doc is None:
        raise KeyError(source_path)

    doc_channel = target_doc.source_channel or "unknown"
    doc_month = (target_doc.date or "")[:7] if target_doc.date else "unknown"
    related_projects = {target_doc.project} if target_doc.project else set()
    related_tickets = set(target_doc.ticket_ids)
    related_people = set(target_doc.people_mentions)
    related_processes = set(target_process_codes)
    related_channels = {doc_channel}
    related_months = {doc_month}

    backlinks = _collect_overlap_backlinks(
        docs=docs,
        projects=related_projects,
        tickets=related_tickets,
        people=related_people,
        channels=related_channels,
        months=related_months,
    )
    related_documents = _build_related_documents(
        docs=docs,
        anchor_type="document",
        anchor_id=source_path,
        related_projects=related_projects,
        related_tickets=related_tickets,
        related_people=related_people,
        related_processes=related_processes,
        related_channels=related_channels,
        month_hits=related_months,
    )
    preview = target_doc.text[:12000]
    return {
        "source_path": target_doc.rel_path,
        "project": target_doc.project,
        "doc_type": target_doc.doc_type,
        "date": target_doc.date,
        "month_bucket": doc_month,
        "source_channel": doc_channel,
        "reliability": target_doc.reliability,
        "ticket_ids": list(target_doc.ticket_ids),
        "people_mentions": list(target_doc.people_mentions[:40]),
        "process_codes": list(target_process_codes),
        "process_flow": _process_flow_summary(related_processes),
        "backlink_groups": backlinks,
        "related_documents": related_documents,
        "line_count": len(target_doc.text.splitlines()),
        "content_preview": preview,
        "content_truncated": len(target_doc.text) > len(preview),
    }
