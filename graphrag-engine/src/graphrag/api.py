from __future__ import annotations

import base64
import json
import os
import re
import secrets
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import local_ocr
from . import people as _people
from .agent_ops import add_confirmation, build_agents_report
from .capture import capture_status, list_capture_folders, preview_capture, write_image_note, write_note
from .config import PROJECT_ROOT, Settings, update_llm_models
from .document_search import search_documents
from .generation.dashboard_renderer import _CSS, _JS_ENHANCED, _SYNC_CONFIG_HTML
from .ingestion.health import build_health_report
from .knowledge_home import build_knowledge_home
from .knowledge_links import build_document_detail, build_knowledge_anchor_detail, build_knowledge_links_report
from .pipeline.build_index import build_index as _build_index
from .pipeline.query import query as _query
from .tenant_browser import (
    TENANTS_ROOT,
    build_document_tree,
    get_project_logs_timeline,
    list_project_logs,
    list_tenant_registry,
)
from .reporting import (
    ReportError,
    build_report as _build_report,
    get_report_artifact_path,
    get_report_detail,
    list_report_summaries,
)

app = FastAPI(title="graphrag-engine (guest)", docs_url="/docs")
app.add_middleware(
    CORSMiddleware,
    # Dashboard đang được mở trực tiếp qua file:///... nên browser gửi Origin: null.
    # Cần cho phép origin này để preflight OPTIONS tới /run-sync không bị 405.
    allow_origins=[
        "null",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost",
        "http://127.0.0.1",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

def _load_settings() -> Settings:
    """`graphrag serve --tenant-root <path>` set biến môi trường GRAPHRAG_TENANT_ROOT trước khi
    uvicorn import module này (xem cli.py) — mỗi API instance giờ chỉ phục vụ đúng 1 tenant,
    khác trước khi tách tenants/ (2026-08-22) khi có 1 index chung cho mọi org/project."""
    tenant_root = os.environ.get("GRAPHRAG_TENANT_ROOT")
    return Settings.load(tenant_root=Path(tenant_root) if tenant_root else None)


_settings = _load_settings()
_LOG_PATH = _settings.cache_dir / "api_access.log"
_DASHBOARD_PATH = PROJECT_ROOT.parent / "reports" / "DASHBOARD.html"


def _resolve_tenant_settings(tenant: str | None) -> Settings:
    """`tenant` dạng 'org/workspace' (vd 'msb/magnet', '_common', hoặc lồng instance
    'minmo/saleman-legacy/instances/ibd') — map thẳng sang tenants/<tenant>/, dùng cho các
    endpoint duyệt UI mới (Phase 6 — tổ chức lại dashboard theo cây tenant, 2026-08-25) cần đọc
    dữ liệu của BẤT KỲ tenant nào, không chỉ đúng 1 tenant mà `graphrag serve` đang bind
    (`_settings`). Bỏ trống `tenant` = giữ hành vi cũ (dùng `_settings`), để không phá endpoint
    cũ nào đang gọi không kèm tham số này."""
    if not tenant:
        return _settings
    tenants_root = TENANTS_ROOT.resolve()
    tenant_root = (tenants_root / tenant).resolve()
    try:
        tenant_root.relative_to(tenants_root)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"tenant không hợp lệ: {tenant!r}") from None
    if not (tenant_root / "normalized").is_dir():
        raise HTTPException(status_code=404, detail=f"Không tìm thấy tenant '{tenant}' (chưa có normalized/)")
    return Settings.load(tenant_root=tenant_root)

# Vá "chrome" (CSS/JS/panel Settings) vào file dashboard TĨNH mỗi lần phục vụ trang — KHÔNG đụng
# nội dung <main class="content"> (tổng hợp từng lane, tốn LLM). Nhờ vậy sửa Settings panel/JS
# thấy ngay khi F5 mà không phải chạy lại `graphrag dashboard` (tốn ~1 lần gọi OpenAI/lane).
# Dùng hàm lambda làm replacement (không phải string thường) vì _JS_ENHANCED chứa nhiều `\\` —
# re.sub coi string replacement là có backreference (\1, \g<...>), sẽ lỗi/sai nếu truyền thẳng.
_STYLE_RE = re.compile(r"<style>.*?</style>", re.S)
_SYNC_CONFIG_RE = re.compile(r'<details class="sync-config">.*?</details>', re.S)
_SCRIPT_RE = re.compile(r"<script>.*?</script>", re.S)


def _patch_dashboard_chrome(html: str) -> str:
    html = _STYLE_RE.sub(lambda _m: f"<style>{_CSS}</style>", html, count=1)
    html = _SYNC_CONFIG_RE.sub(lambda _m: _SYNC_CONFIG_HTML, html, count=1)
    html = _SCRIPT_RE.sub(lambda _m: f"<script>{_JS_ENHANCED}</script>", html, count=1)
    return html


@app.get("/")
def dashboard_root() -> HTMLResponse:
    if not _DASHBOARD_PATH.exists():
        raise HTTPException(status_code=404, detail=f"Chưa thấy dashboard tại {_DASHBOARD_PATH}")
    html = _DASHBOARD_PATH.read_text(encoding="utf-8")
    return HTMLResponse(content=_patch_dashboard_chrome(html))


@app.get("/dashboard")
def dashboard_file() -> HTMLResponse:
    return dashboard_root()


class QueryRequest(BaseModel):
    question: str


class QueryResponse(BaseModel):
    answer: str
    sources: list[str]


class QueryDebugChunk(BaseModel):
    chunk_id: str
    source_path: str
    section: str
    project: str
    source_channel: str
    date: str
    reliability: str
    start_line: int
    end_line: int


class QueryDebugResponse(BaseModel):
    answer: str
    planner_mode: str
    retrieval_trace: list[str]
    context_chunk_ids: list[str]
    context: str
    context_stats: dict[str, int]
    chunks: list[QueryDebugChunk]


class CaptureRequest(BaseModel):
    folder: str
    text: str
    tenant: str | None = None


class CaptureResponse(BaseModel):
    saved_to: str
    n_chunks_indexed: int
    skipped_duplicate: int = 0


class CaptureImageRequest(BaseModel):
    folder: str
    image_base64: str
    tenant: str | None = None


class CaptureStatusResponse(BaseModel):
    captured: bool
    last_captured_at: str | None = None
    note_count: int = 0


class CapturePreviewRequest(BaseModel):
    text: str
    tenant: str | None = None


class CapturePreviewResponse(BaseModel):
    total: int
    new_count: int
    seen_count: int
    oldest_already_seen: bool | None = None


class RunSyncResponse(BaseModel):
    success: bool
    exit_code: int
    log_tail: str


class PersonRequest(BaseModel):
    canonical: str
    aliases: list[str] = []


class IgnoreRequest(BaseModel):
    names: list[str]


class PeopleResponse(BaseModel):
    registry: list[dict]
    unmapped_by_channel: dict[str, list[dict]]
    ignored: list[str]


def _check_api_key(x_api_key: str | None) -> None:
    expected = os.environ.get("GUEST_API_KEY")
    if not expected:
        raise HTTPException(status_code=503, detail="GUEST_API_KEY chưa được set trong .env — server chưa sẵn sàng.")
    if not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="Thiếu hoặc sai X-API-Key.")


