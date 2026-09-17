from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings

# Ví dụ 1 dòng ALERTS.md, dạng mới nhất (STATUS từ 2026-08-14, PIC/Role từ 2026-08-14):
#   - [2026-08-14 00:00] [approval-verification] [CAO] [MO] [PIC: anh Tuấn (EA)] Claim "..." — xem .../change-cr-log.md
# Dạng cũ hơn (thiếu [STATUS] và/hoặc [PIC: ...]) vẫn parse được — coi như [MO]/"Chưa gán" mặc
# định, để không phải sửa lại các dòng đã ghi trước đó (ALERTS.md là append-only, xem CLAUDE.md).
_ALERT_LINE_RE = re.compile(
    r"^- \[(?P<time>[^\]]+)\] \[(?P<agent>[^\]]+)\] \[(?P<severity>[^\]]+)\]"
    r"(?: \[(?P<status>MO|DA_XU_LY)\])?"
    r"(?: \[PIC: (?P<pic>[^\]]+)\])? (?P<message>.+)$"
)
_FRONTMATTER_PROJECT_RE = re.compile(r"^project:\s*(.+)$", re.MULTILINE)


def _alert_id(ticket: str, agent: str, message: str) -> str:
    """ID ổn định cho 1 alert, dùng để nối confirmation -> đúng dòng ALERTS.md. Tính từ
    (ticket, agent, message) — KHÔNG gồm status/time, để id không đổi khi agent sau này sửa dòng
    từ [MO] thành [DA_XU_LY] (message giữ nguyên, chỉ thêm/đổi status token)."""
    raw = f"{ticket}|{agent}|{message}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:12]


def list_alerts(settings: Settings) -> list[dict]:
    """Gom mọi dòng cảnh báo từ toàn bộ `_project-logs/ALERTS.md` dưới normalized_dir, mới nhất
    trước. Đây là nguồn "cảnh báo" thật cho /agents-ui — parse trực tiếp file trên đĩa, không cache,
    vì ALERTS.md nhỏ và ít file (1 file/dự án đang có _project-logs)."""
    alerts: list[dict] = []
    for alerts_path in sorted(settings.normalized_dir.rglob("ALERTS.md")):
        ticket_dir = alerts_path.parent.parent  # cha của _project-logs/
        rel_ticket = ticket_dir.relative_to(settings.normalized_dir).as_posix()
        text = alerts_path.read_text(encoding="utf-8", errors="replace")
        project_match = _FRONTMATTER_PROJECT_RE.search(text)
        project = project_match.group(1).strip() if project_match else rel_ticket.split("/")[0]
        for line in text.splitlines():
            m = _ALERT_LINE_RE.match(line.strip())
            if not m:
                continue
            agent = m.group("agent").strip()
            message = m.group("message").strip()
            alerts.append(
                {
                    "alert_id": _alert_id(rel_ticket, agent, message),
                    "time": m.group("time").strip(),
                    "agent": agent,
                    "severity": m.group("severity").strip(),
                    "status": m.group("status") or "MO",
                    "pic": (m.group("pic") or "").strip() or "Chưa gán",
                    "message": message,
                    "project": project,
                    "ticket": rel_ticket,
                    "source_path": alerts_path.relative_to(settings.normalized_dir).as_posix(),
                }
            )
    alerts.sort(key=lambda a: a["time"], reverse=True)
    return alerts


def list_agent_runs(settings: Settings, limit: int = 100) -> list[dict]:
    """Đọc run-log JSONL do orchestrator ghi qua scripts/agent_run_log.py (mỗi lần orchestrator
    được gọi -> 1 dòng), mới nhất trước. Trả [] nếu chưa có lần chạy nào được ghi."""
    p = settings.cache_dir / "agent_runs.jsonl"
    if not p.exists():
        return []
    lines = [line for line in p.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    entries: list[dict] = []
    for line in lines[-limit:]:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    entries.reverse()
    return entries


def _confirmations_path(settings: Settings) -> Path:
    # settings.cache_dir đã là <engine_root>/data/cache — cùng thư mục mà scripts/agent_confirmations.py
    # (standalone, tự tính root theo cách riêng) cũng ghi vào, nên 2 phía luôn đọc/ghi chung 1 file.
    path = settings.cache_dir / "agent_confirmations.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def list_confirmations(settings: Settings) -> list[dict]:
    p = _confirmations_path(settings)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def add_confirmation(
    settings: Settings,
    *,
    alert_id: str,
    project: str,
    ticket: str,
    agent: str,
    message: str,
    note: str,
) -> dict:
    """Xếp hàng 1 xác nhận của người dùng cho 1 alert cụ thể — CHỈ ghi hàng chờ, KHÔNG tự xác
    minh gì cả (server không có Read/Grep/đánh giá bằng chứng như agent thật). Lần kế tiếp agent
    tương ứng chạy (qua scripts/agent_confirmations.py list-pending), nó mới thực sự đối chiếu và
    quyết định resolved/still-open."""
    entries = list_confirmations(settings)
    confirmation_id = f"conf_{int(time.time())}_{secrets.token_hex(3)}"
    entry = {
        "confirmation_id": confirmation_id,
        "alert_id": alert_id,
        "project": project,
        "ticket": ticket,
        "agent": agent,
        "alert_message": message,
        "note": note,
        "submitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "pending",
        "processed_at": None,
        "processed_note": None,
    }
    entries.append(entry)
    _confirmations_path(settings).write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")
    return entry


def build_agents_report(settings: Settings) -> dict:
    alerts = list_alerts(settings)
    runs = list_agent_runs(settings)
    confirmations = list_confirmations(settings)
    by_severity: dict[str, int] = {}
    open_count = 0
    for a in alerts:
        by_severity[a["severity"]] = by_severity.get(a["severity"], 0) + 1
        if a["status"] == "MO":
            open_count += 1
    pending_confirmations = sum(1 for c in confirmations if c["status"] == "pending")
    return {
        "summary": {
            "total_alerts": len(alerts),
            "open_alerts": open_count,
            "alerts_by_severity": by_severity,
            "total_runs": len(runs),
            "last_run_time": runs[0]["time"] if runs else None,
            "pending_confirmations": pending_confirmations,
        },
        "alerts": alerts,
        "runs": runs,
        "confirmations": confirmations,
    }
