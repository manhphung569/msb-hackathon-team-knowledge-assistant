from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import yaml

from .config import PROJECT_ROOT

TENANTS_ROOT = PROJECT_ROOT.parent / "tenants"
REGISTRY_PATH = TENANTS_ROOT / "registry.yaml"

# Thu muc con KHONG tinh la "1 doc" khi dem so luong hien thi tren sidebar/file tree - day la
# noi dung ho tro (chat-note/zalo-note/nhat ky du an), khong phai tai lieu goc cua du an.
_NOTE_DIR_NAMES = {"_chat-notes", "_zalo-notes", "_project-logs", "_scratch-analysis"}


def _count_md(root: Path) -> int:
    if not root.is_dir():
        return 0
    return sum(1 for p in root.rglob("*.md") if p.name != "README.md")


def _tenant_node(org_id: str, workspace_id: str | None, rel_path: str, note: str = "") -> dict:
    """Xay 1 node tenant/workspace - tu phat hien co du lieu truc tiep (normalized/) hay chi la
    thu muc cha chua instances/<id>/ (vd tenants/minmo/saleman-legacy/instances/ibd) - khong doc
    cung status: empty khai bao trong registry.yaml, luon tu kiem tra dia chi thuc te tren dia."""
    root = TENANTS_ROOT / Path(rel_path).relative_to("tenants")
    normalized_dir = root / "normalized"
    node = {
        "org_id": org_id,
        "workspace_id": workspace_id,
        "tenant_id": rel_path.removeprefix("tenants/"),
        "path": rel_path,
        "note": note,
    }
    if normalized_dir.is_dir():
        node["status"] = "active"
        node["doc_count"] = _count_md(normalized_dir)
        node["children"] = []
        return node

    instances_dir = root / "instances"
    children = []
    if instances_dir.is_dir():
        for inst in sorted(p for p in instances_dir.iterdir() if p.is_dir()):
            inst_rel = f"{rel_path}/instances/{inst.name}"
            children.append(_tenant_node(org_id, inst.name, inst_rel))
    node["status"] = "parent" if children else "empty"
    node["doc_count"] = sum(c.get("doc_count", 0) for c in children)
    node["children"] = children
    return node


def list_tenant_registry() -> dict:
    """Doc tenants/registry.yaml, tra ve cay org -> workspace (kem instances long nhau neu co)
    dung de dung sidebar - moi entry tu kiem tra thuc te tren dia (khong chi tin theo field
    status khai bao trong yaml, vi field do co the loi thoi)."""
    raw = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8")) or {}
    entries = raw.get("tenants", [])

    orgs: dict[str, dict] = {}
    common_node: dict | None = None
    for entry in entries:
        org_id = entry["org_id"]
        workspace_id = entry.get("workspace_id")
        node = _tenant_node(org_id, workspace_id, entry["path"], entry.get("note", "") or "")
        if org_id == "_common":
            common_node = node
            continue
        orgs.setdefault(org_id, {"org_id": org_id, "workspaces": []})
        orgs[org_id]["workspaces"].append(node)

    return {
        "orgs": list(orgs.values()),
        "common": common_node,
    }


def _resolve_tenant_root(tenant_id: str) -> Path:
    tenants_root = TENANTS_ROOT.resolve()
    tenant_root = (tenants_root / tenant_id).resolve()
    try:
        tenant_root.relative_to(tenants_root)
    except ValueError as exc:
        raise ValueError("tenant khong hop le") from exc
    return tenant_root