def _check_owner_key(x_api_key: str | None) -> None:
    # Tách biệt hoàn toàn GUEST_API_KEY (chỉ đọc, dùng chung được) — endpoint này GHI dữ liệu,
    # tuyệt đối không được chấp nhận guest key.
    expected = os.environ.get("OWNER_API_KEY")
    if not expected:
        raise HTTPException(status_code=503, detail="OWNER_API_KEY chưa được set trong .env — server chưa sẵn sàng.")
    if not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="Thiếu hoặc sai X-API-Key.")


def _log_access(question: str, n_chunks: int) -> None:
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "question": question,
        "n_chunks": n_chunks,
    }
    with open(_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


@app.post("/query", response_model=QueryResponse)
def query_endpoint(req: QueryRequest, x_api_key: str | None = Header(default=None)) -> QueryResponse:
    """API cho người khác truy vấn (guest=True) — chỉ thấy chunk visibility='shared', graph
    layer luôn tắt (đang tắt mặc định cho cả owner, xem README). Không trả raw chunk text,
    chỉ answer đã tổng hợp + danh sách nguồn trích dẫn, giống output CLI `graphrag query`.
    federate_common mặc định True (_query không truyền tường minh) — nhưng tenants/_common
    hiện CHƯA có config/settings.yaml overlay nên mọi chunk của nó mặc định visibility='owner',
    bị filter guest loại hết -> federation với _common thực tế KHÔNG có tác dụng cho guest cho
    tới khi ai đó chủ động thêm overlay visibility_rules cho _common (quyết định chính sách, chưa
    tự làm). Owner (`/query-debug`, CLI) không bị ảnh hưởng, thấy _common bình thường."""
    _check_api_key(x_api_key)
    try:
        result = _query(req.question, _settings, generate=True, use_graph=False, guest=True)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    sources = [f"{c.source_path}#{c.section or 'root'}" for c in result["chunks"]]
    _log_access(req.question, len(result["chunks"]))
    answer = result["answer"] or "Không tìm thấy thông tin phù hợp trong phạm vi được chia sẻ."
    return QueryResponse(answer=answer, sources=sources)


@app.post("/query-debug", response_model=QueryDebugResponse)
def query_debug_endpoint(
    req: QueryRequest, x_api_key: str | None = Header(default=None)
) -> QueryDebugResponse:
    """Owner-only — truy vấn trên toàn bộ index và trả thêm planner/retrieval trace để debug UI."""
    _check_owner_key(x_api_key)
    try:
        result = _query(req.question, _settings, generate=True, use_graph=False, guest=False)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    chunks = result["chunks"]
    included_ids = set(result.get("context_chunk_ids", []))
    included_chunks = [c for c in chunks if c.chunk_id in included_ids]
    unique_candidate_sources = {c.source_path for c in chunks}
    unique_context_sources = {c.source_path for c in included_chunks}

    return QueryDebugResponse(
        answer=result["answer"] or "Không tìm thấy thông tin phù hợp trong index hiện tại.",
        planner_mode=result.get("planner_mode", "unknown"),
        retrieval_trace=result.get("retrieval_trace", []),
        context_chunk_ids=result.get("context_chunk_ids", []),
        context=result.get("context", ""),
        context_stats={
            "candidate_chunks": len(chunks),
            "context_chunks": len(included_chunks),
            "candidate_sources": len(unique_candidate_sources),
            "context_sources": len(unique_context_sources),
            "context_duplicate_chunks": max(0, len(included_chunks) - len(unique_context_sources)),
        },
        chunks=[
            QueryDebugChunk(
                chunk_id=c.chunk_id,
                source_path=c.source_path,
                section=c.section or "root",
                project=c.project,
                source_channel=c.source_channel,
                date=c.date,
                reliability=c.reliability,
                start_line=c.start_line,
                end_line=c.end_line,
            )
            for c in chunks
        ],
    )


@app.get("/folders")
def folders_endpoint(tenant: str | None = None, x_api_key: str | None = Header(default=None)) -> list[str]:
    """Owner-only — danh mục folder cho dropdown extension thu thập tri thức. `tenant` (dạng
    'org/workspace', vd 'msb/ekyc') chọn đúng tenant cần liệt kê — bỏ trống = tenant server đang
    bind qua --tenant-root (hành vi cũ, giữ tương thích ngược cho client chưa gửi tenant)."""
    _check_owner_key(x_api_key)
    return list_capture_folders(_resolve_tenant_settings(tenant))


@app.post("/capture", response_model=CaptureResponse)
def capture_endpoint(req: CaptureRequest, x_api_key: str | None = Header(default=None)) -> CaptureResponse:
    """Owner-only — ghi nội dung tự chọn (vd bôi đen từ Zalo Web) thành 1 note mới, luôn
    confidential/owner cứng (xem capture.write_note), rồi build lại index ngay (vector-only)
    để tra cứu được luôn, giống cách CLAUDE.md yêu cầu chạy `graphrag build` sau khi ghi note —
    ở đây chạy tự động vì capture xảy ra ngoài phiên Claude Code. `req.tenant` chọn đúng tenant
    (org/workspace) chứa `req.folder` — bỏ trống = tenant server đang bind."""
    _check_owner_key(x_api_key)
    settings = _resolve_tenant_settings(req.tenant)
    try:
        path, skipped = write_note(req.folder, req.text, settings)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    n_chunks = _build_index(settings, build_graph=False)
    return CaptureResponse(
        saved_to=str(path.relative_to(settings.normalized_dir)),
        n_chunks_indexed=n_chunks,
        skipped_duplicate=skipped,
    )


@app.get("/capture-status", response_model=CaptureStatusResponse)
def capture_status_endpoint(
    group_id: str = "",
    group_name: str = "",
    tenant: str | None = None,
    x_api_key: str | None = Header(default=None),
) -> CaptureStatusResponse:
    """Owner-only — đoạn Zalo đang mở (group_id cho group chat, group_name fallback cho chat
    1-1) đã có note capture nào chưa — dùng cho badge tự động trên icon extension. `tenant` chọn
    đúng tenant cần tra — bỏ trống = tenant server đang bind."""
    _check_owner_key(x_api_key)
    if not group_id and not group_name:
        raise HTTPException(status_code=400, detail="Cần group_id hoặc group_name.")
    return CaptureStatusResponse(**capture_status(group_id, group_name, _resolve_tenant_settings(tenant)))


@app.post("/capture-preview", response_model=CapturePreviewResponse)
def capture_preview_endpoint(
    req: CapturePreviewRequest, x_api_key: str | None = Header(default=None)
) -> CapturePreviewResponse:
    """Owner-only — xem trước (KHÔNG ghi gì) tin nào trong `req.text` đã từng capture, theo đúng
    cơ chế lọc trùng-theo-hash mà `/capture` thực sự dùng khi lưu (xem capture.preview_capture) —
    dùng cho extension kiểm tra "độ phủ" trước khi bấm Lưu, đáng tin hơn tự đoán qua khoảng thời
    gian vì so khớp đúng từng tin nhắn thật. `req.tenant` chọn đúng tenant — bỏ trống = tenant
    server đang bind."""
    _check_owner_key(x_api_key)
    return CapturePreviewResponse(**preview_capture(req.text, _resolve_tenant_settings(req.tenant)))


@app.post("/capture-image", response_model=CaptureResponse)
def capture_image_endpoint(
    req: CaptureImageRequest, x_api_key: str | None = Header(default=None)
) -> CaptureResponse:
    """Owner-only — OCR local (Tesseract) 1 ảnh từ Zalo Web, KHÔNG gọi cloud OCR nào (khác
    normalize-engine/converters/ocr.py dùng cho artifacts/ — ảnh Zalo áp nguyên tắc riêng, xem
    ARCHITECTURE.md §8.1). Trả 503 rõ ràng nếu Tesseract chưa cài, không âm thầm rơi về cloud.
    `req.tenant` chọn đúng tenant chứa `req.folder` — bỏ trống = tenant server đang bind."""
    _check_owner_key(x_api_key)
    if not local_ocr.is_available():
        raise HTTPException(
            status_code=503,
            detail="Tesseract chưa cài hoặc không tìm thấy — xem README mục OCR local để cài.",
        )
    settings = _resolve_tenant_settings(req.tenant)
    try:
        image_bytes = base64.b64decode(req.image_base64)
        ocr_text = local_ocr.ocr_image(image_bytes)
        path = write_image_note(req.folder, ocr_text, settings)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except OSError as e:
        # PIL.UnidentifiedImageError (ảnh hỏng/không phải file ảnh) kế thừa OSError, không
        # phải ValueError — bắt riêng để trả lỗi rõ ràng thay vì 500 chung chung.
        raise HTTPException(status_code=400, detail=f"Không đọc được ảnh: {e}") from e

    n_chunks = _build_index(settings, build_graph=False)
    return CaptureResponse(saved_to=str(path.relative_to(settings.normalized_dir)), n_chunks_indexed=n_chunks)


_SYNC_SCRIPTS = {
    # mode -> (tên script, tên file log) — "full" chạy đủ normalize+build+dashboard (giống lịch
    # 6h), "worklog" chỉ normalize+dashboard (bỏ qua build vì tab E không dùng vector index) —
    # đúng yêu cầu nút "Tổng hợp Luồng công việc": chỉ cập nhật phần Luồng công việc, không đụng
    # bước khác. Xem scripts/worklog-sync.ps1 để biết lý do bỏ qua build là an toàn.
    "full": ("daily-sync.ps1", "daily-sync.log"),
    "worklog": ("worklog-sync.ps1", "worklog-sync.log"),
}


@app.post("/run-sync", response_model=RunSyncResponse)
def run_sync_endpoint(mode: str = "full", x_api_key: str | None = Header(default=None)) -> RunSyncResponse:
    """Owner-only — chạy scripts/daily-sync.ps1 hoặc scripts/worklog-sync.ps1 (tuỳ `mode`) đồng
    bộ, dùng cho 2 nút "Chạy đồng bộ ngay"/"Tổng hợp Luồng công việc" trên chính
    reports/DASHBOARD.html (file tĩnh mở qua file://, JS trong đó không tự chạy được script local
    — phải qua server này, nơi đã có sẵn cơ chế owner-only key cho các thao tác có tác dụng phụ
    trên máy). Chạy đồng bộ (chặn request) vì đây là route thường (không async) — FastAPI tự chạy
    trong threadpool riêng, không chặn các request khác (vd /query) đang xử lý song song."""
    _check_owner_key(x_api_key)
    if mode not in _SYNC_SCRIPTS:
        raise HTTPException(status_code=400, detail=f"mode không hợp lệ: {mode!r} (chỉ nhận 'full'/'worklog')")
    script_name, log_name = _SYNC_SCRIPTS[mode]
    script_path = PROJECT_ROOT / "scripts" / script_name
    log_path = _settings.cache_dir / log_name
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script_path)],
            capture_output=True,
            timeout=900,
        )
    except subprocess.TimeoutExpired as e:
        raise HTTPException(status_code=504, detail=f"{script_name} chạy quá 900s, đã huỷ: {e}") from e
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Không chạy được PowerShell: {e}") from e

    log_tail = ""
    if log_path.exists():
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        log_tail = "\n".join(lines[-40:])
    return RunSyncResponse(success=proc.returncode == 0, exit_code=proc.returncode, log_tail=log_tail)


