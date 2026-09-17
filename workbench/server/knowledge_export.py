"""Xuất backlog item (SQLite, workbench) thành note .md trong `normalized/_workbench-backlog/`
của tenant demo graphrag-engine — đúng "ngoại lệ note" đã dùng cho chat-notes/zalo-notes/Jira
sync (không có file gốc trong artifacts/, ghi thẳng vào normalized/). Đây là chiều dữ liệu
ngược lại với graphrag_client.py: workbench GHI vào index thay vì chỉ ĐỌC.

Best-effort, KHÔNG BAO GIỜ raise — 1 request CRUD backlog thật (tạo/sửa/xoá item) không được
phép 500 chỉ vì đường dẫn tenant demo bị cấu hình sai hoặc ổ đĩa lỗi; lỗi chỉ log ra console.
Reindex (graphrag build) là thủ công cho bản hackathon này — xem workbench/SMOKE_TEST_HACKATHON.md."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml

from .config import GRAPHRAG_DEMO_TENANT_ROOT


def _out_dir() -> Path:
    return GRAPHRAG_DEMO_TENANT_ROOT / "normalized" / "_workbench-backlog"


def export_backlog_item(item_row: dict) -> None:
    try:
        item_id = item_row["id"]
        title = str(item_row.get("title") or "").strip() or f"Backlog item {item_id}"
        description = str(item_row.get("description") or "").strip() or "(không có mô tả)"
        status = str(item_row.get("status") or "open")
        priority = str(item_row.get("priority") or "medium")
        category = str(item_row.get("category") or "")
        ai_reasoning = str(item_row.get("ai_reasoning") or "").strip()

        meta = {
            "source": "workbench-backlog",
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "project": GRAPHRAG_DEMO_TENANT_ROOT.name,
            "doc_type": "backlog",
            "backlog_item_id": item_id,
            "status": status,
            "priority": priority,
        }
        frontmatter_yaml = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False, default_flow_style=False)

        body_parts = [
            f"## {title}",
            f"**Priority:** {priority} | **Category:** {category or 'n/a'} | **Status:** {status}",
            "",
            "## Mô tả",
            description,
        ]
        if ai_reasoning:
            body_parts += ["", "## AI reasoning", ai_reasoning]

        content = f"---\n{frontmatter_yaml}---\n\n" + "\n".join(body_parts) + "\n"

        out_dir = _out_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{item_id}.md").write_text(content, encoding="utf-8")
    except Exception as e:
        print(f"[Vault] WARN knowledge_export.export_backlog_item({item_row.get('id')}): {e}")


def delete_backlog_item_export(item_id: int) -> None:
    try:
        path = _out_dir() / f"{item_id}.md"
        if path.exists():
            path.unlink()
    except Exception as e:
        print(f"[Vault] WARN knowledge_export.delete_backlog_item_export({item_id}): {e}")
