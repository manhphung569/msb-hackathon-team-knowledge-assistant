# Smoke test thủ công — workbench × graphrag-engine (MSB AI Hackathon 2026)

Không có test tự động cho `workbench/` (stdlib HTTP handler, không phải framework có test
client sẵn — xem EXTRACTION_NOTES.md). Checklist curl này thay thế, chạy lại **mỗi khi sửa
`chat.py`/`graphrag_client.py`/`config.py`** trước khi tin là rewire vẫn hoạt động.

**Luôn dùng `WORKBENCH_DATA_DIR` trỏ vào thư mục rỗng/throwaway** — KHÔNG BAO GIỜ chạy checklist
này (hay bất kỳ thứ gì liên quan hackathon) nhắm vào `workbench/data/vault.db` mặc định, file đó
là bản copy dữ liệu production thật (xem `workbench/README.md`).

## Chuẩn bị (2 terminal)

Terminal 1 — graphrag-engine, trỏ tenant demo:
```bash
cd graphrag-engine
export GUEST_API_KEY=<key-tuỳ-chọn-cho-test>
export OWNER_API_KEY=<key-tuỳ-chọn-cho-test>
./.venv/Scripts/graphrag.exe serve --port 8090 --tenant-root ../tenants/demo/teamassistant
```

Terminal 2 — workbench, DB throwaway:
```bash
cd .. # repo root — bắt buộc, "python -m workbench.server.main" cần chạy từ đây
export WORKBENCH_DATA_DIR="$(pwd)/workbench/_scratch_data"   # KHÔNG PHẢI workbench/data
export GRAPHRAG_API_BASE="http://127.0.0.1:8090"
export GRAPHRAG_API_KEY="<đúng GUEST_API_KEY ở trên>"
export PYTHONIOENCODING=utf-8   # tránh UnicodeEncodeError khi print "✅" trên Windows console
./workbench/.venv/Scripts/python.exe -m workbench.server.main --port 8768 --no-open
```

## Checklist

### 1. Login (tài khoản admin mặc định, DB throwaway mới seed)
```bash
curl -s -X POST http://127.0.0.1:8768/vault/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'
```
**Kỳ vọng**: 200, có `token`. Lưu lại token cho các bước sau.

### 2. Discover-mode qua rewire — trả lời đúng, đủ citation
```bash
curl -s -X POST http://127.0.0.1:8768/discovery/ask \
  -H "Content-Type: application/json" -H "X-Session: <token>" \
  -d '{"project":"teamassistant","question":"MAX_RETRIES trong retry.py la bao nhieu va team quyet dinh doi gi trong cuoc hop?","mode":"discover"}'
```
**Kỳ vọng**: 200, `provider: "graphrag"`, `sources` có path từ **cả 3** pillar
(`source-code/...`, `_jira-notes/...` nếu đã sync Jira, `_chat-notes/...`), `answer` không rỗng.

### 3. `elicit` mode — KHÔNG bị ảnh hưởng bởi rewire (regression guard)
```bash
curl -s -X POST http://127.0.0.1:8768/discovery/ask \
  -H "Content-Type: application/json" -H "X-Session: <token>" \
  -d '{"project":"teamassistant","question":"test elicit mode","mode":"elicit"}'
```
**Kỳ vọng**: vẫn đi qua nhánh keyword-grep cũ (không có `provider: "graphrag"` trong response,
hoặc lỗi `openspec not found` nếu tenant demo không có cấu trúc `openspec/` — cả 2 đều CHỨNG
MINH đúng: nhánh mới (§5) chỉ chặn đúng `mode == "discover"`, không đụng `elicit`).

### 4. Không silent fallback khi graphrag-engine down
Tắt server ở Terminal 1 (Ctrl+C), lặp lại bước 2:
```bash
curl -s -w "\nHTTP_STATUS:%{http_code}\n" -X POST http://127.0.0.1:8768/discovery/ask \
  -H "Content-Type: application/json" -H "X-Session: <token>" \
  -d '{"project":"teamassistant","question":"test khi graphrag down","mode":"discover"}'
```
**Kỳ vọng**: `HTTP_STATUS:502`, JSON `{"error": "graphrag-engine không phản hồi..."}` — KHÔNG
hang, KHÔNG âm thầm trả lời sai qua nhánh cũ.

### 5. Backlog item → xuất ra normalized/ (§4)
```bash
curl -s -X POST http://127.0.0.1:8768/vault/backlog/item \
  -H "Content-Type: application/json" -H "X-Session: <token>" \
  -d '{"session_id": <id>, "title": "Test item", "description": "...", "priority": "high", "category": "bugfix"}'
```
**Kỳ vọng**: file mới xuất hiện tại
`tenants/demo/teamassistant/normalized/_workbench-backlog/<id>.md`, frontmatter đúng
(`source: workbench-backlog`, `status`, `priority`, `backlog_item_id`).
```bash
curl -s -X DELETE http://127.0.0.1:8768/vault/backlog/item/<id> -H "X-Session: <token>"
```
**Kỳ vọng**: file `<id>.md` biến mất.

## Đã chạy thật (không chỉ lý thuyết)

Cả 5 bước đã chạy thật trong phiên làm việc dựng §5/§4 (2026-09-17) — tất cả pass đúng kỳ vọng
trên (bước 1 login qua DB throwaway mới seed; bước 5 test cả create lẫn delete, xác nhận file
xuất hiện/biến mất đúng, frontmatter parse lại được bằng yaml.safe_load).