@app.get("/people", response_model=PeopleResponse)
def people_list_endpoint(x_api_key: str | None = Header(default=None)) -> PeopleResponse:
    """Owner-only — dữ liệu cho màn hình /people-ui: registry hiện tại, và tên/định danh chưa
    map theo từng kênh (zalo, email...) để người dùng tự tick chọn rồi gộp tay."""
    _check_owner_key(x_api_key)
    registry = _people.load_registry(_settings)
    unmapped_by_channel = _people.list_unmapped_by_channel(_settings)
    ignored = _people.load_ignored(_settings)
    return PeopleResponse(registry=registry, unmapped_by_channel=unmapped_by_channel, ignored=ignored)


@app.post("/people")
def people_upsert_endpoint(req: PersonRequest, x_api_key: str | None = Header(default=None)) -> list[dict]:
    """Owner-only — lưu 1 cặp tên chính/alias đã được BẠN xác nhận trên màn hình /people-ui.
    Không có bước tự động gộp nào ở đây — chỉ ghi đúng gì được gửi lên."""
    _check_owner_key(x_api_key)
    return _people.upsert_person(_settings, req.canonical, req.aliases)


@app.delete("/people/{canonical}")
def people_delete_endpoint(canonical: str, x_api_key: str | None = Header(default=None)) -> list[dict]:
    _check_owner_key(x_api_key)
    return _people.delete_person(_settings, canonical)


