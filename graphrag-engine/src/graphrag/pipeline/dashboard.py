from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from ..config import PROJECT_ROOT, Settings
from ..generation.answer_generator import call_llm
from ..ingestion.loader import iter_documents

_PROMPT_PATH = PROJECT_ROOT / "config" / "prompts" / "daily_dashboard.md"
_INBOX = "Chưa phân loại"
_NOTE_DIRS = ("_zalo-notes", "_chat-notes")
_RECENT_DAYS = 14
_CHARS_PER_TOKEN = 4
_CONTEXT_TOKEN_BUDGET = 30_000  # ~ ngân sách context cho 1 lần gọi LLM / lane

# Mapping lane -> Report ID đã soát tay, copy đúng bảng "Report ID hiện có" ở reports/README.md.
# Không suy đoán tự động từ tên folder (không có quy tắc chung đủ tin cậy) — cập nhật tay ở đây
# khi có ID mới, cùng lúc với khi thêm dòng mới vào bảng trong README.
_LANE_TO_REPORT_ID = {
    "MSBPay": "CIO-MSBPAY",
    "Magnet": "CIO-MAGNET",
    "DIP": "CIO-DIP",
    "MConnect": "CIO-MCONNECT",
    "New_eKYC": "CIO-EKYC",
    "ibd": "CIO-IBD",
}

# Mapping lane -> tenant root (path tính từ repo root, xem tenants/registry.yaml) — kể từ khi
# tách tenants/ (2026-08-22), mỗi lane = đúng 1 tenant có index riêng, không còn suy project từ
# cấu trúc path trong 1 cây normalized/ chung nữa (org_paths.py cũ đã bỏ). Cập nhật tay ở đây
# cùng lúc với _LANE_TO_REPORT_ID khi có lane mới hoặc tenant đổi vị trí.
_LANE_TO_TENANT_ROOT = {
    "MSBPay": "tenants/msb/msbpay",
    "Magnet": "tenants/msb/magnet",
    "DIP": "tenants/msb/dip",
    "MConnect": "tenants/msb/mconnect",
    "New_eKYC": "tenants/msb/ekyc",
    "ibd": "tenants/minmo/saleman-legacy/instances/ibd",
}

_DASHBOARD_ASOF_RE = re.compile(r'<div class="asof-chip">.*?<strong>([^<]+)</strong>', re.S)
_E_PANEL_OPEN_RE = re.compile(r'<div id="([^"]+-e)" class="lane-tab-panel(?: active)?">')


@dataclass
class LaneResult:
    lane: str
    summary: str = ""
    tasks: list[dict] = field(default_factory=list)
    risks: list[dict] = field(default_factory=list)
    n_sources: int = 0
    error: str | None = None
    preserved_e_html: str | None = None
    preserved_notice: str | None = None
    report_id: str | None = None  # CIO-<ID> đã soát tay cho lane này (nếu có) — dashboard_renderer
    # dùng để trích A/B/C/D từ reports/<report_id>.html; None nghĩa là lane chưa có báo cáo CIO.


def _slug(lane: str) -> str:
    return "tab-" + "".join(c if c.isalnum() else "-" for c in lane).strip("-").lower()


def _extract_div_inner_html(text: str, start_index: int) -> str | None:
    open_end = text.find(">", start_index)
    if open_end == -1:
        return None
    depth = 1
    pos = open_end + 1
    for match in re.finditer(r"</?div\b[^>]*>", text[pos:], re.I):
        token = match.group(0)
        if token.startswith("</"):
            depth -= 1
        else:
            depth += 1
        if depth == 0:
            inner_end = pos + match.start()
            return text[pos:inner_end]
    return None


def _is_healthy_previous_e_panel(html: str) -> bool:
    lowered = html.lower()
    if "lỗi tổng hợp lane này" in lowered:
        return False
    return "<table" in lowered or "không có dữ liệu nguồn nào cho lane này." in lowered


def _load_previous_e_panels(path: Path) -> tuple[dict[str, str], str | None]:
    if not path.exists():
        return {}, None
    text = path.read_text(encoding="utf-8")
    asof_match = _DASHBOARD_ASOF_RE.search(text)
    asof = asof_match.group(1) if asof_match else None
    panels: dict[str, str] = {}
    for match in _E_PANEL_OPEN_RE.finditer(text):
        inner_html = _extract_div_inner_html(text, match.start())
        if inner_html and _is_healthy_previous_e_panel(inner_html):
            panels[match.group(1)] = inner_html
    return panels, asof


def _friendly_error_label(error: str) -> str:
    lowered = error.lower()
    if "insufficient_quota" in lowered or "credit_balance_exhausted" in lowered:
        return "hết quota OpenAI"
    if "429" in lowered:
        return "lỗi giới hạn / quota của LLM"
    return "lỗi LLM/API"


