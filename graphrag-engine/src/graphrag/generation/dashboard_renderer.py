from __future__ import annotations

import html as _html
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import PROJECT_ROOT
from ..pipeline.dashboard import _LANE_TO_TENANT_ROOT
from ..tenant_browser import list_tenant_registry
from ..timeline_sync import (
    load_lane_timeline_bars,
    normalize_backlog_key,
    resolve_cio_report_path,
    status_display,
    timeline_percent,
)

if TYPE_CHECKING:
    from ..pipeline.dashboard import LaneResult

_STATUS_CLASS = {
    "Mới mở": "open",
    "Đang xử lý": "progress",
    "Hoàn thành": "done",
    "Blocked": "blocked",
}
_PRIORITY_CLASS = {"Cao": "cao", "TB": "tb", "Thấp": "thap"}
_SEVERITY_CLASS = {"Cao": "critical", "TB": "warning", "Thấp": "good"}

# Đảo _LANE_TO_TENANT_ROOT (lane -> "tenants/<id>") thành tenant_id -> lane, dùng để biết workspace
# nào có sẵn báo cáo CIO thủ công khi dựng tab "Báo cáo CIO" trong panel workspace (Phase 6,
# 2026-08-25 — sidebar giờ đi theo cây org/workspace thay vì danh sách lane phẳng).
_TENANT_ID_TO_LANE = {v.removeprefix("tenants/"): k for k, v in _LANE_TO_TENANT_ROOT.items()}
_ORG_DISPLAY = {"msb": ("MSB", "🏢", "msb"), "minmo": ("Minmo", "🏢", "minmo")}
_WORKSPACE_DISPLAY_NAME = {
    "dip": "DIP",
    "ekyc": "New eKYC",
    "magnet": "Magnet",
    "msbpay": "MSBPay",
    "mconnect": "MConnect",
    "_dept": "_dept",
    "saleman-platform": "Saleman Platform",
    "saleman-legacy": "Saleman Legacy",
    "onewallet": "OneWallet",
    "pmem": "PMEM",
    "ibd": "IBD",
}

# Mo ta thu cong 5 tai lieu cua tenants/_common (Phase 6, 2026-08-25) - khac moi tenant khac,
# _common la danh muc nho/co dinh (methodology dung chung, khong phai project) nen dung server-
# render tinh voi mo ta rieng thay vi tai lieu chung qua /tenant-documents (tenant do van dung
# API do binh thuong, chi UI panel nay uu tien hien thi dep hon). Cap nhat tay o day neu _common
# co them/bot tai lieu — cung quy uoc voi SOURCE_META (dashboard_renderer.py) cho citation.
_COMMON_DOC_META = [
    ("Architecture Design Patterns v1.0.md", "Architecture Design Patterns v1.0", "Pattern kiến trúc hệ thống: Hexagonal/DDD, CQRS/Event Sourcing, Microservices, Saga, Circuit Breaker..."),
    ("RapidExploration&PresentationMethodology v1.1.md", "RapidExploration & PresentationMethodology v1.1", "Phương pháp khám phá nhanh + trình bày ứng dụng công nghệ mới (REP framework)"),
    ("Triển khai dự án thần tốc v1.3.md", "Triển khai dự án thần tốc v1.3", "Triển khai tốc độ cao, đảm bảo Deadline/Goal/Roadmap mà không cần OT"),
    ("Unknown_Unknowns_OS_v2_Full.md", "Unknown_Unknowns_OS_v2_Full", "53 tầng + 103 cơ chế epistemic — scan rủi ro & cơ hội \"chưa biết mình chưa biết\""),
    ("Slide-Trustworthy-Adversarial-Framework v1.0.md", "Slide-Trustworthy-Adversarial-Framework v1.0", "6 trụ cột dựng báo cáo/slide đáng tin, chịu được phản biện ngay từ lúc viết"),
]


def _common_doc_grid_html() -> str:
    cards = "".join(
        f'<div class="doc-card"><div class="doc-num">{i}</div><div class="doc-body">'
        f'<h4>{_esc(title)}</h4><p>{_esc(desc)}</p><div class="doc-src">tenants/_common/normalized/{_esc(fname)}</div></div></div>'
        for i, (fname, title, desc) in enumerate(_COMMON_DOC_META, start=1)
    )
    return f'<div class="card doc-grid">{cards}</div>'
_INBOX_LANE = "Chưa phân loại"

_SECTION_RE = re.compile(r"<section>(.*?)</section>", re.S)
_SECTION_LETTER_RE = re.compile(r"^\s*<h2>([A-G])\.")
_HEADER_RE = re.compile(r"<header>(.*?)</header>", re.S)
_ASOF_DATE_RE = re.compile(r"Dữ liệu tính đến:\s*<strong>(\d{2}/\d{2}/\d{4})</strong>")
_FOOTER_RE = re.compile(r"<footer>(.*?)</footer>", re.S)
_E_H3_RE = re.compile(r"<h3>(.*?)</h3>", re.S)
_TR_RE = re.compile(r"<tr>(.*?)</tr>", re.S)
_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_TABLE_RE = re.compile(r"<table\b(?P<attrs>[^>]*)>(?P<body>.*?)</table>", re.S | re.I)
_BACKLOG_ID_RE = re.compile(r"\b(?:QLYC|YC|RE|PA)-\d+", re.I)
_DATE_RE = re.compile(r"\b(\d{1,2}/\d{1,2}(?:/\d{2,4})?)\b")
_ALERT_LINE_RE = re.compile(
    r"^- \[(?P<time>[^\]]+)\] \[(?P<agent>[^\]]+)\] \[(?P<severity>[^\]]+)\]"
    r"(?: \[(?P<status>MO|DA_XU_LY)\])?"
    r"(?: \[PIC: (?P<pic>[^\]]+)\])? (?P<message>.+)$"
)
_EMPTY_CIO = (
    '<div class="empty-state"><div class="empty-state-icon">📄</div>'
    "<p>Chưa có báo cáo CIO-Report cho mục này.</p></div>"
)

@dataclass
class _HtmlCell:
    tag: str
    attrs: dict[str, str]
    inner_html: str
    text: str
    rowspan: int = 1
    colspan: int = 1


@dataclass
class _HtmlTable:
    attrs: dict[str, str]
    header_rows: list[list[_HtmlCell]] = field(default_factory=list)
    body_rows: list[list[_HtmlCell]] = field(default_factory=list)


@dataclass
class _WorkflowItem:
    combo: str
    backlog_id: str
    backlog_title: str
    backlog_key: str
    plan_golive: str
    subbacklog: str
    task: str
    pic: str
    priority: str
    status: str
    deadline: str
    sort_date: datetime | None


@dataclass
class _LogEvent:
    source_name: str
    source_path: str
    source_channel: str
    source_title: str
    raised_by: str
    status: str
    date_label: str
    sort_date: datetime | None
    detail: str


@dataclass
class _RootEvent:
    source_path: str
    source_channel: str
    source_title: str
    raised_by: str
    date_label: str
    sort_date: datetime | None
    detail: str


_SUPPLEMENTAL_WORKFLOW_SOURCES: dict[str, list[tuple[str, str, set[str] | None]]] = {
    "MSBPay": [
        ("msbpay_full", "tenants/msb/msbpay/normalized/MSBPay_Backlog_Full_v2.md", None),
    ],
    "DIP": [
        ("mconnect_full", "tenants/msb/mconnect/normalized/MConnect_Backlog_Full_v2.md", {"QLYC-11863"}),
    ],
}


def _lane_root(lane: str) -> Path:
    """Thư mục normalized/ của tenant ứng với lane này (xem _LANE_TO_TENANT_ROOT,
    pipeline/dashboard.py) — mỗi lane = đúng 1 tenant riêng kể từ khi tách tenants/
    (2026-08-22), khác trước đây (1 cây normalized/02_Work/Msb/DMUDCNS/<lane>/ dùng chung)."""
    tenant_rel = _LANE_TO_TENANT_ROOT.get(lane)
    if not tenant_rel:
        return PROJECT_ROOT.parent / "__unknown_lane__"
    return PROJECT_ROOT.parent / tenant_rel / "normalized"


def _esc(v) -> str:
    return _html.escape(str(v)) if v is not None else ""


def _strip_tags(s: str) -> str:
    return _html.unescape(_TAG_RE.sub("", s)).strip()


def _slug(lane: str) -> str:
    return "tab-" + "".join(c if c.isalnum() else "-" for c in lane).strip("-").lower()


def _parse_attrs(raw: str) -> dict[str, str]:
    return {m.group(1).lower(): _html.unescape(m.group(2)) for m in re.finditer(r'([:\w-]+)\s*=\s*"([^"]*)"', raw)}


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.table_attrs: dict[str, str] = {}
        self.header_rows: list[list[_HtmlCell]] = []
        self.body_rows: list[list[_HtmlCell]] = []
        self._section = "body"
        self._in_table = False
        self._in_cell = False
        self._current_row: list[_HtmlCell] = []
        self._cell_tag = ""
        self._cell_attrs: dict[str, str] = {}
        self._cell_parts: list[str] = []
        self._cell_text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {k.lower(): (v or "") for k, v in attrs}
        if tag == "table":
            self._in_table = True
            self.table_attrs = attrs_dict
            return
        if not self._in_table:
            return
        if tag == "thead":
            self._section = "head"
        elif tag == "tbody":
            self._section = "body"
        elif tag == "tr":
            self._current_row = []
        elif tag in {"th", "td"}:
            self._in_cell = True
            self._cell_tag = tag
            self._cell_attrs = attrs_dict
            self._cell_parts = []
            self._cell_text_parts = []
        elif self._in_cell:
            self._cell_parts.append(self.get_starttag_text() or f"<{tag}>")

    def handle_endtag(self, tag: str) -> None:
        if not self._in_table:
            return
        if tag == "table":
            self._in_table = False
            return
        if tag == "thead":
            self._section = "body"
            return
        if tag in {"th", "td"} and self._in_cell:
            cell = _HtmlCell(
                tag=self._cell_tag,
                attrs=dict(self._cell_attrs),
                inner_html="".join(self._cell_parts).strip(),
                text=_html.unescape("".join(self._cell_text_parts)).strip(),
                rowspan=max(1, int(self._cell_attrs.get("rowspan", "1") or "1")),
                colspan=max(1, int(self._cell_attrs.get("colspan", "1") or "1")),
            )
            self._current_row.append(cell)
            self._in_cell = False
            return
        if tag == "tr":
            if not self._current_row:
                return
            target = self.header_rows if self._section == "head" else self.body_rows
            target.append(self._current_row)
            self._current_row = []
            return
        if self._in_cell:
            self._cell_parts.append(f"</{tag}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._in_cell:
            attr_text = "".join(f' {k}="{_html.escape(v or "", quote=True)}"' for k, v in attrs)
            self._cell_parts.append(f"<{tag}{attr_text}/>")

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_parts.append(data)
            self._cell_text_parts.append(data)

    def handle_entityref(self, name: str) -> None:
        text = f"&{name};"
        if self._in_cell:
            self._cell_parts.append(text)
            self._cell_text_parts.append(_html.unescape(text))

    def handle_charref(self, name: str) -> None:
        text = f"&#{name};"
        if self._in_cell:
            self._cell_parts.append(text)
            self._cell_text_parts.append(_html.unescape(text))


def _parse_table(table_html: str) -> _HtmlTable:
    parser = _TableParser()
    parser.feed(table_html)
    return _HtmlTable(attrs=parser.table_attrs, header_rows=parser.header_rows, body_rows=parser.body_rows)


def _expand_rows(rows: list[list[_HtmlCell]]) -> list[list[_HtmlCell]]:
    expanded: list[list[_HtmlCell]] = []
    active: list[tuple[_HtmlCell, int]] = []
    for row in rows:
        logical: list[_HtmlCell] = []
        next_active: list[tuple[_HtmlCell, int]] = []
        for cell, remain in active:
            logical.append(cell)
            if remain > 1:
                next_active.append((cell, remain - 1))
        active = next_active
        cursor = len(logical)
        for cell in row:
            while len(logical) > cursor:
                cursor += 1
            for _ in range(cell.colspan):
                logical.append(cell)
            if cell.rowspan > 1:
                for _ in range(cell.colspan):
                    active.append((cell, cell.rowspan - 1))
            cursor = len(logical)
        expanded.append(logical)
    return expanded


def _split_table_header_and_body(parsed: _HtmlTable) -> tuple[list[_HtmlCell], list[list[_HtmlCell]]]:
    header_rows = _expand_rows(parsed.header_rows)
    body_rows = _expand_rows(parsed.body_rows)
    if header_rows:
        return header_rows[-1], body_rows
    if body_rows:
        return body_rows[0], body_rows[1:]
    return [], []


def _cell_html(cell: _HtmlCell, *, force_class: str | None = None) -> str:
    attrs = dict(cell.attrs)
    if force_class:
        existing = attrs.get("class", "").strip()
        attrs["class"] = f"{existing} {force_class}".strip()
    attr_text = "".join(f' {k}="{_html.escape(v, quote=True)}"' for k, v in attrs.items() if v)
    return f"<{cell.tag}{attr_text}>{cell.inner_html}</{cell.tag}>"


def _cell_with_tag(cell: _HtmlCell, tag: str = "td") -> str:
    attr_text = "".join(f' {k}="{_html.escape(v, quote=True)}"' for k, v in cell.attrs.items() if v)
    return f"<{tag}{attr_text}>{cell.inner_html}</{tag}>"


def _normalize_key(backlog_id: str, title: str) -> str:
    backlog_token = (backlog_id or "").strip()
    backlog_token = backlog_token.strip("-").strip("–").strip("—").strip()
    if backlog_token:
        return backlog_token.lower()
    return re.sub(r"\s+", " ", _strip_tags(title)).strip().lower()


def _normalize_backlog_text(text: str) -> str:
    return (text or "").replace("‐", "-").replace("‑", "-").replace("‒", "-").replace("–", "-").replace("—", "-")


def _canonicalize_title(text: str) -> str:
    raw = _normalize_backlog_text(_strip_tags(text)).lower()
    raw = re.sub(r"\[[^\]]+\]", " ", raw)
    raw = re.sub(r"\([^)]*\)", " ", raw)
    raw = re.sub(r"\b(qlyc|yc|re|pa)-\d+\b", " ", raw, flags=re.I)
    raw = re.sub(r"\b(golive|plan|deadline|lane|đầu mối|workstream|backlog|nhóm nguồn)\b", " ", raw)
    raw = re.sub(r"[^0-9a-zàáảãạăắằẳẵặâấầẩẫậđèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵ]+", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


def _ascii_fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).replace("đ", "d").replace("Đ", "D")


def _title_tokens(text: str) -> set[str]:
    canonical = _ascii_fold(_canonicalize_title(text)).lower()
    return {token for token in canonical.split() if len(token) >= 3}


def _header_index(headers: list[_HtmlCell], *patterns: str) -> int:
    normalized = [_strip_tags(cell.text).strip().lower() for cell in headers]
    for idx, text in enumerate(normalized):
        if all(pattern in text for pattern in patterns):
            return idx
    return -1


def _parse_date_label(text: str) -> tuple[str, datetime | None]:
    raw = (text or "").strip()
    match = _DATE_RE.search(raw)
    if not match:
        return (raw or "Chưa chốt"), None
    label = match.group(1)
    parts = label.split("/")
    try:
        if len(parts) == 3:
            year = int(parts[2])
            if year < 100:
                year += 2000
            dt = datetime(year, int(parts[1]), int(parts[0]))
        else:
            dt = datetime(2026, int(parts[1]), int(parts[0]))
        return label, dt
    except ValueError:
        return label, None


def _extract_backlog_parts(cell: _HtmlCell) -> tuple[str, str, str]:
    backlog_id_match = _BACKLOG_ID_RE.search(_normalize_backlog_text(cell.text))
    backlog_id = backlog_id_match.group(0).upper() if backlog_id_match else ""
    title_html = re.sub(r'<span class="backlog-id">.*?</span>', "", cell.inner_html, flags=re.S)
    golive_match = re.search(r'<div class="golive-plan">(.*?)</div>', title_html, re.S)
    plan_golive = _strip_tags(golive_match.group(1)) if golive_match else ""
    title_html = re.sub(r'<div class="golive-plan">.*?</div>', "", title_html, flags=re.S).strip()
    title_text = _strip_tags(title_html) or cell.text
    return backlog_id, title_text, plan_golive


def _extract_workflow_groups(f_content: str) -> dict[str, dict[str, object]]:
    match = _TABLE_RE.search(f_content or "")
    if not match:
        return {}
    table = _parse_table(match.group(0))
    expanded_headers = _expand_rows(table.header_rows)
    if not expanded_headers:
        return {}
    header_row = expanded_headers[-1]
    combo_idx = _header_index(header_row, "combo")
    backlog_idx = _header_index(header_row, "backlog")
    sub_idx = _header_index(header_row, "subbacklog")
    task_idx = _header_index(header_row, "luồng", "công việc")
    pic_idx = _header_index(header_row, "pic")
    priority_idx = _header_index(header_row, "priority")
    status_idx = _header_index(header_row, "status")
    deadline_idx = _header_index(header_row, "deadline")
    required = [combo_idx, backlog_idx, sub_idx, task_idx, pic_idx, priority_idx, status_idx, deadline_idx]
    if any(idx < 0 for idx in required):
        return {}
    rows = _expand_rows(table.body_rows)
    groups: dict[str, dict[str, object]] = {}
    for row in rows:
        max_idx = max(required)
        if len(row) <= max_idx:
            continue
        combo = row[combo_idx].text
        backlog_id, backlog_title, plan_golive = _extract_backlog_parts(row[backlog_idx])
        backlog_key = _normalize_key(backlog_id, backlog_title)
        subbacklog = row[sub_idx].text
        deadline_label, deadline_dt = _parse_date_label(row[deadline_idx].text)
        item = _WorkflowItem(
            combo=combo,
            backlog_id=backlog_id,
            backlog_title=backlog_title,
            backlog_key=backlog_key,
            plan_golive=plan_golive,
            subbacklog=subbacklog,
            task=row[task_idx].text,
            pic=row[pic_idx].text,
            priority=row[priority_idx].text,
            status=row[status_idx].text,
            deadline=deadline_label,
            sort_date=deadline_dt,
        )
        entry = groups.setdefault(
            backlog_key,
            {
                "backlog_id": backlog_id,
                "title": backlog_title,
                "plan_golive": plan_golive,
                "combo": combo,
                "items": [],
            },
        )
        cast_items = entry["items"]
        assert isinstance(cast_items, list)
        cast_items.append(item)
    return groups


def _workflow_status_label(raw: str) -> str:
    normalized = _ascii_fold(_canonicalize_title(raw)).lower()
    if "blocked" in normalized or "on hold" in normalized:
        return "Blocked"
    if "cancel" in normalized:
        return "Cancelled"
    if "done" in normalized or "hoan thanh" in normalized or "golive" in normalized:
        return "Hoàn thành"
    if "to do" in normalized or "ke hoach" in normalized or "moi mo" in normalized:
        return "Mới mở"
    if normalized:
        return "Đang xử lý"
    return "—"


def _workflow_priority_label(raw: str) -> str:
    normalized = _ascii_fold(_canonicalize_title(raw)).lower()
    if "highest" in normalized or "cao" in normalized or "high" in normalized:
        return "Cao"
    if "medium" in normalized or "tb" in normalized or "trung binh" in normalized:
        return "TB"
    if "low" in normalized or "thap" in normalized:
        return "Thấp"
    return "—"


def _append_workflow_item(
    groups: dict[str, dict[str, object]],
    *,
    combo: str,
    backlog_id: str,
    backlog_title: str,
    plan_golive: str,
    subbacklog: str,
    task: str,
    pic: str,
    priority: str,
    status: str,
    deadline: str,
) -> None:
    backlog_key = _normalize_key(backlog_id, backlog_title)
    deadline_label, deadline_dt = _parse_date_label(deadline)
    item = _WorkflowItem(
        combo=combo,
        backlog_id=backlog_id,
        backlog_title=backlog_title,
        backlog_key=backlog_key,
        plan_golive=plan_golive,
        subbacklog=subbacklog,
        task=task.strip(),
        pic=pic.strip(),
        priority=priority.strip(),
        status=status.strip(),
        deadline=deadline_label,
        sort_date=deadline_dt,
    )
    entry = groups.setdefault(
        backlog_key,
        {
            "backlog_id": backlog_id,
            "title": backlog_title,
            "plan_golive": plan_golive,
            "combo": combo,
            "items": [],
        },
    )
    items = entry["items"]
    assert isinstance(items, list)
    items.append(item)


def _parse_markdown_pipe_row(line: str) -> list[str]:
    text = line.strip()
    if not text.startswith("|") or not text.endswith("|"):
        return []
    return [part.strip() for part in text.strip("|").split("|")]


def _parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    frontmatter: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        frontmatter[key.strip()] = value.strip()
    return frontmatter


def _first_markdown_heading(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return ""


def _is_generic_doc_heading(value: str) -> bool:
    clean = re.sub(r"\s+", " ", (value or "").strip())
    if not clean:
        return True
    folded = _ascii_fold(clean).lower()
    if folded in {"noi dung", "trang 1", "trang 2", "trang 3", "page 1", "page 2", "page 3"}:
        return True
    return bool(re.fullmatch(r"(trang|page)\s+\d+", folded))


def _extract_subject_from_text(text: str) -> str:
    subject_patterns = [
        r"^Subject:\s*(.+)$",
        r"^Tiêu đề:\s*(.+)$",
    ]
    for pattern in subject_patterns:
        match = re.search(pattern, text, re.M)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if re.match(r"^(RE|FW|FWD):", line, re.I):
            return re.sub(r"\s+", " ", line)
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or _is_generic_doc_heading(line):
            continue
        if line.startswith(("#", "---")):
            continue
        if re.match(r"^https?://\S+$", line, re.I):
            continue
        if re.match(r"^(source|converted_at|converter|date|project|reliability|basis|corroborated_by|log_type|last_updated|people_mentions):", line, re.I):
            continue
        if re.match(r"^(Từ|From|To|Tới|Cc|Bcc|Ngày|Date):", line, re.I):
            continue
        normalized_line = _ascii_fold(line).lower()
        if normalized_line.startswith(("dear ", "em gui lai noi dung cuoc hop", "gui trong meeting")):
            continue
        if len(line) >= 18 and any(token in _ascii_fold(line).lower() for token in ("qlyc-", "hop", "meeting", "daily", "mom", "ticket", "backlog", "golive")):
            return re.sub(r"\s+", " ", line)
    return ""


def _extract_sender_from_text(text: str) -> str:
    for pattern in (r"^From:\s*(.+)$", r"^Từ:\s*(.+)$"):
        match = re.search(pattern, text, re.M)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip()
    return ""


def _pick_root_event_detail(text: str, title: str, path: Path) -> str:
    heading = _first_markdown_heading(text)
    if heading and not _is_generic_doc_heading(heading):
        return heading
    subject = _extract_subject_from_text(text)
    if subject:
        return subject
    if title and not _is_generic_doc_heading(title):
        return title
    return path.stem


def _collect_explicit_dates(text: str) -> list[tuple[str, datetime]]:
    candidates: list[tuple[str, datetime]] = []
    seen: set[str] = set()
    for match in _DATE_RE.finditer(text or ""):
        label, dt = _parse_date_label(match.group(1))
        if dt is None:
            continue
        key = dt.isoformat()
        if key in seen:
            continue
        seen.add(key)
        candidates.append((label, dt))
    return candidates


def _looks_like_event_start(lines: list[str], idx: int) -> bool:
    line = lines[idx].strip()
    if not line or _is_generic_doc_heading(line):
        return False
    if line.startswith(("#", "---")):
        return False
    if re.match(r"^Subject:\s*.+$", line, re.I):
        return True
    if re.match(r"^(RE|FW|FWD):\s*.+$", line, re.I):
        return True
    nearby = "\n".join(lines[idx + 1 : idx + 6])
    normalized = _ascii_fold(line).lower()
    has_business_hint = any(token in normalized for token in ("qlyc-", "hop", "meeting", "daily", "mom", "golive", "kiosk"))
    if has_business_hint and re.search(r"^(Từ|From|Ngày|Date):", nearby, re.M | re.I):
        return True
    return False


def _extract_event_blocks(path: Path, text: str) -> list[str]:
    path_posix = path.as_posix()
    if "_zalo-notes" in path_posix or "[Teams]" in path.name or path.name.startswith("[Teams]"):
        return [text]
    lines = text.splitlines()
    start_indexes = [idx for idx in range(len(lines)) if _looks_like_event_start(lines, idx)]
    if not start_indexes:
        return [text]
    if start_indexes[0] != 0:
        start_indexes = [0] + start_indexes
    blocks: list[str] = []
    for pos, start in enumerate(start_indexes):
        end = start_indexes[pos + 1] if pos + 1 < len(start_indexes) else len(lines)
        chunk = "\n".join(lines[start:end]).strip()
        if chunk:
            blocks.append(chunk)
    return blocks or [text]


def _build_root_event_from_text(path: Path, rel_path: str, text: str) -> _RootEvent | None:
    channel, title, raised_by = _infer_comm_meta_from_text(path, text)
    detail = _pick_root_event_detail(text, title, path)
    normalized_title = _ascii_fold(title or detail).lower()
    date_seed = " ".join(part for part in [title, detail, text[:4000]] if part)
    date_label, sort_dt = _parse_date_label(date_seed)
    if sort_dt is None:
        frontmatter = _parse_frontmatter(text)
        for candidate in (frontmatter.get("date", ""), frontmatter.get("last_updated", "")):
            if not candidate:
                continue
            parsed_label, parsed_dt = _parse_date_label(candidate)
            if parsed_dt is not None:
                date_label, sort_dt = parsed_label, parsed_dt
                break
    if sort_dt is None:
        return None
    if channel in {"", "Chat-note"}:
        return None
    if channel == "Tài liệu" and not raised_by:
        return None
    if normalized_title.startswith("ngay xuat:") or "jira subtasks" in normalized_title or "export" in _ascii_fold(path.stem).lower():
        return None
    return _RootEvent(
        source_path=rel_path,
        source_channel=channel,
        source_title=title or path.stem,
        raised_by=raised_by,
        date_label=date_label,
        sort_date=sort_dt,
        detail=_summarize_timeline_text(detail, path.stem, limit=180),
    )


def _detect_log_channel(path: Path, frontmatter: dict[str, str]) -> str:
    log_type = frontmatter.get("log_type", "").strip()
    if log_type:
        return f"Project-log / {log_type}"
    if "_zalo-notes" in path.as_posix():
        return "Zalo"
    if "_chat-notes" in path.as_posix():
        return "Chat-note"
    return "Project-log"


def _extract_log_raiser(text: str, frontmatter: dict[str, str]) -> str:
    patterns = [
        "Người đề xuất",
        "Người quyết",
        "Người xác nhận dời",
        "Owner",
        "PIC",
        "Approver",
        "Driver",
    ]
    rows = [_parse_markdown_pipe_row(line) for line in text.splitlines()]
    rows = [row for row in rows if row]
    for idx, row in enumerate(rows[:-1]):
        for pattern in patterns:
            try:
                col_idx = next(i for i, cell in enumerate(row) if pattern.lower() in _ascii_fold(cell).lower())
            except StopIteration:
                continue
            next_row = rows[idx + 1]
            if len(next_row) > col_idx:
                candidate = re.sub(r"\*\*", "", next_row[col_idx]).strip()
                if candidate and candidate not in {"—", "-", "Chưa có", "Không có"}:
                    return candidate
    pic_match = re.search(r"\[PIC:\s*([^\]]+)\]", text)
    if pic_match:
        return pic_match.group(1).strip()
    people_mentions = frontmatter.get("people_mentions", "").strip()
    if people_mentions:
        return people_mentions.split(",")[0].strip()
    return ""


def _extract_referenced_markdown_names(text: str) -> list[str]:
    names: list[str] = []
    for match in re.finditer(r"`([^`]+\.md)`", text):
        candidate = Path(match.group(1)).name.strip()
        if candidate and candidate not in names:
            names.append(candidate)
    return names


def _infer_comm_meta_from_text(path: Path, text: str) -> tuple[str, str, str]:
    frontmatter = _parse_frontmatter(text)
    name = path.name
    channel = ""
    title = ""
    raised_by = ""

    if "_zalo-notes" in path.as_posix():
        channel = "Zalo"
        group_match = re.search(r"\[(?:Nhóm|NhÃ³m|Nh\S{1,6}m):\s*(.*?)\s*\|", text)
        if group_match:
            title = group_match.group(1).strip()
        else:
            for raw_line in text.splitlines():
                line = raw_line.strip()
                if "| ID:" in line and line.startswith("[") and ":" in line:
                    candidate = line.split(":", 1)[1].split("|", 1)[0].strip("[] ").strip()
                    if candidate:
                        title = candidate
                        break
        first_speaker = re.search(r"\[\d{4}-\d{2}-\d{2} [^\]]+\]\s*([^:]+):", text)
        if first_speaker:
            raised_by = first_speaker.group(1).strip()
    elif name.startswith("[Teams]") or "[Teams]" in name:
        channel = "Teams"
        title = name.replace(".md", "").replace("[Teams]", "").strip(" -")
        first_speaker = re.search(r"^\s*[-*]?\s*([^:]{2,60}):", text, re.M)
        if first_speaker:
            raised_by = first_speaker.group(1).strip()
    else:
        subject = _extract_subject_from_text(text)
        sender = _extract_sender_from_text(text)
        if subject or sender or name.upper().startswith(("RE ", "FW ", "FWD ")):
            channel = "Email"
            title = (subject or path.stem).replace("\n", " ")
            if sender:
                raised_by = sender

    if not title or _is_generic_doc_heading(title):
        title = _extract_subject_from_text(text)
    if not title or _is_generic_doc_heading(title):
        title = _first_markdown_heading(text) or path.stem
    if not channel:
        source_hint = frontmatter.get("source", "")
        if "[Teams]" in source_hint or "[Teams]" in name:
            channel = "Teams"
        elif "zalo" in source_hint.lower() or "_zalo-notes" in path.as_posix():
            channel = "Zalo"
        elif "mail" in source_hint.lower() or "pdf" in source_hint.lower() or name.upper().startswith("RE "):
            channel = "Email"
        elif source_hint.startswith("artifacts/"):
            channel = "Tài liệu"
    if channel == "Tài liệu" and (_extract_subject_from_text(text) or _extract_sender_from_text(text)):
        channel = "Email"
    if not raised_by:
        raised_by = _extract_sender_from_text(text)
    if not channel:
        channel = "Tài liệu"
    return channel, title, raised_by


def _infer_comm_meta_from_doc(path: Path) -> tuple[str, str, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return "", "", ""
    return _infer_comm_meta_from_text(path, text)


def _resolve_referenced_source_meta(lane_root: Path, text: str) -> tuple[str, str, str]:
    for filename in _extract_referenced_markdown_names(text):
        matches = [p for p in lane_root.glob(f"**/{filename}") if "_project-logs" not in p.as_posix()]
        if not matches:
            continue
        channel, title, raised_by = _infer_comm_meta_from_doc(matches[0])
        if channel or title or raised_by:
            return channel, title, raised_by
    return "", "", ""


def _collect_backlog_source_meta(lane: str) -> dict[str, dict[str, str]]:
    lane_root = _lane_root(lane)
    if not lane_root.exists():
        return {}
    best: dict[str, tuple[int, dict[str, str]]] = {}
    for path in lane_root.glob("**/*.md"):
        if "_project-logs" in path.as_posix():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        backlog_ids = {m.group(0).upper() for m in _BACKLOG_ID_RE.finditer(str(path))}
        backlog_ids.update({m.group(0).upper() for m in _BACKLOG_ID_RE.finditer(text)})
        if not backlog_ids:
            continue
        channel, title, raised_by = _infer_comm_meta_from_doc(path)
        if not (channel or title or raised_by):
            continue
        score = 0
        name_upper = path.name.upper()
        if channel:
            score += 2
        if title:
            score += 2
        if raised_by:
            score += 1
        for backlog_id in backlog_ids:
            if backlog_id in name_upper:
                score += 2
            current = best.get(backlog_id)
            payload = {
                "channel": channel,
                "title": title,
                "raised_by": raised_by,
                "path": path.relative_to(PROJECT_ROOT.parent).as_posix(),
            }
            if current is None or score > current[0]:
                best[backlog_id] = (score, payload)
    return {key: value for key, (_, value) in best.items()}


def _collect_lane_source_candidates(lane: str) -> list[dict[str, str]]:
    lane_root = _lane_root(lane)
    if not lane_root.exists():
        return []
    candidates: list[dict[str, str]] = []
    seen: set[str] = set()
    for path in lane_root.glob("**/*.md"):
        if "_project-logs" in path.as_posix():
            continue
        channel, title, raised_by = _infer_comm_meta_from_doc(path)
        if not (channel or title or raised_by):
            continue
        key = f"{channel}|{title}|{raised_by}|{path.name}"
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            {
                "channel": channel,
                "title": title,
                "raised_by": raised_by,
                "tokens": " ".join(sorted(_title_tokens(title or path.stem))),
            }
        )
    return candidates


def _collect_root_events(lane: str) -> dict[str, list[_RootEvent]]:
    lane_root = _lane_root(lane)
    if not lane_root.exists():
        return {}
    events: dict[str, list[_RootEvent]] = {}
    seen: set[tuple[str, str, str, str]] = set()
    for path in lane_root.glob("**/*.md"):
        path_posix = path.as_posix()
        if "_project-logs" in path_posix or "_chat-notes" in path_posix:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        backlog_ids = {m.group(0).upper() for m in _BACKLOG_ID_RE.finditer(str(path))}
        backlog_ids.update({m.group(0).upper() for m in _BACKLOG_ID_RE.finditer(text)})
        if not backlog_ids:
            continue
        rel_path = path.relative_to(PROJECT_ROOT.parent).as_posix()
        candidate_events = [
            event
            for block in _extract_event_blocks(path, text)
            for event in [_build_root_event_from_text(path, rel_path, block)]
            if event is not None
        ]
        if not candidate_events:
            fallback_event = _build_root_event_from_text(path, rel_path, text)
            if fallback_event is not None:
                candidate_events = [fallback_event]
        for backlog_id in backlog_ids:
            for root_event in candidate_events:
                sort_key = root_event.sort_date.isoformat() if root_event.sort_date else ""
                title_key = _canonicalize_title(root_event.source_title or root_event.detail)
                channel_key = _canonicalize_title(root_event.source_channel)
                key = (backlog_id, channel_key, sort_key, title_key)
                if key in seen:
                    continue
                seen.add(key)
                events.setdefault(backlog_id, []).append(root_event)
    for backlog_id, items in events.items():
        items.sort(key=lambda item: (item.sort_date or datetime.max, item.source_title.lower()))
    return events


def _resolve_source_meta_by_title(title: str, candidates: list[dict[str, str]]) -> dict[str, str]:
    row_tokens = _title_tokens(title)
    if not row_tokens:
        return {}
    best: dict[str, str] = {}
    best_score = 0.0
    for candidate in candidates:
        candidate_title = candidate.get("title", "")
        candidate_tokens = _title_tokens(candidate_title)
        if not candidate_tokens:
            continue
        overlap = row_tokens & candidate_tokens
        if not overlap:
            continue
        score = len(overlap) / max(min(len(row_tokens), len(candidate_tokens)), 1)
        if row_tokens <= candidate_tokens or candidate_tokens <= row_tokens:
            score += 0.15
        if score > best_score and score >= 0.5:
            best = {
                "channel": candidate.get("channel", ""),
                "title": candidate_title,
                "raised_by": candidate.get("raised_by", ""),
            }
            best_score = score
    return best


def _extract_backlog_id(text: str) -> str:
    match = _BACKLOG_ID_RE.search(_normalize_backlog_text(text))
    return match.group(0).upper() if match else ""


def _parse_msbpay_full_workflow(path: Path, backlog_filter: set[str] | None) -> dict[str, dict[str, object]]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    groups: dict[str, dict[str, object]] = {}
    heading_re = re.compile(r"^###\s+📌(QLYC-\d+)—\s*(.+?)\d+\s+subtask\s*$", re.I)
    for idx, line in enumerate(lines):
        match = heading_re.match(line.strip())
        if not match:
            continue
        backlog_id = match.group(1).upper()
        if backlog_filter and backlog_id not in backlog_filter:
            continue
        backlog_title = match.group(2).strip()
        cursor = idx + 1
        while cursor < len(lines) and not lines[cursor].strip().startswith("| # |"):
            cursor += 1
        if cursor >= len(lines):
            continue
        cursor += 2
        while cursor < len(lines):
            row = _parse_markdown_pipe_row(lines[cursor])
            if len(row) < 5:
                break
            subbacklog = _extract_backlog_id(row[1]) or row[1]
            _append_workflow_item(
                groups,
                combo="Backlog tổng hợp MSBPay",
                backlog_id=backlog_id,
                backlog_title=backlog_title,
                plan_golive="Golive: Chưa chốt",
                subbacklog=subbacklog,
                task=row[2],
                pic=row[4],
                priority="—",
                status=_workflow_status_label(row[3]),
                deadline="Chưa chốt",
            )
            cursor += 1
    in_epic_table = False
    for line in lines:
        if line.startswith("| # | Key | Summary | Status | Tasks active | Liên kết QLYC |"):
            in_epic_table = True
            continue
        if not in_epic_table:
            continue
        row = _parse_markdown_pipe_row(line)
        if len(row) < 6:
            if line.strip().startswith("###") or line.strip().startswith("## "):
                in_epic_table = False
            continue
        linked_backlog = _extract_backlog_id(row[5])
        if not linked_backlog or (backlog_filter and linked_backlog not in backlog_filter):
            continue
        task_count = row[4].strip()
        if task_count in {"—", "0", ""}:
            continue
        _append_workflow_item(
            groups,
            combo="MSBPAY Project — Epics Active",
            backlog_id=linked_backlog,
            backlog_title=row[2],
            plan_golive="Golive: Chưa chốt",
            subbacklog=_extract_backlog_id(row[1]) or row[1],
            task=f'Epic liên quan "{row[2]}" — {task_count} active; trạng thái hiện tại: {row[3]}.',
            pic="Chưa rõ",
            priority="—",
            status=_workflow_status_label(row[3]),
            deadline="Chưa chốt",
        )
    return groups


def _parse_mconnect_full_workflow(path: Path, backlog_filter: set[str] | None) -> dict[str, dict[str, object]]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    groups: dict[str, dict[str, object]] = {}
    current_combo = "Backlog tổng hợp MConnect"
    idx = 0
    detail_re = re.compile(r"↳\s*([A-Z]+-\d+)(?:\s*\([^)]*\))?\s*(.*?)\s+—\s+([^;]+?)(?:\s+\(([^()]*)\))?(?=\s*(?:;|$))")
    while idx < len(lines):
        line = lines[idx]
        group_match = re.match(r"^\*\*🏷️\s+Nhóm:\s*(.*?)\*\*$", line.strip())
        if group_match:
            current_combo = group_match.group(1).strip()
            idx += 1
            continue
        row = _parse_markdown_pipe_row(line)
        if len(row) >= 10 and row[0].isdigit():
            backlog_id = _extract_backlog_id(row[1])
            backlog_title = row[3]
            if not backlog_id or (backlog_filter and backlog_id not in backlog_filter):
                idx += 1
                continue
            detail_line = lines[idx + 1] if idx + 1 < len(lines) else ""
            detail_row = _parse_markdown_pipe_row(detail_line)
            detail_text = detail_row[1] if len(detail_row) >= 2 else ""
            found = False
            for sub_match in detail_re.finditer(detail_text):
                found = True
                status_raw = sub_match.group(3).strip()
                assignee = (sub_match.group(4) or "Chưa rõ").strip()
                _append_workflow_item(
                    groups,
                    combo=current_combo,
                    backlog_id=backlog_id,
                    backlog_title=backlog_title,
                    plan_golive="Golive: Chưa chốt",
                    subbacklog=sub_match.group(1).strip(),
                    task=sub_match.group(2).strip(),
                    pic=assignee,
                    priority=_workflow_priority_label(status_raw),
                    status=_workflow_status_label(status_raw),
                    deadline="Chưa chốt",
                )
            if not found and detail_text and "Không có QLYC subtask" not in detail_text:
                cleaned = re.sub(r"\s+", " ", detail_text.replace("⚠️", "")).strip(" ;")
                if cleaned:
                    _append_workflow_item(
                        groups,
                        combo=current_combo,
                        backlog_id=backlog_id,
                        backlog_title=backlog_title,
                        plan_golive="Golive: Chưa chốt",
                        subbacklog="—",
                        task=cleaned,
                        pic="Chưa rõ",
                        priority="—",
                        status="Đang xử lý",
                        deadline="Chưa chốt",
                    )
        idx += 1
    return groups


def _load_supplemental_workflow_groups(lane: str) -> dict[str, dict[str, object]]:
    groups: dict[str, dict[str, object]] = {}
    for parser_name, rel_path, backlog_filter in _SUPPLEMENTAL_WORKFLOW_SOURCES.get(lane, []):
        path = PROJECT_ROOT.parent / rel_path
        if not path.exists():
            continue
        try:
            if parser_name == "msbpay_full":
                parsed = _parse_msbpay_full_workflow(path, backlog_filter)
            elif parser_name == "mconnect_full":
                parsed = _parse_mconnect_full_workflow(path, backlog_filter)
            else:
                continue
        except OSError:
            continue
        for key, group in parsed.items():
            existing = groups.get(key)
            if existing is None:
                groups[key] = group
                continue
            existing_items = existing.get("items", [])
            new_items = group.get("items", [])
            if isinstance(existing_items, list) and isinstance(new_items, list):
                existing_items.extend(new_items)
    return groups


def _merge_workflow_groups(
    primary_groups: dict[str, dict[str, object]],
    supplemental_groups: dict[str, dict[str, object]],
) -> dict[str, dict[str, object]]:
    merged = {
        key: {
            "backlog_id": str(group.get("backlog_id", "")),
            "title": str(group.get("title", "")),
            "plan_golive": str(group.get("plan_golive", "")),
            "combo": str(group.get("combo", "")),
            "items": list(group.get("items", [])) if isinstance(group.get("items", []), list) else [],
        }
        for key, group in primary_groups.items()
    }
    for key, group in supplemental_groups.items():
        if key not in merged:
            merged[key] = group
            continue
        primary_items = _workflow_items_for_popup(merged[key])
        if primary_items:
            continue
        supplemental_items = group.get("items", [])
        if isinstance(supplemental_items, list):
            merged[key]["items"] = list(supplemental_items)
        if not merged[key].get("plan_golive"):
            merged[key]["plan_golive"] = str(group.get("plan_golive", ""))
        if not merged[key].get("combo"):
            merged[key]["combo"] = str(group.get("combo", ""))
    return merged


def _is_placeholder_workflow_item(item: _WorkflowItem) -> bool:
    task = _ascii_fold(_canonicalize_title(item.task)).lower()
    subbacklog = _ascii_fold(_canonicalize_title(item.subbacklog)).lower()
    pic = _ascii_fold(_canonicalize_title(item.pic)).lower()
    status = _ascii_fold(_canonicalize_title(item.status)).lower()
    deadline = _ascii_fold(_canonicalize_title(item.deadline)).lower()
    if task in {"chua trien khai", "chua co task pic trien khai"}:
        return True
    return (
        task in {"chua co task", "chua ro"}
        and subbacklog in {"", "chua ro"}
        and pic in {"", "chua ro"}
        and status in {"", "chua ro"}
        and deadline in {"", "chua chot", "chua ro"}
    )


def _workflow_items_for_popup(workflow_group: dict[str, object] | None) -> list[_WorkflowItem]:
    if not workflow_group:
        return []
    items = workflow_group.get("items", [])
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, _WorkflowItem) and not _is_placeholder_workflow_item(item)]


def _format_display_date(raw: str, dt: datetime | None) -> str:
    if dt is not None:
        return dt.strftime("%d/%m/%Y")
    text = re.sub(r"\s+", " ", (raw or "").strip())
    if not text:
        return "Chưa chốt"
    iso_match = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if iso_match:
        try:
            return datetime(int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3))).strftime("%d/%m/%Y")
        except ValueError:
            return text
    slash_match = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", text)
    if slash_match:
        try:
            year = slash_match.group(3)
            if year is None:
                year_int = 2026
            else:
                year_int = int(year)
                if year_int < 100:
                    year_int += 2000
            return datetime(year_int, int(slash_match.group(2)), int(slash_match.group(1))).strftime("%d/%m/%Y")
        except ValueError:
            return text
    return text


def _summarize_timeline_text(text: str, fallback: str, limit: int = 120) -> str:
    clean = re.sub(r"\s+", " ", (text or "").strip())
    if not clean:
        clean = fallback
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "…"


def _serialize_workflow_task(item: _WorkflowItem) -> dict[str, str]:
    return {
        "subbacklog": item.subbacklog,
        "task": item.task,
        "pic": item.pic,
        "priority": item.priority,
        "status": item.status,
        "deadline": _format_display_date(item.deadline, item.sort_date),
        "sortDate": item.sort_date.isoformat() if item.sort_date else "",
    }


def _serialize_log_event(event: _LogEvent) -> dict[str, str]:
    return {
        "sourceName": event.source_name,
        "sourcePath": event.source_path,
        "sourceChannel": event.source_channel,
        "sourceTitle": event.source_title,
        "raisedBy": event.raised_by,
        "status": event.status,
        "dateLabel": _format_display_date(event.date_label, event.sort_date),
        "sortDate": event.sort_date.isoformat() if event.sort_date else "",
        "detail": event.detail,
    }


def _serialize_root_event(event: _RootEvent) -> dict[str, str]:
    return {
        "sourcePath": event.source_path,
        "sourceChannel": event.source_channel,
        "sourceTitle": event.source_title,
        "raisedBy": event.raised_by,
        "dateLabel": _format_display_date(event.date_label, event.sort_date),
        "sortDate": event.sort_date.isoformat() if event.sort_date else "",
        "detail": event.detail,
    }


def _build_workflow_timeline_groups(
    items: list[_WorkflowItem],
    root_events: list[_RootEvent],
    log_events: list[_LogEvent],
    backlog_source_meta: dict[str, str] | None = None,
) -> list[dict[str, object]]:
    groups: dict[str, dict[str, object]] = {}
    source_meta = backlog_source_meta or {}

    def _prefer_root_channel(channel: str) -> str:
        clean = (channel or "").strip()
        if not clean or clean.startswith("Project-log"):
            return source_meta.get("channel", "")
        return clean

    def _prefer_root_title(title: str, source_name: str) -> str:
        clean = (title or "").strip()
        if not clean:
            return source_meta.get("title", "") or source_name
        normalized = _ascii_fold(_canonicalize_title(clean)).lower()
        if normalized in {
            "decision log",
            "raid log",
            "change cr log",
            "milestone log",
            "retro",
            "alerts",
            "adr",
            "decision-log",
            "raid-log",
            "change-cr-log",
            "milestone-log",
        }:
            return source_meta.get("title", "") or clean
        return clean

    def _prefer_root_raiser(raised_by: str) -> str:
        clean = (raised_by or "").strip()
        return clean or source_meta.get("raised_by", "")

    def _group_key(raw: str, dt: datetime | None, fallback: str, kind: str, source_key: str = "") -> str:
        if kind == "root":
            title_key = _canonicalize_title(source_key or fallback or raw or "event")
            if dt is not None:
                return f"{dt.strftime('%Y-%m-%d')}::{title_key}"
            return f"root::undated::{title_key}"
        if dt is not None:
            return dt.strftime("%Y-%m-%d")
        normalized = _canonicalize_title(raw or fallback or "Ch?a r? ng?y")
        return f"undated::{normalized or 'na'}"

    def _ensure_group(raw: str, dt: datetime | None, title: str, kind: str, source_key: str = "") -> dict[str, object]:
        key = _group_key(raw, dt, title, kind, source_key)
        existing = groups.get(key)
        label = _format_display_date(raw, dt)
        if existing is None:
            default_title = "Event g?c" if kind in {"root", "log"} else ("M?c k? ho?ch / deadline" if dt else "M?c ch?a ch?t")
            existing = {
                "anchorLabel": label,
                "anchorSortDate": dt.isoformat() if dt else "",
                "anchorTitle": default_title,
                "anchorDetail": _summarize_timeline_text(title, label),
                "anchorKind": kind,
                "anchorChannel": "",
                "anchorRaisedBy": "",
                "anchorFlowTitle": "",
                "anchorSourcePath": "",
                "tasks": [],
                "logs": [],
            }
            groups[key] = existing
            return existing
        if kind in {"root", "log"} and existing.get("anchorKind") not in {"root", "log"}:
            existing["anchorKind"] = kind
            existing["anchorTitle"] = "Event g?c"
            existing["anchorDetail"] = _summarize_timeline_text(title, label)
        elif not existing.get("anchorDetail"):
            existing["anchorDetail"] = _summarize_timeline_text(title, label)
        return existing

    def _pick_best_group(candidates: list[dict[str, object]], hint_text: str) -> dict[str, object]:
        if len(candidates) <= 1:
            return candidates[0]
        hint_tokens = _title_tokens(hint_text)
        if not hint_tokens:
            return candidates[-1]
        best_group = candidates[-1]
        best_score = -1.0
        for group in candidates:
            anchor_tokens = _title_tokens(
                " ".join(
                    [
                        str(group.get("anchorFlowTitle") or ""),
                        str(group.get("anchorTitle") or ""),
                        str(group.get("anchorDetail") or ""),
                    ]
                )
            )
            if not anchor_tokens:
                continue
            overlap = hint_tokens & anchor_tokens
            score = len(overlap) / max(len(hint_tokens), 1)
            if score > best_score:
                best_group = group
                best_score = score
        return best_group

    def _resolve_task_anchor(item: _WorkflowItem) -> tuple[str, datetime | None]:
        candidates = _collect_explicit_dates(" ".join([item.task, item.deadline]))
        if candidates:
            if item.sort_date is not None:
                bounded = [candidate for candidate in candidates if candidate[1] <= item.sort_date]
                if bounded:
                    return bounded[-1]
            return candidates[-1]
        return item.deadline, item.sort_date

    def _resolve_log_anchor(event: _LogEvent) -> tuple[str, datetime | None]:
        combined = " ".join([event.source_title, event.detail])
        for pattern in (
            r"mở từ\s*(\d{1,2}/\d{1,2}/\d{2,4})",
            r"phát sinh\s*\((\d{1,2}/\d{1,2}/\d{2,4})\)",
            r"lần gần nhất\s*(\d{1,2}/\d{1,2}/\d{2,4})",
            r"tại họp\s*(\d{1,2}/\d{1,2}/\d{2,4})",
        ):
            match = re.search(pattern, combined, re.I)
            if not match:
                continue
            label, dt = _parse_date_label(match.group(1))
            if dt is not None:
                return label, dt
        candidates = _collect_explicit_dates(combined)
        if candidates:
            if event.sort_date is not None:
                bounded = [candidate for candidate in candidates if candidate[1] <= event.sort_date]
                if bounded:
                    return bounded[-1]
            return candidates[-1]
        return event.date_label, event.sort_date

    for event in sorted(root_events, key=lambda item: (item.sort_date or datetime.max, item.source_title.lower())):
        anchor_title = _summarize_timeline_text(event.detail, event.source_title)
        group = _ensure_group(
            event.date_label,
            event.sort_date,
            anchor_title,
            "root",
            source_key=f"{event.source_path}::{event.source_title}",
        )
        group["anchorKind"] = "root"
        group["anchorChannel"] = event.source_channel or source_meta.get("channel", "") or "T?i li?u"
        group["anchorRaisedBy"] = event.raised_by or source_meta.get("raised_by", "")
        group["anchorFlowTitle"] = event.source_title or source_meta.get("title", "") or anchor_title
        group["anchorSourcePath"] = event.source_path
        group["anchorTitle"] = event.source_title or anchor_title
        group["anchorDetail"] = event.detail or anchor_title

    dated_group_entries: list[tuple[datetime, dict[str, object]]] = []
    for group in groups.values():
        if group.get("anchorKind") != "root":
            continue
        raw_sort = str(group.get("anchorSortDate") or "").strip()
        if not raw_sort:
            continue
        try:
            dated_group_entries.append((datetime.fromisoformat(raw_sort), group))
        except ValueError:
            continue
    dated_group_entries.sort(key=lambda pair: pair[0])

    def _attach_group(dt: datetime | None, hint_text: str) -> dict[str, object] | None:
        if not dated_group_entries:
            return None
        if dt is not None:
            same_day = [entry[1] for entry in dated_group_entries if entry[0].date() == dt.date()]
            if same_day:
                return _pick_best_group(same_day, hint_text)
            nearest_distance = min(abs((entry[0].date() - dt.date()).days) for entry in dated_group_entries)
            if nearest_distance <= 1:
                nearest_candidates = [entry[1] for entry in dated_group_entries if abs((entry[0].date() - dt.date()).days) == nearest_distance]
                return _pick_best_group(nearest_candidates, hint_text)
            previous = [entry for entry in dated_group_entries if entry[0] <= dt]
            if previous:
                latest_dt = previous[-1][0]
                latest_candidates = [entry[1] for entry in previous if entry[0] == latest_dt]
                return _pick_best_group(latest_candidates, hint_text)
            earliest_dt = dated_group_entries[0][0]
            earliest_candidates = [entry[1] for entry in dated_group_entries if entry[0] == earliest_dt]
            return _pick_best_group(earliest_candidates, hint_text)
        latest_dt = dated_group_entries[-1][0]
        latest_candidates = [entry[1] for entry in dated_group_entries if entry[0] == latest_dt]
        return _pick_best_group(latest_candidates, hint_text)

    for event in sorted(log_events, key=lambda item: (item.sort_date or datetime.max, item.source_name.lower())):
        anchor_title = _summarize_timeline_text(event.detail, event.source_name)
        anchor_label, anchor_dt = _resolve_log_anchor(event)
        group = _attach_group(anchor_dt, " ".join([event.source_title, event.detail])) or _ensure_group(anchor_label, anchor_dt, anchor_title, "log")
        resolved_channel = _prefer_root_channel(event.source_channel)
        resolved_title = _prefer_root_title(event.source_title, event.source_name)
        resolved_raiser = _prefer_root_raiser(event.raised_by)
        if resolved_channel and not group.get("anchorChannel"):
            group["anchorChannel"] = resolved_channel
        elif not group.get("anchorChannel"):
            group["anchorChannel"] = source_meta.get("channel", "") or "T?i li?u"
        if resolved_raiser and not group.get("anchorRaisedBy"):
            group["anchorRaisedBy"] = resolved_raiser
        elif not group.get("anchorRaisedBy"):
            group["anchorRaisedBy"] = source_meta.get("raised_by", "")
        if resolved_title and not group.get("anchorFlowTitle"):
            group["anchorFlowTitle"] = resolved_title
        elif not group.get("anchorFlowTitle"):
            group["anchorFlowTitle"] = source_meta.get("title", "") or event.source_name
        if event.source_path and not group.get("anchorSourcePath"):
            group["anchorSourcePath"] = event.source_path
        cast_logs = group["logs"]
        assert isinstance(cast_logs, list)
        cast_logs.append(_serialize_log_event(event))

    for item in sorted(items, key=lambda entry: (entry.sort_date or datetime.max, entry.task.lower())):
        anchor_label, anchor_dt = _resolve_task_anchor(item)
        if dated_group_entries:
            group = _attach_group(anchor_dt, item.task) or _ensure_group(anchor_label, anchor_dt, item.task, "task")
        else:
            group = _ensure_group(anchor_label, anchor_dt, item.task, "task")
        fallback_channel = source_meta.get("channel", "")
        fallback_title = source_meta.get("title", "")
        fallback_raiser = source_meta.get("raised_by", "")
        if not group.get("anchorFlowTitle"):
            group["anchorFlowTitle"] = fallback_title or item.backlog_title or item.task
        if not group.get("anchorRaisedBy"):
            group["anchorRaisedBy"] = fallback_raiser or item.pic
        if not group.get("anchorChannel"):
            group["anchorChannel"] = fallback_channel or "T?i li?u"
        cast_tasks = group["tasks"]
        assert isinstance(cast_tasks, list)
        cast_tasks.append(_serialize_workflow_task(item))

    timeline_groups = sorted(
        groups.values(),
        key=lambda group: (
            1 if not group.get("anchorSortDate") else 0,
            str(group.get("anchorSortDate") or "9999-12-31"),
            str(group.get("anchorTitle") or ""),
        ),
    )
    return timeline_groups


def _build_workflow_indexes(
    workflow_groups: dict[str, dict[str, object]],
) -> tuple[dict[str, dict[str, object]], dict[str, dict[str, object]], list[tuple[set[str], dict[str, object]]]]:
    by_id: dict[str, dict[str, object]] = {}
    by_title: dict[str, dict[str, object]] = {}
    tokenized: list[tuple[set[str], dict[str, object]]] = []
    for group in workflow_groups.values():
        backlog_id = str(group.get("backlog_id", "")).strip().upper()
        title = str(group.get("title", ""))
        if backlog_id:
            by_id[backlog_id] = group
        canonical_title = _canonicalize_title(title)
        if canonical_title:
            by_title[canonical_title] = group
            tokenized.append((_title_tokens(title), group))
    return by_id, by_title, tokenized


def _best_token_match(row_tokens: set[str], candidates: list[tuple[set[str], object]]) -> object | None:
    """Token-overlap fuzzy match dùng chung cho _resolve_workflow_group (cột Workflow) và
    _render_c_gantt_cell (cột Timeline inline trong bảng C, gộp từ tab G — 2026-08-25) — cùng 1
    công thức điểm (overlap / min-length, +0.2 nếu 1 bên là tập con bên kia) và cùng ngưỡng nhận
    >=0.6, để 2 nơi không lệch tiêu chí khi sau này cần chỉnh ngưỡng."""
    best_item: object | None = None
    best_score = 0.0
    for candidate_tokens, item in candidates:
        if not candidate_tokens:
            continue
        overlap = row_tokens & candidate_tokens
        if not overlap:
            continue
        score = len(overlap) / max(min(len(row_tokens), len(candidate_tokens)), 1)
        if row_tokens <= candidate_tokens or candidate_tokens <= row_tokens:
            score += 0.2
        if score > best_score and score >= 0.6:
            best_item = item
            best_score = score
    return best_item


def _resolve_workflow_group(
    backlog_id: str,
    backlog_title: str,
    workflow_groups: dict[str, dict[str, object]],
    workflow_groups_by_id: dict[str, dict[str, object]],
    workflow_groups_by_title: dict[str, dict[str, object]],
    workflow_group_tokens: list[tuple[set[str], dict[str, object]]],
) -> dict[str, object] | None:
    normalized_backlog_id = (backlog_id or "").strip().upper()
    if normalized_backlog_id and normalized_backlog_id in workflow_groups_by_id:
        return workflow_groups_by_id[normalized_backlog_id]
    normalized_key = _normalize_key(backlog_id, backlog_title)
    if normalized_key in workflow_groups:
        return workflow_groups[normalized_key]
    canonical_title = _canonicalize_title(backlog_title)
    if canonical_title and canonical_title in workflow_groups_by_title:
        return workflow_groups_by_title[canonical_title]
    row_tokens = _title_tokens(backlog_title)
    if not row_tokens:
        return None
    return _best_token_match(row_tokens, workflow_group_tokens)


def _infer_log_event_status(frontmatter: dict[str, str], text: str, snippets: list[str]) -> str:
    log_type = _canonicalize_title(frontmatter.get("log_type", "")).lower()
    combined = _ascii_fold(" ".join([text, *snippets])).lower()
    declared_status = _canonicalize_title(frontmatter.get("status", "")).strip()
    if declared_status:
        return declared_status
    if log_type == "alerts":
        return "Cảnh báo"
    if log_type == "decision-log":
        if "superseded" in combined:
            return "Superseded"
        if "da bac bo" in combined or "bi bac bo" in combined:
            return "Bác bỏ"
        if "active" in combined:
            return "Active"
        return "Quyết định"
    if log_type == "raid-log":
        if "overdue" in combined:
            return "Overdue"
        if re.search(r"\bclosed\b|\bda dong\b", combined):
            return "Closed"
        if re.search(r"\bopen\b|\bchua dong\b", combined):
            return "Open"
        return "Issue"
    if log_type == "milestone-log":
        if "doi moc" in combined or "tre" in combined or "delay" in combined:
            return "Dời mốc"
        return "Milestone"
    if log_type == "change-cr-log":
        if "chua duyet" in combined or "chua xac minh" in combined or "pending" in combined:
            return "Pending"
        return "CR"
    if log_type == "raci-daci-register":
        return "Vai trò"
    if log_type == "retro":
        return "Tổng hợp"
    if log_type == "adr":
        return "ADR"
    return "Ghi nhận"


def _collect_log_events(lane: str) -> dict[str, list[_LogEvent]]:
    lane_root = _lane_root(lane)
    if not lane_root.exists():
        return {}
    events: dict[str, list[_LogEvent]] = {}
    for path in lane_root.glob("**/_project-logs/**/*.md"):
        if path.name.upper() == "ALERTS.MD":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        frontmatter = _parse_frontmatter(text)
        source_channel = _detect_log_channel(path, frontmatter)
        source_title = _first_markdown_heading(text) or path.stem
        raised_by = _extract_log_raiser(text, frontmatter)
        ref_channel, ref_title, ref_raiser = _resolve_referenced_source_meta(lane_root, text)
        if ref_channel:
            source_channel = ref_channel
        if ref_title:
            source_title = ref_title
        if ref_raiser:
            raised_by = ref_raiser
        related_ids = {m.group(0).upper() for m in _BACKLOG_ID_RE.finditer(str(path))}
        for line in text.splitlines():
            upper_ids = {m.group(0).upper() for m in _BACKLOG_ID_RE.finditer(line)}
            if upper_ids:
                related_ids.update(upper_ids)
        if not related_ids:
            continue
        for backlog_id in related_ids:
            snippets = []
            for line in text.splitlines():
                if backlog_id in line.upper():
                    clean = re.sub(r"^\s*[-|#>]+\s*", "", _strip_tags(line))
                    if clean:
                        snippets.append(clean)
                if len(snippets) >= 2:
                    break
            if not snippets:
                snippets = [path.stem]
            date_label, sort_dt = _parse_date_label("\n".join(snippets))
            if sort_dt is None:
                last_updated_match = re.search(r"last_updated:\s*([0-9-]+)", text)
                if last_updated_match:
                    try:
                        sort_dt = datetime.strptime(last_updated_match.group(1), "%Y-%m-%d")
                        date_label = last_updated_match.group(1)
                    except ValueError:
                        pass
            status = _infer_log_event_status(frontmatter, text, snippets)
            rel_path = path.relative_to(PROJECT_ROOT.parent).as_posix()
            event = _LogEvent(
                source_name=path.name,
                source_path=rel_path,
                source_channel=source_channel,
                source_title=source_title,
                raised_by=raised_by,
                status=status,
                date_label=date_label,
                sort_date=sort_dt,
                detail=" ".join(snippets),
            )
            events.setdefault(backlog_id, []).append(event)
    return events


def _collect_agent_alert_events(lane: str) -> dict[str, list[_LogEvent]]:
    lane_root = _lane_root(lane)
    if not lane_root.exists():
        return {}
    events: dict[str, list[_LogEvent]] = {}
    for path in lane_root.glob("**/_project-logs/ALERTS.md"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel_path = path.relative_to(PROJECT_ROOT.parent).as_posix()
        for raw_line in text.splitlines():
            line = raw_line.strip()
            match = _ALERT_LINE_RE.match(line)
            if not match:
                continue
            message = match.group("message").strip()
            related_ids = {m.group(0).upper() for m in _BACKLOG_ID_RE.finditer(f"{rel_path} {message}")}
            if not related_ids:
                continue
            time_label = match.group("time").strip()
            sort_dt = None
            date_label = time_label
            if len(time_label) >= 10:
                try:
                    sort_dt = datetime.strptime(time_label[:10], "%Y-%m-%d")
                    date_label = time_label[:10]
                except ValueError:
                    pass
            agent = match.group("agent").strip()
            severity = match.group("severity").strip()
            status_token = (match.group("status") or "MO").strip()
            pic = (match.group("pic") or "").strip()
            status = "Đã xử lý" if status_token == "DA_XU_LY" else f"Alert {severity}"
            detail = f"{message}"
            if pic:
                detail = f"[PIC: {pic}] {detail}"
            event = _LogEvent(
                source_name="AI Agents — Alerts",
                source_path=rel_path,
                source_channel="AI Agents — Alerts",
                source_title=message,
                raised_by=agent,
                status=status,
                date_label=date_label,
                sort_date=sort_dt,
                detail=detail,
            )
            for backlog_id in related_ids:
                events.setdefault(backlog_id, []).append(event)
    return events


def _render_workflow_popup_html(
    slug: str,
    backlog_id: str,
    backlog_title: str,
    workflow_group: dict[str, object],
    root_events: list[_RootEvent],
    log_events: list[_LogEvent],
    backlog_source_meta: dict[str, str] | None = None,
) -> str:
    items = _workflow_items_for_popup(workflow_group)
    timeline_groups = _build_workflow_timeline_groups(items, root_events, log_events, backlog_source_meta)
    serializable = {
        "backlogId": backlog_id or "—",
        "title": backlog_title,
        "combo": str(workflow_group.get("combo", "")),
        "planGolive": str(workflow_group.get("plan_golive", "")),
        "roots": [_serialize_root_event(event) for event in root_events],
        "tasks": [_serialize_workflow_task(item) for item in items],
        "logs": [_serialize_log_event(event) for event in log_events],
        "timelineGroups": timeline_groups,
    }
    return (
        f'<script type="application/json" class="workflow-popup-data" '
        f'data-backlog-key="{_esc(_normalize_key(backlog_id, backlog_title))}" '
        f'data-lane-slug="{_esc(slug)}">{json.dumps(serializable, ensure_ascii=False).replace("</", "<\\/")}</script>'
    )


_H3_RE = re.compile(r"<h3>(.*?)</h3>", re.S)


def _infer_quarter_from_html_text(text: str) -> int:
    normalized = _ascii_fold(_canonicalize_title(text)).lower()
    quarter_patterns = [
        (1, [r"\bquy 1\b", r"\bq1(?:/2026)?\b", r"\bquy i\b"]),
        (2, [r"\bquy 2\b", r"\bq2(?:/2026)?\b", r"\bquy ii\b"]),
        (3, [r"\bquy 3\b", r"\bq3(?:/2026)?\b", r"\bquy iii\b"]),
        (4, [r"\bquy 4\b", r"\bq4(?:/2026)?\b", r"\bquy iv\b"]),
    ]
    quarter_hits: list[int] = []
    for quarter, patterns in quarter_patterns:
        if any(re.search(pattern, normalized) for pattern in patterns):
            quarter_hits.append(quarter)
    if quarter_hits:
        return max(quarter_hits)
    dates = _DATE_RE.findall(_normalize_backlog_text(_strip_tags(text)))
    if dates:
        quarter_votes: list[int] = []
        for label in dates:
            parts = label.split("/")
            if len(parts) >= 2:
                try:
                    month = int(parts[1])
                except ValueError:
                    continue
                quarter_votes.append(((month - 1) // 3) + 1)
        if quarter_votes:
            return max(quarter_votes)
    return 3


def _find_context_h3_text(html: str) -> str:
    candidates = list(_H3_RE.finditer(html))
    if not candidates:
        return ""
    return _strip_tags(candidates[-1].group(1))


def _infer_priority_from_context(*texts: str) -> str:
    combined = " ".join(_ascii_fold(_canonicalize_title(text)).lower() for text in texts if text)
    if "cao" in combined or "high" in combined or "highest" in combined:
        return "Cao"
    if "trung binh" in combined or "medium" in combined or re.search(r"\btb\b", combined):
        return "Trung bình"
    if "thap" in combined or "low" in combined:
        return "Thấp"
    return "—"


def _quarter_deadline_label(quarter: int) -> str:
    return f"Q{quarter}/2026"


_QUARTER_DEADLINE_RE = re.compile(r"\bQ(?:u[ýy]?)?\.?\s*(\d)\s*[/\-]\s*(\d{4})\b", re.I)
_EXACT_DATE_DEADLINE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_STATUS_TAG_RE = re.compile(r'class="status-tag (\w+)"')


def _row_status_from_cell(status_cell_html: str) -> str | None:
    """Đọc trạng thái Timeline (open/progress/done/blocked/cancelled) trực tiếp từ ô "Trạng thái /
    Ghi chú" GỐC của CHÍNH dòng trong bảng C. "cancel"/"đóng yêu cầu"/"đóng tiếp nhận" trong TEXT
    được tách riêng thành "cancelled" dù span gốc dùng chung 1 class css "status-tag blocked" cho cả
    2 trường hợp (đã huỷ vs thật sự đang bị chặn/rủi ro — 2 nghĩa khác nhau, gộp chung sẽ tô nhầm
    màu đỏ "Rủi ro/quá hạn" cho 1 backlog đã Cancelled)."""
    text = _strip_tags(status_cell_html).lower()
    if "cancel" in text or "đóng yêu cầu" in text or "đóng tiếp nhận" in text:
        return "cancelled"
    status_match = _STATUS_TAG_RE.search(status_cell_html)
    return status_match.group(1) if status_match else None


def _quarter_bounds(quarter: int, year: int) -> tuple[datetime, datetime]:
    start_month = (quarter - 1) * 3 + 1
    start = datetime(year, start_month, 1)
    end = datetime(year, 12, 31) if start_month == 10 else datetime(year, start_month + 3, 1) - timedelta(days=1)
    return start, end


def _parse_deadline_window(text: str) -> tuple[datetime, datetime] | None:
    """Suy ra 1 khung [start, end] từ text cột "Mốc / Deadline" GỐC (caller phải tự loại giá trị
    mono-synthetic — nhãn Quý tự sinh khi report không có cột deadline thật, xem
    _group_c_section_by_quarter — nếu không sẽ suy ra khung Gantt từ dữ liệu KHÔNG có thật). Ngày
    cụ thể dd/mm/yyyy -> khung 1 ngày; "Qx/yyyy" -> cả quý đó. Không cố đoán các dạng tự do khác
    (vd "T8–T9/2026", "Trong 09/2026") — thà bỏ qua còn hơn suy diễn sai từ text rời rạc."""
    text = _strip_tags(text)
    m = _EXACT_DATE_DEADLINE_RE.search(text)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            dt = datetime(y, mo, d)
        except ValueError:
            return None
        return dt, dt
    m = _QUARTER_DEADLINE_RE.search(text)
    if m:
        q, y = int(m.group(1)), int(m.group(2))
        if not 1 <= q <= 4:
            return None
        return _quarter_bounds(q, y)
    return None


def _window_bar_style(
    axis_start: datetime | None, axis_end: datetime | None, window: tuple[datetime, datetime] | None
) -> str | None:
    if not window or not axis_start or not axis_end:
        return None
    start_pct = timeline_percent(axis_start, axis_end, window[0])
    end_pct = timeline_percent(axis_start, axis_end, window[1])
    width_pct = max(end_pct - start_pct, 1.2)  # sàn 1.2% để mốc 1 ngày vẫn thấy được, không biến mất
    return f"left:{start_pct:.1f}%;width:{width_pct:.1f}%;"


def _render_c_gantt_cell(
    backlog_cell_text: str,
    title_text: str,
    deadline_window: tuple[datetime, datetime] | None,
    row_status: str | None,
    rows_by_key: dict,
    title_tokens_index: list[tuple[set[str], object]],
    axis_start: datetime | None,
    axis_end: datetime | None,
    today_pct: float | None,
) -> str:
    """1 ô "Timeline" trong bảng C — thanh Gantt mini kiểu MS Project ngay tại dòng backlog, thay
    cho tab "G. Timeline" riêng đã gộp vào đây (2026-08-25). Ghép với TimelineRow theo backlog-id
    (normalize_backlog_key). CHỈ fallback fuzzy-match theo token tiêu đề (cùng cơ chế/ngưỡng >=0.6
    ở _best_token_match, dùng chung với cột Workflow/_resolve_workflow_group) khi ô Backlog/Mã
    KHÔNG hề chứa mã QLYC/YC/RE/PA nào (vd lane IBD — backlog rời rạc từ chat Zalo, không có mã) —
    nếu ô ĐÃ có mã hợp lệ nhưng đơn giản là chưa có trong dữ liệu Timeline (backlog mới, tab G chưa
    kịp cập nhật) thì KHÔNG đoán theo tiêu đề, tránh gắn nhầm bar/status của 1 backlog khác có tiêu
    đề gần giống sang dòng này. TÁI SỬ DỤNG NGUYÊN style % left/width đã tính sẵn khi có bar thật —
    không tính lại (xem timeline_sync.load_lane_timeline_bars). Khi backlog CHƯA có bar thật nhưng
    cột "Mốc / Deadline" của chính dòng có mốc thật (không phải synthetic), `deadline_window` (đã
    parse sẵn ở caller) cho phép vẽ 1 khung xấp xỉ theo đúng Quý/ngày đó thay vì phủ toàn track
    (thêm 2026-08-25, theo yêu cầu người dùng). `row_status` (đọc từ span "status-tag ..." ngay
    trong ô "Trạng thái / Ghi chú" của CHÍNH dòng — xem _row_status_from_cell) LUÔN được ưu tiên làm
    màu bar hơn trow.resolved_status, vì resolved_status suy từ 1 nguồn tài liệu cũ/khác nên có thể
    lệch với trạng thái mới nhất ghi ngay trong report này (phản hồi người dùng 2026-08-25, case
    QLYC-9567/9509/10594: cột ghi "Cancelled" nhưng bar cũ tô "Kế hoạch còn lại")."""
    backlog_id_match = _BACKLOG_ID_RE.search(_normalize_backlog_text(backlog_cell_text))
    key = normalize_backlog_key(backlog_id_match.group(0)) if backlog_id_match else ""
    trow = rows_by_key.get(key) if key else None
    if trow is None and not backlog_id_match and title_tokens_index:
        row_tokens = _title_tokens(title_text)
        if row_tokens:
            trow = _best_token_match(row_tokens, title_tokens_index)

    window_style = _window_bar_style(axis_start, axis_end, deadline_window)

    if trow is None:
        # Không khớp được TimelineRow nào (không có mã, hoặc có mã nhưng chưa soát ở tab Timeline
        # cũ) — vẫn có thể vẽ được nếu cột Mốc/Deadline của chính dòng có mốc thật, kết hợp trạng
        # thái đọc trực tiếp từ span "status-tag ..." gốc trong bảng C (không suy đoán nếu thiếu 1
        # trong 2 — thà để trống còn hơn vẽ 1 bar không rõ căn cứ vào đâu).
        if window_style and row_status:
            status_css, status_label = status_display(row_status)
            tooltip = f"{status_label} (suy từ Mốc/Deadline)"
            return (
                '<td class="c-gantt-td"><div class="c-gantt-track">'
                f'<div class="c-gantt-bar {status_css} tl-inferred" style="{window_style}" title="{_esc(tooltip)}"></div>'
                "</div></td>"
            )
        return '<td class="c-gantt-td"><span class="c-gantt-none">—</span></td>'
    # Ưu tiên trạng thái đọc trực tiếp từ CHÍNH dòng này trong bảng C (row_status) hơn
    # trow.resolved_status — vì resolved_status suy từ 1 nguồn tài liệu cũ/khác (xem
    # timeline_sync._resolve_row_status) có thể KHÔNG đồng bộ với trạng thái mới nhất đã ghi ngay
    # trong report này, dẫn tới lệch (vd cột Trạng thái ghi "Cancelled" nhưng bar lại tô "Kế hoạch
    # còn lại" vì G-section không hề nhắc chữ đó trong text — phản hồi người dùng 2026-08-25).
    status = row_status or trow.resolved_status or trow.manual_status or "open"
    status_css, status_label = status_display(status)
    tooltip = status_label + (f" — {trow.track_title}" if trow.track_title else "")
    parts: list[str] = []
    if trow.bar:
        inferred = " tl-inferred" if "tl-inferred" in trow.bar.class_name else ""
        parts.append(
            f'<div class="c-gantt-bar {status_css}{inferred}" style="{trow.bar.style}"'
            f' title="{_esc(tooltip)}"></div>'
        )
    else:
        # Không có mốc ngày cụ thể trong TimelineRow (is_empty ở tab Timeline cũ) — ưu tiên khung
        # suy từ cột Mốc/Deadline của chính dòng (chính xác hơn) nếu có, không thì mới phủ toàn
        # track như trước. Luôn giữ hoạ tiết sọc chéo tl-inferred vì đây KHÔNG phải mốc đã soát thủ
        # công — tránh lẫn với bar thật (phản hồi người dùng 2026-08-25).
        style = window_style or "left:0%;width:100%;"
        parts.append(f'<div class="c-gantt-bar {status_css} tl-inferred" style="{style}" title="{_esc(tooltip)}"></div>')
    for marker in trow.markers:
        title_attr = f' title="{_esc(marker.title)}"' if marker.title else ""
        parts.append(f'<div class="c-gantt-marker {marker.class_name}" style="{marker.style}"{title_attr}></div>')
    if today_pct is not None:
        parts.append(f'<div class="c-gantt-marker today" style="left:{today_pct:.1f}%;" title="Hôm nay"></div>')
    return f'<td class="c-gantt-td"><div class="c-gantt-track">{"".join(parts)}</div></td>'


def _group_c_section_by_quarter(c_content: str) -> str:
    if "quarter-h" in c_content or "<table" not in c_content:
        return c_content
    popup_shell = ""
    popup_match = re.search(r'(<div class="workflow-popup-shell" hidden>.*?</div>)\s*$', c_content, re.S)
    if popup_match:
        popup_shell = popup_match.group(1)
        c_content = c_content[: popup_match.start()].rstrip()

    def _strip_empty_table_wraps(html: str) -> str:
        return re.sub(r'<div class="table-wrap">\s*</div>', "", html, flags=re.S)

    table_matches = list(_TABLE_RE.finditer(c_content))
    if not table_matches:
        return c_content + popup_shell

    prefix_parts: list[str] = []
    rows_by_quarter: dict[int, list[str]] = {1: [], 2: [], 3: [], 4: []}
    counts_by_quarter: dict[int, int] = {1: 0, 2: 0, 3: 0, 4: 0}
    cursor = 0
    base_attrs: str | None = None
    canonical_header = (
        "<tr><th class=\"stt-col\">STT</th><th style=\"width:14%\">Nhóm nguồn</th>"
        "<th style=\"width:12%\">Backlog / Mã</th><th>Tên yêu cầu / Nội dung</th>"
        "<th style=\"width:18%\">Trạng thái / Ghi chú</th><th style=\"width:10%\">Ưu tiên</th>"
        "<th style=\"width:12%\">Mốc / Deadline</th><th>Workflow</th></tr>"
    )

    for table_match in table_matches:
        segment_before = c_content[cursor:table_match.start()]
        leading = _strip_empty_table_wraps(_H3_RE.sub("", segment_before))
        context_h3 = _find_context_h3_text(segment_before)
        if leading.strip():
            prefix_parts.append(leading)

        parsed = _parse_table(table_match.group(0))
        if base_attrs is None:
            base_attrs = "".join(f' {k}="{_html.escape(v, quote=True)}"' for k, v in parsed.attrs.items() if v)
        header_row, body_rows = _split_table_header_and_body(parsed)
        if not header_row:
            cursor = table_match.end()
            continue
        backlog_idx = _header_index(header_row, "backlog")
        title_idx = _header_index(header_row, "tên", "yêu cầu")
        if title_idx < 0:
            title_idx = _header_index(header_row, "nội", "dung")
        source_idx = _header_index(header_row, "nhóm", "nguồn")
        if source_idx < 0:
            source_idx = _header_index(header_row, "nhóm", "sáng")
        if source_idx < 0:
            source_idx = _header_index(header_row, "nguồn")
        status_idx = _header_index(header_row, "trạng", "thái")
        if status_idx < 0:
            status_idx = _header_index(header_row, "ghi", "chú")
        priority_idx = _header_index(header_row, "ưu", "tiên")
        deadline_idx = _header_index(header_row, "mốc")
        if deadline_idx < 0:
            deadline_idx = _header_index(header_row, "deadline")
        if deadline_idx < 0:
            deadline_idx = _header_index(header_row, "cam", "kết")
        workflow_idx = _header_index(header_row, "workflow")
        for row in body_rows:
            quarter = _infer_quarter_from_html_text(" ".join(cell.text for cell in row))
            backlog_td = _cell_with_tag(row[backlog_idx]) if backlog_idx >= 0 and len(row) > backlog_idx else '<td class="mono">—</td>'
            title_td = _cell_with_tag(row[title_idx]) if title_idx >= 0 and len(row) > title_idx else "<td>—</td>"
            source_td = (
                _cell_with_tag(row[source_idx])
                if source_idx >= 0 and len(row) > source_idx
                else f"<td>{_esc('CIO Report chính thức')}</td>"
            )
            status_td = (
                _cell_with_tag(row[status_idx])
                if status_idx >= 0 and len(row) > status_idx
                else f"<td>{_esc('—')}</td>"
            )
            priority_value = row[priority_idx].text if priority_idx >= 0 and len(row) > priority_idx else _infer_priority_from_context(context_h3)
            priority_td = (
                _cell_with_tag(row[priority_idx])
                if priority_idx >= 0 and len(row) > priority_idx
                else f"<td>{_esc(priority_value or '—')}</td>"
            )
            deadline_text = row[deadline_idx].text if deadline_idx >= 0 and len(row) > deadline_idx else _quarter_deadline_label(quarter)
            deadline_td = (
                _cell_with_tag(row[deadline_idx])
                if deadline_idx >= 0 and len(row) > deadline_idx
                # "mono-synthetic": KHÔNG phải mốc thật từ nguồn — chỉ là nhãn Quý đã dùng để nhóm
                # bảng (report này không có cột Mốc/Deadline thật). _inject_gantt_column phải nhận
                # diện được cờ này để KHÔNG suy ra khung Gantt từ giá trị "mốc" giả này (nếu không sẽ
                # tự chế ra 1 khung thời gian nhìn như thật cho MỌI dòng của lane, dù nguồn không hề
                # có dữ liệu — phát hiện khi rà lại DIP/Magnet/MSBPay/MConnect 2026-08-25).
                else f'<td><span class="mono mono-synthetic">{_esc(deadline_text)}</span></td>'
            )
            workflow_td = (
                _cell_with_tag(row[workflow_idx])
                if workflow_idx >= 0 and len(row) > workflow_idx
                else '<td><span class="workflow-empty">—</span></td>'
            )
            canonical_row = (
                "<tr>"
                + f'<td class="stt-col">{{stt}}</td>'
                + source_td
                + backlog_td
                + title_td
                + status_td
                + priority_td
                + deadline_td
                + workflow_td
                + "</tr>"
            )
            rows_by_quarter[quarter].append(canonical_row)
            counts_by_quarter[quarter] += 1
        cursor = table_match.end()

    trailing = c_content[cursor:]
    if trailing.strip():
        prefix_parts.append(_strip_empty_table_wraps(_H3_RE.sub("", trailing)))

    if base_attrs is None:
        return "".join(prefix_parts) + popup_shell

    quarter_sections: list[str] = []
    for quarter in range(1, 5):
        quarter_sections.append(f'<h4 class="quarter-h">Quý {quarter}/2026 ({counts_by_quarter[quarter]} mục)</h4>')
        if rows_by_quarter[quarter]:
            body_rows_html = [
                row_html.replace("{stt}", str(idx))
                for idx, row_html in enumerate(rows_by_quarter[quarter], 1)
            ]
            quarter_sections.append(
                '<div class="table-wrap">'
                + f"<table{base_attrs}><thead>{canonical_header}</thead><tbody>{''.join(body_rows_html)}</tbody></table>"
                + "</div>"
            )
        else:
            quarter_sections.append(
                f'<p class="quarter-empty">Không có backlog nào được nguồn hiện tại gắn vào Quý {quarter}/2026.</p>'
            )

    final_html = "".join(prefix_parts) + "".join(quarter_sections) + popup_shell
    return re.sub(r'\s*<div class="table-wrap">\s*</div>\s*', "", final_html, flags=re.S)


def _inject_gantt_column(c_content: str, timeline_index: dict | None) -> str:
    """Chèn cột "Timeline" (Gantt mini kiểu MS Project, gộp từ tab "G. Timeline" riêng — 2026-08-25)
    vào MỌI <table> có cột "Backlog" trong tab C. Chạy SAU _group_c_section_by_quarter, không phải
    bên trong nó — vì 1 số report (vd CIO-EKYC.html) đã tự nhóm sẵn theo Quý ngay trong nguồn
    (<h4 class="quarter-h">...) nên _group_c_section_by_quarter bail sớm (guard "quarter-h" ở đầu
    hàm) và KHÔNG bao giờ chạm vào những table đó. Tách thành 1 pass riêng áp dụng đều cho mọi
    <table>, dù đến từ auto-group hay đã nhóm sẵn trong nguồn, để lane nào cũng có cột này."""
    if timeline_index is None or "<table" not in c_content:
        return c_content
    rows_by_key: dict = timeline_index.get("rows_by_key") or {}
    title_tokens_index = [
        (_title_tokens(title), row) for title, row in (timeline_index.get("rows_by_title") or []) if _title_tokens(title)
    ]
    months: list[str] = timeline_index.get("months") or []
    today_pct = timeline_index.get("today_pct")
    report_date = timeline_index.get("report_date") or ""
    axis_start = timeline_index.get("axis_start")
    axis_end = timeline_index.get("axis_end")
    gantt_th = (
        f'<th class="c-gantt-th"><div class="c-gantt-scale">{"".join(f"<span>{_esc(m)}</span>" for m in months)}</div></th>'
        if months
        else '<th class="c-gantt-th">Timeline</th>'
    )

    def inject(match: re.Match[str]) -> str:
        table_html = match.group(0)
        parsed = _parse_table(table_html)
        header_row, body_rows = _split_table_header_and_body(parsed)
        if not header_row or _header_index(header_row, "backlog") < 0:
            return table_html
        backlog_idx = _header_index(header_row, "backlog")
        title_idx = _header_index(header_row, "tên", "yêu cầu")
        if title_idx < 0:
            title_idx = _header_index(header_row, "nội", "dung")
        deadline_idx = _header_index(header_row, "mốc")
        if deadline_idx < 0:
            deadline_idx = _header_index(header_row, "deadline")
        if deadline_idx < 0:
            deadline_idx = _header_index(header_row, "cam", "kết")
        status_idx = _header_index(header_row, "trạng", "thái")
        if status_idx < 0:
            status_idx = _header_index(header_row, "ghi", "chú")

        def render_header_cell(cell: _HtmlCell, idx: int) -> str:
            # Cột "Tên yêu cầu / Nội dung" vốn không có width cố định (co giãn tự do) — giờ thêm
            # min-width để bảng không bóp nó xuống 1 cột chữ hẹp dựng đứng khi cộng thêm cột
            # Timeline mới (làm bảng vượt quá 100% container thì nên cuộn ngang qua .table-wrap,
            # KHÔNG nên co cột nội dung xuống gần 0 — đây chính là lỗi UI người dùng báo lại).
            if idx != title_idx:
                return _cell_html(cell)
            attrs = dict(cell.attrs)
            existing_style = attrs.get("style", "").strip()
            attrs["style"] = "min-width:280px;" + (f" {existing_style}" if existing_style else "")
            attr_text = "".join(f' {k}="{_html.escape(v, quote=True)}"' for k, v in attrs.items() if v)
            return f"<{cell.tag}{attr_text}>{cell.inner_html}</{cell.tag}>"

        head_html = "".join(
            "<tr>" + "".join(render_header_cell(cell, idx) for idx, cell in enumerate(row)) + "<th>Timeline</th></tr>"
            for row in parsed.header_rows[:-1]
        )
        last_header_html = (
            "<tr>"
            + "".join(render_header_cell(cell, idx) for idx, cell in enumerate(parsed.header_rows[-1]))
            + gantt_th
            + "</tr>"
            if parsed.header_rows
            else ""
        )
        body_html_parts = []
        for row in body_rows:
            backlog_text = row[backlog_idx].text if len(row) > backlog_idx else ""
            title_text = row[title_idx].text if title_idx >= 0 and len(row) > title_idx else ""
            deadline_window = None
            if deadline_idx >= 0 and len(row) > deadline_idx:
                deadline_cell = row[deadline_idx]
                if "mono-synthetic" not in deadline_cell.inner_html:
                    deadline_window = _parse_deadline_window(deadline_cell.text)
            row_status = _row_status_from_cell(row[status_idx].inner_html) if status_idx >= 0 and len(row) > status_idx else None
            gantt_td = _render_c_gantt_cell(
                backlog_text,
                title_text,
                deadline_window,
                row_status,
                rows_by_key,
                title_tokens_index,
                axis_start,
                axis_end,
                today_pct,
            )
            body_html_parts.append("<tr>" + "".join(_cell_html(cell) for cell in row) + gantt_td + "</tr>")
        attrs = "".join(f' {k}="{_html.escape(v, quote=True)}"' for k, v in parsed.attrs.items() if v)
        return f"<table{attrs}><thead>{head_html}{last_header_html}</thead><tbody>{''.join(body_html_parts)}</tbody></table>"

    injected = _TABLE_RE.sub(inject, c_content)
    if not months:
        return injected

    legend_html = (
        '<div class="c-gantt-legend">'
        '<span class="c-gantt-legend-item"><span class="c-gantt-swatch tl-done"></span>Hoàn thành</span>'
        '<span class="c-gantt-legend-item"><span class="c-gantt-swatch tl-progress"></span>Đang triển khai</span>'
        '<span class="c-gantt-legend-item"><span class="c-gantt-swatch tl-open"></span>Kế hoạch còn lại</span>'
        '<span class="c-gantt-legend-item"><span class="c-gantt-swatch tl-onhold"></span>On-Hold</span>'
        '<span class="c-gantt-legend-item"><span class="c-gantt-swatch tl-cancelled"></span>Đóng YC/Cancelled</span>'
        '<span class="c-gantt-legend-item"><span class="c-gantt-swatch tl-blocked"></span>Rủi ro/quá hạn</span>'
        f'<span class="c-gantt-legend-item"><span class="c-gantt-swatch today"></span>Hôm nay ({_esc(report_date)})</span>'
        "</div>"
    )
    h4_match = re.search(r'<h4 class="quarter-h">', injected)
    if h4_match:
        insert_pos = h4_match.start()
    else:
        table_match = re.search(r'<table\b', injected)
        if not table_match:
            return legend_html + injected
        wrap_match = re.search(r'<div class="table-wrap">\s*$', injected[: table_match.start()])
        insert_pos = wrap_match.start() if wrap_match else table_match.start()
    return injected[:insert_pos] + legend_html + injected[insert_pos:]


def _enhance_c_section(lane: str, slug: str, c_content: str, f_content: str, timeline_index: dict | None = None) -> str:
    workflow_groups = _merge_workflow_groups(
        _extract_workflow_groups(f_content),
        _load_supplemental_workflow_groups(lane),
    )
    if not workflow_groups:
        return _inject_gantt_column(c_content, timeline_index)
    workflow_groups_by_id, workflow_groups_by_title, workflow_group_tokens = _build_workflow_indexes(workflow_groups)
    root_events_by_id = _collect_root_events(lane)
    log_events_by_id = _collect_log_events(lane)
    for backlog_id, alert_events in _collect_agent_alert_events(lane).items():
        log_events_by_id.setdefault(backlog_id, []).extend(alert_events)
    backlog_source_meta_by_id = _collect_backlog_source_meta(lane)
    lane_source_candidates = _collect_lane_source_candidates(lane)
    popup_blocks: list[str] = []
    seen_popup_keys: set[tuple[str, str]] = set()

    def replace_table(match: re.Match[str]) -> str:
        table_html = match.group(0)
        parsed = _parse_table(table_html)
        header_row, body_rows = _split_table_header_and_body(parsed)
        if not header_row:
            return table_html
        header_text = " | ".join(cell.text.lower() for cell in header_row)
        if "backlog" not in header_text:
            return table_html
        backlog_idx = _header_index(header_row, "backlog")
        title_idx = _header_index(header_row, "tên", "yêu cầu")
        if title_idx < 0:
            title_idx = _header_index(header_row, "nội", "dung")
        if backlog_idx < 0:
            return table_html

        head_html = "".join(
            "<tr>"
            + "".join(_cell_html(cell) for cell in row)
            + "<th>Workflow</th></tr>"
            for row in parsed.header_rows[:-1]
        )
        if parsed.header_rows:
            last_header = parsed.header_rows[-1]
            last_header_html = "<tr>" + "".join(_cell_html(cell) for cell in last_header) + "<th>Workflow</th></tr>"
        else:
            last_header_html = "<tr>" + "".join(_cell_html(cell, force_class="") for cell in header_row) + "<th>Workflow</th></tr>"
        body_html_parts: list[str] = []
        for row in body_rows:
            if title_idx >= 0 and len(row) > max(backlog_idx, title_idx):
                backlog_id_match = _BACKLOG_ID_RE.search(_normalize_backlog_text(row[backlog_idx].text))
                backlog_id = backlog_id_match.group(0).upper() if backlog_id_match else ""
                backlog_title = _strip_tags(row[title_idx].inner_html) or row[title_idx].text
                _plan_golive = ""
            else:
                if len(row) <= backlog_idx:
                    body_html_parts.append("<tr>" + "".join(_cell_html(cell) for cell in row) + '<td><span class="workflow-empty">—</span></td></tr>')
                    continue
                backlog_cell = row[backlog_idx]
                backlog_id, backlog_title, _plan_golive = _extract_backlog_parts(backlog_cell)
            workflow_group = _resolve_workflow_group(
                backlog_id,
                backlog_title,
                workflow_groups,
                workflow_groups_by_id,
                workflow_groups_by_title,
                workflow_group_tokens,
            )
            resolved_backlog_id = backlog_id or (str(workflow_group.get("backlog_id", "")) if workflow_group else "")
            resolved_backlog_title = backlog_title or (str(workflow_group.get("title", "")) if workflow_group else "")
            backlog_key = _normalize_key(resolved_backlog_id, resolved_backlog_title)
            lookup_backlog_id = (resolved_backlog_id or backlog_id).upper() if (resolved_backlog_id or backlog_id) else ""
            root_events = root_events_by_id.get(lookup_backlog_id, []) if lookup_backlog_id else []
            log_events = log_events_by_id.get(lookup_backlog_id, []) if lookup_backlog_id else []
            popup_items = _workflow_items_for_popup(workflow_group)
            has_popup = bool(popup_items or log_events or root_events)
            if has_popup:
                popup_identity = (slug, backlog_key)
                if popup_identity not in seen_popup_keys:
                    source_meta = backlog_source_meta_by_id.get((resolved_backlog_id or "").upper(), {})
                    if not source_meta.get("channel"):
                        source_meta = {**source_meta, **_resolve_source_meta_by_title(resolved_backlog_title, lane_source_candidates)}
                    if not source_meta.get("channel"):
                        source_meta = {**source_meta, "channel": "Tài liệu"}
                    popup_blocks.append(
                        _render_workflow_popup_html(
                            slug,
                            resolved_backlog_id or "—",
                            resolved_backlog_title,
                            workflow_group or {"combo": "", "plan_golive": "", "items": []},
                            root_events,
                            log_events,
                            source_meta,
                        )
                    )
                    seen_popup_keys.add(popup_identity)
                count = len(popup_items)
                extra = f" · {len(log_events)} log" if log_events else ""
                button_html = (
                    f'<button type="button" class="workflow-open-btn"'
                    f' data-backlog-key="{_esc(backlog_key)}" data-lane-slug="{_esc(slug)}"'
                    f' onclick="window.__openWorkflowPopup && window.__openWorkflowPopup(this)">'
                    f'Xem timeline <span>{count} task{extra}</span></button>'
                )
            else:
                button_html = '<span class="workflow-empty">—</span>'
            body_html_parts.append(
                "<tr>" + "".join(_cell_html(cell) for cell in row) + f"<td>{button_html}</td></tr>"
            )
        attrs = "".join(f' {k}="{_html.escape(v, quote=True)}"' for k, v in parsed.attrs.items() if v)
        return (
            f"<table{attrs}><thead>{head_html}{last_header_html}</thead>"
            f"<tbody>{''.join(body_html_parts)}</tbody></table>"
        )

    enhanced = _TABLE_RE.sub(replace_table, c_content)
    if popup_blocks:
        enhanced += (
            '<div class="workflow-popup-shell" hidden>'
            f"{''.join(popup_blocks)}"
            "</div>"
        )
    return _inject_gantt_column(_group_c_section_by_quarter(enhanced), timeline_index)


def _priority_badge(p: str) -> str:
    cls = _PRIORITY_CLASS.get(p, "tb")
    return f'<span class="prio {cls}"><span class="dot"></span>{_esc(p or "Chưa rõ")}</span>'


def _status_badge(s: str) -> str:
    cls = _STATUS_CLASS.get(s, "open")
    return f'<span class="status-tag {cls}">{_esc(s or "Chưa rõ")}</span>'


def _severity_badge(sv: str) -> str:
    cls = _SEVERITY_CLASS.get(sv, "warning")
    return f'<span class="badge {cls}"><span class="dot"></span>{_esc(sv or "Chưa rõ")}</span>'


def _tasks_table(tasks: list[dict]) -> str:
    if not tasks:
        return '<p class="dep-none">Không có task nào được nhận diện cho lane này.</p>'
    rows = []
    for t in tasks:
        rows.append(
            "<tr>"
            f"<td>{_esc(t.get('task'))}"
            f'<div class="task-src">{_esc(t.get("channel"))} · {_esc(t.get("source_path"))}</div></td>'
            f"<td>{_esc(t.get('pic') or 'Chưa rõ')}</td>"
            f"<td>{_esc(t.get('support') or 'Chưa nêu rõ')}</td>"
            f"<td>{_priority_badge(t.get('priority'))}</td>"
            f"<td>{_esc(t.get('dependency') or 'Không nêu rõ')}</td>"
            f"<td>{_status_badge(t.get('status'))}</td>"
            f"<td class=\"mono\">{_esc(t.get('deadline') or 'Chưa chốt mốc')}</td>"
            "</tr>"
        )
    return f"""<div class="table-wrap"><table>
      <thead><tr><th>Task</th><th>PIC</th><th>Support</th><th>Priority</th><th>Dependency</th><th>Status</th><th>Deadline</th></tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table></div>"""


def _risks_table(risks: list[dict]) -> str:
    if not risks:
        return '<p class="dep-none">Không có rủi ro nổi bật nào được nhận diện.</p>'
    rows = []
    for r in risks:
        rows.append(
            "<tr>"
            f"<td>{_esc(r.get('description'))}"
            f'<div class="task-src">{_esc(r.get("source_path"))}</div></td>'
            f"<td>{_severity_badge(r.get('severity'))}</td>"
            f"<td>{_esc(r.get('related_task') or '—')}</td>"
            "</tr>"
        )
    return f"""<div class="table-wrap"><table>
      <thead><tr><th>Rủi ro / vướng mắc</th><th>Mức độ</th><th>Task liên quan</th></tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table></div>"""


def _lane_panel(lr: "LaneResult") -> str:
    """Fallback khi lane CHƯA có CIO report thủ công (report_id rỗng/file chưa tồn tại) — hiển thị
    tổng hợp LLM tự động thay cho bảng E. Lane đã có report dùng thẳng section E gốc, không qua
    hàm này (xem _cio_tabs_html)."""
    if lr.preserved_e_html:
        notice = _esc(lr.preserved_notice or "Đang dùng lại dữ liệu tab E từ lần build trước.")
        detail = f"<div class=\"stale-detail\"><code>{_esc(lr.error or '')}</code></div>" if lr.error else ""
        return f"""
        <div class="caveat stale"><strong>Đang giữ dữ liệu tab E cũ.</strong> {notice}{detail}</div>
        {lr.preserved_e_html}
        """
    error_block = (
        f'<div class="caveat">Lỗi tổng hợp lane này (LLM trả về không đúng định dạng, hoặc gọi API lỗi): '
        f"{_esc(lr.error)}</div>"
        if lr.error
        else ""
    )
    return f"""
    {error_block}
    <p class="narrative">{_esc(lr.summary) or "(không có tóm tắt)"}
    <span class="task-src">Đã đọc {lr.n_sources} nguồn cho lane này.</span></p>
    <h3>Rủi ro / vướng mắc</h3>
    {_risks_table(lr.risks)}
    <h3>Task</h3>
    {_tasks_table(lr.tasks)}
    """


def _load_cio_report(report_id: str | None) -> str | None:
    if not report_id:
        return None
    path = resolve_cio_report_path(report_id)
    if not path:
        return None
    return path.read_text(encoding="utf-8")


_MERMAID_JS_PATH = PROJECT_ROOT / "src" / "graphrag" / "generation" / "assets" / "mermaid.min.js"


def _load_mermaid_js() -> str:
    """DEPRECATED (v1.3): Tab F giờ là bảng HTML tĩnh, không cần Mermaid JS nữa.
    Giữ lại hàm để không break import cũ nếu có, nhưng trả rỗng."""
    return ""


def _extract_cio_header(report_html: str) -> str:
    m = _HEADER_RE.search(report_html)
    return m.group(1).strip() if m else ""


def _extract_cio_footer(report_html: str) -> str:
    """Khối <footer> (trích dẫn nguồn "Nguồn A-C/D/E: ...") nằm NGOÀI 5 <section>, nên
    _extract_cio_sections() không tự lấy được — trước 2026-08-06 phần này bị bỏ sót hoàn toàn khi
    fold vào DASHBOARD.html (không sao vì lúc đó CIO-<ID>.html còn tồn tại song song để tra cứu
    nguồn). Từ khi CIO-<ID>.html chỉ còn là file build tạm rồi bị archive, DASHBOARD.html phải tự
    mang theo trích dẫn nguồn này — xem _cio_tabs_html()."""
    m = _FOOTER_RE.search(report_html)
    return m.group(1).strip() if m else ""


def _extract_cio_sections(report_html: str) -> dict[str, str]:
    """Tách các <section> phẳng (A-F, F thêm 2026-08-06 = tab WorkFlow Mermaid) của 1 file
    reports/CIO-<ID>.html — đã xác nhận cấu trúc này nhất quán trên cả 6 report thật (không
    <section> lồng nhau) trước khi dùng regex ở đây."""
    sections: dict[str, str] = {}
    for block in _SECTION_RE.findall(report_html):
        m = _SECTION_LETTER_RE.match(block)
        if m:
            sections[m.group(1)] = block.strip()
    return sections


def load_manual_e_data(report_id: str) -> tuple[list[dict], list[dict], int]:
    """Đọc bảng E thủ công (đã soát tay, cấu trúc cố định theo TEMPLATE.html: nhiều bảng con
    Task/PIC/Support/Priority/Dependency/Status/Deadline/Service liên quan nhóm dưới từng <h3>
    nguồn) từ reports/<report_id>.html, trả ra đúng shape dict mà pipeline.dashboard.LaneResult /
    _tasks_table() đang dùng. Thay thế bước gọi LLM cho lane đã có CIO report
    — quyết định người dùng 2026-08-06 (trước đó E được LLM tự tổng hợp mỗi sáng từ nguồn thô, xem
    lịch sử cũ ở reports/README.md). `risks` suy ra từ task có Status=Blocked (bảng E thủ công
    không tách khái niệm rủi ro khỏi task như prompt LLM cũ từng làm) — severity risk = priority
    của chính task đó. Cột thứ 8 "Service liên quan" (thêm TEMPLATE v1.1, cùng 2026-08-06) map
    ngược task → Service ở phần D — chấp nhận bảng E cũ (7 cột, chưa có cột này) bằng cách để
    `service` rỗng thay vì bỏ qua cả dòng."""
    report_html = _load_cio_report(report_id)
    if not report_html:
        return [], [], 0
    e_html = _extract_cio_sections(report_html).get("E", "")

    tasks: list[dict] = []
    parts = _E_H3_RE.split(e_html)  # [preamble, h3_1, chunk_1, h3_2, chunk_2, ...]
    i = 1
    while i < len(parts) - 1:
        channel = _strip_tags(parts[i])
        chunk = parts[i + 1]
        for tr_html in _TR_RE.findall(chunk):
            cells = [_strip_tags(td) for td in _TD_RE.findall(tr_html)]
            if len(cells) < 7:
                continue
            task, pic, support, priority, dependency, status, deadline = cells[:7]
            service = cells[7] if len(cells) > 7 else ""
            tasks.append(
                {
                    "task": task,
                    "pic": pic,
                    "support": support,
                    "priority": priority,
                    "dependency": dependency,
                    "status": status,
                    "deadline": deadline,
                    "service": service,
                    "channel": channel,
                    "source_path": f"reports/{report_id}.html",
                }
            )
        i += 2

    risks = [
        {
            "description": t["task"],
            "severity": t["priority"],
            "related_task": t["task"],
            "source_path": t["source_path"],
        }
        for t in tasks
        if t["status"] == "Blocked"
    ]
    return tasks, risks, len(tasks)


def _cio_tabs_html(lr: "LaneResult") -> str:
    """Tab con của 1 lane trong portal: A+B / C / D / F đều lấy SỐNG từ reports/CIO-<ID>.html (đọc
    lại mỗi lần render — không cache riêng, nên cập nhật CIO-Report theo quy trình cũ thì lần
    `graphrag dashboard` tiếp theo tự phản ánh). Từ 2026-08-11, tab "E. Luồng công việc" đã BỎ khỏi
    6 lane chính (nội dung gộp vào F — xem reports/README.md mục "Tab F — WorkFlow") — tab E giờ
    CHỈ còn hiện có điều kiện: (1) lane CHƯA có CIO report thủ công (report_id rỗng/file chưa tồn
    tại) → vẫn cần tab E để hiện tổng hợp LLM tự động qua `_lane_panel()` (xem load_manual_e_data()
    để biết cách pipeline.dashboard đã dựng sẵn lr.tasks/lr.risks cho trường hợp đó), hoặc (2) 1
    report thủ công nào đó VẪN còn giữ section E riêng (không bắt buộc xoá cho lane mới/tương lai,
    chỉ 6 lane hiện có đã được dọn) — khi đó vẫn hiện nguyên văn để không mất dữ liệu.

    Tab "G. Timeline" đã GỘP vào tab C từ 2026-08-25 (theo yêu cầu người dùng — xem
    load_lane_timeline_bars trong timeline_sync.py): thay vì 1 tab Gantt riêng, mỗi dòng backlog
    trong bảng C giờ tự vẽ luôn thanh Gantt của nó (kiểu MS Project) — xem cột "Timeline" được
    _group_c_section_by_quarter() chèn thêm, ghép theo backlog-id với dữ liệu TimelineRow đã có sẵn
    % left/width tính theo axis chung của lane (không tính lại ở đây, chỉ tái sử dụng style)."""
    slug = _slug(lr.lane)
    report_html = _load_cio_report(lr.report_id)
    header_html = _extract_cio_header(report_html) if report_html else ""
    sections = _extract_cio_sections(report_html) if report_html else {}
    asof_match = _ASOF_DATE_RE.search(header_html) if header_html else None
    date_suffix = f" ({asof_match.group(1)})" if asof_match else ""
    timeline_index: dict | None = None
    if report_html:
        try:
            timeline_index = load_lane_timeline_bars(lr.report_id or "")
        except Exception:
            timeline_index = None
    if report_html and sections.get("C") and sections.get("F"):
        sections["C"] = _enhance_c_section(lr.lane, slug, sections["C"], sections["F"], timeline_index)
    elif report_html and sections.get("C"):
        # Không có section F (WorkFlow) để enhance cột Workflow, nhưng cột Timeline (Gantt inline,
        # gộp từ tab G — 2026-08-25) không phụ thuộc F, nên vẫn chèn được — tránh mất cả tính năng
        # Timeline chỉ vì thiếu F (report đang soạn dở, hoặc lane tương lai chưa có F).
        sections["C"] = _inject_gantt_column(sections["C"], timeline_index)

    ab_content = (sections.get("A", "") + sections.get("B", "")) or f"<h2>A. Tổng quan</h2>{_EMPTY_CIO}"
    c_content = sections.get("C", "") or f"<h2>C. Kế hoạch Backlog</h2>{_EMPTY_CIO}"
    d_content = sections.get("D", "") or f"<h2>D. Luồng giá trị</h2>{_EMPTY_CIO}"
    f_content = sections.get("F", "") or f"<h2>F. WorkFlow — Giá trị → Backlog → Luồng công việc</h2>{_EMPTY_CIO}"

    # Tab mặc định khi mở 1 lane: F (WorkFlow) — bảng tổng hợp đầy đủ nhất, bao phủ cả C/D (và mọi
    # task cụ thể trước kia ở E) trong 1 bảng duy nhất. Timeline (Gantt) giờ nằm ngay trong C, không
    # còn là tab riêng (gộp 2026-08-25).
    tabs = [
        (f"{slug}-ab", f"A. Tổng quan &amp; Kết quả{date_suffix}", ab_content),
        (f"{slug}-d", f"D. Luồng giá trị{date_suffix}", d_content),
        (f"{slug}-c", f"C. Backlog kế hoạch{date_suffix}", c_content),
    ]
    # Tab E chỉ thêm vào KHI THẬT SỰ CẦN — report này có sẵn section E (chưa dọn), hoặc lane chưa
    # có report thủ công nào (fallback LLM). Không hiện mặc định cho 6 lane chính đã dọn E→F.
    if sections.get("E") or not report_html:
        e_content = sections.get("E", "") or (
            "<h2>E. Luồng công việc — Task cụ thể theo PIC/Support/Priority/Dependency/Status/Deadline</h2>"
            f"{_lane_panel(lr)}"
        )
        tabs.append((f"{slug}-e", f"E. Luồng công việc{date_suffix}", e_content))
    default_idx = next(i for i, (tid, _label, _c) in enumerate(tabs) if tid.endswith("-c"))
    buttons = "".join(
        f'<button class="lane-tab-btn{" active" if i == default_idx else ""}" data-target="{tid}">{label}</button>'
        for i, (tid, label, _content) in enumerate(tabs)
    )
    panels = "".join(
        f'<div id="{tid}" class="lane-tab-panel{" active" if i == default_idx else ""}">{content}</div>'
        for i, (tid, _label, content) in enumerate(tabs)
    )
    header_block = f'<div class="cio-header">{header_html}</div>' if header_html else f"<h1>{_esc(lr.lane)}</h1>"
    footer_html = _extract_cio_footer(report_html) if report_html else ""
    # Trích dẫn nguồn hiện luôn dưới mọi tab (không đổi theo tab đang mở) — vì footer gốc trong
    # CIO report gộp chung "Nguồn A-C/D/E" chứ không tách riêng theo từng tab con ab/c/d/e ở đây.
    footer_block = f'<div class="lane-footer">{footer_html}</div>' if footer_html else ""
    return f'{header_block}<div class="lane-tabs">{buttons}</div>{panels}{footer_block}'


_CSS = """
:root {
  color-scheme: light;
  --surface-1: #fcfcfb; --surface-2: #f2f1ee; --page: #f9f9f7; --text-primary: #0b0b0b; --text-secondary: #52514e;
  --text-muted: #898781; --gridline: #e1e0d9; --border: rgba(11,11,11,0.10); --good-text: #006300;
  --series-1: #2a78d6; --ord-low: #86b6ef; --ord-mid: #3987e5; --ord-high: #1c5cab;
  --status-good: #0ca30c; --status-warning: #fab219; --status-serious: #ec835a; --status-critical: #d03b3b;
  --cat-1: #2a78d6; --cat-2: #eb6834; --cat-3: #1baf7a; --cat-4: #eda100;
  --cat-5: #e87ba4; --cat-6: #008300; --cat-7: #4a3aa7; --cat-8: #e34948;
  --radius-sm: 7px; --radius-md: 11px; --radius-lg: 16px;
  --shadow-card: 0 1px 2px rgba(11,11,11,.05), 0 1px 1px rgba(11,11,11,.04);
  --shadow-pop: 0 6px 20px rgba(11,11,11,.10), 0 2px 4px rgba(11,11,11,.06);
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) {
    color-scheme: dark;
    --surface-1: #1a1a19; --surface-2: #232322; --page: #0d0d0d; --text-primary: #fff; --text-secondary: #c3c2b7;
    --text-muted: #898781; --gridline: #2c2c2a; --border: rgba(255,255,255,0.10); --good-text: #0ca30c;
    --series-1: #3987e5; --ord-low: #6da7ec; --ord-mid: #3987e5; --ord-high: #184f95;
    --cat-1: #3987e5; --cat-2: #d95926; --cat-3: #199e70; --cat-4: #c98500;
    --cat-5: #d55181; --cat-6: #008300; --cat-7: #9085e9; --cat-8: #e66767;
    --shadow-card: 0 1px 2px rgba(0,0,0,.4), 0 1px 1px rgba(0,0,0,.3);
    --shadow-pop: 0 6px 24px rgba(0,0,0,.5), 0 2px 4px rgba(0,0,0,.35);
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --surface-1: #1a1a19; --surface-2: #232322; --page: #0d0d0d; --text-primary: #fff; --text-secondary: #c3c2b7;
  --text-muted: #898781; --gridline: #2c2c2a; --border: rgba(255,255,255,0.10); --good-text: #0ca30c;
  --series-1: #3987e5; --ord-low: #6da7ec; --ord-mid: #3987e5; --ord-high: #184f95;
  --cat-1: #3987e5; --cat-2: #d95926; --cat-3: #199e70; --cat-4: #c98500;
  --cat-5: #d55181; --cat-6: #008300; --cat-7: #9085e9; --cat-8: #e66767;
  --shadow-card: 0 1px 2px rgba(0,0,0,.4), 0 1px 1px rgba(0,0,0,.3);
  --shadow-pop: 0 6px 24px rgba(0,0,0,.5), 0 2px 4px rgba(0,0,0,.35);
}
* { box-sizing: border-box; }
html, body { height: 100%; margin: 0; }
body { overflow: hidden; background: var(--page); color: var(--text-primary); font-family: system-ui, -apple-system, "Segoe UI", sans-serif; font-size: 13px; line-height: 1.5; -webkit-font-smoothing: antialiased; }

/* ===== App shell: 2 pane cuộn độc lập — sidebar trái CỐ ĐỊNH (không cuộn theo trang), khung
   phải tự cuộn riêng và cao full viewport. Không dùng position:sticky (dễ vỡ khi có overflow-x
   trên body/html — đã tự gặp lỗi này) — dùng model 2 pane cao 100% + overflow-y:auto riêng từng
   bên, chắc chắn hơn nhiều trên mọi trình duyệt. */
.app-shell { display: flex; height: 100vh; height: 100dvh; }

.sidebar { flex: none; width: 228px; height: 100%; background: var(--surface-1); border-right: 1px solid var(--border); display: flex; flex-direction: column; overflow: hidden; }
.sidebar-brand { display: flex; align-items: center; gap: 9px; padding: 14px 14px 10px; }
.brand-mark { width: 30px; height: 30px; border-radius: 8px; background: color-mix(in srgb, var(--series-1) 16%, transparent); display: flex; align-items: center; justify-content: center; font-size: 15px; flex: none; }
.brand-title { font-size: 13px; font-weight: 700; letter-spacing: -0.01em; }
.brand-sub { font-size: 10.5px; color: var(--text-muted); }

.sidebar-nav { flex: 1; overflow-y: auto; padding: 2px 10px 10px; display: flex; flex-direction: column; gap: 1px; }
.sidebar-label { font-size: 10px; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; color: var(--text-muted); margin: 10px 0 3px; padding: 0 7px; }
.sidebar-label:first-child { margin-top: 4px; }
.side-item, .side-link { display: flex; align-items: center; gap: 8px; text-align: left; background: none; border: none; border-radius: var(--radius-sm); padding: 5px 7px; font-size: 12px; color: var(--text-secondary); cursor: pointer; transition: background-color .15s ease, color .15s ease; text-decoration: none; }
.side-item:hover, .side-link:hover { background: var(--surface-2); }
.side-item.active { background: color-mix(in srgb, var(--series-1) 14%, transparent); color: var(--series-1); font-weight: 600; }
.side-icon { font-size: 13px; width: 18px; text-align: center; flex: none; }

/* ===== Sidebar: cây org -> workspace (Phase 6, 2026-08-25 — tổ chức lại UI theo tenants/registry.yaml) */
.org-block { margin-bottom: 2px; }
.org-head { display: flex; align-items: center; gap: 7px; width: 100%; background: none; border: none; text-align: left; padding: 6px 7px; border-radius: var(--radius-sm); cursor: pointer; font-size: 12px; font-weight: 700; color: var(--text-primary); font: inherit; }
.org-head:hover { background: var(--surface-2); }
.org-chevron { font-size: 9px; color: var(--text-muted); transition: transform .15s ease; width: 8px; display: inline-block; }
.org-block.collapsed .org-chevron { transform: rotate(-90deg); }
.org-block.collapsed .ws-list { display: none; }
.org-mark { width: 17px; height: 17px; border-radius: 5px; display: flex; align-items: center; justify-content: center; font-size: 10px; flex: none; }
.org-mark.msb { background: color-mix(in srgb, var(--cat-1) 22%, transparent); }
.org-mark.minmo { background: color-mix(in srgb, var(--cat-5) 24%, transparent); }
.ws-list { display: flex; flex-direction: column; gap: 1px; padding-left: 22px; margin: 1px 0 4px; }
.ws-list .ws-list { padding-left: 16px; }
.side-item .status-dot { width: 6px; height: 6px; border-radius: 50%; flex: none; }
.status-dot.active-t { background: var(--status-good); }
.status-dot.dept-t { background: var(--cat-7); }
.status-dot.empty-t { background: var(--gridline); border: 1px solid var(--text-muted); }
.side-item .ws-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.side-item .ws-hint { font-size: 10px; color: var(--text-muted); flex: none; }
.side-item.empty-item { color: var(--text-muted); }
.side-item .doc-count { font-size: 10px; color: var(--text-muted); flex: none; font-variant-numeric: tabular-nums; }
.side-item.active .doc-count { color: var(--series-1); opacity: .8; }
.common-item { display: flex; align-items: center; gap: 8px; width: 100%; text-align: left; background: none; border: none; border-radius: var(--radius-sm); padding: 6px 7px; font-size: 12px; font-weight: 700; color: var(--text-primary); cursor: pointer; margin-top: 2px; font: inherit; }
.common-item:hover { background: var(--surface-2); }
.common-item.active { background: color-mix(in srgb, var(--cat-3) 16%, transparent); color: var(--cat-3); }
.common-mark { width: 17px; height: 17px; border-radius: 5px; background: color-mix(in srgb, var(--cat-3) 22%, transparent); display: flex; align-items: center; justify-content: center; font-size: 10px; flex: none; }

/* ===== Workspace detail: header + 4 tab con (Báo cáo CIO / Tài liệu / Nhật ký dự án / Health) */
.ws-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 14px; flex-wrap: wrap; }
.ws-header h2 { margin: 0 0 4px; font-size: 18px; letter-spacing: -0.01em; display: flex; align-items: center; gap: 9px; flex-wrap: wrap; }
.ws-breadcrumb { font-size: 11px; color: var(--text-muted); font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
.badge.dept { background: color-mix(in srgb, var(--cat-7) 16%, transparent); color: var(--cat-7); }
.badge.planned { background: var(--surface-2); color: var(--text-muted); border: 1px solid var(--border); }
.meta-row { display: flex; gap: 14px; margin-top: 8px; font-size: 11.5px; color: var(--text-muted); flex-wrap: wrap; }
.meta-row span { display: flex; align-items: center; gap: 4px; }
.ws-tabs { display: flex; gap: 2px; border-bottom: 1px solid var(--border); margin-bottom: 16px; flex-wrap: wrap; }
.ws-tab-btn { background: none; border: none; padding: 8px 12px; font-size: 12px; color: var(--text-secondary); cursor: pointer; border-bottom: 2px solid transparent; margin-bottom: -1px; font: inherit; display: flex; align-items: center; gap: 6px; }
.ws-tab-btn:hover { color: var(--text-primary); }
.ws-tab-btn.active { color: var(--series-1); border-bottom-color: var(--series-1); font-weight: 600; }
.ws-tab-count { font-size: 10px; background: var(--surface-2); border-radius: 999px; padding: 1px 6px; color: var(--text-muted); }
.ws-tab-btn.active .ws-tab-count { background: color-mix(in srgb, var(--series-1) 16%, transparent); color: var(--series-1); }
.ws-sub-panel { display: none; }
.ws-sub-panel.active { display: block; animation: fade-in .15s ease; }
.info-strip { display: flex; gap: 8px; align-items: flex-start; padding: 10px 12px; border-radius: var(--radius-sm); background: color-mix(in srgb, var(--series-1) 8%, transparent); border: 1px solid color-mix(in srgb, var(--series-1) 25%, var(--border)); font-size: 11.5px; color: var(--text-secondary); margin-bottom: 14px; }
/* .card dùng làm khung chung cho file-tree/log-list/doc-grid bên dưới (thiếu ở lần thêm CSS đầu,
   phát hiện khi review — không có class này thì 3 khối trên render trần, không viền/không bóng) */
.card { background: var(--surface-1); border: 1px solid var(--border); border-radius: var(--radius-md); box-shadow: var(--shadow-card); }

/* file tree (tab "Tài liệu") */
.file-tree { padding: 6px; }
.file-node { display: flex; align-items: center; gap: 8px; padding: 6px 9px; border-radius: var(--radius-sm); font-size: 12.5px; cursor: default; }
.file-node:hover { background: var(--surface-2); }
.file-node .f-icon { width: 16px; text-align: center; flex: none; font-size: 12px; }
.file-node .f-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.file-node .f-meta { font-size: 10.5px; color: var(--text-muted); flex: none; font-variant-numeric: tabular-nums; }
.file-node.folder .f-name { font-weight: 600; }
.file-node.nested { padding-left: 30px; }
.logs-pill { font-size: 9.5px; background: color-mix(in srgb, var(--status-warning) 20%, transparent); color: #9a6a00; border-radius: 999px; padding: 1px 7px; flex: none; }
@media (prefers-color-scheme: dark) { :root:where(:not([data-theme="light"])) .logs-pill { color: #e8b32f; } }
:root[data-theme="dark"] .logs-pill { color: #e8b32f; }

/* project logs list (tab "Nhật ký dự án") */
.log-list { padding: 4px; }
.log-row { display: flex; align-items: center; gap: 11px; padding: 10px 11px; border-radius: var(--radius-sm); cursor: pointer; }
.log-row:hover { background: var(--surface-2); }
.log-row + .log-row { border-top: 1px solid var(--gridline); }
.log-icon { width: 30px; height: 30px; border-radius: 8px; display: flex; align-items: center; justify-content: center; font-size: 14px; flex: none; background: color-mix(in srgb, var(--cat-1) 16%, transparent); }
.log-row.alert-row .log-icon { background: color-mix(in srgb, var(--status-critical) 16%, transparent); }
.log-row.alert-row .log-title { color: var(--status-critical); }
.log-body { flex: 1; min-width: 0; }
.log-title { font-size: 12.5px; font-weight: 600; }
.log-desc { font-size: 11px; color: var(--text-muted); margin-top: 1px; }
.log-tag { font-size: 10px; color: var(--text-muted); font-family: ui-monospace, SFMono-Regular, Consolas, monospace; flex: none; }
.log-detail { margin-top: 10px; }

/* timeline da chieu (tab "Nhat ky du an", phia tren log-list - xem mockup da duyet) */
.tl-wrap { margin-bottom: 16px; }
.tl-legend { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; padding: 9px 12px; font-size: 11px; color: var(--text-secondary); margin-bottom: 10px; }
.tl-legend-item { display: flex; align-items: center; gap: 6px; }
.tl-legend-dot { width: 9px; height: 9px; border-radius: 50%; flex: none; }
.tl-scroll { overflow-x: auto; }
.tl-grid { min-width: 760px; position: relative; }
.tl-axis { display: flex; height: 24px; position: relative; margin-left: 152px; border-bottom: 1px solid var(--gridline); margin-bottom: 4px; }
.tl-month { position: absolute; top: 0; font-size: 10px; font-weight: 700; color: var(--text-muted); text-transform: uppercase; letter-spacing: .04em; border-left: 1px solid var(--gridline); padding-left: 6px; height: 100%; display: flex; align-items: flex-end; padding-bottom: 4px; }
.tl-lane { display: flex; align-items: stretch; min-height: 38px; border-top: 1px solid var(--gridline); }
.tl-lane:first-of-type { border-top: none; }
.tl-lane-label { width: 152px; flex: none; display: flex; align-items: center; gap: 7px; padding: 6px 8px 6px 2px; }
.tl-lane-icon { width: 22px; height: 22px; border-radius: 7px; display: flex; align-items: center; justify-content: center; font-size: 11px; flex: none; }
.tl-lane-name { font-size: 11px; font-weight: 700; line-height: 1.2; }
.tl-lane-agent { font-size: 9px; color: var(--text-muted); font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
.tl-track { flex: 1; position: relative; }
.tl-marker { position: absolute; top: 50%; width: 11px; height: 11px; border-radius: 50%; transform: translate(-50%, -50%); cursor: pointer; border: 2px solid var(--surface-1); box-shadow: 0 0 0 1px var(--border); transition: transform .12s ease; }
.tl-marker:hover { transform: translate(-50%, -50%) scale(1.35); z-index: 3; }
.tl-marker.selected { transform: translate(-50%, -50%) scale(1.5); z-index: 4; box-shadow: 0 0 0 3px color-mix(in srgb, var(--series-1) 45%, transparent); }
.tl-marker.sev-cao { background: var(--status-critical); }
.tl-marker.lane-decision { background: var(--cat-1); }
.tl-marker.lane-raid { background: var(--status-warning); }
.tl-marker.lane-milestone { background: var(--cat-7); }
.tl-marker.lane-adr { background: var(--cat-3); }
.tl-marker.lane-retro { background: var(--cat-5); }
.tl-marker.lane-alerts.sev-cao { background: var(--status-critical); }
.tl-marker.sev-normal.lane-alerts { background: var(--cat-1); }
.tl-marker-count { position: absolute; top: -7px; right: -7px; min-width: 13px; height: 13px; border-radius: 999px; background: var(--text-primary); color: var(--surface-1); font-size: 8.5px; font-weight: 700; display: flex; align-items: center; justify-content: center; padding: 0 2px; }
.tl-detail { margin-top: 14px; }
.tl-detail-card { display: flex; gap: 12px; padding: 13px 15px; }
.tl-detail-card + .tl-detail-card { border-top: 1px solid var(--gridline); }
.tl-detail-icon { width: 32px; height: 32px; border-radius: 9px; display: flex; align-items: center; justify-content: center; font-size: 15px; flex: none; background: color-mix(in srgb, var(--series-1) 16%, transparent); }
.tl-detail-body { flex: 1; min-width: 0; }
.tl-detail-meta { display: flex; gap: 10px; flex-wrap: wrap; font-size: 10.5px; color: var(--text-muted); margin-bottom: 6px; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
.tl-detail-title { font-size: 12.5px; font-weight: 700; margin-bottom: 5px; }
.tl-detail-text { font-size: 11.5px; color: var(--text-secondary); line-height: 1.6; }
.tl-badge { display: inline-flex; align-items: center; gap: 4px; font-size: 9.5px; font-weight: 700; padding: 1px 7px; border-radius: 999px; }
.tl-badge.cao { background: color-mix(in srgb, var(--status-critical) 16%, transparent); color: var(--status-critical); }
.tl-badge.backfill { background: var(--surface-2); color: var(--text-muted); border: 1px solid var(--border); }
.tl-badge.live { background: color-mix(in srgb, var(--status-good) 15%, transparent); color: var(--good-text); }
.tl-open-file-btn { display: inline-flex; margin-top: 8px; border: 1px solid var(--border); background: var(--page); color: var(--text-primary); border-radius: 999px; padding: 4px 10px; font-size: 10.5px; font: inherit; cursor: pointer; }
.tl-open-file-btn:hover { background: var(--surface-2); }

/* timeline dọc theo cột (tab "Nhật ký dự án", thay cho danh sách file phẳng — 1 cột = 1 sổ nhật
   ký, trục thời gian chạy từ trên xuống — thêm 2026-08-26 theo yêu cầu người dùng). Dùng chung
   data lanes/events với timeline ngang phía trên, chỉ đổi left->top. Mỗi mốc (nhiều sự kiện cùng
   ngày gộp lại) là 1 "block" chứa NHIỀU DÒNG — mỗi sự kiện 1 dòng riêng (chấm màu + tiêu đề ngắn
   gọn), không rút gọn về 1 dòng + số đếm nữa (theo yêu cầu người dùng: "bao nhiêu sự kiện thì
   break bấy nhiêu dòng"). CHỈ 1 trục thời gian DÙNG CHUNG bên trái (.vtl-axis + .vtl-month-line
   phủ hết bề ngang) — KHÔNG có spine/tick riêng cho từng cột (đã thử thêm rồi bỏ lại theo đúng
   phản hồi người dùng 2026-08-26: "mỗi cột đều đang có timeline là ko đúng" — chỉ nên có 1 timeline
   dùng chung bên trái, không phải mỗi cột tự có trục riêng). Mỗi block chiếm trọn bề ngang cột, neo
   từ mép trái, nền LUÔN đục (không chỉ khi hover) để không bị vạch tháng/backfill xuyên qua đè lên
   chữ khi trùng hàng; màu chấm set inline theo lane/severity (xem renderFileTimeline JS). */
/* Không đặt min-width cho grid/head-row/body/col nữa — 6 cột co giãn đều vừa khít bề ngang khung
   chứa (flex:1, min-width:0), tránh sinh scrollbar ngang (phản hồi người dùng 2026-08-26: "đang có
   scrollbar ngang, hãy làm sao để ko có"). Đánh đổi: cột hẹp lại trên màn hình nhỏ, nhưng label đã
   có -webkit-line-clamp nên vẫn gọn gàng, không tràn/vỡ layout. */
.vtl-wrap { margin-bottom: 12px; }
/* KHÔNG đặt overflow-x: hidden ở đây nữa — theo spec CSS, 1 trục overflow non-visible ("hidden")
   sẽ luôn ép trục kia (đang "visible") tự đổi thành "auto" (kể cả khi mình khai tường minh
   "overflow-y: visible" — trình duyệt vẫn ép về "auto", đã tự kiểm chứng qua getComputedStyle),
   khiến .vtl-grid tự sinh scrollbar dọc ngoài ý muốn (phát hiện + xác nhận qua yêu cầu người dùng
   2026-08-26: "kiểm tra bảng vtl-grid... ko muốn có scrollbar"). overflow-x:hidden vốn dùng để
   chặn tràn ngang hồi cột còn min-width cố định — nay cột đã flex:1/min-width:0 (không còn ép
   min-width) nên không bao giờ tràn ngang nữa, bỏ hẳn overflow-x là an toàn. */
.vtl-head-row { display: flex; }
/* Dải trục chung bên trái — hiện thẳng mốc ngày "dd/mm/yyyy" cho MỌI ngày có sự kiện (gộp cả 6 sổ,
   không phân biệt sổ nào) thay vì chỉ nhãn tháng thô — theo đúng yêu cầu người dùng 2026-08-27
   ("hiển thị luôn các mốc dạng 12/08/2026, 14/08/2026..."; trước đó thử chấm màu theo lane nhưng bị
   từ chối "không đúng"). Rộng 80px đủ cho 1 dòng "dd/mm/yyyy". */
.vtl-axis-spacer { width: 80px; flex: none; }
.vtl-col-head { flex: 1; min-width: 0; padding: 6px 8px; font-size: 11px; font-weight: 700; border-top: 3px solid var(--cat-1); display: flex; align-items: center; gap: 6px; }
.vtl-col-icon { width: 20px; height: 20px; border-radius: 6px; display: flex; align-items: center; justify-content: center; font-size: 11px; flex: none; }
.vtl-col-count { margin-left: auto; font-size: 9.5px; color: var(--text-muted); background: var(--surface-2); border-radius: 999px; padding: 1px 6px; font-weight: 700; }
.vtl-body { position: relative; display: flex; border-top: 1px solid var(--gridline); }
.vtl-axis { width: 80px; flex: none; position: relative; }
.vtl-axis-date { position: absolute; left: 0; right: 6px; font-size: 9.5px; font-weight: 700; color: var(--text-muted); font-variant-numeric: tabular-nums; text-align: right; white-space: nowrap; }
/* Vạch tháng PHỦ HẾT bề ngang (không chỉ nằm trong dải trục bên trái) — trục thời gian DÙNG CHUNG
   duy nhất để "gióng" hàng chấm ở các cột xa bên phải về đúng tháng. */
.vtl-month-line { position: absolute; left: 80px; right: 0; height: 0; border-top: 1px dashed var(--gridline); z-index: 0; }
.vtl-col { flex: 1; min-width: 0; position: relative; border-left: 1px solid var(--gridline); overflow: hidden; }
/* background LUÔN đục (không chỉ khi hover) — nếu không, khi 1 block rơi trùng hàng với vạch
   tháng/backfill (đường kẻ CHUNG chạy phía sau), đường kẻ sẽ "xuyên" qua khoảng trống quanh chữ
   nhìn như gạch ngang chữ, dù z-index đã cao hơn (chữ thì đứng trên, nhưng nền trong suốt không
   che được đường kẻ ở NGAY BÊN CẠNH/GIỮA các ký tự — phát hiện khi rà lại 2026-08-26). */
.vtl-marker-block { position: absolute; left: 6px; right: 6px; padding: 2px 4px; border-radius: 6px; cursor: pointer; background: var(--surface-1); transition: box-shadow .12s ease, background-color .12s ease; z-index: 2; }
.vtl-marker-block:hover { box-shadow: 0 0 0 1px var(--series-1); background: color-mix(in srgb, var(--series-1) 6%, transparent); z-index: 3; }
.vtl-marker-block.selected { box-shadow: 0 0 0 2px var(--series-1); background: color-mix(in srgb, var(--series-1) 10%, transparent); z-index: 4; }
.vtl-marker-row { display: flex; align-items: flex-start; gap: 5px; min-height: 30px; padding: 1px 7px 1px 1px; }
.vtl-marker-dot { width: 9px; height: 9px; border-radius: 50%; flex: none; margin-top: 3px; }
/* Nhãn cho wrap tối đa 2 dòng thay vì cắt gọn 1 dòng — đỡ "lấp chữ" (phản hồi người dùng
   2026-08-26), -webkit-line-clamp vẫn được mọi engine Chromium/WebKit hỗ trợ dù có tiền tố. */
.vtl-marker-label { font-size: 9.5px; line-height: 1.35; color: var(--text-secondary); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.log-list-caption { font-size: 10px; font-weight: 700; color: var(--text-muted); text-transform: uppercase; letter-spacing: .04em; padding: 9px 11px 2px; }

/* _common doc grid */
.doc-grid { display: flex; flex-direction: column; gap: 9px; padding: 8px; }
.doc-card { display: flex; gap: 12px; padding: 12px 13px; }
.doc-num { width: 26px; height: 26px; border-radius: 7px; background: color-mix(in srgb, var(--cat-3) 18%, transparent); color: var(--cat-3); display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 700; flex: none; }
.doc-body h4 { margin: 0 0 3px; font-size: 12.5px; }
.doc-body p { margin: 0; font-size: 11.5px; color: var(--text-secondary); }
.doc-body .doc-src { font-size: 10px; color: var(--text-muted); margin-top: 4px; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }

.app-main { flex: 1; min-width: 0; height: 100%; overflow-y: auto; }
.topbar { padding: 8px 24px 0; }
.topbar-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-bottom: 6px; }
.asof-chip { display: inline-flex; align-items: center; gap: 5px; font-size: 10.5px; color: var(--text-muted); background: var(--surface-1); border: 1px solid var(--border); border-radius: 999px; padding: 3px 9px; }
.topbar-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.sync-status-row { display: flex; align-items: center; gap: 6px; }
.sync-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--text-muted); flex: none; }
.sync-dot.ok { background: var(--status-good); }
.sync-dot.err { background: var(--status-critical); }
.sync-status { font-size: 10.5px; color: var(--text-secondary); white-space: nowrap; }
.sync-status.err { color: var(--status-critical); }
.sync-status.ok { color: var(--status-good); }
.sync-btn { background: var(--series-1); color: #fff; border: none; border-radius: var(--radius-sm); padding: 6px 11px; font-size: 11.5px; font-weight: 600; cursor: pointer; transition: opacity .15s ease, background-color .15s ease; white-space: nowrap; }
.sync-btn:hover { opacity: .88; }
.sync-btn:disabled { background: var(--text-muted); cursor: default; opacity: 1; }
.sync-btn.secondary { background: none; border: 1px solid var(--border); color: var(--text-primary); }
.sync-btn.secondary:hover { background: var(--surface-2); opacity: 1; }
.sync-btn.secondary:disabled { background: none; color: var(--text-muted); }
.sync-feedback { font-size: 10.5px; color: var(--text-muted); min-height: 16px; text-align: right; }
.sync-feedback.ok { color: var(--status-good); }
.sync-feedback.err { color: var(--status-critical); }
.topbar-tools { display: flex; flex-direction: column; align-items: flex-end; gap: 5px; margin-bottom: 8px; }
.sync-log { font-size: 10px; color: var(--text-muted); background: var(--surface-1); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 7px; max-height: 120px; overflow: auto; white-space: pre-wrap; display: none; width: 100%; max-width: 420px; }
.sync-config { font-size: 11px; }
.sync-config summary { cursor: pointer; font-size: 11px; color: var(--text-muted); text-align: right; }
.sync-config[open] { background: var(--surface-1); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 10px 12px; width: 280px; }
.sync-config label { display: block; font-size: 10.5px; color: var(--text-muted); margin: 6px 0 3px; }
.sync-config input { width: 100%; box-sizing: border-box; font-size: 12px; padding: 5px 7px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--page); color: var(--text-primary); }
.sync-config button { margin-top: 6px; padding: 5px 9px; font-size: 11.5px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--page); color: var(--text-primary); cursor: pointer; }
.model-info-box { font-size: 11px; color: var(--text-muted); line-height: 1.6; background: var(--page); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 7px 9px; }
.model-info-box strong { color: var(--text-primary); }
.settings-menu { position: relative; }
.settings-menu summary { list-style: none; cursor: pointer; }
.settings-menu summary::-webkit-details-marker { display: none; }
.settings-trigger { display: inline-flex; align-items: center; gap: 6px; }
.settings-panel { position: absolute; right: 0; top: calc(100% + 6px); width: min(340px, calc(100vw - 24px)); background: var(--surface-1); border: 1px solid var(--border); border-radius: var(--radius-md); box-shadow: var(--shadow-pop); padding: 10px; z-index: 5; }
.settings-status { display: flex; align-items: flex-start; gap: 7px; margin-bottom: 8px; padding-bottom: 8px; border-bottom: 1px solid var(--gridline); }
.settings-status .sync-status { white-space: normal; }
.settings-status .sync-dot { margin-top: 5px; }
.settings-section-title { font-size: 10px; font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase; color: var(--text-muted); margin-bottom: 5px; }
.settings-panel .sync-config summary { display: none; }
.settings-panel .sync-config[open] { background: none; border: none; border-radius: 0; padding: 0; width: auto; }
.topbar-tools.has-settings-menu { align-items: stretch; }
.topbar-tools.has-settings-menu .sync-config { width: 100%; }
.topbar-tools.has-settings-menu .sync-config summary { display: none; }
.topbar-tools.has-settings-menu .sync-config[open] { background: none; border: none; border-radius: 0; padding: 0; width: auto; }
.banner { display: flex; gap: 8px; align-items: flex-start; background: color-mix(in srgb, var(--status-warning) 12%, transparent); border: 1px solid color-mix(in srgb, var(--status-warning) 35%, transparent); border-radius: var(--radius-md); padding: 9px 12px; font-size: 11.5px; margin-bottom: 14px; }
.banner-icon { flex: none; }

.content { padding: 0 24px 40px; }
.lane-panel { display: none; }
.lane-panel.active { display: block; animation: fade-in .18s ease; }
.manage-shell { display: grid; gap: 12px; }
.manage-hero { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; padding: 12px 14px; background: var(--surface-1); border: 1px solid var(--border); border-radius: var(--radius-md); box-shadow: var(--shadow-card); }
.manage-hero h2 { margin: 0; border: none; padding: 0; color: var(--text-primary); font-size: 15px; letter-spacing: -0.01em; text-transform: none; }
.manage-intro { margin: 4px 0 0; font-size: 11.5px; line-height: 1.45; color: var(--text-secondary); max-width: 760px; }
.manage-badge { display: inline-flex; align-items: center; min-height: 24px; padding: 0 9px; border-radius: 999px; background: color-mix(in srgb, var(--series-1) 12%, transparent); color: var(--series-1); font-size: 10px; font-weight: 700; white-space: nowrap; }
.manage-native { width: 100%; }
.manage-native .page-shell { padding: 0; }
.manage-native .layout { display: grid; grid-template-columns: minmax(280px, 0.74fr) minmax(520px, 1.56fr); gap: 12px; align-items: start; }
.manage-native .stack { display: grid; gap: 12px; }
.manage-native .stack:first-child { max-width: 372px; }
.manage-native .section-card { background: var(--surface-1); border: 1px solid var(--border); border-radius: var(--radius-lg); box-shadow: var(--shadow-card); padding: 12px; }
.manage-native .section-head { display: flex; justify-content: space-between; align-items: baseline; gap: 10px; margin-bottom: 8px; }
.manage-native .section-head h2,
.manage-native .section-head h3 { margin: 0; font-size: 13px; font-weight: 700; letter-spacing: -0.01em; text-transform: none; border: none; padding: 0; color: var(--text-primary); }
.manage-native .section-note { margin: 2px 0 0; font-size: 11px; color: var(--text-secondary); }
.manage-native .chip { display: inline-flex; align-items: center; min-height: 22px; padding: 0 8px; border-radius: 999px; background: var(--surface-2); color: var(--text-secondary); font-size: 10px; font-weight: 700; }
.manage-native #status { min-height: 16px; margin: 0 0 8px; font-size: 11.5px; color: var(--text-secondary); }
.manage-native #status.error { color: var(--status-critical); }
.manage-native #status.ok { color: var(--status-good); }
.manage-native .field-label { display: block; margin: 0 0 6px; font-size: 10px; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; color: var(--text-muted); }
.manage-native .row,
.manage-native .action-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.manage-native input[type="text"],
.manage-native input[type="password"] { width: 100%; min-height: 32px; padding: 6px 10px; border: 1px solid var(--border); border-radius: 999px; background: var(--page); color: var(--text-primary); font: inherit; outline: none; }
.manage-native input[type="text"]:focus,
.manage-native input[type="password"]:focus { border-color: color-mix(in srgb, var(--series-1) 50%, var(--border)); box-shadow: 0 0 0 3px color-mix(in srgb, var(--series-1) 16%, transparent); }
.manage-native input[type="text"].input-error { border-color: var(--status-critical); box-shadow: 0 0 0 3px rgba(208,59,59,.12); }
.manage-native button { min-height: 30px; padding: 0 11px; border: none; border-radius: 999px; background: var(--series-1); color: #fff; font: inherit; font-size: 11px; font-weight: 700; cursor: pointer; }
.manage-native button.secondary { background: transparent; color: var(--text-primary); border: 1px solid var(--border); }
.manage-native button.danger { background: #f9e8e2; color: var(--status-critical); }
.manage-native .table-wrap { overflow: hidden; border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--surface-1); }
.manage-native table { width: 100%; border-collapse: collapse; font-size: 12px; }
/* Cột nút Xoá dùng width CỐ ĐỊNH theo px (không phải %) — ở container hẹp (panel này stack 1 cột
   dưới 920px), 22% có thể chỉ còn vài chục px, không đủ chỗ cho padding+border-left nên nút bị ép
   sát vào chữ alias (phản hồi người dùng 2026-08-26: "nút xóa đang sát chữ quá"). Cột alias để
   width:auto — với table-layout:fixed, cột auto duy nhất tự nhận hết phần còn lại sau khi trừ 2
   cột kia, nên vẫn co giãn nhưng cột nút luôn có đúng 76px bất kể bảng rộng hẹp thế nào. */
.manage-native #registryTable { table-layout: fixed; }
.manage-native #registryTable th:nth-child(1),
.manage-native #registryTable td:nth-child(1) { width: 36%; }
.manage-native #registryTable th:nth-child(2),
.manage-native #registryTable td:nth-child(2) { width: auto; padding-right: 14px; }
.manage-native #registryTable th:nth-child(3),
.manage-native #registryTable td:nth-child(3) { width: 76px; }
.manage-native #registryTable th:nth-child(3) { text-align: right; padding-right: 16px; }
.manage-native #registryTable td:nth-child(3) { display: flex; justify-content: flex-end; align-items: center; padding-left: 14px; padding-right: 16px; border-left: 1px solid var(--gridline); }
.manage-native #registryTable td:nth-child(1),
.manage-native #registryTable td:nth-child(2) { overflow-wrap: anywhere; }
.manage-native thead th { text-align: left; font-size: 10px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--text-muted); font-weight: 700; padding: 8px 10px; border-bottom: 1px solid var(--gridline); }
.manage-native tbody td { padding: 8px 10px; border-bottom: 1px solid var(--gridline); vertical-align: top; }
.manage-native tbody tr:last-child td { border-bottom: none; }
.manage-native .subtle-box { background: color-mix(in srgb, var(--surface-2) 50%, transparent); border: 1px solid var(--border); border-radius: var(--radius-md); padding: 10px; }
.manage-native .field-stack { display: grid; gap: 8px; }
.manage-native .columns-wrap { position: relative; }
.manage-native #linksSvg { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; overflow: visible; }
.manage-native #linksSvg line { stroke: color-mix(in srgb, var(--series-1) 76%, #1baf7a); stroke-width: 2; }
.manage-native #linksSvg line.temp { stroke: var(--text-muted); stroke-dasharray: 5 4; }
.manage-native .channel-columns { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px; }
.manage-native .channel-col { border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--surface-1); padding: 10px; }
.manage-native .channel-col h3 { margin: 0 0 6px; font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted); }
.manage-native .channel-col label { display: block; padding: 7px 8px; margin-bottom: 6px; border: 1px solid color-mix(in srgb, var(--border) 78%, transparent); border-radius: 10px; background: color-mix(in srgb, var(--page) 82%, white); }
.manage-native .channel-col label:last-child { margin-bottom: 0; }
.manage-native .channel-col label .hint { display: block; margin: 3px 0 0 20px; color: var(--text-muted); font-size: 10px; }
.manage-native .drag-handle { display: inline-block; width: 10px; height: 10px; margin-right: 5px; border-radius: 50%; background: var(--series-1); cursor: crosshair; vertical-align: middle; box-shadow: 0 0 0 3px color-mix(in srgb, var(--series-1) 12%, transparent); }
.manage-native .selection-bar { margin-top: 8px; padding: 8px; border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--surface-1); box-shadow: var(--shadow-card); }
.manage-native .selection-bar input { flex: 1 1 240px; }
.manage-native .group-card { margin-top: 8px; padding: 9px 10px; border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--surface-1); box-shadow: var(--shadow-card); }
.manage-native .group-card:first-child { margin-top: 0; }
.manage-native .group-card .names { margin-bottom: 7px; font-size: 12px; }
.manage-native .compact-block { background: var(--surface-1); border: 1px solid var(--border); border-radius: var(--radius-lg); box-shadow: var(--shadow-card); padding: 12px; }
.manage-native .compact-block summary { cursor: pointer; list-style: none; display: flex; align-items: center; justify-content: space-between; gap: 10px; font-size: 12px; font-weight: 700; color: var(--text-primary); margin: 0; }
.manage-native .compact-block summary::after { content: '+'; font-size: 14px; line-height: 1; color: var(--text-muted); }
.manage-native .compact-block summary::-webkit-details-marker { display: none; }
.manage-native .compact-block[open] summary { margin-bottom: 10px; }
.manage-native .compact-block[open] summary::after { content: '-'; }
.manage-native #ignoredList { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; }
.manage-native #ignoredList li { padding: 8px 10px; border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--surface-1); }
.manage-native #ignoredList a { color: var(--status-critical); text-decoration: none; font-weight: 600; }
.manage-native .empty-card { padding: 10px; border: 1px dashed var(--gridline); border-radius: var(--radius-md); background: var(--surface-1); color: var(--text-secondary); }
.manage-native .manage-error { padding: 12px; border-radius: var(--radius-md); font-size: 12px; color: var(--status-critical); background: color-mix(in srgb, var(--status-critical) 8%, var(--surface-1)); border: 1px solid color-mix(in srgb, var(--status-critical) 35%, var(--border)); }
.query-shell { display: grid; gap: 12px; }
.query-hero { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; padding: 12px 14px; background: var(--surface-1); border: 1px solid var(--border); border-radius: var(--radius-md); box-shadow: var(--shadow-card); }
.query-hero h2 { margin: 0; border: none; padding: 0; color: var(--text-primary); font-size: 15px; letter-spacing: -0.01em; text-transform: none; }
.query-intro { margin: 4px 0 0; font-size: 11.5px; line-height: 1.45; color: var(--text-secondary); max-width: 760px; }
.query-badge { display: inline-flex; align-items: center; min-height: 24px; padding: 0 9px; border-radius: 999px; background: color-mix(in srgb, var(--cat-2) 14%, transparent); color: var(--cat-2); font-size: 10px; font-weight: 700; white-space: nowrap; }
.query-native { display: grid; gap: 12px; }
.query-form, .query-block { background: var(--surface-1); border: 1px solid var(--border); border-radius: var(--radius-md); box-shadow: var(--shadow-card); padding: 12px; }
.query-form { display: grid; gap: 10px; }
.query-form label, .query-block-title, .query-mini-label { font-size: 10px; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; color: var(--text-muted); }
.query-textarea { width: 100%; min-height: 92px; resize: vertical; padding: 10px 12px; border: 1px solid var(--border); border-radius: 16px; background: var(--page); color: var(--text-primary); font: inherit; outline: none; }
.query-textarea:focus { border-color: color-mix(in srgb, var(--series-1) 50%, var(--border)); box-shadow: 0 0 0 3px color-mix(in srgb, var(--series-1) 16%, transparent); }
.query-actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; justify-content: space-between; }
.query-actions-left, .query-actions-right { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.query-btn { min-height: 34px; padding: 0 13px; border: none; border-radius: 999px; background: var(--series-1); color: #fff; font: inherit; font-size: 11px; font-weight: 700; cursor: pointer; }
.query-btn.secondary { background: none; color: var(--text-primary); border: 1px solid var(--border); }
.query-status { min-height: 18px; font-size: 11.5px; color: var(--text-secondary); }
.query-status.ok { color: var(--status-good); }
.query-status.err { color: var(--status-critical); }
.query-benchmark-grid { display: grid; grid-template-columns: 1.1fr 0.9fr; gap: 12px; }
.query-benchmark-list { display: grid; gap: 8px; }
.query-benchmark-item { display: grid; gap: 4px; padding: 9px 10px; border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--page); }
.query-benchmark-item strong { font-size: 11.5px; color: var(--text-primary); }
.query-benchmark-item span { font-size: 11px; color: var(--text-secondary); }
.query-benchmark-actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.query-benchmark-log { min-height: 140px; max-height: 320px; overflow: auto; padding: 10px 12px; border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--page); color: var(--text-primary); font-size: 11.5px; line-height: 1.5; white-space: pre-wrap; }
.links-shell { display: grid; gap: 12px; }
.links-hero { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; padding: 12px 14px; background: var(--surface-1); border: 1px solid var(--border); border-radius: var(--radius-md); box-shadow: var(--shadow-card); }
.links-hero h2 { margin: 0; border: none; padding: 0; color: var(--text-primary); font-size: 15px; letter-spacing: -0.01em; text-transform: none; }
.links-intro { margin: 4px 0 0; font-size: 11.5px; line-height: 1.45; color: var(--text-secondary); max-width: 760px; }
.links-badge { display: inline-flex; align-items: center; min-height: 24px; padding: 0 9px; border-radius: 999px; background: color-mix(in srgb, var(--cat-2) 14%, transparent); color: var(--cat-2); font-size: 10px; font-weight: 700; white-space: nowrap; }
.links-native { display: grid; gap: 12px; }
.links-status { min-height: 18px; font-size: 11.5px; color: var(--text-secondary); }
.links-status.ok { color: var(--status-good); }
.links-status.err { color: var(--status-critical); }
.links-toolbar { display: grid; grid-template-columns: minmax(0, 1.2fr) repeat(2, minmax(180px, 0.6fr)) auto; gap: 10px; align-items: end; }
.links-filter { display: grid; gap: 6px; }
.links-filter label { font-size: 10px; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; color: var(--text-muted); }
.links-filter-search, .links-filter-select { width: 100%; min-height: 34px; padding: 7px 11px; border: 1px solid var(--border); border-radius: 999px; background: var(--page); color: var(--text-primary); font: inherit; outline: none; }
.links-filter-search:focus, .links-filter-select:focus { border-color: color-mix(in srgb, var(--series-1) 50%, var(--border)); box-shadow: 0 0 0 3px color-mix(in srgb, var(--series-1) 16%, transparent); }
.links-meta { min-height: 34px; display: inline-flex; align-items: center; justify-content: flex-end; font-size: 11px; color: var(--text-secondary); }
.links-summary { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 12px; }
.links-card { background: var(--surface-1); border: 1px solid var(--border); border-radius: var(--radius-md); box-shadow: var(--shadow-card); padding: 11px 12px; }
.links-card strong { display: block; margin-top: 4px; font-size: 20px; color: var(--text-primary); }
.links-grid { display: grid; grid-template-columns: 1.15fr 1.15fr 1fr; gap: 12px; }
.links-list { display: grid; gap: 8px; max-height: 360px; overflow: auto; }
.links-item { width: 100%; text-align: left; border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--page); padding: 10px 11px; cursor: pointer; }
.links-item:hover { border-color: color-mix(in srgb, var(--series-1) 35%, var(--border)); }
.links-item.active { border-color: color-mix(in srgb, var(--series-1) 45%, var(--border)); background: color-mix(in srgb, var(--series-1) 9%, var(--page)); }
.links-item strong { display: block; font-size: 12px; color: var(--text-primary); }
.links-item span { display: block; margin-top: 4px; font-size: 11px; color: var(--text-secondary); }
.links-detail { display: grid; gap: 12px; }
.links-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.links-chip { display: inline-flex; align-items: center; min-height: 24px; padding: 0 8px; border-radius: 999px; background: var(--page); border: 1px solid var(--border); font-size: 10px; color: var(--text-secondary); }
.links-docs { display: grid; gap: 8px; max-height: 360px; overflow: auto; }
.links-doc { border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--page); padding: 10px 11px; }
.links-doc strong { display: block; font-size: 12px; color: var(--text-primary); }
.links-doc-meta { margin-top: 6px; display: flex; flex-wrap: wrap; gap: 6px; }
.links-empty { color: var(--text-muted); font-size: 11.5px; }
.links-backlog-list { display: grid; gap: 8px; max-height: 260px; overflow: auto; }
.links-backlog-item { border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--page); padding: 9px 10px; }
.links-backlog-item strong { display: block; font-size: 11.5px; color: var(--text-primary); }
.links-backlog-item span { display: block; margin-top: 4px; font-size: 10.5px; color: var(--text-secondary); }
.links-gap-box { padding: 10px 11px; border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--page); }
.links-gap-box strong { display: block; font-size: 11.5px; color: var(--text-primary); }
.links-gap-box ul { margin: 8px 0 0 18px; padding: 0; color: var(--text-secondary); font-size: 11px; line-height: 1.45; }
.links-related-list { display: grid; gap: 8px; max-height: 320px; overflow: auto; }
.links-related-reasons { margin-top: 6px; display: grid; gap: 4px; }
.links-related-reason { font-size: 10.5px; color: var(--text-secondary); }
.links-backlink-group { border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--page); padding: 9px 10px; }
.links-backlink-group strong { display: block; font-size: 11.5px; color: var(--text-primary); }
.links-backlink-group .links-doc-meta { margin-top: 6px; }
.links-doc-action { margin-top: 8px; }
.links-doc-btn { min-height: 28px; padding: 0 10px; border: 1px solid var(--border); border-radius: 999px; background: var(--surface-1); color: var(--text-primary); font: inherit; font-size: 10.5px; cursor: pointer; }
.links-doc-btn:hover { border-color: color-mix(in srgb, var(--series-1) 35%, var(--border)); }
.links-preview { white-space: pre-wrap; max-height: 360px; overflow: auto; padding: 10px 12px; border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--page); color: var(--text-primary); font-size: 11.5px; line-height: 1.5; }
.query-topline { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.query-chip-row, .query-source-meta { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.query-chip { display: inline-flex; align-items: center; min-height: 22px; padding: 0 8px; border-radius: 999px; background: var(--surface-2); color: var(--text-secondary); font-size: 10px; font-weight: 700; }
.query-chip.good { background: color-mix(in srgb, var(--status-good) 14%, transparent); color: var(--status-good); }
.query-stats { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 10px; }
.query-stat-card { padding: 10px 12px; border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--page); }
.query-stat-card strong { display: block; margin-top: 6px; font-size: 22px; line-height: 1; color: var(--text-primary); }
.query-presets { display: flex; flex-wrap: wrap; gap: 8px; }
.query-preset { min-height: 28px; padding: 0 10px; border: 1px solid var(--border); border-radius: 999px; background: var(--page); color: var(--text-primary); font: inherit; font-size: 11px; cursor: pointer; }
.query-answer { white-space: pre-wrap; line-height: 1.55; color: var(--text-primary); }
.query-context { white-space: pre-wrap; max-height: 360px; overflow: auto; padding: 10px 12px; border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--page); color: var(--text-primary); font-size: 11.5px; line-height: 1.5; }
.query-source-list { list-style: none; margin: 0; padding: 0; display: grid; gap: 10px; }
.query-source-list li { padding: 10px 12px; border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--page); }
.query-empty { font-size: 11.5px; color: var(--text-secondary); }
.health-shell { display: grid; gap: 12px; }
.health-hero { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; padding: 12px 14px; background: var(--surface-1); border: 1px solid var(--border); border-radius: var(--radius-md); box-shadow: var(--shadow-card); }
.health-hero h2 { margin: 0; border: none; padding: 0; color: var(--text-primary); font-size: 15px; letter-spacing: -0.01em; text-transform: none; }
.health-intro { margin: 4px 0 0; font-size: 11.5px; line-height: 1.45; color: var(--text-secondary); max-width: 760px; }
.health-badge { display: inline-flex; align-items: center; min-height: 24px; padding: 0 9px; border-radius: 999px; background: color-mix(in srgb, var(--cat-3) 14%, transparent); color: var(--cat-3); font-size: 10px; font-weight: 700; white-space: nowrap; }
.health-native { display: grid; gap: 12px; }
.health-config { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; gap: 10px; align-items: end; padding: 12px; background: var(--surface-1); border: 1px solid var(--border); border-radius: var(--radius-md); box-shadow: var(--shadow-card); }
.health-field { display: grid; gap: 6px; }
.health-field label { font-size: 10px; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; color: var(--text-muted); }
.health-input { width: 100%; min-height: 34px; padding: 7px 11px; border: 1px solid var(--border); border-radius: 999px; background: var(--page); color: var(--text-primary); font: inherit; outline: none; }
.health-input:focus { border-color: color-mix(in srgb, var(--series-1) 50%, var(--border)); box-shadow: 0 0 0 3px color-mix(in srgb, var(--series-1) 16%, transparent); }
.health-textarea { width: 100%; min-height: 84px; padding: 8px 11px; border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--page); color: var(--text-primary); font: inherit; outline: none; resize: vertical; }
.health-textarea:focus { border-color: color-mix(in srgb, var(--series-1) 50%, var(--border)); box-shadow: 0 0 0 3px color-mix(in srgb, var(--series-1) 16%, transparent); }
.health-actions { display: flex; gap: 8px; align-items: center; justify-content: flex-end; }
.health-btn { min-height: 34px; padding: 0 13px; border: none; border-radius: 999px; background: var(--series-1); color: #fff; font: inherit; font-size: 11px; font-weight: 700; cursor: pointer; }
.health-btn.secondary { background: none; color: var(--text-primary); border: 1px solid var(--border); }
.health-status { min-height: 18px; font-size: 11.5px; color: var(--text-secondary); }
.health-status.ok { color: var(--status-good); }
.health-status.err { color: var(--status-critical); }
.health-metrics { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.health-card { background: var(--surface-1); border: 1px solid var(--border); border-radius: var(--radius-md); box-shadow: var(--shadow-card); padding: 12px; }
.health-card h3 { margin: 0; border: none; padding: 0; font-size: 13px; color: var(--text-primary); text-transform: none; }
.health-eyebrow { font-size: 10px; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; color: var(--text-muted); margin-bottom: 7px; }
.health-big { font-size: 30px; font-weight: 700; letter-spacing: -0.04em; line-height: 1; margin: 10px 0 6px; }
.health-subtle { font-size: 11px; color: var(--text-secondary); }
.health-grid { display: grid; grid-template-columns: 1.08fr 0.92fr; gap: 12px; align-items: start; }
.health-coverage-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.health-coverage-item { padding: 12px; border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--page); }
.health-progress { height: 8px; border-radius: 999px; background: var(--surface-2); overflow: hidden; margin-top: 8px; }
.health-progress > span { display: block; height: 100%; background: var(--series-1); border-radius: inherit; }
.health-history-list { list-style: none; margin: 0; padding: 0; display: grid; gap: 10px; }
.health-history-list li { padding: 10px 12px; border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--page); }
.health-detail-grid { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 10px; }
.health-chip { display: inline-flex; align-items: center; min-height: 22px; padding: 0 8px; border-radius: 999px; font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; }
.health-chip.summary { background: color-mix(in srgb, var(--status-warning) 16%, transparent); color: var(--status-warning); }
.health-chip.owner { background: color-mix(in srgb, var(--status-good) 16%, transparent); color: var(--status-good); }
.health-chip.critical { background: color-mix(in srgb, var(--status-critical) 16%, transparent); color: var(--status-critical); }
.health-detail-tables { display: grid; gap: 12px; }
.health-table-wrap { overflow: auto; border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--surface-1); }
.health-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.health-table th { text-align: left; font-size: 10px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--text-muted); font-weight: 700; padding: 8px 10px; border-bottom: 1px solid var(--gridline); }
.health-table td { padding: 8px 10px; border-bottom: 1px solid var(--gridline); vertical-align: top; }
.health-table tr:last-child td { border-bottom: none; }
.health-code { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 11px; word-break: break-word; }
.health-table-alerts { table-layout: fixed; }
.health-table-alerts td { overflow-wrap: break-word; }
@media (max-width: 920px) { .manage-hero, .manage-native .layout { grid-template-columns: 1fr; display: grid; } .manage-badge { justify-self: start; } }
@media (max-width: 980px) { .health-grid, .health-config, .health-metrics, .health-detail-grid, .query-topline, .query-benchmark-grid, .query-stats, .links-summary, .links-grid, .links-toolbar { grid-template-columns: 1fr; } .health-coverage-grid { grid-template-columns: 1fr; } .health-badge, .query-badge, .links-badge { justify-self: start; } .links-meta { justify-content: flex-start; } }
@keyframes fade-in { from { opacity: 0; transform: translateY(2px); } to { opacity: 1; transform: none; } }
.cio-header { margin-bottom: 14px; }

/* CSS gốc từ reports/TEMPLATE.html — giữ nguyên tên class để tái dùng trực tiếp nội dung trích
   xuất từ CIO-Report (A/B/C/D) mà không cần viết lại HTML của các phần đó, chỉ nâng cấp trực quan. */
.id-badge { display: inline-block; font-size: 10px; font-weight: 700; letter-spacing: 0.04em; color: var(--series-1); background: color-mix(in srgb, var(--series-1) 12%, transparent); border: 1px solid color-mix(in srgb, var(--series-1) 30%, transparent); border-radius: var(--radius-sm); padding: 2px 7px; margin-bottom: 8px; }
.cio-header h1 { font-size: 18px; margin: 0 0 3px; letter-spacing: -0.01em; }
.subtitle { color: var(--text-secondary); font-size: 12.5px; margin: 0 0 6px; }
.asof { font-size: 11px; color: var(--text-muted); border-top: 1px solid var(--gridline); padding-top: 6px; margin-top: 8px; }
h2 { font-size: 11px; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; color: var(--text-muted); margin: 0 0 12px; padding-bottom: 8px; border-bottom: 1px solid var(--gridline); }
h3 { font-size: 13px; font-weight: 600; margin: 14px 0 8px; color: var(--text-primary); }
h4.quarter-h {
  font-size: 12px; font-weight: 700; margin: 14px 0 6px; padding: 3px 10px;
  display: inline-block; border-radius: 999px; letter-spacing: 0.02em;
  background: color-mix(in srgb, var(--series-1) 16%, transparent); color: var(--text-primary);
}
.stt-col { width: 34px; text-align: center; color: var(--text-muted); }
.quarter-empty { font-size: 12.5px; color: var(--text-muted); margin: 0 0 14px; font-style: italic; }
.tiles { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 12px; }
.tile { background: var(--surface-1); border: 1px solid var(--border); border-radius: var(--radius-md); padding: 11px 13px; box-shadow: var(--shadow-card); transition: transform .15s ease, box-shadow .15s ease; }
.tile:hover { transform: translateY(-1px); box-shadow: var(--shadow-pop); }
.tile .value { font-size: 23px; font-weight: 700; letter-spacing: -0.01em; line-height: 1; }
.tile .label { font-size: 11px; color: var(--text-secondary); margin-top: 2px; }
.tile .note { font-size: 11px; color: var(--text-muted); margin-top: 6px; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
thead th { text-align: left; font-size: 10px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--text-muted); font-weight: 700; padding: 8px 10px; border-bottom: 1px solid var(--gridline); }
tbody td { padding: 8px 10px; border-bottom: 1px solid var(--gridline); vertical-align: top; }
tbody tr { transition: background-color .12s ease; }
tbody tr:hover { background: var(--surface-2); }
tbody tr:last-child td { border-bottom: none; }
.mono { font-variant-numeric: tabular-nums; color: var(--text-secondary); font-size: 11px; }
.table-wrap { overflow-x: auto; margin-bottom: 10px; border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--surface-1); box-shadow: var(--shadow-card); }
.task-src { font-size: 10px; color: var(--text-muted); margin-top: 2px; }
.task-src-kind,
.task-src-title,
.task-src-context,
.task-src-path,
.task-src-artifact { display: block; }
.task-src-kind { font-weight: 700; color: var(--text-secondary); }
.task-src-title { color: var(--text-secondary); }
.task-src-context { color: var(--text-muted); }
.task-src-path,
.task-src-artifact { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; color: var(--text-muted); opacity: .92; word-break: break-word; }
.dep-none { color: var(--text-muted); font-style: italic; font-size: 12.5px; }
.lane-footer { margin-top: 28px; padding-top: 14px; border-top: 1px solid var(--gridline); font-size: 11.5px; color: var(--text-muted); overflow-wrap: anywhere; overflow-x: clip; }
.lane-footer .src { margin: 3px 0; overflow-wrap: anywhere; word-break: break-word; }
.lane-footer code { display: inline-block; max-width: 100%; background: var(--surface-2); border-radius: 4px; padding: 1px 5px; font-size: 11px; white-space: break-spaces; overflow-wrap: anywhere; word-break: break-all; vertical-align: bottom; }
.empty-state { text-align: center; padding: 38px 20px; color: var(--text-muted); background: var(--surface-1); border: 1px dashed var(--border); border-radius: var(--radius-md); }
.empty-state-icon { font-size: 26px; margin-bottom: 8px; opacity: .6; }
.empty-state p { margin: 0; font-size: 13px; }
.narrative { background: var(--surface-1); border: 1px solid var(--border); border-radius: var(--radius-md); padding: 11px 13px; font-size: 12.5px; box-shadow: var(--shadow-card); }
.narrative ul { margin: 6px 0 0; padding-left: 18px; }
.narrative li { margin-bottom: 4px; }
.caveat { background: color-mix(in srgb, var(--status-warning) 10%, transparent); border: 1px solid color-mix(in srgb, var(--status-warning) 35%, transparent); border-radius: var(--radius-md); padding: 9px 12px; font-size: 11.5px; margin-bottom: 12px; }
.caveat.stale { background: color-mix(in srgb, var(--series-1) 10%, transparent); border-color: color-mix(in srgb, var(--series-1) 26%, transparent); }
.caveat strong { color: #a66a00; }
.caveat.stale strong { color: var(--series-1); }
.stale-detail { margin-top: 6px; font-size: 10.5px; color: var(--text-secondary); overflow-wrap: anywhere; }
.stale-detail code { white-space: pre-wrap; }
.reliability-hi { display: inline-block; font-size: 10px; font-weight: 700; color: var(--good-text); background: color-mix(in srgb, var(--status-good) 14%, transparent); border-radius: var(--radius-sm); padding: 2px 6px; margin-bottom: 8px; }
.reliability-lo { display: inline-block; font-size: 10px; font-weight: 700; color: #a66a00; background: color-mix(in srgb, var(--status-warning) 20%, transparent); border-radius: var(--radius-sm); padding: 2px 6px; margin-bottom: 8px; }
.status-tag { display: inline-block; font-size: 10px; font-weight: 600; padding: 2px 8px; border-radius: var(--radius-sm); }
.status-tag.open { color: var(--status-warning); background: color-mix(in srgb, var(--status-warning) 16%, transparent); }
.status-tag.progress { color: var(--series-1); background: color-mix(in srgb, var(--series-1) 14%, transparent); }
.status-tag.done { color: var(--good-text); background: color-mix(in srgb, var(--status-good) 14%, transparent); }
.status-tag.blocked { color: var(--status-serious); background: color-mix(in srgb, var(--status-serious) 16%, transparent); }
.prio { display: inline-flex; align-items: center; gap: 4px; font-size: 11px; font-weight: 600; }
.prio .dot { width: 7px; height: 7px; border-radius: 2px; }
.prio.cao .dot { background: var(--ord-high); }
.prio.tb .dot { background: var(--ord-mid); }
.prio.thap .dot { background: var(--ord-low); }
.badge { display: inline-flex; align-items: center; gap: 5px; font-size: 11.5px; font-weight: 600; padding: 3px 9px 3px 7px; border-radius: 999px; white-space: nowrap; }
.badge .dot { width: 7px; height: 7px; border-radius: 50%; }
.badge.good { color: var(--good-text); background: color-mix(in srgb, var(--status-good) 14%, transparent); }
.badge.good .dot { background: var(--status-good); }
.badge.critical { color: var(--status-critical); background: color-mix(in srgb, var(--status-critical) 14%, transparent); }
.badge.critical .dot { background: var(--status-critical); }
.badge.warning { color: #a66a00; background: color-mix(in srgb, var(--status-warning) 20%, transparent); }
.badge.warning .dot { background: var(--status-warning); }
.barchart { display: flex; flex-direction: column; gap: 10px; margin: 4px 0 18px; }
.barrow { display: grid; grid-template-columns: 80px 1fr 40px; align-items: center; gap: 10px; font-size: 13px; }
.barrow .track { background: var(--gridline); border-radius: 4px; height: 14px; overflow: hidden; }
.barrow .fill { height: 100%; border-radius: 4px 0 0 4px; }
.barrow .count { text-align: right; font-variant-numeric: tabular-nums; color: var(--text-secondary); }
.legend-note { font-size: 11.5px; color: var(--text-muted); margin-top: -4px; margin-bottom: 14px; }
.chain { display: flex; flex-direction: column; gap: 0; }
.chain-stage { background: var(--surface-1); border: 1px solid var(--border); border-radius: var(--radius-md); padding: 16px 18px; box-shadow: var(--shadow-card); }
.chain-stage .stage-label { font-size: 11px; font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase; color: var(--series-1); margin-bottom: 10px; }
.chain-arrow { text-align: center; font-size: 15px; color: var(--text-muted); padding: 6px 0; }
.seg-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
.seg-card { border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 11px 13px; background: var(--page); transition: box-shadow .15s ease; }
.seg-card:hover { box-shadow: var(--shadow-card); }
.seg-card .seg-name { font-size: 12.5px; font-weight: 700; margin-bottom: 6px; }
.seg-card ul { margin: 0; padding-left: 16px; font-size: 12px; color: var(--text-secondary); }
.home-shell { display: grid; gap: 12px; }
.home-hero { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; padding: 12px 14px; background: var(--surface-1); border: 1px solid var(--border); border-radius: var(--radius-md); box-shadow: var(--shadow-card); }
.home-hero h2 { margin: 0; border: none; padding: 0; color: var(--text-primary); font-size: 15px; letter-spacing: -0.01em; text-transform: none; }
.home-intro { margin: 4px 0 0; font-size: 11.5px; line-height: 1.45; color: var(--text-secondary); max-width: 760px; }
.home-badge { display: inline-flex; align-items: center; min-height: 24px; padding: 0 9px; border-radius: 999px; background: color-mix(in srgb, var(--cat-4) 14%, transparent); color: #9a6200; font-size: 10px; font-weight: 700; white-space: nowrap; }
.home-native { display: grid; gap: 12px; }
.home-status { min-height: 18px; font-size: 11.5px; color: var(--text-secondary); }
.home-status.ok { color: var(--status-good); }
.home-status.err { color: var(--status-critical); }
.home-summary { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 12px; }
.home-tile { background: var(--surface-1); border: 1px solid var(--border); border-radius: var(--radius-md); padding: 12px; box-shadow: var(--shadow-card); }
.home-tile strong { display: block; font-size: 24px; line-height: 1; color: var(--text-primary); }
.home-tile span { display: block; margin-top: 6px; font-size: 11px; color: var(--text-secondary); }
.home-grid { display: grid; grid-template-columns: 1.1fr 0.9fr; gap: 12px; align-items: start; }
.home-list { display: grid; gap: 8px; }
.home-item { border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--page); padding: 10px 11px; }
.home-item strong { display: block; font-size: 12px; color: var(--text-primary); }
.home-item span { display: block; margin-top: 4px; font-size: 11px; color: var(--text-secondary); }
.search-shell { display: grid; gap: 12px; }
.search-hero { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; padding: 12px 14px; background: var(--surface-1); border: 1px solid var(--border); border-radius: var(--radius-md); box-shadow: var(--shadow-card); }
.search-hero h2 { margin: 0; border: none; padding: 0; color: var(--text-primary); font-size: 15px; letter-spacing: -0.01em; text-transform: none; }
.search-intro { margin: 4px 0 0; font-size: 11.5px; line-height: 1.45; color: var(--text-secondary); max-width: 760px; }
.search-badge { display: inline-flex; align-items: center; min-height: 24px; padding: 0 9px; border-radius: 999px; background: color-mix(in srgb, var(--cat-2) 14%, transparent); color: var(--cat-2); font-size: 10px; font-weight: 700; white-space: nowrap; }
.search-native { display: grid; gap: 12px; }
.search-form, .search-block { background: var(--surface-1); border: 1px solid var(--border); border-radius: var(--radius-md); box-shadow: var(--shadow-card); padding: 12px; }
.search-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
.search-field { display: grid; gap: 6px; }
.search-field label { font-size: 10px; font-weight: 700; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.06em; }
.search-field input, .search-field select { min-height: 34px; border: 1px solid var(--border); border-radius: var(--radius-md); padding: 0 10px; background: var(--page); color: var(--text-primary); font: inherit; }
.search-actions { display: flex; gap: 8px; align-items: center; justify-content: space-between; flex-wrap: wrap; margin-top: 10px; }
.search-summary { min-height: 18px; font-size: 11.5px; color: var(--text-secondary); }
.search-results { display: grid; gap: 10px; }
.search-result { border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--page); padding: 11px 12px; }
.search-result strong { display: block; font-size: 12px; color: var(--text-primary); }
.search-meta { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 7px; }
.search-chip { display: inline-flex; align-items: center; min-height: 22px; padding: 0 8px; border-radius: 999px; background: var(--surface-2); color: var(--text-secondary); font-size: 10px; font-weight: 700; }
.search-snippet { margin-top: 8px; font-size: 11.5px; line-height: 1.5; color: var(--text-secondary); white-space: pre-wrap; }
.search-empty { padding: 12px; border: 1px dashed var(--border); border-radius: var(--radius-md); color: var(--text-secondary); background: color-mix(in srgb, var(--surface-1) 84%, transparent); }
.nav-shell { display: grid; gap: 12px; }
.nav-hero { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; padding: 12px 14px; background: var(--surface-1); border: 1px solid var(--border); border-radius: var(--radius-md); box-shadow: var(--shadow-card); }
.nav-hero h2 { margin: 0; border: none; padding: 0; color: var(--text-primary); font-size: 15px; letter-spacing: -0.01em; text-transform: none; }
.nav-intro { margin: 4px 0 0; font-size: 11.5px; line-height: 1.45; color: var(--text-secondary); max-width: 760px; }
.nav-badge { display: inline-flex; align-items: center; min-height: 24px; padding: 0 9px; border-radius: 999px; background: color-mix(in srgb, var(--cat-3) 14%, transparent); color: var(--cat-3); font-size: 10px; font-weight: 700; white-space: nowrap; }
.nav-native { display: grid; gap: 12px; }
.nav-toolbar, .nav-block { background: var(--surface-1); border: 1px solid var(--border); border-radius: var(--radius-md); box-shadow: var(--shadow-card); padding: 12px; }
.nav-toolbar-grid { display: grid; grid-template-columns: 1.2fr 0.8fr 0.8fr; gap: 10px; }
.nav-field { display: grid; gap: 6px; }
.nav-field label { font-size: 10px; font-weight: 700; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.06em; }
.nav-field input, .nav-field select { min-height: 34px; border: 1px solid var(--border); border-radius: var(--radius-md); padding: 0 10px; background: var(--page); color: var(--text-primary); font: inherit; }
.nav-summary { min-height: 18px; font-size: 11.5px; color: var(--text-secondary); margin-top: 10px; }
.nav-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.nav-list { display: grid; gap: 8px; margin-top: 8px; }
.nav-item { width: 100%; text-align: left; border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--page); padding: 10px 11px; cursor: pointer; color: inherit; font: inherit; }
.nav-item:hover { border-color: color-mix(in srgb, var(--series-1) 35%, var(--border)); box-shadow: var(--shadow-card); }
.nav-item strong { display: block; font-size: 12px; color: var(--text-primary); }
.nav-item span { display: block; margin-top: 4px; font-size: 11px; color: var(--text-secondary); }
.nav-empty { padding: 12px; border: 1px dashed var(--border); border-radius: var(--radius-md); color: var(--text-secondary); background: color-mix(in srgb, var(--surface-1) 84%, transparent); }
.seg-card li { margin-bottom: 3px; }
/* Panel "Khám phá" (#knowledge-links) = 2 cột: trái Duyệt nhanh (.nav-rail, rail hẹp cố định),
   phải chi tiết Links (.links-shell, co giãn). .nav-rail override .nav-toolbar-grid/.nav-grid
   (vốn thiết kế 3 cột cho trang full-width) về 1 cột vì rail chỉ rộng ~360px. */
.explore-shell { display: grid; grid-template-columns: 360px 1fr; gap: 14px; align-items: start; }
.nav-rail .nav-toolbar-grid { grid-template-columns: 1fr; }
.nav-rail .nav-grid { grid-template-columns: 1fr; }
.nav-rail .nav-list { max-height: 180px; }
@media (max-width: 980px) { .explore-shell { grid-template-columns: 1fr; } }
.cap-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
.cap-card { border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 11px 13px; background: var(--page); transition: box-shadow .15s ease; }
.cap-card:hover { box-shadow: var(--shadow-card); }
.cap-card .cap-name { font-size: 12.5px; font-weight: 700; margin-bottom: 6px; }
.cap-card ul { margin: 0; padding-left: 16px; font-size: 12px; color: var(--text-secondary); }
.cap-card li { margin-bottom: 3px; }
.it-list { margin: 0; padding-left: 18px; font-size: 13px; }
.it-list li { margin-bottom: 5px; }
.chip-row { display: flex; flex-wrap: wrap; gap: 7px; }
.chip { display: inline-block; font-size: 12px; padding: 4px 10px; border-radius: 999px; background: color-mix(in srgb, var(--series-1) 10%, transparent); border: 1px solid color-mix(in srgb, var(--series-1) 26%, transparent); color: var(--text-primary); }
/* Tab F — WorkFlow (bảng tổng hợp, thay Mermaid từ v1.3). */
.workflow-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.workflow-table thead th { text-align: left; font-size: 10px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--text-muted); font-weight: 700; padding: 8px 10px; border-bottom: 2px solid var(--gridline); white-space: nowrap; }
.workflow-table tbody td { padding: 8px 10px; border-bottom: 1px solid var(--gridline); vertical-align: top; }
.workflow-table tbody tr { transition: background-color .12s ease; }
.workflow-table tbody tr:hover { background: var(--surface-2); }
.workflow-table tbody tr:last-child td { border-bottom: none; }
.workflow-table td[rowspan] { border-right: 1px solid var(--gridline); }
.workflow-table .col-combo { min-width: 110px; font-weight: 600; background: color-mix(in srgb, var(--series-1) 5%, transparent); }
.workflow-table .col-backlog { min-width: 130px; }
.workflow-table .col-backlog .backlog-id { font-size: 10px; color: var(--text-muted); font-variant-numeric: tabular-nums; display: block; }
.workflow-table .col-backlog .golive-plan { font-size: 10px; color: var(--good-text); margin-top: 2px; }
.workflow-table .col-sub { min-width: 100px; font-size: 11px; }
.workflow-table .col-sub .so-id { font-size: 10px; color: var(--text-muted); display: block; }
.workflow-table .col-task { min-width: 140px; }
.workflow-table .unlinked { color: var(--text-muted); font-style: italic; }
.workflow-open-btn {
  display: inline-flex; align-items: center; gap: 8px; min-height: 34px; padding: 7px 12px;
  border: 1px solid color-mix(in srgb, var(--series-1) 30%, var(--border));
  border-radius: 12px;
  background:
    linear-gradient(180deg, color-mix(in srgb, var(--series-1) 12%, white 88%), color-mix(in srgb, var(--series-1) 6%, white 94%));
  color: var(--series-1); font: inherit; font-size: 11px; font-weight: 800; cursor: pointer;
  line-height: 1.1; box-shadow: 0 6px 14px rgba(22, 66, 122, .08);
  transition: transform .12s ease, box-shadow .12s ease, border-color .12s ease, background .12s ease;
}
.workflow-open-btn::before {
  content: '◷';
  width: 20px; height: 20px; border-radius: 999px; display: inline-flex; align-items: center; justify-content: center;
  background: color-mix(in srgb, var(--series-1) 14%, white 86%);
  color: var(--series-1); font-size: 11px; flex: 0 0 auto;
}
.workflow-open-btn span {
  display: inline-flex; align-items: center; min-height: 18px; padding: 0 7px; border-radius: 999px;
  font-size: 10px; font-weight: 700; color: var(--text-secondary);
  background: rgba(255,255,255,.8); border: 1px solid color-mix(in srgb, var(--series-1) 12%, var(--border));
}
.workflow-open-btn:hover {
  transform: translateY(-1px);
  border-color: color-mix(in srgb, var(--series-1) 46%, var(--border));
  background:
    linear-gradient(180deg, color-mix(in srgb, var(--series-1) 18%, white 82%), color-mix(in srgb, var(--series-1) 8%, white 92%));
  box-shadow: 0 10px 20px rgba(22, 66, 122, .12);
}
.workflow-open-btn:active { transform: translateY(0); box-shadow: 0 4px 10px rgba(22, 66, 122, .08); }
.workflow-empty { color: var(--text-muted); }
.workflow-modal {
  position: fixed; inset: 0; z-index: 60; display: none; align-items: stretch; justify-content: flex-end;
  background: rgba(11,11,11,.38);
}
.workflow-modal.open { display: flex; }
.workflow-modal-panel {
  width: 100vw; max-width: 100vw; height: 100vh; overflow-y: auto; background: var(--surface-1);
  border-left: none; box-shadow: none; padding: 24px 28px 32px;
}
.workflow-modal-close {
  min-height: 32px; padding: 0 12px; border: 1px solid var(--border); border-radius: 999px;
  background: var(--page); color: var(--text-primary); font: inherit; font-size: 11px; cursor: pointer;
}
.workflow-modal-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; margin-bottom: 14px; }
.workflow-modal-head h3 { margin: 0; font-size: 16px; }
.workflow-modal-sub { font-size: 11.5px; color: var(--text-secondary); margin-top: 4px; }
.workflow-modal-meta { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0 16px; }
.workflow-modal-chip {
  display: inline-flex; align-items: center; min-height: 24px; padding: 0 9px; border-radius: 999px;
  background: var(--page); border: 1px solid var(--border); font-size: 10px; font-weight: 700; color: var(--text-secondary);
}
.workflow-mini-track {
  position: relative; height: 26px; border-radius: 8px; background: var(--surface-2);
  border: 1px solid var(--border); margin: 8px 0 16px;
}
.workflow-mini-bar {
  position: absolute; left: 0; top: 3px; bottom: 3px; min-width: 96px; border-radius: 6px; display: flex;
  align-items: center; padding: 0 10px; font-size: 10px; font-weight: 700; color: #fff; background: var(--series-1);
}
.workflow-mini-bar.done { background: var(--status-good); }
.workflow-mini-bar.blocked { background: var(--status-critical); }
.workflow-event-list { display: grid; gap: 10px; }
.workflow-timeline-table {
  display: grid; gap: 0; border: 1px solid var(--border); border-radius: 18px; overflow: hidden;
  background: var(--page);
}
.workflow-timeline-head,
.workflow-timeline-row {
  display: grid; grid-template-columns: minmax(220px, .95fr) minmax(360px, 1.35fr) minmax(320px, 1.2fr);
}
.workflow-timeline-head {
  background: color-mix(in srgb, var(--surface-2) 78%, white 22%);
  border-bottom: 1px solid var(--border);
}
.workflow-timeline-head .workflow-timeline-cell {
  min-height: 46px; display: flex; align-items: center; font-size: 10.5px; font-weight: 800;
  text-transform: uppercase; letter-spacing: .05em; color: var(--text-secondary);
}
.workflow-timeline-row + .workflow-timeline-row { border-top: 1px solid var(--border); }
.workflow-timeline-cell {
  padding: 14px 16px; min-width: 0;
}
.workflow-timeline-cell + .workflow-timeline-cell { border-left: 1px solid var(--border); }
.workflow-timeline-anchor {
  display: grid; gap: 8px; height: 100%; padding: 12px 13px; border-radius: 14px;
  background: linear-gradient(180deg, color-mix(in srgb, var(--series-1) 8%, white 92%), var(--page));
  border: 1px solid color-mix(in srgb, var(--series-1) 18%, var(--border));
}
.workflow-anchor-kicker {
  display: inline-flex; align-items: center; width: fit-content; min-height: 22px; padding: 0 8px;
  border-radius: 999px; font-size: 10px; font-weight: 800; letter-spacing: .04em;
  background: color-mix(in srgb, var(--series-1) 10%, white 90%);
  border: 1px solid color-mix(in srgb, var(--series-1) 22%, var(--border));
  color: var(--series-1);
}
.workflow-anchor-date {
  font-size: 11px; font-weight: 800; color: var(--series-1); letter-spacing: .04em; text-transform: uppercase;
}
.workflow-anchor-title { font-size: 13px; font-weight: 800; color: var(--text-primary); line-height: 1.4; }
.workflow-anchor-detail { font-size: 11.5px; color: var(--text-secondary); line-height: 1.5; }
.workflow-anchor-facts {
  display: grid; gap: 6px; margin-top: 2px;
}
.workflow-anchor-fact {
  display: grid; grid-template-columns: 74px 1fr; gap: 8px; align-items: start;
  font-size: 11px; color: var(--text-secondary);
}
.workflow-anchor-fact strong {
  color: var(--text-muted); font-size: 10px; text-transform: uppercase; letter-spacing: .04em;
}
.workflow-column-stack { display: grid; gap: 8px; align-content: start; }
.workflow-column-empty {
  display: grid; place-items: center; min-height: 92px; padding: 12px; border: 1px dashed var(--border);
  border-radius: 12px; color: var(--text-secondary); background: color-mix(in srgb, var(--surface-2) 72%, white 28%);
  font-size: 11.5px; text-align: center;
}
.workflow-group {
  display: grid; grid-template-columns: minmax(220px, 1.05fr) 28px minmax(260px, 1.25fr) 28px minmax(240px, 1.1fr);
  gap: 0; align-items: stretch;
}
.workflow-stage {
  border: 1px solid var(--border); border-radius: 16px; background: var(--page); min-height: 100%;
  box-shadow: var(--shadow-card);
}
.workflow-stage.anchor {
  background: linear-gradient(180deg, color-mix(in srgb, var(--series-1) 8%, white 92%), var(--page));
}
.workflow-stage.tasks {
  background: linear-gradient(180deg, color-mix(in srgb, var(--surface-2) 82%, white 18%), var(--page));
}
.workflow-stage.logs {
  background: linear-gradient(180deg, color-mix(in srgb, var(--status-warning) 8%, white 92%), var(--page));
}
.workflow-stage-head {
  padding: 12px 14px 10px; border-bottom: 1px solid var(--border);
}
.workflow-stage-date {
  font-size: 11px; font-weight: 800; color: var(--series-1); letter-spacing: .03em; text-transform: uppercase;
}
.workflow-stage-title { margin-top: 5px; font-size: 13px; font-weight: 800; color: var(--text-primary); line-height: 1.4; }
.workflow-stage-sub { margin-top: 5px; font-size: 11px; color: var(--text-secondary); line-height: 1.45; }
.workflow-stage-body { display: grid; gap: 8px; padding: 12px 14px 14px; }
.workflow-group-section { display: grid; gap: 8px; align-content: start; }
.workflow-group-label {
  display: inline-flex; align-items: center; min-height: 22px; width: fit-content; padding: 0 8px;
  border-radius: 999px; background: var(--surface-1); border: 1px solid var(--border);
  font-size: 10px; font-weight: 800; color: var(--text-secondary); text-transform: uppercase; letter-spacing: .04em;
}
.workflow-entry-list { display: grid; gap: 8px; }
.workflow-connector {
  position: relative; min-height: 100%;
}
.workflow-connector::before {
  content: ''; position: absolute; left: 5px; right: 5px; top: 50%; height: 2px;
  background: linear-gradient(90deg, color-mix(in srgb, var(--series-1) 50%, transparent), color-mix(in srgb, var(--status-warning) 40%, transparent));
  transform: translateY(-50%);
}
.workflow-connector::after {
  content: '›'; position: absolute; right: 2px; top: 50%; transform: translateY(-50%);
  color: var(--series-1); font-size: 18px; font-weight: 800;
}
.workflow-entry {
  display: grid; gap: 6px; padding: 10px 11px; border: 1px solid var(--border);
  border-radius: 12px; background: var(--surface-1);
}
.workflow-entry.task { border-left: 4px solid color-mix(in srgb, var(--series-1) 55%, transparent); }
.workflow-entry.log { border-left: 4px solid color-mix(in srgb, var(--status-warning) 55%, transparent); }
.workflow-anchor-card {
  display: grid; gap: 8px; padding: 11px 12px; border: 1px solid color-mix(in srgb, var(--series-1) 26%, var(--border));
  border-radius: 14px; background: rgba(255,255,255,.78);
}
.workflow-anchor-meta {
  display: flex; flex-wrap: wrap; gap: 6px;
}
.workflow-anchor-meta span {
  display: inline-flex; align-items: center; min-height: 22px; padding: 0 8px; border-radius: 999px;
  background: color-mix(in srgb, var(--series-1) 10%, white 90%); border: 1px solid color-mix(in srgb, var(--series-1) 24%, var(--border));
  font-size: 10px; color: var(--text-secondary);
}
.workflow-entry-top { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.workflow-entry-date { font-size: 10px; font-weight: 700; color: var(--text-muted); letter-spacing: .04em; text-transform: uppercase; }
.workflow-event {
  display: grid; grid-template-columns: 108px 1fr; gap: 12px; padding: 11px 12px; border: 1px solid var(--border);
  border-radius: var(--radius-md); background: var(--page);
}
.workflow-event-date { font-size: 10px; font-weight: 700; color: var(--text-muted); text-transform: uppercase; letter-spacing: .05em; }
.workflow-event-kind { display: inline-flex; align-items: center; min-height: 20px; padding: 0 7px; border-radius: 999px; font-size: 9.5px; font-weight: 700; margin-bottom: 6px; }
.workflow-event-kind.task { background: color-mix(in srgb, var(--series-1) 12%, transparent); color: var(--series-1); }
.workflow-event-kind.log { background: color-mix(in srgb, var(--status-warning) 16%, transparent); color: #9a6200; }
.workflow-event-title { font-size: 12px; font-weight: 700; color: var(--text-primary); }
.workflow-event-desc { margin-top: 4px; font-size: 11.5px; color: var(--text-secondary); line-height: 1.5; white-space: pre-wrap; }
.workflow-event-meta { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 7px; }
.workflow-event-meta span {
  display: inline-flex; align-items: center; min-height: 22px; padding: 0 8px; border-radius: 999px;
  background: var(--surface-1); border: 1px solid var(--border); font-size: 10px; color: var(--text-secondary);
}
.workflow-modal-panel .workflow-timeline-table { border-radius: 10px; }
.workflow-modal-panel .workflow-timeline-head .workflow-timeline-cell { min-height: 32px; font-size: 9.5px; }
.workflow-modal-panel .workflow-timeline-cell { padding: 7px 9px; }
.workflow-modal-panel .workflow-timeline-anchor { gap: 4px; padding: 7px 8px; border-radius: 8px; }
.workflow-modal-panel .workflow-anchor-kicker { min-height: 16px; padding: 0 5px; font-size: 9px; }
.workflow-modal-panel .workflow-anchor-date { font-size: 9px; }
.workflow-modal-panel .workflow-anchor-title { font-size: 11px; line-height: 1.25; }
.workflow-modal-panel .workflow-anchor-detail { font-size: 9.5px; line-height: 1.25; }
.workflow-modal-panel .workflow-anchor-facts { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 4px 8px; }
.workflow-modal-panel .workflow-anchor-fact { grid-template-columns: 1fr; gap: 1px; font-size: 9.5px; }
.workflow-modal-panel .workflow-anchor-fact strong { font-size: 8px; }
.workflow-modal-panel .workflow-anchor-meta span { min-height: 16px; padding: 0 5px; font-size: 8.5px; }
.workflow-modal-panel .workflow-column-stack { gap: 3px; }
.workflow-modal-panel .workflow-column-empty { min-height: 38px; padding: 6px; border-radius: 7px; font-size: 9px; }
.workflow-modal-panel .workflow-entry { gap: 3px; padding: 5px 6px; border-radius: 7px; }
.workflow-modal-panel .workflow-entry-grid { display: grid; gap: 4px 6px; align-items: start; }
.workflow-modal-panel .workflow-entry-grid.task-grid { grid-template-columns: minmax(72px, .68fr) minmax(0, 1.7fr) minmax(84px, .76fr) minmax(66px, .58fr) minmax(74px, .68fr) minmax(66px, .55fr); }
.workflow-modal-panel .workflow-entry-grid.log-grid { grid-template-columns: minmax(96px, .7fr) minmax(0, 1.1fr) minmax(74px, .62fr) minmax(0, 1.25fr); }
.workflow-modal-panel .workflow-entry-grid.anchor-grid { grid-template-columns: minmax(56px, .5fr) minmax(68px, .56fr) minmax(96px, .78fr) minmax(0, 1.9fr); }
.workflow-modal-panel .workflow-grid-k { font-size: 7.5px; text-transform: uppercase; letter-spacing: .04em; color: var(--text-muted); }
.workflow-modal-panel .workflow-grid-v { font-size: 9.5px; color: var(--text-primary); line-height: 1.2; }
.workflow-modal-panel .workflow-grid-v.clip { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.workflow-modal-panel .workflow-source-link {
  display: inline-flex; align-items: center; gap: 4px; color: var(--series-1);
  text-decoration: none; cursor: pointer; font-weight: 600;
}
.workflow-modal-panel .workflow-source-link:hover { text-decoration: underline; }
.workflow-modal-panel .workflow-grid-v .status-tag { font-size: 9px; padding: 2px 6px; border-radius: 999px; white-space: nowrap; }
.workflow-modal-panel .workflow-grid-v .status-tag.neutral { color: var(--text-secondary); background: color-mix(in srgb, var(--text-secondary) 12%, transparent); }
.workflow-modal-panel .workflow-grid-v .status-tag.log-warn { color: #9a6200; background: color-mix(in srgb, var(--status-warning) 18%, transparent); }
.workflow-filter-row { display: flex; justify-content: flex-end; margin: 6px 0 10px; }
.workflow-filter-control { display: inline-flex; align-items: center; gap: 8px; font-size: 10px; color: var(--text-secondary); }
.workflow-filter-select {
  min-height: 28px; min-width: 170px; padding: 4px 26px 4px 10px; border-radius: 999px;
  border: 1px solid var(--border); background: var(--page); color: var(--text-primary); font-size: 10.5px;
}
.workflow-head-cell { display: grid; gap: 6px; align-content: start; }
.workflow-head-title { font-size: 9.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .04em; color: var(--text-secondary); }
.workflow-head-cell .workflow-filter-control { display: grid; gap: 4px; align-items: start; }
.workflow-head-cell .workflow-filter-control span { font-size: 8.5px; color: var(--text-muted); }
.workflow-head-cell .workflow-filter-select { min-width: 100%; min-height: 24px; padding: 3px 24px 3px 8px; font-size: 9.5px; }
@media (max-width: 980px) {
  .workflow-timeline-head,
  .workflow-timeline-row { grid-template-columns: 1fr; }
  .workflow-timeline-head { display: none; }
  .workflow-timeline-cell + .workflow-timeline-cell { border-left: none; border-top: 1px solid var(--border); }
  .workflow-modal-panel .workflow-anchor-facts { grid-template-columns: 1fr; }
  .workflow-modal-panel .workflow-entry-grid.task-grid,
  .workflow-modal-panel .workflow-entry-grid.log-grid,
  .workflow-modal-panel .workflow-entry-grid.anchor-grid { grid-template-columns: 1fr 1fr; }
  .workflow-group { grid-template-columns: 1fr; gap: 10px; }
  .workflow-connector { min-height: 18px; }
  .workflow-connector::before {
    left: 50%; right: auto; top: 2px; bottom: 2px; width: 2px; height: auto;
    transform: translateX(-50%);
    background: linear-gradient(180deg, color-mix(in srgb, var(--series-1) 50%, transparent), color-mix(in srgb, var(--status-warning) 40%, transparent));
  }
  .workflow-connector::after {
    content: '⌄'; left: 50%; right: auto; top: auto; bottom: -2px; transform: translateX(-50%);
  }
}
body.workflow-modal-open { overflow: hidden; }

/* Cột "Timeline" inline trong bảng C (tab "C. Backlog kế hoạch") — thanh Gantt mini kiểu MS
   Project ngay tại mỗi dòng backlog, thay cho tab "G. Timeline" riêng đã gộp vào đây (2026-08-25,
   theo yêu cầu người dùng — xem dashboard_renderer._render_c_gantt_cell/_group_c_section_by_quarter
   và timeline_sync.load_lane_timeline_bars). % left/width của .c-gantt-bar TÁI SỬ DỤNG nguyên từ
   TimelineRow, không tính lại — nên vẫn đúng trục thời gian chung của cả lane dù mỗi Quý nằm
   trong 1 <table> riêng (canonical_header lặp lại giống hệt nhau ở mỗi bảng Quý). */
.c-gantt-th { min-width: 260px; padding: 4px 8px 6px !important; vertical-align: bottom; }
.c-gantt-scale { position: relative; display: flex; }
.c-gantt-scale span { flex: 1 1 0; font-size: 8.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .02em; color: var(--text-muted); text-align: left; padding-left: 3px; border-left: 1px solid var(--gridline); }
.c-gantt-scale span:first-child { border-left: none; }
.c-gantt-td { min-width: 260px; padding: 4px 8px !important; }
.c-gantt-track { position: relative; height: 20px; background: var(--surface-2); border-radius: 4px; }
.c-gantt-bar { position: absolute; top: 2px; height: 16px; border-radius: 3px; cursor: default; }
.c-gantt-bar.tl-inferred { opacity: .55; background-image: repeating-linear-gradient(135deg, rgba(255,255,255,.35) 0 4px, transparent 4px 8px); }
.c-gantt-marker { position: absolute; top: -2px; bottom: -2px; width: 2px; }
.c-gantt-marker.commit-live { background: var(--status-critical); }
.c-gantt-marker.commit-past { background: var(--text-muted); opacity: .55; }
.c-gantt-marker.actual { background: var(--good-text); }
.c-gantt-marker.today { background: var(--series-1); opacity: .7; width: 1.5px; }
.c-gantt-none { color: var(--text-muted); font-size: 11px; }
/* background-color (không dùng shorthand "background") để không đè mất background-image sọc chéo
   của .tl-inferred khi 1 bar vừa mang class trạng thái vừa mang tl-inferred (2 class cùng specificity,
   "background" shorthand đứng sau sẽ reset background-image về none nếu không khai báo lại). */
.c-gantt-bar.tl-done, .c-gantt-swatch.tl-done { background-color: var(--status-good); }
.c-gantt-bar.tl-progress, .c-gantt-swatch.tl-progress { background-color: var(--series-1); }
.c-gantt-bar.tl-open, .c-gantt-swatch.tl-open { background-color: var(--surface-1); border: 1px solid var(--border); }
.c-gantt-bar.tl-onhold, .c-gantt-swatch.tl-onhold { background-color: var(--status-warning); }
.c-gantt-bar.tl-cancelled, .c-gantt-swatch.tl-cancelled { background-color: var(--text-muted); }
.c-gantt-bar.tl-blocked, .c-gantt-swatch.tl-blocked { background-color: var(--status-critical); }
.c-gantt-swatch.today { background: var(--series-1); opacity: .7; }
.c-gantt-legend { display: flex; flex-wrap: wrap; gap: 12px; font-size: 11px; color: var(--text-secondary); margin: -2px 0 12px; }
.c-gantt-legend-item { display: inline-flex; align-items: center; gap: 5px; }
.c-gantt-swatch { width: 10px; height: 10px; border-radius: 3px; display: inline-block; }
@media (max-width: 640px) {
  .c-gantt-th, .c-gantt-td { min-width: 160px; }
}

/* lane sub-tabs (A/C/D/E trong 1 lane) — segmented control */
.lane-tabs { display: inline-flex; flex-wrap: wrap; gap: 2px; background: var(--surface-2); border-radius: var(--radius-md); padding: 3px; margin-bottom: 14px; }
.lane-tab-btn { background: none; border: none; border-radius: calc(var(--radius-md) - 3px); padding: 5px 10px; font-size: 11.5px; color: var(--text-secondary); cursor: pointer; transition: background-color .15s ease, color .15s ease, box-shadow .15s ease; }
.lane-tab-btn.active { background: var(--surface-1); color: var(--text-primary); font-weight: 600; box-shadow: var(--shadow-card); }
.lane-tab-panel { display: none; }
.lane-tab-panel.active { display: block; animation: fade-in .15s ease; }
.table-toolbar {
  padding: 9px 10px;
  border-bottom: 1px solid var(--gridline);
  background:
    linear-gradient(180deg, color-mix(in srgb, var(--surface-2) 70%, transparent), transparent 100%),
    var(--surface-1);
}
.table-toolbar-main { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.table-toolbar-advanced { display: none; gap: 6px; flex-wrap: wrap; margin-top: 6px; padding-top: 6px; border-top: 1px dashed var(--gridline); }
.table-toolbar-advanced.open { display: flex; }
.table-filter-chip {
  min-height: 28px; padding: 0 10px; border: 1px solid var(--border); border-radius: 999px;
  background: var(--page); color: var(--text-secondary); font-size: 10.5px; font-weight: 600; cursor: pointer;
}
.table-filter-chip.active { background: color-mix(in srgb, var(--series-1) 12%, transparent); color: var(--series-1); border-color: color-mix(in srgb, var(--series-1) 28%, transparent); }
.table-filter-search,
.table-filter-select {
  min-height: 28px; border: 1px solid var(--border); border-radius: 999px; background: var(--page);
  color: var(--text-primary); font-size: 11px;
}
.table-filter-search { min-width: 220px; padding: 5px 10px; flex: 1 1 220px; }
.table-filter-select { min-width: 138px; padding: 5px 28px 5px 9px; }
.table-filter-meta { font-size: 10px; color: var(--text-muted); margin-left: auto; padding: 0 2px; white-space: nowrap; }
.table-filter-reset {
  min-height: 28px; padding: 0 10px; border: 1px solid var(--border); border-radius: 999px;
  background: transparent; color: var(--text-secondary); font-size: 10.5px; cursor: pointer;
}
.table-filter-empty td { text-align: center; color: var(--text-muted); font-style: italic; }

@media (max-width: 860px) {
  /* Màn hình hẹp: bỏ model 2-pane cuộn riêng — quá chật để vừa sidebar cố định vừa nội dung,
     chuyển về trang cuộn bình thường (sidebar nằm ngang trên cùng, cuộn theo trang như cũ). */
  body { overflow: auto; }
  .app-shell { flex-direction: column; height: auto; }
  .sidebar { width: 100%; height: auto; overflow-y: visible; border-right: none; border-bottom: 1px solid var(--border); }
  .sidebar-nav { flex-direction: row; overflow-x: auto; padding: 6px 10px; }
  .sidebar-label { display: none; }
  .side-item { white-space: nowrap; }
  .app-main { height: auto; overflow-y: visible; }
  .topbar, .content { padding-left: 14px; padding-right: 14px; }
  .topbar-row { align-items: flex-start; }
  .topbar-tools { align-items: flex-start; }
  .settings-panel { left: 0; right: auto; width: min(320px, calc(100vw - 24px)); }
  .sync-config summary { text-align: left; }
  .table-filter-search, .table-filter-select { width: 100%; min-width: 0; }
  .table-filter-meta { margin-left: 0; width: 100%; }
  .tiles, .seg-grid, .cap-grid { grid-template-columns: repeat(2, 1fr); }
  .barrow { grid-template-columns: 60px 1fr 34px; }
}
"""

_JS = """
function wireSideItem(btn) {
  if (!btn || btn.dataset.wired === '1') return;
  btn.dataset.wired = '1';
  btn.addEventListener('click', () => {
    document.querySelectorAll('.side-item, .common-item').forEach((b) => b.classList.remove('active'));
    document.querySelectorAll('.lane-panel').forEach((p) => p.classList.remove('active'));
    btn.classList.add('active');
    var targetPanel = document.getElementById(btn.dataset.target);
    if (targetPanel) targetPanel.classList.add('active');
    if (btn.dataset.target === 'manage-users' && window.__bootManageUsersInline) {
      window.__bootManageUsersInline();
    }
    if (btn.dataset.target === 'system-health' && window.__loadHealthInline) {
      window.__loadHealthInline();
    }
    if (btn.dataset.target === 'knowledge-links' && window.__loadKnowledgeLinksInline) {
      window.__loadKnowledgeLinksInline();
    }
    if (btn.dataset.target === 'retrieval-debug' && window.__loadRetrievalDebugInline) {
      window.__loadRetrievalDebugInline();
    }
    if (btn.dataset.target === 'qtit-raci' && window.__loadQtitRaciInline) {
      window.__loadQtitRaciInline();
    }
    if (btn.dataset.target === 'ai-agents' && window.__loadAiAgentsInline) {
      window.__loadAiAgentsInline();
    }
    if (btn.dataset.target === 'strategy-methodology' && window.__loadStrategyMethodologyInline) {
      window.__loadStrategyMethodologyInline();
    }
  });
}
document.querySelectorAll('.side-item[data-target], .common-item[data-target]').forEach(wireSideItem);
document.addEventListener('click', function (event) {
  var btn = event.target && event.target.closest ? event.target.closest('.side-item[data-target], .common-item[data-target]') : null;
  if (!btn) return;
  if (btn.dataset.wired === '1') return;
  wireSideItem(btn);
  btn.click();
});
document.querySelectorAll('.lane-tab-btn').forEach((btn) => {
  btn.addEventListener('click', () => {
    var panel = btn.closest('.lane-panel');
    panel.querySelectorAll('.lane-tab-btn').forEach((b) => b.classList.remove('active'));
    panel.querySelectorAll('.lane-tab-panel').forEach((p) => p.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById(btn.dataset.target).classList.add('active');
  });
});
function toggleOrgBlock(id) {
  var el = document.getElementById(id);
  if (el) el.classList.toggle('collapsed');
}
document.querySelectorAll('.ws-tab-btn').forEach((btn) => {
  btn.addEventListener('click', () => {
    var panel = btn.closest('.lane-panel');
    panel.querySelectorAll('.ws-tab-btn').forEach((b) => b.classList.remove('active'));
    panel.querySelectorAll('.ws-sub-panel').forEach((p) => p.classList.remove('active'));
    btn.classList.add('active');
    var sub = document.getElementById(btn.dataset.sub);
    if (sub) {
      sub.classList.add('active');
      if (window.__loadWorkspaceSubPanel) window.__loadWorkspaceSubPanel(sub);
    }
  });
});

(function () {
  var modal = document.createElement('div');
  modal.className = 'workflow-modal';
  modal.innerHTML = '<div class="workflow-modal-panel"></div>';
  document.body.appendChild(modal);
  var panel = modal.querySelector('.workflow-modal-panel');

  function esc(text) {
    return String(text || '').replace(/[&<>\"]/g, function (ch) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[ch];
    });
  }

  function normalizeText(text) {
    return String(text || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '');
  }

  function workflowStatusClass(text, kind) {
    var value = normalizeText(text);
    if (!value || value === '\u2014') return 'neutral';
    if (kind === 'log') {
      if (value.indexOf('da xu ly') >= 0 || value.indexOf('closed') >= 0) return 'done';
      if (value.indexOf('alert cao') >= 0 || value.indexOf('canh bao') >= 0 || value.indexOf('overdue') >= 0 || value.indexOf('bac bo') >= 0) return 'blocked';
      if (value.indexOf('alert') >= 0 || value.indexOf('pending') >= 0 || value.indexOf('doi moc') >= 0 || value.indexOf('active') >= 0 || value.indexOf('open') >= 0) return 'log-warn';
      if (value.indexOf('superseded') >= 0) return 'neutral';
      return 'progress';
    }
    if (value.indexOf('hoan thanh') >= 0 || value.indexOf('done') >= 0 || value.indexOf('closed') >= 0) return 'done';
    if (value.indexOf('blocked') >= 0 || value.indexOf('cancel') >= 0 || value.indexOf('huy') >= 0) return 'blocked';
    if (value.indexOf('ready') >= 0 || value.indexOf('in progress') >= 0 || value.indexOf('dang') >= 0 || value.indexOf('tiep nhan') >= 0) return 'progress';
    if (value.indexOf('open') >= 0 || value.indexOf('moi') >= 0 || value.indexOf('pending') >= 0) return 'open';
    return 'neutral';
  }

  function renderStatusBadge(text, kind) {
    var label = text || '\u2014';
    return '<span class="status-tag ' + workflowStatusClass(label, kind) + '">' + esc(label) + '</span>';
  }

  function renderTaskEntry(item) {
    return '<div class="workflow-entry task">'
      + '<div class="workflow-entry-grid task-grid">'
      + '<div><div class="workflow-grid-k">Sub/SO</div><div class="workflow-grid-v mono">' + esc(item.subbacklog || '\u2014') + '</div></div>'
      + '<div><div class="workflow-grid-k">Task</div><div class="workflow-grid-v">' + esc(item.task || 'Ch\u01b0a r\u00f5 task') + '</div></div>'
      + '<div><div class="workflow-grid-k">PIC</div><div class="workflow-grid-v clip">' + esc(item.pic || 'Ch\u01b0a r\u00f5') + '</div></div>'
      + '<div><div class="workflow-grid-k">Prio</div><div class="workflow-grid-v">' + esc(item.priority || '\u2014') + '</div></div>'
      + '<div><div class="workflow-grid-k">Deadline</div><div class="workflow-grid-v mono">' + esc(item.deadline || 'Ch\u01b0a ch\u1ed1t') + '</div></div>'
      + '<div><div class="workflow-grid-k">Status</div><div class="workflow-grid-v">' + renderStatusBadge(item.status || '\u2014', 'task') + '</div></div>'
      + '</div>'
      + '</div>';
  }

  function renderLogEntry(item) {
    return '<div class="workflow-entry log">'
      + '<div class="workflow-entry-grid log-grid">'
      + '<div><div class="workflow-grid-k">S\u1ed5</div><div class="workflow-grid-v clip">' + esc(item.sourceName || 'project-log') + '</div></div>'
      + '<div><div class="workflow-grid-k">N\u1ed9i dung</div><div class="workflow-grid-v">' + esc(item.detail || item.sourceTitle || '\u2014') + '</div></div>'
      + '<div><div class="workflow-grid-k">Raise</div><div class="workflow-grid-v clip">' + esc(item.raisedBy || 'Ch\u01b0a r\u00f5') + '</div></div>'
      + '<div><div class="workflow-grid-k">Tr\u1ea1ng th\u00e1i</div><div class="workflow-grid-v">' + renderStatusBadge(item.status || 'Ghi nh\u1eadn', 'log') + '</div></div>'
      + '</div>'
      + '</div>';
  }

  function renderAnchorCell(group) {
    var flowTitle = esc(group.anchorFlowTitle || 'Ch\u01b0a r\u00f5');
    var flowHtml = group.anchorSourcePath
      ? '<a href="#" class="workflow-source-link" data-doc-source-path="' + esc(group.anchorSourcePath) + '">' + flowTitle + '</a>'
      : flowTitle;
    return '<div class="workflow-timeline-cell">'
      + '<div class="workflow-entry">'
      + '<div class="workflow-entry-grid anchor-grid">'
      + '<div><div class="workflow-grid-k">Ng\u00e0y</div><div class="workflow-grid-v mono">' + esc(group.anchorLabel || 'Ch\u01b0a r\u00f5 ng\u00e0y') + '</div></div>'
      + '<div><div class="workflow-grid-k">K\u00eanh</div><div class="workflow-grid-v">' + esc(group.anchorChannel || 'Ch\u01b0a r\u00f5') + '</div></div>'
      + '<div><div class="workflow-grid-k">Raise</div><div class="workflow-grid-v clip">' + esc(group.anchorRaisedBy || 'Ch\u01b0a r\u00f5') + '</div></div>'
      + '<div><div class="workflow-grid-k">Lu\u1ed3ng</div><div class="workflow-grid-v">' + flowHtml + '</div></div>'
      + '</div>'
      + '</div>'
      + '</div>';
  }

  function renderColumnCell(html, emptyText) {
    return '<div class="workflow-timeline-cell"><div class="workflow-column-stack">'
      + (html || ('<div class="workflow-column-empty">' + esc(emptyText) + '</div>'))
      + '</div></div>';
  }

  function collectTaskStatusOptions(groups) {
    var seen = Object.create(null);
    var options = [];
    groups.forEach(function (group) {
      (Array.isArray(group.tasks) ? group.tasks : []).forEach(function (task) {
        var label = String((task && task.status) || '').trim();
        if (!label || seen[label]) return;
        seen[label] = true;
        options.push(label);
      });
    });
    return options.sort(function (a, b) { return a.localeCompare(b, 'vi'); });
  }

  function collectLogStatusOptions(groups) {
    var seen = Object.create(null);
    var options = [];
    groups.forEach(function (group) {
      (Array.isArray(group.logs) ? group.logs : []).forEach(function (log) {
        var label = String((log && log.status) || '').trim();
        if (!label || seen[label]) return;
        seen[label] = true;
        options.push(label);
      });
    });
    return options.sort(function (a, b) { return a.localeCompare(b, 'vi'); });
  }

  function filterTasksByStatus(tasks, statusFilter) {
    if (!statusFilter) return tasks;
    return tasks.filter(function (task) {
      return normalizeText(task && task.status) === statusFilter;
    });
  }

  function filterLogsByStatus(logs, statusFilter) {
    if (!statusFilter) return logs;
    return logs.filter(function (log) {
      return normalizeText(log && log.status) === statusFilter;
    });
  }

  function renderTimelineGroup(group, taskStatusFilter, logStatusFilter) {
    var logs = filterLogsByStatus(Array.isArray(group.logs) ? group.logs : [], logStatusFilter);
    var tasks = filterTasksByStatus(Array.isArray(group.tasks) ? group.tasks : [], taskStatusFilter);
    var taskHtml = tasks.length ? tasks.map(renderTaskEntry).join('') : '';
    var logHtml = logs.length ? logs.map(renderLogEntry).join('') : '';
    return '<div class="workflow-timeline-row">'
      + renderAnchorCell(group)
      + renderColumnCell(taskHtml, taskStatusFilter ? 'Kh\u00f4ng c\u00f3 task n\u00e0o kh\u1edbp status \u0111ang l\u1ecdc \u1edf m\u1ed1c n\u00e0y.' : 'Ch\u01b0a c\u00f3 task n\u00e0o b\u00e1m theo m\u1ed1c n\u00e0y.')
      + renderColumnCell(logHtml, logStatusFilter ? 'Kh\u00f4ng c\u00f3 event ghi s\u1ed5 n\u00e0o kh\u1edbp status \u0111ang l\u1ecdc \u1edf m\u1ed1c n\u00e0y.' : 'Ch\u01b0a c\u00f3 event n\u00e0o \u0111\u01b0\u1ee3c ghi v\u00e0o s\u1ed5 nh\u1eadt k\u00fd cho m\u1ed1c n\u00e0y.')
      + '</div>';
  }

  function renderWorkflowModal(data, taskStatusFilter, logStatusFilter) {
    var timelineGroups = Array.isArray(data.timelineGroups) ? data.timelineGroups : [];
    var taskStatusOptions = collectTaskStatusOptions(timelineGroups);
    var logStatusOptions = collectLogStatusOptions(timelineGroups);
    var backlogState = (data.tasks || []).some(function (item) { return item.status === 'Blocked'; }) ? 'blocked'
      : ((data.tasks || []).some(function (item) { return normalizeText(item.status) === 'hoan thanh'; }) ? 'done' : '');
    var eventHtml = timelineGroups.length
      ? timelineGroups.map(function (group) { return renderTimelineGroup(group, taskStatusFilter, logStatusFilter); }).join('')
      : '<div class="empty-state"><p>Ch\u01b0a c\u00f3 event g\u1ed1c, task hay log n\u00e0o \u0111\u1ec3 hi\u1ec3n th\u1ecb cho backlog n\u00e0y.</p></div>';
    var taskFilterHtml = taskStatusOptions.length
      ? '<label class="workflow-filter-control"><span>L\u1ecdc status task</span><select class="workflow-filter-select" id="workflowTaskStatusFilter"><option value="">T\u1ea5t c\u1ea3 status</option>'
        + taskStatusOptions.map(function (status) {
            var value = normalizeText(status);
            var selected = value === taskStatusFilter ? ' selected' : '';
            return '<option value="' + esc(value) + '"' + selected + '>' + esc(status) + '</option>';
          }).join('')
        + '</select></label>'
      : '';
    var logFilterHtml = logStatusOptions.length
      ? '<label class="workflow-filter-control"><span>L\u1ecdc status event</span><select class="workflow-filter-select" id="workflowLogStatusFilter"><option value="">T\u1ea5t c\u1ea3 status</option>'
        + logStatusOptions.map(function (status) {
            var value = normalizeText(status);
            var selected = value === logStatusFilter ? ' selected' : '';
            return '<option value="' + esc(value) + '"' + selected + '>' + esc(status) + '</option>';
          }).join('')
        + '</select></label>'
      : '';
    panel.innerHTML = ''
      + '<div class="workflow-modal-head">'
      + '<div><h3>' + esc(data.backlogId || '\u2014') + ' \u00b7 ' + esc(data.title || '') + '</h3>'
      + '<div class="workflow-modal-sub">Timeline l\u1ea5y event g\u1ed1c theo ng\u00e0y l\u00e0m m\u1ed1c. C\u00e1c task v\u00e0 event ghi s\u1ed5 \u0111\u01b0\u1ee3c gom b\u00e1m theo t\u1eebng m\u1ed1c \u0111\u00f3.</div></div>'
      + '<button type="button" class="workflow-modal-close">\u0110\u00f3ng</button>'
      + '</div>'
      + '<div class="workflow-modal-meta">'
      + '<span class="workflow-modal-chip">Combo: ' + esc(data.combo || 'Ch\u01b0a r\u00f5') + '</span>'
      + '<span class="workflow-modal-chip">Plan Golive: ' + esc(data.planGolive || 'Ch\u01b0a ch\u1ed1t') + '</span>'
      + '<span class="workflow-modal-chip">' + esc(String((data.tasks || []).length)) + ' task</span>'
      + '<span class="workflow-modal-chip">' + esc(String((data.logs || []).length)) + ' log</span>'
      + '<span class="workflow-modal-chip">' + esc(String(timelineGroups.length)) + ' m\u1ed1c g\u1ed1c</span>'
      + '</div>'
      + '<div class="workflow-mini-track"><div class="workflow-mini-bar ' + backlogState + '">Event g\u1ed1c \u2192 Task / S\u1ed5</div></div>'
      + '<div class="workflow-timeline-table">'
      + '<div class="workflow-timeline-head">'
      + '<div class="workflow-timeline-cell"><div class="workflow-head-cell"><div class="workflow-head-title">Event g\u1ed1c</div></div></div>'
      + '<div class="workflow-timeline-cell"><div class="workflow-head-cell"><div class="workflow-head-title">Task \u0103n theo</div>' + taskFilterHtml + '</div></div>'
      + '<div class="workflow-timeline-cell"><div class="workflow-head-cell"><div class="workflow-head-title">Event ghi s\u1ed5</div>' + logFilterHtml + '</div></div>'
      + '</div>'
      + eventHtml
      + '</div>';
    var closeBtn = panel.querySelector('.workflow-modal-close');
    if (closeBtn) {
      closeBtn.addEventListener('click', closeWorkflowModal, { once: true });
    }
    var filterEl = panel.querySelector('#workflowTaskStatusFilter');
    if (filterEl) {
      filterEl.addEventListener('change', function () {
        var logFilter = panel.querySelector('#workflowLogStatusFilter');
        renderWorkflowModal(data, filterEl.value || '', logFilter ? (logFilter.value || '') : '');
      });
    }
    var logFilterEl = panel.querySelector('#workflowLogStatusFilter');
    if (logFilterEl) {
      logFilterEl.addEventListener('change', function () {
        var taskFilter = panel.querySelector('#workflowTaskStatusFilter');
        renderWorkflowModal(data, taskFilter ? (taskFilter.value || '') : '', logFilterEl.value || '');
      });
    }
    panel.querySelectorAll('.workflow-source-link[data-doc-source-path]').forEach(function (link) {
      if (link.dataset.wired) return;
      link.dataset.wired = '1';
      link.addEventListener('click', function (event) {
        event.preventDefault();
        var sourcePath = link.getAttribute('data-doc-source-path');
        if (window.__openKnowledgeDocumentDetail && sourcePath) {
          window.__openKnowledgeDocumentDetail(sourcePath);
        }
      });
    });
  }

  function openWorkflowModal(data) {
    renderWorkflowModal(data, '', '');
    modal.classList.add('open');
    document.body.classList.add('workflow-modal-open');
  }

  function closeWorkflowModal() {
    modal.classList.remove('open');
    document.body.classList.remove('workflow-modal-open');
  }

  function openWorkflowPopupFromButton(btn) {
    if (!btn) return;
    var key = btn.getAttribute('data-backlog-key');
    var laneSlug = btn.getAttribute('data-lane-slug');
    var script = Array.prototype.find.call(
      document.querySelectorAll('.workflow-popup-data'),
      function (node) {
        return node.getAttribute('data-backlog-key') === key
          && node.getAttribute('data-lane-slug') === laneSlug;
      }
    );
    if (!script) return;
    try {
      openWorkflowModal(JSON.parse(script.textContent || '{}'));
    } catch (err) {
      console.error('workflow popup parse failed', err);
    }
  }

  window.__openWorkflowPopup = openWorkflowPopupFromButton;

  modal.addEventListener('click', function (event) {
    if (event.target === modal) closeWorkflowModal();
  });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && modal.classList.contains('open')) closeWorkflowModal();
  });
  document.addEventListener('click', function (event) {
    var btn = event.target && event.target.closest ? event.target.closest('.workflow-open-btn') : null;
    if (!btn) return;
    openWorkflowPopupFromButton(btn);
  });
})();

(function () {
  var manageUsersBooted = false;
  function currentApiBase() {
    var input = document.getElementById('syncApiBase');
    return (input && input.value.trim()) || localStorage.getItem('dashboardApiBase') || window.location.origin || 'http://127.0.0.1:8000';
  }
  function seedOwnerKey() {
    var syncInput = document.getElementById('syncOwnerKey');
    var value = (syncInput && syncInput.value.trim()) || localStorage.getItem('dashboardOwnerKey') || localStorage.getItem('graphrag_owner_key') || '';
    if (value) {
      localStorage.setItem('dashboardOwnerKey', value);
      localStorage.setItem('graphrag_owner_key', value);
    }
  }
  function bootManageUsersInline() {
    seedOwnerKey();
    var root = document.getElementById('manageUsersRoot');
    if (!root) return;
    if (manageUsersBooted) return;
    manageUsersBooted = true;
    if (!document.querySelector('script[data-people-inline="1"]')) {
      var script = document.createElement('script');
      script.src = currentApiBase().replace(/\/$/, '') + '/static/people.js?v=20260807b';
      script.dataset.peopleInline = '1';
      script.onerror = function () {
        root.innerHTML = '<div class="manage-error">Không tải được logic quản lý người dùng.</div>';
      };
      document.body.appendChild(script);
    }
  }
  window.__bootManageUsersInline = bootManageUsersInline;
  function escapeHtml(text) {
    return String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  // Key = source_path (rel_path TRONG normalized/ CỦA ĐÚNG TENANT, không tiền tố lane) — khớp
  // đúng giá trị doc.rel_path (loader.py) kể từ khi tách tenants/ (2026-08-22). LƯU Ý: dict này
  // dùng chung cho mọi lane/tenant (không tenant-qualify key) — 2 tenant khác nhau có file trùng
  // tên tương đối (vd cùng 1 giây capture zalo-notes) SẼ đụng key, hiển thị sai kind/title cho 1
  // trong 2 (rủi ro thấp, chỉ ảnh hưởng nhãn hiển thị, không ảnh hưởng dữ liệu task/risk thật).
  // (MSB AI Hackathon 2026 submission: bản gốc có 1 map fallback tay chứa tiêu đề họp/nhóm Zalo
  // thật của MSB tại đây — đã xoá, không dùng cho bản nộp thi. inferSourceInfo() bên dưới vẫn
  // hoạt động đúng nhờ nhánh fallback generic (không có SOURCE_META[path] nào khớp).)
  var SOURCE_META = {};
  function splitSourceText(raw) {
    var text = raw.trim();
    var match = text.match(/([^·]+\.(?:md|html|pdf|pptx|docx))$/i);
    if (!match) return null;
    return { hint: text.slice(0, match.index).replace(/[·:–-]+$/, '').trim(), path: match[1].trim() };
  }
  function inferSourceInfo(path, hint) {
    var meta = SOURCE_META[path];
    if (meta) return { kind: meta.kind || hint || 'Tài liệu', title: meta.title || '', artifact: meta.artifact || '', path: path };
    if (path.indexOf('/_zalo-notes/') !== -1) return { kind: 'Zalo', title: 'Nhóm Zalo: chưa có metadata tên nhóm trong file capture này', artifact: '', path: path };
    if (path.indexOf('/_chat-notes/') !== -1) return { kind: 'Chat-note', title: 'File gốc: chưa có artifact gốc riêng cho chat-note này', artifact: '', path: path };
    return { kind: hint || 'Tài liệu', title: 'File gốc: ' + path.replace(/^normalized\\//, 'artifacts/'), artifact: '', path: path };
  }
  document.querySelectorAll('.task-src').forEach(function (node) {
    var parsed = splitSourceText(node.textContent || '');
    if (!parsed) return;
    var info = inferSourceInfo(parsed.path, parsed.hint);
    node.innerHTML = '<span class="task-src-kind">' + escapeHtml(info.kind) + '</span><span class="task-src-title">' + escapeHtml(info.title) + '</span>' + (info.artifact ? '<span class="task-src-artifact">' + escapeHtml(info.artifact) + '</span>' : '') + '<span class="task-src-path">' + escapeHtml(info.path) + '</span>';
  });
  if (document.getElementById('manage-users') && document.getElementById('manage-users').classList.contains('active')) {
    bootManageUsersInline();
  }
})();

(function () {
  var booted = false;
  var root = null;

  function escapeHtml(text) {
    return String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function apiBase() {
    var origin = window.location && /^https?:/i.test(window.location.origin || '') ? window.location.origin : '';
    var el = document.getElementById('syncApiBase');
    var configured = (el && el.value.trim()) || localStorage.getItem('dashboardApiBase') || '';
    if (origin) return origin.replace(/\/$/, '');
    if (configured) return configured.replace(/\/$/, '');
    return 'http://127.0.0.1:8000';
  }
  function ownerKey() {
    return localStorage.getItem('dashboardOwnerKey') || localStorage.getItem('graphrag_owner_key') || '';
  }
  function formatDate(value) {
    if (!value) return 'n/a';
    var d = new Date(value);
    return isNaN(d.getTime()) ? value : d.toLocaleString('vi-VN');
  }
  function formatSeconds(value) {
    return typeof value === 'number' ? value.toFixed(2) + 's' : 'n/a';
  }
  function ensurePanel() {
    var panel = document.getElementById('system-health');
    if (!panel) {
      var content = document.querySelector('.content');
      if (content) {
        panel = document.createElement('section');
        panel.id = 'system-health';
        panel.className = 'lane-panel';
        panel.innerHTML =
          '<div class="health-shell">' +
          '<div class="health-hero"><div><h2>Health hệ thống</h2><p class="health-intro">Quan sát nhanh độ tươi của kho tri thức, tiến độ normalize và build, cùng các tín hiệu thiếu sót để xử lý sớm ngay trong quá trình làm việc.</p></div><span class="health-badge">Observability</span></div>' +
          '<div id="healthInlineRoot" class="health-native"></div>' +
          '</div>';
        content.appendChild(panel);
      }
    }
    if (!document.querySelector('.side-item[data-target="system-health"]')) {
      var nav = document.querySelector('.sidebar-nav');
      var manageBtn = document.querySelector('.side-item[data-target="manage-users"]');
      if (nav) {
        var btn = document.createElement('button');
        btn.className = 'side-item';
        btn.dataset.target = 'system-health';
        btn.innerHTML = '<span class="side-icon">🩺</span>Health hệ thống';
        if (manageBtn && manageBtn.parentNode === nav) nav.insertBefore(btn, manageBtn);
        else nav.appendChild(btn);
        if (typeof wireSideItem === 'function') wireSideItem(btn);
      }
    }
    root = document.getElementById('healthInlineRoot');
    if (!root || booted) return;
    booted = true;
    root.innerHTML =
      '<div class="health-config">' +
        '<div class="health-field"><label for="healthOwnerKeyInline">OWNER_API_KEY để mở detail nhạy cảm</label><input id="healthOwnerKeyInline" class="health-input" type="password" placeholder="Dán key nếu muốn xem stale/missing chi tiết"></div>' +
        '<div class="health-actions"><button id="healthSaveKeyInline" class="health-btn secondary" type="button">Lưu key</button><button id="healthReloadInline" class="health-btn" type="button">Tải lại</button></div>' +
        '<div id="healthStatusInline" class="health-status">Đang chờ tải dữ liệu health.</div>' +
      '</div>' +
      '<div id="healthMetricsInline" class="health-metrics"></div>' +
      '<div class="health-grid">' +
        '<section class="health-card"><div class="health-eyebrow">Metadata coverage</div><h3>Nền dữ liệu cho retrieval và liên kết tri thức</h3><div id="healthCoverageInline" class="health-coverage-grid" style="margin-top:10px;"></div></section>' +
        '<section class="health-card"><div class="health-eyebrow">Build history</div><h3>Nhịp vận hành gần đây</h3><ul id="healthHistoryInline" class="health-history-list" style="margin-top:10px;"></ul></section>' +
      '</div>' +
      '<section class="health-card">' +
        '<div style="display:flex;justify-content:space-between;gap:12px;align-items:baseline;flex-wrap:wrap;">' +
          '<div><div class="health-eyebrow">Detail stale và missing</div><h3>Cảnh báo thao tác cần xử lý ngay</h3><div class="health-subtle">Summary luôn hiện. File path chi tiết chỉ tải khi có owner key hợp lệ.</div></div>' +
          '<span id="healthModeInline" class="health-chip summary">Summary only</span>' +
        '</div>' +
        '<div id="healthDetailCountsInline" class="health-detail-grid" style="margin-top:12px;"></div>' +
        '<div id="healthDetailTablesInline" class="health-detail-tables" style="margin-top:12px;"></div>' +
      '</section>';
    var keyEl = document.getElementById('healthOwnerKeyInline');
    if (keyEl) keyEl.value = ownerKey();
    document.getElementById('healthSaveKeyInline').addEventListener('click', function () {
      var value = (document.getElementById('healthOwnerKeyInline').value || '').trim();
      localStorage.setItem('dashboardOwnerKey', value);
      localStorage.setItem('graphrag_owner_key', value);
      var syncKey = document.getElementById('syncOwnerKey');
      if (syncKey) syncKey.value = value;
      setStatus('Đã lưu key. Đang tải lại health detail...', 'ok');
      loadHealthInline();
    });
    document.getElementById('healthReloadInline').addEventListener('click', loadHealthInline);
  }
  function setStatus(text, kind) {
    var el = document.getElementById('healthStatusInline');
    if (!el) return;
    el.textContent = text;
    el.className = 'health-status' + (kind ? ' ' + kind : '');
  }
  function renderSummary(summary, generatedAt) {
    var labels = {
      artifact_files: ['Artifact files', 'Số file gốc đang được quản lý'],
      normalized_files: ['Normalized files', 'Số file markdown đã normalize'],
      indexed_docs: ['Indexed docs', 'Số document đang nằm trong vector index'],
      docs_needing_normalization: ['Need normalization', 'Artifact đã đổi nhưng markdown chưa theo kịp'],
      docs_needing_build: ['Need build', 'Markdown đã đổi nhưng index chưa theo kịp'],
      orphaned_normalized_files: ['Orphaned normalized', 'Markdown không còn artifact gốc tương ứng']
    };
    var order = ['artifact_files', 'normalized_files', 'indexed_docs', 'docs_needing_normalization', 'docs_needing_build', 'orphaned_normalized_files'];
    document.getElementById('healthMetricsInline').innerHTML = order.map(function (key) {
      var label = labels[key];
      var value = summary[key] || 0;
      return '<article class="health-card"><div class="health-eyebrow">' + label[0] + '</div><div class="health-big">' + value + '</div><div class="health-subtle">' + label[1] + '</div>' + (key === 'artifact_files' ? '<div class="health-subtle" style="margin-top:8px;">Generated: ' + escapeHtml(formatDate(generatedAt)) + '</div>' : '') + '</article>';
    }).join('');
  }
  function renderCoverage(coverage) {
    var order = ['project', 'source_channel', 'date', 'reliability', 'ticket_ids', 'people_mentions', 'document_type'];
    document.getElementById('healthCoverageInline').innerHTML = order.map(function (key) {
      var item = coverage[key];
      if (!item) return '';
      return '<div class="health-coverage-item"><div class="health-eyebrow">' + escapeHtml(key.replace(/_/g, ' ')) + '</div><div class="health-big" style="font-size:22px;margin:0 0 6px;">' + item.pct + '%</div><div class="health-subtle">' + item.count + '/' + item.total + ' document có field này</div><div class="health-progress"><span style="width:' + item.pct + '%"></span></div></div>';
    }).join('');
  }
  function renderHistory(entries) {
    var list = document.getElementById('healthHistoryInline');
    if (!entries || !entries.length) {
      list.innerHTML = '<li class="health-subtle">Chưa có build history.</li>';
      return;
    }
    list.innerHTML = entries.map(function (entry) {
      return '<li><div style="display:flex;justify-content:space-between;gap:10px;align-items:baseline;flex-wrap:wrap;"><strong>' + escapeHtml(formatDate(entry.time)) + '</strong><span class="health-subtle">' + escapeHtml(formatSeconds(entry.duration_seconds)) + '</span></div><div class="health-subtle">changed docs: ' + (entry.changed_docs || 0) + ', chunks: ' + (entry.n_chunks || 0) + ', provider: ' + escapeHtml(entry.provider || 'n/a') + '</div></li>';
    }).join('');
  }
  function renderDetailCounts(summary, detailData, hasDetail) {
    var stale = detailData && detailData.stale_files ? detailData.stale_files : null;
    var cards = [
      ['Missing normalized', stale ? stale.missing_normalized.length : (summary.docs_needing_normalization || 0), 'Artifact có nhưng chưa có markdown mirror'],
      ['Stale normalized', stale ? stale.stale_normalized.length : 0, 'Artifact mới hơn markdown'],
      ['Stale build', stale ? stale.stale_build.length : (summary.docs_needing_build || 0), 'Markdown đổi nhưng cache build chưa cập nhật'],
      ['Missing indexed', stale ? stale.missing_indexed.length : 0, 'Doc có trong normalized nhưng chưa vào index'],
      ['Orphaned normalized', stale ? stale.orphaned_normalized.length : (summary.orphaned_normalized_files || 0), 'Markdown không còn artifact gốc']
    ];
    document.getElementById('healthDetailCountsInline').innerHTML = cards.map(function (card) {
      return '<div class="health-coverage-item"><div class="health-eyebrow">' + escapeHtml(card[0]) + '</div><div class="health-big" style="font-size:24px;margin:0 0 6px;">' + card[1] + '</div><div class="health-subtle">' + escapeHtml(card[2]) + '</div></div>';
    }).join('');
    var mode = document.getElementById('healthModeInline');
    mode.textContent = hasDetail ? 'Owner detail' : 'Summary only';
    mode.className = 'health-chip ' + (hasDetail ? 'owner' : 'summary');
  }
  function summarizeItem(kind, item) {
    if (kind === 'missing_normalized') return '<span class="health-code">' + escapeHtml(item.artifact_path) + '</span><div class="health-subtle">expected: ' + escapeHtml(item.expected_normalized_path) + '</div>';
    if (kind === 'stale_normalized') return '<span class="health-code">' + escapeHtml(item.artifact_path) + '</span><div class="health-subtle">normalized: ' + escapeHtml(item.normalized_path) + '</div>';
    if (kind === 'stale_build' || kind === 'missing_indexed') return '<span class="health-code">' + escapeHtml(item.normalized_path) + '</span><div class="health-subtle">doc_id: ' + escapeHtml(item.doc_id) + '</div>';
    if (kind === 'orphaned_normalized') return '<span class="health-code">' + escapeHtml(item.normalized_path) + '</span><div class="health-subtle">missing artifact: ' + escapeHtml(item.missing_artifact_path) + '</div>';
    return '<span class="health-code">' + escapeHtml(JSON.stringify(item)) + '</span>';
  }
  function renderDetailTables(detailData, hasDetail) {
    var holder = document.getElementById('healthDetailTablesInline');
    if (!hasDetail) {
      holder.innerHTML = '<div class="health-card"><div class="health-subtle">Đang ở chế độ summary-only. Nhập OWNER_API_KEY để xem chi tiết file/path đang stale hoặc missing.</div></div>';
      return;
    }
    var sections = [
      ['missing_normalized', 'Missing normalized'],
      ['stale_normalized', 'Stale normalized'],
      ['stale_build', 'Stale build'],
      ['missing_indexed', 'Missing indexed'],
      ['orphaned_normalized', 'Orphaned normalized']
    ];
    holder.innerHTML = sections.map(function (section) {
      var key = section[0];
      var title = section[1];
      var items = (detailData.stale_files && detailData.stale_files[key]) || [];
      var rows = items.length ? items.map(function (item, index) {
        return '<tr><td>' + (index + 1) + '</td><td>' + summarizeItem(key, item) + '</td></tr>';
      }).join('') : '<tr><td colspan="2"><div class="health-subtle">Không có mục nào trong nhóm này.</div></td></tr>';
      return '<section class="health-card"><div style="display:flex;justify-content:space-between;gap:12px;align-items:baseline;flex-wrap:wrap;"><div class="health-eyebrow">' + escapeHtml(title) + '</div><span class="health-subtle">' + items.length + ' items</span></div><div class="health-table-wrap"><table class="health-table"><thead><tr><th>#</th><th>Item</th></tr></thead><tbody>' + rows + '</tbody></table></div></section>';
    }).join('');
  }
  function applyDetail(summaryData, detailData, hasDetail) {
    renderCoverage((detailData && detailData.metadata_coverage) || {});
    renderHistory((detailData && detailData.build_history) || []);
    renderDetailCounts(summaryData.summary || {}, detailData, hasDetail);
    renderDetailTables(detailData, hasDetail);
  }
  function loadHealthInline() {
    ensurePanel();
    setStatus('Đang tải health summary...', '');
    fetch(apiBase() + '/health')
      .then(function (res) { return res.json().then(function (data) { return { ok: res.ok, data: data }; }); })
      .then(function (result) {
        if (!result.ok) throw new Error(result.data.detail || 'Không mở được /health');
        renderSummary(result.data.summary || {}, result.data.generated_at);
        if (!ownerKey()) {
          applyDetail(result.data, null, false);
          setStatus('Đã tải summary. Muốn xem detail file/path thì thêm OWNER_API_KEY.', 'ok');
          return;
        }
        return fetch(apiBase() + '/health/details', { headers: { 'X-API-Key': ownerKey() } })
          .then(function (res) { return res.json().then(function (data) { return { ok: res.ok, data: data }; }); })
          .then(function (detailResult) {
            if (!detailResult.ok) throw new Error(detailResult.data.detail || 'Không mở được health detail');
            applyDetail(detailResult.data, detailResult.data, true);
            setStatus('Đã tải health detail bằng OWNER_API_KEY.', 'ok');
          })
          .catch(function (err) {
            applyDetail(result.data, null, false);
            setStatus('Summary đã tải, nhưng detail không mở được: ' + err.message, 'err');
          });
      })
      .catch(function (err) {
        setStatus('Không tải được health: ' + err.message, 'err');
      });
  }
  window.__loadHealthInline = loadHealthInline;
  ensurePanel();
  if (document.getElementById('system-health') && document.getElementById('system-health').classList.contains('active')) {
    loadHealthInline();
  }
})();

(function () {
  // Tab con "Tài liệu" / "Nhật ký dự án" / "Health" trong panel 1 workspace (Phase 6, 2026-08-25
  // — sidebar đi theo cây org/workspace, xem _sidebar_and_panels). Lazy-load khi tab được click
  // lần đầu (giống pattern loadHealthInline phía trên) — data-tenant-docs/-logs/-health đánh dấu
  // sẵn trong HTML tĩnh, gọi đúng /tenant-documents, /tenant-project-logs, /health?tenant=...
  // (đều owner-only trừ /health tóm tắt, xem api.py) qua OWNER_API_KEY dùng CHUNG khối "Cấu hình
  // server" — không có ô nhập key riêng cho panel này.
  function apiBase() {
    var origin = window.location && /^https?:/i.test(window.location.origin || '') ? window.location.origin : '';
    var el = document.getElementById('syncApiBase');
    var configured = (el && el.value.trim()) || localStorage.getItem('dashboardApiBase') || '';
    if (origin) return origin.replace(/\/$/, '');
    if (configured) return configured.replace(/\/$/, '');
    return 'http://127.0.0.1:8000';
  }
  function ownerKey() {
    return localStorage.getItem('dashboardOwnerKey') || localStorage.getItem('graphrag_owner_key') || '';
  }
  function escapeHtml(text) {
    return String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function ownerGate(sub) {
    if (ownerKey()) return true;
    sub.innerHTML = '<div class="empty-state"><div class="empty-state-icon">🔒</div><p>Dùng OWNER_API_KEY ở "Cấu hình server" (góc trên) để tải phần này.</p></div>';
    return false;
  }
  function fetchOwner(path) {
    return fetch(apiBase() + path, { headers: { 'X-API-Key': ownerKey() } })
      .then(function (res) { return res.json().then(function (data) { return { ok: res.ok, data: data }; }); })
      .then(function (result) {
        if (!result.ok) throw new Error(result.data.detail || ('Không mở được ' + path));
        return result.data;
      });
  }
  function openLogPreview(tenantId, sourcePath, detailEl) {
    if (!detailEl || !sourcePath) return;
    detailEl.innerHTML = '<div class="card" style="padding:12px;">Đang tải...</div>';
    fetchOwner('/knowledge-links/document?tenant=' + encodeURIComponent(tenantId) + '&source_path=' + encodeURIComponent(sourcePath))
      .then(function (doc) {
        detailEl.innerHTML = '<div class="card" style="padding:12px;"><pre style="white-space:pre-wrap;font-size:11.5px;margin:0;">' + escapeHtml(doc.preview || '(không có nội dung)') + '</pre></div>';
      })
      .catch(function (err) {
        detailEl.innerHTML = '<div class="empty-state"><p>Không tải được: ' + escapeHtml(err.message) + '</p></div>';
      });
  }
  // Nút "Xem file gốc" trong 1 thẻ chi tiết sự kiện (timeline ngang hoặc dọc) — bấm sẽ mở preview
  // ngay trong ô .log-detail DÙNG CHUNG của panel (tìm qua .ws-sub-panel chứa `anyElInPanel`),
  // tenantId đọc từ data-tenant-id đã gắn sẵn trên sub-panel (renderLogs) — không cần truyền tham
  // số qua nhiều lớp hàm (thêm 2026-08-26, theo yêu cầu người dùng: chi tiết hơn cho danh sách file).
  function wireDetailOpenButtons(detailEl, anyElInPanel) {
    var sub = anyElInPanel.closest('.ws-sub-panel');
    if (!sub) return;
    var previewEl = sub.querySelector('.log-detail');
    var tenantId = sub.dataset.tenantId;
    detailEl.querySelectorAll('[data-open-log-path]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        openLogPreview(tenantId, btn.dataset.openLogPath, previewEl);
        if (previewEl && previewEl.scrollIntoView) previewEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      });
    });
  }
  function renderDocTree(sub, data) {
    var folders = (data.folders || []).map(function (f) {
      var pill = f.has_project_logs ? '<span class="logs-pill">có nhật ký dự án</span>' : '';
      return '<div class="file-node folder"><span class="f-icon">📁</span><span class="f-name">' + escapeHtml(f.name) + '/</span>' + pill + '<span class="f-meta">' + f.file_count + ' file</span></div>';
    }).join('');
    var files = (data.files || []).map(function (f) {
      return '<div class="file-node"><span class="f-icon">📄</span><span class="f-name">' + escapeHtml(f.name) + '</span></div>';
    }).join('');
    if (!folders && !files) {
      sub.innerHTML = '<div class="empty-state"><div class="empty-state-icon">📄</div><p>Chưa có tài liệu nào.</p></div>';
      return;
    }
    sub.innerHTML = '<div class="card file-tree">' + folders + files + '</div>';
  }
  var TL_LANE_COLOR = { alerts: '--status-critical', decision: '--cat-1', raid: '--status-warning', milestone: '--cat-7', adr: '--cat-3', retro: '--cat-5' };
  function tlPct(date, start, end) {
    var total = end - start;
    if (total <= 0) return 0;
    return Math.max(0, Math.min(100, ((date - start) / total) * 100));
  }
  function tlMonthLabel(date) { return 'Th' + (date.getMonth() + 1) + '/' + date.getFullYear(); }
  function tlFmtDate(date) {
    var dd = String(date.getDate()).padStart(2, '0');
    var mm = String(date.getMonth() + 1).padStart(2, '0');
    var hh = date.getHours(), mi = date.getMinutes();
    var timePart = (hh || mi) ? ' ' + String(hh).padStart(2, '0') + ':' + String(mi).padStart(2, '0') : '';
    return dd + '/' + mm + '/' + date.getFullYear() + timePart;
  }
  function tlGroupByDay(events) {
    var buckets = {}; var order = [];
    events.forEach(function (ev) {
      var key = ev.date.getFullYear() + '-' + ev.date.getMonth() + '-' + ev.date.getDate();
      if (!buckets[key]) { buckets[key] = []; order.push(key); }
      buckets[key].push(ev);
    });
    return order.map(function (k) { return buckets[k]; });
  }
  function renderTimelineDetail(detailEl, lane, group, backfillDate) {
    detailEl.innerHTML = group.map(function (ev) {
      var badge = ev.sev === 'cao' ? '<span class="tl-badge cao">CAO</span>' : '';
      var bfBadge = backfillDate ? (ev.date < backfillDate ? '<span class="tl-badge backfill">backfill</span>' : '<span class="tl-badge live">gần-thời-gian-thực</span>') : '';
      var agentTag = ev.agent ? '<span>' + escapeHtml(ev.agent) + '</span>' : '';
      var openBtn = ev.path ? '<button type="button" class="tl-open-file-btn" data-open-log-path="' + escapeHtml(ev.path) + '">📄 Xem file gốc</button>' : '';
      return '<div class="tl-detail-card">' +
        '<div class="tl-detail-icon">' + lane.icon + '</div>' +
        '<div class="tl-detail-body">' +
        '<div class="tl-detail-meta"><span>' + tlFmtDate(ev.date) + '</span>' + agentTag + badge + bfBadge + '</div>' +
        '<div class="tl-detail-title">' + escapeHtml(ev.title) + '</div>' +
        '<div class="tl-detail-text">' + escapeHtml(ev.text) + '</div>' +
        openBtn +
        '</div></div>';
    }).join('');
  }
  function renderTimeline(container, data) {
    var lanes = (data.lanes || []).map(function (lane) {
      return {
        id: lane.id, name: lane.name, icon: lane.icon, agent: lane.agent,
        events: (lane.events || []).map(function (ev) {
          return { date: new Date(ev.date), title: ev.title, text: ev.text, sev: ev.sev, agent: ev.agent, path: ev.path || null };
        })
      };
    });
    var allDates = [];
    lanes.forEach(function (l) { l.events.forEach(function (e) { allDates.push(e.date); }); });
    if (!allDates.length) {
      container.innerHTML = '<div class="empty-state"><p>Chưa đủ dữ liệu ngày tháng để dựng timeline.</p></div>';
      return;
    }
    var minD = new Date(Math.min.apply(null, allDates));
    var maxD = new Date(Math.max.apply(null, allDates));
    var axisStart = new Date(minD.getFullYear(), minD.getMonth(), 1);
    var axisEnd = new Date(maxD.getFullYear(), maxD.getMonth() + 1, 1);
    var backfillDate = data.backfill_date ? new Date(data.backfill_date + 'T00:00:00') : null;

    var legend = lanes.map(function (l) {
      return '<div class="tl-legend-item"><span class="tl-legend-dot" style="background:var(' + (TL_LANE_COLOR[l.id] || '--cat-1') + ');"></span>' + escapeHtml(l.name) + '</div>';
    }).join('');

    var monthParts = [];
    var cur = new Date(axisStart.getFullYear(), axisStart.getMonth(), 1);
    while (cur < axisEnd) {
      var leftPct = tlPct(cur < axisStart ? axisStart : cur, axisStart, axisEnd);
      monthParts.push('<div class="tl-month" style="left:' + leftPct + '%;">' + tlMonthLabel(cur) + '</div>');
      cur = new Date(cur.getFullYear(), cur.getMonth() + 1, 1);
    }

    var laneHtml = lanes.map(function (lane) {
      var groups = tlGroupByDay(lane.events);
      var markers = groups.map(function (group, gi) {
        var ev = group[0];
        var left = tlPct(ev.date, axisStart, axisEnd);
        var sevCls = group.some(function (e) { return e.sev === 'cao'; }) ? 'sev-cao' : 'sev-normal';
        var countBadge = group.length > 1 ? '<span class="tl-marker-count">' + group.length + '</span>' : '';
        return '<div class="tl-marker lane-' + lane.id + ' ' + sevCls + '" style="left:' + left + '%;" data-lane="' + lane.id + '" data-group="' + gi + '" title="' + group.length + ' sự kiện">' + countBadge + '</div>';
      }).join('');
      var iconVar = TL_LANE_COLOR[lane.id] || '--cat-1';
      return '<div class="tl-lane" data-lane-id="' + lane.id + '">' +
        '<div class="tl-lane-label"><div class="tl-lane-icon" style="background:color-mix(in srgb, var(' + iconVar + ') 18%, transparent);">' + lane.icon + '</div><div><div class="tl-lane-name">' + escapeHtml(lane.name) + '</div><div class="tl-lane-agent">' + escapeHtml(lane.agent) + '</div></div></div>' +
        '<div class="tl-track">' + markers + '</div>' +
        '</div>';
    }).join('');

    var detailId = 'tlDetail-' + Math.random().toString(36).slice(2);
    container.innerHTML =
      '<div class="tl-legend">' + legend + '</div>' +
      '<div class="card" style="padding:16px 18px 18px;">' +
      '<div class="tl-scroll"><div class="tl-grid"><div class="tl-axis">' + monthParts.join('') + '</div><div>' + laneHtml + '</div></div></div>' +
      '<div class="tl-detail" id="' + detailId + '"><div class="empty-state" style="padding:20px;"><p>👆 Bấm 1 chấm trên timeline để xem chi tiết sự kiện đó.</p></div></div>' +
      '</div>';

    var detailEl = document.getElementById(detailId);
    container.querySelectorAll('.tl-marker').forEach(function (m) {
      m.addEventListener('click', function () {
        container.querySelectorAll('.tl-marker.selected').forEach(function (s) { s.classList.remove('selected'); });
        m.classList.add('selected');
        var lane = lanes.find(function (l) { return l.id === m.dataset.lane; });
        var groups = tlGroupByDay(lane.events);
        var group = groups[parseInt(m.dataset.group, 10)];
        renderTimelineDetail(detailEl, lane, group, backfillDate);
        wireDetailOpenButtons(detailEl, container);
      });
    });
  }
  // Timeline dọc theo cột (1 cột = 1 sổ nhật ký: Alerts/Decision/RAID/Milestone/ADR/Retro), thay
  // cho danh sách file phẳng cũ (.log-list) — cùng nguồn dữ liệu lanes/events với renderTimeline()
  // ở trên (không gọi API riêng), chỉ đổi trục left->top, hàng->cột (thêm 2026-08-26, theo yêu cầu
  // người dùng: "chi tiết hơn, chia thành các cột mỗi nội dung file là 1 cột bám theo timeline").
  // Trả về danh sách source_path đã "phủ" được bởi timeline, để renderLeftoverLogs() biết file nào
  // (vd raci-daci-register.md, change-cr-log.md, hoặc 1 ADR chưa có event_date) cần hiện riêng.
  function renderFileTimeline(container, data, tenantId) {
    var lanes = (data.lanes || []).map(function (lane) {
      return {
        id: lane.id, name: lane.name, icon: lane.icon, agent: lane.agent,
        events: (lane.events || []).map(function (ev) {
          return { date: new Date(ev.date), title: ev.title, text: ev.text, sev: ev.sev, agent: ev.agent, path: ev.path || null };
        })
      };
    }).filter(function (l) { return l.events.length > 0; });
    if (!lanes.length) {
      container.innerHTML = '<div class="empty-state"><p>Chưa có sổ nào đủ ngày tháng để dựng timeline dọc.</p></div>';
      return [];
    }
    var allDates = [];
    lanes.forEach(function (l) { l.events.forEach(function (e) { allDates.push(e.date); }); });
    var minD = new Date(Math.min.apply(null, allDates));
    var maxD = new Date(Math.max.apply(null, allDates));
    var axisStart = new Date(minD.getFullYear(), minD.getMonth(), 1);
    var axisEnd = new Date(maxD.getFullYear(), maxD.getMonth() + 1, 1);
    var backfillDate = data.backfill_date ? new Date(data.backfill_date + 'T00:00:00') : null;
    var monthSpan = Math.max(1, (axisEnd.getFullYear() * 12 + axisEnd.getMonth()) - (axisStart.getFullYear() * 12 + axisStart.getMonth()));
    var VTL_ROW_H = 30; // px mỗi DÒNG sự kiện — đủ cao cho nhãn wrap 2 dòng (bớt cắt chữ, phản hồi "bị lấp chữ")
    var VTL_BLOCK_GAP = 30; // px khoảng hở tối thiểu giữa 2 mốc (block) liền kề — nới rộng thêm theo phản hồi người dùng ("cần khoảng cách rộng hơn nữa", 2026-08-26)
    var VTL_BLOCK_PAD = 4; // px padding TRÊN+DƯỚI của .vtl-marker-block (2px+2px, xem CSS) — phải cộng vào tổng chiều cao THẬT của block, nếu không thuật toán tránh chồng sẽ ước lượng THIẾU và vẫn chồng (phát hiện qua phản hồi người dùng 2026-08-26: "các khối event đang bị lồng vào nhau")
    function blockHeight(group) { return Math.max(1, group.length) * VTL_ROW_H + VTL_BLOCK_PAD; }
    var allGroupsByLane = lanes.map(function (l) {
      var groups = tlGroupByDay(l.events);
      groups.sort(function (a, b) { return a[0].date - b[0].date; });
      return groups;
    });
    // THIẾT KẾ LẠI 2026-08-27 theo phản hồi người dùng: "các dòng text ở các cột Alerts, Decision
    // Log, RAID Log... cùng giống với mốc ở cột duy nhất timeline này" — tức MỖI NGÀY phải là 1
    // HÀNG DÙNG CHUNG trên toàn bộ 6 cột + trục chung bên trái, không phải mỗi cột tự định vị theo
    // dữ liệu riêng của nó (khiến cùng 1 ngày lại nằm ở độ cao khác nhau giữa các cột). Gộp TẤT CẢ
    // ngày có sự kiện (không phân biệt sổ) thành 1 danh sách "rows" duy nhất, MỖI row giữ tối đa 1
    // group cho MỖI lane (null nếu lane đó không có sự kiện ngày này); chiều cao của row = MAX
    // chiều cao cần thiết trong số các lane có dữ liệu ngày đó (lane nào ngắn hơn thì chừa khoảng
    // trống bên dưới trong đúng ô của nó, không đẩy lệch sang hàng khác). Trục chung + cả 6 cột đều
    // render theo CÙNG 1 mảng rowTops này — đảm bảo luôn thẳng hàng.
    var rowMap = {};
    var rowOrder = [];
    allGroupsByLane.forEach(function (groups, li) {
      groups.forEach(function (g) {
        var d = g[0].date;
        var key = d.getFullYear() + '-' + d.getMonth() + '-' + d.getDate();
        if (!rowMap[key]) {
          rowMap[key] = { date: new Date(d.getFullYear(), d.getMonth(), d.getDate()), perLane: new Array(lanes.length).fill(null) };
          rowOrder.push(key);
        }
        rowMap[key].perLane[li] = g;
      });
    });
    var AXIS_DATE_ROW_H = 15; // px — chiều cao SÀN của 1 row khi không lane nào có sự kiện cao hơn (vd chỉ có 1 sự kiện 1 dòng ở lane khác)
    var rows = rowOrder.map(function (k) { return rowMap[k]; }).sort(function (a, b) { return a.date - b.date; });
    function rowHeight(row) {
      return row.perLane.reduce(function (m, g) { return g ? Math.max(m, blockHeight(g)) : m; }, AXIS_DATE_ROW_H);
    }
    // "natural" (vị trí lý tưởng theo % ngày) tính theo 1 THANG CỐ ĐỊNH hoàn toàn độc lập với
    // trackHeight (NATURAL_SCALE) — rồi mới suy trackHeight = chiều cao THẬT SỰ cần dùng sau khi đã
    // xếp xong toàn bộ rows, tránh vòng lặp phụ thuộc ngược đã từng gây lỗi "2 khối lồng vào nhau"
    // (rà lại 2026-08-26 — xem lịch sử sửa, không lặp lại lỗi cũ dù đổi sang model rows chung).
    var NATURAL_SCALE = Math.max(1, monthSpan * 90);
    function layoutRows(rows) {
      var lastBottom = -Infinity;
      return rows.map(function (row) {
        var natural = (tlPct(row.date, axisStart, axisEnd) / 100) * NATURAL_SCALE;
        var top = Math.max(natural, lastBottom);
        lastBottom = top + rowHeight(row) + VTL_BLOCK_GAP;
        return top;
      });
    }
    var rowTops = layoutRows(rows);
    var trackHeight = rows.reduce(function (m, row, i) { return Math.max(m, rowTops[i] + rowHeight(row)); }, 460) + 20;
    // Timeline dọc này chạy NGƯỢC chiều thời gian so với chiều % gốc của tlPct() (0%=sớm nhất,
    // 100%=muộn nhất, dùng chung với timeline ngang phía trên) — theo yêu cầu người dùng: dưới lên
    // trên phải là thời gian TĂNG dần (mốc sớm nhất nằm DƯỚI CÙNG, mốc muộn nhất nằm TRÊN CÙNG).
    // Lật 1 lần duy nhất lúc render (flipPct/flipTop) sau khi trackHeight đã CHỐT — không sửa lại
    // thuật toán va chạm cho phức tạp thêm (thêm 2026-08-26).
    function flipPct(pct) { return 100 - pct; }
    function flipTop(top, height) { return trackHeight - top - height; }

    // Vạch ngang PHỦ HẾT các cột (không chỉ nằm gọn trong dải trục bên trái) để có thể "gióng sang"
    // xem 1 hàng chấm rơi vào tháng nào khi nhìn các cột xa bên phải (phản hồi người dùng
    // 2026-08-26: "không nhìn thấy mốc của các event gióng sang trên timeline"). Không còn nhãn chữ
    // "TH7/2026" trong trục nữa — đã thay bằng mốc ngày chính xác "dd/mm/yyyy" (axisDateLabelsHtml,
    // theo yêu cầu người dùng 2026-08-27) nên nhãn tháng thô giờ chỉ còn giữ lại phần vạch kẻ.
    var monthLines = [];
    var cur = new Date(axisStart.getFullYear(), axisStart.getMonth(), 1);
    while (cur < axisEnd) {
      monthLines.push('<div class="vtl-month-line" style="top:' + flipPct(tlPct(cur, axisStart, axisEnd)) + '%;"></div>');
      cur = new Date(cur.getFullYear(), cur.getMonth() + 1, 1);
    }

    // Hiện thẳng NGÀY dạng "dd/mm/yyyy" cho từng row DÙNG CHUNG trên trục bên trái — theo yêu cầu
    // người dùng 2026-08-27 (đã thử chấm màu theo lane trước đó nhưng bị từ chối "không đúng, ý
    // tôi là hiển thị luôn các mốc dạng 12/08/2026, 14/08/2026..."; rồi lại yêu cầu tiếp "các dòng
    // text ở các cột... cùng giống với mốc ở cột duy nhất timeline này" nên đổi sang model rows
    // dùng chung — xem comment ở rowMap/layoutRows phía trên). Nhãn ngày TOP-ALIGN ở mép trên của
    // đúng row đó — CÙNG top với block của mọi lane có sự kiện ngày này (đối chiếu trực tiếp qua
    // getBoundingClientRect() khi kiểm thử, không chỉ so style top).
    var axisDateLabelsHtml = rows.map(function (row, ri) {
      var top = flipTop(rowTops[ri], rowHeight(row));
      return '<div class="vtl-axis-date" style="top:' + top.toFixed(0) + 'px;">' + tlFmtDate(row.date) + '</div>';
    }).join('');

    var headHtml = lanes.map(function (lane) {
      var colorVar = TL_LANE_COLOR[lane.id] || '--cat-1';
      return '<div class="vtl-col-head" style="border-top-color:var(' + colorVar + ');">' +
        '<span class="vtl-col-icon" style="background:color-mix(in srgb, var(' + colorVar + ') 18%, transparent);">' + lane.icon + '</span>' +
        '<span>' + escapeHtml(lane.name) + '</span><span class="vtl-col-count">' + lane.events.length + '</span></div>';
    }).join('');

    var colsHtml = lanes.map(function (lane, li) {
      var colorVar = TL_LANE_COLOR[lane.id] || '--cat-1';
      // Duyệt qua CÙNG mảng "rows" dùng chung (không phải allGroupsByLane[li] riêng của lane) —
      // đây chính là điểm mấu chốt để mỗi lane render đúng vào ô của ngày tương ứng, thẳng hàng
      // với nhãn ngày trên trục và với mọi lane khác cùng có sự kiện ngày đó (2026-08-27).
      var markers = rows.map(function (row, ri) {
        var group = row.perLane[li];
        if (!group) return '';
        var tip = tlFmtDate(group[0].date) + ' — ' + group.length + ' sự kiện';
        // Mỗi sự kiện trong mốc gộp (cùng ngày) tách hẳn ra 1 dòng riêng, không rút về 1 dòng +
        // số đếm nữa — theo yêu cầu người dùng 2026-08-26 ("bao nhiêu sự kiện thì break bấy nhiêu
        // dòng"). Màu chấm lấy inline theo severity/lane thay vì class CSS để khỏi phụ thuộc thứ
        // tự specificity giữa quy tắc theo lane và theo severity.
        var rowsHtml = group.map(function (ev) {
          var dotColor = ev.sev === 'cao' ? 'var(--status-critical)' : 'var(' + colorVar + ')';
          return '<div class="vtl-marker-row"><span class="vtl-marker-dot" style="background:' + dotColor + ';"></span>' +
            '<span class="vtl-marker-label">' + escapeHtml(ev.title) + '</span></div>';
        }).join('');
        // top-align trong đúng slot của row (dùng rowHeight(row), KHÔNG dùng blockHeight(group) —
        // nếu lane khác cùng row có block cao hơn thì slot của row phải đủ cao cho lane đó, còn
        // block của lane này chỉ chiếm phần trên của slot, chừa trống bên dưới trong đúng ô mình).
        var renderTop = flipTop(rowTops[ri], rowHeight(row));
        return '<div class="vtl-marker-block" style="top:' + renderTop.toFixed(0) + 'px;" data-lane="' + lane.id + '" data-row="' + ri + '" title="' + escapeHtml(tip) + '">' + rowsHtml + '</div>';
      }).join('');
      return '<div class="vtl-col" data-lane-id="' + lane.id + '">' + markers + '</div>';
    }).join('');

    var detailId = 'vtlDetail-' + Math.random().toString(36).slice(2);
    container.innerHTML =
      '<div class="vtl-grid">' +
        '<div class="vtl-head-row"><div class="vtl-axis-spacer"></div>' + headHtml + '</div>' +
        '<div class="vtl-body" style="height:' + trackHeight + 'px;">' +
          monthLines.join('') +
          '<div class="vtl-axis">' + axisDateLabelsHtml + '</div>' + colsHtml +
        '</div>' +
      '</div>' +
      '<div class="tl-detail" id="' + detailId + '"><div class="empty-state" style="padding:20px;"><p>👆 Bấm 1 chấm trên timeline dọc để xem chi tiết.</p></div></div>';

    var detailEl = document.getElementById(detailId);
    // data-lane/data-row tra vào "rows" dùng chung (mảng render ra cả trục lẫn 6 cột) — data-row là
    // chỉ số vào rows, không phải chỉ số riêng của từng lane nữa (đổi cùng lúc với việc gộp layout).
    container.querySelectorAll('.vtl-marker-block').forEach(function (m) {
      m.addEventListener('click', function () {
        container.querySelectorAll('.vtl-marker-block.selected').forEach(function (s) { s.classList.remove('selected'); });
        m.classList.add('selected');
        var laneId = m.dataset.lane;
        var lane = lanes.find(function (l) { return l.id === laneId; });
        var group = rows[parseInt(m.dataset.row, 10)].perLane[lanes.indexOf(lane)];
        renderTimelineDetail(detailEl, lane, group, backfillDate);
        wireDetailOpenButtons(detailEl, container);
      });
    });

    var coveredPaths = [];
    lanes.forEach(function (l) { l.events.forEach(function (e) { if (e.path) coveredPaths.push(e.path); }); });
    return coveredPaths;
  }
  // Danh sách gọn các file KHÔNG lên được timeline dọc (raci-daci-register.md, change-cr-log.md —
  // nội dung văn xuôi, không có cột ngày sạch để trích; hoặc 1 ADR chưa backfill event_date) — giữ
  // lại để không mất quyền truy cập trực tiếp, nhưng không còn là khối chính của tab (thêm 2026-08-26).
  function renderLeftoverLogs(container, allLogs, coveredPaths, sub) {
    var coveredSet = {};
    (coveredPaths || []).forEach(function (p) { coveredSet[p] = true; });
    var leftover = (allLogs || []).filter(function (log) { return !coveredSet[log.path]; });
    if (!leftover.length) {
      container.className = '';
      container.innerHTML = '';
      return;
    }
    container.className = 'card log-list';
    var rows = leftover.map(function (log) {
      var alertCls = log.file === 'ALERTS.md' ? ' alert-row' : '';
      return '<div class="log-row' + alertCls + '" data-log-path="' + escapeHtml(log.path) + '"><div class="log-icon">' + log.icon + '</div><div class="log-body"><div class="log-title">' + escapeHtml(log.title) + '</div><div class="log-desc">' + escapeHtml(log.desc) + '</div></div></div>';
    }).join('');
    container.innerHTML = '<div class="log-list-caption">📄 Tài liệu khác (chưa đủ mốc ngày để lên timeline)</div>' + rows;
    container.querySelectorAll('[data-log-path]').forEach(function (row) {
      row.addEventListener('click', function () {
        openLogPreview(sub.dataset.tenantId, row.dataset.logPath, sub.querySelector('.log-detail'));
      });
    });
  }
  function loadCombinedTimeline(sub, tenantId, ticket, allLogs) {
    var horizEl = document.getElementById('tlWrap-' + sub.id);
    var vertEl = document.getElementById('vtlWrap-' + sub.id);
    var leftoverEl = document.getElementById('logLeftover-' + sub.id);
    horizEl.innerHTML = '<div class="empty-state"><p>Đang tải timeline...</p></div>';
    vertEl.innerHTML = '<div class="empty-state"><p>Đang tải timeline dọc...</p></div>';
    fetchOwner('/tenant-project-logs-timeline?tenant=' + encodeURIComponent(tenantId) + '&ticket=' + encodeURIComponent(ticket))
      .then(function (tlData) {
        renderTimeline(horizEl, tlData);
        var coveredPaths = renderFileTimeline(vertEl, tlData, tenantId);
        renderLeftoverLogs(leftoverEl, allLogs, coveredPaths, sub);
      })
      .catch(function (err) {
        var msg = '<div class="empty-state"><p>Không tải được timeline: ' + escapeHtml(err.message) + '</p></div>';
        horizEl.innerHTML = msg;
        vertEl.innerHTML = msg;
        renderLeftoverLogs(leftoverEl, allLogs, [], sub);
      });
  }
  function renderLogs(sub, data, tenantId) {
    if (!data.ticket && (!data.tickets || !data.tickets.length)) {
      sub.innerHTML = '<div class="empty-state"><div class="empty-state-icon">📋</div><p>Workspace này chưa có dự án/ticket nào mở tầng nhật ký (_project-logs/).</p></div>';
      return;
    }
    if (!data.ticket) {
      // Nhieu ticket co _project-logs/ - cho chon truoc khi hien danh sach so.
      var items = data.tickets.map(function (t) {
        return '<button class="side-item" style="width:100%;" data-pick-ticket="' + escapeHtml(t) + '"><span class="side-icon">📁</span>' + escapeHtml(t) + '</button>';
      }).join('');
      sub.innerHTML = '<div class="info-strip">📌 Chọn 1 ticket để xem 6 sổ nhật ký + ALERTS.</div><div class="card" style="padding:6px;">' + items + '</div>';
      sub.querySelectorAll('[data-pick-ticket]').forEach(function (btn) {
        btn.addEventListener('click', function () {
          sub.dataset.loaded = '';
          loadLogsFor(sub, tenantId, btn.dataset.pickTicket);
        });
      });
      return;
    }
    if (!data.logs || !data.logs.length) {
      sub.innerHTML = '<div class="empty-state"><p>Chưa có file nào trong _project-logs/.</p></div>';
      return;
    }
    var switchBtn = (data.tickets && data.tickets.length > 1)
      ? '<button class="ws-tab-btn" style="border-bottom:none;padding:0 0 8px;" data-back-to-tickets="1">← Đổi ticket khác (' + data.tickets.length + ')</button>'
      : '';
    sub.dataset.tenantId = tenantId;
    sub.innerHTML = switchBtn + '<div class="info-strip">📌 Ticket <strong>' + escapeHtml(data.ticket) + '</strong></div>' +
      '<div class="tl-wrap" id="tlWrap-' + sub.id + '"></div>' +
      '<div class="vtl-wrap" id="vtlWrap-' + sub.id + '"></div>' +
      '<div id="logLeftover-' + sub.id + '"></div>' +
      '<div id="logDetail-' + sub.id + '" class="log-detail"></div>';
    var backBtn = sub.querySelector('[data-back-to-tickets]');
    if (backBtn) {
      backBtn.addEventListener('click', function () {
        sub.dataset.loaded = '';
        loadLogsFor(sub, tenantId, undefined);
      });
    }
    loadCombinedTimeline(sub, tenantId, data.ticket, data.logs);
  }
  function loadLogsFor(sub, tenantId, ticket) {
    if (sub.dataset.loaded === '1' && !ticket) return;
    sub.innerHTML = '<div class="empty-state"><p>Đang tải...</p></div>';
    var qs = '/tenant-project-logs?tenant=' + encodeURIComponent(tenantId) + (ticket ? '&ticket=' + encodeURIComponent(ticket) : '');
    fetchOwner(qs).then(function (data) {
      sub.dataset.loaded = '1';
      renderLogs(sub, data, tenantId);
    }).catch(function (err) {
      sub.innerHTML = '<div class="empty-state"><p>Không tải được nhật ký dự án: ' + escapeHtml(err.message) + '</p></div>';
    });
  }
  function renderHealthTiles(sub, summary) {
    var labels = {
      artifact_files: 'Artifact files', normalized_files: 'Normalized files', indexed_docs: 'Indexed docs',
      docs_needing_normalization: 'Cần normalize', docs_needing_build: 'Cần build', orphaned_normalized_files: 'Orphaned'
    };
    var order = ['artifact_files', 'normalized_files', 'indexed_docs', 'docs_needing_normalization', 'docs_needing_build', 'orphaned_normalized_files'];
    var tiles = order.map(function (key) {
      var bad = (key !== 'artifact_files' && key !== 'normalized_files' && key !== 'indexed_docs') && summary[key] > 0;
      return '<div class="health-coverage-item"><div class="health-eyebrow">' + labels[key] + '</div><div class="health-big" style="font-size:22px;margin:0 0 6px;' + (bad ? 'color:var(--status-critical);' : '') + '">' + (summary[key] || 0) + '</div></div>';
    }).join('');
    sub.innerHTML = '<div class="health-coverage-grid">' + tiles + '</div>';
  }
  function loadHealthFor(sub, tenantId) {
    fetch(apiBase() + '/health?tenant=' + encodeURIComponent(tenantId))
      .then(function (res) { return res.json().then(function (data) { return { ok: res.ok, data: data }; }); })
      .then(function (result) {
        if (!result.ok) throw new Error(result.data.detail || 'Không mở được /health');
        sub.dataset.loaded = '1';
        renderHealthTiles(sub, result.data.summary || {});
      })
      .catch(function (err) {
        sub.innerHTML = '<div class="empty-state"><p>Không tải được health: ' + escapeHtml(err.message) + '</p></div>';
      });
  }
  function loadWorkspaceSubPanel(sub) {
    if (sub.dataset.loaded === '1') return;
    if (sub.dataset.tenantDocs) {
      if (!ownerGate(sub)) return;
      sub.dataset.loaded = '1';
      fetchOwner('/tenant-documents?tenant=' + encodeURIComponent(sub.dataset.tenantDocs))
        .then(function (data) { renderDocTree(sub, data); })
        .catch(function (err) { sub.innerHTML = '<div class="empty-state"><p>Không tải được: ' + escapeHtml(err.message) + '</p></div>'; });
    } else if (sub.dataset.tenantLogs) {
      if (!ownerGate(sub)) return;
      loadLogsFor(sub, sub.dataset.tenantLogs, null);
    } else if (sub.dataset.tenantHealth) {
      loadHealthFor(sub, sub.dataset.tenantHealth);
    }
  }
  window.__loadWorkspaceSubPanel = loadWorkspaceSubPanel;
})();

(function () {
  // Panel "AI Agents — Alerts" — render native, cùng bộ CSS .health-* với panel "Health hệ
  // thống" (không nhúng iframe /agents-ui riêng). OWNER_API_KEY dùng CHUNG với khối "Cấu hình
  // server" (#syncOwnerKey / localStorage dashboardOwnerKey) — không có ô nhập key riêng trong
  // panel này, theo đúng yêu cầu người dùng 2026-08-14.
  //
  // Lifecycle alert (bổ sung 2026-08-14, theo yêu cầu người dùng): ALERTS.md là append-only,
  // agent KHÔNG tự đóng alert. Người dùng bấm "Xác nhận đã xử lý" trên 1 dòng -> POST
  // /agent-ops/confirm -> server CHỈ xếp hàng (KHÔNG tự xác minh, xem agent_ops.add_confirmation).
  // Lần kế tiếp đúng agent đã sinh alert đó chạy lại cho đúng ticket, nó đọc hàng chờ qua
  // scripts/agent_confirmations.py list-pending, tự đối chiếu bằng chứng thật rồi mới quyết định
  // ghi [DA_XU_LY] vào ALERTS.md hay giữ [MO] + tiếp tục alert — dashboard chỉ hiển thị lại kết
  // quả đó ở lần tải sau, không có gì "tự động resolve" ngay khi bấm gửi.
  var booted = false;
  var openConfirmFor = null; // alert_id đang mở ô nhập ghi chú (chỉ 1 dòng tại 1 thời điểm)
  function escapeHtml(text) {
    return String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function apiBase() {
    var origin = window.location && /^https?:/i.test(window.location.origin || '') ? window.location.origin : '';
    var el = document.getElementById('syncApiBase');
    var configured = (el && el.value.trim()) || localStorage.getItem('dashboardApiBase') || '';
    if (origin) return origin.replace(/\/$/, '');
    if (configured) return configured.replace(/\/$/, '');
    return 'http://127.0.0.1:8000';
  }
  function ownerKey() {
    return localStorage.getItem('dashboardOwnerKey') || localStorage.getItem('graphrag_owner_key') || '';
  }
  function formatDate(value) {
    if (!value) return 'n/a';
    var d = new Date(String(value).replace(' ', 'T'));
    return isNaN(d.getTime()) ? value : d.toLocaleString('vi-VN');
  }
  function ensurePanel() {
    var panel = document.getElementById('ai-agents');
    if (!panel) {
      var content = document.querySelector('.content');
      if (content) {
        panel = document.createElement('section');
        panel.id = 'ai-agents';
        panel.className = 'lane-panel';
        panel.innerHTML =
          '<div class="health-shell">' +
          '<div class="health-hero"><div><h2>AI Agents — Lịch sử &amp; Alerts</h2><p class="health-intro">Toàn bộ các lần knowledge-agents-orchestrator (approval-verification, architecture-compliance, raid-milestone-extraction, raci-daci-enforcer, retro-synthesizer) đã chạy, cùng mọi ALERT gom từ _project-logs/ALERTS.md của từng dự án.</p></div><span class="health-badge">Giám sát</span></div>' +
          '<div id="aiAgentsInlineRoot" class="health-native"></div>' +
          '</div>';
        content.appendChild(panel);
      }
    }
    if (!document.querySelector('.side-item[data-target="ai-agents"]')) {
      var nav = document.querySelector('.sidebar-nav');
      if (nav) {
        var btn = document.createElement('button');
        btn.className = 'side-item';
        btn.dataset.target = 'ai-agents';
        btn.innerHTML = '<span class="side-icon">🤖</span>AI Agents — Alerts';
        nav.appendChild(btn);
        if (typeof wireSideItem === 'function') wireSideItem(btn);
      }
    }
    var root = document.getElementById('aiAgentsInlineRoot');
    if (!root || booted) return root;
    booted = true;
    root.innerHTML =
      '<div class="health-config" style="grid-template-columns: 1fr auto;">' +
        '<div id="aiAgentsStatus" class="health-status">Đang chờ tải dữ liệu.</div>' +
        '<div class="health-actions"><button id="aiAgentsReload" class="health-btn" type="button">Tải lại</button></div>' +
      '</div>' +
      '<div id="aiAgentsMetrics" class="health-metrics"></div>' +
      '<section class="health-card">' +
        '<div class="health-eyebrow">Các lần orchestrator đã chạy</div><h3>Lịch sử giám sát</h3>' +
        '<ul id="aiAgentsRuns" class="health-history-list" style="margin-top:10px;"></ul>' +
      '</section>' +
      '<section class="health-card">' +
        '<div style="display:flex;justify-content:space-between;gap:12px;align-items:baseline;flex-wrap:wrap;">' +
          '<div><div class="health-eyebrow">Alerts</div><h3>Gom từ toàn bộ _project-logs/ALERTS.md</h3></div>' +
          '<span id="aiAgentsAlertCount" class="health-chip owner">0 alert</span>' +
        '</div>' +
        '<div class="links-toolbar" style="margin-top:10px;display:grid;grid-template-columns:repeat(4, minmax(0,1fr));gap:10px;">' +
          '<div class="links-filter"><label for="aiAgentsFilterProject">Dự án</label><select id="aiAgentsFilterProject" class="links-filter-select"><option value="">Tất cả</option></select></div>' +
          '<div class="links-filter"><label for="aiAgentsFilterAgent">Agent</label><select id="aiAgentsFilterAgent" class="links-filter-select"><option value="">Tất cả</option></select></div>' +
          '<div class="links-filter"><label for="aiAgentsFilterSeverity">Mức độ</label><select id="aiAgentsFilterSeverity" class="links-filter-select"><option value="">Tất cả</option></select></div>' +
          '<div class="links-filter"><label for="aiAgentsFilterStatus">Trạng thái</label><select id="aiAgentsFilterStatus" class="links-filter-select"><option value="">Tất cả</option><option value="MO">Đang mở</option><option value="DA_XU_LY">Đã xử lý</option></select></div>' +
        '</div>' +
        '<div id="aiAgentsAlertsTableWrap" class="health-table-wrap" style="margin-top:12px;"></div>' +
      '</section>';
    document.getElementById('aiAgentsReload').addEventListener('click', loadAiAgentsInline);
    ['aiAgentsFilterProject', 'aiAgentsFilterAgent', 'aiAgentsFilterSeverity', 'aiAgentsFilterStatus'].forEach(function (id) {
      document.getElementById(id).addEventListener('change', renderAlertsFiltered);
    });
    document.getElementById('aiAgentsAlertsTableWrap').addEventListener('click', handleAlertsTableClick);
    return root;
  }
  function setStatus(text, kind) {
    var el = document.getElementById('aiAgentsStatus');
    if (!el) return;
    el.textContent = text;
    el.className = 'health-status' + (kind ? ' ' + kind : '');
  }
  function severityChipClass(sev) {
    return (String(sev || '').toUpperCase() === 'CAO') ? 'critical' : 'summary';
  }
  function statusChipClass(status) {
    return status === 'DA_XU_LY' ? 'owner' : 'summary';
  }
  function statusLabel(status) {
    return status === 'DA_XU_LY' ? 'Đã xử lý' : 'Đang mở';
  }
  function renderMetrics(summary, alerts) {
    var open = (alerts || []).filter(function (a) { return a.status !== 'DA_XU_LY'; }).length;
    var openCao = (alerts || []).filter(function (a) { return a.status !== 'DA_XU_LY' && String(a.severity).toUpperCase() === 'CAO'; }).length;
    var cards = [
      ['Đang mở', open, 'Chưa được xác nhận xử lý'],
      ['CAO đang mở', openCao, 'Ưu tiên xử lý trước'],
      ['Chờ agent kiểm tra lại', summary.pending_confirmations || 0, 'Đã gửi xác nhận, agent chưa chạy lại để đối chiếu'],
      ['Tổng ALERT (mọi thời điểm)', summary.total_alerts || 0, 'Bao gồm cả alert đã xử lý'],
      ['Số lần orchestrator chạy', summary.total_runs || 0, 'Tính từ khi bắt đầu ghi run-log'],
      ['Lần chạy gần nhất', escapeHtml(formatDate(summary.last_run_time)), 'Thời điểm orchestrator chạy lần cuối']
    ];
    document.getElementById('aiAgentsMetrics').innerHTML = cards.map(function (c) {
      return '<article class="health-card"><div class="health-eyebrow">' + c[0] + '</div><div class="health-big" style="font-size:26px;">' + c[1] + '</div><div class="health-subtle">' + c[2] + '</div></article>';
    }).join('');
  }
  function agentChipHtml(agent) {
    var status = (agent.status || 'ok').toLowerCase();
    var cls = status === 'error' ? 'critical' : (status === 'skipped' ? 'summary' : 'owner');
    return '<span class="health-chip ' + cls + '" style="margin:2px 4px 2px 0;">' + escapeHtml(agent.name) + (agent.status ? ' (' + escapeHtml(agent.status) + ')' : '') + '</span>';
  }
  function renderRuns(runs) {
    var list = document.getElementById('aiAgentsRuns');
    if (!runs || !runs.length) {
      list.innerHTML = '<li class="health-subtle">Chưa có lần orchestrator nào được ghi run-log.</li>';
      return;
    }
    list.innerHTML = runs.map(function (run) {
      var agents = (run.agents || []).map(agentChipHtml).join('');
      var projects = (run.projects && run.projects.length) ? run.projects.join(', ') : run.scope;
      var alertsNew = run.alerts_new || [];
      var alertsHtml = alertsNew.length
        ? '<ul style="margin:8px 0 0;padding-left:18px;">' + alertsNew.map(function (a) {
            return '<li class="health-subtle"><strong>[' + escapeHtml(a.severity || '?') + '] ' + escapeHtml(a.agent || '?') + '</strong> (' + escapeHtml(a.project || '') + '): ' + escapeHtml(a.message || '') + '</li>';
          }).join('') + '</ul>'
        : '';
      return '<li>' +
        '<div style="display:flex;justify-content:space-between;gap:10px;align-items:baseline;flex-wrap:wrap;"><strong>' + escapeHtml(formatDate(run.time)) + ' — ' + escapeHtml(run.trigger || 'manual') + '</strong><span class="health-chip ' + (alertsNew.length ? 'critical' : 'owner') + '">' + alertsNew.length + ' alert mới</span></div>' +
        '<div class="health-subtle" style="margin-top:6px;">Phạm vi: ' + escapeHtml(projects) + '</div>' +
        '<div style="margin-top:6px;">' + agents + '</div>' +
        (run.summary ? '<div class="health-subtle" style="margin-top:6px;">' + escapeHtml(run.summary) + '</div>' : '') +
        alertsHtml +
        '</li>';
    }).join('');
  }
  var allAlerts = [];
  function populateFilterOptions() {
    var projects = Array.from(new Set(allAlerts.map(function (a) { return a.project; }).filter(Boolean))).sort();
    var agents = Array.from(new Set(allAlerts.map(function (a) { return a.agent; }).filter(Boolean))).sort();
    var severities = Array.from(new Set(allAlerts.map(function (a) { return a.severity; }).filter(Boolean))).sort();
    function fill(id, values) {
      var el = document.getElementById(id);
      var current = el.value;
      el.innerHTML = '<option value="">Tất cả</option>' + values.map(function (v) { return '<option value="' + escapeHtml(v) + '">' + escapeHtml(v) + '</option>'; }).join('');
      if (values.indexOf(current) !== -1) el.value = current;
    }
    fill('aiAgentsFilterProject', projects);
    fill('aiAgentsFilterAgent', agents);
    fill('aiAgentsFilterSeverity', severities);
  }
  var AGENT_LABELS = {
    'approval-verification': 'Xác minh phê duyệt',
    'architecture-compliance': 'Rà soát kiến trúc',
    'raid-milestone-extraction': 'RAID & mốc tiến độ',
    'raci-daci-enforcer': 'Đối chiếu phân công RACI',
    'retro-synthesizer': 'Tổng hợp retro'
  };
  function agentCellHtml(agentName) {
    var desc = AGENT_LABELS[agentName] || '';
    return '<div>' + escapeHtml(agentName) + '</div>' + (desc ? '<div class="health-subtle" style="margin-top:2px;">' + escapeHtml(desc) + '</div>' : '');
  }
  function ticketCellHtml(a) {
    var short = a.ticket.split('/').pop();
    return escapeHtml(a.project) + '<div class="health-subtle" style="margin-top:2px;"><span class="health-code" title="' + escapeHtml(a.ticket) + '">' + escapeHtml(short) + '</span></div>';
  }
  function picCellHtml(a) {
    if (!a.pic || a.pic === 'Chưa gán') {
      return '<span class="health-subtle">Chưa gán</span>';
    }
    return escapeHtml(a.pic);
  }
  function confirmCellHtml(a) {
    if (a.status === 'DA_XU_LY') {
      return '<span class="health-subtle">Đã xử lý</span>';
    }
    if (openConfirmFor === a.alert_id) {
      return (
        '<div style="display:grid;gap:6px;">' +
          '<textarea class="health-textarea" rows="3" data-confirm-input data-alert-id="' + escapeHtml(a.alert_id) + '" placeholder="Bằng chứng/ghi chú xác nhận..."></textarea>' +
          '<div style="display:flex;gap:6px;">' +
            '<button class="health-btn" type="button" data-confirm-submit data-alert-id="' + escapeHtml(a.alert_id) + '">Gửi</button>' +
            '<button class="health-btn secondary" type="button" data-confirm-cancel data-alert-id="' + escapeHtml(a.alert_id) + '">Huỷ</button>' +
          '</div>' +
          '<div class="health-subtle" data-confirm-msg="' + escapeHtml(a.alert_id) + '"></div>' +
        '</div>'
      );
    }
    return '<button class="health-btn secondary" type="button" data-confirm-open data-alert-id="' + escapeHtml(a.alert_id) + '">Xác nhận đã xử lý</button>';
  }
  function renderAlertsFiltered() {
    var pf = document.getElementById('aiAgentsFilterProject').value;
    var af = document.getElementById('aiAgentsFilterAgent').value;
    var sf = document.getElementById('aiAgentsFilterSeverity').value;
    var stf = document.getElementById('aiAgentsFilterStatus').value;
    var filtered = allAlerts.filter(function (a) {
      return (!pf || a.project === pf) && (!af || a.agent === af) && (!sf || a.severity === sf) && (!stf || a.status === stf);
    });
    document.getElementById('aiAgentsAlertCount').textContent = filtered.length + ' / ' + allAlerts.length + ' alert';
    var rows = filtered.length ? filtered.map(function (a) {
      return '<tr><td>' + escapeHtml(formatDate(a.time)) + '</td>' +
        '<td><span class="health-chip ' + statusChipClass(a.status) + '">' + statusLabel(a.status) + '</span></td>' +
        '<td><span class="health-chip ' + severityChipClass(a.severity) + '">' + escapeHtml(a.severity) + '</span></td>' +
        '<td>' + agentCellHtml(a.agent) + '</td>' +
        '<td>' + ticketCellHtml(a) + '</td>' +
        '<td>' + picCellHtml(a) + '</td>' +
        '<td>' + escapeHtml(a.message) + '</td>' +
        '<td>' + confirmCellHtml(a) + '</td></tr>';
    }).join('') : '<tr><td colspan="8"><div class="health-subtle">Không có alert nào khớp bộ lọc.</div></td></tr>';
    document.getElementById('aiAgentsAlertsTableWrap').innerHTML =
      '<table class="health-table health-table-alerts">' +
        '<colgroup><col style="width:8%"><col style="width:7%"><col style="width:6%"><col style="width:11%"><col style="width:12%"><col style="width:11%"><col style="width:24%"><col style="width:21%"></colgroup>' +
        '<thead><tr><th>Thời gian</th><th>Trạng thái</th><th>Mức độ</th><th>Agent</th><th>Dự án / Ticket</th><th>PIC / Role</th><th>Nội dung</th><th>Xác nhận</th></tr></thead>' +
        '<tbody>' + rows + '</tbody>' +
      '</table>';
    var openArea = document.querySelector('textarea[data-confirm-input]');
    if (openArea) openArea.focus();
  }
  function submitConfirmation(alertId, note, submitBtn, msgEl) {
    var alertObj = allAlerts.filter(function (a) { return a.alert_id === alertId; })[0];
    if (!alertObj) return;
    submitBtn.disabled = true;
    if (msgEl) msgEl.textContent = 'Đang gửi...';
    fetch(apiBase() + '/agent-ops/confirm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-API-Key': ownerKey() },
      body: JSON.stringify({
        alert_id: alertObj.alert_id,
        project: alertObj.project,
        ticket: alertObj.ticket,
        agent: alertObj.agent,
        message: alertObj.message,
        note: note
      })
    })
      .then(function (res) { return res.json().then(function (data) { return { ok: res.ok, data: data }; }); })
      .then(function (result) {
        if (!result.ok) throw new Error(result.data.detail || 'Gửi thất bại');
        openConfirmFor = null;
        setStatus('Đã gửi xác nhận cho ' + escapeHtml(alertObj.agent) + ' — sẽ được đối chiếu bằng chứng thật ở lần chạy tiếp theo, không tự đóng ngay.', 'ok');
        renderAlertsFiltered();
      })
      .catch(function (err) {
        submitBtn.disabled = false;
        if (msgEl) msgEl.textContent = 'Lỗi: ' + err.message;
      });
  }
  function handleAlertsTableClick(event) {
    var openBtn = event.target.closest('[data-confirm-open]');
    if (openBtn) {
      openConfirmFor = openBtn.getAttribute('data-alert-id');
      renderAlertsFiltered();
      return;
    }
    var cancelBtn = event.target.closest('[data-confirm-cancel]');
    if (cancelBtn) {
      openConfirmFor = null;
      renderAlertsFiltered();
      return;
    }
    var submitBtn = event.target.closest('[data-confirm-submit]');
    if (submitBtn) {
      var alertId = submitBtn.getAttribute('data-alert-id');
      var input = document.querySelector('textarea[data-confirm-input][data-alert-id="' + alertId + '"]');
      var msgEl = document.querySelector('[data-confirm-msg="' + alertId + '"]');
      var note = input ? input.value.trim() : '';
      if (!note) {
        if (msgEl) msgEl.textContent = 'Cần nhập ghi chú/bằng chứng trước khi gửi.';
        return;
      }
      submitConfirmation(alertId, note, submitBtn, msgEl);
    }
  }
  function loadAiAgentsInline() {
    ensurePanel();
    if (!ownerKey()) {
      setStatus('Cần OWNER_API_KEY — nhập ở khối "Cấu hình server" phía trên rồi bấm "Lưu cấu hình".', 'err');
      document.getElementById('aiAgentsMetrics').innerHTML = '';
      document.getElementById('aiAgentsRuns').innerHTML = '';
      document.getElementById('aiAgentsAlertsTableWrap').innerHTML = '';
      return;
    }
    setStatus('Đang tải dữ liệu agent ops...', '');
    fetch(apiBase() + '/agent-ops', { headers: { 'X-API-Key': ownerKey() } })
      .then(function (res) { return res.json().then(function (data) { return { ok: res.ok, data: data }; }); })
      .then(function (result) {
        if (!result.ok) throw new Error(result.data.detail || 'Không mở được /agent-ops');
        allAlerts = result.data.alerts || [];
        renderMetrics(result.data.summary || {}, allAlerts);
        renderRuns(result.data.runs || []);
        populateFilterOptions();
        renderAlertsFiltered();
        setStatus('Đã tải xong.', 'ok');
      })
      .catch(function (err) {
        setStatus('Không tải được /agent-ops: ' + err.message, 'err');
      });
  }
  window.__loadAiAgentsInline = loadAiAgentsInline;
  ensurePanel();
  if (document.getElementById('ai-agents') && document.getElementById('ai-agents').classList.contains('active')) {
    loadAiAgentsInline();
  }
})();

(function () {
  // Nội dung "Chiến lược & Phương pháp luận" (combo card + bảng chi tiết cho 3/5 tài liệu của
  // tenants/_common) — TRƯỚC ĐÂY tự tạo 1 panel + nút sidebar RIÊNG (id="strategy-methodology"),
  // trùng lặp với entry "🌐 _common" đã có sẵn trong cây "Dữ liệu" từ Phase 6 (2026-08-25, sidebar
  // đi theo tenants/registry.yaml — xem _sidebar_and_panels). Phat hien khi nguoi dung bao "khong
  // click duoc nut" - panel/nut cu nay khong nam trong co che active-state cleanup moi
  // (.side-item, .common-item), gay 2 nut cung sang active mot luc, roi vao doi voi nhau. Sua:
  // gan noi dung nay TRUC TIEP vao tab "Tài liệu" cua panel _common that su (#ws--common-docs),
  // KHONG con tu tao panel/nut rieng nua.
  function esc(t) { return String(t); }
  function chip(t) { return '<span class="chip">' + esc(t) + '</span>'; }
  function chipGroup(title, items) {
    return '<div style="margin:0 0 8px;"><div style="font-size:11px;font-weight:700;color:var(--text-secondary);margin-bottom:4px;">' +
      esc(title) + '</div><div class="chip-row">' + items.map(chip).join('') + '</div></div>';
  }
  function mkTable(headers, rows) {
    var thead = '<thead><tr>' + headers.map(function (h) {
      return '<th style="text-align:left;">' + esc(h) + '</th>';
    }).join('') + '</tr></thead>';
    var tbody = '<tbody>' + rows.map(function (r) {
      return '<tr>' + r.map(function (c) { return '<td>' + c + '</td>'; }).join('') + '</tr>';
    }).join('') + '</tbody>';
    return '<div class="table-wrap"><table>' + thead + tbody + '</table></div>';
  }
  function comboCard(icon, title, formula) {
    return '<div class="health-card">' +
      '<div class="health-eyebrow">' + icon + ' ' + esc(title) + '</div>' +
      '<div style="font-size:12px;line-height:1.55;color:var(--text-primary);">' + formula + '</div>' +
      '</div>';
  }
  function sectionTitle(text, sub) {
    return '<div style="margin:22px 0 8px;"><div class="query-block-title">' + esc(text) + '</div>' +
      (sub ? '<div style="font-size:11.5px;color:var(--text-muted);margin-top:2px;">' + sub + '</div>' : '') + '</div>';
  }

  function ensurePanel() {
    var mount = document.getElementById('ws--common-docs');
    if (!mount || mount.dataset.strategyInjected === '1') return mount;

    var html = '';
    html += '<div class="cio-header"><span class="id-badge">TÓM TẮT ÁP DỤNG</span>' +
      '<h1>Chiến lược &amp; Phương pháp luận</h1>' +
      '<p class="subtitle">Combo pattern/phương pháp sẵn dùng, tổng hợp từ 3/5 tài liệu bên dưới ' +
      '— dùng để tối ưu hoá quyết định, thực thi, dự đoán.</p></div>';

    html += sectionTitle('🧩 Combo nổi bật — áp dụng ngay', 'Phối hợp pattern/phương pháp từ cả 3 tài liệu theo từng tình huống cụ thể.');
    var combos = [
      comboCard('🏗️', 'Kiến trúc hệ nghiệp vụ phức tạp',
        'Hexagonal/Clean Architecture + DDD Tactical (Aggregate, Repository, Domain Service, Bounded Context) + CQRS/Event Sourcing'),
      comboCard('🔀', 'Hệ phân tán chịu lỗi cao',
        'Microservices + Saga (Orchestration/Choreography) + Retry, Backoff, Circuit Breaker, Bulkhead (Reliability) + Service Mesh'),
      comboCard('🧱', 'Migrate dần từ hệ legacy',
        'API Gateway + BFF + Anti-Corruption Layer + Strangler Fig'),
      comboCard('🔐', 'Nền tảng bảo mật Cloud-Native',
        'Zero Trust + mTLS Everywhere + Policy-as-Code + Secretless Architecture + Vault Pattern'),
      comboCard('🤖', 'Bảo mật hệ thống AI Agent',
        'Prompt Validation + Guardrail + LLM Firewall + Tool Invocation Policy + Agent Sandbox + Memory Isolation'),
      comboCard('🔎', 'Research công nghệ mới trong 3 ngày',
        'Gartner Problem Definition + MECE/Issue Tree (Ngày 1) → FII + Gartner Insight Loop + IHER (Ngày 2) → Pyramid Principle + SCQA + McKinsey Slide Logic (Ngày 3)'),
      comboCard('🚀', 'Triển khai dự án tốc độ cao',
        'Triple-Thrust (Two-Pizza Team + Fast-Tracking/CCPM + OODA Loop) + Timeboxing + MoSCoW + DevOps/CI-CD + Quiet-Hour'),
      comboCard('📊', 'Trình bày thuyết phục lãnh đạo (CIO/CTO)',
        'Pyramid Principle (Minto) + SCQA + McKinsey Slide Logic + Gartner Storytelling (Business Value + Human Value Story)')
    ];
    html += '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:10px;margin-bottom:6px;">' +
      combos.join('') + '</div>';

    html += sectionTitle('A. Kiến trúc &amp; Design Patterns', 'Nguồn: "Architecture Design Patterns v1.0" (Dec 2025) — 3 nhóm lớn, ~130 pattern.');

    html += '<div style="font-size:11.5px;font-weight:700;color:var(--text-primary);margin:10px 0 2px;">A1. Architectural Patterns (Macro Architecture)</div>';
    html += chipGroup('Traditional', ['Layered Architecture', 'Monolithic Architecture', 'Modular Monolith', 'Client–Server', 'SOA', 'Component-Based Architecture', 'Pipeline Architecture', 'Blackboard Architecture']);
    html += chipGroup('Modern Architecture', ['Hexagonal (Ports &amp; Adapters)', 'Onion Architecture', 'Clean Architecture', 'Screaming Architecture', 'Microkernel (Plug-in)', 'Reflection', 'PAC']);
    html += chipGroup('Microservices', ['Microservices Architecture', 'Self-Contained Systems (SCS)']);
    html += chipGroup('Cloud Architecture', ['Serverless Architecture', 'Cloud-Native Architecture']);
    html += chipGroup('Event Architecture', ['Event-Driven Architecture (EDA)', 'Event Streaming Architecture', 'CQRS', 'Event Sourcing', 'Saga (Orchestration)', 'Saga (Choreography)']);
    html += chipGroup('Advanced Architecture', ['Reactive Architecture', 'Space-Based Architecture', 'Actor Model Architecture', 'Data Mesh', 'Data Lake/Lakehouse']);

    html += '<div style="font-size:11.5px;font-weight:700;color:var(--text-primary);margin:14px 0 2px;">A2. Enterprise Integration Patterns (EIP)</div>';
    html += chipGroup('1. Messaging Channels', ['Point-to-Point Channel', 'Publish-Subscribe Channel', 'Dead Letter Channel', 'Guaranteed Delivery', 'Message Bus']);
    html += chipGroup('2. Message Construction', ['Command Message', 'Event Message', 'Document Message', 'Request-Reply', 'Correlation Identifier']);
    html += chipGroup('3. Message Routing', ['Content-Based Router', 'Message Filter', 'Dynamic Router', 'Recipient List', 'Splitter', 'Aggregator', 'Scatter-Gather', 'Routing Slip', 'Resequencer']);
    html += chipGroup('4. Message Transformation', ['Content Enricher', 'Content Filter', 'Claim Check', 'Normalizer', 'Canonical Data Model']);
    html += chipGroup('5. Integration Endpoints', ['Messaging Gateway', 'Polling Consumer', 'Event-Driven Consumer', 'Service Activator']);
    html += chipGroup('6. Modern Integration Patterns', ['API Gateway', 'BFF (Backend for Frontend)', 'ACL (Anti-Corruption Layer)', 'Strangler Fig']);

    html += '<div style="font-size:11.5px;font-weight:700;color:var(--text-primary);margin:14px 0 2px;">A3. Cloud-Native / Distributed System Patterns</div>';
    html += chipGroup('A. Reliability', ['Retry', 'Backoff', 'Timeout', 'Circuit Breaker', 'Bulkhead', 'Fallback', 'Failover', 'Supervisor']);
    html += chipGroup('B. Scalability / State', ['Stateless Service', 'Sharding', 'Partitioning', 'Cache-Aside', 'Distributed Cache', 'Read/Write Split', 'API Composition', 'Aggregator']);
    html += chipGroup('C. Deployment', ['Blue-Green', 'Canary Release', 'Rolling Update', 'Immutable Infrastructure', 'GitOps']);
    html += chipGroup('D. Kubernetes', ['Sidecar', 'Ambassador', 'Adapter', 'Operator', 'Controller', 'Init Container', 'Service Mesh']);
    html += chipGroup('E. Distributed Event', ['Event Notification', 'Event-Carried State Transfer', 'Transactional Outbox', 'Idempotent Consumer']);

    html += '<div style="font-size:11.5px;font-weight:700;color:var(--text-primary);margin:14px 0 2px;">A4. Cloud &amp; AI Security Patterns</div>';
    html += chipGroup('Cloud Security', ['Zero Trust', 'Identity Federation', 'SPIFFE/SPIRE', 'mTLS Everywhere', 'Secretless Architecture', 'Vault Pattern', 'Policy-as-Code', 'Microsegmentation', 'SBOM', 'Signed Artifact', 'Secure Supply Chain']);
    html += chipGroup('AI / LLM / Agent Security', ['Prompt Validation', 'Guardrail', 'LLM Firewall', 'Output Filtering', 'Semantic Safety', 'Tool Invocation Policy', 'Memory Isolation', 'Agent Sandbox']);

    html += '<div style="font-size:11.5px;font-weight:700;color:var(--text-primary);margin:14px 0 2px;">B. Code-Level Patterns — GoF (Gang of Four)</div>';
    html += chipGroup('Creational', ['Singleton', 'Factory Method', 'Abstract Factory', 'Builder', 'Prototype']);
    html += chipGroup('Structural', ['Adapter', 'Bridge', 'Composite', 'Decorator', 'Facade', 'Flyweight', 'Proxy']);
    html += chipGroup('Behavioral', ['Strategy', 'State', 'Observer', 'Command', 'Chain of Responsibility', 'Mediator', 'Iterator', 'Template Method', 'Visitor', 'Memento', 'Interpreter']);

    html += '<div style="font-size:11.5px;font-weight:700;color:var(--text-primary);margin:14px 0 2px;">C. Domain-Driven Design — DDD Tactical Patterns</div>';
    html += chipGroup('Strategic Patterns', ['Domain', 'Subdomain (Core/Supporting/Generic)', 'Bounded Context', 'Context Map', 'Shared Kernel', 'Customer–Supplier', 'Conformist', 'Anti-Corruption Layer', 'Open-Host Service', 'Published Language']);
    html += chipGroup('Tactical DDD Patterns', ['Entity', 'Value Object', 'Domain Event', 'Aggregate', 'Aggregate Root', 'Factory', 'Repository', 'Specification', 'Domain Service', 'Application Service', 'Policy Pattern', 'Event Handler', 'Event Publisher', 'Snapshot', 'Optimistic Locking/Versioning', 'Read Model', 'Write Model']);

    html += sectionTitle('B. Phương pháp luận Research &amp; Trình bày (REP)', 'Nguồn: "Rapid Exploration &amp; Presentation Methodology v1.1" (Nov 2025) — 4 pha, 15 phương pháp, đóng gói thành khung 3 ngày.');

    html += mkTable(
      ['Pha REP', 'Mục tiêu', 'Thời lượng gợi ý'],
      [
        ['<strong>1. Rapid Exploration</strong><br>(Problem Framing &amp; Scoping)', 'Hiểu đúng vấn đề, phạm vi, bối cảnh; phân rã có hệ thống', '10–20%'],
        ['<strong>2. Rapid Synthesis</strong><br>(Insight &amp; Recommendation)', 'Biến dữ kiện thành insight và khuyến nghị có căn cứ', '30–40%'],
        ['<strong>3. Rapid Presentation</strong><br>(Storyline &amp; Slide Design)', 'Trình bày ngắn – rõ – thuyết phục cho đúng đối tượng', '20–30%'],
        ['<strong>4. Reflection &amp; Re-iteration</strong><br>(Learning &amp; Next Loop)', 'Rút kinh nghiệm, củng cố tri thức, chuẩn bị vòng lặp mới', '10–15%']
      ]
    );

    html += '<div style="font-size:11.5px;font-weight:700;color:var(--text-primary);margin:14px 0 2px;">15 phương pháp luận trong 4 pha REP</div>';
    html += mkTable(
      ['#', 'Phương pháp', 'Giai đoạn', 'Vai trò'],
      [
        ['1', 'Gartner Problem Definition Framework', 'Pha 1 — Problem Framing', 'Logic'],
        ['2', 'Design Thinking (Empathize–Define–Ideate–Prototype–Test)', 'Pha 1 — Empathize &amp; Define', 'Logic'],
        ['3', 'Issue Tree / Logic Tree', 'Pha 1 — Decomposition', 'Logic'],
        ['4', 'MECE (Mutually Exclusive, Collectively Exhaustive)', 'Pha 1 — Structuring', 'Logic'],
        ['5', 'Gartner Insight Loop (Observe–Orient–Decide–Act–Learn)', 'Pha 2 — Insight Formation', 'Logic'],
        ['6', 'Fact → Insight → Implication (FII)', 'Pha 2 — Insight Creation', 'Logic'],
        ['7', 'Issue–Hypothesis–Evidence–Recommendation (IHER)', 'Pha 2 — Hypothesis Testing', 'Logic'],
        ['8', 'BCG Pyramid', 'Pha 2 — Structure Insight', 'Trình bày'],
        ['9', 'Pyramid Principle (Barbara Minto)', 'Pha 3 — Story Structuring', 'Trình bày'],
        ['10', 'SCQA (Situation–Complication–Question–Answer)', 'Pha 3 — Story Framing', 'Trình bày'],
        ['11', 'SCR (Situation–Complication–Resolution)', 'Pha 3 — Narrative Flow', 'Trình bày'],
        ['12', 'McKinsey Slide Logic', 'Pha 3 — Slide Design', 'Trình bày'],
        ['13', 'Gartner Storytelling Framework', 'Pha 3 — Executive Storytelling', 'Trình bày'],
        ['14', 'AIDA (Attention–Interest–Desire–Action)', 'Pha 3 — Engagement Design', 'Trình bày'],
        ['15', '4MAT (Why–What–How–What if)', 'Pha 4 — Knowledge Retention', 'Trình bày']
      ]
    );

    html += '<div style="font-size:11.5px;font-weight:700;color:var(--text-primary);margin:14px 0 2px;">🧩 Combo chính: 3 Days Framework (nén 4 pha REP + 15 phương pháp vào 3 ngày)</div>';
    html += mkTable(
      ['Ngày', 'Giai đoạn', 'Phương pháp áp dụng', 'Sản phẩm đầu ra'],
      [
        ['<strong>Day 1</strong>', 'Problem Clarification', 'Gartner Problem Definition + SCQA (S-C-Q) + MECE + Issue Tree + giả thuyết H0/H1', 'One-Sentence Problem Statement, Issue Tree MECE, Scope IN/OUT, Success Criteria'],
        ['<strong>Day 2</strong>', 'Exploration &amp; Insight Generation', 'FII (Fact→Insight→Implication) + Gartner Insight Loop + IHER + Evidence Table + MECE Grouping', 'Bảng FII, Evidence Table, Implication Map, Proposed Answer Draft (Pyramid)'],
        ['<strong>Day 3</strong>', 'Synthesis &amp; Presentation', 'Pyramid Principle + SCQA (hoàn thiện Answer) + McKinsey Slide Logic + BCG Pyramid + Gartner Storytelling', 'Slide Deck hoàn chỉnh, Storyline SCQA, Executive Summary 1 trang, Action Plan']
      ]
    );

    html += '<div style="font-size:11.5px;font-weight:700;color:var(--text-primary);margin:14px 0 2px;">5 nhóm phương pháp trình bày theo loại giá trị</div>';
    html += mkTable(
      ['Nhóm', 'Phương pháp tiêu biểu', 'Loại Value phù hợp'],
      [
        ['Logic truyền đạt', 'SCQA, Pyramid Principle, MECE, Issue Tree, Gartner Insight Loop', 'Business Value Story'],
        ['Kể chuyện &amp; cảm xúc', 'Hero’s Journey, Gartner Storytelling, Business Narrative Arc, TED Story Curve', 'Human Value Story'],
        ['Trình bày chiến lược', 'BCG Pyramid, McKinsey Slide Logic, FII, SCR', 'Business Value Story (có thể Hybrid)'],
        ['Giải quyết vấn đề', 'Design Thinking, Gartner Problem Definition, TOGAF/BCG Issue Tree', 'Business Value Story'],
        ['Truyền cảm hứng / đào tạo', 'AIDA, 4MAT, GROW, ASK Model', 'Human Value Story (thường Hybrid)']
      ]
    );

    html += '<div style="font-size:11.5px;font-weight:700;color:var(--text-primary);margin:14px 0 2px;">3 cấp độ nguồn thông tin khi research</div>';
    html += mkTable(
      ['Cấp độ', 'Đặc điểm', 'Ví dụ'],
      [
        ['1. Nguồn sơ cấp (Primary)', 'Chưa qua xử lý/diễn giải, giá trị xác thực cao', 'Báo cáo nghiên cứu gốc (arXiv, IEEE), khảo sát, log hệ thống, tài liệu gốc tổ chức'],
        ['2. Nguồn thứ cấp (Secondary)', 'Phân tích/tổng hợp/diễn giải từ nguồn sơ cấp', 'Review paper, báo cáo phân tích McKinsey/Gartner/BCG'],
        ['3. Nguồn tam cấp (Tertiary)', 'Tổng hợp/tra cứu từ nhiều nguồn khác, dùng để định hướng nhanh', 'Wikipedia, từ điển, encyclopedia, catalog thư viện']
      ]
    );

    html += '<div style="font-size:11.5px;font-weight:700;color:var(--text-primary);margin:14px 0 2px;">18 loại giá trị khi trình bày 1 dịch vụ/giải pháp</div>';
    html += chipGroup('Business Value', ['Financial Value', 'Strategic Alignment Value', 'Compliance Value', 'ESG/Social Value']);
    html += chipGroup('Operational Value', ['Functional Value', 'Operational Value', 'Risk Reduction Value', 'Reliability Value', 'Speed/Time-to-Value', 'Scalability Value', 'Flexibility Value']);
    html += chipGroup('Human Value', ['Customer Value', 'Experience Value', 'Human Value', 'Collaboration Value']);
    html += chipGroup('Innovation Value', ['Digital Transformation Value', 'Innovation Value', 'Data Value']);

    html += '<div style="font-size:11.5px;font-weight:700;color:var(--text-primary);margin:14px 0 2px;">Risk control khi research thần tốc</div>';
    html += mkTable(
      ['Rủi ro', 'Cơ chế kiểm soát'],
      [
        ['Shallow Research', 'Method 15–50–80 (breadth ngày 1, đào sâu ngày 2, refine ngày 3) + MECE Issue Tree ngay từ đầu + tối thiểu 3 nguồn uy tín'],
        ['Jumping to Conclusions', 'Red Team Check 30 phút + IHER ép kiểm chứng + SCQA tránh kết luận sớm'],
        ['Decision Risk', '2-Level Decision Validation (Value Impact, Risk Impact) + Boundary of Confidence + Pre-mortem 20 phút'],
        ['Over-Speed', 'Timeboxing 90-30-30 (Research–Synthesis–Review) + Quality Gate cuối Day 2 (MECE + Logic + Evidence ≥60%)']
      ]
    );

    html += sectionTitle('C. Triển khai dự án thần tốc', 'Nguồn: "Triển khai dự án thần tốc (Accelerated Project Delivery) v1.3" (Nov 2025) — nguyên tắc, khung 3 trục, 15 phương pháp, team, risk.');

    html += '<div style="font-size:11.5px;color:var(--text-secondary);margin:6px 0 10px;">Nguyên tắc gốc: <strong>tối ưu hệ thống để tăng tốc</strong> (flow, pipeline, quy trình) — không phải ép nhân viên làm nhiều hơn; <strong>giảm chờ đợi</strong> Design ↔ Dev ↔ Test bằng Fast-Tracking hợp lý, không phải chồng lớp bừa bãi làm giảm chất lượng.</div>';

    html += '<div style="font-size:11.5px;font-weight:700;color:var(--text-primary);margin:10px 0 2px;">🧩 Combo chính: Triple-Thrust (3 mũi đẩy đồng thời)</div>';
    html += mkTable(
      ['Trục', 'Tư duy cốt lõi', 'Công cụ / Framework đặc trưng'],
      [
        ['Tổ chức (Organizational)', 'Squad nhỏ, tự chủ, đa chức năng', 'Spotify Squad Model, Two-Pizza Team Rule, DevSecOps cross-role'],
        ['Quy trình (Process)', 'Loại bỏ lãng phí, tự động hóa, song song hóa', 'Agile/Kanban, Lean Flow, Fast-Tracking, DevOps/CI-CD, CCPM Buffer'],
        ['Quyết định (Decision)', 'Ra quyết định dựa dữ liệu &amp; feedback thực tế', 'OODA Loop, OKR, Experimentation Framework, A/B Test']
      ]
    );
    html += '<div style="font-size:11px;color:var(--text-muted);margin:0 0 12px;">Chỉ tăng tốc 1 trục mà 2 trục kia vẫn "truyền thống" → dự án tăng rủi ro nhưng không tăng tốc độ thật.</div>';

    html += '<div style="font-size:11.5px;font-weight:700;color:var(--text-primary);margin:14px 0 2px;">15 phương pháp trong 5 giai đoạn tăng tốc</div>';
    html += mkTable(
      ['Giai đoạn', 'Phương pháp', 'Ứng dụng thực tế'],
      [
        ['1 — Accelerated Planning', 'Timeboxing', 'Sprint/pha cố định 1–2 tuần bất kể khối lượng'],
        ['', 'MoSCoW Prioritization', 'Chọn đúng phạm vi "phải có" khi thời gian giới hạn'],
        ['', 'Rolling-Wave Planning', 'Chi tiết 1–2 Sprint tới, phần xa hơn giữ high-level'],
        ['', 'CCPM (Critical Chain)', 'Quản lý buffer/constraint để tối đa throughput'],
        ['2 — Accelerated Execution', 'Two-Pizza Team Rule', '≤ 8 người, toàn quyền trong phạm vi chức năng'],
        ['', 'Fast-Tracking (PMBOK)', 'Song hành design–build–test thay vì tuần tự'],
        ['', 'Swarming (Spotify/Jira)', 'Cả đội dồn vào 1 blocker, rút ngắn lead time'],
        ['', 'Crashing (PMBOK)', 'Bổ sung chuyên gia/automation để rút ngắn tiến độ'],
        ['', 'DevOps / CI-CD / IaC', 'Mỗi commit deploy an toàn, giảm độ trễ feedback'],
        ['3 — Accelerated Flow', 'Lean Thinking', 'Cắt bỏ báo cáo, họp, approval không cần thiết'],
        ['', 'Value Stream Mapping', 'Loại bỏ mọi điểm chờ không tạo giá trị'],
        ['', 'Agile/Scrum/Kanban Flow', 'Theo dõi SLE 85% trong 3 ngày'],
        ['4 — Accelerated Decision', 'Lean Decision Loop (OODA/BOLC)', 'Quyết định dựa dữ liệu &amp; feedback real-time'],
        ['5 — Accelerated Risk &amp; Governance', 'Design Sprint (Google)', 'Define→Ideate→Prototype→Test trong 1 tuần'],
        ['', 'Fail-Fast &amp; Learn-Fast', 'MVP → Test → Pivot / Persevere']
      ]
    );

    html += '<div style="font-size:11.5px;font-weight:700;color:var(--text-primary);margin:14px 0 2px;">Team 5–7 người (đa nhiệm để tối ưu tốc độ)</div>';
    html += mkTable(
      ['Role', 'Mục tiêu chính', 'Số người', 'Đa nhiệm'],
      [
        ['Product Owner / PM', 'Chốt scope, giữ 1 Goal duy nhất, ưu tiên MoSCoW', '1', '60% PO – 40% BA/UX'],
        ['Tech Lead', 'Kiến trúc, quyết định kỹ thuật nhanh, unblock', '1', '50% TL – 30% Dev – 20% DevOps'],
        ['Delivery Lead', 'Điều phối delivery, loại bỏ tắc nghẽn', '1', '80% DL – 20% Coordination/Risk'],
        ['Full-stack Devs', 'Build nhanh UI + BE + Integration', '2–4', '80% Dev – 20% BA/QA'],
        ['QA / Tester', 'Test Must-path (không test 100%)', '0.5–1', '80% QA – 20% BA'],
        ['DevOps / Infra', 'CI/CD, logging, monitoring, auto-rollback', '0.5–1', '70% DevOps – 30% QA-auto'],
        ['BA (tuỳ chọn)', 'Chỉ cần khi nghiệp vụ phức tạp', '0–1', '60% BA – 30% UX – 10% PO']
      ]
    );
    html += '<div style="font-size:11px;color:var(--text-muted);margin:0 0 12px;">Team &gt; 8 người → thời gian phối hợp tăng theo cấp số nhân, tốc độ giảm ~30–40%.</div>';

    html += '<div style="font-size:11.5px;font-weight:700;color:var(--text-primary);margin:14px 0 2px;">Bóc nhỏ công việc &amp; Quiet-Hour</div>';
    html += mkTable(
      ['Mức task', 'Thời gian', 'Ví dụ', 'Tỷ lệ'],
      [
        ['XS Task', '0.5 ngày', 'Fix bug UI nhỏ, chỉnh validation', '30–40%'],
        ['S Task', '1 ngày', 'Tạo API, tạo form đơn, 1 business rule', '40–50%'],
        ['M Task', '2 ngày', 'Flow end-to-end đơn giản duy nhất', '10–20%'],
        ['L Task', '&gt; 2 ngày — CẤM', 'Feature logic phức tạp — PHẢI xé nhỏ', '—']
      ]
    );
    html += mkTable(
      ['Vai trò', 'Số task tối ưu / Sprint (2 tuần)'],
      [
        ['Developer', '5–8 task'],
        ['BA', '7–12 task'],
        ['Tester', '6–10 task'],
        ['Techlead', '3–5 task'],
        ['Delivery Lead', '6–10 task'],
        ['Squad Lead / PM', '3–5 task thực thi'],
        ['Designer', '4–7 task']
      ]
    );
    html += '<div style="font-size:11px;color:var(--text-muted);margin:0 0 12px;"><strong>Quiet-Hour</strong>: khung 60–90 phút cả đội tập trung tối đa, không họp – không chat – không đổi task. Đề xuất T2–T5: 10h–11h sáng và 14h–15h chiều.</div>';

    html += '<div style="font-size:11.5px;font-weight:700;color:var(--text-primary);margin:14px 0 2px;">Outcome / Deliverables &amp; tần suất Release</div>';
    html += mkTable(
      ['Deliverable', 'Chuẩn thần tốc'],
      [
        ['Architecture Sketch', '1 trang, high-level'],
        ['API Contract / OpenAPI', 'Chỉ API Must'],
        ['Data Model (ERD)', '5–10 bảng cốt lõi, high-level'],
        ['Sprint Backlog', '5–7 core tasks / sprint'],
        ['CI/CD Pipeline', 'Build → Test → Deploy, auto basic'],
        ['Logging &amp; Monitoring Dashboard', '5–10 metric chính'],
        ['Test Scenarios Must-Path', '60–90%, không test all'],
        ['RC Build (Release Candidate)', '1 bản']
      ]
    );
    html += '<div style="font-size:11px;color:var(--text-muted);margin:0 0 12px;">Release <strong>2 tuần/lần</strong> nếu CI/CD mạnh + automated test 40–60% + team 6–9 người. Release <strong>4 tuần/lần</strong> (ổn định hơn) nếu QA/CI-CD chưa hoàn thiện, tính năng phức tạp, hoặc team mới phối hợp.</div>';

    html += '<div style="font-size:11.5px;font-weight:700;color:var(--text-primary);margin:14px 0 2px;">Risk control khi triển khai thần tốc</div>';
    html += mkTable(
      ['Rủi ro', 'Cơ chế kiểm soát'],
      [
        ['Lệch hướng (Misalignment)', 'North Star Architecture 1–2 trang + 3-level alignment (Business→Product→Sprint) + Planning Sync 30’'],
        ['Chất lượng lệch (Quality Variance)', 'Quality Gate 20 tiêu chí + CI/CD Template bắt buộc + API contract-first'],
        ['Quá tải (Overload/Burnout)', 'MoSCoW + Timebox cứng + Weekly Load Check + Swarming 2–3 ngày khi nghẽn'],
        ['Rủi ro Silo', '1 Source of Truth + Weekly End-to-End Demo + Guild/Chapter hoạt động thực chất'],
        ['Khó Scale', 'Tất cả service stateless + Event-driven integration + Observability 3 lớp'],
        ['Xung đột vai trò', 'Single Ownership Rule (1 việc – 1 chủ sở hữu) + phân định CORE ROLE vs SUPPORT ROLE']
      ]
    );

    html += '<details style="margin-top:8px;"><summary style="cursor:pointer;font-size:11.5px;font-weight:700;color:var(--text-secondary);">Xem đầy đủ 20 nguyên tắc Đúng vs Ngộ nhận (Anti-Pattern)</summary>' +
      mkTable(
        ['Đúng (Best Practice)', 'Không đúng (Misconception)'],
        [
          ['Tối ưu hệ thống để tăng tốc (flow, pipeline, quy trình)', 'Ép nhân viên làm việc nhiều hơn để chạy nhanh'],
          ['Giảm chờ đợi Design↔Dev↔Test bằng Fast-Tracking hợp lý', 'Chồng lớp bừa bãi khiến chất lượng giảm'],
          ['Làm nhỏ hạng mục (slice nhỏ, increment nhanh)', 'Ôm việc lớn, dồn cuối sprint'],
          ['Timeboxing để kiểm soát phạm vi &amp; ưu tiên', 'Ép hoàn tất mọi thứ trong 1 box bất kể nguồn lực'],
          ['Ưu tiên Must–Should theo MoSCoW để giảm tải', 'Làm tất cả mọi thứ vì "sếp muốn"'],
          ['Rolling-Wave Planning (kế hoạch cuốn chiếu tuần)', 'Lập kế hoạch 3–6 tháng cố định, không điều chỉnh'],
          ['Giới hạn WIP &amp; giảm context switching', 'Để nhân viên làm 3–5 task song song'],
          ['Swarming để xử lý blocker trong ngày', 'Để task bị kẹt 2–3 ngày không ai xử lý'],
          ['Dev–QA–BA làm việc sát nhau (close-loop)', 'Làm việc silo, truyền đạt qua nhiều tầng'],
          ['Tự động hóa CI/CD, QA automation', 'Làm thủ công, release mất 1–2 ngày'],
          ['Fail-Fast &amp; Learn-Fast, lặp nhanh', 'Né thử nghiệm vì sợ sai, rework lớn'],
          ['Daily Sync + Risk Scan 10 phút', 'Daily họp dài 45–60 phút'],
          ['Bảo vệ sức khỏe đội (no overtime policy)', 'Tăng OT để đạt "thần tốc"'],
          ['Giảm Non-Value Work (họp thừa, báo cáo thừa)', 'Thêm họp, thêm báo cáo để "quản lý thời gian"'],
          ['Tập trung chất lượng đầu vào (Definition of Ready)', 'Nhét yêu cầu chưa rõ, gây rework gấp đôi'],
          ['Quyết định dựa trên số liệu: SLE, Cycle Time', 'Quyết định theo cảm tính, kỳ vọng chủ quan'],
          ['Nâng cấp năng lực đội: pairing, mentoring', 'Đổ lỗi cá nhân khi chậm tiến độ'],
          ['Quản trị rủi ro chủ động (MTTR thấp)', 'Chỉ xử lý khi rủi ro đã bùng nổ'],
          ['Release nhỏ – nhanh – an toàn', 'Release lớn gây căng thẳng, dễ lỗi'],
          ['Lãnh đạo dọn đường: unblock, remove waste', 'Lãnh đạo chỉ giao mục tiêu và gây áp lực']
        ]
      ) + '</details>';

    mount.insertAdjacentHTML('afterbegin', html);
    mount.dataset.strategyInjected = '1';
    return mount;
  }
  window.__loadStrategyMethodologyInline = ensurePanel;
  ensurePanel();
})();

(function () {
  // Panel "RACI QT.IT.005/009/019" — nhúng nguyên trạng file tĩnh scratch/RACI_...html (route
  // /reference/qt-it-raci) ngay trong body panel, giống các panel native khác (không mở tab mới,
  // không iframe — theo đúng yêu cầu người dùng 2026-08-17). Vì tài liệu gốc có CSS chọn tử phẳng
  // (h1/h2/table/ul/...) và biến CSS riêng (--surface/--text/...) sẽ đụng thẳng CSS toàn dashboard
  // nếu chèn trực tiếp vào light DOM, nên dùng Shadow DOM để cô lập style — vẫn coi là "load vào
  // body" vì không có iframe, không có tab mới, nội dung nằm ngay trong cây DOM của panel.
  var booted = false;
  function escapeHtml(text) {
    return String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function apiBase() {
    var origin = window.location && /^https?:/i.test(window.location.origin || '') ? window.location.origin : '';
    var el = document.getElementById('syncApiBase');
    var configured = (el && el.value.trim()) || localStorage.getItem('dashboardApiBase') || '';
    if (origin) return origin.replace(/\/$/, '');
    if (configured) return configured.replace(/\/$/, '');
    return 'http://127.0.0.1:8000';
  }
  function ensurePanel() {
    var panel = document.getElementById('qtit-raci');
    if (!panel) {
      var content = document.querySelector('.content');
      if (content) {
        panel = document.createElement('section');
        panel.id = 'qtit-raci';
        panel.className = 'lane-panel';
        panel.innerHTML =
          '<div class="health-shell">' +
          '<div class="health-hero"><div><h2>RACI QT.IT.005 / QT.IT.009 / QT.IT.019</h2><p class="health-intro">Tài liệu tra cứu RACI + sơ đồ quy trình, tổng quát cho mọi role/dự án — nội dung gốc được nhúng nguyên trạng ngay bên dưới.</p></div><span class="health-badge">Tham khảo</span></div>' +
          '<div id="qtitRaciRoot" class="health-native"></div>' +
          '</div>';
        content.appendChild(panel);
      }
    }
    if (!document.querySelector('.side-item[data-target="qtit-raci"]')) {
      var nav = document.querySelector('.sidebar-nav');
      var afterBtn = document.querySelector('.side-item[data-target="knowledge-links"]');
      if (nav) {
        var btn = document.createElement('button');
        btn.className = 'side-item';
        btn.dataset.target = 'qtit-raci';
        btn.title = 'Tài liệu tra cứu RACI + sơ đồ quy trình QT.IT.005/009/019 — dùng chung cho mọi role/dự án.';
        btn.innerHTML = '<span class="side-icon">📋</span>RACI QT.IT.005/009/019';
        if (afterBtn) afterBtn.insertAdjacentElement('afterend', btn);
        else nav.appendChild(btn);
        if (typeof wireSideItem === 'function') wireSideItem(btn);
      }
    }
  }
  function loadQtitRaciInline() {
    ensurePanel();
    var root = document.getElementById('qtitRaciRoot');
    if (!root || booted) return;
    booted = true;
    root.innerHTML = '<div class="nav-empty">Đang tải tài liệu...</div>';
    fetch(apiBase() + '/reference/qt-it-raci')
      .then(function (res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.text();
      })
      .then(function (html) {
        var parsed = new DOMParser().parseFromString(html, 'text/html');
        var styleEl = parsed.querySelector('style');
        var styleText = styleEl ? styleEl.textContent.replace(/:root/g, ':host') : '';
        var bodyClone = parsed.body.cloneNode(true);
        var scriptTexts = [];
        bodyClone.querySelectorAll('script').forEach(function (s) {
          scriptTexts.push(s.textContent);
          s.remove();
        });
        root.innerHTML = '';
        var host = document.createElement('div');
        // Panel gốc được thiết kế cho cả chiều rộng cửa sổ trình duyệt (240px sidebar +
        // main 1400px). Nhúng vào 1 panel hẹp hơn của dashboard (chính nó lại nằm trong
        // .app-shell display:flex và .health-shell/.health-native display:grid, các item mặc
        // định không tự co theo nội dung — min-width: auto) khiến nội dung rộng nhất bên trong
        // (bảng RACI min-width 1080px, track timeline min-width: max-content) đẩy ngược ra ngoài,
        // gây scrollbar ngang cho cả trang thay vì chỉ cuộn trong .table-wrap/.tl-scroll như thiết
        // kế gốc. Chặn ngay tại host: width/max-width 100% + overflow-x hidden khiến trình duyệt
        // coi kích thước tối thiểu của host là 0 khi tổ tiên (flex/grid) tính toán co giãn, đồng
        // thời clip phần dư — các scrollbar ngang CÓ CHỦ ĐÍCH bên trong (.table-wrap, .tl-scroll)
        // vẫn hoạt động bình thường vì chúng nằm gọn bên trong, chỉ chặn phần bị đẩy TRÀN RA NGOÀI.
        host.style.width = '100%';
        host.style.maxWidth = '100%';
        host.style.overflowX = 'hidden';
        root.appendChild(host);
        var shadow = host.attachShadow({ mode: 'open' });
        var hostStyle = document.createElement('style');
        hostStyle.textContent =
          ':host { display: block; width: 100%; max-width: 100%; overflow-x: hidden; font-family: "Segoe UI", system-ui, sans-serif; line-height: 1.55; background: var(--page); color: var(--text); } ' +
          styleText;
        shadow.appendChild(hostStyle);
        while (bodyClone.firstChild) shadow.appendChild(bodyClone.firstChild);
        // Link Mục lục (<a href="#id">) là fragment-nav chuẩn của trình duyệt — chỉ hoạt động
        // trong document gốc, KHÔNG tự cuộn tới phần tử nằm trong Shadow DOM (browser tìm id đó ở
        // document ngoài, không tìm xuyên qua shadow boundary nên bấm vào không có tác dụng gì).
        // Tự bắt click, tự scrollIntoView tới đúng phần tử trong shadow root để thay thế hành vi
        // mặc định bị mất khi nhúng qua Shadow DOM.
        shadow.addEventListener('click', function (e) {
          var link = e.target && e.target.closest ? e.target.closest('a[href^="#"]') : null;
          if (!link) return;
          var id = link.getAttribute('href').slice(1);
          if (!id) return;
          var target = shadow.getElementById(id);
          if (!target) return;
          e.preventDefault();
          target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        });
        // Script gốc dùng document.getElementById/querySelectorAll/addEventListener trong scope
        // của chính nó (RACI badges, modal, timeline) — proxy các lệnh đó sang shadow root
        // (ShadowRoot hỗ trợ sẵn getElementById/querySelectorAll/addEventListener qua
        // NonElementParentNode/ParentNode/EventTarget), còn document.createElement và
        // document.body (khoá scroll nền khi mở modal fullscreen) vẫn trỏ về document thật vì đó
        // đúng là hành vi mong muốn.
        var fakeDoc = new Proxy(document, {
          get: function (target, prop) {
            if (prop === 'getElementById' || prop === 'querySelector' || prop === 'querySelectorAll' ||
                prop === 'addEventListener' || prop === 'removeEventListener') {
              var v = shadow[prop];
              return typeof v === 'function' ? v.bind(shadow) : v;
            }
            var v = target[prop];
            return typeof v === 'function' ? v.bind(target) : v;
          }
        });
        scriptTexts.forEach(function (text) {
          try {
            new Function('document', text)(fakeDoc);
          } catch (e) {
            console.error('qtit-raci inline script error', e);
          }
        });
      })
      .catch(function (err) {
        root.innerHTML = '<div class="nav-empty">Không tải được tài liệu: ' + escapeHtml(err.message) + '</div>';
      });
  }
  window.__loadQtitRaciInline = loadQtitRaciInline;
  ensurePanel();
  if (document.getElementById('qtit-raci') && document.getElementById('qtit-raci').classList.contains('active')) {
    loadQtitRaciInline();
  }
})();

// Knowledge Home (khối "Mới cập nhật trong kho" trên Trang chủ) đã bị bỏ hoàn toàn khỏi Trang chủ
// theo yêu cầu người dùng 2026-08-11 (Trang chủ không còn chứa dữ liệu chi tiết nào) — toàn bộ IIFE
// fetch /knowledge-home + render #knowledgeHomeRoot trước đây nằm ở đây đã xoá vì không còn HTML
// target nào để gắn vào nữa.

(function () {
  var booted = false;
  function currentApiBase() {
    var input = document.getElementById('syncApiBase');
    return (input && input.value.trim()) || localStorage.getItem('dashboardApiBase') || window.location.origin || 'http://127.0.0.1:8000';
  }
  function currentOwnerKey() {
    var input = document.getElementById('syncOwnerKey');
    return (input && input.value.trim()) || localStorage.getItem('dashboardOwnerKey') || localStorage.getItem('graphrag_owner_key') || '';
  }
  function escapeHtml(text) {
    return String(text || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
  function setStatus(text, kind) {
    var el = document.getElementById('retrievalStatus');
    if (!el) return;
    el.textContent = text;
    el.className = 'query-status' + (kind ? ' ' + kind : '');
  }
  function renderTrace(trace) {
    var root = document.getElementById('retrievalTrace');
    if (!root) return;
    if (!trace || !trace.length) {
      root.innerHTML = '<span class="query-chip">không có</span>';
      return;
    }
    root.innerHTML = trace.map(function (item) {
      return '<span class="query-chip good">' + escapeHtml(item) + '</span>';
    }).join('');
  }
  function renderStats(stats) {
    var root = document.getElementById('retrievalStats');
    if (!root) return;
    var safe = stats || {};
    var cards = [
      ['candidate chunks', safe.candidate_chunks || 0],
      ['context chunks', safe.context_chunks || 0],
      ['candidate sources', safe.candidate_sources || 0],
      ['context sources', safe.context_sources || 0],
      ['duplicate in context', safe.context_duplicate_chunks || 0]
    ];
    root.innerHTML = cards.map(function (item) {
      return '<div class="query-stat-card"><div class="query-mini-label">' + escapeHtml(item[0]) + '</div><strong>' + item[1] + '</strong></div>';
    }).join('');
  }
  function renderSources(chunks, contextChunkIds) {
    var root = document.getElementById('retrievalSources');
    if (!root) return;
    if (!chunks || !chunks.length) {
      root.innerHTML = '<li><div class="query-empty">Không có source nào được trả về.</div></li>';
      return;
    }
    var included = {};
    (contextChunkIds || []).forEach(function (id) { included[id] = true; });
    root.innerHTML = chunks.map(function (chunk, idx) {
      var lineLabel = chunk.start_line ? ('dòng ' + chunk.start_line + '-' + chunk.end_line) : 'không rõ dòng';
      var inContext = included[chunk.chunk_id] ? '<span class="query-chip good">in context</span>' : '<span class="query-chip">candidate</span>';
      return '<li>'
        + '<div><strong>' + (idx + 1) + '. ' + escapeHtml(chunk.source_path) + '</strong></div>'
        + '<div class="query-mini-label" style="margin-top:6px;">' + escapeHtml(chunk.section || 'root') + '</div>'
        + '<div class="query-source-meta">'
        + inContext
        + '<span class="query-chip">' + escapeHtml(chunk.project || 'general') + '</span>'
        + '<span class="query-chip">' + escapeHtml(chunk.source_channel || 'n/a') + '</span>'
        + '<span class="query-chip">' + escapeHtml(chunk.reliability || 'n/a') + '</span>'
        + '<span class="query-chip">' + escapeHtml(lineLabel) + '</span>'
        + '</div>'
        + '</li>';
    }).join('');
  }
  function runQueryDebugRequest(question) {
    var ownerKey = currentOwnerKey();
    if (!ownerKey) {
      return Promise.reject(new Error('Thiếu OWNER_API_KEY — mở Cấu hình server và lưu key trước.'));
    }
    return fetch(currentApiBase().replace(/\/$/, '') + '/query-debug', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-API-Key': ownerKey },
      body: JSON.stringify({ question: question })
    })
      .then(function (res) {
        return res.json().then(function (data) { return { ok: res.ok, data: data }; });
      })
      .then(function (result) {
        if (!result.ok) {
          throw new Error(result.data && result.data.detail ? result.data.detail : 'HTTP error');
        }
        return result.data;
      });
  }
  function runQueryDebug() {
    var questionEl = document.getElementById('retrievalQuestion');
    var plannerEl = document.getElementById('retrievalPlanner');
    var answerEl = document.getElementById('retrievalAnswer');
    var contextEl = document.getElementById('retrievalContext');
    var btn = document.getElementById('retrievalRunBtn');
    if (!questionEl || !plannerEl || !answerEl || !contextEl || !btn) return;
    var question = questionEl.value.trim();
    if (!question) {
      setStatus('Nhập một câu hỏi trước khi chạy.', 'err');
      return;
    }
    var ownerKey = currentOwnerKey();
    if (!ownerKey) {
      setStatus('Thiếu OWNER_API_KEY — mở Cấu hình server và lưu key trước.', 'err');
      return;
    }
    btn.disabled = true;
    setStatus('Đang chạy retrieval debug...', '');
    plannerEl.textContent = 'đang chạy...';
    answerEl.textContent = 'Đang lấy kết quả...';
    contextEl.textContent = 'Đang build context...';
    contextEl.className = 'query-context';
    renderTrace([]);
    runQueryDebugRequest(question)
      .then(function (data) {
        plannerEl.textContent = data.planner_mode || 'unknown';
        answerEl.textContent = data.answer || 'Không có answer.';
        contextEl.textContent = data.context || 'Không có context.';
        if (!data.context) contextEl.className = 'query-context query-empty';
        renderTrace(data.retrieval_trace || []);
        renderStats(data.context_stats || {});
        renderSources(data.chunks || [], data.context_chunk_ids || []);
        setStatus('Đã tải retrieval debug.', 'ok');
      })
      .catch(function (err) {
        plannerEl.textContent = 'lỗi';
        answerEl.textContent = 'Không tải được kết quả.';
        contextEl.textContent = 'Không tải được context.';
        contextEl.className = 'query-context query-empty';
        renderStats({});
        renderSources([], []);
        setStatus('Lỗi: ' + err.message, 'err');
      })
      .finally(function () {
        btn.disabled = false;
      });
  }
  function runMiniBenchmark() {
    var runBtn = document.getElementById('retrievalBenchmarkRunBtn');
    var exportBtn = document.getElementById('retrievalBenchmarkExportBtn');
    var statusEl = document.getElementById('retrievalBenchmarkStatus');
    var logEl = document.getElementById('retrievalBenchmarkLog');
    var cases = [
      { id: 'E05', question: 'QT IT 005' },
      { id: 'M04', question: 'quy trình golive production test sau golive' },
      { id: 'S04', question: 'cần tài liệu gì để UAT release' },
      { id: 'H02', question: 'từ QT.IT.005 sang QT.IT.019 và QT.IT.009 để ra flow từ demand đến golive' }
    ];
    if (!runBtn || !exportBtn || !statusEl || !logEl) return;
    runBtn.disabled = true;
    exportBtn.disabled = true;
    statusEl.textContent = 'Đang chạy benchmark mini...';
    statusEl.className = 'query-status';
    logEl.textContent = 'Đang chạy...';
    logEl.className = 'query-benchmark-log';
    var results = [];
    var chain = Promise.resolve();
    cases.forEach(function (item) {
      chain = chain.then(function () {
        return runQueryDebugRequest(item.question).then(function (data) {
          results.push({
            id: item.id,
            question: item.question,
            planner_mode: data.planner_mode || 'unknown',
            retrieval_trace: data.retrieval_trace || [],
            first_source: data.chunks && data.chunks[0] ? data.chunks[0].source_path : 'n/a',
            context_stats: data.context_stats || {}
          });
        });
      });
    });
    chain
      .then(function () {
        window.__lastRetrievalBenchmark = results;
        logEl.textContent = results.map(function (item) {
          return '[' + item.id + '] ' + item.question + '\\n'
            + 'planner: ' + item.planner_mode + '\\n'
            + 'trace: ' + item.retrieval_trace.join(' -> ') + '\\n'
            + 'top1: ' + item.first_source + '\\n'
            + 'context: ' + (item.context_stats.context_chunks || 0) + '/' + (item.context_stats.candidate_chunks || 0)
            + ' chunks, unique sources ' + (item.context_stats.context_sources || 0) + ', duplicate chunks ' + (item.context_stats.context_duplicate_chunks || 0);
        }).join('\\n\\n');
        statusEl.textContent = 'Đã chạy benchmark mini.';
        statusEl.className = 'query-status ok';
      })
      .catch(function (err) {
        logEl.textContent = 'Lỗi benchmark: ' + err.message;
        logEl.className = 'query-benchmark-log query-empty';
        statusEl.textContent = 'Benchmark mini thất bại.';
        statusEl.className = 'query-status err';
      })
      .finally(function () {
        runBtn.disabled = false;
        exportBtn.disabled = false;
      });
  }
  function exportMiniBenchmark() {
    var results = window.__lastRetrievalBenchmark || [];
    var statusEl = document.getElementById('retrievalBenchmarkStatus');
    if (!results.length) {
      if (statusEl) {
        statusEl.textContent = 'Chưa có benchmark để xuất — chạy benchmark mini trước.';
        statusEl.className = 'query-status err';
      }
      return;
    }
    var lines = [
      '# Retrieval Benchmark Run',
      '',
      '- Benchmark version: `v1`',
      '- Date: `2026-08-07`',
      '- Retrieval mode: `UI mini benchmark via /query-debug`',
      '',
      '| ID | Query | Planner | Trace | Top 1 source |',
      '|---|---|---|---|---|'
    ];
    results.forEach(function (item) {
      lines.push('| ' + item.id + ' | ' + item.question.replace(/\|/g, '\\|') + ' | ' + item.planner_mode + ' | ' + item.retrieval_trace.join(' -> ').replace(/\|/g, '\\|') + ' | ' + item.first_source.replace(/\|/g, '\\|') + ' |');
    });
    lines.push('');
    lines.push('## Context stats');
    lines.push('');
    lines.push('| ID | Context chunks | Candidate chunks | Context sources | Duplicate chunks in context |');
    lines.push('|---|---|---|---|---|');
    results.forEach(function (item) {
      var stats = item.context_stats || {};
      lines.push('| ' + item.id + ' | ' + (stats.context_chunks || 0) + ' | ' + (stats.candidate_chunks || 0) + ' | ' + (stats.context_sources || 0) + ' | ' + (stats.context_duplicate_chunks || 0) + ' |');
    });
    var blob = new Blob([lines.join('\\n')], { type: 'text/markdown;charset=utf-8' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = 'retrieval-benchmark-mini-2026-08-07.md';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    if (statusEl) {
      statusEl.textContent = 'Đã xuất file benchmark mini.';
      statusEl.className = 'query-status ok';
    }
  }
  window.__loadRetrievalDebugInline = function () {
    var runBtn = document.getElementById('retrievalRunBtn');
    var sampleBtn = document.getElementById('retrievalSampleBtn');
    var benchmarkRunBtn = document.getElementById('retrievalBenchmarkRunBtn');
    var benchmarkExportBtn = document.getElementById('retrievalBenchmarkExportBtn');
    var questionEl = document.getElementById('retrievalQuestion');
    if (runBtn && !runBtn.dataset.wired) {
      runBtn.dataset.wired = '1';
      runBtn.addEventListener('click', runQueryDebug);
    }
    if (sampleBtn && !sampleBtn.dataset.wired) {
      sampleBtn.dataset.wired = '1';
      sampleBtn.addEventListener('click', function () {
        if (questionEl) questionEl.value = 'cần tài liệu gì để UAT release';
      });
    }
    if (benchmarkRunBtn && !benchmarkRunBtn.dataset.wired) {
      benchmarkRunBtn.dataset.wired = '1';
      benchmarkRunBtn.addEventListener('click', runMiniBenchmark);
    }
    if (benchmarkExportBtn && !benchmarkExportBtn.dataset.wired) {
      benchmarkExportBtn.dataset.wired = '1';
      benchmarkExportBtn.addEventListener('click', exportMiniBenchmark);
    }
    document.querySelectorAll('.query-preset[data-query-sample]').forEach(function (btn) {
      if (btn.dataset.wired) return;
      btn.dataset.wired = '1';
      btn.addEventListener('click', function () {
        if (!questionEl) return;
        questionEl.value = btn.getAttribute('data-query-sample') || '';
      });
    });
  };
  if (document.getElementById('retrieval-debug') && document.getElementById('retrieval-debug').classList.contains('active')) {
    window.__loadRetrievalDebugInline();
  }
})();

(function () {
  var booted = false;
  function currentApiBase() {
    var input = document.getElementById('syncApiBase');
    return (input && input.value.trim()) || localStorage.getItem('dashboardApiBase') || window.location.origin || 'http://127.0.0.1:8000';
  }
  function currentOwnerKey() {
    var input = document.getElementById('syncOwnerKey');
    return (input && input.value.trim()) || localStorage.getItem('dashboardOwnerKey') || localStorage.getItem('graphrag_owner_key') || '';
  }
  function escapeHtml(text) {
    return String(text || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
  function setStatus(text, kind) {
    var el = document.getElementById('knowledgeLinksStatus');
    if (!el) return;
    el.textContent = text;
    el.className = 'links-status' + (kind ? ' ' + kind : '');
  }
  function ensurePanel() {
    var panel = document.getElementById('knowledge-links');
    if (!panel) {
      var content = document.querySelector('.content');
      if (content) {
        panel = document.createElement('section');
        panel.id = 'knowledge-links';
        panel.className = 'lane-panel';
        panel.innerHTML =
          '<div class="links-shell">' +
          '<div class="links-hero"><div><h2>Knowledge links theo backlog</h2><p class="links-intro">Duyệt liên kết tri thức được suy diễn quyết định từ metadata sẵn có: project, backlog/ticket, people, source channel và gợi ý quy trình QT.IT.005 / 009 / 019. Màn này giúp kiểm tra nhanh một backlog đang nối với tài liệu và luồng quy trình nào.</p></div><span class="links-badge">Phase 3</span></div>' +
          '<div id="knowledgeLinksStatus" class="links-status">Dùng OWNER_API_KEY ở Cấu hình server để tải linked knowledge.</div>' +
          '<div class="links-toolbar">' +
          '<div class="links-filter"><label for="knowledgeLinksSearch">Từ khóa</label><input id="knowledgeLinksSearch" class="links-filter-search" type="search" placeholder="Ví dụ: QLYC-11886, MSBPay, UAT, Hà..."></div>' +
          '<div class="links-filter"><label for="knowledgeLinksProcess">Quy trình</label><select id="knowledgeLinksProcess" class="links-filter-select"><option value="">Tất cả quy trình</option></select></div>' +
          '<div class="links-filter"><label for="knowledgeLinksProject">Project</label><select id="knowledgeLinksProject" class="links-filter-select"><option value="">Tất cả project</option></select></div>' +
          '<div id="knowledgeLinksMeta" class="links-meta">Chưa tải dữ liệu.</div>' +
          '</div>' +
          '<div id="knowledgeLinksSummary" class="links-summary"></div>' +
          '<div class="links-grid">' +
          '<section class="links-card"><div class="query-block-title">Backlog / ticket anchors</div><div id="knowledgeTickets" class="links-list"><div class="links-empty">Chưa tải dữ liệu.</div></div></section>' +
          '<section class="links-card"><div class="query-block-title">Project anchors</div><div id="knowledgeProjects" class="links-list"><div class="links-empty">Chưa tải dữ liệu.</div></div></section>' +
          '<section class="links-card"><div class="query-block-title">People anchors</div><div id="knowledgePeople" class="links-list"><div class="links-empty">Chưa tải dữ liệu.</div></div></section>' +
          '</div>' +
          '<div class="links-grid" style="grid-template-columns: 1.2fr 0.8fr;">' +
          '<section class="links-card links-detail"><div><div class="query-block-title">Anchor detail</div><div id="knowledgeDetailTitle" class="query-answer"></div><div id="knowledgeDetailMeta" class="query-mini-label" style="margin-top:6px;"></div></div><div id="knowledgeDetailChips" class="links-chips"><span class="links-empty">Chưa chọn anchor nào.</span></div><div id="knowledgeDetailDocs" class="links-docs"><div class="links-empty">Chưa có document nào để hiển thị.</div></div></section>' +
          '<section class="links-card"><div class="query-block-title">Backlog docs gần đây</div><div id="knowledgeBacklogDocs" class="links-backlog-list"><div class="links-empty">Chưa tải dữ liệu.</div></div></section>' +
          '</div>' +
          '</div>';
        content.appendChild(panel);
      }
    }
    panel = document.getElementById('knowledge-links');
    if (panel) {
      var shell = panel.querySelector('.links-shell');
      var grids = shell ? shell.querySelectorAll('.links-grid') : [];
      if (shell && grids.length >= 2) {
        var primaryGrid = grids[0];
        var secondaryGrid = grids[1];
        if (!document.getElementById('knowledgeChannels')) {
          var channelCard = document.createElement('section');
          channelCard.className = 'links-card';
          channelCard.innerHTML = '<div class="query-block-title">Source channel anchors</div><div id="knowledgeChannels" class="links-list"><div class="links-empty">Chua tai du lieu.</div></div>';
          primaryGrid.appendChild(channelCard);
        }
        if (!document.getElementById('knowledgeMonths')) {
          var monthCard = document.createElement('section');
          monthCard.className = 'links-card';
          monthCard.innerHTML = '<div class="query-block-title">Timeline theo thang</div><div id="knowledgeMonths" class="links-list"><div class="links-empty">Chua tai du lieu.</div></div>';
          primaryGrid.appendChild(monthCard);
        }
        if (!document.getElementById('knowledgeAnchorCue')) {
          var cueCard = document.createElement('section');
          cueCard.className = 'links-card';
          cueCard.innerHTML = '<div class="query-block-title">Anchor cue</div><div id="knowledgeAnchorCue" class="links-docs"><div class="links-empty">Chua chon anchor nao de xem cue.</div></div>';
          secondaryGrid.appendChild(cueCard);
        }
        if (!document.getElementById('knowledgeBacklinks')) {
          var backlinkCard = document.createElement('section');
          backlinkCard.className = 'links-card';
          backlinkCard.innerHTML = '<div class="query-block-title">Backlink summary</div><div id="knowledgeBacklinks" class="links-related-list"><div class="links-empty">Chua co du lieu backlink.</div></div>';
          secondaryGrid.appendChild(backlinkCard);
        }
        if (!document.getElementById('knowledgeRelatedDocs')) {
          var relatedCard = document.createElement('section');
          relatedCard.className = 'links-card';
          relatedCard.innerHTML = '<div class="query-block-title">Related documents</div><div id="knowledgeRelatedDocs" class="links-related-list"><div class="links-empty">Chua co goi y lien quan.</div></div>';
          secondaryGrid.appendChild(relatedCard);
        }
        if (!document.getElementById('knowledgeDocumentDetail')) {
          var docDetailCard = document.createElement('section');
          docDetailCard.className = 'links-card';
          docDetailCard.innerHTML = '<div class="query-block-title">Document detail</div><div id="knowledgeDocumentDetail" class="links-docs"><div class="links-empty">Chua mo document nao.</div></div>';
          secondaryGrid.appendChild(docDetailCard);
        }
      }
    }
    if (!document.querySelector('.side-item[data-target="knowledge-links"]')) {
      var nav = document.querySelector('.sidebar-nav');
      var manageBtn = document.querySelector('.side-item[data-target="manage-users"]');
      if (nav) {
        var btn = document.createElement('button');
        btn.className = 'side-item';
        btn.dataset.target = 'knowledge-links';
        btn.innerHTML = '<span class="side-icon">🧭</span>Khám phá (Project/Ticket/Người/Quy trình)';
        if (manageBtn && manageBtn.parentNode === nav) nav.insertBefore(btn, manageBtn);
        else nav.appendChild(btn);
        if (typeof wireSideItem === 'function') wireSideItem(btn);
      }
    }
  }
  function fetchJson(path) {
    var ownerKey = currentOwnerKey();
    if (!ownerKey) {
      return Promise.reject(new Error('Thiếu OWNER_API_KEY — mở Cấu hình server và lưu key trước.'));
    }
    return fetch(currentApiBase().replace(/\/$/, '') + path, {
      headers: { 'X-API-Key': ownerKey }
    }).then(function (res) {
      return res.json().then(function (data) { return { ok: res.ok, data: data }; });
    }).then(function (result) {
      if (!result.ok) {
        throw new Error(result.data && result.data.detail ? result.data.detail : 'HTTP error');
      }
      return result.data;
    });
  }
  function renderSummary(summary) {
    var root = document.getElementById('knowledgeLinksSummary');
    if (!root) return;
    var cards = [
      ['documents', summary.documents || 0],
      ['projects', summary.projects || 0],
      ['tickets', summary.tickets || 0],
      ['people', summary.people || 0],
      ['channels', summary.channels || 0],
      ['months', summary.months || 0],
      ['backlog docs', summary.backlog_documents || 0]
    ];
    root.innerHTML = cards.map(function (item) {
      return '<div class="links-card"><div class="query-mini-label">' + escapeHtml(item[0]) + '</div><strong>' + item[1] + '</strong></div>';
    }).join('');
  }
  function normalizeKnowledgeText(text) {
    return String(text || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/\s+/g, ' ').trim().toLowerCase();
  }
  function renderSummaryFromData(data) {
    renderSummary({
      documents: (data.backlog_documents || []).length,
      projects: (data.projects || []).length,
      tickets: (data.tickets || []).length,
      people: (data.people || []).length,
      channels: (data.channels || []).length,
      months: (data.months || []).length,
      backlog_documents: (data.backlog_documents || []).length
    });
  }
  function populateKnowledgeFilterOptions(data) {
    var processEl = document.getElementById('knowledgeLinksProcess');
    var projectEl = document.getElementById('knowledgeLinksProject');
    if (!processEl || !projectEl) return;
    var processes = {};
    var projects = {};
    []
      .concat(data.projects || [])
      .concat(data.tickets || [])
      .concat(data.people || [])
      .concat(data.channels || [])
      .concat(data.months || [])
      .concat(data.backlog_documents || [])
      .forEach(function (item) {
        (item.process_codes || []).forEach(function (code) { processes[code] = true; });
        if (item.name) { projects[item.name] = true; }
        if (item.project) { projects[item.project] = true; }
        (item.projects || []).forEach(function (project) { projects[project] = true; });
      });
    processEl.innerHTML = '<option value="">Tất cả quy trình</option>' + Object.keys(processes).sort().map(function (code) {
      return '<option value="' + escapeHtml(code) + '">' + escapeHtml(code) + '</option>';
    }).join('');
    projectEl.innerHTML = '<option value="">Tất cả project</option>' + Object.keys(projects).sort().map(function (project) {
      return '<option value="' + escapeHtml(project) + '">' + escapeHtml(project) + '</option>';
    }).join('');
  }
  function currentKnowledgeFilters() {
    var searchEl = document.getElementById('knowledgeLinksSearch');
    var processEl = document.getElementById('knowledgeLinksProcess');
    var projectEl = document.getElementById('knowledgeLinksProject');
    return {
      search: normalizeKnowledgeText(searchEl && searchEl.value),
      process: processEl && processEl.value || '',
      project: projectEl && projectEl.value || ''
    };
  }
  function knowledgeItemMatches(item, filters) {
    var processCodes = item.process_codes || [];
    var projects = item.projects || (item.project ? [item.project] : (item.name ? [item.name] : []));
    if (filters.process && processCodes.indexOf(filters.process) === -1) return false;
    if (filters.project && projects.indexOf(filters.project) === -1) return false;
    if (!filters.search) return true;
    var haystack = normalizeKnowledgeText([
      item.id,
      item.name,
      item.project,
      item.source_path,
      item.source_channel,
      item.month_bucket,
      (item.projects || []).join(' '),
      (item.source_channels || []).join(' '),
      (item.ticket_ids || []).join(' '),
      (item.process_codes || []).join(' '),
      (item.people_mentions || []).join(' ')
    ].join(' '));
    return haystack.indexOf(filters.search) !== -1;
  }
  function applyKnowledgeFilters() {
    var raw = window.__knowledgeLinksData;
    if (!raw) return;
    var filters = currentKnowledgeFilters();
    var filtered = {
      projects: (raw.projects || []).filter(function (item) { return knowledgeItemMatches(item, filters); }),
      tickets: (raw.tickets || []).filter(function (item) { return knowledgeItemMatches(item, filters); }),
      people: (raw.people || []).filter(function (item) { return knowledgeItemMatches(item, filters); }),
      channels: (raw.channels || []).filter(function (item) { return knowledgeItemMatches(item, filters); }),
      months: (raw.months || []).filter(function (item) { return knowledgeItemMatches(item, filters); }),
      backlog_documents: (raw.backlog_documents || []).filter(function (item) { return knowledgeItemMatches(item, filters); })
    };
    renderSummaryFromData(filtered);
    renderAnchorList('knowledgeTickets', filtered.tickets, 'ticket', function (item) {
      return item.doc_count + ' docs | ' + (item.projects || []).join(', ');
    });
    renderAnchorList('knowledgeProjects', filtered.projects, 'project', function (item) {
      return item.backlog_doc_count + ' backlog docs | ' + item.doc_count + ' docs';
    });
    renderAnchorList('knowledgePeople', filtered.people, 'person', function (item) {
      return item.doc_count + ' docs | ' + item.ticket_count + ' tickets';
    });
    renderAnchorList('knowledgeChannels', filtered.channels, 'channel', function (item) {
      return item.doc_count + ' docs | ' + item.ticket_count + ' tickets';
    });
    renderAnchorList('knowledgeMonths', filtered.months, 'month', function (item) {
      return item.doc_count + ' docs | ' + (item.source_channels || []).slice(0, 3).join(', ');
    });
    renderBacklogDocs(filtered.backlog_documents);
    var meta = document.getElementById('knowledgeLinksMeta');
    if (meta) {
      meta.textContent = 'Hiển thị ' + filtered.tickets.length + ' ticket, ' + filtered.projects.length + ' project, ' + filtered.people.length + ' people, ' + filtered.channels.length + ' channel, ' + filtered.months.length + ' month';
    }
    wireAnchorButtons();
  }
  function wireKnowledgeFilters() {
    var searchEl = document.getElementById('knowledgeLinksSearch');
    var processEl = document.getElementById('knowledgeLinksProcess');
    var projectEl = document.getElementById('knowledgeLinksProject');
    [searchEl, processEl, projectEl].forEach(function (el) {
      if (!el || el.dataset.wired) return;
      el.dataset.wired = '1';
      el.addEventListener(el.tagName === 'INPUT' ? 'input' : 'change', applyKnowledgeFilters);
    });
  }
  function renderAnchorList(rootId, items, anchorType, labelBuilder) {
    var root = document.getElementById(rootId);
    if (!root) return;
    if (!items || !items.length) {
      root.innerHTML = '<div class="links-empty">Chưa có anchor nào.</div>';
      return;
    }
    root.innerHTML = items.map(function (item, idx) {
      var anchorId = anchorType === 'ticket' ? item.id : item.name;
      return '<button type="button" class="links-item" data-anchor-type="' + anchorType + '" data-anchor-id="' + escapeHtml(anchorId) + '"' + (idx === 0 ? ' data-default-anchor="1"' : '') + '>'
        + '<strong>' + escapeHtml(anchorId) + '</strong>'
        + '<span>' + escapeHtml(labelBuilder(item)) + '</span>'
        + '</button>';
    }).join('');
  }
  function renderBacklogDocs(items) {
    var root = document.getElementById('knowledgeBacklogDocs');
    if (!root) return;
    if (!items || !items.length) {
      root.innerHTML = '<div class="links-empty">Chưa có backlog/doc anchor nào.</div>';
      return;
    }
    root.innerHTML = items.map(function (item) {
      return '<div class="links-backlog-item">'
        + '<strong>' + escapeHtml(item.source_path) + '</strong>'
        + '<span>' + escapeHtml((item.project || 'general') + ' | ' + (item.date || 'n/a')) + '</span>'
        + '<span>' + escapeHtml((item.ticket_ids || []).join(', ') || 'không có ticket id') + '</span>'
        + '<span>' + escapeHtml((item.process_codes || []).join(', ') || 'chưa map quy trình') + '</span>'
        + '</div>';
    }).join('');
  }
  function renderDocumentOpenButton(sourcePath) {
    return '<div class="links-doc-action"><button type="button" class="links-doc-btn" data-doc-source-path="' + escapeHtml(sourcePath) + '">Open detail</button></div>';
  }
  function renderDocumentDetail(data) {
    var root = document.getElementById('knowledgeDocumentDetail');
    if (!root) return;
    var flow = data.process_flow || {};
    root.innerHTML =
      '<div class="links-doc">'
      + '<strong>' + escapeHtml(data.source_path || 'n/a') + '</strong>'
      + '<div class="links-doc-meta">'
      + '<span class="links-chip">' + escapeHtml(data.project || 'general') + '</span>'
      + '<span class="links-chip">' + escapeHtml(data.doc_type || 'n/a') + '</span>'
      + '<span class="links-chip">' + escapeHtml(data.date || 'n/a') + '</span>'
      + '<span class="links-chip">' + escapeHtml(data.source_channel || 'n/a') + '</span>'
      + '<span class="links-chip">' + escapeHtml(data.reliability || 'n/a') + '</span>'
      + '<span class="links-chip">lines: ' + escapeHtml(data.line_count || 0) + '</span>'
      + '</div>'
      + '<div class="links-doc-meta">'
      + ((data.ticket_ids || []).map(function (ticket) { return '<span class="links-chip">' + escapeHtml(ticket) + '</span>'; }).join(''))
      + ((data.people_mentions || []).slice(0, 8).map(function (person) { return '<span class="links-chip">' + escapeHtml(person) + '</span>'; }).join(''))
      + ((data.process_codes || []).map(function (code) { return '<span class="links-chip">' + escapeHtml(code) + '</span>'; }).join(''))
      + '<span class="links-chip">coverage: ' + escapeHtml(flow.coverage_ratio || '0/3') + '</span>'
      + '</div>'
        + '</div>'
        + '<div class="links-preview">' + escapeHtml(data.content_preview || '') + (data.content_truncated ? String.fromCharCode(10, 10) + '[preview truncated]' : '') + '</div>';
  }
  function activateKnowledgeLinksPanel() {
    var btn = document.querySelector('.side-item[data-target="knowledge-links"]');
    var panel = document.getElementById('knowledge-links');
    if (btn && panel) {
      document.querySelectorAll('.side-item, .common-item').forEach(function (b) { b.classList.remove('active'); });
      document.querySelectorAll('.lane-panel').forEach(function (p) { p.classList.remove('active'); });
      btn.classList.add('active');
      panel.classList.add('active');
      if (window.__loadKnowledgeLinksInline) {
        window.__loadKnowledgeLinksInline();
      }
    }
  }
  function openKnowledgeDocumentDetail(sourcePath) {
    if (!sourcePath) return;
    ensurePanel();
    activateKnowledgeLinksPanel();
    setStatus('Dang tai document detail...', '');
    fetchJson('/knowledge-links/document?source_path=' + encodeURIComponent(sourcePath))
      .then(function (data) {
        renderDocumentDetail(data);
        setStatus('Da tai document detail.', 'ok');
      })
      .catch(function (err) {
        setStatus('Loi document detail: ' + err.message, 'err');
      });
  }
  function openKnowledgeAnchorDetail(anchorType, anchorId) {
    if (!anchorType || !anchorId) return;
    window.__knowledgeLinksSelected = { type: anchorType, id: anchorId };
    document.querySelectorAll('.links-item[data-anchor-type]').forEach(function (el) {
      var sameType = el.getAttribute('data-anchor-type') === anchorType;
      var sameId = el.getAttribute('data-anchor-id') === anchorId;
      el.classList.toggle('active', sameType && sameId);
    });
    ensurePanel();
    setStatus('Dang tai chi tiet ' + anchorType + '...', '');
    fetchJson('/knowledge-links/detail?anchor_type=' + encodeURIComponent(anchorType) + '&anchor_id=' + encodeURIComponent(anchorId))
      .then(function (data) {
        renderDetail(data);
        setStatus('Da tai knowledge links.', 'ok');
      })
      .catch(function (err) {
        setStatus('Loi: ' + err.message, 'err');
      });
  }
  function wireDocumentButtons() {
    document.querySelectorAll('[data-doc-source-path]').forEach(function (btn) {
      if (btn.dataset.wired) return;
      btn.dataset.wired = '1';
      btn.addEventListener('click', function () {
          var sourcePath = btn.getAttribute('data-doc-source-path');
          openKnowledgeDocumentDetail(sourcePath);
      });
    });
  }
  function renderDetail(data) {
    var hero = document.getElementById('knowledgeDetailTitle');
    var meta = document.getElementById('knowledgeDetailMeta');
    var chips = document.getElementById('knowledgeDetailChips');
    var docs = document.getElementById('knowledgeDetailDocs');
    var cue = document.getElementById('knowledgeAnchorCue');
    var backlinksRoot = document.getElementById('knowledgeBacklinks');
    var relatedRoot = document.getElementById('knowledgeRelatedDocs');
    if (!hero || !meta || !chips || !docs) return;
    hero.textContent = data.anchor_type + ': ' + data.anchor_id;
    var flow = data.process_flow || {};
    meta.textContent = 'docs: ' + (data.doc_count || 0) + ' | latest: ' + (data.latest_date || 'n/a') + ' | process coverage: ' + (flow.coverage_ratio || '0/3');

    var chipValues = []
      .concat((data.related_process_codes || []).map(function (item) { return 'process: ' + item; }))
      .concat((data.related_projects || []).map(function (item) { return 'project: ' + item; }))
      .concat((data.related_tickets || []).slice(0, 12).map(function (item) { return 'ticket: ' + item; }))
      .concat((data.related_source_channels || []).map(function (item) { return 'channel: ' + item; }))
      .concat((data.related_months || []).map(function (item) { return 'month: ' + item; }));
    if (flow.covered && flow.covered.length) {
      chipValues = chipValues.concat(flow.covered.map(function (item) { return 'covered: ' + item; }));
    }
    if (flow.missing && flow.missing.length) {
      chipValues = chipValues.concat(flow.missing.map(function (item) { return 'missing: ' + item; }));
    }
    chips.innerHTML = chipValues.length ? chipValues.map(function (item) {
      return '<span class="links-chip">' + escapeHtml(item) + '</span>';
    }).join('') : '<span class="links-empty">Chưa có anchor liên quan.</span>';

    var gapHtml = '';
    if (flow.gap_hints && flow.gap_hints.length) {
      gapHtml = flow.gap_hints.map(function (entry) {
        var hints = (entry.hints || []).map(function (hint) {
          return '<li>' + escapeHtml(hint) + '</li>';
        }).join('');
        return '<div class="links-gap-box">'
          + '<strong>Thiếu evidence cho ' + escapeHtml(entry.code) + '</strong>'
          + '<ul>' + hints + '</ul>'
          + '</div>';
      }).join('');
    }

    var docsHtml = (data.documents || []).length ? data.documents.map(function (item) {
      return '<div class="links-doc">'
        + '<strong>' + escapeHtml(item.source_path) + '</strong>'
        + '<div class="links-doc-meta">'
        + '<span class="links-chip">' + escapeHtml(item.project || 'general') + '</span>'
        + '<span class="links-chip">' + escapeHtml(item.doc_type || 'n/a') + '</span>'
        + '<span class="links-chip">' + escapeHtml(item.date || 'n/a') + '</span>'
        + '<span class="links-chip">' + escapeHtml(item.source_channel || 'n/a') + '</span>'
        + '<span class="links-chip">' + escapeHtml(item.reliability || 'n/a') + '</span>'
        + '</div>'
        + '<div class="links-doc-meta">'
        + ((item.ticket_ids || []).map(function (ticket) { return '<span class="links-chip">' + escapeHtml(ticket) + '</span>'; }).join(''))
        + ((item.process_codes || []).map(function (code) { return '<span class="links-chip">' + escapeHtml(code) + '</span>'; }).join(''))
        + '</div>'
        + renderDocumentOpenButton(item.source_path)
        + '</div>';
    }).join('') : '<div class="links-empty">Không có document nào khớp anchor này.</div>';

    docs.innerHTML = gapHtml + docsHtml;
    if (cue) {
      cue.innerHTML =
        '<div class="links-doc">'
        + '<strong>Anchor summary</strong>'
        + '<div class="links-doc-meta">'
        + '<span class="links-chip">' + escapeHtml(data.anchor_type || 'n/a') + '</span>'
        + '<span class="links-chip">' + escapeHtml(data.anchor_id || 'n/a') + '</span>'
        + '<span class="links-chip">docs: ' + escapeHtml(data.doc_count || 0) + '</span>'
        + '</div>'
        + '</div>'
        + '<div class="links-doc">'
        + '<strong>Coverage cue</strong>'
        + '<div class="links-doc-meta">'
        + '<span class="links-chip">latest: ' + escapeHtml(data.latest_date || 'n/a') + '</span>'
        + '<span class="links-chip">coverage: ' + escapeHtml(flow.coverage_ratio || '0/3') + '</span>'
        + '<span class="links-chip">missing: ' + escapeHtml((flow.missing || []).join(', ') || 'none') + '</span>'
        + '</div>'
        + '</div>';
    }
    if (backlinksRoot) {
      var backlinkGroups = data.backlink_groups || [];
      backlinksRoot.innerHTML = backlinkGroups.length ? backlinkGroups.map(function (group) {
        return '<div class="links-backlink-group">'
          + '<strong>' + escapeHtml(group.label || 'group') + '</strong>'
          + '<div class="links-doc-meta">'
          + (group.items || []).map(function (item) {
            return '<span class="links-chip">' + escapeHtml(item.id) + ' (' + escapeHtml(item.count) + ')</span>';
          }).join('')
          + '</div>'
          + '</div>';
      }).join('') : '<div class="links-empty">Chua co backlink nao du de hien thi.</div>';
    }
    if (relatedRoot) {
      var relatedDocs = data.related_documents || [];
      relatedRoot.innerHTML = relatedDocs.length ? relatedDocs.map(function (item) {
        return '<div class="links-doc">'
          + '<strong>' + escapeHtml(item.source_path) + '</strong>'
          + '<div class="links-doc-meta">'
          + '<span class="links-chip">score: ' + escapeHtml(item.related_score || 0) + '</span>'
          + '<span class="links-chip">' + escapeHtml(item.project || 'general') + '</span>'
          + '<span class="links-chip">' + escapeHtml(item.date || 'n/a') + '</span>'
          + '</div>'
          + '<div class="links-related-reasons">'
          + (item.reasons || []).map(function (reason) {
            return '<div class="links-related-reason">' + escapeHtml(reason) + '</div>';
          }).join('')
          + '</div>'
          + renderDocumentOpenButton(item.source_path)
          + '</div>';
      }).join('') : '<div class="links-empty">Chua co tai lieu lien quan nao ngoai anchor nay.</div>';
    }
    wireDocumentButtons();
  }
  function wireAnchorButtons() {
    document.querySelectorAll('.links-item[data-anchor-type]').forEach(function (btn) {
      if (btn.dataset.wired) return;
      btn.dataset.wired = '1';
      btn.addEventListener('click', function () {
        window.__knowledgeLinksSelected = {
          type: btn.getAttribute('data-anchor-type'),
          id: btn.getAttribute('data-anchor-id')
        };
        document.querySelectorAll('.links-item[data-anchor-type]').forEach(function (el) { el.classList.remove('active'); });
        btn.classList.add('active');
        var anchorType = btn.getAttribute('data-anchor-type');
        var anchorId = btn.getAttribute('data-anchor-id');
        openKnowledgeAnchorDetail(anchorType, anchorId);
      });
    });
    var selected = window.__knowledgeLinksSelected;
    if (selected && window.CSS && typeof window.CSS.escape === 'function') {
      var selectedBtn = document.querySelector('.links-item[data-anchor-type="' + selected.type + '"][data-anchor-id="' + window.CSS.escape(selected.id) + '"]');
      if (selectedBtn) {
        selectedBtn.classList.add('active');
        return;
      }
    }
    var first = document.querySelector('.links-item[data-default-anchor="1"]');
    if (first) {
      first.click();
    }
  }
  function loadKnowledgeLinksInline() {
    ensurePanel();
    if (booted && document.getElementById('knowledgeLinksSummary') && document.getElementById('knowledgeLinksSummary').children.length) {
      return;
    }
    booted = true;
    setStatus('Đang tải linked knowledge...', '');
    fetchJson('/knowledge-links')
      .then(function (data) {
        window.__knowledgeLinksData = data;
        populateKnowledgeFilterOptions(data);
        wireKnowledgeFilters();
        applyKnowledgeFilters();
        setStatus('Đã tải linked knowledge theo backlog / ticket / project / person.', 'ok');
      })
      .catch(function (err) {
        setStatus('Lỗi: ' + err.message, 'err');
      });
  }
  function triggerKnowledgeLinksLoad() {
    setTimeout(function () {
      var panel = document.getElementById('knowledge-links');
      if (panel && panel.classList.contains('active')) {
        loadKnowledgeLinksInline();
      }
    }, 0);
  }
  window.__loadKnowledgeLinksInline = loadKnowledgeLinksInline;
  window.__openKnowledgeDocumentDetail = openKnowledgeDocumentDetail;
  window.__openKnowledgeAnchorDetail = openKnowledgeAnchorDetail;
  ensurePanel();
  document.addEventListener('click', function (event) {
    var btn = event.target && event.target.closest ? event.target.closest('.side-item[data-target="knowledge-links"]') : null;
    if (!btn) return;
    triggerKnowledgeLinksLoad();
  });
  var panelEl = document.getElementById('knowledge-links');
  if (panelEl && typeof MutationObserver !== 'undefined') {
    new MutationObserver(function () {
      if (panelEl.classList.contains('active')) {
        triggerKnowledgeLinksLoad();
      }
    }).observe(panelEl, { attributes: true, attributeFilter: ['class'] });
  }
  setInterval(function () {
    var panel = document.getElementById('knowledge-links');
    var summary = document.getElementById('knowledgeLinksSummary');
    if (!panel || !panel.classList.contains('active')) return;
    if (!summary || !summary.children.length) {
      triggerKnowledgeLinksLoad();
    }
  }, 1500);
  if (document.getElementById('knowledge-links') && document.getElementById('knowledge-links').classList.contains('active')) {
    triggerKnowledgeLinksLoad();
  }
  })();
  
  (function () {
    var booted = false;
    function currentApiBase() {
      var input = document.getElementById('syncApiBase');
      return (input && input.value.trim()) || localStorage.getItem('dashboardApiBase') || window.location.origin || 'http://127.0.0.1:8000';
    }
    function currentOwnerKey() {
      var input = document.getElementById('syncOwnerKey');
      return (input && input.value.trim()) || localStorage.getItem('dashboardOwnerKey') || localStorage.getItem('graphrag_owner_key') || '';
    }
    function escapeHtml(value) {
      return String(value == null ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    }
    function fetchJson(path) {
      var headers = {};
      var ownerKey = currentOwnerKey();
      if (ownerKey) headers['x-api-key'] = ownerKey;
      return fetch(currentApiBase().replace(/\/$/, '') + path, { headers: headers }).then(function (resp) {
        if (!resp.ok) {
          return resp.text().then(function (text) { throw new Error(text || ('HTTP ' + resp.status)); });
        }
        return resp.json();
      });
    }
    function populateSelect(id, items, selected) {
      var el = document.getElementById(id);
      if (!el) return;
      var current = selected != null ? selected : el.value;
      var options = ['<option value="">Tất cả</option>'].concat((items || []).map(function (item) {
        var value = String(item || '');
        return '<option value="' + escapeHtml(value) + '"' + (value === current ? ' selected' : '') + '>' + escapeHtml(value) + '</option>';
      }));
      el.innerHTML = options.join('');
    }
    function renderResults(items) {
      var root = document.getElementById('documentSearchResults');
      if (!root) return;
      if (!items || !items.length) {
        root.innerHTML = '<div class="search-empty">Không có kết quả khớp với bộ lọc hiện tại.</div>';
        return;
      }
      root.innerHTML = items.map(function (item) {
        var meta = [
          item.project || 'general',
          item.doc_type || 'n/a',
          item.date || 'n/a',
          item.source_channel || 'n/a',
          item.reliability || 'n/a',
          'lines: ' + (item.line_count || 0)
        ];
        var chips = meta.map(function (entry) { return '<span class="search-chip">' + escapeHtml(entry) + '</span>'; }).join('')
          + (item.ticket_ids || []).map(function (ticket) { return '<span class="search-chip">' + escapeHtml(ticket) + '</span>'; }).join('')
          + (item.people_mentions || []).map(function (person) { return '<span class="search-chip">' + escapeHtml(person) + '</span>'; }).join('');
        return '<article class="search-result">'
          + '<strong>' + escapeHtml(item.source_path || 'n/a') + '</strong>'
          + '<div class="search-meta">' + chips + '</div>'
          + '<div class="search-snippet">' + escapeHtml(item.snippet || '') + '</div>'
          + '<div class="links-doc-action"><button type="button" class="links-doc-btn" data-search-doc-source-path="' + escapeHtml(item.source_path || '') + '">Open detail</button></div>'
          + '</article>';
      }).join('');
    }
    function updateSummary(text, kind) {
      var el = document.getElementById('documentSearchSummary');
      if (!el) return;
      el.textContent = text;
      el.className = 'search-summary' + (kind ? ' ' + kind : '');
    }
    function collectParams() {
      var form = document.getElementById('documentSearchForm');
      var data = new FormData(form);
      var params = new URLSearchParams();
      ['q', 'project', 'source_channel', 'reliability', 'ticket', 'person', 'date_from', 'date_to'].forEach(function (key) {
        var value = String(data.get(key) || '').trim();
        if (value) params.set(key, value);
      });
      params.set('limit', '40');
      return params;
    }
    function loadDocumentSearch(force) {
      var resultsRoot = document.getElementById('documentSearchResults');
      if (!force && booted && resultsRoot && resultsRoot.children.length && !resultsRoot.querySelector('.search-empty')) return;
      booted = true;
      updateSummary('Đang tải Search...', '');
      var params = collectParams();
      fetchJson('/document-search?' + params.toString())
        .then(function (data) {
          var filters = data.available_filters || {};
          var applied = data.applied_filters || {};
          populateSelect('searchProject', filters.projects || [], applied.project || '');
          populateSelect('searchChannel', filters.source_channels || [], applied.source_channel || '');
          populateSelect('searchReliability', filters.reliability || [], applied.reliability || '');
          populateSelect('searchTicket', filters.tickets || [], applied.ticket || '');
          populateSelect('searchPerson', filters.people || [], applied.person || '');
          renderResults(data.results || []);
          var summary = data.summary || {};
          updateSummary('Kết quả: ' + (summary.result_count || 0) + '/' + (summary.matched_before_limit || 0) + ' match, tổng kho ' + (summary.total_documents || 0) + ' docs.', 'ok');
        })
        .catch(function (err) {
          renderResults([]);
          updateSummary('Lỗi: ' + err.message, 'err');
        });
    }
    window.__loadDocumentSearchInline = loadDocumentSearch;
    document.addEventListener('submit', function (event) {
      if (event.target && event.target.id === 'documentSearchForm') {
        event.preventDefault();
        loadDocumentSearch(true);
      }
    });
    document.addEventListener('click', function (event) {
      var btn = event.target && event.target.closest ? event.target.closest('.side-item[data-target="document-search"]') : null;
      if (btn) {
        setTimeout(function () { loadDocumentSearch(false); }, 0);
        return;
      }
      var openBtn = event.target && event.target.closest ? event.target.closest('[data-search-doc-source-path]') : null;
      if (!openBtn) return;
      var sourcePath = openBtn.getAttribute('data-search-doc-source-path');
      var linksBtn = document.querySelector('.side-item[data-target="knowledge-links"]');
      if (linksBtn) linksBtn.click();
      setTimeout(function () {
        if (window.__openKnowledgeDocumentDetail) {
          window.__openKnowledgeDocumentDetail(sourcePath);
        }
      }, 60);
    });
    var panelEl = document.getElementById('document-search');
    if (panelEl && typeof MutationObserver !== 'undefined') {
      new MutationObserver(function () {
        if (panelEl.classList.contains('active')) loadDocumentSearch(false);
      }).observe(panelEl, { attributes: true, attributeFilter: ['class'] });
    }
  })();
  
  (function () {
    var booted = false;
    function currentApiBase() {
      var input = document.getElementById('syncApiBase');
      return (input && input.value.trim()) || localStorage.getItem('dashboardApiBase') || window.location.origin || 'http://127.0.0.1:8000';
    }
    function currentOwnerKey() {
      var input = document.getElementById('syncOwnerKey');
      return (input && input.value.trim()) || localStorage.getItem('dashboardOwnerKey') || localStorage.getItem('graphrag_owner_key') || '';
    }
    function escapeHtml(value) {
      return String(value == null ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    }
    function fetchJson(path) {
      var headers = {};
      var ownerKey = currentOwnerKey();
      if (ownerKey) headers['x-api-key'] = ownerKey;
      return fetch(currentApiBase().replace(/\/$/, '') + path, { headers: headers }).then(function (resp) {
        if (!resp.ok) {
          return resp.text().then(function (text) { throw new Error(text || ('HTTP ' + resp.status)); });
        }
        return resp.json();
      });
    }
    function updateSummary(text) {
      var el = document.getElementById('knowledgeNavigatorSummary');
      if (el) el.textContent = text;
    }
    function populateProjectFilter(projects, selected) {
      var el = document.getElementById('knowledgeNavProjectFilter');
      if (!el) return;
      var current = selected != null ? selected : el.value;
      el.innerHTML = ['<option value="">Tất cả</option>'].concat((projects || []).map(function (item) {
        var value = item.name || '';
        return '<option value="' + escapeHtml(value) + '"' + (value === current ? ' selected' : '') + '>' + escapeHtml(value) + '</option>';
      })).join('');
    }
    function matchesText(text, keyword) {
      if (!keyword) return true;
      return String(text || '').toLowerCase().indexOf(keyword) >= 0;
    }
    function inProject(item, project) {
      if (!project) return true;
      if (item.name === project) return true;
      if (item.project === project) return true;
      return Array.isArray(item.projects) && item.projects.indexOf(project) >= 0;
    }
    function renderAnchorList(rootId, items, anchorType, subtitle) {
      var root = document.getElementById(rootId);
      if (!root) return;
      if (!items || !items.length) {
        root.innerHTML = '<div class="nav-empty">Không có anchor khớp bộ lọc.</div>';
        return;
      }
      root.innerHTML = items.map(function (item) {
        var anchorId = anchorType === 'ticket' ? item.id : item.name;
        return '<button type="button" class="nav-item" data-nav-anchor-type="' + escapeHtml(anchorType) + '" data-nav-anchor-id="' + escapeHtml(anchorId) + '">'
          + '<strong>' + escapeHtml(anchorId) + '</strong>'
          + '<span>' + escapeHtml(subtitle(item)) + '</span>'
          + '</button>';
      }).join('');
    }
    function renderBacklogDocs(items) {
      var root = document.getElementById('knowledgeNavBacklogDocs');
      if (!root) return;
      if (!items || !items.length) {
        root.innerHTML = '<div class="nav-empty">Không có backlog doc khớp bộ lọc.</div>';
        return;
      }
      root.innerHTML = items.map(function (item) {
        return '<button type="button" class="nav-item" data-nav-doc-source-path="' + escapeHtml(item.source_path || '') + '">'
          + '<strong>' + escapeHtml(item.source_path || 'n/a') + '</strong>'
          + '<span>' + escapeHtml((item.project || 'general') + ' | ' + (item.date || 'n/a') + ' | ' + ((item.ticket_ids || []).join(', ') || 'no ticket')) + '</span>'
          + '</button>';
      }).join('');
    }
    function applyFilters() {
      var data = window.__knowledgeLinksData;
      if (!data) return;
      var keyword = String((document.getElementById('knowledgeNavSearch') || {}).value || '').trim().toLowerCase();
      var type = String((document.getElementById('knowledgeNavType') || {}).value || '').trim();
      var project = String((document.getElementById('knowledgeNavProjectFilter') || {}).value || '').trim();
      var showProjects = !type || type === 'project';
      var showTickets = !type || type === 'ticket';
      var showPeople = !type || type === 'person';
      var showChannels = !type || type === 'channel';
      var showMonths = !type || type === 'month';
      var projects = (data.projects || []).filter(function (item) {
        return matchesText([item.name, item.latest_date].join(' '), keyword) && inProject(item, project);
      }).slice(0, 18);
      var tickets = (data.tickets || []).filter(function (item) {
        return matchesText([item.id, (item.projects || []).join(' '), (item.doc_types || []).join(' ')].join(' '), keyword) && inProject(item, project);
      }).slice(0, 18);
      var people = (data.people || []).filter(function (item) {
        return matchesText([item.name, (item.projects || []).join(' ')].join(' '), keyword) && inProject(item, project);
      }).slice(0, 18);
      var channels = (data.channels || []).filter(function (item) {
        return matchesText([item.name, (item.projects || []).join(' ')].join(' '), keyword) && inProject(item, project);
      }).slice(0, 18);
      var months = (data.months || []).filter(function (item) {
        return matchesText([item.name, (item.projects || []).join(' ')].join(' '), keyword) && inProject(item, project);
      }).slice(0, 18);
      var backlogDocs = (data.backlog_documents || []).filter(function (item) {
        return matchesText([item.source_path, item.project, (item.ticket_ids || []).join(' ')].join(' '), keyword) && inProject(item, project);
      }).slice(0, 12);
      renderAnchorList('knowledgeNavProjects', showProjects ? projects : [], 'project', function (item) {
        return (item.backlog_doc_count || 0) + ' backlog docs | ' + (item.doc_count || 0) + ' docs | ' + (item.latest_date || 'n/a');
      });
      renderAnchorList('knowledgeNavTickets', showTickets ? tickets : [], 'ticket', function (item) {
        return (item.doc_count || 0) + ' docs | ' + (item.projects || []).slice(0, 3).join(', ');
      });
      renderAnchorList('knowledgeNavPeople', showPeople ? people : [], 'person', function (item) {
        return (item.doc_count || 0) + ' docs | ' + (item.ticket_count || 0) + ' tickets';
      });
      renderAnchorList('knowledgeNavChannels', showChannels ? channels : [], 'channel', function (item) {
        return (item.doc_count || 0) + ' docs | ' + (item.ticket_count || 0) + ' tickets';
      });
      renderAnchorList('knowledgeNavMonths', showMonths ? months : [], 'month', function (item) {
        return (item.doc_count || 0) + ' docs | ' + (item.latest_date || 'n/a');
      });
      renderBacklogDocs(backlogDocs);
      updateSummary('Projects: ' + projects.length + ' | Tickets: ' + tickets.length + ' | People: ' + people.length + ' | Channels: ' + channels.length + ' | Months: ' + months.length);
    }
    function loadNavigator(force) {
      if (!force && booted && window.__knowledgeLinksData) {
        applyFilters();
        return;
      }
      booted = true;
      updateSummary('Đang tải Knowledge Navigator...');
      fetchJson('/knowledge-links')
        .then(function (data) {
          window.__knowledgeLinksData = data;
          populateProjectFilter(data.projects || []);
          applyFilters();
        })
        .catch(function (err) {
          updateSummary('Lỗi: ' + err.message);
        });
    }
    window.__loadKnowledgeNavigatorInline = loadNavigator;
    document.addEventListener('input', function (event) {
      if (event.target && event.target.id === 'knowledgeNavSearch') applyFilters();
    });
    document.addEventListener('change', function (event) {
      if (!event.target) return;
      if (event.target.id === 'knowledgeNavType' || event.target.id === 'knowledgeNavProjectFilter') applyFilters();
    });
    document.addEventListener('click', function (event) {
      // Nút "knowledge-navigator" không còn tồn tại riêng — Duyệt nhanh giờ là cột trái nằm
      // trong CÙNG panel #knowledge-links (Khám phá), nên ăn theo đúng nút đó.
      var btn = event.target && event.target.closest ? event.target.closest('.side-item[data-target="knowledge-links"]') : null;
      if (btn) {
        setTimeout(function () { loadNavigator(false); }, 0);
        return;
      }
      // Trước đây bấm 1 anchor/document phải giả lập chuyển tab (linksBtn.click() + setTimeout)
      // sang panel Links riêng. Giờ Links luôn hiện ngay cạnh (cột phải cùng panel) nên gọi thẳng,
      // không cần chuyển tab/đợi timeout nữa.
      var anchorBtn = event.target && event.target.closest ? event.target.closest('[data-nav-anchor-type][data-nav-anchor-id]') : null;
      if (anchorBtn) {
        var anchorType = anchorBtn.getAttribute('data-nav-anchor-type');
        var anchorId = anchorBtn.getAttribute('data-nav-anchor-id');
        if (window.__openKnowledgeAnchorDetail) window.__openKnowledgeAnchorDetail(anchorType, anchorId);
        return;
      }
      var docBtn = event.target && event.target.closest ? event.target.closest('[data-nav-doc-source-path]') : null;
      if (docBtn) {
        var sourcePath = docBtn.getAttribute('data-nav-doc-source-path');
        if (window.__openKnowledgeDocumentDetail) window.__openKnowledgeDocumentDetail(sourcePath);
      }
    });
    var panelEl = document.getElementById('knowledge-links');
    if (panelEl && typeof MutationObserver !== 'undefined') {
      new MutationObserver(function () {
        if (panelEl.classList.contains('active')) loadNavigator(false);
      }).observe(panelEl, { attributes: true, attributeFilter: ['class'] });
    }
  })();
  
  (function () {
    function normalizeText(text) { return String(text || '').normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').replace(/\\s+/g, ' ').trim().toLowerCase(); }
    function getCellText(cell) { return (cell.textContent || '').replace(/\\s+/g, ' ').trim(); }
  function shouldCreateSelect(values) { return values.length > 1 && values.length <= 12; }
  function buildFilterSelect(label, values, onChange) {
    var select = document.createElement('select');
    select.className = 'table-filter-select';
    var allOption = document.createElement('option');
    allOption.value = '';
    allOption.textContent = label + ': Tất cả';
    select.appendChild(allOption);
    values.forEach(function (value) {
      var option = document.createElement('option');
      option.value = value;
      option.textContent = label + ': ' + value;
      select.appendChild(option);
    });
    select.addEventListener('change', onChange);
    return select;
  }
  document.querySelectorAll('.lane-tab-panel[id$="-e"] .table-wrap table').forEach(function (table, tableIndex) {
    var tbody = table.querySelector('tbody');
    var headCells = Array.from(table.querySelectorAll('thead th'));
    var bodyRows = Array.from(table.querySelectorAll('tbody tr'));
    var tableWrap = table.closest('.table-wrap');
    if (!tbody || !headCells.length || !bodyRows.length || !tableWrap) return;
    var headers = headCells.map(function (cell) { return getCellText(cell); });
    var rowsMeta = bodyRows.map(function (row) { return { row: row, cells: Array.from(row.children).map(getCellText), haystack: normalizeText(row.textContent) }; });
    var toolbar = document.createElement('div');
    toolbar.className = 'table-toolbar';
    toolbar.setAttribute('data-filter-table', String(tableIndex));
    var toolbarMain = document.createElement('div');
    toolbarMain.className = 'table-toolbar-main';
    var toolbarAdvanced = document.createElement('div');
    toolbarAdvanced.className = 'table-toolbar-advanced';
    toolbar.appendChild(toolbarMain);
    toolbar.appendChild(toolbarAdvanced);
    var search = document.createElement('input');
    search.className = 'table-filter-search';
    search.type = 'search';
    search.placeholder = 'Tìm trong bảng này...';
    toolbarMain.appendChild(search);
    var activeSelects = [];
    headers.forEach(function (header, colIndex) {
      var values = rowsMeta.map(function (item) { return item.cells[colIndex] || ''; }).filter(function (value) { return value; }).filter(function (value, idx, arr) { return arr.indexOf(value) === idx; });
      if (!shouldCreateSelect(values)) return;
      if (normalizeText(header).indexOf('rui ro') !== -1 || normalizeText(header).indexOf('task lien quan') !== -1) return;
      if (colIndex === 0 && values.length > 8) return;
      var select = buildFilterSelect(header, values.sort(function (a, b) { return a.localeCompare(b, 'vi'); }), applyFilters);
      activeSelects.push({ index: colIndex, select: select });
      toolbarAdvanced.appendChild(select);
    });
    var meta = document.createElement('div');
    meta.className = 'table-filter-meta';
    toolbarMain.appendChild(meta);
    var toggleBtn = null;
    if (activeSelects.length) {
      toggleBtn = document.createElement('button');
      toggleBtn.type = 'button';
      toggleBtn.className = 'table-filter-chip';
      toggleBtn.textContent = 'Bộ lọc';
      toggleBtn.addEventListener('click', function () {
        var isOpen = toolbarAdvanced.classList.toggle('open');
        toggleBtn.classList.toggle('active', isOpen);
        updateToggle(activeCount());
      });
      toolbarMain.appendChild(toggleBtn);
    }
    var resetBtn = document.createElement('button');
    resetBtn.type = 'button';
    resetBtn.className = 'table-filter-reset';
    resetBtn.textContent = 'Xóa lọc';
    resetBtn.addEventListener('click', function () {
      search.value = '';
      activeSelects.forEach(function (entry) { entry.select.value = ''; });
      applyFilters();
    });
    toolbarMain.appendChild(resetBtn);
    tableWrap.insertBefore(toolbar, table);
    var emptyRow = document.createElement('tr');
    emptyRow.className = 'table-filter-empty';
    emptyRow.style.display = 'none';
    var emptyCell = document.createElement('td');
    emptyCell.colSpan = headers.length;
    emptyCell.textContent = 'Không có dòng nào khớp bộ lọc.';
    emptyRow.appendChild(emptyCell);
    tbody.appendChild(emptyRow);
    search.addEventListener('input', applyFilters);
    function activeCount() {
      var count = search.value.trim() ? 1 : 0;
      activeSelects.forEach(function (entry) { if (entry.select.value) count += 1; });
      return count;
    }
    function updateToggle(count) {
      if (!toggleBtn) return;
      var isOpen = toolbarAdvanced.classList.contains('open');
      toggleBtn.textContent = isOpen ? (count ? 'Ẩn bộ lọc (' + count + ')' : 'Ẩn bộ lọc') : (count ? 'Bộ lọc (' + count + ')' : 'Bộ lọc');
    }
    function applyFilters() {
      var query = normalizeText(search.value);
      var visibleCount = 0;
      rowsMeta.forEach(function (item) {
        var matchesQuery = !query || item.haystack.indexOf(query) !== -1;
        var matchesSelects = activeSelects.every(function (entry) { return !entry.select.value || item.cells[entry.index] === entry.select.value; });
        var visible = matchesQuery && matchesSelects;
        item.row.style.display = visible ? '' : 'none';
        if (visible) visibleCount += 1;
      });
      emptyRow.style.display = visibleCount ? 'none' : '';
      meta.textContent = 'Hiển thị ' + visibleCount + '/' + rowsMeta.length + ' dòng';
      updateToggle(activeCount());
    }
    applyFilters();
  });
})();

(function () {
  var apiBaseEl = document.getElementById('syncApiBase');
  var ownerKeyEl = document.getElementById('syncOwnerKey');
  var saveBtn = document.getElementById('syncSaveConfig');
  var runBtn = document.getElementById('syncRunBtn');
  var worklogBtn = document.getElementById('syncWorklogBtn');
  var statusEl = document.getElementById('syncStatus');
  var feedbackEl = document.getElementById('syncFeedback');
  var dotEl = document.getElementById('syncDot');
  var logEl = document.getElementById('syncLog');
  var allBtns = [runBtn, worklogBtn];
  var settingsMenuEl = null;
  function setStatus(text, kind) {
    statusEl.textContent = text;
    statusEl.className = 'sync-status' + (kind ? ' ' + kind : '');
    dotEl.className = 'sync-dot' + (kind ? ' ' + kind : '');
    if (feedbackEl) {
      feedbackEl.textContent = text;
      feedbackEl.className = 'sync-feedback' + (kind ? ' ' + kind : '');
    }
  }
  function setBusy(busy) { allBtns.forEach(function (b) { b.disabled = busy; }); }
  function setupSettingsMenu() {
    var topbarActions = document.querySelector('.topbar-actions');
    var topbarTools = document.querySelector('.topbar-tools');
    var statusRow = document.querySelector('.sync-status-row');
    var configEl = document.querySelector('.sync-config');
    if (!topbarActions || !topbarTools || !statusRow || !configEl) return;
    var menu = document.createElement('details');
    menu.className = 'settings-menu';
    var summary = document.createElement('summary');
    summary.className = 'sync-btn secondary settings-trigger';
    summary.innerHTML = '<span>&#9881;</span><span>Settings</span>';
    menu.appendChild(summary);
    var panel = document.createElement('div');
    panel.className = 'settings-panel';
    statusRow.classList.add('settings-status');
    panel.appendChild(statusRow);
    var sectionTitle = document.createElement('div');
    sectionTitle.className = 'settings-section-title';
    sectionTitle.textContent = 'Cấu hình server';
    panel.appendChild(sectionTitle);
    configEl.open = true;
    panel.appendChild(configEl);
    menu.appendChild(panel);
    topbarActions.appendChild(menu);
    topbarTools.classList.add('has-settings-menu');
    settingsMenuEl = menu;
  }
  apiBaseEl.value = localStorage.getItem('dashboardApiBase') || 'http://127.0.0.1:8000';
  ownerKeyEl.value = localStorage.getItem('dashboardOwnerKey') || '';
  setupSettingsMenu();
  saveBtn.addEventListener('click', function () {
    localStorage.setItem('dashboardApiBase', apiBaseEl.value.trim() || 'http://127.0.0.1:8000');
    localStorage.setItem('dashboardOwnerKey', ownerKeyEl.value.trim());
    // Đồng bộ sang key mà panel "Quản lý người dùng" (people.js) thực sự đọc — xem ghi chú ở
    // đầu file _JS_ENHANCED. Không đồng bộ ngược lại (nếu người dùng đổi key ở /people-ui) vì
    // "Cấu hình server" là nơi chính để nhập key, /people-ui chỉ là trang độc lập cũ hơn.
    localStorage.setItem('graphrag_owner_key', ownerKeyEl.value.trim());
    setStatus('Đã lưu cấu hình.', 'ok');
  });
  function runSync(mode, label) {
    var apiBase = apiBaseEl.value.trim() || 'http://127.0.0.1:8000';
    var ownerKey = ownerKeyEl.value.trim();
    if (!ownerKey) {
      setStatus('Chưa nhập OWNER_API_KEY — mở menu Settings để cấu hình.', 'err');
      return;
    }
    setBusy(true);
    logEl.style.display = 'none';
    setStatus('Đang ' + label + ' — có thể mất 1-2 phút…', '');
    fetch(apiBase + '/run-sync?mode=' + encodeURIComponent(mode), { method: 'POST', headers: { 'X-API-Key': ownerKey } })
      .then(function (res) { return res.json().then(function (data) { return { ok: res.ok, data: data }; }); })
      .then(function (r) {
        if (!r.ok) {
          if (settingsMenuEl) settingsMenuEl.open = true;
          setStatus('Lỗi: ' + (r.data.detail || 'không rõ'), 'err');
          setBusy(false);
          return;
        }
        if (r.data.log_tail) {
          if (settingsMenuEl) settingsMenuEl.open = true;
          logEl.textContent = r.data.log_tail;
          logEl.style.display = 'block';
        }
        if (r.data.success) {
          setStatus('Xong — đang tải lại trang…', 'ok');
          setTimeout(function () { location.reload(); }, 1500);
        } else {
          setStatus('Thất bại (exit ' + r.data.exit_code + ') — xem log bên dưới.', 'err');
          setBusy(false);
        }
      })
      .catch(function () {
        if (settingsMenuEl) settingsMenuEl.open = true;
        setStatus('Không kết nối được server — mở terminal chạy `graphrag serve` trước.', 'err');
        setBusy(false);
      });
  }
  runBtn.addEventListener('click', function () { runSync('full', 'chạy đồng bộ đầy đủ'); });
  worklogBtn.addEventListener('click', function () { runSync('worklog', 'tổng hợp Luồng công việc'); });
  var modelExtractionEl = document.getElementById('modelExtraction');
  var modelAnswerEl = document.getElementById('modelAnswer');
  var modelSaveBtn = document.getElementById('modelSaveBtn');
  var modelSaveStatus = document.getElementById('modelSaveStatus');
  [modelExtractionEl, modelAnswerEl].forEach(function (el) {
    if (el) el.addEventListener('input', function () { el.dataset.dirty = '1'; });
  });
  function loadModelInfo() {
    var box = document.getElementById('modelInfoBox');
    if (!box) return;
    var apiBase = apiBaseEl.value.trim() || 'http://127.0.0.1:8000';
    fetch(apiBase + '/model-info')
      .then(function (res) { if (!res.ok) throw new Error('HTTP ' + res.status); return res.json(); })
      .then(function (d) {
        box.innerHTML =
          '<div><strong>Extraction (graph layer):</strong> ' + d.extraction_provider + ' / ' + d.extraction_model + '</div>' +
          '<div><strong>Answer (graphrag query/dashboard):</strong> ' + d.answer_provider + ' / ' + d.answer_model + '</div>' +
          '<div><strong>Embedding:</strong> ' + d.embedding_default_provider + ' (fallback local: ' + d.embedding_local_model + ')</div>';
        if (modelExtractionEl && !modelExtractionEl.dataset.dirty) modelExtractionEl.value = d.extraction_model;
        if (modelAnswerEl && !modelAnswerEl.dataset.dirty) modelAnswerEl.value = d.answer_model;
      })
      .catch(function () {
        box.textContent = 'Không lấy được — cần graphrag serve đang chạy ở địa chỉ trên.';
      });
  }
  loadModelInfo();
  saveBtn.addEventListener('click', loadModelInfo);
  if (modelSaveBtn) {
    modelSaveBtn.addEventListener('click', function () {
      var apiBase = apiBaseEl.value.trim() || 'http://127.0.0.1:8000';
      var ownerKey = ownerKeyEl.value.trim();
      modelSaveStatus.style.display = 'block';
      if (!ownerKey) {
        modelSaveStatus.textContent = 'Chưa nhập OWNER_API_KEY ở trên.';
        return;
      }
      var body = {};
      if (modelExtractionEl && modelExtractionEl.value.trim()) body.extraction_model = modelExtractionEl.value.trim();
      if (modelAnswerEl && modelAnswerEl.value.trim()) body.answer_model = modelAnswerEl.value.trim();
      if (!body.extraction_model && !body.answer_model) {
        modelSaveStatus.textContent = 'Chưa nhập model nào để lưu.';
        return;
      }
      modelSaveBtn.disabled = true;
      modelSaveStatus.textContent = 'Đang lưu…';
      fetch(apiBase + '/model-info', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-API-Key': ownerKey },
        body: JSON.stringify(body)
      })
        .then(function (res) { return res.json().then(function (data) { return { ok: res.ok, data: data }; }); })
        .then(function (r) {
          modelSaveBtn.disabled = false;
          if (!r.ok) {
            modelSaveStatus.textContent = 'Lỗi: ' + (r.data.detail || 'không rõ');
            return;
          }
          modelSaveStatus.textContent = 'Đã lưu vào config/settings.yaml — áp dụng ngay cho lần gọi tiếp theo.';
          if (modelExtractionEl) modelExtractionEl.dataset.dirty = '';
          if (modelAnswerEl) modelAnswerEl.dataset.dirty = '';
          loadModelInfo();
        })
        .catch(function () {
          modelSaveBtn.disabled = false;
          modelSaveStatus.textContent = 'Không kết nối được server.';
        });
    });
  }
})();
"""


def _sidebar_and_panels(results: list["LaneResult"]) -> tuple[str, str]:
    by_lane = {lr.lane: lr for lr in results}
    side_items = [
        '<button class="side-item active" data-target="overview" '
        'title="Landing page — không chứa dữ liệu chi tiết. Chọn 1 workspace ở nhóm Dữ liệu, hoặc '
        'dùng nhóm Công cụ bên dưới.">'
        '<span class="side-icon">🏠</span>Trang chủ</button>',
        '<div class="sidebar-label">🗂️ Dữ liệu</div>',
    ]
    # Trang chủ CHỦ Ý không chứa dữ liệu chi tiết (tile/rủi ro/task, snapshot kho tri thức) — theo
    # yêu cầu người dùng 2026-08-11: mọi task/rủi ro tổng hợp ở đây trước kia (_overview_panel(), đã
    # xoá) vốn PARSE TRỰC TIẾP từ chính section E của từng lane (xem load_manual_e_data()) nên không
    # có thông tin nào bị mất — chi tiết đầy đủ vẫn nằm nguyên ở tab "E. Luồng công việc"/"Nhật ký
    # dự án" của từng workspace. Từ Phase 6 (2026-08-25), sidebar đi theo cây org/workspace thay vì
    # danh sách lane phẳng — xem _sidebar_and_panels bên dưới.
    panels = [
        '<section id="overview" class="lane-panel active"><h2>Trang chủ</h2>'
        '<div class="empty-state"><div class="empty-state-icon">🏠</div>'
        '<p>Chọn 1 workspace ở nhóm "🗂️ Dữ liệu" bên trái để xem báo cáo CIO/tài liệu/nhật ký dự án, '
        'hoặc dùng nhóm "🛠️ Công cụ" để tìm kiếm/kiểm tra hệ thống.</p></div>'
        '</section>',
        '<section id="document-search" class="lane-panel">'
        '<div class="search-shell">'
        '<div class="search-hero"><div><h2>Search tài liệu</h2><p class="search-intro">Tìm nhanh tài liệu trong kho bằng từ khóa và lọc theo project, kênh nguồn, reliability, ticket, person và khoảng ngày. Kết quả bấm thẳng sang document detail để kiểm tra evidence.</p></div><span class="search-badge">Phase 4</span></div>'
        '<div id="documentSearchRoot" class="search-native">'
        '<form id="documentSearchForm" class="search-form">'
        '<div class="search-grid">'
        '<div class="search-field"><label for="searchQuery">Query</label><input id="searchQuery" name="q" type="text" placeholder="vd: QLYC-11886 kiosk golive"></div>'
        '<div class="search-field"><label for="searchProject">Project</label><select id="searchProject" name="project"><option value="">Tất cả</option></select></div>'
        '<div class="search-field"><label for="searchChannel">Source channel</label><select id="searchChannel" name="source_channel"><option value="">Tất cả</option></select></div>'
        '<div class="search-field"><label for="searchReliability">Reliability</label><select id="searchReliability" name="reliability"><option value="">Tất cả</option></select></div>'
        '<div class="search-field"><label for="searchTicket">Ticket</label><select id="searchTicket" name="ticket"><option value="">Tất cả</option></select></div>'
        '<div class="search-field"><label for="searchPerson">Person</label><select id="searchPerson" name="person"><option value="">Tất cả</option></select></div>'
        '<div class="search-field"><label for="searchDateFrom">Date from</label><input id="searchDateFrom" name="date_from" type="date"></div>'
        '<div class="search-field"><label for="searchDateTo">Date to</label><input id="searchDateTo" name="date_to" type="date"></div>'
        '</div>'
        '<div class="search-actions"><div id="documentSearchSummary" class="search-summary">Dùng OWNER_API_KEY ở Cấu hình server để tải Search.</div><div><button type="submit" class="side-item" style="min-height:34px;">Tìm kiếm</button></div></div>'
        '</form>'
        '<section class="search-block"><div class="query-block-title">Kết quả</div><div id="documentSearchResults" class="search-results"><div class="search-empty">Chưa tải dữ liệu.</div></div></section>'
        '</div>'
        '</div>'
        '</section>'
    ]

    # Cột trái ("Duyệt nhanh") của panel #knowledge-links gộp — trước đây là section
    # #knowledge-navigator độc lập, đọc CHUNG endpoint /knowledge-links với Links nên gộp vào làm
    # rail lọc bên trái, không còn là 1 tab riêng (xem CSS .explore-shell / .nav-rail).
    _nav_rail_html = (
        '<div class="nav-shell nav-rail">'
        '<div class="nav-hero"><div><h2>Duyệt nhanh</h2><p class="nav-intro">Lọc theo anchor: project, ticket, person, source channel, month — bấm 1 kết quả để xem chi tiết ở cột bên phải.</p></div></div>'
        '<div class="nav-native">'
        '<section class="nav-toolbar"><div class="nav-toolbar-grid">'
        '<div class="nav-field"><label for="knowledgeNavSearch">Từ khóa anchor</label><input id="knowledgeNavSearch" type="search" placeholder="VD: MSBPay, QLYC-11886, Hà, zalo"></div>'
        '<div class="nav-field"><label for="knowledgeNavType">Nhóm anchor</label><select id="knowledgeNavType"><option value=\"\">Tất cả</option><option value=\"project\">Project</option><option value=\"ticket\">Ticket</option><option value=\"person\">Person</option><option value=\"channel\">Channel</option><option value=\"month\">Month</option></select></div>'
        '<div class="nav-field"><label for="knowledgeNavProjectFilter">Project focus</label><select id="knowledgeNavProjectFilter"><option value=\"\">Tất cả</option></select></div>'
        '</div><div id="knowledgeNavigatorSummary" class="nav-summary">Dùng OWNER_API_KEY ở Cấu hình server để tải navigator.</div></section>'
        '<div class="nav-grid">'
        '<section class="nav-block"><div class="query-block-title">Projects</div><div id="knowledgeNavProjects" class="nav-list"><div class="nav-empty">Chưa tải dữ liệu.</div></div></section>'
        '<section class="nav-block"><div class="query-block-title">Tickets</div><div id="knowledgeNavTickets" class="nav-list"><div class="nav-empty">Chưa tải dữ liệu.</div></div></section>'
        '<section class="nav-block"><div class="query-block-title">People</div><div id="knowledgeNavPeople" class="nav-list"><div class="nav-empty">Chưa tải dữ liệu.</div></div></section>'
        '<section class="nav-block"><div class="query-block-title">Channels</div><div id="knowledgeNavChannels" class="nav-list"><div class="nav-empty">Chưa tải dữ liệu.</div></div></section>'
        '<section class="nav-block"><div class="query-block-title">Months</div><div id="knowledgeNavMonths" class="nav-list"><div class="nav-empty">Chưa tải dữ liệu.</div></div></section>'
        '<section class="nav-block"><div class="query-block-title">Recent backlog docs</div><div id="knowledgeNavBacklogDocs" class="nav-list"><div class="nav-empty">Chưa tải dữ liệu.</div></div></section>'
        '</div>'
        '</div>'
        '</div>'
    )

    # ===== Cây "Dữ liệu": org -> workspace (kèm instance lồng nhau), đọc trực tiếp từ
    # tenants/registry.yaml qua tenant_browser.list_tenant_registry() — Phase 6 (2026-08-25):
    # thay hẳn danh sách lane phẳng cũ (_SIDEBAR_GROUPS/emit) để sidebar phản ánh đúng cấu trúc
    # tenants/<org>/<workspace>/ thật, không còn tên lane cũ (New_eKYC...) hay _common bị ẩn.
    registry = list_tenant_registry()

    def panel_id(tenant_id: str) -> str:
        return "ws-" + tenant_id.replace("/", "-").replace("_", "-")

    def workspace_button(node: dict, display_name: str, active: bool = False) -> str:
        tenant_id = node["tenant_id"]
        status = node["status"]
        if status == "empty":
            dot = "empty-t"
            hint = '<span class="ws-hint">trống</span>'
        elif node.get("workspace_id") == "_dept":
            dot = "dept-t"
            hint = '<span class="ws-hint">org-wide</span>'
        else:
            dot = "active-t"
            hint = f'<span class="doc-count">{node["doc_count"]}</span>'
        cls = "side-item empty-item" if status == "empty" else "side-item"
        cls += " active" if active else ""
        return (
            f'<button class="{cls}" data-target="{panel_id(tenant_id)}">'
            f'<span class="status-dot {dot}"></span><span class="ws-name">{_esc(display_name)}</span>{hint}</button>'
        )

    def workspace_panel(node: dict, display_name: str) -> str:
        tenant_id = node["tenant_id"]
        status = node["status"]
        if status in ("empty", "parent"):
            note = _esc(node.get("note", "")) or "Chưa có tài liệu nào trong tenant này."
            return (
                f'<div class="ws-header"><div><h2>{_esc(display_name)} <span class="badge planned">○ trống</span></h2>'
                f'<div class="ws-breadcrumb">tenants/{_esc(tenant_id)}</div></div></div>'
                f'<div class="empty-state"><div class="empty-state-icon">🌱</div><p>{note}</p></div>'
            )
        lane = _TENANT_ID_TO_LANE.get(tenant_id)
        lr = by_lane.get(lane) if lane else None
        is_dept = node.get("workspace_id") == "_dept"
        is_common = tenant_id == "_common"
        badge = (
            '<span class="badge dept">org-wide</span>' if is_dept
            else '<span class="badge good">● shared</span>' if is_common
            else '<span class="badge good">● active</span>'
        )
        report_tab = (
            _cio_tabs_html(lr) if lr is not None
            else '<div class="empty-state"><div class="empty-state-icon">📊</div>'
            '<p>Workspace này chưa có báo cáo CIO thủ công — xem tab "Tài liệu"/"Nhật ký dự án" để tra cứu trực tiếp.</p></div>'
        )
        info_strip = (
            '<div class="info-strip">🔗 Tự động được gộp vào <strong>mọi</strong> câu hỏi ở bất kỳ '
            'workspace nào (query-time federation), trọng số thấp hơn tenant chính — xem CLAUDE.md.</div>'
            if is_common else ""
        )
        docs_tab = (
            f'<div id="{panel_id(tenant_id)}-docs" class="ws-sub-panel">{_common_doc_grid_html()}</div>'
            if is_common else
            f'<div id="{panel_id(tenant_id)}-docs" class="ws-sub-panel" data-tenant-docs="{_esc(tenant_id)}">'
            '<div class="empty-state"><div class="empty-state-icon">📄</div><p>Đang tải danh sách tài liệu...</p></div></div>'
        )
        return f"""<div class="ws-header">
  <div><h2>{_esc(display_name)} {badge}</h2><div class="ws-breadcrumb">tenants/{_esc(tenant_id)}</div></div>
  <div class="meta-row"><span>📄 {node["doc_count"]} tài liệu</span></div>
</div>
{info_strip}
<div class="ws-tabs">
  <button class="ws-tab-btn active" data-sub="{panel_id(tenant_id)}-report">📊 Báo cáo CIO</button>
  <button class="ws-tab-btn" data-sub="{panel_id(tenant_id)}-docs">📄 Tài liệu</button>
  <button class="ws-tab-btn" data-sub="{panel_id(tenant_id)}-logs">📋 Nhật ký dự án</button>
  <button class="ws-tab-btn" data-sub="{panel_id(tenant_id)}-health">💚 Health</button>
</div>
<div id="{panel_id(tenant_id)}-report" class="ws-sub-panel active">{report_tab}</div>
{docs_tab}
<div id="{panel_id(tenant_id)}-logs" class="ws-sub-panel" data-tenant-logs="{_esc(tenant_id)}">
  <div class="empty-state"><div class="empty-state-icon">📋</div><p>Đang tải nhật ký dự án...</p></div>
</div>
<div id="{panel_id(tenant_id)}-health" class="ws-sub-panel" data-tenant-health="{_esc(tenant_id)}">
  <div class="empty-state"><div class="empty-state-icon">💚</div><p>Đang tải health...</p></div>
</div>"""

    for org in registry["orgs"]:
        org_id = org["org_id"]
        org_label, org_icon, org_css = _ORG_DISPLAY.get(org_id, (org_id.upper(), "🏢", "msb"))
        side_items.append(
            f'<div class="org-block" id="org-{_esc(org_id)}">'
            f'<button class="org-head" onclick="toggleOrgBlock(\'org-{_esc(org_id)}\')">'
            f'<span class="org-chevron">▾</span><span class="org-mark {org_css}">{org_icon}</span>{_esc(org_label)}</button>'
            '<div class="ws-list">'
        )

        def emit_node(node: dict, nested: bool = False) -> None:
            display_name = _WORKSPACE_DISPLAY_NAME.get(node.get("workspace_id") or "", node.get("workspace_id") or "")
            if node["status"] == "parent":
                side_items.append(
                    f'<div class="sidebar-label" style="margin:4px 0 2px;">{_esc(display_name)}</div><div class="ws-list">'
                )
                for child in node["children"]:
                    emit_node(child, nested=True)
                side_items.append("</div>")
                return
            side_items.append(workspace_button(node, display_name))
            panels.append(f'<section id="{panel_id(node["tenant_id"])}" class="lane-panel">{workspace_panel(node, display_name)}</section>')

        for ws in org["workspaces"]:
            emit_node(ws)
        side_items.append("</div></div>")

    if registry.get("common"):
        common = registry["common"]
        side_items.append(
            f'<button class="common-item" data-target="{panel_id(common["tenant_id"])}">'
            '<span class="common-mark">🌐</span>_common <span class="ws-hint" style="margin-left:auto;">dùng chung</span></button>'
        )
        panels.append(
            f'<section id="{panel_id(common["tenant_id"])}" class="lane-panel">{workspace_panel(common, "_common")}</section>'
        )

    # "Chưa phân loại" (lane fallback LLM tự động) không thuộc registry (không phải 1 tenant) —
    # vẫn giữ lại như 1 mục riêng cuối cây Dữ liệu để không mất tính năng cũ.
    if _INBOX_LANE in by_lane:
        lr = by_lane[_INBOX_LANE]
        slug = _slug(lr.lane)
        side_items.append(
            f'<button class="side-item" data-target="{slug}"><span class="status-dot empty-t"></span>'
            f'<span class="ws-name">{_esc(_INBOX_LANE)}</span></button>'
        )
        panels.append(f'<section id="{slug}" class="lane-panel">{_cio_tabs_html(lr)}</section>')

    # Nhóm "Tra cứu tri thức": 2 công cụ cho người dùng thường (không phải admin) — 2 mô hình tra
    # cứu khác nhau (search từ khoá vs duyệt có cấu trúc theo anchor), khớp mẫu Outline/Wiki.js
    # (Search tách khỏi Collections/anchor browsing). "Khám phá" tự nó đã gộp 2 tính năng cũ hay bị
    # nhầm là trùng lặp (Knowledge Navigator + Knowledge links, cùng đọc chung /knowledge-links) —
    # xem `_nav_rail_html` (cột trái) lồng trong panel `#knowledge-links` (cột phải) bên dưới.
    side_items.append('<div class="sidebar-label">🛠️ Công cụ</div>')
    side_items.append(
        '<button class="side-item" data-target="document-search" '
        'title="Tìm tài liệu theo từ khoá, lọc theo project/kênh nguồn/reliability/ticket/người/ngày.">'
        '<span class="side-icon">🔎</span>Tìm tài liệu</button>'
    )
    side_items.append(
        '<button class="side-item" data-target="knowledge-links" '
        'title="Duyệt có cấu trúc: lọc nhanh theo project/ticket/person/kênh/tháng ở cột trái, xem '
        'chi tiết backlog nối với tài liệu nào và quy trình QT.IT.005/009/019 nào ở cột phải.">'
        '<span class="side-icon">🧭</span>Khám phá (Project/Ticket/Người/Quy trình)</button>'
    )

    side_items.append(
        '<button class="side-item" data-target="system-health" '
        'title="Độ tươi của index: file nào cần normalize/build lại, lịch sử build gần đây.">'
        '<span class="side-icon">🩺</span>Health hệ thống</button>'
    )
    panels.append(
        '<section id="system-health" class="lane-panel">'
        '<div class="health-shell">'
        '<div class="health-hero"><div><h2>Health hệ thống</h2><p class="health-intro">Quan sát nhanh độ tươi của kho tri thức, tiến độ normalize và build, cùng các tín hiệu thiếu sót để xử lý sớm ngay trong quá trình làm việc.</p></div><span class="health-badge">Observability</span></div>'
        '<div id="healthInlineRoot" class="health-native"></div>'
        '</div>'
        '</section>'
    )
    side_items.append(
        '<button class="side-item" data-target="retrieval-debug" '
        'title="Công cụ debug: chạy 1 câu hỏi qua đúng pipeline retrieval thật (planner, trace, '
        'context, answer) để kiểm tra chất lượng truy xuất — không phải chỗ tìm tài liệu thường ngày, '
        'dùng tab Tìm tài liệu cho việc đó.">'
        '<span class="side-icon">🧪</span>Kiểm thử truy vấn (debug)</button>'
    )
    panels.append(
        '<section id="retrieval-debug" class="lane-panel">'
        '<div class="query-shell">'
        '<div class="query-hero"><div><h2>Search / Retrieval Debug</h2><p class="query-intro">Nhập câu hỏi thật để xem answer, planner mode, retrieval trace và top source sau các bước exact, keyword, vector, rerank. Màn này giúp bạn nhìn thấy trực tiếp các nâng cấp retrieval trên UI.</p></div><span class="query-badge">Phase 2</span></div>'
        '<div id="retrievalDebugRoot" class="query-native">'
        '<section class="query-form">'
        '<label for="retrievalQuestion">Câu hỏi kiểm tra</label>'
        '<textarea id="retrievalQuestion" class="query-textarea" placeholder="Ví dụ: cần tài liệu gì để UAT release"></textarea>'
        '<div><div class="query-mini-label">Preset mẫu</div><div class="query-presets"><button type="button" class="query-preset" data-query-sample="cần tài liệu gì để UAT release">UAT release</button><button type="button" class="query-preset" data-query-sample="quy trình tiếp nhận yêu cầu phát triển công nghệ fast lane">Fast lane</button><button type="button" class="query-preset" data-query-sample="các hạng mục cần trước khi lên production theo quy trình nội bộ">Before production</button><button type="button" class="query-preset" data-query-sample="quy trình golive production test sau golive">Golive / production test</button></div></div>'
        '<div class="query-actions"><div class="query-actions-left"><button id="retrievalRunBtn" class="query-btn" type="button">Chạy truy vấn</button><button id="retrievalSampleBtn" class="query-btn secondary" type="button">Điền ví dụ</button></div><div class="query-actions-right"><span id="retrievalStatus" class="query-status">Dùng OWNER_API_KEY ở Cấu hình server để chạy trên toàn bộ index.</span></div></div>'
        '</section>'
        '<section class="query-block"><div class="query-topline"><div><div class="query-block-title">Planner mode</div><div id="retrievalPlanner" class="query-answer">n/a</div></div><div><div class="query-block-title">Retrieval trace</div><div id="retrievalTrace" class="query-chip-row"><span class="query-chip">chưa chạy</span></div></div></div></section>'
        '<section class="query-block"><div class="query-block-title">Context stats</div><div id="retrievalStats" class="query-stats"><div class="query-stat-card"><div class="query-mini-label">candidate chunks</div><strong>0</strong></div><div class="query-stat-card"><div class="query-mini-label">context chunks</div><strong>0</strong></div><div class="query-stat-card"><div class="query-mini-label">candidate sources</div><strong>0</strong></div><div class="query-stat-card"><div class="query-mini-label">context sources</div><strong>0</strong></div><div class="query-stat-card"><div class="query-mini-label">duplicate in context</div><strong>0</strong></div></div></section>'
        '<section class="query-block"><div class="query-block-title">Answer</div><div id="retrievalAnswer" class="query-answer query-empty">Chưa có kết quả.</div></section>'
        '<section class="query-block"><div class="query-block-title">Packed context</div><div id="retrievalContext" class="query-context query-empty">Chưa có context nào được build.</div></section>'
        '<section class="query-block"><div class="query-block-title">Top sources</div><ul id="retrievalSources" class="query-source-list"><li><div class="query-empty">Chưa có chunk nào để hiển thị.</div></li></ul></section>'
        '<section class="query-block"><div class="query-block-title">Benchmark mini</div><div class="query-benchmark-grid"><div><div class="query-benchmark-actions"><button id="retrievalBenchmarkRunBtn" class="query-btn" type="button">Chạy benchmark mini</button><button id="retrievalBenchmarkExportBtn" class="query-btn secondary" type="button">Xuất .md</button></div><div id="retrievalBenchmarkStatus" class="query-status" style="margin-top:8px;">Chạy nhanh một tập câu hỏi chuẩn để review retrieval trên UI.</div><div class="query-benchmark-list" style="margin-top:10px;"><div class="query-benchmark-item"><strong>E05</strong><span>QT IT 005</span></div><div class="query-benchmark-item"><strong>M04</strong><span>quy trình golive production test sau golive</span></div><div class="query-benchmark-item"><strong>S04</strong><span>cần tài liệu gì để UAT release</span></div><div class="query-benchmark-item"><strong>H02</strong><span>từ QT.IT.005 sang QT.IT.019 và QT.IT.009 để ra flow từ demand đến golive</span></div></div></div><div><div class="query-mini-label">Kết quả gần nhất</div><div id="retrievalBenchmarkLog" class="query-benchmark-log query-empty">Chưa có benchmark nào được chạy.</div></div></div></section>'
        '</div>'
        '</div>'
        '</section>'
    )
    panels.append(
        '<section id="knowledge-links" class="lane-panel">'
        '<h2 class="explore-title">Khám phá tri thức</h2>'
        '<div class="explore-shell">'
        + _nav_rail_html +
        '<div class="links-shell">'
        '<div class="links-hero"><div><p class="links-intro">Chi tiết 1 backlog/ticket/project/người: tài liệu nối tới và gợi ý quy trình QT.IT.005 / 009 / 019. Chọn 1 kết quả ở cột "Duyệt nhanh" bên trái, hoặc lọc trực tiếp ở đây.</p></div><span class="links-badge">Phase 3</span></div>'
        '<div id="knowledgeLinksStatus" class="links-status">Dùng OWNER_API_KEY ở Cấu hình server để tải linked knowledge.</div>'
        '<div class="links-toolbar">'
        '<div class="links-filter"><label for="knowledgeLinksSearch">Từ khóa</label><input id="knowledgeLinksSearch" class="links-filter-search" type="search" placeholder="Ví dụ: QLYC-11886, MSBPay, UAT, Hà..."></div>'
        '<div class="links-filter"><label for="knowledgeLinksProcess">Quy trình</label><select id="knowledgeLinksProcess" class="links-filter-select"><option value="">Tất cả quy trình</option></select></div>'
        '<div class="links-filter"><label for="knowledgeLinksProject">Project</label><select id="knowledgeLinksProject" class="links-filter-select"><option value="">Tất cả project</option></select></div>'
        '<div id="knowledgeLinksMeta" class="links-meta">Chưa tải dữ liệu.</div>'
        '</div>'
        '<div id="knowledgeLinksSummary" class="links-summary"></div>'
        '<div class="links-grid">'
        '<section class="links-card"><div class="query-block-title">Backlog / ticket anchors</div><div id="knowledgeTickets" class="links-list"><div class="links-empty">Chưa tải dữ liệu.</div></div></section>'
        '<section class="links-card"><div class="query-block-title">Project anchors</div><div id="knowledgeProjects" class="links-list"><div class="links-empty">Chưa tải dữ liệu.</div></div></section>'
        '<section class="links-card"><div class="query-block-title">People anchors</div><div id="knowledgePeople" class="links-list"><div class="links-empty">Chưa tải dữ liệu.</div></div></section>'
        '</div>'
        '<div class="links-grid" style="grid-template-columns: 1.2fr 0.8fr;">'
        '<section class="links-card links-detail"><div><div class="query-block-title">Anchor detail</div><div id="knowledgeDetailTitle" class="query-answer"></div><div id="knowledgeDetailMeta" class="query-mini-label" style="margin-top:6px;"></div></div><div id="knowledgeDetailChips" class="links-chips"><span class="links-empty">Chưa chọn anchor nào.</span></div><div id="knowledgeDetailDocs" class="links-docs"><div class="links-empty">Chưa có document nào để hiển thị.</div></div></section>'
        '<section class="links-card"><div class="query-block-title">Backlog docs gần đây</div><div id="knowledgeBacklogDocs" class="links-backlog-list"><div class="links-empty">Chưa tải dữ liệu.</div></div></section>'
        '</div>'
        '</div>'
        '</div>'
        '</section>'
    )
    side_items.append(
        '<button class="side-item" data-target="manage-users" '
        'title="Đối soát và gộp danh tính người dùng giữa các kênh Zalo, Teams, Email.">'
        '<span class="side-icon">👥</span>Quản lý người dùng</button>'
    )
    panels.append(
        '<section id="manage-users" class="lane-panel">'
        '<div class="manage-shell">'
        '<div class="manage-hero"><div><h2>Quản lý người dùng</h2><p class="manage-intro">Mapping người dùng được render trực tiếp trong dashboard để bạn đối soát Zalo, Teams và Email ngay trong cùng một màn hình.</p></div><span class="manage-badge">Trực tiếp</span></div>'
        '<div id="manageUsersRoot" class="manage-native"><div class="page-shell"><div id="status"></div><div class="layout"><div class="stack"><section class="section-card"><div class="section-head"><div><h2>Registry hiện tại</h2><p class="section-note">Tên chính và alias đã xác nhận.</p></div><span class="chip">Registry</span></div><div class="table-wrap"><table id="registryTable"><thead><tr><th>Tên chính</th><th>Alias</th><th></th></tr></thead><tbody id="registryBody"></tbody></table></div></section><details class="compact-block"><summary>Thêm người mới</summary><div class="subtle-box"><div class="field-stack"><input id="newCanonical" type="text" placeholder="Tên chính"><input id="newAliases" type="text" placeholder="Alias, phân tách bằng dấu phẩy"></div><div class="action-row" style="margin-top:8px;"><button id="addPerson">Lưu vào registry</button></div></div></details><details class="compact-block"><summary>Danh sách đã ẩn</summary><ul id="ignoredList"></ul></details></div><div class="stack"><section class="section-card"><div class="section-head"><div><h2>Chưa map theo kênh</h2><p class="section-note">Kéo để nối hoặc tick rồi gộp.</p></div><span class="chip">Mapping</span></div><div class="columns-wrap"><svg id="linksSvg"></svg><div id="channelColumns" class="channel-columns"></div></div><div class="selection-bar action-row"><input id="mergeCanonical" type="text" placeholder="Tên chính cho các mục đã chọn"><button id="mergeSelected">Gộp</button><button id="ignoreSelected" class="danger">Không phải người</button></div></section><details class="compact-block" open><summary>Nhóm đã nối</summary><div id="linkGroups"></div></details></div></div></div></div>'
        '</div>'
        '</section>'
    )
    # AI Agents (lịch sử orchestrator + alerts) — render NATIVE bằng đúng bộ CSS .health-* dùng
    # chung với panel "Health hệ thống" (không nhúng iframe trang /agents-ui riêng nữa — theo yêu
    # cầu người dùng 2026-08-14: cùng format/CSS với các phần khác + dùng chung OWNER_API_KEY ở
    # "Cấu hình server", không có ô nhập key riêng). Logic fetch/render nằm trong IIFE JS ngay sau
    # panel "Health hệ thống" trong _JS_ENHANCED — xem graphrag/agent_ops.py cho phần API.
    side_items.append(
        '<button class="side-item" data-target="ai-agents" '
        'title="Lịch sử các lần knowledge-agents-orchestrator đã chạy và toàn bộ ALERT gom từ '
        '_project-logs/ALERTS.md của từng dự án.">'
        '<span class="side-icon">🤖</span>AI Agents — Alerts</button>'
    )
    panels.append(
        '<section id="ai-agents" class="lane-panel">'
        '<div class="health-shell">'
        '<div class="health-hero"><div><h2>AI Agents — Lịch sử &amp; Alerts</h2>'
        '<p class="health-intro">Toàn bộ các lần knowledge-agents-orchestrator (approval-verification, '
        'architecture-compliance, raid-milestone-extraction, raci-daci-enforcer, retro-synthesizer) đã '
        'chạy, cùng mọi ALERT gom từ _project-logs/ALERTS.md của từng dự án.</p></div>'
        '<span class="health-badge">Giám sát</span></div>'
        '<div id="aiAgentsInlineRoot" class="health-native"></div>'
        '</div>'
        '</section>'
    )

    return "".join(side_items), "".join(panels)


# Khối "chrome" quanh Settings (server config + model đang dùng) — KHÔNG phụ thuộc dữ liệu
# lane/LLM nào, nên api.py.dashboard_root() vá (regex-replace) trực tiếp khối này + _CSS +
# _JS_ENHANCED vào file reports/DASHBOARD.html tĩnh mỗi lần phục vụ trang, tách biệt hoàn toàn
# với nội dung tab lane (tốn tiền, chỉ đổi khi bấm nút/`graphrag dashboard`). Nhờ vậy sửa
# Settings panel (thêm field, đổi label...) thấy ngay khi F5, không cần re-run tốn OpenAI.
_SYNC_CONFIG_HTML = """<details class="sync-config">
          <summary>Cấu hình server</summary>
          <label for="syncApiBase">Địa chỉ server (graphrag serve)</label>
          <input id="syncApiBase" placeholder="http://127.0.0.1:8000">
          <label for="syncOwnerKey">OWNER_API_KEY</label>
          <input id="syncOwnerKey" type="password" placeholder="dán key từ .env">
          <div><button id="syncSaveConfig" type="button">Lưu cấu hình</button></div>
          <div class="settings-section-title" style="margin-top:10px;">Model đang dùng</div>
          <div id="modelInfoBox" class="model-info-box">Đang tải…</div>
          <label for="modelExtraction">Extraction model (graph layer)</label>
          <input id="modelExtraction" placeholder="gpt-5.4">
          <label for="modelAnswer">Answer model (graphrag query / dashboard)</label>
          <input id="modelAnswer" placeholder="gpt-5.4">
          <div><button id="modelSaveBtn" type="button">Lưu model</button></div>
          <div id="modelSaveStatus" class="model-info-box" style="display:none;"></div>
        </details>"""


_JS_ENHANCED = _JS

_MERMAID_INIT_JS = ""  # Mermaid removed in v1.3 — Tab F giờ là bảng HTML tĩnh, không cần JS render


def render_dashboard(results: list["LaneResult"], generated_at: datetime) -> str:
    asof = generated_at.strftime("%H:%M %d/%m/%Y")
    side_items, panels = _sidebar_and_panels(results)

    return f"""<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dashboard tổng hợp buổi sáng</title>
<style>{_CSS}</style>
</head>
<body>
<div class="app-shell">
  <nav class="sidebar">
    <div class="sidebar-brand">
      <div class="brand-mark">📋</div>
      <div>
        <div class="brand-title">Dashboard</div>
        <div class="brand-sub">Tổng hợp tri thức MSB</div>
      </div>
    </div>
    <div class="sidebar-nav">{side_items}</div>
  </nav>
  <div class="app-main">
    <header class="topbar">
      <div class="topbar-row">
        <div class="asof-chip">🕒 Cập nhật lúc <strong>{asof}</strong></div>
        <div class="topbar-actions">
          <div class="sync-status-row">
            <span id="syncDot" class="sync-dot"></span>
            <span id="syncStatus" class="sync-status">Cần <code>graphrag serve</code> chạy cục bộ.</span>
          </div>
          <button id="syncWorklogBtn" class="sync-btn secondary" type="button">🧩 Tổng hợp Luồng công việc</button>
          <button id="syncRunBtn" class="sync-btn" type="button">🔄 Chạy đồng bộ ngay</button>
        </div>
      </div>
      <div id="syncFeedback" class="sync-feedback">Sẵn sàng.</div>
      <div class="topbar-tools">
        <pre id="syncLog" class="sync-log"></pre>
        {_SYNC_CONFIG_HTML}
      </div>
      <div class="banner">
        <span class="banner-icon">ℹ️</span>
        <span><strong>File này là nguồn duy nhất</strong> — không còn 6 file <code>CIO-&lt;ID&gt;.html</code>
        riêng lẻ nữa (đổi từ 2026-08-06). Muốn cập nhật: yêu cầu Claude <strong>"build lại
        DASHBOARD"</strong>, không hỏi riêng từng lane. Riêng lane <strong>chưa có dữ liệu đã soát
        tay</strong> (project mới, hoặc mục "Chưa phân loại") vẫn dùng tổng hợp tự động 1 lần gọi
        LLM cho tab E — chưa đối chiếu chéo nhiều nguồn, có thể sai PIC/priority/deadline,
        <strong>không dùng làm căn cứ quyết định</strong>.
        <strong>⚠️ 2 nút "🧩 Tổng hợp Luồng công việc"/"🔄 Chạy đồng bộ ngay" bên dưới KHÔNG còn an
        toàn để tự bấm</strong> — chúng gọi thẳng <code>graphrag dashboard</code>, lệnh này sẽ từ
        chối chạy (raise lỗi) nếu không thấy staging file, nhưng tốt nhất vẫn nên nhờ Claude chạy
        đúng quy trình "build lại DASHBOARD" thay vì bấm nút.</span>
      </div>
    </header>
    <main class="content">{panels}</main>
  </div>
</div>
<script>{_JS_ENHANCED}</script>
</body>
</html>
"""