@app.post("/people/ignore")
def people_ignore_endpoint(req: IgnoreRequest, x_api_key: str | None = Header(default=None)) -> list[str]:
    """Owner-only — đánh dấu 1 cụm là "không phải người" (vd "Bước 1" bị regex sender-line
    nhận nhầm trong nội dung tin nhắn dài) — loại khỏi mọi danh sách "chưa map" từ lần sau."""
    _check_owner_key(x_api_key)
    return _people.add_ignored(_settings, req.names)


@app.delete("/people/ignore/{name}")
def people_unignore_endpoint(name: str, x_api_key: str | None = Header(default=None)) -> list[str]:
    _check_owner_key(x_api_key)
    return _people.remove_ignored(_settings, name)


@app.get("/people-ui")
def people_ui_redirect() -> RedirectResponse:
    return RedirectResponse(url="/static/people.html")


@app.get("/health-ui")
def health_ui_redirect() -> RedirectResponse:
    return RedirectResponse(url="/static/health.html")


@app.get("/reports-ui")
def reports_ui_redirect() -> RedirectResponse:
    return RedirectResponse(url="/static/reports.html")


@app.get("/agents-ui")
def agents_ui_redirect() -> RedirectResponse:
    return RedirectResponse(url="/static/agents.html")


_QTIT_RACI_PATH = (
    PROJECT_ROOT.parent
    / "tenants"
    / "msb"
    / "_dept"
    / "artifacts"
    / "RACI_QT.IT.005_009_019_2026-08-17_view.html"
)


