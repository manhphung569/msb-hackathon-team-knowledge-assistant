from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import html
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile

from .config import PROJECT_ROOT

KNOWLEDGE_ROOT = PROJECT_ROOT.parent
_SOURCE_GLOB = "tenants/msb/_dept/normalized/DM&UDCNS_BaoCaoCIO_*.md"
_DATE_IN_NAME_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
_PAGE_RE = re.compile(r"^## Trang (\d+)\s*$", re.M)
_SECTION_RE = re.compile(r"<section>(.*?)</section>", re.S)
_SECTION_LETTER_RE = re.compile(r"^\s*<h2>([A-G])\.", re.S)
_ROW_RE = re.compile(
    r'<div class="timeline-row(?P<empty> timeline-empty-row)?">\s*'
    r'<div class="timeline-row-label">\s*(?P<label>.*?)</div>\s*'
    r'<div class="timeline-track"(?P<track_attrs>[^>]*)>(?P<track_inner>.*?)</div>\s*'
    r"</div>",
    re.S,
)
# Optional nhãn nhóm quý (thêm 2026-08-18, xem reports/TEMPLATE.html v1.6) — 1 lane có thể chèn
# <h4 class="quarter-h">Quý N/2026 ...</h4> xen giữa các .timeline-row trong staging file để nhóm
# hàng theo Quý mà vẫn giữ nguyên Gantt. _ITEM_RE bắt CẢ 2 dạng theo đúng thứ tự xuất hiện trong
# tài liệu, để load_lane_timeline_bars()/_inject_gantt_column() (dashboard_renderer.py) phát lại
# đúng thứ tự (header xen giữa) + tự đánh STT
# lại từ 1 sau mỗi header — không cần STT thủ công trong text label (dễ lệch khi status được tính
# lại từ nguồn CIO Report, xem _resolve_row_status).
_ITEM_RE = re.compile(
    r'<h4 class="quarter-h">(?P<qh_text>.*?)</h4>'
    r'|<div class="timeline-row(?P<empty> timeline-empty-row)?">\s*'
    r'<div class="timeline-row-label">\s*(?P<label>.*?)</div>\s*'
    r'<div class="timeline-track"(?P<track_attrs>[^>]*)>(?P<track_inner>.*?)</div>\s*'
    r"</div>",
    re.S,
)
_BAR_RE = re.compile(
    r'<div class="timeline-bar (?P<class>[^"]+)" style="(?P<style>[^"]*)">(?P<label>.*?)</div>',
    re.S,
)
_MARKER_RE = re.compile(
    r'<div class="timeline-marker (?P<class>[^"]+)" style="(?P<style>[^"]*)"'
    r'(?: data-label="(?P<data_label>[^"]*)")?'
    r'(?: title="(?P<title>[^"]*)")?></div>',
    re.S,
)
_MONTH_RE = re.compile(r'<div class="timeline-scale-months">\s*(?P<inner>.*?)\s*</div>', re.S)
_SPAN_RE = re.compile(r"<span>(.*?)</span>")
_CAVEAT_RE = re.compile(r'<div class="caveat">(.*?)</div>', re.S)
_TITLE_RE = re.compile(r"<h2>.*?</h2>", re.S)
_SUBTITLE_RE = re.compile(r"<p class=\"legend-note\">(.*?)</p>", re.S)
_TRACK_TITLE_RE = re.compile(r'\btitle="([^"]*)"')
_BACKLOG_ID_RE = re.compile(r"<span class=\"backlog-id\">(.*?)</span>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_PAGE11_ROW_RE = re.compile(
    r"^(MSB Pay|DIP|MConnect|Magnet_BAU|E-KYC|TỔNG)\s+(\d+)\s+([^\s]+)\s*(.*)$",
    re.M,
)
_REPORT_ID_TO_PAGE = {
    "CIO-MSBPAY": 14,
    "CIO-DIP": 15,
    "CIO-MCONNECT": 16,
    "CIO-MAGNET": 17,
    "CIO-EKYC": 18,
}
_REPORT_ID_TO_PAGE11_LANE = {
    "CIO-MSBPAY": "MSB Pay",
    "CIO-DIP": "DIP",
    "CIO-MCONNECT": "MConnect",
    "CIO-MAGNET": "Magnet_BAU",
    "CIO-EKYC": "E-KYC",
}
_REPORT_ID_TO_SLIDE_LANE = {
    "CIO-MSBPAY": "MSB PAY",
    "CIO-MCONNECT": "MCONNECT",
    "CIO-DIP": "DIP",
    "CIO-MAGNET": "MAGNET_BAU",
    "CIO-EKYC": "E-KYC",
}
_MAIN_SLIDE_ORDER = ["CIO-MSBPAY", "CIO-MCONNECT", "CIO-DIP", "CIO-MAGNET", "CIO-EKYC"]
_STATUS_PREFIX = {
    "done": "[D] ",
    "progress": "[P] ",
    "blocked": "[B] ",
    "onhold": "[H] ",
    "cancelled": "[C] ",
}
_STATUS_LABEL = {
    "done": "Hoàn thành",
    "progress": "Đang triển khai",
    "blocked": "Rủi ro/quá hạn",
    "onhold": "On-Hold",
    "cancelled": "Đóng YC/Cancelled",
    "open": "Kế hoạch còn lại",
}
_STATUS_CSS = {
    "done": "tl-done",
    "progress": "tl-progress",
    "blocked": "tl-blocked",
    "onhold": "tl-onhold",
    "cancelled": "tl-cancelled",
    "open": "tl-open",
}
_PPT_NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
}