def build_document_tree(tenant_id: str) -> dict:
    """Cay thu muc normalized/ cua 1 tenant - phan biet thu muc ticket (co the co
    _project-logs/) voi thu muc note (_chat-notes/_zalo-notes o cap goc tenant)."""
    tenant_root = _resolve_tenant_root(tenant_id)
    normalized_dir = tenant_root / "normalized"
    if not normalized_dir.is_dir():
        return {"tenant_id": tenant_id, "folders": [], "files": []}

    folders = []
    files = []
    for p in sorted(normalized_dir.iterdir()):
        if p.name.startswith(".") or p.name == "README.md":
            continue
        if p.is_dir():
            file_count = _count_md(p)
            has_logs = (p / "_project-logs").is_dir()
            sub_dirs = sorted(sub.name for sub in p.iterdir() if sub.is_dir()) if p.is_dir() else []
            folders.append(
                {
                    "name": p.name,
                    "file_count": file_count,
                    "has_project_logs": has_logs,
                    "is_note_dir": p.name in _NOTE_DIR_NAMES,
                    "sub_dirs": [d for d in sub_dirs if d in _NOTE_DIR_NAMES or d == "adr"],
                }
            )
        elif p.suffix == ".md":
            files.append({"name": p.name})
    return {"tenant_id": tenant_id, "folders": folders, "files": files}


_LOG_FILE_META = {
    "decision-log.md": ("📝", "Decision Log", "PMBOK/PRINCE2 — quyết định thật đã chốt"),
    "raid-log.md": ("⚠️", "RAID Log", "PMI — Risk/Assumption/Issue/Dependency + action item"),
    "raci-daci-register.md": ("👤", "RACI/DACI Register", "DACI — ai duyệt gì, đầu mối theo hạng mục"),
    "change-cr-log.md": ("🔄", "Change/CR Log", "ITIL 4 — CR lên production"),
    "milestone-log.md": ("📅", "Milestone Log", "PRINCE2 — mốc go-live, mỗi lần dời là 1 dòng mới"),
    "ALERTS.md": ("🚨", "ALERTS", "Cảnh báo mức cao từ 5 agent giám sát"),
    "RETRO_blameless-postmortem.md": ("🔍", "Retro", "Google SRE blameless postmortem"),
}


def _describe_log_file(name: str) -> tuple[str, str, str]:
    if name in _LOG_FILE_META:
        return _LOG_FILE_META[name]
    if name.startswith("ADR-"):
        return ("🏛️", name, "Nygard ADR — quyết định kiến trúc")
    return ("📄", name, "")


def list_project_logs(tenant_id: str, ticket: str | None = None) -> dict:
    """Neu co `ticket`: liet ke file trong <tenant>/normalized/<ticket>/_project-logs/ (kem
    adr/ long 1 cap). Neu khong: quet toan tenant, tra ve danh sach ticket nao co _project-logs/
    de UI tu chon."""
    tenant_root = _resolve_tenant_root(tenant_id)
    normalized_dir = tenant_root / "normalized"
    if not normalized_dir.is_dir():
        return {"tenant_id": tenant_id, "ticket": ticket, "tickets": [], "logs": []}

    # Luon tinh du danh sach ticket (ke ca khi da chon 1 ticket cu the) - UI can gia tri nay de
    # biet co nen hien nut "doi ticket khac" hay khong (phat hien khi review Phase 6, 2026-08-25:
    # ban dau chi tra tickets=[] khi da chon ticket, khien workspace co >=2 ticket co logs se ket
    # o mot ticket khong co duong quay lai).
    tickets = [
        p.name
        for p in sorted(normalized_dir.iterdir())
        if p.is_dir() and p.name not in _NOTE_DIR_NAMES and (p / "_project-logs").is_dir()
    ]

    if not ticket:
        return {"tenant_id": tenant_id, "ticket": None, "tickets": tickets, "logs": []}

    logs_dir = normalized_dir / ticket / "_project-logs"
    if not logs_dir.is_dir():
        return {"tenant_id": tenant_id, "ticket": ticket, "tickets": tickets, "logs": []}
    logs = []
    for p in sorted(logs_dir.iterdir()):
        if p.is_file() and p.suffix == ".md":
            icon, title, desc = _describe_log_file(p.name)
            logs.append({"file": p.name, "icon": icon, "title": title, "desc": desc, "path": f"{ticket}/_project-logs/{p.name}"})
        elif p.is_dir() and p.name == "adr":
            for adr in sorted(p.iterdir()):
                if adr.suffix == ".md":
                    icon, title, desc = _describe_log_file(adr.name)
                    logs.append({"file": adr.name, "icon": icon, "title": title, "desc": desc, "path": f"{ticket}/_project-logs/adr/{adr.name}"})
    return {"tenant_id": tenant_id, "ticket": ticket, "tickets": tickets, "logs": logs}