@app.get("/reference/qt-it-raci")
def qtit_raci_reference() -> FileResponse:
    """Tài liệu tra cứu RACI + sơ đồ quy trình QT.IT.005/009/019 (đã tổng quát hoá, không gắn
    project/người cụ thể) — phục vụ trực tiếp file tĩnh trong tenants/msb/_dept/artifacts/, không
    copy/nhân bản. Trước 2026-08-26 trỏ vào scratch/ (thư mục tạm, đã bị dọn sạch từ 22/08 nên 404)
    — file thật đã được chuyển vào artifacts/ của tenant cùng lúc với bản .md gốc/normalized."""
    if not _QTIT_RACI_PATH.exists():
        raise HTTPException(status_code=404, detail=f"Chưa thấy file tại {_QTIT_RACI_PATH}")
    return FileResponse(_QTIT_RACI_PATH, media_type="text/html")


@app.get("/agent-ops")
def agent_ops_endpoint(x_api_key: str | None = Header(default=None)) -> dict:
    """Owner-only — toàn bộ dữ liệu cho /agents-ui: lịch sử các lần chạy knowledge-agents
    orchestrator (ghi qua scripts/agent_run_log.py) + mọi ALERT gom từ toàn bộ
    `_project-logs/ALERTS.md` trong normalized/ (xem CLAUDE.md § Sổ nhật ký dự án)."""
    _check_owner_key(x_api_key)
    return build_agents_report(_settings)