@dataclass
class TimelineMarker:
    class_name: str
    style: str
    data_label: str | None = None
    title: str | None = None


@dataclass
class TimelineBar:
    class_name: str
    style: str
    label: str


@dataclass
class TimelineRow:
    backlog_id: str
    title: str
    title_html: str
    track_title: str
    bar: TimelineBar | None
    markers: list[TimelineMarker] = field(default_factory=list)
    is_empty: bool = False
    manual_status: str | None = None
    resolved_status: str | None = None
    kind: str = "row"  # "row" | "header" — xem _ITEM_RE, header không có bar/track/status
    header_text: str = ""


@dataclass
class LaneTimeline:
    report_id: str
    report_path: Path
    title: str
    subtitle: str
    caveat_html: str
    months: list[str]
    rows: list[TimelineRow]
    axis_start: datetime | None
    axis_end: datetime | None


@dataclass
class TimelineContext:
    source_path: Path
    report_date: str
    pages: dict[int, str]
    page11_counts: dict[str, str]
    extra_done_ids: set[str]
    closed_ids: set[str]
    status_chunks: dict[str, dict[str, str]]


def _normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _strip_tags(text: str) -> str:
    return _normalize_space(_TAG_RE.sub(" ", text))


def _normalize_backlog_key(value: str) -> str:
    value = html.unescape(value).strip()
    upper = value.upper()
    if upper.startswith("QLYC-"):
        return upper.split("-", 1)[1]
    if upper.startswith("YC-"):
        return upper.split("-", 1)[1]
    return upper


def _parse_ddmmyyyy_from_name(path: Path) -> datetime:
    match = _DATE_IN_NAME_RE.search(path.name)
    if not match:
        return datetime.fromtimestamp(path.stat().st_mtime)
    day, month, year = match.groups()
    return datetime(int(year), int(month), int(day))


def _resolve_latest_source(source_path: Path | None = None) -> Path:
    if source_path is not None:
        return source_path
    matches = sorted(KNOWLEDGE_ROOT.glob(_SOURCE_GLOB), key=_parse_ddmmyyyy_from_name, reverse=True)
    if not matches:
        raise FileNotFoundError(f"Không tìm thấy source markdown theo glob: {_SOURCE_GLOB}")
    return matches[0]


def _load_pages(markdown_text: str) -> dict[int, str]:
    matches = list(_PAGE_RE.finditer(markdown_text))
    pages: dict[int, str] = {}
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(markdown_text)
        pages[int(match.group(1))] = markdown_text[start:end].strip()
    return pages