# ===== Timeline da chieu (Phase 7) =====
# Moi "chieu"/lane doc 1 so du an that va tu trich ngay/tieu de/noi dung - khong hardcode du
# lieu tung du an (khac mockup, mockup chi de minh hoa thiet ke). Chat luong parse theo tung
# loai so da duoc kiem chung thu cong tren QLYC-11886 truoc khi viet code nay: decision/raid/
# milestone-log co cot ngay sach (Tot), ALERTS.md co dong cau truc san (Tot), ADR can doc
# `event_date` frontmatter (Trung binh - khong tu dong hoan toan, agent phai tu dien khi tao
# ADR moi). change-cr-log.md va raci-daci-register.md KHONG dua vao timeline (xem ghi chu trong
# mockup) - ngay nam rai trong van xuoi / khong phai nhat ky co ngay.

_DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})(?:[^\d]{0,3}(\d{1,2}):(\d{2}))?")


def _strip_md(cell: str) -> str:
    return cell.replace("**", "").strip()


def _excerpt(text: str, max_len: int = 110) -> str:
    text = _strip_md(text)
    if len(text) <= max_len:
        return text
    cut = text[:max_len]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut + "…"


def _parse_vn_date(cell: str) -> datetime | None:
    """Tim occurrence DAU TIEN dang DD/MM/YYYY (kem gio tuy chon) trong 1 o bang - chap nhan ca
    truong hop khoang ngay kieu "~20-24/07/2026" (regex se khop "24/07/2026", lay dau muon hon
    cua khoang - xap xi hop ly, khong can chinh xac tuyet doi cho muc dich dinh vi tren truc
    thoi gian)."""
    m = _DATE_RE.search(cell)
    if not m:
        return None
    day, month, year, hh, mm = m.groups()
    try:
        return datetime(int(year), int(month), int(day), int(hh or 0), int(mm or 0))
    except ValueError:
        return None


def _extract_table_rows(md_text: str) -> list[list[str]]:
    """Doc 1 bang markdown DAU TIEN trong file (moi so hien co dung 1 bang) - bo qua dong header
    va dong separator (---), tra ve list cell da strip cho tung dong du lieu."""
    rows: list[list[str]] = []
    in_table = False
    header_skipped = False
    for line in md_text.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            if in_table:
                break
            continue
        in_table = True
        if not header_skipped:
            header_skipped = True
            continue
        core = s.strip("|")
        if not set(core.replace("-", "").replace(":", "").replace("|", "").strip()):
            continue
        rows.append([c.strip() for c in core.split("|")])
    return rows


def _read_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    return meta, parts[2].lstrip("\n")


def _decision_log_events(logs_dir: Path) -> list[dict]:
    path = logs_dir / "decision-log.md"
    if not path.is_file():
        return []
    rel_path = f"{logs_dir.parent.name}/_project-logs/decision-log.md"
    events = []
    for cells in _extract_table_rows(path.read_text(encoding="utf-8")):
        if len(cells) < 6:
            continue
        dt = _parse_vn_date(cells[0])
        if not dt:
            continue
        events.append(
            {
                "date": dt.isoformat(),
                "title": _strip_md(cells[1]),
                "text": f"Người quyết: {_strip_md(cells[2])}. Trạng thái: {_strip_md(cells[5])}.",
                "sev": "normal",
                "agent": None,
                "path": rel_path,
            }
        )
    return events


def _raid_log_events(logs_dir: Path) -> list[dict]:
    path = logs_dir / "raid-log.md"
    if not path.is_file():
        return []
    rel_path = f"{logs_dir.parent.name}/_project-logs/raid-log.md"
    events = []
    for cells in _extract_table_rows(path.read_text(encoding="utf-8")):
        if len(cells) < 6:
            continue
        dt = _parse_vn_date(cells[2])
        if not dt:
            continue
        loai = _strip_md(cells[0])
        mota = _strip_md(cells[1])
        events.append(
            {
                "date": dt.isoformat(),
                "title": f"{loai}: {_excerpt(mota, 90)}",
                "text": f"{mota} — Trạng thái: {_strip_md(cells[5])}.",
                "sev": "normal",
                "agent": None,
                "path": rel_path,
            }
        )
    return events