class AlertConfirmRequest(BaseModel):
    alert_id: str
    project: str
    ticket: str
    agent: str
    message: str
    note: str


@app.post("/agent-ops/confirm")
def agent_ops_confirm_endpoint(
    req: AlertConfirmRequest, x_api_key: str | None = Header(default=None)
) -> dict:
    """Owner-only — CHỈ xếp hàng xác nhận của người dùng cho 1 alert, KHÔNG tự xác minh gì (server
    không có Read/Grep/đánh giá bằng chứng như agent thật, xem scripts/agent_confirmations.py).
    Lần kế tiếp agent tương ứng (đúng field `agent` của alert) chạy cho đúng `ticket`, nó sẽ đọc
    hàng chờ này, tự đối chiếu bằng chứng thật rồi quyết định resolved/still-open."""
    _check_owner_key(x_api_key)
    if not req.note.strip():
        raise HTTPException(status_code=400, detail="note không được để trống.")
    entry = add_confirmation(
        _settings,
        alert_id=req.alert_id,
        project=req.project,
        ticket=req.ticket,
        agent=req.agent,
        message=req.message,
        note=req.note.strip(),
    )
    return {"confirmation_id": entry["confirmation_id"], "status": "queued"}


class ModelInfoResponse(BaseModel):
    extraction_provider: str
    extraction_model: str
    answer_provider: str
    answer_model: str
    embedding_default_provider: str
    embedding_local_model: str