def resolve_cio_report_path(report_id: str) -> Path | None:
    candidates = [
        PROJECT_ROOT.parent / "reports" / f"{report_id}.html",
        PROJECT_ROOT.parent / "reports" / "_archive" / f"{report_id}.html",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def _extract_html_sections(report_html: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    for block in _SECTION_RE.findall(report_html):
        match = _SECTION_LETTER_RE.match(block)
        if match:
            sections[match.group(1)] = block.strip()
    return sections


def _parse_axis_dates(text: str) -> tuple[datetime | None, datetime | None]:
    match = re.search(r"(\d{2}/\d{2}/\d{4})\s*[–-]\s*(\d{2}/\d{2}/\d{4})", text)
    if match:
        return tuple(datetime.strptime(item, "%d/%m/%Y") for item in match.groups())
    match = re.search(r"(\d{2}/\d{2})[–-](\d{2}/\d{2}/\d{4})", text)
    if match:
        start, end = match.groups()
        end_dt = datetime.strptime(end, "%d/%m/%Y")
        return datetime.strptime(f"{start}/{end_dt.year}", "%d/%m/%Y"), end_dt
    return None, None


def _bar_status_from_class(class_name: str | None) -> str | None:
    if not class_name:
        return None
    mapping = {
        "tl-done": "done",
        "tl-progress": "progress",
        "tl-blocked": "blocked",
        "tl-onhold": "onhold",
        "tl-cancelled": "cancelled",
        "tl-open": "open",
    }
    for token, status in mapping.items():
        if token in class_name:
            return status
    return None


def _manual_status_from_text(text: str) -> str | None:
    lowered = text.lower()
    if "blocked" in lowered or "rủi ro" in lowered:
        return "blocked"
    if "on-hold" in lowered or "on hold" in lowered:
        return "onhold"
    if "đóng yêu cầu" in lowered or "đóng tiếp nhận" in lowered or "cancelled" in lowered:
        return "cancelled"
    if "hoàn thành" in lowered:
        return "done"
    if "đang" in lowered or "ready uat" in lowered or "ready golive" in lowered:
        return "progress"
    if "kế hoạch" in lowered or "chưa" in lowered or "to do" in lowered:
        return "open"
    return None


def _build_status_chunks(page_text: str) -> dict[str, str]:
    chunks: dict[str, str] = {}
    id_matches = list(re.finditer(r"(QLYC-\d+|YC-\d+|EK\d+)", page_text))
    for idx, match in enumerate(id_matches):
        start = match.start()
        end = id_matches[idx + 1].start() if idx + 1 < len(id_matches) else len(page_text)
        key = _normalize_backlog_key(match.group(1))
        chunks.setdefault(key, page_text[start:end])
    for first, second in re.findall(r"QLYC-(\d+)\s*/\s*(\d+)", page_text):
        if first in chunks and second not in chunks:
            chunks[second] = chunks[first]
    return chunks


def _parse_page11_counts(page11: str) -> dict[str, str]:
    counts: dict[str, str] = {}
    for lane, _start_q3, current, _note in _PAGE11_ROW_RE.findall(page11):
        counts[lane] = current.replace("*", "")
    return counts


def _parse_page19_note(page19: str) -> tuple[set[str], set[str], list[str]]:
    match = re.search(r"📝\s*Ghi chú Timeline:(.*)", page19, re.S)
    note_text = _normalize_space(match.group(1) if match else "")
    done_ids = {
        id_
        for id_ in re.findall(r"\b(?:QLYC-)?(\d{4,5})\b", note_text)
        if id_ not in {"9609", "10076", "11803"}
    }
    closed_match = re.search(r"3 mã BU \(([^)]+)\)", note_text)
    closed_ids = set(re.findall(r"\b(\d{4,5})\b", closed_match.group(1))) if closed_match else set()
    note_lines = [
        f"📝 Ghi chú Timeline:",
        f"Dữ liệu đến {note_text.split('Timeline hiển thị', 1)[0].strip()}" if note_text.startswith("Dữ liệu đến") else "",
        note_text,
    ]
    return done_ids, closed_ids, [line for line in note_lines if line]


def load_timeline_context(source_path: Path | None = None) -> TimelineContext:
    resolved_source = _resolve_latest_source(source_path)
    pages = _load_pages(resolved_source.read_text(encoding="utf-8"))
    report_date_match = re.search(r"CIO Report\s*\|\s*(\d{2}/\d{2}/\d{4})", pages.get(1, ""))
    report_date = report_date_match.group(1) if report_date_match else _parse_ddmmyyyy_from_name(resolved_source).strftime("%d/%m/%Y")
    extra_done_ids, closed_ids, _note_lines = _parse_page19_note(pages.get(19, ""))
    status_chunks = {
        report_id: _build_status_chunks(pages.get(page_no, ""))
        for report_id, page_no in _REPORT_ID_TO_PAGE.items()
    }
    return TimelineContext(
        source_path=resolved_source,
        report_date=report_date,
        pages=pages,
        page11_counts=_parse_page11_counts(pages.get(11, "")),
        extra_done_ids=extra_done_ids,
        closed_ids=closed_ids,
        status_chunks=status_chunks,
    )


def _parse_manual_timeline_rows(g_section: str) -> tuple[str, str, str, list[str], list[TimelineRow], datetime | None, datetime | None]:
    title_match = _TITLE_RE.search(g_section)
    subtitle_match = _SUBTITLE_RE.search(g_section)
    caveat_match = _CAVEAT_RE.search(g_section)
    months_match = _MONTH_RE.search(g_section)
    title = title_match.group(0) if title_match else "<h2>G. Timeline</h2>"
    subtitle = subtitle_match.group(1) if subtitle_match else ""
    caveat_html = caveat_match.group(0) if caveat_match else ""
    months = [_strip_tags(item) for item in _SPAN_RE.findall(months_match.group("inner"))] if months_match else []
    axis_start, axis_end = _parse_axis_dates(_strip_tags(g_section))

    rows: list[TimelineRow] = []
    for match in _ITEM_RE.finditer(g_section):
        if match.group("qh_text") is not None:
            rows.append(
                TimelineRow(
                    backlog_id="",
                    title="",
                    title_html="",
                    track_title="",
                    bar=None,
                    kind="header",
                    header_text=_strip_tags(match.group("qh_text")),
                )
            )
            continue
        label_html = match.group("label")
        track_inner = match.group("track_inner")
        track_attrs = match.group("track_attrs") or ""
        backlog_id_match = _BACKLOG_ID_RE.search(label_html)
        backlog_id = _strip_tags(backlog_id_match.group(1)) if backlog_id_match else "—"
        title_html = re.sub(r"<span class=\"backlog-id\">.*?</span>", "", label_html, count=1, flags=re.S).strip()
        title_text = _strip_tags(title_html)
        track_title_match = _TRACK_TITLE_RE.search(track_attrs)
        bar_match = _BAR_RE.search(track_inner)
        bar = None
        manual_status = None
        if bar_match:
            bar = TimelineBar(
                class_name=bar_match.group("class"),
                style=bar_match.group("style"),
                label=_strip_tags(bar_match.group("label")),
            )
            manual_status = _bar_status_from_class(bar.class_name)
        markers = [
            TimelineMarker(
                class_name=item.group("class"),
                style=item.group("style"),
                data_label=item.group("data_label"),
                title=item.group("title"),
            )
            for item in _MARKER_RE.finditer(track_inner)
            if "today" not in item.group("class")
        ]
        if not manual_status:
            manual_status = _manual_status_from_text(f"{title_text} {html.unescape(track_title_match.group(1)) if track_title_match else ''}")
        rows.append(
            TimelineRow(
                backlog_id=backlog_id,
                title=title_text,
                title_html=title_html,
                track_title=html.unescape(track_title_match.group(1)) if track_title_match else "",
                bar=bar,
                markers=markers,
                is_empty=bool(match.group("empty")),
                manual_status=manual_status,
            )
        )
    return title, subtitle, caveat_html, months, rows, axis_start, axis_end


def load_lane_timeline(report_id: str, source_path: Path | None = None) -> tuple[TimelineContext, LaneTimeline]:
    context = load_timeline_context(source_path)
    report_path = resolve_cio_report_path(report_id)
    if not report_path:
        raise FileNotFoundError(f"Không tìm thấy report HTML cho {report_id}")
    sections = _extract_html_sections(report_path.read_text(encoding="utf-8"))
    title, subtitle, caveat_html, months, rows, axis_start, axis_end = _parse_manual_timeline_rows(sections.get("G", ""))
    lane_timeline = LaneTimeline(
        report_id=report_id,
        report_path=report_path,
        title=title,
        subtitle=subtitle,
        caveat_html=caveat_html,
        months=months,
        rows=rows,
        axis_start=axis_start,
        axis_end=axis_end,
    )
    enrich_lane_timeline(context, lane_timeline)
    return context, lane_timeline


def _resolve_row_status(context: TimelineContext, report_id: str, row: TimelineRow) -> str:
    key = _normalize_backlog_key(row.backlog_id)
    if key in context.closed_ids:
        return "cancelled"
    if key in context.extra_done_ids:
        return "done"
    chunk = context.status_chunks.get(report_id, {}).get(key, "")
    status_source = f"{chunk} {row.track_title} {row.title}"
    lowered = status_source.lower()
    if "blocked" in lowered or "rủi ro golive" in lowered:
        return "blocked"
    if "đóng yêu cầu" in lowered or "đóng tiếp nhận" in lowered or "cancelled" in lowered:
        return "cancelled"
    if "on-hold" in lowered or "on hold" in lowered:
        return "onhold"
    if "hoàn thành" in lowered or " done" in lowered or "tồn tại và done" in lowered:
        return "done"
    if any(token in lowered for token in ["đang thực hiện", "đang dev", "dev/sit", "ready uat", "ready golive", "đang xử lý", "in progress"]):
        return "progress"
    if any(token in lowered for token in ["kế hoạch", "to do", "chưa cập", "chưa có", "mới mở"]):
        return "open"
    return row.manual_status or "open"


def enrich_lane_timeline(context: TimelineContext, lane_timeline: LaneTimeline) -> None:
    for row in lane_timeline.rows:
        if row.kind == "header":
            continue
        row.resolved_status = _resolve_row_status(context, lane_timeline.report_id, row)


def _timeline_percent(axis_start: datetime, axis_end: datetime, target: datetime) -> float:
    total_days = max((axis_end - axis_start).days, 1)
    elapsed = max(0, min((target - axis_start).days, total_days))
    return elapsed / total_days * 100


def timeline_percent(axis_start: datetime, axis_end: datetime, target: datetime) -> float:
    """Alias public của _timeline_percent — dùng để dashboard_renderer.py tự tính % left/width cho
    1 khung thời gian SUY RA từ cột "Mốc / Deadline" trong bảng C (vd "Q1/2026" -> cả quý, ngày cụ
    thể -> đúng ngày đó), khi backlog CHƯA có bar thật từ TimelineRow (tab Timeline cũ) nhưng có
    mốc deadline nguồn thật (không phải placeholder tự sinh — xem _quarter_deadline_label ở
    dashboard_renderer.py) — theo ĐÚNG axis_start/axis_end của lane để vẫn thẳng hàng với các bar
    khác trên cùng trục (thêm 2026-08-25, theo yêu cầu người dùng)."""
    return _timeline_percent(axis_start, axis_end, target)


def status_display(status: str) -> tuple[str, str]:
    """Trả (css_class, label hiển thị) cho 1 status Timeline đã resolve (done/progress/blocked/
    onhold/cancelled/open) — dùng cho cột Timeline inline trong bảng C
    (dashboard_renderer._render_c_gantt_cell, gộp từ tab "G. Timeline" riêng — 2026-08-25)."""
    return _STATUS_CSS.get(status, "tl-open"), _STATUS_LABEL.get(status, "Kế hoạch còn lại")


def normalize_backlog_key(value: str) -> str:
    """Alias public của _normalize_backlog_key — dùng để ghép (join) 1 backlog-id đọc từ bảng C
    (tab "Backlog kế hoạch", xem dashboard_renderer._group_c_section_by_quarter) với backlog-id
    trong TimelineRow ở đây, khi vẽ Gantt bar ngay trong dòng backlog thay vì ở tab Timeline riêng
    (gộp 2 tab từ 2026-08-25)."""
    return _normalize_backlog_key(value)


def _axis_bounds_from_months(months: list[str]) -> tuple[datetime | None, datetime | None]:
    """Fallback khi report không khai báo khung trục dạng "DD/MM/YYYY – DD/MM/YYYY" trong text G
    section (_parse_axis_dates trả None, None — gặp ở CIO-EKYC/CIO-MCONNECT) nhưng VẪN có dải
    tháng ở .timeline-scale-months (vd "11/2025".."10/2026") — suy axis_start/axis_end từ tháng đầu
    /cuối dải đó, đủ để tính % vị trí cho khung Mốc/Deadline suy ra (thêm 2026-08-25)."""
    parsed = [
        (int(m.group(2)), int(m.group(1)))
        for m in (re.match(r"^(\d{1,2})/(\d{4})$", item.strip()) for item in months)
        if m
    ]
    if not parsed:
        return None, None
    parsed.sort()
    first_year, first_month = parsed[0]
    last_year, last_month = parsed[-1]
    start = datetime(first_year, first_month, 1)
    end = datetime(last_year, 12, 31) if last_month == 12 else datetime(last_year, last_month + 1, 1) - timedelta(days=1)
    return start, end


def load_lane_timeline_bars(report_id: str, source_path: Path | None = None) -> dict:
    """Trả về đúng dữ liệu cần để vẽ Gantt bar/marker inline ngay trong mỗi dòng bảng Backlog kế
    hoạch (tab C) — thay cho tab "Timeline" riêng đã gộp vào C. `rows_by_key` map
    normalize_backlog_key(backlog_id) -> TimelineRow (đã có sẵn .bar.style/.class_name/.markers
    tính % left/width theo axis chung của lane, xem _timeline_percent — chỉ việc tái sử dụng
    nguyên style, không tính lại). `rows_by_title` là fallback khi backlog
    không có mã QLYC/YC/RE/PA chuẩn để ghép theo id (vd lane IBD — backlog rời rạc từ chat Zalo,
    không có mã) — dashboard_renderer._inject_gantt_column tự làm fuzzy-match theo token tiêu đề
    (cùng cơ chế _resolve_workflow_group đã dùng cho cột Workflow). `axis_start`/`axis_end` (có thể
    None nếu report không có khung trục hợp lệ VÀ không suy được từ dải tháng — xem
    _axis_bounds_from_months) đi kèm timeline_percent() để dashboard_renderer.py tự suy ra 1 khung
    thời gian từ cột "Mốc / Deadline" khi backlog chưa có bar thật."""
    context, lane_timeline = load_lane_timeline(report_id, source_path)
    axis_start, axis_end = lane_timeline.axis_start, lane_timeline.axis_end
    if axis_start is None or axis_end is None:
        fallback_start, fallback_end = _axis_bounds_from_months(lane_timeline.months)
        axis_start = axis_start or fallback_start
        axis_end = axis_end or fallback_end
    today_pct = None
    if axis_start and axis_end:
        report_dt = datetime.strptime(context.report_date, "%d/%m/%Y")
        today_pct = _timeline_percent(axis_start, axis_end, report_dt)
    rows_by_key: dict[str, TimelineRow] = {}
    rows_by_title: list[tuple[str, TimelineRow]] = []
    for row in lane_timeline.rows:
        if row.kind == "header":
            continue
        if row.backlog_id and row.backlog_id != "—":
            rows_by_key[_normalize_backlog_key(row.backlog_id)] = row
        if row.title:
            rows_by_title.append((row.title, row))
    return {
        "rows_by_key": rows_by_key,
        "rows_by_title": rows_by_title,
        "axis_start": axis_start,
        "axis_end": axis_end,
        "months": lane_timeline.months,
        "today_pct": today_pct,
        "report_date": context.report_date,
    }


def build_slide19_lane_rows(source_path: Path | None = None) -> dict[str, list[tuple[str, str]]]:
    result: dict[str, list[tuple[str, str]]] = {}
    for report_id in _MAIN_SLIDE_ORDER:
        context, lane_timeline = load_lane_timeline(report_id, source_path)
        rows: list[tuple[str, str]] = []
        for row in lane_timeline.rows:
            if row.kind == "header":
                continue
            status = row.resolved_status or row.manual_status or "open"
            prefix = _STATUS_PREFIX.get(status, "")
            base = f"{row.backlog_id} {row.title}".strip()
            rows.append((_REPORT_ID_TO_SLIDE_LANE[report_id], f"{prefix}{base}"))
        result[_REPORT_ID_TO_SLIDE_LANE[report_id]] = rows
    return result


def materialize_slide19_xml(
    template_slide_xml: bytes,
    context: TimelineContext,
    lane_rows: dict[str, list[tuple[str, str]]],
) -> bytes:
    root = ET.fromstring(template_slide_xml)
    text_shapes: list[tuple[str, ET.Element]] = []
    for sp in root.findall(".//p:sp", _PPT_NS):
        texts = [node.text.strip() for node in sp.findall(".//a:t", _PPT_NS) if node.text and node.text.strip()]
        if texts:
            text_shapes.append((" | ".join(texts), sp))

    counts = {
        _REPORT_ID_TO_SLIDE_LANE[report_id]: context.page11_counts.get(_REPORT_ID_TO_PAGE11_LANE[report_id], str(len(lane_rows[_REPORT_ID_TO_SLIDE_LANE[report_id]])))
        for report_id in _MAIN_SLIDE_ORDER
    }
    lane_heading_shapes: list[tuple[str, ET.Element, int]] = []
    for idx, (text, sp) in enumerate(text_shapes):
        for lane_name in counts:
            if text.startswith(lane_name):
                lane_heading_shapes.append((lane_name, sp, idx))
                break

    for lane_name, sp, _idx in lane_heading_shapes:
        _set_shape_texts(sp, [f"{lane_name}  ·  {counts[lane_name]} yêu cầu"])

    for idx, (lane_name, _sp, shape_idx) in enumerate(lane_heading_shapes):
        row_texts = [text for _lane, text in lane_rows.get(lane_name, [])]
        next_idx = lane_heading_shapes[idx + 1][2] if idx + 1 < len(lane_heading_shapes) else len(text_shapes)
        cursor = 0
        for item_idx in range(shape_idx + 1, next_idx):
            text, sp = text_shapes[item_idx]
            if text.startswith("📝 Ghi chú Timeline"):
                break
            if cursor >= len(row_texts):
                break
            _set_shape_texts(sp, [row_texts[cursor]])
            cursor += 1

    short_date = context.report_date.lstrip("0").replace("/0", "/")
    for text, sp in text_shapes:
        if text.startswith("▼ Hôm nay"):
            _set_shape_texts(sp, [f"▼ Hôm nay {short_date[:-5] if short_date.endswith('/2026') else short_date}"])
        elif text.startswith("📝 Ghi chú Timeline"):
            note_lines = [
                "📝 Ghi chú Timeline:",
                f"Tiền tố [D]/[P]/[H]/[C]/[B] phản ánh trạng thái backlog mới nhất từ kho tri thức khi chưa có đủ mốc ngày chi tiết để vẽ lại bar động. Dữ liệu đến {context.report_date}.",
                "3 mã BU (9609, 10076, 11803) là Đóng tiếp nhận YC, không tính Done. Chi tiết lane-level xem Dashboard realtime.",
            ]
            _set_shape_texts(sp, note_lines)

    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _set_shape_texts(shape: ET.Element, texts: list[str]) -> None:
    nodes = shape.findall(".//a:t", _PPT_NS)
    for idx, node in enumerate(nodes):
        node.text = texts[idx] if idx < len(texts) else ""


def rewrite_zip_member(zip_path: Path, updates: dict[str, bytes]) -> None:
    tmp_path = zip_path.with_suffix(zip_path.suffix + ".tmp")
    with zipfile.ZipFile(zip_path, "r") as src, zipfile.ZipFile(tmp_path, "w") as dst:
        for info in src.infolist():
            payload = updates.get(info.filename)
            if payload is None:
                payload = src.read(info.filename)
            dst.writestr(info, payload)
    tmp_path.replace(zip_path)