def _milestone_log_events(logs_dir: Path) -> list[dict]:
    path = logs_dir / "milestone-log.md"
    if not path.is_file():
        return []
    rel_path = f"{logs_dir.parent.name}/_project-logs/milestone-log.md"
    events = []
    for cells in _extract_table_rows(path.read_text(encoding="utf-8")):
        if len(cells) < 6:
            continue
        dt = _parse_vn_date(cells[1])
        if not dt:
            continue
        moc = _strip_md(cells[0])
        target = _strip_md(cells[2])
        lan = _strip_md(cells[3])
        events.append(
            {
                "date": dt.isoformat(),
                "title": f"{moc} — lần dời {lan}: → {target}",
                "text": f"Lý do: {_strip_md(cells[4])}. Xác nhận: {_strip_md(cells[5])}.",
                "sev": "normal",
                "agent": None,
                "path": rel_path,
            }
        )
    return events


_ALERT_LINE_RE = re.compile(
    r"^-\s*\[(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})\]\s*\[([^\]]+)\]\s*\[([^\]]+)\]\s*\[([^\]]+)\]\s*\[PIC:\s*([^\]]+)\]\s*(.+)$"
)


def _alerts_events(logs_dir: Path) -> list[dict]:
    path = logs_dir / "ALERTS.md"
    if not path.is_file():
        return []
    rel_path = f"{logs_dir.parent.name}/_project-logs/ALERTS.md"
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _ALERT_LINE_RE.match(line.strip())
        if not m:
            continue
        date_s, time_s, agent, sev, status, pic, msg = m.groups()
        try:
            dt = datetime.fromisoformat(f"{date_s}T{time_s}:00")
        except ValueError:
            continue
        msg = msg.strip()
        events.append(
            {
                "date": dt.isoformat(),
                "title": _excerpt(msg, 100),
                "text": f"{msg} (PIC: {pic.strip()}, trạng thái: {status.strip()})",
                "sev": "cao" if sev.strip().upper() == "CAO" else "normal",
                "agent": agent.strip(),
                "path": rel_path,
            }
        )
    return events


_ADR_H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_ADR_DECISION_RE = re.compile(r"##\s*Quyết định.*?\n+(.+?)(?:\n##|\Z)", re.DOTALL)


def _adr_events(logs_dir: Path) -> list[dict]:
    adr_dir = logs_dir / "adr"
    if not adr_dir.is_dir():
        return []
    ticket = logs_dir.parent.name
    events = []
    for p in sorted(adr_dir.glob("ADR-*.md")):
        meta, body = _read_frontmatter(p.read_text(encoding="utf-8"))
        event_date_raw = meta.get("event_date")
        if not event_date_raw:
            # ADR chua duoc backfill event_date - khong doan bang last_updated (de gay hieu
            # lam, xem chu thich template) - bo qua khoi timeline, khong loi.
            continue
        try:
            dt = datetime.fromisoformat(str(event_date_raw))
        except ValueError:
            continue
        h1 = _ADR_H1_RE.search(body)
        title = h1.group(1).strip() if h1 else p.stem
        dec = _ADR_DECISION_RE.search(body)
        dec_text = re.sub(r"\s+", " ", dec.group(1)).strip() if dec else ""
        status = str(meta.get("status") or "").strip()
        prefix = f"({status}) " if status else ""
        events.append(
            {
                "date": dt.isoformat(),
                "title": title,
                "text": prefix + _excerpt(dec_text, 260),
                "sev": "normal",
                "agent": None,
                "path": f"{ticket}/_project-logs/adr/{p.name}",
            }
        )
    return events