@app.get("/model-info", response_model=ModelInfoResponse)
def model_info_endpoint() -> ModelInfoResponse:
    """Public, chỉ đọc — loại model LLM/embedding đang cấu hình trong config/settings.yaml, hiển
    thị trên dashboard (Settings panel) để đối chiếu nhanh."""
    return ModelInfoResponse(
        extraction_provider=_settings.extraction_provider,
        extraction_model=_settings.extraction_model,
        answer_provider=_settings.answer_provider,
        answer_model=_settings.answer_model,
        embedding_default_provider=_settings.embedding_default_provider,
        embedding_local_model=_settings.embedding_local_model,
    )


class ModelUpdateRequest(BaseModel):
    extraction_model: str | None = None
    answer_model: str | None = None


@app.post("/model-info", response_model=ModelInfoResponse)
def model_info_update_endpoint(
    req: ModelUpdateRequest, x_api_key: str | None = Header(default=None)
) -> ModelInfoResponse:
    """Owner-only — ghi trực tiếp extraction_model/answer_model xuống config/settings.yaml (giữ
    nguyên comment, xem config.update_llm_models) rồi reload _settings trong process này. Chỉ đổi
    TÊN model (vd gpt-5.4 -> model khác) — không đổi provider (openai/claude) hay đụng embedding,
    vì đó là quyết định kiến trúc khác, không phải việc chỉnh trên Settings panel."""
    _check_owner_key(x_api_key)
    if not req.extraction_model and not req.answer_model:
        raise HTTPException(status_code=400, detail="Cần ít nhất extraction_model hoặc answer_model.")
    try:
        update_llm_models(extraction_model=req.extraction_model, answer_model=req.answer_model)
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    global _settings
    _settings = _load_settings()
    return ModelInfoResponse(
        extraction_provider=_settings.extraction_provider,
        extraction_model=_settings.extraction_model,
        answer_provider=_settings.answer_provider,
        answer_model=_settings.answer_model,
        embedding_default_provider=_settings.embedding_default_provider,
        embedding_local_model=_settings.embedding_local_model,
    )


@app.get("/health")
def health(tenant: str | None = None) -> dict:
    report = build_health_report(_resolve_tenant_settings(tenant))
    return {"status": report["status"], "generated_at": report["generated_at"], "summary": report["summary"]}


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/health/details")
def health_details(tenant: str | None = None, x_api_key: str | None = Header(default=None)) -> dict:
    _check_owner_key(x_api_key)
    return build_health_report(_resolve_tenant_settings(tenant))


@app.get("/tenants")
def tenants_endpoint() -> dict:
    """Cây org -> workspace (kèm instance lồng nhau, vd saleman-legacy -> ibd) đọc trực tiếp từ
    tenants/registry.yaml + đếm tài liệu thật trên đĩa — nguồn cho sidebar dashboard (Phase 6,
    2026-08-25). Không cần owner key: chỉ trả cấu trúc/đếm số, không trả nội dung tài liệu."""
    return list_tenant_registry()


