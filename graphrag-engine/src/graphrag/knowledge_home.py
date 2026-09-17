from __future__ import annotations

from .config import Settings
from .ingestion.health import build_health_report
from .ingestion.loader import iter_documents
from .knowledge_links import _EXPECTED_PROCESS_FLOW, build_knowledge_links_report


def build_knowledge_home(settings: Settings) -> dict:
    docs = list(iter_documents(settings))
    health = build_health_report(settings)
    links = build_knowledge_links_report(settings)

    recent_documents = sorted(
        (
            {
                "source_path": doc.rel_path,
                "project": doc.project,
                "date": doc.date,
                "doc_type": doc.doc_type,
                "source_channel": doc.source_channel,
                "reliability": doc.reliability,
            }
            for doc in docs
        ),
        key=lambda item: (item["date"], item["source_path"]),
        reverse=True,
    )[:10]

    recent_chat_notes = [
        item
        for item in recent_documents
        if "/_chat-notes/" in item["source_path"] or "/_zalo-notes/" in item["source_path"]
    ][:10]

    active_projects = [
        {
            "name": item["name"],
            "doc_count": item["doc_count"],
            "backlog_doc_count": item["backlog_doc_count"],
            "latest_date": item["latest_date"],
            "process_codes": item["process_codes"],
            "missing_processes": [code for code in _EXPECTED_PROCESS_FLOW if code not in item["process_codes"]],
        }
        for item in links["projects"]
        if item["name"] and item["name"] != "general"
    ][:8]

    stale = health["stale_files"]
    attention_items: list[dict] = []
    if stale["missing_normalized"]:
        attention_items.append(
            {
                "severity": "warning",
                "title": "Thieu file normalized",
                "detail": f"{len(stale['missing_normalized'])} artifact chua co mirror trong normalized.",
            }
        )
    if stale["stale_normalized"]:
        attention_items.append(
            {
                "severity": "warning",
                "title": "Normalized dang cu hon artifact",
                "detail": f"{len(stale['stale_normalized'])} file can normalize lai.",
            }
        )
    if stale["stale_build"] or stale["missing_indexed"]:
        attention_items.append(
            {
                "severity": "critical",
                "title": "Index/build chua theo kip",
                "detail": f"{len(stale['stale_build']) + len(stale['missing_indexed'])} file dang thieu build hoac missing index.",
            }
        )
    for project in active_projects:
        if project["missing_processes"]:
            attention_items.append(
                {
                    "severity": "info",
                    "title": f"Project {project['name']} chua phu du process",
                    "detail": "Thieu: " + ", ".join(project["missing_processes"][:3]),
                }
            )
    attention_items = attention_items[:10]

    summary = {
        "documents": len(docs),
        "projects": len(links["projects"]),
        "backlog_documents": links["summary"]["backlog_documents"],
        "recent_chat_notes": len(recent_chat_notes),
        "stale_attention": len(attention_items),
        "docs_needing_build": health["summary"]["docs_needing_build"],
        "docs_needing_normalization": health["summary"]["docs_needing_normalization"],
    }

    return {
        "generated_at": health["generated_at"],
        "summary": summary,
        "attention_items": attention_items,
        "active_projects": active_projects,
        "recent_documents": recent_documents,
        "recent_chat_notes": recent_chat_notes,
    }