def _gather_lane_blocks(docs: list) -> tuple[list[tuple[float, str]], list[tuple[float, str]], int]:
    """Trả về (block "luôn giữ" từ _zalo-notes/_chat-notes, block "gần đây" từ file khác, tổng số
    nguồn đã gom trước khi cắt). `docs` = list SourceDocument đọc từ ĐÚNG 1 tenant (xem
    build_dashboard — mỗi lane giờ = 1 tenant riêng, index vật lý tách biệt, nên không còn cần
    lọc theo lane nữa như hồi còn 1 cây normalized/ chung) — đọc trực tiếp file, không qua vector
    search, tránh bỏ sót như từng gặp khi làm reports/CIO-*.html (xem reports/README.md, mục
    "chỉ Glob _zalo-notes/ và _chat-notes/ là chưa đủ")."""
    cutoff = datetime.now() - timedelta(days=_RECENT_DAYS)
    always: list[tuple[float, str]] = []
    recent: list[tuple[float, str]] = []
    n_sources = 0

    for doc in docs:
        mtime = doc.path.stat().st_mtime
        block = f"[{doc.source_channel} | {doc.date} | {doc.rel_path}]\n{doc.text.strip()}\n"
        n_sources += 1
        is_note = any(part in _NOTE_DIRS for part in doc.path.parts)
        if is_note:
            always.append((mtime, block))
        elif datetime.fromtimestamp(mtime) >= cutoff:
            recent.append((mtime, block))

    return always, recent, n_sources


def _build_context(docs: list) -> tuple[str, int]:
    always, recent, n_sources = _gather_lane_blocks(docs)
    always.sort(key=lambda t: t[0])
    recent.sort(key=lambda t: t[0], reverse=True)  # mới nhất trước, cắt cái cũ trước nếu vượt ngân sách

    budget = _CONTEXT_TOKEN_BUDGET * _CHARS_PER_TOKEN
    used = sum(len(b) for _, b in always)
    kept_recent = []
    for mtime, block in recent:
        if used + len(block) > budget:
            continue
        kept_recent.append((mtime, block))
        used += len(block)
    kept_recent.sort(key=lambda t: t[0])

    blocks = [b for _, b in always] + [b for _, b in kept_recent]
    return "\n---\n".join(blocks), n_sources


def _extract_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    return json.loads(text)


def _build_lane(lane_name: str, docs: list, settings: Settings) -> LaneResult:
    from ..generation.dashboard_renderer import load_manual_e_data
    from ..timeline_sync import resolve_cio_report_path

    report_id = _LANE_TO_REPORT_ID.get(lane_name)
    if report_id and resolve_cio_report_path(report_id):
        # Lane đã có báo cáo CIO thủ công — luôn dùng bảng E đã soát tay trong đó (kể cả khi rỗng,
        # vd MConnect với khối .caveat "không có dữ liệu nguồn"), KHÔNG gọi LLM nữa — quyết định
        # người dùng 2026-08-06 (trước đó E được LLM tự tổng hợp mỗi sáng từ nguồn thô). Chỉ những
        # lane CHƯA có report_id/report file (project mới chưa kịp làm CIO report, hoặc mục "Chưa
        # phân loại") mới rơi xuống nhánh LLM bên dưới.
        tasks, risks, n_tasks = load_manual_e_data(report_id)
        return LaneResult(lane=lane_name, tasks=tasks, risks=risks, n_sources=n_tasks, report_id=report_id)

    context, n_sources = _build_context(docs)
    if not context.strip():
        return LaneResult(
            lane=lane_name,
            summary="Không có dữ liệu nguồn nào cho lane này.",
            n_sources=0,
            report_id=report_id,
        )

    template = _PROMPT_PATH.read_text(encoding="utf-8")
    prompt = (
        template.replace("{{lane}}", lane_name)
        .replace("{{cio_note}}", "")
        .replace("{{context}}", context)
    )

    try:
        # Gọi cùng provider/model đang dùng cho graphrag query (settings.answer_provider/model)
        # — KHÔNG lọc sensitivity=confidential trước khi gửi ở đây (khác mọi chỗ khác trong hệ
        # thống: embedding local, graph extraction local/bỏ qua, generate_answer() bình thường
        # qua CLI luôn dùng --no-answer nên không gọi cloud). Đây là NGOẠI LỆ CÓ CHỦ ĐÍCH — người
        # dùng đã xác nhận chấp nhận gửi cả nội dung _zalo-notes/ (confidential) qua cloud LLM
        # riêng cho lệnh `graphrag dashboard` này, đổi lấy tổng hợp đầy đủ tín hiệu hơn. Từ
        # 2026-08-06, nhánh này CHỈ còn chạy cho lane chưa có CIO report thủ công (xem đầu
        # _build_lane) — 6 lane chính đã có report không còn gọi LLM/gửi zalo-notes ra cloud nữa.
        # Không phải lỗ hổng/thiếu sót, không tự ý sửa lại thành lọc confidential nếu không có yêu
        # cầu mới từ người dùng.
        raw = call_llm(prompt, settings.answer_provider, settings.answer_model, max_tokens=4096)
        data = _extract_json(raw)
    except Exception as e:  # noqa: BLE001 — lane lỗi không được làm sập cả lệnh, các lane khác vẫn phải render
        return LaneResult(lane=lane_name, error=str(e), n_sources=n_sources, report_id=report_id)

    return LaneResult(
        lane=lane_name,
        summary=str(data.get("summary", "")),
        tasks=list(data.get("tasks", []) or []),
        risks=list(data.get("risks", []) or []),
        n_sources=n_sources,
        report_id=report_id,
    )