@app.get("/tenant-documents")
def tenant_documents_endpoint(tenant: str, x_api_key: str | None = Header(default=None)) -> dict:
    _check_owner_key(x_api_key)
    try:
        return build_document_tree(tenant)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/tenant-project-logs")
def tenant_project_logs_endpoint(
    tenant: str, ticket: str | None = None, x_api_key: str | None = Header(default=None)
) -> dict:
    _check_owner_key(x_api_key)
    try:
        return list_project_logs(tenant, ticket)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/tenant-project-logs-timeline")
def tenant_project_logs_timeline_endpoint(
    tenant: str, ticket: str, x_api_key: str | None = Header(default=None)
) -> dict:
    """Timeline đa chiều: 5 lane (alerts/decision/raid/milestone/adr) cùng 1 trục thời gian,
    mỗi lane tự trích ngày/tiêu đề/nội dung thật từ đúng sổ nó phụ trách (xem tenant_browser.py)."""
    _check_owner_key(x_api_key)
    try:
        return get_project_logs_timeline(tenant, ticket)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/knowledge-links")
def knowledge_links(x_api_key: str | None = Header(default=None)) -> dict:
    """Owner-only browse summary for deterministic linked knowledge over project/backlog/ticket/person/channel/month."""
    _check_owner_key(x_api_key)
    return build_knowledge_links_report(_settings)


@app.get("/knowledge-links/detail")
def knowledge_links_detail(
    anchor_type: str,
    anchor_id: str,
    x_api_key: str | None = Header(default=None),
) -> dict:
    _check_owner_key(x_api_key)
    if anchor_type not in {"project", "ticket", "person", "channel", "month"}:
        raise HTTPException(
            status_code=400,
            detail="anchor_type must be one of: project, ticket, person, channel, month",
        )
    return build_knowledge_anchor_detail(_settings, anchor_type=anchor_type, anchor_id=anchor_id)


@app.get("/knowledge-home")
def knowledge_home(x_api_key: str | None = Header(default=None)) -> dict:
    _check_owner_key(x_api_key)
    return build_knowledge_home(_settings)


@app.get("/document-search")
def document_search(
    q: str = "",
    project: str = "",
    source_channel: str = "",
    reliability: str = "",
    ticket: str = "",
    person: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = 40,
    x_api_key: str | None = Header(default=None),
) -> dict:
    _check_owner_key(x_api_key)
    return search_documents(
        _settings,
        q=q,
        project=project,
        source_channel=source_channel,
        reliability=reliability,
        ticket=ticket,
        person=person,
        date_from=date_from,
        date_to=date_to,
        limit=max(1, min(limit, 100)),
    )


@app.get("/knowledge-links/document")
def knowledge_links_document(
    source_path: str, tenant: str | None = None, x_api_key: str | None = Header(default=None)
) -> dict:
    _check_owner_key(x_api_key)
    try:
        return build_document_detail(_resolve_tenant_settings(tenant), source_path=source_path)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Không thấy document: {source_path}") from exc


@app.get("/reports")
def reports_list_endpoint(x_api_key: str | None = Header(default=None)) -> list[dict]:
    _check_owner_key(x_api_key)
    return list_report_summaries()


@app.get("/reports/{report_id}")
def report_detail_endpoint(report_id: str, x_api_key: str | None = Header(default=None)) -> dict:
    _check_owner_key(x_api_key)
    try:
        return get_report_detail(report_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Không thấy report: {report_id}") from exc


@app.post("/reports/{report_id}/build")
def report_build_endpoint(report_id: str, x_api_key: str | None = Header(default=None)) -> dict:
    _check_owner_key(x_api_key)
    try:
        return _build_report(report_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Không thấy report: {report_id}") from exc
    except ReportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/reports/{report_id}/artifact")
def report_artifact_endpoint(report_id: str, x_api_key: str | None = Header(default=None)) -> FileResponse:
    _check_owner_key(x_api_key)
    try:
        path = get_report_artifact_path(report_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Không thấy report: {report_id}") from exc
    except ReportError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")