def _retro_events(logs_dir: Path) -> list[dict]:
    """RETRO_blameless-postmortem.md la van ban tong hop tu do (Summary/Timeline/Root Cause/...),
    khong phai bang co cot ngay - khong parse noi dung tu do (de vo, giong ly do change-cr-log.md
    bi loai). Nguon parseable AN TOAN DUY NHAT la field `last_updated` (agent retro-synthesizer
    da tu quy uoc san ghi kem ghi chu ly do cap nhat, xem retro-synthesizer.md) - moi ticket chi
    cho ra DUNG 1 event: lan dong bo retro gan nhat."""
    path = logs_dir / "RETRO_blameless-postmortem.md"
    if not path.is_file():
        return []
    meta, _ = _read_frontmatter(path.read_text(encoding="utf-8"))
    raw = str(meta.get("last_updated") or "")
    m = re.match(r"(\d{4}-\d{2}-\d{2})\s*(?:\(([^)]*)\))?", raw)
    if not m:
        return []
    try:
        dt = datetime.fromisoformat(m.group(1))
    except ValueError:
        return []
    note = (m.group(2) or "").strip()
    if "," in note:
        # note dang "retro-synthesizer, cap nhat lan 3 - sau 5 ALERT_CAO moi 18/08" - bo phan
        # ten agent (da co field agent rieng cua lane), chi giu ly do cap nhat.
        note = note.split(",", 1)[1].strip()
    title = f"Retro cập nhật — {note}" if note else "Retro — bản tổng hợp mới nhất"
    text = "Bản tổng hợp Retro (blameless postmortem) mới nhất, đọc từ toàn bộ 6 sổ project-logs."
    if note:
        text += f" Lý do cập nhật lần này: {note}."
    rel_path = f"{logs_dir.parent.name}/_project-logs/RETRO_blameless-postmortem.md"
    return [{"date": dt.isoformat(), "title": title, "text": text, "sev": "normal", "agent": None, "path": rel_path}]


_TIMELINE_LANES = [
    ("alerts", "Alerts", "🚨", "nhiều agent (ghi theo mỗi lần chạy)", _alerts_events),
    ("decision", "Decision Log", "📝", "không agent (backfill thủ công)", _decision_log_events),
    ("raid", "RAID Log", "⚠️", "raid-milestone-extraction", _raid_log_events),
    ("milestone", "Milestone Log", "📅", "raid-milestone-extraction", _milestone_log_events),
    ("adr", "ADR", "🏛️", "architecture-compliance", _adr_events),
    ("retro", "Retro", "🔍", "retro-synthesizer", _retro_events),
]


def get_project_logs_timeline(tenant_id: str, ticket: str) -> dict:
    """Tra ve du lieu cho timeline da chieu: moi lane la 1 so/nguon, moi event kem ngay ISO -
    frontend tu tinh vi tri % tren truc (khong tinh san % o backend, giu FE don gian de tai
    dung y tuong da duyet trong mockup)."""
    tenant_root = _resolve_tenant_root(tenant_id)
    logs_dir = tenant_root / "normalized" / ticket / "_project-logs"

    lanes = []
    for lane_id, name, icon, agent, fn in _TIMELINE_LANES:
        events = sorted(fn(logs_dir), key=lambda e: e["date"])
        lanes.append({"id": lane_id, "name": name, "icon": icon, "agent": agent, "events": events})

    # "Vach backfill": ngay decision-log.md duoc backfill 1 lan (file nay khong co agent so
    # huu - last_updated khong mang hau to ten agent, xem feedback_mandatory_knowledge_workflow)
    # - dung lam moc phan biet du lieu dung lai tu MoM cu voi du lieu agent ghi gan-thoi-gian-thuc.
    backfill_date = None
    decision_path = logs_dir / "decision-log.md"
    if decision_path.is_file():
        meta, _ = _read_frontmatter(decision_path.read_text(encoding="utf-8"))
        m = re.match(r"(\d{4}-\d{2}-\d{2})", str(meta.get("last_updated") or ""))
        if m:
            backfill_date = m.group(1)

    return {"tenant_id": tenant_id, "ticket": ticket, "backfill_date": backfill_date, "lanes": lanes}