def build_dashboard() -> Path:
    """Không còn nhận `settings: Settings` từ ngoài — dashboard tổng hợp CROSS-TENANT (mỗi lane
    là 1 tenant riêng, tự index riêng, xem _LANE_TO_TENANT_ROOT), khác `build`/`query`/`health`
    vốn chỉ chạy trong phạm vi đúng 1 tenant qua --tenant-root. Tự load 1 Settings riêng cho mỗi
    lane bên trong."""
    from ..generation.dashboard_renderer import render_dashboard
    from ..timeline_sync import resolve_cio_report_path

    repo_root = PROJECT_ROOT.parent
    out_path = repo_root / "reports" / "DASHBOARD.html"

    # Chốt vận hành 2026-08-06: reports/CIO-<ID>.html giờ chỉ còn là file build TẠM THỜI (Claude
    # sinh ra lúc "build lại DASHBOARD", fold nội dung vào đây rồi archive đi ngay) — không còn
    # tồn tại thường trực trong reports/ nữa. Vì `_build_lane()` im lặng rơi về LLM/empty-state khi
    # không thấy file CIO report, một lần chạy `graphrag dashboard` "trần" (không qua đúng quy
    # trình sinh CIO tạm trước) sẽ XOÁ SẠCH nội dung DASHBOARD hiện có mà không cảnh báo gì — guard
    # dưới đây chặn đúng tình huống đó.
    known_report_ids = set(_LANE_TO_REPORT_ID.values())
    reports_with_file = {rid for rid in known_report_ids if resolve_cio_report_path(rid)}
    if out_path.exists() and not reports_with_file:
        raise RuntimeError(
            "Không thấy file reports/CIO-*.html nào cho 6 lane đã biết, nhưng "
            f"{out_path} đang có sẵn nội dung — dừng lại, KHÔNG ghi đè rỗng. reports/CIO-<ID>.html "
            "giờ chỉ là staging tạm thời: cần Claude sinh lại đủ 6 file đó trước (nghiên cứu từng "
            "lane), rồi mới gọi `graphrag dashboard`, rồi mới archive — không tự chạy lệnh CLI này "
            "một mình khi thư mục reports/ đã được dọn sạch."
        )

    previous_e_panels, previous_asof = _load_previous_e_panels(out_path)

    # Mỗi lane MSB chính thức = 1 tenant riêng, tự Settings + iter_documents riêng (thay vì đọc
    # 1 cây normalized/ chung rồi lọc theo lane như hồi trước khi tách tenants/).
    results: list[LaneResult] = []
    for lane, tenant_rel in _LANE_TO_TENANT_ROOT.items():
        tenant_settings = Settings.load(tenant_root=repo_root / tenant_rel)
        docs = list(iter_documents(tenant_settings))
        results.append(_build_lane(lane, docs, tenant_settings))

    # "Chưa phân loại" — không còn tenant/inbox nào ánh xạ tới (01_Inbox cũ đã dọn khi tách
    # tenants/, 2 note còn giá trị đã chuyển vào docs/decisions/) — luôn rỗng, giữ tab để không
    # âm thầm biến mất nếu sau này có nội dung thật cần xếp vào đây. Settings() không tenant_root
    # vẫn đọc đúng config/settings.yaml chung của engine (answer_provider/model) dù normalized_dir
    # của nó không tồn tại — không sao vì docs=[] nên không có iter_documents() nào chạy tới đó.
    results.append(_build_lane(_INBOX, [], Settings.load()))

    for lr in results:
        if not lr.error:
            continue
        preserved = previous_e_panels.get(f"{_slug(lr.lane)}-e")
        if not preserved:
            continue
        lr.preserved_e_html = preserved
        when = f" lúc {previous_asof}" if previous_asof else " ở lần build trước"
        lr.preserved_notice = (
            f"Không thể tổng hợp mới do {_friendly_error_label(lr.error)}, nên đang giữ dữ liệu tab E cũ{when}."
        )

    html = render_dashboard(results, generated_at=datetime.now())
    out_path.write_text(html, encoding="utf-8")
    return out_path
